"""RoomOrchestrator — Qt-free multi-room session orchestration (actor model).

Migrated from ``lsc.gui.multi_room.manager.MultiRoomManager`` (M1 Tasks 4–5).
Public API signatures match the manager; Qt Signals/QTimer/QThread are replaced
with EventBus / deadline waits / ThreadPoolExecutor jobs.
"""
from __future__ import annotations

import logging
import os
import queue
import re
import shutil
import threading
import time
import time as _time
import traceback
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any, TypeVar
from uuid import uuid4

from lsc.config import (
    ExportProfile,
    is_platform_pipeline_component_enabled,
    load_config,
)
from lsc.core.events import EventBus
from lsc.core.services.ingest_registry import get_shared_ingest_registry
from lsc.core.services.recording_service import RecordingService
from lsc.core.services.timeline_service import get_timeline_service
from lsc.core.session import RoomSession
from lsc.platforms.base import StreamInfo
from lsc.platforms.failure import FailureKind, classify_failure, normalize_failure_kind
from lsc.platforms.redaction import redact_text
from lsc.platforms.registry import detect_platform, parse_stream, select_quality

_log = logging.getLogger(__name__)

# 多个 RoomOrchestrator（测试、热重启或迁移期桥接）可能指向同一个 rooms.json。
# 原子替换所用的固定 .tmp 路径必须跨实例串行，否则一个实例会先移走另一个
# 实例的临时文件，表现为 WinError 2，并可能让录制/预览状态恢复数据丢失。
_ROOMS_PERSIST_LOCK = threading.Lock()

_T = TypeVar("_T")

ControllerFactory = Callable[[], object]
PreviewFactory = Callable[[], object]

# ── Resource limits ──────────────────────────────────────────
# 可通过 settings.json 的 max_rooms / max_concurrent_previews 覆盖
MAX_ROOMS = 12
MAX_CONCURRENT_PREVIEWS = 4
# 录制过程中磁盘剩余空间低于此阈值时停止录制（2 GB）
_MIN_FREE_BYTES_WHILE_RECORDING = 2 * 1024 * 1024 * 1024


def _get_configured_max_rooms() -> int:
    """从 settings.json 读取最大房间数，默认 MAX_ROOMS。"""
    try:
        cfg = load_config()
        return int(getattr(cfg, "max_rooms", MAX_ROOMS))
    except Exception:
        return MAX_ROOMS


def _get_configured_max_previews() -> int:
    """从 settings.json 读取最大并发预览数，默认 MAX_CONCURRENT_PREVIEWS。"""
    try:
        cfg = load_config()
        return int(getattr(cfg, "max_concurrent_previews", MAX_CONCURRENT_PREVIEWS))
    except Exception:
        return MAX_CONCURRENT_PREVIEWS


def _room_platform_key(room: Any) -> str:
    """Return a canonical platform id before a room has been resolved.

    Newly added rooms do not have ``RoomSession.platform`` populated yet.  A
    pure adapter ``can_handle`` check is enough to route the feature flag and
    does not perform page/network parsing, so an allowlisted platform cannot
    accidentally take the legacy path on its first connection.
    """
    current = str(
        getattr(room, "platform", "")
        or getattr(room, "platform_name", "")
        or ""
    ).strip()
    if current:
        return current
    try:
        return str(detect_platform(str(getattr(room, "room_url", "") or "")) or "")
    except Exception as exc:
        _log.debug("platform pre-routing failed: %s", redact_text(exc))
        return ""

# ── Reconnect strategy ───────────────────────────────────────
_MAX_RECONNECT_ATTEMPTS = 3
_RECONNECT_DELAY_SEC = 2.0  # Base delay for exponential backoff
_RECONNECT_MAX_DELAY_SEC = 30.0  # Maximum delay between attempts
_RECONNECT_BACKOFF_FACTOR = 2.0  # Exponential backoff multiplier

# 流 URL 主动刷新阈值：过期前 60 秒主动刷新（与 registry 一致）
_STREAM_URL_REFRESH_THRESHOLD_SEC = 60
# 连接成功后房间级流缓存复用窗口：预览/录制启动时跳过重复 HTTP 解析
_STREAM_CACHE_REUSE_SEC = 120.0

# ── Heartbeat intervals (seconds) ────────────────────────────
# Timer fires every 3s; stagger medium work across rooms to cut I/O spikes.
_TICK_INTERVAL_MS = 3000
# High-frequency: elapsed time, playback position (every tick ≈ 3s)
_HIGH_FREQ_INTERVAL = 1
# Medium-frequency: file size / FFmpeg health — every tick, but staggered by room
_MEDIUM_FREQ_INTERVAL = 1
_SHARED_INGEST_STALL_CHECKS = 4  # 4 × 3s ≈ 12s，与 capture.check_health 量级对齐
# Low-frequency: disk space check (every N ticks ≈ 12s)
_LOW_FREQ_INTERVAL = 4
# 交错轮询：同一次 tick 只处理 1/N 房间的 medium 工作
_STAGGER_GROUPS = 3

_OFFLINE_STREAM_ERROR_PATTERNS = (
    "下播",
    "未开播",
    "未直播",
    "直播已结束",
    "直播间已结束",
    "不在直播",
    "not live",
    "offline",
)


def _is_stream_offline_error(message: str) -> bool:
    if not message:
        return False
    if classify_failure(message) is FailureKind.OFFLINE:
        return True
    lowered = message.lower()
    return any(pattern in lowered for pattern in _OFFLINE_STREAM_ERROR_PATTERNS)


def _offline_stream_error_message(raw: str = "") -> str:
    if raw and _is_stream_offline_error(raw):
        return f"{raw}，录制已停止"
    return "直播间已下播或未开播，录制已停止"


def _is_stream_url_expiring(url: str, threshold_sec: int = _STREAM_URL_REFRESH_THRESHOLD_SEC) -> bool:
    """检查流 URL 是否即将过期。

    平台 CDN URL 包含过期时间戳参数：
    - 抖音: expire=<hex_timestamp> 或 wsTime=<hex_timestamp>
    - B站: expires=<decimal_timestamp>
    - 虎牙: wsTime=<hex_timestamp>
    """
    if not url:
        return False
    try:
        from urllib.parse import parse_qs
        from urllib.parse import urlparse as _urlparse
        params = parse_qs(_urlparse(url).query)
        now = time.time()
        # 启发式：遍历所有 query 参数值，识别"像时间戳"的整数（十进制或 hex，
        # 值接近当前时间 ±48h），覆盖任意平台的任意过期参数名，避免平台新增
        # URL 参数（斗鱼 time、快手/小红书签名等）时效检测静默失效。
        # B站 expires 是十进制；虎牙/抖音 wsTime/expire、斗鱼 time 是 hex。
        for vals in params.values():
            for raw in vals:
                if not raw:
                    continue
                for _base in (16, 10):
                    try:
                        _ts = int(raw, _base)
                    except (ValueError, OverflowError):
                        continue
                    if _ts > 0 and abs(_ts - now) < 48 * 3600 and now > _ts - threshold_sec:
                        return True
    except Exception as exc:
        _log.debug("操作异常（已忽略）: %s", exc)
    return False


def _get_room_stream_url(room: RoomSession) -> str:
    """取房间当前可用流地址（优先 stream_info → cache → controller）。"""
    if room.stream_info and room.stream_info.stream_url:
        return str(room.stream_info.stream_url)
    if room.stream_url_cached:
        return str(room.stream_url_cached)
    controller = room.controller
    if controller is not None:
        return str(getattr(controller, "stream_url", "") or "")
    return ""


def _room_stream_is_reusable(room: RoomSession) -> bool:
    """连接后短时间内的流地址可直接复用，避免预览/录制再打一轮平台解析。

    必须有 ``stream_parsed_at``（由 apply_stream_info 写入），否则仍走解析，
    防止旧会话/过期无参 URL 被误当成新鲜缓存。
    """
    if room.stream_parsed_at <= 0:
        return False
    url = _get_room_stream_url(room)
    if not url:
        return False
    if _is_stream_url_expiring(url):
        return False
    last_error = str(getattr(room, "last_error", "") or "")
    if last_error:
        kind = classify_failure(last_error)
        if kind in {
            FailureKind.CDN_FORBIDDEN,
            FailureKind.SIGNATURE_EXPIRED,
            FailureKind.CONNECTION_RESET,
            FailureKind.CONNECT_TIMEOUT,
        }:
            return False
    age = time.time() - float(room.stream_parsed_at)
    return age <= _STREAM_CACHE_REUSE_SEC


def _sync_controller_stream(room: RoomSession, info: StreamInfo | None = None) -> None:
    """把流地址/headers 同步到 RecordingController。"""
    controller = room.controller
    if controller is None:
        return
    if info is not None:
        legacy_info = info.to_legacy_dict()
        controller.stream_url = info.stream_url
        controller.input_args = legacy_info.get("_inputArgs", [])
        controller.selected_quality = legacy_info.get("selectedQuality", info.selected_quality)
        return
    url = _get_room_stream_url(room)
    if url:
        controller.stream_url = url
    if room.stream_info is not None:
        legacy_info = room.stream_info.to_legacy_dict()
        controller.input_args = legacy_info.get("_inputArgs", [])
        if legacy_info.get("selectedQuality"):
            controller.selected_quality = legacy_info.get("selectedQuality")


def _heal_connected_flag(room: RoomSession) -> bool:
    """修复「前端显示已连接但 is_connected 被预览刷新失败清掉」的脏状态。"""
    if room.is_connected:
        return True
    url = _get_room_stream_url(room)
    if not url or _is_stream_url_expiring(url):
        return False
    room.is_connected = True
    if room.stream_info is None and room.stream_url_cached:
        room.stream_info = StreamInfo(
            platform=room.platform or "unknown",
            room_url=room.room_url,
            stream_url=room.stream_url_cached,
            is_live=True,
            selected_quality=room.selected_quality,
        )
    _sync_controller_stream(room)
    _log.warning("healed stale is_connected for room %s (had usable stream cache)", room.room_id)
    return True


def _make_room_output_dir(base_dir: str, room: RoomSession) -> str:
    """生成可读的多房间录制子目录名，避免纯 uuid 难以辨认。

    格式: {platform}_{streamer}_{room_id_short}
    非法文件名字符会被替换为下划线，并以 room_id 后 6 位保证唯一性。
    """
    platform = re.sub(r"[^\w\-]", "_", (room.platform or "unknown")).strip("_")[:20]
    streamer = re.sub(r"[^\w\-]", "_", (room.streamer_name or "room")).strip("_")[:30]
    short_id = room.room_id[-6:]
    name = f"{platform}_{streamer}_{short_id}"
    # 防止连续下划线或首尾下划线
    name = re.sub(r"_+", "_", name).strip("_")
    if not name:
        name = f"room_{short_id}"
    return os.path.join(base_dir, name)


class SizeUpdateJob:
    """Background task to query file size without blocking the GUI thread.

    Includes caching to avoid redundant file system calls.
    """
    _size_cache: dict[str, tuple[float, float]] = {}  # path -> (size_mb, timestamp)
    _cache_lock: threading.Lock = threading.Lock()
    _CACHE_TTL = 2.0  # Cache for 2 seconds

    def __init__(self, room: RoomSession, path: str) -> None:
        self.room = room
        self.path = path

    def run(self) -> None:
        try:
            import time as _time
            now = _time.time()

            # Check cache first
            with self._cache_lock:
                cached = self._size_cache.get(self.path)
                if cached and (now - cached[1]) < self._CACHE_TTL:
                    with self.room._room_lock:
                        self.room.record_size_mb = cached[0]
                    return

            # Query file system
            size = os.path.getsize(self.path) / (1024 * 1024)
            with self.room._room_lock:
                self.room.record_size_mb = size

            # Update cache
            with self._cache_lock:
                self._size_cache[self.path] = (size, now)

                # Clean old cache entries (keep last 100)
                if len(self._size_cache) > 100:
                    oldest_keys = sorted(
                        self._size_cache.keys(),
                        key=lambda k: self._size_cache[k][1]
                    )[:50]
                    for k in oldest_keys:
                        del self._size_cache[k]
        except OSError as exc:
            _log.debug("更新文件大小失败 (%s): %s", self.path, exc)

_log = logging.getLogger(__name__)

# ── Resource limits ──────────────────────────────────────────
MAX_ROOMS = 12
MAX_CONCURRENT_PREVIEWS = 4
# 录制过程中磁盘剩余空间低于此阈值时停止录制（2 GB）
_MIN_FREE_BYTES_WHILE_RECORDING = 2 * 1024 * 1024 * 1024

# ── Reconnect strategy ───────────────────────────────────────
_MAX_RECONNECT_ATTEMPTS = 3
_RECONNECT_DELAY_SEC = 2.0  # Base delay for exponential backoff
_RECONNECT_MAX_DELAY_SEC = 30.0  # Maximum delay between attempts
_RECONNECT_BACKOFF_FACTOR = 2.0  # Exponential backoff multiplier

# 流 URL 主动刷新阈值：过期前 60 秒主动刷新（与 registry 一致）
_STREAM_URL_REFRESH_THRESHOLD_SEC = 60
# 连接成功后房间级流缓存复用窗口：预览/录制启动时跳过重复 HTTP 解析
_STREAM_CACHE_REUSE_SEC = 120.0

# ── Heartbeat intervals (seconds) ────────────────────────────
# Timer fires every 3s; stagger medium work across rooms to cut I/O spikes.
_TICK_INTERVAL_MS = 3000
# High-frequency: elapsed time, playback position (every tick ≈ 3s)
_HIGH_FREQ_INTERVAL = 1
# Medium-frequency: file size / FFmpeg health — every tick, but staggered by room
_MEDIUM_FREQ_INTERVAL = 1
_SHARED_INGEST_STALL_CHECKS = 4  # 4 × 3s ≈ 12s，与 capture.check_health 量级对齐
# Low-frequency: disk space check (every N ticks ≈ 12s)
_LOW_FREQ_INTERVAL = 4
# 交错轮询：同一次 tick 只处理 1/N 房间的 medium 工作
_STAGGER_GROUPS = 3

def _is_stream_url_expiring(url: str, threshold_sec: int = _STREAM_URL_REFRESH_THRESHOLD_SEC) -> bool:
    """检查流 URL 是否即将过期。

    平台 CDN URL 包含过期时间戳参数：
    - 抖音: expire=<hex_timestamp> 或 wsTime=<hex_timestamp>
    - B站: expires=<decimal_timestamp>
    - 虎牙: wsTime=<hex_timestamp>
    """
    if not url:
        return False
    try:
        import time as _time_mod
        from urllib.parse import parse_qs
        from urllib.parse import urlparse as _urlparse
        params = parse_qs(_urlparse(url).query)
        now = _time_mod.time()
        # 启发式：遍历所有 query 参数值，识别"像时间戳"的整数（十进制或 hex，
        # 值接近当前时间 ±48h），覆盖任意平台的任意过期参数名，避免平台新增
        # URL 参数（斗鱼 time、快手/小红书签名等）时效检测静默失效。
        # B站 expires 是十进制；虎牙/抖音 wsTime/expire、斗鱼 time 是 hex。
        for vals in params.values():
            for raw in vals:
                if not raw:
                    continue
                for _base in (16, 10):
                    try:
                        _ts = int(raw, _base)
                    except (ValueError, OverflowError):
                        continue
                    if _ts > 0 and abs(_ts - now) < 48 * 3600 and now > _ts - threshold_sec:
                        return True
    except Exception as exc:
        _log.debug("操作异常（已忽略）: %s", exc)
    return False


def _get_room_stream_url(room: RoomSession) -> str:
    """取房间当前可用流地址（优先 stream_info → cache → controller）。"""
    if room.stream_info and room.stream_info.stream_url:
        return str(room.stream_info.stream_url)
    if room.stream_url_cached:
        return str(room.stream_url_cached)
    controller = room.controller
    if controller is not None:
        return str(getattr(controller, "stream_url", "") or "")
    return ""


def _room_stream_is_reusable(room: RoomSession) -> bool:
    """连接后短时间内的流地址可直接复用，避免预览/录制再打一轮平台解析。

    必须有 ``stream_parsed_at``（由 apply_stream_info 写入），否则仍走解析，
    防止旧会话/过期无参 URL 被误当成新鲜缓存。
    """
    if room.stream_parsed_at <= 0:
        return False
    url = _get_room_stream_url(room)
    if not url:
        return False
    if _is_stream_url_expiring(url):
        return False
    last_error = str(getattr(room, "last_error", "") or "")
    if last_error:
        kind = classify_failure(last_error)
        if kind in {
            FailureKind.CDN_FORBIDDEN,
            FailureKind.SIGNATURE_EXPIRED,
            FailureKind.CONNECTION_RESET,
            FailureKind.CONNECT_TIMEOUT,
        }:
            return False
    age = _time.time() - float(room.stream_parsed_at)
    return age <= _STREAM_CACHE_REUSE_SEC


def _sync_controller_stream(room: RoomSession, info: StreamInfo | None = None) -> None:
    """把流地址/headers 同步到 RecordingController。"""
    controller = room.controller
    if controller is None:
        return
    if info is not None:
        legacy_info = info.to_legacy_dict()
        controller.stream_url = info.stream_url
        controller.input_args = legacy_info.get("_inputArgs", [])
        controller.selected_quality = legacy_info.get("selectedQuality", info.selected_quality)
        return
    url = _get_room_stream_url(room)
    if url:
        controller.stream_url = url
    if room.stream_info is not None:
        legacy_info = room.stream_info.to_legacy_dict()
        controller.input_args = legacy_info.get("_inputArgs", [])
        if legacy_info.get("selectedQuality"):
            controller.selected_quality = legacy_info.get("selectedQuality")


def _heal_connected_flag(room: RoomSession) -> bool:
    """修复「前端显示已连接但 is_connected 被预览刷新失败清掉」的脏状态。"""
    if room.is_connected:
        return True
    url = _get_room_stream_url(room)
    if not url or _is_stream_url_expiring(url):
        return False
    room.is_connected = True
    if room.stream_info is None and room.stream_url_cached:
        room.stream_info = StreamInfo(
            platform=room.platform or "unknown",
            room_url=room.room_url,
            stream_url=room.stream_url_cached,
            is_live=True,
            selected_quality=room.selected_quality,
        )
    _sync_controller_stream(room)
    _log.warning("healed stale is_connected for room %s (had usable stream cache)", room.room_id)
    return True


def _make_room_output_dir(base_dir: str, room: RoomSession) -> str:
    """生成可读的多房间录制子目录名，避免纯 uuid 难以辨认。

    格式: {platform}_{streamer}_{room_id_short}
    非法文件名字符会被替换为下划线，并以 room_id 后 6 位保证唯一性。
    """
    platform = re.sub(r"[^\w\-]", "_", (room.platform or "unknown")).strip("_")[:20]
    streamer = re.sub(r"[^\w\-]", "_", (room.streamer_name or "room")).strip("_")[:30]
    short_id = room.room_id[-6:]
    name = f"{platform}_{streamer}_{short_id}"
    # 防止连续下划线或首尾下划线
    name = re.sub(r"_+", "_", name).strip("_")
    if not name:
        name = f"room_{short_id}"
    return os.path.join(base_dir, name)


class _ConnectJob:
    """Background job for non-blocking stream URL parsing."""

    def __init__(self, room_id: str, url: str, quality_preset: str = "原画"):
        self.room_id = room_id
        self.url = url
        self.quality_preset = quality_preset
        self.cancel = threading.Event()

    def run(self) -> tuple[str, bool, str, object | None]:
        try:
            if self.cancel.is_set():
                return self.room_id, False, "已取消", None
            info = parse_stream(self.url)
            if self.cancel.is_set():
                return self.room_id, False, "已取消", None
            if info.is_live and self.quality_preset:
                stream_url, selected_quality = select_quality(info, self.quality_preset)
                if stream_url:
                    info.stream_url = stream_url
                    info.selected_quality = selected_quality
            success = bool(info.is_live and info.stream_url)
            error = "" if success else (info.error or "连接失败")
            return self.room_id, success, error, info
        except Exception as exc:
            return self.room_id, False, str(exc), None


class _MetadataProbeJob:
    """Background job for non-blocking ffprobe of stream resolution/fps."""

    def __init__(self, room_id: str, stream_url: str, controller: object):
        self.room_id = room_id
        self.stream_url = stream_url
        self.controller = controller
        self.cancel = threading.Event()

    def run(self) -> tuple[str, str, str] | None:
        try:
            if self.cancel.is_set():
                return None
            probe_fn = getattr(self.controller, "probe_stream_metadata", None)
            if not callable(probe_fn):
                return None
            resolution, fps = probe_fn(self.stream_url)
            if self.cancel.is_set():
                return None
            return self.room_id, resolution, fps
        except Exception as exc:
            _log.debug("Metadata probe failed for room %s: %s", self.room_id, exc)
            return self.room_id, "", ""


class _BatchRecordJob:
    """Background job for parallel batch recording start."""

    def __init__(self, orchestrator: RoomOrchestrator, room_ids: list[str],
                 output_dir: str, encoder: str, crf: int,
                 param_mode: str = "CRF 质量", bitrate: str | None = None,
                 bitrate_unit: str = "kbps"):
        self._orchestrator = orchestrator
        self._room_ids = room_ids
        self._output_dir = output_dir
        self._encoder = encoder
        self._crf = crf
        self._param_mode = param_mode
        self._bitrate = bitrate
        self._bitrate_unit = bitrate_unit
        self.cancel = threading.Event()

    def run(self) -> None:
        started = 0
        worker_count = min(12, len(self._room_ids))
        with ThreadPoolExecutor(max_workers=worker_count) as pool:
            futures = {}
            for room_id in self._room_ids:
                if self.cancel.is_set():
                    break
                fut = pool.submit(
                    self._orchestrator.start_recording,
                    room_id, self._output_dir, self._encoder, self._crf,
                    param_mode=self._param_mode,
                    bitrate=self._bitrate,
                    bitrate_unit=self._bitrate_unit,
                    _run_in_background=True,
                )
                futures[fut] = room_id
            for fut in as_completed(futures):
                room_id = futures[fut]
                try:
                    ok = fut.result()
                    if ok:
                        started += 1
                except Exception as exc:
                    _log.error("批量录制房间异常: room_id=%s, error=%s", room_id, exc)
                    ok = False
                orch = self._orchestrator
                orch.submit(lambda rid=room_id, success=ok: orch.bus.emit(
                    "batch_record_progress", rid, success
                ))
        total = len(self._room_ids)
        started_n = started
        orch = self._orchestrator
        orch.submit(lambda: orch.bus.emit("batch_record_finished", started_n, total))
        orch.submit(lambda: setattr(orch, "_batch_record_job", None))


class _CallRequest:
    def __init__(self, fn: Callable, args: tuple, kwargs: dict):
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.result: Any = None
        self.exception: BaseException | None = None
        self.traceback: str | None = None
        self.event = threading.Event()
        self.cancelled = False


def _mark_failed_candidate(room, error_msg: str) -> None:
    """Delegate failed-candidate handling to the owning platform policy."""
    try:
        from lsc.platforms.recovery_policy import mark_failed_candidate

        network_context = getattr(room, "network_context", {}) or {}
        network_profile = ""
        if isinstance(network_context, dict):
            network_profile = str(
                network_context.get("profile")
                or network_context.get("proxy_url")
                or ""
            )
        ingest = None
        registry = get_shared_ingest_registry()
        getter = getattr(registry, "get", None)
        if callable(getter):
            ingest = getter(str(getattr(room, "room_id", "") or ""))
        saw_first_ts = bool(getattr(ingest, "_upstream_has_produced_data", False))
        mark_failed_candidate(
            getattr(room, "stream_info", None),
            error_msg,
            room_id=str(getattr(room, "room_id", "") or ""),
            network_profile=network_profile,
            saw_first_ts=saw_first_ts,
        )
    except Exception as exc:
        _log.debug("mark_failed_cdn policy failed: %s", exc)


_QUARANTINE_PROBE_KINDS = {
    "CDN_FORBIDDEN",
    "SIGNATURE_EXPIRED",
    "CONNECT_TIMEOUT",
    "CONNECTION_RESET",
}


def _quarantine_failed_probe_candidates(room, resolve_result, probes) -> None:
    """Isolate probed-dead CDN lines before the next parse/retry."""
    if str(getattr(resolve_result, "platform", "") or "") != "huya":
        return
    try:
        from lsc.platforms.huya import mark_cdn_bad
    except Exception as exc:
        _log.debug("huya CDN quarantine import failed: %s", exc)
        return
    network_context = getattr(room, "network_context", {}) or {}
    network_profile = ""
    if isinstance(network_context, dict):
        network_profile = str(
            network_context.get("profile")
            or network_context.get("proxy_url")
            or ""
        )
    candidates = {
        str(getattr(item, "candidate_id", "") or ""): item
        for item in tuple(getattr(resolve_result, "candidates", ()) or ())
    }
    room_key = str(getattr(room, "room_url", "") or "")
    for item in (probes or {}).values():
        kind = str(getattr(item, "failure_kind", "") or "")
        if kind not in _QUARANTINE_PROBE_KINDS:
            continue
        cdn_id = str(getattr(item, "cdn_id", "") or "")
        if not cdn_id:
            candidate = candidates.get(str(getattr(item, "candidate_id", "") or ""))
            cdn_id = str(getattr(candidate, "cdn_id", "") or "") if candidate else ""
        if not cdn_id:
            continue
        try:
            mark_cdn_bad(
                cdn_id,
                room_key=room_key,
                network_profile=network_profile,
            )
        except Exception as exc:
            _log.debug("huya CDN quarantine failed: %s", exc)


class RoomOrchestrator:
    _MAX_PENDING_REQUESTS = 8
    # Bound the command mailbox so producer threads cannot exhaust memory
    # when a room controller or UI client floods the orchestrator.  Keep this
    # separate from the synchronous pending-request cap: fire-and-forget
    # callbacks are allowed to queue more work, but still have a hard limit.
    _MAX_QUEUED_COMMANDS = 1024

    def __init__(
        self,
        controller_factory: ControllerFactory | None = None,
        preview_factory: PreviewFactory | None = None,
    ) -> None:
        self._controller_factory = controller_factory
        self._preview_factory = preview_factory
        self.bus = EventBus()
        self._cmd_queue: queue.Queue[Any] = queue.Queue(maxsize=self._MAX_QUEUED_COMMANDS)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._pending_count = 0
        self._pending_lock = threading.Lock()
        self._rooms: dict[str, RoomSession] = {}
        # _lock 保护_rooms 及其属性（is_recording/last_error 等）并发读写
        self._lock = threading.RLock()
        self._connect_jobs: dict[str, _ConnectJob] = {}
        self._metadata_probe_jobs: dict[str, _MetadataProbeJob] = {}
        self._batch_record_job: _BatchRecordJob | None = None
        self._refresh_cancels: dict[str, threading.Event] = {}
        self._lease_managers: dict[str, Any] = {}
        self._stream_leases: dict[str, Any] = {}
        self._pending_stream_infos: dict[str, StreamInfo] = {}
        self._lease_refresh_inflight: set[str] = set()
        self._tick_counter = 0
        self._next_tick_deadline: float | None = None
        self._loop_deadline: float | None = None
        self._dirty_recording: bool = False
        self._dirty_connection: bool = False
        self._missing_controller_tick_warned: set[str] = set()
        self._loop_room_id: str | None = None
        self._loop_start: float = 0.0
        self._loop_end: float = 0.0
        self._loop_native: bool = False
        self._worker_pool = ThreadPoolExecutor(max_workers=6, thread_name_prefix="orch-worker")

    @property
    def thread_ident(self) -> int | None:
        t = self._thread
        return t.ident if t else None

    @staticmethod
    def _pipeline_component_enabled(
        component: str,
        room: RoomSession,
        config: Any | None = None,
    ) -> bool:
        """Evaluate a V2 gate with the room's rollout context."""
        context = getattr(room, "network_context", {}) or {}
        if not isinstance(context, dict):
            context = {}
        platform = _room_platform_key(room)
        try:
            return is_platform_pipeline_component_enabled(
                component,
                platform,
                config,
                room_id=room.room_id,
                user_id=str(context.get("user_id", "") or "") or None,
                account_ref=str(
                    getattr(room, "account_ref", "")
                    or context.get("account_ref", "")
                    or ""
                ) or None,
                app_version=str(
                    context.get("app_version", "")
                    or os.environ.get("LSC_APP_VERSION", "")
                    or ""
                ) or None,
            )
        except TypeError as exc:
            # Preserve test/third-party gate shims that still implement the
            # original three-argument callable.
            if not any(name in str(exc) for name in ("room_id", "user_id", "account_ref", "app_version")):
                raise
            try:
                return is_platform_pipeline_component_enabled(component, platform, config)
            except TypeError as legacy_exc:
                if "positional argument" not in str(legacy_exc):
                    raise
                return is_platform_pipeline_component_enabled(component, platform)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run_loop, name="RoomOrchestrator", daemon=True)
        self._thread.start()
        while self._thread.ident is None:
            time.sleep(0.001)
        self.bus.bind_emitter_thread(self._thread)

    def _run_loop(self) -> None:
        while not self._stop.is_set():
            timeout = self._compute_wait_timeout()
            try:
                item = self._cmd_queue.get(timeout=timeout)
            except queue.Empty:
                self._safe_on_deadlines()
                continue
            if item is None:
                break
            if isinstance(item, _CallRequest):
                self._dispatch_request(item)
            elif callable(item):
                try:
                    item()
                except Exception:
                    _log.exception("orchestrator command failed")
            self._safe_on_deadlines()

    def _safe_on_deadlines(self) -> None:
        """执行到期 tick，任何异常都不得杀死编排线程。

        编排线程死亡 = 全部房间的录制/预览/重连/磁盘守卫整体冻结且无法自动恢复。
        单房 controller tick 已有内部兜底，这里再包一层防御全局 tick
        （文件竞态 OSError、lease 刷新异常等）不预期的异常。
        """
        try:
            self._on_deadlines()
        except Exception:
            _log.exception("orchestrator global tick crashed, continuing")

    def _compute_wait_timeout(self) -> float | None:
        now = time.monotonic()
        deadlines = [d for d in (self._next_tick_deadline, self._loop_deadline) if d is not None]
        if not deadlines:
            return 0.05
        return max(0.0, min(deadlines) - now)

    def _on_deadlines(self) -> None:
        now = time.monotonic()
        if self._next_tick_deadline is not None and now >= self._next_tick_deadline:
            self._on_global_tick()
            self._next_tick_deadline = now + (_TICK_INTERVAL_MS / 1000.0)
        if self._loop_deadline is not None and now >= self._loop_deadline:
            self._on_preview_loop_tick()
            # Only re-arm if still active (stop_range_loop clears deadline)
            if self._loop_deadline is not None:
                self._loop_deadline = now + 0.05

    def _dispatch_request(self, req: _CallRequest) -> None:
        if req.cancelled:
            with self._pending_lock:
                self._pending_count -= 1
            return
        try:
            req.result = req.fn(*req.args, **req.kwargs)
        except Exception as exc:
            req.exception = exc
            req.traceback = traceback.format_exc()
            _log.error("orchestrator call raised", exc_info=True)
        finally:
            with self._pending_lock:
                self._pending_count -= 1
            req.event.set()

    def call(self, fn: Callable[..., _T], *args: Any, timeout: float = 10.0, **kwargs: Any) -> _T:
        if self._thread and threading.current_thread() is self._thread:
            return fn(*args, **kwargs)
        with self._pending_lock:
            if self._pending_count >= self._MAX_PENDING_REQUESTS:
                raise TimeoutError(
                    f"orchestrator too busy ({self._pending_count} pending, "
                    f"max {self._MAX_PENDING_REQUESTS})"
                )
            self._pending_count += 1
        req = _CallRequest(fn, args, kwargs)
        try:
            self._cmd_queue.put_nowait(req)
        except queue.Full as exc:
            with self._pending_lock:
                self._pending_count -= 1
            raise RuntimeError("command queue full") from exc
        if not req.event.wait(timeout=timeout):
            req.cancelled = True
            raise TimeoutError("orchestrator call timed out")
        if req.exception is not None:
            raise req.exception
        return req.result

    def submit(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        if self._thread and threading.current_thread() is self._thread:
            try:
                fn(*args, **kwargs)
            except Exception:
                _log.exception("submit on orch thread failed")
            return
        try:
            self._cmd_queue.put_nowait(lambda: fn(*args, **kwargs))
        except queue.Full as exc:
            raise RuntimeError("command queue full") from exc

    def _submit_worker(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> bool:
        """Submit background work unless shutdown has started.

        The orchestrator loop can still finish one deadline tick while
        ``shutdown()`` is joining it.  Guarding the executor boundary avoids
        ``cannot schedule new futures after shutdown`` and treats late
        maintenance work as safely discarded.
        """
        if self._stop.is_set():
            return False
        if bool(getattr(self._worker_pool, "_shutdown", False)):
            return False
        try:
            self._worker_pool.submit(fn, *args, **kwargs)
            return True
        except RuntimeError as exc:
            # Executor shutdown is an expected teardown race; avoid logging
            # against a test/application stream that may already be closed.
            if not self._stop.is_set() and not bool(
                getattr(self._worker_pool, "_shutdown", False)
            ):
                _log.warning("worker submission rejected: %s", redact_text(exc))
            return False

    def _on_ingest_event(self, event: Any) -> None:
        """Publish supervisor events on the orchestrator-owned EventBus."""
        room = self.get_room(str(getattr(event, "room_id", "") or ""))
        platform = str(
            getattr(event, "platform_id", "")
            or getattr(room, "platform", "")
            or getattr(room, "platform_name", "")
            or ""
        ) if room is not None else ""
        if not platform:
            platform = str(getattr(event, "platform_id", "") or "")
        runtime_gate = (
            self._pipeline_component_enabled("runtime_events_v2", room)
            if room is not None
            else is_platform_pipeline_component_enabled(
                "runtime_events_v2", platform, load_config()
            )
        )
        if not runtime_gate:
            return
        if self._thread is not None and threading.current_thread() is self._thread:
            self.bus.emit("runtime_event", event)
        else:
            self.submit(self.bus.emit, "runtime_event", event)

    def _ensure_tick_armed(self) -> None:
        if self._next_tick_deadline is None:
            self._next_tick_deadline = time.monotonic() + (_TICK_INTERVAL_MS / 1000.0)

    def _disarm_tick_if_empty(self) -> None:
        with self._lock:
            empty = len(self._rooms) == 0
        if empty:
            self._next_tick_deadline = None


    def _create_controller(self) -> object:
        if self._controller_factory is not None:
            return self._controller_factory()
        try:
            from lsc.gui.pages.recording_controller import RecordingController
            controller = RecordingController()
            # 初始化录制和导出组件，否则录制功能无法使用
            controller.init_capture()
            controller.init_exporter()
            return controller
        except ImportError:
            # RecordingController 已移除（PySide6 GUI 层），Electron 后端不需要 controller
            return None

    def _create_preview(self) -> object:
        if self._preview_factory is not None:
            return self._preview_factory()
        try:
            from lsc.gui.components.mpv_widget import MpvWidget
            return MpvWidget()
        except ImportError:
            # MpvWidget 已移除（PySide6 GUI 层），Electron 后端不需要 preview widget
            return None

    # ── Room CRUD ────────────────────────────────────────────

    def add_room(self, url: str) -> RoomSession | None:
        """Add a room. Returns None if max_rooms limit is reached."""
        if self._thread is not None and threading.current_thread() is not self._thread:
            return self.call(lambda: type(self).add_room(self, url=url))
        max_rooms = _get_configured_max_rooms()
        with self._lock:
            if len(self._rooms) >= max_rooms:
                _log.warning("Room limit reached (%d), cannot add more", max_rooms)
                return None

            # #27: duplicate URL detection
            url_stripped = url.strip().rstrip("/").lower()
            for existing in list(self._rooms.values()):
                existing_url = getattr(existing, "room_url", "").strip().rstrip("/").lower()
                if existing_url == url_stripped:
                    _log.warning("duplicate URL rejected: %s", url)
                    return None
            room_id = uuid4().hex
            controller = self._create_controller()

            # Preview widget is created lazily when the user clicks "预览".
            # This avoids the cost of one libmpv instance per room upfront in
            # multi-room scenarios.
            room = RoomSession(
                room_id=room_id,
                room_url=url.strip(),
                controller=controller,
                preview_widget=None,
            )
            self._rooms[room_id] = room

        # Auto-start global timer when first room is added
        if self.room_count() == 1:
            self._ensure_tick_armed()

        # Persist the updated room list (skip during batch load)
        if not getattr(self, "_batch_loading", False):
            self.save_rooms()

        return room

    def get_room(self, room_id: str) -> RoomSession | None:
        """Return the ``RoomSession`` for ``room_id``, or ``None`` if not found."""
        if self._thread is not None and threading.current_thread() is not self._thread:
            return self.call(lambda: type(self).get_room(self, room_id=room_id))
        with self._lock:
            return self._rooms.get(room_id)

    def get_recording_status(self, room_id: str) -> dict[str, Any]:
        """Return a small, lock-protected recording status snapshot.

        This read-only helper deliberately does not marshal through ``call()``.
        Starting a recording is performed in a worker because URL refresh and the
        FFmpeg startup probe may block; waiting for a busy orchestrator thread just
        to read the final state can otherwise turn a successful recording start into
        a misleading ``orchestrator call timed out`` error.
        """
        with self._lock:
            room = self._rooms.get(room_id)
            if room is None:
                return {
                    "exists": False,
                    "is_recording": False,
                    "last_error": "",
                    "streamer_name": "",
                    "platform_name": "",
                    "preview_enabled": False,
                }
            is_recording = bool(room.is_recording)
            last_error = str(room.last_error or "")
            # 共享录制使用独立 FFmpeg sink。它退出后必须立刻反映到状态，不能只
            # 依赖 RoomSession 的旧布尔值，否则界面会继续显示“录制中”。
            try:
                shared_ingest = get_shared_ingest_registry().get(room_id)
            except Exception:
                shared_ingest = None
            if shared_ingest is not None and not bool(getattr(shared_ingest, "recording_active", False)):
                is_recording = False
                shared_error = str(getattr(shared_ingest, "recording_error", "") or "")
                if shared_error:
                    last_error = shared_error
            return {
                "exists": True,
                "is_recording": is_recording,
                "last_error": last_error,
                "streamer_name": str(room.streamer_name or ""),
                "platform_name": str(room.platform_name or ""),
                "preview_enabled": bool(room.preview_enabled),
            }

    def list_rooms(self) -> list[RoomSession]:
        """Return all currently managed ``RoomSession`` objects."""
        if self._thread is not None and threading.current_thread() is not self._thread:
            return self.call(lambda: type(self).list_rooms(self))
        with self._lock:
            return list(self._rooms.values())

    def room_count(self) -> int:
        """Return the number of rooms currently managed."""
        if self._thread is not None and threading.current_thread() is not self._thread:
            return self.call(lambda: type(self).room_count(self))
        with self._lock:
            return len(self._rooms)

    def max_rooms(self) -> int:
        """Return the configured upper limit on concurrently managed rooms."""
        if self._thread is not None and threading.current_thread() is not self._thread:
            return self.call(lambda: type(self).max_rooms(self))
        return _get_configured_max_rooms()

    def remove_room(self, room_id: str) -> bool:
        """Remove a room and clean up all associated resources.

        Stops any active preview or recording, cancels pending async workers
        (connect, metadata probe, refresh), disposes the controller and
        preview widget, and persists the updated room list. If this was the
        last room, the global heartbeat timer is also stopped.

        Returns:
            True if the room was found and removed; False otherwise.
        """
        if self._thread is not None and threading.current_thread() is not self._thread:
            return self.call(lambda: type(self).remove_room(self, room_id=room_id))
        with self._lock:
            room = self._rooms.pop(room_id, None)
        if room is None:
            return False
        self._pending_stream_infos.pop(room_id, None)
        self._stream_leases.pop(room_id, None)
        self._lease_managers.pop(room_id, None)
        self._lease_refresh_inflight.discard(room_id)
        self._missing_controller_tick_warned.discard(room_id)
        # 若正在循环试听这个房间,停止 timer,避免删房后空转。
        if self._loop_room_id == room_id:
            self.stop_range_loop()
        # 先取消后台重连线程，防止删房后重连线程继续操作已移除的 room 对象
        self._cancel_reconnect_thread(room_id)
        if room.preview_enabled:
            self.stop_preview(room_id)
        # 非阻塞停止录制，避免 capture.stop() 阻塞 Qt 主线程最长 13 秒
        if room.is_recording:
            controller = room.controller
            if controller is not None:
                try:
                    controller.stop_recording_async()
                except Exception as exc:
                    _log.debug("操作异常（已忽略）: %s", exc)
            room.is_recording = False
            room.is_reconnecting = False

        # Cancel pending async connect
        self._cancel_connect_worker(room_id)

        # Cancel pending metadata probe (avoid late callback into a removed room)
        probe = self._metadata_probe_jobs.pop(room_id, None)
        if probe is not None:
            probe.cancel.set()

        controller = room.controller
        if controller is not None:
            cleanup_fn = getattr(controller, "cleanup", None)
            if callable(cleanup_fn):
                try:
                    cleanup_fn()
                except Exception as exc:
                    _log.warning("Controller cleanup failed for room %s: %s", room_id, exc)

        # Cleanup preview widget
        preview = room.preview_widget
        if preview is not None:
            cleanup_fn = getattr(preview, "cleanup", None)
            if callable(cleanup_fn):
                try:
                    cleanup_fn()
                except Exception as exc:
                    _log.warning("Preview cleanup failed for room %s: %s", room_id, exc)

        # Stop global timer when last room is removed
        if not self.room_count():
            self._disarm_tick_if_empty()

        # Persist the updated room list
        self.save_rooms()

        return True

    # ── Persistence ─────────────────────────────────────────

    def _config_file_path(self) -> str:
        """Return the JSON config file path for room persistence."""
        base = os.path.join(
            os.path.expanduser("~"),
            ".lsc",
            "LiveStreamClipper",
        )
        os.makedirs(base, exist_ok=True)
        return os.path.join(base, "rooms.json")

    def _backup_config_path(self) -> str:
        return self._config_file_path() + ".bak"

    def _temp_config_path(self) -> str:
        return self._config_file_path() + ".tmp"

    def _serialize_room(self, room: RoomSession) -> dict[str, Any]:
        """把单个房间序列化为可持久化的 dict。

        仅保存用户偏好与选区(跨重启稳定的纯数据),不保存瞬时连接/录制状态、
        controller/preview_widget 等运行时句柄。mark_in/mark_out 仍需对应房间
        重新连接后才有意义的时长,但保留下来可避免用户白标选区。
        """
        entry: dict[str, Any] = {"url": room.room_url}
        if room.mark_in is not None:
            entry["mark_in"] = float(room.mark_in)
        if room.mark_out is not None:
            entry["mark_out"] = float(room.mark_out)
        # include_in_cut / preview_muted 与默认值不同时才存,减少噪声
        # (RoomSession 的默认值见 session.py 字段定义)
        if room.include_in_cut is not True:
            entry["include_in_cut"] = room.include_in_cut
        if room.preview_muted is not True:
            entry["preview_muted"] = room.preview_muted
        # #29: persist alignment state across restarts
        if room.align_group_id:
            entry["align_group_id"] = room.align_group_id
        if room.content_offset:
            entry["content_offset"] = room.content_offset
        return entry

    def save_rooms(self) -> int:
        """Persist the current room list atomically (debounced 1s, fsync every N writes).

        Writes to a temporary file first, then renames it into place.
        Keeps a .bak copy of the previous config so load_rooms can recover
        from a corrupt primary file.

        Returns number of rooms queued for save.
        """
        if self._thread is not None and threading.current_thread() is not self._thread:
            return self.call(lambda: type(self).save_rooms(self))
        data = {
            "version": 2,
            "saved_at": datetime.now().isoformat(timespec="seconds"),
            "rooms": [self._serialize_room(room) for room in self._rooms.values()],
        }
        count = len(self._rooms)
        self._pending_save_payload = data
        self._pending_save_count = count
        timer = getattr(self, "_save_rooms_timer", None)
        if timer is not None:
            try:
                timer.cancel()
            except Exception as exc:
                _log.debug("cancel save_rooms timer failed: %s", exc)
        timer = threading.Timer(1.0, self._flush_save_rooms)
        timer.daemon = True
        self._save_rooms_timer = timer
        timer.start()
        return count

    def flush_save_rooms(self) -> int:
        """Cancel debounce and flush immediately (tests / shutdown)."""
        if self._thread is not None and threading.current_thread() is not self._thread:
            return self.call(lambda: type(self).flush_save_rooms(self))
        timer = getattr(self, "_save_rooms_timer", None)
        if timer is not None:
            try:
                timer.cancel()
            except Exception as exc:
                _log.debug("cancel save_rooms timer failed: %s", exc)
            self._save_rooms_timer = None
        return self._flush_save_rooms()

    def _flush_save_rooms(self) -> int:
        """写出合并后的房间配置。"""
        import json

        self._save_rooms_timer = None
        data = getattr(self, "_pending_save_payload", None)
        count = int(getattr(self, "_pending_save_count", 0) or 0)
        self._pending_save_payload = None
        if data is None:
            return 0

        path = self._config_file_path()
        tmp_path = self._temp_config_path()
        bak_path = self._backup_config_path()
        write_n = int(getattr(self, "_rooms_write_count", 0) or 0) + 1
        self._rooms_write_count = write_n
        do_fsync = write_n % 5 == 0

        try:
            with _ROOMS_PERSIST_LOCK:
                with open(tmp_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                    f.flush()
                    if do_fsync:
                        os.fsync(f.fileno())

                if os.path.isfile(path):
                    try:
                        os.replace(path, bak_path)
                    except Exception as exc:
                        _log.warning("Failed to create config backup: %s", exc)

                os.replace(tmp_path, path)
            _log.info("Saved %d rooms to %s", count, path)
            return count
        except Exception as exc:
            _log.error("Failed to save rooms: %s", exc)
            return 0

    def _load_json_file(self, path: str) -> dict[str, Any] | None:
        """Load and parse a JSON config file. Returns None on any failure."""
        import json
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else None
        except Exception as exc:
            _log.debug("Failed to load JSON from %s: %s", path, exc)
            return None

    def load_rooms(self) -> int:
        """Load rooms from the persisted config.

        If the primary config file is missing or corrupt, attempts to fall
        back to the .bak copy. Returns number of loaded rooms.
        """
        if self._thread is not None and threading.current_thread() is not self._thread:
            return self.call(lambda: type(self).load_rooms(self))
        path = self._config_file_path()
        bak_path = self._backup_config_path()

        data = self._load_json_file(path)
        if data is None and os.path.isfile(bak_path):
            _log.warning("Primary config missing or corrupt, trying backup: %s", bak_path)
            data = self._load_json_file(bak_path)

        if data is None:
            if not os.path.isfile(path):
                _log.info("No saved room config at %s", path)
            else:
                _log.warning("Failed to load rooms from %s and backup is unavailable", path)
            return 0

        rooms = data.get("rooms", []) if isinstance(data, dict) else []
        if not isinstance(rooms, list):
            return 0

        loaded = 0
        self._batch_loading = True
        for item in rooms:
            if not isinstance(item, dict):
                continue
            url = item.get("url", "").strip()
            if not url:
                continue
            room = self.add_room(url)
            if room is None:
                continue
            loaded += 1
            # 恢复用户偏好与选区(向后兼容:缺失字段保持 RoomSession 默认值)
            if "mark_in" in item and item["mark_in"] is not None:
                try:
                    room.mark_in = float(item["mark_in"])
                except (TypeError, ValueError):
                    pass
            if "mark_out" in item and item["mark_out"] is not None:
                try:
                    room.mark_out = float(item["mark_out"])
                except (TypeError, ValueError):
                    pass
            if "include_in_cut" in item:
                room.include_in_cut = bool(item["include_in_cut"])
            if "preview_muted" in item:
                room.preview_muted = bool(item["preview_muted"])

        self._batch_loading = False
        self.save_rooms()
        _log.info("Loaded %d rooms from %s", loaded, path)
        return loaded

    # ── Connection ───────────────────────────────────────────

    def connect_room(self, room_id: str, *, async_mode: bool = False,
                     quality_preset: str = "原画") -> bool:
        """Connect a room to its live stream.

        Args:
            room_id: The room identifier.
            async_mode: If True, parsing runs in a background thread and
                ``room_connect_finished`` is emitted on completion.

        Returns:
            For sync mode: True if connected successfully.
            For async mode: True if the background job was launched.
        """
        if self._thread is not None and threading.current_thread() is not self._thread:
            return self.call(lambda: type(self).connect_room(self, room_id=room_id, async_mode=async_mode, quality_preset=quality_preset))
        room = self.get_room(room_id)
        if room is None:
            return False

        if async_mode:
            return self._connect_room_async(room, quality_preset=quality_preset)
        return self._connect_room_sync(room, quality_preset=quality_preset)

    def _connect_room_sync(self, room: RoomSession, quality_preset: str = "原画") -> bool:
        try:
            cfg = load_config()
            if (
                self._pipeline_component_enabled("unified_resolver_v2", room, cfg)
                and self._pipeline_component_enabled("media_probe_v2", room, cfg)
                and self._pipeline_component_enabled("stream_lease_v2", room, cfg)
            ):
                info = self._resolve_v2_stream_info(room, cfg, quality_preset=quality_preset)
                return bool(info and self._apply_stream_info(room, info))
        except Exception as exc:
            room.set_error(f"V2 直播流连接失败: {exc}")
            return False
        info = parse_stream(room.room_url)
        if info.is_live and quality_preset:
            stream_url, selected_quality = select_quality(info, quality_preset)
            if stream_url:
                info.stream_url = stream_url
                info.selected_quality = selected_quality
        return self._apply_stream_info(room, info)

    def _connect_room_async(self, room: RoomSession, quality_preset: str = "原画") -> bool:
        if room.room_id in self._connect_jobs:
            return False  # Already connecting

        room.is_connecting = True
        room.last_error = ""

        job = _ConnectJob(room.room_id, room.room_url, quality_preset)
        self._connect_jobs[room.room_id] = job

        def _run() -> None:
            try:
                cfg = load_config()
                if (
                    self._pipeline_component_enabled("unified_resolver_v2", room, cfg)
                    and self._pipeline_component_enabled("media_probe_v2", room, cfg)
                    and self._pipeline_component_enabled("stream_lease_v2", room, cfg)
                ):
                    info = self._resolve_v2_stream_info(
                        room,
                        cfg,
                        quality_preset=job.quality_preset,
                        cancellation=job.cancel,
                    )
                    result = (
                        room.room_id,
                        bool(info),
                        "" if info else (room.last_error or "V2 直播流连接失败"),
                        info,
                    )
                else:
                    result = job.run()
            except Exception as exc:
                result = (room.room_id, False, str(exc), None)
            self.submit(self._on_connect_finished, *result)

        self._submit_worker(_run)
        return True

    def _on_connect_finished(self, room_id: str, success: bool, error: str,
                             info: StreamInfo | None) -> None:
        self._connect_jobs.pop(room_id, None)
        room = self.get_room(room_id)
        if room is None or room.disconnect_requested:
            return

        if success and info is not None:
            # Reuse the StreamInfo parsed in the worker thread — no second HTTP request.
            self._apply_stream_info(room, info)
            # 异步探测分辨率/帧率回填详情面板（原详情面板"帧率"恒为 --）。
            if info.stream_url:
                from lsc.platforms.capabilities import (
                    get_platform_capabilities,
                    uses_ingest_probe,
                )

                if not uses_ingest_probe(get_platform_capabilities(info.platform)):
                    self._probe_metadata_async(room_id, info.stream_url)
        else:
            room.set_error(error or "连接失败")

        self.bus.emit("room_connect_finished", room_id, success, error)

    def _probe_metadata_async(self, room_id: str, stream_url: str) -> None:
        """启动后台 ffprobe 探测直播流分辨率/帧率，结果回填 RoomSession。"""
        room = self.get_room(room_id)
        if room is None or room.controller is None:
            return
        # 若已有探测在跑，先取消旧的
        existing = self._metadata_probe_jobs.pop(room_id, None)
        if existing is not None:
            existing.cancel.set()
        job = _MetadataProbeJob(room_id, stream_url, room.controller)
        self._metadata_probe_jobs[room_id] = job

        def _run() -> None:
            result = job.run()
            if result is not None:
                self.submit(self._on_probe_finished, *result)

        self._submit_worker(_run)

    def _on_probe_finished(self, room_id: str, resolution: str, fps: str) -> None:
        """ffprobe 探测完成回调（主线程执行）：回填分辨率/帧率并刷新 UI。"""
        self._metadata_probe_jobs.pop(room_id, None)
        room = self.get_room(room_id)
        if room is None:
            return
        room.stream_resolution = resolution
        room.stream_fps = fps
        self._dirty_connection = True


    def _apply_stream_info(self, room: RoomSession, info: StreamInfo) -> bool:
        """Apply parsed StreamInfo to room session and controller."""
        room.apply_stream_info(info)
        raw_context = getattr(info, "raw", {}) or {}
        if isinstance(raw_context, dict):
            context = raw_context.get("network_context")
            if isinstance(context, dict):
                room.network_context = dict(context)
        room.preview_error = ""
        # Mark state changed for UI refresh
        self._dirty_connection = True
        if not info.is_live or not info.stream_url:
            room.set_error(info.error or "连接失败")
            return False
        controller = room.controller
        if controller is not None:
            legacy_info = info.to_legacy_dict()
            controller.stream_url = info.stream_url
            controller.input_args = legacy_info.get("_inputArgs", [])
            controller.selected_quality = legacy_info.get("selectedQuality", info.selected_quality)
        return True

    def _cancel_connect_worker(self, room_id: str) -> None:
        """取消进行中的异步连接 worker,避免其完成后回写覆盖用户的断开/删除意图。"""
        job = self._connect_jobs.pop(room_id, None)
        if job is not None:
            job.cancel.set()

    def _cancel_reconnect_thread(self, room_id: str) -> None:
        """取消后台重连线程，防止断开/删除后重连线程继续修改房间状态。"""
        room = self.get_room(room_id)
        if room is None:
            return
        room._cancel_reconnect.set()
        room.is_reconnecting = False
        room.reconnect_next_attempt_at = 0.0
        t = getattr(room, '_reconnect_thread', None)
        if t is not None:
            try:
                if hasattr(t, 'is_alive') and t.is_alive():
                    t.join(timeout=2.0)
            except Exception as exc:
                _log.debug("操作异常（已忽略）: %s", exc)
        room._reconnect_thread = None

    def disconnect_room(self, room_id: str) -> bool:
        """Disconnect a room from its live stream.

        Cancels any pending async connection, stops the preview if active,
        stops recording if active (non-blocking), cancels background reconnect,
        and clears all connection-related state. The room object itself is
        retained so the user can reconnect later.

        Returns:
            True if the room was found and disconnected.
        """
        if self._thread is not None and threading.current_thread() is not self._thread:
            return self.call(lambda: type(self).disconnect_room(self, room_id=room_id))
        room = self.get_room(room_id)
        if room is None:
            return False
        # 先取消进行中的连接,否则 worker 跑完会通过 _on_connect_finished 把
        # is_connected 重新置 True,覆盖用户的断开意图。
        self._cancel_connect_worker(room_id)
        # 取消后台重连线程，防止断开后重连线程继续修改房间状态
        self._cancel_reconnect_thread(room_id)
        if room.preview_enabled:
            self.stop_preview(room_id)
        # 非阻塞停止录制，避免阻塞 Qt 主线程
        if room.is_recording:
            controller = room.controller
            if controller is not None:
                try:
                    controller.stop_recording_async()
                except Exception as exc:
                    _log.debug("操作异常（已忽略）: %s", exc)
        # 重置所有状态
        room.is_connected = False
        room.is_connecting = False
        room.is_recording = False
        room.is_reconnecting = False
        room.reconnect_attempts = 0
        room.reconnect_next_attempt_at = 0.0
        room.preview_error = ""
        room.last_error = ""
        return True

    # ── Preview ──────────────────────────────────────────────

    def get_active_preview_count(self) -> int:
        """Return the number of rooms with an enabled, un-paused preview."""
        if self._thread is not None and threading.current_thread() is not self._thread:
            return self.call(lambda: type(self).get_active_preview_count(self))
        return sum(1 for r in self._rooms.values()
                   if r.preview_enabled and not r.preview_paused)

    def start_preview(self, room_id: str) -> bool:
        """Enable preview playback for a connected room.

        A preview widget is created lazily on first use. The method enforces
        ``MAX_CONCURRENT_PREVIEWS``; if the limit is reached the request is
        rejected and an error is set on the room.

        Returns:
            True if the preview was started; False if the room is not
            connected, the widget could not be created, or the concurrency
            limit was reached.
        """
        if self._thread is not None and threading.current_thread() is not self._thread:
            return self.call(lambda: type(self).start_preview(self, room_id=room_id))
        room = self.get_room(room_id)
        if room is None or not room.is_connected:
            return False

        # Enforce preview concurrency limit
        max_previews = _get_configured_max_previews()
        if self.get_active_preview_count() >= max_previews:
            _log.warning("Preview limit reached (%d), cannot start more", max_previews)
            room.preview_error = f"预览数已达上限 ({max_previews})"
            return False

        # Lazy creation: only create the mpv widget when the user actually
        # wants to preview this room.
        if room.preview_widget is None:
            try:
                room.preview_widget = self._create_preview()
            except Exception as exc:
                _log.warning("Preview widget creation failed: %s", exc)
                room.preview_error = "无法创建预览组件"
                return False

        # 如果组件创建成功但后端（libmpv）未初始化，提前失败并给出友好提示
        is_available_fn = getattr(room.preview_widget, "is_available", None)
        if callable(is_available_fn) and not is_available_fn():
            room.preview_error = getattr(room.preview_widget, "init_error", lambda: "预览初始化失败")()
            return False

        room.preview_enabled = True
        room.preview_paused = False
        room.preview_error = ""

        # 播放交由调用方在 widget 嵌入卡片并 reparent/rebind 后触发
        # （见 play_preview_stream）。此处不抢跑播放：mpv 若绑定到尚未稳定的
        # HWND，reparent 后句柄变更会导致首帧渲染丢失、画面黑屏。
        return True

    def play_preview_stream(self, room_id: str) -> None:
        """在 widget 嵌入卡片并完成 rebind 后播放直播流。

        ``start_preview`` 仅创建 widget 并置状态，真正的 ``mpv.play`` 必须在
        reparent/rebind 之后再触发，否则在 Windows 上 reparent 改变 HWND 会让
        绑定到旧句柄的播放请求失效，表现为预览黑屏。本方法封装了
        ``_play_stream``，供 ``MultiRoomPage._on_preview`` 在延迟回调中调用。
        """
        if self._thread is not None and threading.current_thread() is not self._thread:
            return self.call(lambda: type(self).play_preview_stream(self, room_id=room_id))
        room = self.get_room(room_id)
        if room is None:
            return
        if not room.preview_enabled:
            return
        self._play_stream(room)

    def pause_preview(self, room_id: str) -> bool:
        """Pause the preview widget without tearing down the mpv instance.

        Returns:
            True if the room's preview was paused; False if the room or
            preview does not exist.
        """
        if self._thread is not None and threading.current_thread() is not self._thread:
            return self.call(lambda: type(self).pause_preview(self, room_id=room_id))
        room = self.get_room(room_id)
        if room is None or not room.preview_enabled:
            return False
        room.preview_paused = True

        widget = room.preview_widget
        if widget is not None:
            widget.pause()
        return True

    def resume_preview(self, room_id: str) -> bool:
        """Resume a previously paused preview widget.

        Returns:
            True if the room's preview was resumed; False if the room or
            preview does not exist.
        """
        if self._thread is not None and threading.current_thread() is not self._thread:
            return self.call(lambda: type(self).resume_preview(self, room_id=room_id))
        room = self.get_room(room_id)
        if room is None or not room.preview_enabled:
            return False
        room.preview_paused = False

        widget = room.preview_widget
        if widget is not None:
            widget.resume()
        return True

    def stop_preview(self, room_id: str) -> bool:
        """Stop preview playback and release the preview widget.

        The underlying mpv instance is stopped but retained on the room so
        that a subsequent ``start_preview`` can resume without a full
        re-creation.

        Returns:
            True if the room's preview was stopped; False if the room does
            not exist.
        """
        if self._thread is not None and threading.current_thread() is not self._thread:
            return self.call(lambda: type(self).stop_preview(self, room_id=room_id))
        room = self.get_room(room_id)
        if room is None:
            return False
        room.preview_enabled = False
        room.preview_paused = False

        widget = room.preview_widget
        if widget is not None:
            widget.stop()
        return True

    def set_preview_muted(self, room_id: str, muted: bool) -> None:
        """Set the mute state of the room's preview widget.

        The preference is also persisted on ``RoomSession.preview_muted`` so
        it survives across preview stop/start cycles.
        """
        if self._thread is not None and threading.current_thread() is not self._thread:
            return self.call(lambda: type(self).set_preview_muted(self, room_id=room_id, muted=muted))
        room = self.get_room(room_id)
        if room is None:
            return
        room.preview_muted = muted

        # Pass mute state to mpv widget
        widget = room.preview_widget
        if widget is not None:
            widget.set_muted(muted)

    def seek_preview(self, room_id: str, seconds: float) -> bool:
        """Seek the room's preview widget to an absolute position.

        Also updates ``controller.current_sec`` so the timeline reflects
        the new position immediately even before the next widget callback.
        Returns False if the room or preview widget does not exist.
        """
        if self._thread is not None and threading.current_thread() is not self._thread:
            return self.call(lambda: type(self).seek_preview(self, room_id=room_id, seconds=seconds))
        room = self.get_room(room_id)
        if room is None:
            return False
        controller = room.controller
        if controller is not None:
            controller.current_sec = max(0.0, float(seconds))
        widget = room.preview_widget
        if widget is None:
            return False
        seek_fn = getattr(widget, "seek", None)
        if callable(seek_fn):
            seek_fn(seconds)
        return True

    def get_preview_position(self, room_id: str) -> float:
        """Return the current playback position of the room's preview widget.

        Falls back to ``controller.current_sec`` when the widget is not
        available or reports no position (e.g. live streams).
        """
        if self._thread is not None and threading.current_thread() is not self._thread:
            return self.call(lambda: type(self).get_preview_position(self, room_id=room_id))
        room = self.get_room(room_id)
        if room is None:
            return 0.0
        widget = room.preview_widget
        if widget is not None:
            pos_fn = getattr(widget, "time_pos", None)
            if callable(pos_fn):
                try:
                    pos = float(pos_fn() or 0.0)
                    if pos > 0:
                        return pos
                except Exception as exc:
                    _log.debug("操作异常（已忽略）: %s", exc)
        controller = room.controller
        if controller is not None:
            return float(getattr(controller, "current_sec", 0.0) or 0.0)
        return 0.0

    def get_preview_duration(self, room_id: str) -> float:
        """Return the duration reported by the preview widget.

        For live streams mpv may report 0; callers should fall back to
        ``controller.total_sec`` in that case.
        """
        if self._thread is not None and threading.current_thread() is not self._thread:
            return self.call(lambda: type(self).get_preview_duration(self, room_id=room_id))
        room = self.get_room(room_id)
        if room is None:
            return 0.0
        widget = room.preview_widget
        if widget is not None:
            dur_fn = getattr(widget, "duration", None)
            if callable(dur_fn):
                try:
                    return float(dur_fn() or 0.0)
                except Exception as exc:
                    _log.debug("操作异常（已忽略）: %s", exc)
        return 0.0

    def align_previews_to_live(self) -> int:
        """Seek every active preview to its live edge (latest position).

        This gives users a one-click way to re-synchronise all multi-room
        previews to "now" after seeking backwards in one of them. Returns
        the number of previews that were aligned.

        For live streams the duration reported by mpv is often 0, so we use
        the maximum current playback position across all selected previews as
        the live edge target instead.
        """
        if self._thread is not None and threading.current_thread() is not self._thread:
            return self.call(lambda: type(self).align_previews_to_live(self))
        candidate_positions: list[float] = []
        active_rooms: list[RoomSession] = []
        for room in list(self._rooms.values()):
            if not room.preview_enabled or room.preview_widget is None:
                continue
            active_rooms.append(room)
            pos = self.get_preview_position(room.room_id)
            if pos > 0:
                candidate_positions.append(pos)
            duration = self.get_preview_duration(room.room_id)
            if duration > 0:
                candidate_positions.append(duration)
            total = float(getattr(room.controller, "total_sec", 0) or 0)
            if total > 0:
                candidate_positions.append(total)

        if not candidate_positions:
            return 0

        target = max(candidate_positions)
        aligned = 0
        for room in active_rooms:
            self.seek_preview(room.room_id, target)
            aligned += 1
        return aligned

    # ── Range loop preview ─────────────────────────────────

    def start_range_loop(self, room_id: str, start: float, end: float) -> None:
        """循环播放 [start, end]。

        优先使用 mpv 原生 A-B 循环（精度高、无 polling 开销）；
        若预览组件不支持，则回退到 50ms 轮询检查位置并手动 seek。
        """
        if self._thread is not None and threading.current_thread() is not self._thread:
            return self.call(lambda: type(self).start_range_loop(self, room_id=room_id, start=start, end=end))
        self.stop_range_loop()
        self._loop_room_id = room_id
        self._loop_start = start
        self._loop_end = end
        self.seek_preview(room_id, start)
        room = self.get_room(room_id)
        widget = room.preview_widget if room else None
        if widget is not None and hasattr(widget, "set_ab_loop"):
            try:
                self._loop_native = widget.set_ab_loop(start, end)
            except Exception:
                self._loop_native = False
        if not self._loop_native:
            self._loop_deadline = time.monotonic() + 0.05

    def stop_range_loop(self) -> None:
        """停止选区循环播放。"""
        if self._thread is not None and threading.current_thread() is not self._thread:
            return self.call(lambda: type(self).stop_range_loop(self))
        if self._loop_native:
            room = self.get_room(self._loop_room_id or "")
            widget = room.preview_widget if room else None
            if widget is not None and hasattr(widget, "clear_ab_loop"):
                try:
                    widget.clear_ab_loop()
                except Exception as exc:
                    _log.debug("操作异常（已忽略）: %s", exc)
            self._loop_native = False
        self._loop_deadline = None
        self._loop_room_id = None

    def is_range_loop_active(self) -> bool:
        """返回当前是否正在循环试听。"""
        if self._thread is not None and threading.current_thread() is not self._thread:
            return self.call(lambda: type(self).is_range_loop_active(self))
        return self._loop_room_id is not None

    def _on_preview_loop_tick(self) -> None:
        """循环试听的心跳：检查播放位置是否超出选区，若是则 seek 回起点。"""
        if self._loop_room_id is None:
            return
        pos = self.get_preview_position(self._loop_room_id)
        if pos >= self._loop_end or pos < self._loop_start:
            self.seek_preview(self._loop_room_id, self._loop_start)

    def seek_selected_previews(self, room_ids: list[str], seconds: float) -> None:
        """Seek the previews of every room in ``room_ids`` to ``seconds``.

        Used by the multi-room page when multiple cards are selected so a
        single timeline drag moves all selected previews at once.
        """
        if self._thread is not None and threading.current_thread() is not self._thread:
            return self.call(lambda: type(self).seek_selected_previews(self, room_ids=room_ids, seconds=seconds))
        for room_id in room_ids:
            self.seek_preview(room_id, seconds)

    def _play_stream(self, room: RoomSession) -> None:
        """Start playing the stream URL in the room's preview widget."""
        widget = room.preview_widget
        if widget is None:
            return

        stream_url = ""
        if room.stream_info and room.stream_info.stream_url:
            stream_url = room.stream_info.stream_url

        if not stream_url:
            return

        # Pass HTTP headers (Referer, User-Agent, etc.) so platform CDNs
        # accept the preview request — without these Douyin/Bilibili/Huya
        # streams return 403 Forbidden.
        headers = {}
        if room.stream_info and room.stream_info.headers:
            headers = dict(room.stream_info.headers)
        set_headers_fn = getattr(widget, "set_stream_headers", None)
        if callable(set_headers_fn) and headers:
            set_headers_fn(headers)

        widget.play_live(stream_url)
        widget.set_muted(room.preview_muted)

    def refresh_stream_url(self, room_id: str, *, force: bool = False,
                           _run_in_background: bool = False) -> bool:
        """Re-parse the stream to get a fresh CDN URL. Returns True on success.

        Args:
            force: If True, bypass the 30s parse cache and force a fresh
                   HTTP request. Used by MSE reconnect to avoid getting
                   a cached (possibly expired) stream URL.
            _run_in_background: 允许在后台线程直接执行（跳过 call 回编守卫），
                   与 start_recording 的 _run_in_background 语义一致——录制重连
                   落地段在 worker 池执行时 URL 刷新（10s+ 网络/探测）不能再
                   排队回编排线程阻塞全局 tick。
        """
        if (self._thread is not None and threading.current_thread() is not self._thread
                and not _run_in_background):
            return self.call(lambda: type(self).refresh_stream_url(self, room_id=room_id, force=force))
        room = self.get_room(room_id)
        if room is None:
            return False
        # 连接刚完成时复用房间级流缓存，避免预览再打一轮 10s+ 平台解析
        if not force and _room_stream_is_reusable(room):
            room.is_connected = True
            _sync_controller_stream(room)
            _log.info("refresh_stream_url reuse cached stream for %s (age<=%.0fs)", room_id, _STREAM_CACHE_REUSE_SEC)
            return True

        # Once a platform is admitted to the V2 pipeline, every refresh and
        # reconnect must keep the Resolver -> Probe -> Lease contract.  The
        # legacy branch below remains the explicit rollback path when the
        # global/platform gate is disabled; silently calling parse_stream here
        # would reintroduce an unprobed URL during recovery.
        try:
            cfg = load_config()
            use_v2 = all(
                self._pipeline_component_enabled(component, room, cfg)
                for component in (
                    "unified_resolver_v2",
                    "media_probe_v2",
                    "stream_lease_v2",
                )
            )
        except Exception as exc:
            _log.debug("V2 refresh gate lookup failed room=%s: %s", room_id, redact_text(exc))
            use_v2 = False

        if use_v2:
            try:
                info = self._resolve_v2_stream_info(
                    room,
                    cfg,
                    quality_preset=getattr(room, "selected_quality", "") or None,
                )
            except Exception as exc:
                _log.warning("V2 refresh_stream_url failed for %s: %s", room_id, redact_text(exc))
                room.set_error(redact_text(exc))
                return False
            if info is None or not info.is_live or not info.stream_url:
                _log.warning("V2 refresh_stream_url returned no playable stream for %s", room_id)
                return False
            room.apply_stream_info(info)
            _sync_controller_stream(room, info)
            return True

        try:
            # 利用 30 秒缓存避免每次预览都重新发 HTTP 请求；
            # 缓存过期或 URL 失效时自动重新解析
            info = parse_stream(room.room_url, force_refresh=force)
        except Exception as exc:
            _log.warning("refresh_stream_url failed for %s: %s", room_id, exc)
            # 解析失败但房间仍有可用缓存时，不打断连接态（预览可继续用旧 URL 尝试）
            if _room_stream_is_reusable(room) or (_get_room_stream_url(room) and not _is_stream_url_expiring(_get_room_stream_url(room))):
                room.is_connected = True
                _sync_controller_stream(room)
                _log.warning("refresh_stream_url falling back to cached stream for %s", room_id)
                return True
            return False
        if not info.is_live or not info.stream_url:
            if _get_room_stream_url(room) and not _is_stream_url_expiring(_get_room_stream_url(room)):
                room.is_connected = True
                _sync_controller_stream(room)
                _log.warning("refresh_stream_url parse offline, keep cached stream for %s", room_id)
                return True
            return False
        room.apply_stream_info(info)
        _sync_controller_stream(room, info)
        return True

    def refresh_stream_url_async(self, room_id: str, callback: Callable[[str, bool], None]) -> None:
        """Refresh stream URL in a background thread, then call callback(room_id, success)."""
        if self._thread is not None and threading.current_thread() is not self._thread:
            return self.call(lambda: type(self).refresh_stream_url_async(self, room_id=room_id, callback=callback))
        room = self.get_room(room_id)
        if room is None:
            callback(room_id, False)
            return

        cancel = threading.Event()
        key = f"_refresh_{room_id}"
        self._refresh_cancels[key] = cancel

        def _run() -> None:
            try:
                if cancel.is_set():
                    return
                ok = self.refresh_stream_url(room_id)
                if cancel.is_set():
                    return
                self.submit(callback, room_id, ok)
            finally:
                self.submit(lambda: self._refresh_cancels.pop(key, None))

        self._submit_worker(_run)

    # ── Mute ─────────────────────────────────────────────────

    def mute_room(self, room_id: str, muted: bool) -> None:
        if self._thread is not None and threading.current_thread() is not self._thread:
            return self.call(lambda: type(self).mute_room(self, room_id=room_id, muted=muted))
        self.set_preview_muted(room_id, muted)

    # ── Recording ────────────────────────────────────────────

    def _refresh_room_stream_for_recording(self, room: RoomSession) -> bool:
        """Refresh short-lived CDN URLs before starting FFmpeg recording."""
        try:
            cfg = load_config()
            if (
                self._pipeline_component_enabled("unified_resolver_v2", room, cfg)
                and self._pipeline_component_enabled("media_probe_v2", room, cfg)
                and self._pipeline_component_enabled("stream_lease_v2", room, cfg)
            ):
                pending = self._pending_stream_infos.pop(room.room_id, None)
                pending_lease = self._stream_leases.get(room.room_id)
                pending_manager = self._lease_managers.get(room.room_id)
                if pending is not None and pending_lease is not None and pending_manager is not None:
                    try:
                        if not pending_manager.is_expired(
                            pending_lease,
                            now=_time.monotonic(),
                        ):
                            return self._apply_stream_info(room, pending)
                    except Exception as exc:
                        _log.debug(
                            "pending V2 lease rejected room=%s: %s",
                            room.room_id,
                            exc,
                        )
                info = self._resolve_v2_stream_info(room, cfg)
                if info is None:
                    return False
                return self._apply_stream_info(room, info)
        except Exception as exc:
            room.set_error(f"V2 直播流解析失败: {exc}")
            _log.warning("V2 stream refresh failed room=%s: %s", room.room_id, exc)
            return False

        # 共享进样已在 preview-only：切录制会重建上游，强制刷流避免缓存 URL 触发 CDN 断流
        force_fresh = False
        try:
            cfg = load_config()
            if getattr(cfg, "shared_ingest_enabled", False):
                shared = get_shared_ingest_registry().get(room.room_id)
                if (
                    shared is not None
                    and not getattr(shared, "recording_active", False)
                    and getattr(shared, "preview_subscribers", 0) > 0
                ):
                    force_fresh = True
                    _log.info(
                        "recording force-refresh stream for %s (shared preview→recording)",
                        room.room_id,
                    )
        except Exception as exc:
            _log.debug("shared ingest force-refresh check failed: %s", exc)

        # 刚连接成功时直接复用缓存，避免录制前再解析一次（抖音/B站可达 10s+）
        if not force_fresh and _room_stream_is_reusable(room):
            room.is_connected = True
            _sync_controller_stream(room)
            _log.info(
                "recording reuse cached stream for %s (age<=%.0fs)",
                room.room_id,
                _STREAM_CACHE_REUSE_SEC,
            )
            return True

        previous_quality = room.selected_quality
        try:
            info = parse_stream(room.room_url, force_refresh=force_fresh)
            if not info or not info.stream_url or not info.is_live or _is_stream_url_expiring(info.stream_url):
                info = parse_stream(room.room_url, force_refresh=True)
        except Exception as exc:
            room.last_error = f"刷新直播流失败: {exc}"
            return False

        if info.is_live and previous_quality and previous_quality in info.quality_urls:
            info.stream_url = info.quality_urls[previous_quality]
            info.selected_quality = previous_quality

        if not info.is_live or not info.stream_url:
            message = info.error or ""
            if not info.is_live or _is_stream_offline_error(message):
                room.set_error(_offline_stream_error_message(message))
            else:
                room.set_error(message or "刷新直播流失败")
            return False

        return self._apply_stream_info(room, info)

    def _resolve_v2_stream_info(
        self,
        room: RoomSession,
        cfg: Any,
        *,
        quality_preset: str | None = None,
        deadline_monotonic: float | None = None,
        cancellation: Any | None = None,
    ) -> StreamInfo | None:
        """Resolve, probe and lease a stream before it reaches FFmpeg."""
        from lsc.platforms.capabilities import uses_ingest_probe
        from lsc.platforms.lease_manager import LeaseManager
        from lsc.platforms.models import ResolveRequest
        from lsc.platforms.probe import summarize_probe_failures
        from lsc.platforms.resolver import (
            limit_probe_candidates,
            probe_candidates,
            resolve_stream_v2,
            select_ingest_lease,
            select_stream_lease,
        )

        deadline = deadline_monotonic or (_time.monotonic() + 20.0)
        result = resolve_stream_v2(
            ResolveRequest(
                source_url=room.room_url,
                requested_quality=quality_preset or room.selected_quality,
                account_ref=str(
                    getattr(room, "account_ref", "")
                    or (getattr(room, "network_context", {}) or {}).get("account_ref", "")
                    or "default"
                ),
                force_refresh=True,
                request_id=f"recording:{room.room_id}",
                deadline_monotonic=deadline,
                cancellation=cancellation,
                network_context=dict(getattr(room, "network_context", {}) or {}),
            )
        )
        credential_status = getattr(result, "credential_status", None)
        room.credential_status = str(
            getattr(credential_status, "value", credential_status)
            or "NOT_CONFIGURED"
        )
        if not result.candidates:
            message = result.error.user_message if result.error else "未找到直播流候选"
            room.set_error(message)
            return None
        capabilities = result.capabilities
        manager = self._lease_managers.setdefault(room.room_id, LeaseManager())
        probes: dict = {}
        if uses_ingest_probe(capabilities):
            lease = select_ingest_lease(
                result,
                room_id=room.room_id,
                lease_manager=manager,
                requested_quality=quality_preset or room.selected_quality,
            )
        else:
            probe_timeout = capabilities.probe_timeout_sec if capabilities else 8.0
            max_concurrency = capabilities.max_connect_concurrency if capabilities else 1
            limited = limit_probe_candidates(result.candidates, capabilities)
            if deadline_monotonic is None and int(max_concurrency or 1) <= 1:
                # Serial signed-CDN failover needs wall time for each line,
                # plus parse overhead already consumed above.
                needed = _time.monotonic() + (
                    float(probe_timeout) * max(1, len(limited))
                ) + 5.0
                deadline = max(deadline, needed)
            probes = probe_candidates(
                limited,
                ffprobe_path=getattr(cfg, "ffprobe_path", "") or "ffprobe",
                timeout_sec=probe_timeout,
                max_concurrency=max_concurrency,
                request_id=f"probe:{room.room_id}",
                deadline_monotonic=deadline,
                cancellation=cancellation,
                network_context=dict(getattr(room, "network_context", {}) or {}),
                platform=result.platform,
                account_ref=str(
                    getattr(room, "account_ref", "")
                    or (getattr(room, "network_context", {}) or {}).get("account_ref", "")
                    or "default"
                ),
            )
            lease = select_stream_lease(
                result,
                probes,
                room_id=room.room_id,
                lease_manager=manager,
                requested_quality=quality_preset or room.selected_quality,
            )
        if lease is None:
            if uses_ingest_probe(capabilities):
                message = (
                    result.error.user_message
                    if result.error and result.error.user_message
                    else "未找到可播放的直播流"
                )
            else:
                message = summarize_probe_failures(probes)
                _log.warning(
                    "V2 probe selected no lease room=%s platform=%s probes=%s",
                    room.room_id,
                    result.platform,
                    {
                        candidate_id: getattr(item, "failure_kind", "")
                        for candidate_id, item in probes.items()
                    },
                )
                _quarantine_failed_probe_candidates(room, result, probes)
            room.set_error(message)
            return None
        self._stream_leases[room.room_id] = lease
        # A V2 StreamInfo represents one probed lease, not the entire
        # resolver response. Publishing every candidate through the legacy
        # quality map lets preview code silently replace the leased URL with
        # an unprobed (and often 403ing) CDN line. Quality changes must run a
        # new resolve -> probe -> lease cycle; compatibility consumers may
        # only reuse the selected lease here.
        quality_urls = (
            {lease.candidate.quality_id: lease.candidate.url}
            if lease.candidate.quality_id
            else {}
        )
        selected_quality = lease.candidate.quality_id or room.selected_quality
        account_ref = str(
            getattr(room, "account_ref", "")
            or (getattr(room, "network_context", {}) or {}).get("account_ref", "")
            or "default"
        )
        return StreamInfo(
            platform=result.platform,
            room_url=room.room_url,
            stream_url=lease.candidate.url,
            title=result.room_title,
            streamer=result.anchor_name,
            is_live=result.live_status == "LIVE",
            quality_urls=quality_urls,
            selected_quality=selected_quality,
            headers=dict(lease.candidate.request_headers),
            raw={
                "v2": True,
                "lease_id": lease.lease_id,
                "lease_generation": lease.generation,
                "candidate_id": lease.candidate.candidate_id,
                "candidate_cdn_id": lease.candidate.cdn_id,
                "candidate_protocol": lease.candidate.protocol,
                "candidate_quality_id": lease.candidate.quality_id,
                "account_ref": account_ref,
                "probe": probes.get(lease.candidate.candidate_id).failure_detail
                if probes.get(lease.candidate.candidate_id)
                else "",
                "network_context": dict(getattr(room, "network_context", {}) or {}),
            },
        )

    def _schedule_v2_lease_refresh(self, room: RoomSession) -> None:
        """Resolve/probe the next lease in the worker pool without stopping sinks.

        The current upstream keeps running while the next signed URL is
        resolved and probed.  The pending result is consumed only by the next
        reconnect/sink restart, so a successful pre-refresh cannot interrupt a
        healthy recording or preview.
        """
        room_id = room.room_id
        lease = self._stream_leases.get(room_id)
        manager = self._lease_managers.get(room_id)
        if lease is None or manager is None:
            return
        try:
            if not manager.needs_refresh(lease, now=_time.monotonic()):
                return
        except Exception as exc:
            _log.debug("lease refresh eligibility failed room=%s: %s", room_id, exc)
            return
        if room_id in self._lease_refresh_inflight:
            return
        if not self._pipeline_component_enabled("stream_lease_v2", room):
            return
        self._lease_refresh_inflight.add(room_id)
        registry = get_shared_ingest_registry()
        supervisor = registry.get_supervisor_if_exists(room_id)
        if supervisor is not None:
            supervisor.begin_refresh("LEASE_REFRESH_PROACTIVE")

        old_lease_id = str(getattr(lease, "lease_id", ""))

        def _refresh() -> None:
            cfg = load_config()
            try:
                info = self._resolve_v2_stream_info(
                    room,
                    cfg,
                    quality_preset=getattr(room, "selected_quality", "") or None,
                )
                refreshed_lease = self._stream_leases.get(room_id)
                self.submit(
                    self._finish_v2_lease_refresh,
                    room_id,
                    old_lease_id,
                    str(getattr(refreshed_lease, "lease_id", "")),
                    info,
                    None,
                )
            except Exception as exc:
                self.submit(
                    self._finish_v2_lease_refresh,
                    room_id,
                    old_lease_id,
                    "",
                    None,
                    exc,
                )

        self._submit_worker(_refresh)

    def _finish_v2_lease_refresh(
        self,
        room_id: str,
        old_lease_id: str,
        refreshed_lease_id: str,
        info: StreamInfo | None,
        error: Exception | None,
    ) -> None:
        self._lease_refresh_inflight.discard(room_id)
        supervisor = get_shared_ingest_registry().get_supervisor_if_exists(room_id)
        room = self._rooms.get(room_id)
        success = bool(info is not None and error is None and room is not None)
        if success and room is not None:
            current = self._stream_leases.get(room_id)
            # Resolver may have been superseded by a reconnect while the worker
            # was running.  Never overwrite a newer generation.
            current_id = str(getattr(current, "lease_id", "")) if current is not None else ""
            if current is not None and current_id == refreshed_lease_id:
                self._pending_stream_infos[room_id] = info
                success = True
            else:
                success = False
        if supervisor is not None:
            supervisor.finish_refresh(
                success,
                reason_code=(
                    "LEASE_REFRESH_READY"
                    if success
                    else "LEASE_REFRESH_FAILED"
                ),
            )
        if not success:
            _log.warning(
                "V2 lease refresh failed room=%s error=%s",
                room_id,
                redact_text(error) if error else "superseded",
            )

    def _maybe_prefetch_http_session_lease(self, room: RoomSession) -> None:
        """Parse the next signed URL before a bounded HTTP session ends."""
        ingest = get_shared_ingest_registry().get(room.room_id)
        needs = getattr(ingest, "needs_lease_prefetch", None)
        if ingest is None or not callable(needs) or not needs():
            return
        if room.room_id in self._lease_refresh_inflight:
            return
        self._lease_refresh_inflight.add(room.room_id)
        room_id = room.room_id

        def _refresh() -> None:
            ok = False
            try:
                ok = bool(self.refresh_stream_url(room_id, force=True))
            except Exception as exc:
                _log.warning(
                    "HTTP session lease prefetch failed room=%s: %s",
                    room_id,
                    redact_text(exc),
                )
            self.submit(self._finish_http_session_lease_prefetch, room_id, ok)

        self._submit_worker(_refresh)

    def _finish_http_session_lease_prefetch(self, room_id: str, ok: bool) -> None:
        self._lease_refresh_inflight.discard(room_id)
        if not ok:
            return
        room = self._rooms.get(room_id)
        ingest = get_shared_ingest_registry().get(room_id)
        stash = getattr(ingest, "stash_pending_lease", None)
        if room is None or ingest is None or not callable(stash):
            return
        info = getattr(room, "stream_info", None)
        url = str(getattr(info, "stream_url", "") or "")
        if not url:
            return
        lease = self._stream_leases.get(room_id)
        stash(
            url=url,
            headers=dict(getattr(info, "headers", {}) or {}),
            network_context=dict(getattr(room, "network_context", {}) or {}),
            lease_id=str(getattr(lease, "lease_id", "") or ""),
            generation=getattr(lease, "generation", None) if lease is not None else None,
        )
        _log.info("Room %s stashed next signed HTTP lease for rotation", room_id)

    def start_recording(self, room_id: str, output_dir: str, encoder: str, crf: int,
                        param_mode: str = "CRF 质量", bitrate: str | None = None,
                        bitrate_unit: str = "kbps",
                        resolution: str | None = None,
                        framerate: str | None = None,
                        audio_bitrate: str | None = None,
                        *, _run_in_background: bool = False) -> bool:
        """Start FFmpeg recording for a single connected room.

        The method performs a pre-flight disk-space check, refreshes the
        short-lived CDN URL, creates a per-room output sub-directory to
        avoid filename collisions in multi-room scenarios, and delegates to
        ``RecordingController.start_recording_with_crf``. On success the
        room's recording state and reconnect parameters are updated so that
        an automatic reconnect can resume if the stream drops.

        Args:
            room_id: Target room identifier.
            output_dir: Base directory for all room recordings.
            encoder: FFmpeg video encoder name (e.g. ``libx264``).
            crf: Constant Rate Factor for quality control.
            param_mode: ``"CRF 质量"`` or ``"CBR 码率"``.
            bitrate: Target bitrate string (used only in CBR mode).
            bitrate_unit: Unit for ``bitrate`` (``"kbps"`` or ``"Mbps"``).
            resolution: Optional output resolution override.
            framerate: Optional output framerate override.
            audio_bitrate: Optional audio bitrate override.

        Returns:
            True if recording started successfully.
        """
        if (self._thread is not None and threading.current_thread() is not self._thread
                and not _run_in_background):
            return self.call(lambda: type(self).start_recording(self, room_id=room_id, output_dir=output_dir, encoder=encoder, crf=crf, param_mode=param_mode, bitrate=bitrate, bitrate_unit=bitrate_unit, resolution=resolution, framerate=framerate, audio_bitrate=audio_bitrate))
        _log.info("[录制诊断] start_recording called for room_id=%s", room_id)
        # ``_run_in_background`` 由 Electron 后端的录制执行器使用：URL 刷新和
        # FFmpeg 首帧探测可能超过 10 秒，不能再排队到编排线程后触发 call 超时。
        # 读取房间引用受 _lock 保护；后续耗时工作不持锁，避免阻塞其他状态读取。
        with self._lock:
            room = self._rooms.get(room_id)
        if room is None:
            _log.warning("[录制诊断] room not found: %s", room_id)
            return False
        previous_recording_id = str(getattr(room, "recording_id", "") or "")
        keep_started_at = room.record_started_at if room.is_reconnecting else None

        def _commit_recording_epoch(media_start_mono: float | None) -> None:
            """提交新录制 epoch；重启时清除旧音频对齐，禁止复用陈旧 offset。"""
            new_recording_id = uuid4().hex
            room.recording_id = new_recording_id
            get_timeline_service().on_recording_id_change(
                room_id,
                new_recording_id,
                media_start_mono=media_start_mono,
            )
            if previous_recording_id:
                if room.align_group_id or room.content_offset:
                    _log.warning(
                        "Room %s recording epoch changed; clearing stale alignment group %s",
                        room_id,
                        room.align_group_id,
                    )
                room.align_group_id = ""
                room.content_offset = 0.0

        controller = room.controller
        if controller is None:
            _log.warning("[录制诊断] controller is None for room %s (is_connected=%s)", room_id, room.is_connected)
            room.last_error = "录制控制器未初始化"
            return False
        effective_encoder = encoder
        normalized_encoder = str(encoder or "").lower()
        if "nvenc" in normalized_encoder:
            try:
                nvenc_available = bool(controller.is_nvenc_available())
            except Exception:
                nvenc_available = False
            if not nvenc_available:
                effective_encoder = "H.265 CPU" if "265" in normalized_encoder or "hevc" in normalized_encoder else "H.264 CPU"
                _log.warning(
                    "NVENC is unavailable; falling back to %s for room %s",
                    effective_encoder,
                    room_id,
                )
        _log.info("[录制诊断] controller OK, is_connected=%s, stream_url=%s", room.is_connected, bool(getattr(controller, 'stream_url', None)))
        # 预览刷新失败可能误清 is_connected，但流缓存仍可用
        if not room.is_connected and not _heal_connected_flag(room):
            room.last_error = "房间未连接"
            return False
        # Pre-flight disk space check；重连中用 2GB 阈值，开录仍用默认 8GB/路
        _preflight_min = (
            _MIN_FREE_BYTES_WHILE_RECORDING if room.is_reconnecting else None
        )
        preflight = RecordingService.preflight_check(
            output_dir,
            concurrent_streams=1,
            min_free_bytes_per_stream=_preflight_min,
        )
        if preflight:
            # Fallback chain for unwritable / full output directories:
            #   1. If the configured dir fails, try ~/.lsc/output (user home, usually writable).
            #   2. If that also fails, surface the error and abort so FFmpeg
            #      doesn't start and immediately die mid-write.
            fallback_base = os.path.join(os.path.expanduser('~'), '.lsc', 'output')
            if os.path.abspath(fallback_base) != os.path.abspath(output_dir):
                _log.warning("预检失败 %s，回退到 %s", output_dir, fallback_base)
                fallback_preflight = RecordingService.preflight_check(
                    fallback_base,
                    concurrent_streams=1,
                    min_free_bytes_per_stream=_preflight_min,
                )
                if not fallback_preflight:
                    output_dir = fallback_base
                    preflight = ""
                else:
                    _log.warning("回退目录预检也失败: %s", fallback_preflight)
            if preflight:
                room.last_error = preflight
                _log.warning("录制预检失败: %s", preflight)
                return False
        _log.info("[录制诊断] refreshing stream for recording...")
        if not self._refresh_room_stream_for_recording(room):
            _log.warning("[录制诊断] stream refresh failed, last_error=%s", room.last_error)
            return False
        stream_url = controller.stream_url
        input_args = controller.input_args
        _log.info("[录制诊断] stream refreshed, stream_url=%s", bool(stream_url))

        # Per-room output directory:
        #   - Uses a readable name (platform_streamer_shortid) instead of a raw UUID.
        #   - Appends a numeric suffix if the directory already exists, which can
        #     happen when two rooms point at the same streamer/short_id combo.
        #   - Falls back to ~/.lsc/output on OSError (e.g. sandboxed environments).
        if room.is_reconnecting and room.reconnect_output_dir and os.path.isdir(room.reconnect_output_dir):
            room_output_dir = room.reconnect_output_dir
        else:
            room_output_dir = _make_room_output_dir(output_dir, room)
            # 若可读目录名已存在（同名主播+同 short_id 概率极低），追加序号避免覆盖
            original_room_output_dir = room_output_dir
            suffix = 1
            while os.path.exists(room_output_dir):
                room_output_dir = f"{original_room_output_dir}_{suffix}"
                suffix += 1
        try:
            os.makedirs(room_output_dir, exist_ok=True)
        except OSError:
            # 默认目录不可写（如沙箱环境），回退到 ~/.lsc/output
            fallback_base = os.path.join(os.path.expanduser('~'), '.lsc', 'output')
            fallback_dir = os.path.join(fallback_base, os.path.basename(room_output_dir))
            _log.warning("录制目录不可写 %s，回退到 %s", room_output_dir, fallback_dir)
            room_output_dir = fallback_dir
            try:
                os.makedirs(room_output_dir, exist_ok=True)
            except OSError as exc:
                room.last_error = f"录制目录不可写，请在设置中修改输出目录（{exc.strerror or exc}）"
                return False

        _log.info("[录制诊断] calling start_recording_with_crf, output_dir=%s", room_output_dir)
        shared_profile = self._build_recording_profile(
            effective_encoder, crf, param_mode, bitrate, bitrate_unit, resolution, framerate, audio_bitrate,
        )
        shared_output, shared_media_start, shared_error = self._start_shared_recording_if_enabled(
            room, room_output_dir, stream_url, shared_profile,
        )
        if shared_output:
            output_path = shared_output
            media_start_mono = shared_media_start
            room.is_recording = True
            room.record_output_path = output_path
            room.record_started_at = keep_started_at or datetime.now()
            # 共享进样模式也需要同步 controller.video_path，否则导出时找不到文件
            if controller is not None:
                controller.video_path = output_path
            room.recording_start_mono = _time.monotonic()
            room.recording_media_start_mono = media_start_mono or None
            room._first_frame_corrected = False
            room._shared_ingest_last_file_size = 0
            room._shared_ingest_stall_checks = 0
            _commit_recording_epoch(
                room.recording_media_start_mono or room.recording_start_mono
            )
            self._dirty_recording = True
            room.reconnect_output_dir = room_output_dir
            room.reconnect_encoder = effective_encoder
            room.reconnect_crf = crf
            room.reconnect_param_mode = param_mode
            room.reconnect_bitrate = bitrate or ""
            room.reconnect_bitrate_unit = bitrate_unit
            room.reconnect_resolution = resolution or ""
            room.reconnect_framerate = framerate or ""
            room.reconnect_audio_bitrate = audio_bitrate or ""
            room.reconnect_attempts = 0
            room.reconnect_next_attempt_at = 0.0
            return True
        if shared_error:
            room.last_error = shared_error
            _mark_failed_candidate(room, shared_error)
            if self._pipeline_component_enabled("ingest_supervisor_v2", room):
                # Once a room enters the V2 ownership model the supervisor is
                # the only component allowed to own its upstream and sinks.
                # Falling through to StreamCapture here reuses the candidate
                # that just failed (typically a 403/expired signature),
                # creates a second lifecycle owner and can report a false
                # recording success before that process exits.
                _log.warning(
                    "V2 shared ingest recording failed; legacy fallback is disabled: "
                    "room=%s, error=%s",
                    room_id,
                    shared_error,
                )
                room.is_recording = False
                room.record_output_path = ""
                room.record_started_at = None
                room.recording_start_mono = None
                room.recording_media_start_mono = None
                room.recording_id = ""
                self._dirty_recording = True
                return False
            # 共享进样只是性能优化，不应成为录制可用性的单点故障。尤其在 VMware
            # 等环境中，pipe sink 可能启动后立即退出；此时改用常规 StreamCapture。
            _log.warning(
                "shared ingest unavailable, falling back to regular recording: room=%s, error=%s",
                room_id,
                shared_error,
            )

        ok, output_path, _encoder_used, error_msg = controller.start_recording_with_crf(
            stream_url,
            room_output_dir,
            effective_encoder,
            crf,
            param_mode=param_mode,
            bitrate=bitrate,
            bitrate_unit=bitrate_unit,
            input_args=input_args or None,
            resolution=resolution,
            framerate=framerate,
            audio_bitrate=audio_bitrate,
        )
        _log.info("[录制诊断] start_recording_with_crf returned ok=%s, error_msg=%s", ok, error_msg)
        room.is_recording = ok
        room.record_output_path = output_path
        room.record_started_at = (keep_started_at or datetime.now()) if ok else None
        if ok:
            room.recording_start_mono = getattr(controller, 'recording_start_mono', 0.0) or _time.monotonic()
            room.recording_media_start_mono = None
            # 重置首帧校正标记, 以便中频 tick 重新校正 (重连场景)
            room._first_frame_corrected = False
            room._shared_ingest_last_file_size = 0
            room._shared_ingest_stall_checks = 0
            _commit_recording_epoch(
                room.recording_media_start_mono or room.recording_start_mono
            )
        else:
            room.recording_start_mono = None
            room.recording_media_start_mono = None
            room.recording_id = ''
        # Mark state changed for UI refresh
        self._dirty_recording = True
        if ok:
            # Save recording params for auto-reconnect
            room.reconnect_output_dir = room_output_dir
            room.reconnect_encoder = effective_encoder
            room.reconnect_crf = crf
            room.reconnect_param_mode = param_mode
            room.reconnect_bitrate = bitrate or ""
            room.reconnect_bitrate_unit = bitrate_unit
            room.reconnect_resolution = resolution or ""
            room.reconnect_framerate = framerate or ""
            room.reconnect_audio_bitrate = audio_bitrate or ""
            room.reconnect_attempts = 0
            room.reconnect_next_attempt_at = 0.0
        if not ok:
            room.last_error = error_msg or "录制启动失败"
            # Platform policy decides family invalidation vs CDN quarantine.
            _mark_failed_candidate(room, room.last_error)
        return ok

    @staticmethod
    def _build_recording_profile(
        encoder: str,
        crf: int,
        param_mode: str = "CRF 质量",
        bitrate: str | None = None,
        bitrate_unit: str = "kbps",
        resolution: str | None = None,
        framerate: str | None = None,
        audio_bitrate: str | None = None,
    ) -> ExportProfile:
        rate_mode = "crf" if param_mode == "CRF 质量" else "bitrate"
        raw_bitrate = (bitrate or "8000").strip()
        unit = (bitrate_unit or "kbps").strip()
        if not raw_bitrate.endswith(("k", "K", "M", "m")):
            if unit == "Mbps":
                video_bitrate = f"{raw_bitrate}M"
            else:
                video_bitrate = f"{raw_bitrate}k"
        else:
            video_bitrate = raw_bitrate
        res = resolution or ""
        if res in ("原画", "原始", "", "auto"):
            res = ""
        fps = 0.0
        if framerate and framerate not in ("原画", "原始", "", "auto"):
            try:
                fps = float(framerate)
            except (TypeError, ValueError):
                fps = 0.0
        # ExportProfile 会直接把 codec 传给 FFmpeg；界面设置则使用展示名称。
        # 两者不能混用，否则 NVENC 自动回退为 "H.264 CPU" 时 FFmpeg 会报
        # "Unknown encoder"，录制文件停在首个很短的片段，持续分析也就没有
        # 新内容可处理。
        ffmpeg_codec = {
            "H.264 CPU": "libx264",
            "H.265 CPU": "libx265",
            "H.264 NVENC": "h264_nvenc",
            "H.265 NVENC": "hevc_nvenc",
            "Copy": "copy",
        }.get(encoder, encoder)
        return ExportProfile(
            codec=ffmpeg_codec,
            crf=crf,
            preset="medium",
            audio_bitrate=(audio_bitrate or "128k").strip() or "128k",
            rate_mode=rate_mode,
            video_bitrate=video_bitrate,
            resolution=res,
            fps=fps,
        )

    def _start_shared_recording_if_enabled(
        self,
        room: RoomSession,
        room_output_dir: str,
        stream_url: str,
        profile: ExportProfile | None = None,
    ) -> tuple[str, float, str]:
        """Returns (output_path, media_start_mono, error). Empty output_path + empty error means shared not enabled."""
        try:
            cfg = load_config()
        except Exception as exc:
            _log.debug("shared ingest config unavailable: %s", exc)
            return "", 0.0, ""
        use_v2_ingest = self._pipeline_component_enabled(
            "ingest_supervisor_v2", room, cfg
        )
        use_segmented_recording = self._pipeline_component_enabled(
            "segmented_recording_v2", room, cfg
        )
        if not getattr(cfg, "shared_ingest_enabled", False) and not use_v2_ingest:
            return "", 0.0, ""

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        unique_suffix = uuid4().hex[:6]
        output_path = os.path.join(room_output_dir, f"recording_{timestamp}_{unique_suffix}.mp4")
        headers = {}
        if room.stream_info is not None:
            headers = dict(getattr(room.stream_info, "headers", {}) or {})

        registry = get_shared_ingest_registry()
        try:
            if use_v2_ingest:
                supervisor = registry.get_supervisor(
                    room.room_id,
                    url=stream_url,
                    headers=headers,
                    network_context=dict(getattr(room, "network_context", {}) or {}),
                    event_callback=self._on_ingest_event,
                )
                lease = self._stream_leases.get(room.room_id)
                set_lease_context = getattr(supervisor, "set_lease_context", None)
                if callable(set_lease_context) and lease is not None:
                    set_lease_context(
                        session_id=getattr(room, "recording_id", "") or room.room_id,
                        platform_id=str(
                            getattr(room, "platform", "")
                            or getattr(room, "platform_name", "")
                            or ""
                        ),
                        lease_id=getattr(lease, "lease_id", ""),
                        candidate_id=getattr(
                            getattr(lease, "candidate", None), "candidate_id", ""
                        ),
                        generation=getattr(lease, "generation", 0),
                        quality_id=str(
                            getattr(getattr(lease, "candidate", None), "quality_id", "")
                            or ""
                        ),
                        protocol=str(
                            getattr(getattr(lease, "candidate", None), "protocol", "")
                            or ""
                        ),
                        cdn_id=str(
                            getattr(getattr(lease, "candidate", None), "cdn_id", "")
                            or ""
                        ),
                        expires_at=getattr(lease, "expires_at", None),
                        refresh_at=getattr(lease, "refresh_at", None),
                    )
                    ingest = getattr(supervisor, "ingest", None)
                    bind_lease = getattr(ingest, "bind_lease", None)
                    lease_manager = self._lease_managers.get(room.room_id)
                    if callable(bind_lease) and lease_manager is not None:
                        bind_lease(lease_manager, getattr(lease, "lease_id", ""))
                # A refreshed lease must replace the remote upstream before a
                # new recording sink is attached.  Keep an existing preview
                # subscriber alive while the shared ingest swaps processes;
                # the supervisor/generation guard prevents late bytes from
                # the expired URL reaching either sink.
                existing_ingest = getattr(supervisor, "ingest", None)
                existing_context = dict(
                    getattr(existing_ingest, "network_context", {}) or {}
                )
                existing_headers = dict(getattr(existing_ingest, "headers", {}) or {})
                if (
                    existing_ingest is not None
                    and (
                        str(getattr(existing_ingest, "url", "") or "") != str(stream_url)
                        or existing_headers != headers
                        or existing_context != dict(getattr(room, "network_context", {}) or {})
                    )
                ):
                    switch_upstream = getattr(supervisor, "switch_upstream", None)
                    if callable(switch_upstream):
                        switched = switch_upstream(
                            stream_url,
                            headers=headers,
                            network_context=dict(getattr(room, "network_context", {}) or {}),
                            generation=getattr(lease, "generation", None) if lease is not None else None,
                        )
                        if not switched:
                            error = str(supervisor.health().get("last_error", "upstream switch failed"))
                            return "", 0.0, error
                if use_segmented_recording:
                    result_ok = supervisor.start_recording(
                        output_path,
                        profile=profile,
                        segmented=True,
                        platform_id=getattr(room, "platform", "") or room.platform_name,
                        canonical_room_id=getattr(room, "canonical_room_id", "") or "",
                    )
                else:
                    result_ok = supervisor.start_recording(output_path, profile=profile)
                result_error = supervisor.health().get("last_error", "")
            else:
                ingest = registry.get_or_create(
                    room.room_id,
                    url=stream_url,
                    headers=headers,
                    network_context=dict(getattr(room, "network_context", {}) or {}),
                )
                if use_segmented_recording:
                    result = ingest.start_recording(
                        output_path,
                        profile=profile,
                        segmented=True,
                        platform_id=getattr(room, "platform", "") or room.platform_name,
                        canonical_room_id=getattr(room, "canonical_room_id", "") or "",
                    )
                else:
                    result = ingest.start_recording(output_path, profile=profile)
                result_ok = result.ok
                result_error = result.error
        except Exception as exc:
            registry.stop_room(room.room_id, reason="shared recording start exception")
            _log.warning("shared ingest recording failed room=%s: %s", room.room_id, exc)
            return "", 0.0, str(exc)
        if result_ok:
            ingest = registry.get(room.room_id)
            if use_segmented_recording and ingest is not None:
                room.record_manifest_path = str(
                    getattr(ingest, "recording_manifest_path", "") or ""
                )
                controller = getattr(room, "controller", None)
                if controller is not None:
                    controller.record_manifest_path = room.record_manifest_path
            # _wait_for_startup_data 已确保录制进程稳定存活 1 秒以上；
            # 此处仅检查 recording_active 作为最终确认，不再额外检�� PID，
            # 避免在 mock/测试环境中因无真实进程而误判。
            if not bool(getattr(ingest, "recording_active", False)):
                error = str(getattr(ingest, "recording_error", "") or "shared recording process is not running")
                registry.stop_room(room.room_id, reason="shared recording process unavailable")
                _log.warning("shared ingest recording unusable room=%s: %s", room.room_id, error)
                return "", 0.0, error
            return output_path, getattr(ingest, "recording_media_start_mono", 0.0), ""
        registry.stop_room(room.room_id, reason="shared recording start failed")
        _log.warning("shared ingest recording failed room=%s: %s", room.room_id, result_error)
        return "", 0.0, str(result_error)

    def stop_recording(self, room_id: str) -> bool:
        """Stop FFmpeg recording for a room and reset reconnect state.

        Delegates to ``RecordingController.stop_recording``, clears the
        room's recording flags, and triggers an async duration probe so the
        UI can show the final clip length once FFprobe reports back.

        Returns:
            True if the controller accepted the stop request.
        """
        if self._thread is not None and threading.current_thread() is not self._thread:
            return self.call(lambda: type(self).stop_recording(self, room_id=room_id))
        room = self.get_room(room_id)
        if room is None:
            return False
        controller = room.controller
        registry = get_shared_ingest_registry()
        shared_ingest = registry.get(room_id)
        if shared_ingest is not None and getattr(shared_ingest, "recording_active", False):
            output_path = room.record_output_path or getattr(shared_ingest, "_recording_path", "")
            if self._pipeline_component_enabled("ingest_supervisor_v2", room):
                registry.get_supervisor(
                    room_id,
                    url=getattr(shared_ingest, "url", ""),
                    headers=getattr(shared_ingest, "headers", {}),
                    network_context=dict(getattr(room, "network_context", {}) or {}),
                ).stop_recording(reason="manager stop recording")
            else:
                shared_ingest.stop_recording_sink(reason="manager stop recording")
            if shared_ingest.is_stopped or shared_ingest.preview_subscribers <= 0:
                registry.stop_room(room_id, reason="manager stop recording")
            room.is_recording = False
            room.is_reconnecting = False
            room.record_started_at = None
            room.reconnect_attempts = 0
            room.reconnect_next_attempt_at = 0.0
            self._dirty_recording = True
            if output_path:
                room.record_output_path = output_path
            return True
        if controller is None:
            return False
        ok, _size_mb, output_path = controller.stop_recording()
        room.is_recording = False
        room.is_reconnecting = False
        room.record_started_at = None
        room.reconnect_attempts = 0
        room.reconnect_next_attempt_at = 0.0
        # Mark state changed for UI refresh
        self._dirty_recording = True
        if output_path:
            room.record_output_path = output_path
        if output_path:
            probe_fn = getattr(controller, "probe_video_duration", None)
            if callable(probe_fn):
                def _on_probed(duration: float) -> None:
                    if duration > 0:
                        controller.total_sec = int(duration)
                probe_fn(on_probed=_on_probed)
        return ok

    def stop_recording_async(self, room_id: str) -> bool:
        """Non-blocking stop: marks state immediately, FFmpeg cleaned up in background.

        Avoids blocking the Qt main thread for up to 13 seconds (5+3+5 three-level stop).
        The caller can continue immediately; FFmpeg is cleaned up in a background thread.
        """
        if self._thread is not None and threading.current_thread() is not self._thread:
            return self.call(lambda: type(self).stop_recording_async(self, room_id=room_id))
        room = self.get_room(room_id)
        if room is None:
            return False
        controller = room.controller
        registry = get_shared_ingest_registry()
        shared_ingest = registry.get(room_id)
        if shared_ingest is not None and getattr(shared_ingest, "recording_active", False):
            output_path = room.record_output_path or getattr(shared_ingest, "_recording_path", "")
            if self._pipeline_component_enabled("ingest_supervisor_v2", room):
                registry.get_supervisor(
                    room_id,
                    url=getattr(shared_ingest, "url", ""),
                    headers=getattr(shared_ingest, "headers", {}),
                    network_context=dict(getattr(room, "network_context", {}) or {}),
                ).stop_recording(reason="manager async stop recording")
            else:
                shared_ingest.stop_recording_sink(reason="manager async stop recording")
            if shared_ingest.is_stopped or shared_ingest.preview_subscribers <= 0:
                registry.stop_room(room_id, reason="manager async stop recording")
            room.is_recording = False
            room.is_reconnecting = False
            room.record_started_at = None
            room.reconnect_attempts = 0
            room.reconnect_next_attempt_at = 0.0
            self._dirty_recording = True
            if output_path:
                room.record_output_path = output_path
            return True
        if controller is None:
            return False
        output_path = room.record_output_path or ""
        controller.stop_recording_async()
        room.is_recording = False
        room.is_reconnecting = False
        room.record_started_at = None
        room.reconnect_attempts = 0
        room.reconnect_next_attempt_at = 0.0
        self._dirty_recording = True
        if output_path:
            room.record_output_path = output_path
            probe_fn = getattr(controller, "probe_video_duration", None)
            if callable(probe_fn):
                def _on_probed(duration: float) -> None:
                    if duration > 0:
                        controller.total_sec = int(duration)
                probe_fn(on_probed=_on_probed)
        return True

    def start_recording_all(self, output_dir: str, encoder: str, crf: int,
                            param_mode: str = "CRF 质量", bitrate: str | None = None,
                            bitrate_unit: str = "kbps") -> dict[str, bool]:
        """Start recording on every managed room (synchronous, blocking)."""
        if self._thread is not None and threading.current_thread() is not self._thread:
            return self.call(lambda: type(self).start_recording_all(self, output_dir=output_dir, encoder=encoder, crf=crf, param_mode=param_mode, bitrate=bitrate, bitrate_unit=bitrate_unit))
        rooms = self.list_rooms()
        # Pre-flight: ensure disk space scales with concurrent stream count.
        if rooms:
            preflight = RecordingService.preflight_check(
                output_dir, concurrent_streams=len(rooms)
            )
            if preflight:
                _log.warning("批量录制预检失败: %s", preflight)
                return {r.room_id: False for r in rooms}
        if not rooms:
            return {}
        with ThreadPoolExecutor(max_workers=min(12, len(rooms))) as pool:
            futures = {
                pool.submit(
                    self.start_recording,
                    room.room_id,
                    output_dir,
                    encoder,
                    crf,
                    param_mode=param_mode,
                    bitrate=bitrate,
                    bitrate_unit=bitrate_unit,
                    _run_in_background=True,
                ): room.room_id
                for room in rooms
            }
            results: dict[str, bool] = {}
            for future in as_completed(futures):
                room_id = futures[future]
                try:
                    results[room_id] = bool(future.result())
                except Exception as exc:
                    _log.error("Batch recording room failed: room_id=%s, error=%s", room_id, exc)
                    results[room_id] = False
            return results

    def start_recording_all_async(self, output_dir: str, encoder: str, crf: int,
                                  param_mode: str = "CRF 质量", bitrate: str | None = None,
                                  bitrate_unit: str = "kbps") -> bool:
        """Start recording on all connected rooms without blocking the caller thread.

        Pre-flight disk space check runs synchronously on the caller thread;
        if it fails, returns False and emits no signals. Otherwise a
        background worker iterates over rooms, emitting
        ``batch_record_progress`` per room and ``batch_record_finished``
        when done.

        Returns True if the worker was started, False on pre-flight failure
        or if a batch is already running.
        """
        if self._thread is not None and threading.current_thread() is not self._thread:
            return self.call(lambda: type(self).start_recording_all_async(self, output_dir=output_dir, encoder=encoder, crf=crf, param_mode=param_mode, bitrate=bitrate, bitrate_unit=bitrate_unit))
        if self._batch_record_job is not None:
            _log.warning("批量录制已在进行中，忽略重复请求")
            return False

        rooms = self.list_rooms()
        if not rooms:
            return False

        # Pre-flight: ensure disk space scales with concurrent stream count.
        preflight = RecordingService.preflight_check(
            output_dir, concurrent_streams=len(rooms)
        )
        if preflight:
            _log.warning("批量录制预检失败: %s", preflight)
            return False

        room_ids = [r.room_id for r in rooms]
        job = _BatchRecordJob(
            self,
            room_ids,
            output_dir,
            encoder,
            crf,
            param_mode=param_mode,
            bitrate=bitrate,
            bitrate_unit=bitrate_unit,
        )
        self._batch_record_job = job
        self._submit_worker(job.run)
        return True

    def stop_recording_all(self) -> dict[str, bool]:
        """Stop recording on every managed room and return per-room results."""
        if self._thread is not None and threading.current_thread() is not self._thread:
            return self.call(lambda: type(self).stop_recording_all(self))
        return {r.room_id: self.stop_recording(r.room_id) for r in self.list_rooms()}

    def shutdown(self, timeout_sec: float = 10.0) -> dict[str, int]:
        """Release all runtime resources owned by the orchestrator.

        This is an application-exit cleanup path. It intentionally does not
        call save_rooms(), because shutdown should not overwrite the user's
        persisted room list with an empty runtime state.
        """
        if self._thread is not None and threading.current_thread() is not self._thread:
            stats = self.call(type(self)._shutdown_resources, self, timeout_sec)
        else:
            stats = self._shutdown_resources(timeout_sec)

        self._stop.set()
        # Shutdown must still be able to wake the loop if a producer filled
        # the bounded mailbox.  Drop one queued callback when necessary; the
        # normal resource cleanup has already run synchronously above.
        try:
            self._cmd_queue.put_nowait(None)
        except queue.Full:
            try:
                self._cmd_queue.get_nowait()
            except queue.Empty:
                pass
            self._cmd_queue.put_nowait(None)
        if self._thread:
            self._thread.join(timeout=timeout_sec)
        self._worker_pool.shutdown(wait=False, cancel_futures=True)
        return stats

    def _shutdown_resources(self, timeout_sec: float = 10.0) -> dict[str, int]:
        """Manager-parity resource cleanup (runs on orchestrator thread)."""
        shutdown_deadline = _time.monotonic() + max(0.1, float(timeout_sec))
        stats = {
            "rooms": len(self._rooms),
            "recordings_stopped": 0,
            "previews_stopped": 0,
            "shared_ingests_stopped": 0,
            "workers_cancelled": 0,
            "controllers_cleaned": 0,
            "previews_cleaned": 0,
        }

        self.stop_range_loop()
        self._disarm_tick_if_empty()
        self._next_tick_deadline = None

        def _stop_job(job: object | None) -> bool:
            if job is None:
                return False
            try:
                cancel = getattr(job, "cancel", None)
                if cancel is not None and hasattr(cancel, "set"):
                    cancel.set()
                return True
            except Exception as exc:
                _log.warning("Worker shutdown failed: %s", exc)
                return False

        for job in list(self._connect_jobs.values()):
            if _stop_job(job):
                stats["workers_cancelled"] += 1
        self._connect_jobs.clear()

        for cancel in list(self._refresh_cancels.values()):
            try:
                cancel.set()
                stats["workers_cancelled"] += 1
            except Exception as exc:
                _log.debug("refresh cancel failed: %s", exc)
        self._refresh_cancels.clear()

        for job in list(self._metadata_probe_jobs.values()):
            if _stop_job(job):
                stats["workers_cancelled"] += 1
        self._metadata_probe_jobs.clear()

        if _stop_job(self._batch_record_job):
            stats["workers_cancelled"] += 1
        self._batch_record_job = None

        for room in list(self._rooms.values()):
            try:
                self._cancel_reconnect_thread(room.room_id)
            except Exception as exc:
                _log.debug("Reconnect thread cleanup failed for room %s: %s", room.room_id, exc)

            if room.preview_enabled:
                try:
                    if self.stop_preview(room.room_id):
                        stats["previews_stopped"] += 1
                except Exception as exc:
                    _log.warning("Preview stop failed for room %s: %s", room.room_id, exc)

            controller = room.controller
            if room.is_recording and controller is not None:
                try:
                    stop_async = getattr(controller, "stop_recording_async", None)
                    if callable(stop_async):
                        stop_async()
                    else:
                        stop = getattr(controller, "stop_recording", None)
                        if callable(stop):
                            stop()
                    stats["recordings_stopped"] += 1
                except Exception as exc:
                    _log.warning("Recording stop failed for room %s: %s", room.room_id, exc)

            # V2/shared-ingest recording can run without a legacy controller.
            # Always release the room-owned upstream here so application
            # restart cannot leave an ffmpeg process or preview queue alive.
            try:
                registry = get_shared_ingest_registry()
                if registry.get(room.room_id) is not None:
                    try:
                        registry.stop_room(
                            room.room_id,
                            reason="orchestrator shutdown",
                            deadline_monotonic=shutdown_deadline,
                        )
                    except TypeError as exc:
                        if "deadline_monotonic" not in str(exc):
                            raise
                        registry.stop_room(
                            room.room_id,
                            reason="orchestrator shutdown",
                        )
                    stats["shared_ingests_stopped"] += 1
            except Exception as exc:
                _log.warning("Shared ingest cleanup failed for room %s: %s", room.room_id, exc)

            if controller is not None:
                cleanup_fn = getattr(controller, "cleanup", None)
                if callable(cleanup_fn):
                    try:
                        cleanup_fn()
                        stats["controllers_cleaned"] += 1
                    except Exception as exc:
                        _log.warning("Controller cleanup failed for room %s: %s", room.room_id, exc)

            preview = room.preview_widget
            if preview is not None:
                cleanup_fn = getattr(preview, "cleanup", None)
                if callable(cleanup_fn):
                    try:
                        cleanup_fn()
                        stats["previews_cleaned"] += 1
                    except Exception as exc:
                        _log.warning("Preview cleanup failed for room %s: %s", room.room_id, exc)

            room.is_connected = False
            room.is_connecting = False
            room.is_recording = False
            room.is_reconnecting = False
            room.preview_enabled = False
            room.preview_paused = False

        self._rooms.clear()
        self._pending_stream_infos.clear()
        self._stream_leases.clear()
        self._lease_managers.clear()
        self._lease_refresh_inflight.clear()
        self._dirty_recording = False
        self._dirty_connection = False
        _log.info("RoomOrchestrator shutdown complete: %s", stats)
        return stats


    # ── Export ───────────────────────────────────────────────

    def start_export(self, room_id: str, start_sec: float, end_sec: float,
                     output_dir: str, title: str = "",
                     on_done: Callable[[bool, str, str, float, str], None] | None = None,
                     on_progress: Callable[[float, float, float], None] | None = None,
                     profile: ExportProfile | None = None) -> str:
        """Start async clip export for a room's recording.

        Parameters
        ----------
        on_done : callable | None
            完成回调 ``callback(success, path, error, size_mb, thumbnail_path)``。
        on_progress : callable | None
            进度回调 ``callback(percent: float, elapsed: float, total: float)``。
        profile : ExportProfile | None
            编码配置。若为 None 则使用默认配置。

        Returns
        -------
        str
            export_id（非空字符串）表示已启动；空字符串表示启动失败。
            可传给 :meth:`cancel_export` 取消该任务。
        """
        if self._thread is not None and threading.current_thread() is not self._thread:
            return self.call(lambda: type(self).start_export(self, room_id=room_id, start_sec=start_sec, end_sec=end_sec, output_dir=output_dir, title=title, on_done=on_done, on_progress=on_progress, profile=profile))
        room = self.get_room(room_id)
        if room is None:
            return ""
        controller = room.controller
        if controller is None:
            return ""
        export_id = controller.start_export(start_sec, end_sec, output_dir, title, on_done,
                                            profile=profile, on_progress=on_progress)
        if not export_id and not controller._last_export_error:
            # 启动失败时在 controller 上标记错误原因
            controller._last_export_error = "导出启动失败（控制器异常）"
        return export_id

    def cancel_export(self, export_id: str) -> bool:
        """取消指定 export_id 的导出任务。

        Returns
        -------
        bool
            True 表示已发送 kill 信号；False 表示任务不存在或已结束。
        """
        if self._thread is not None and threading.current_thread() is not self._thread:
            return self.call(lambda: type(self).cancel_export(self, export_id=export_id))
        # export_id 可能注册在任意房间的 controller 上，需要遍历查找
        for room in self._rooms.values():
            controller = room.controller
            if controller is None:
                continue
            # RecordingController 维护全局 _export_workers，但实例独立
            # 这里遍历所有 controller 尝试取消
            if hasattr(controller, 'cancel_export'):
                try:
                    if controller.cancel_export(export_id):
                        return True
                except Exception:
                    _log.exception("cancel_export failed for export_id=%s", export_id)
        return False

    # ── Cut ──────────────────────────────────────────────────

    def get_rooms_for_cut(self) -> list[RoomSession]:
        """Return rooms that have opted in to the cut/export pipeline."""
        if self._thread is not None and threading.current_thread() is not self._thread:
            return self.call(lambda: type(self).get_rooms_for_cut(self))
        return [r for r in self.list_rooms() if r.include_in_cut]

    def get_total_recording_size_mb(self) -> float:
        """返回所有房间当前录制文件的总大小（MB）。"""
        if self._thread is not None and threading.current_thread() is not self._thread:
            return self.call(lambda: type(self).get_total_recording_size_mb(self))
        return sum(r.record_size_mb for r in self._rooms.values() if r.is_recording)

    # ── Global heartbeat ─────────────────────────────────────

    # ── Recording reconnect ─────────────────────────────────

    def _check_shared_ingest_file_stall(self, room: RoomSession) -> str:
        """检测共享进样录制文件是否停滞。返回错误信息或空字符串。"""
        output_path = room.record_output_path
        if not output_path:
            return ""
        file_exists = os.path.isfile(output_path)
        cur_size = os.path.getsize(output_path) if file_exists else 0
        if cur_size == 0 or cur_size == room._shared_ingest_last_file_size:
            room._shared_ingest_stall_checks += 1
            stall_checks = room._shared_ingest_stall_checks
        else:
            room._shared_ingest_stall_checks = 0
            stall_checks = 0
        room._shared_ingest_last_file_size = cur_size
        if stall_checks >= _SHARED_INGEST_STALL_CHECKS:
            if cur_size == 0:
                return "录制文件未写入数据，直播流可能已中断"
            return "输出文件长时间未增长，录制可能已卡住"
        return ""

    def _shared_ingest_recovery_allowed(
        self,
        room: RoomSession,
        ingest: Any,
        error: str,
    ) -> bool:
        """Use typed supervisor failure state before legacy text heuristics."""
        from lsc.platforms.failure import is_recoverable_failure
        from lsc.utils.error_messages import is_recoverable_error

        failure_kind = ""
        supervisor = get_shared_ingest_registry().get_supervisor_if_exists(room.room_id)
        if supervisor is not None:
            try:
                raw_failure_kind = supervisor.health().get("failure_kind", "") or ""
                failure_kind = (
                    normalize_failure_kind(raw_failure_kind).value
                    if raw_failure_kind
                    else ""
                )
            except Exception as exc:
                _log.debug("shared ingest typed failure lookup failed: %s", redact_text(exc))
        if failure_kind:
            typed = normalize_failure_kind(failure_kind)
            if typed is not FailureKind.UNKNOWN:
                return is_recoverable_failure(typed)
        from lsc.platforms.recovery_policy import recovery_action

        saw_first_ts = bool(getattr(ingest, "_upstream_has_produced_data", False))
        if recovery_action(getattr(room, "stream_info", None), error, saw_first_ts=saw_first_ts) == "rotate_lease":
            return True
        return bool(error and is_recoverable_error(error))

    def _recover_shared_upstream_in_place(self, room: RoomSession, *,
                                          _run_in_background: bool = False) -> bool:
        """Replace a dead remote upstream while keeping the recording sink and clock."""
        registry = get_shared_ingest_registry()
        ingest = registry.get(room.room_id)
        if ingest is None:
            return False
        recording_live = getattr(ingest, "recording_sink_is_live", None)
        if callable(recording_live):
            recording_ok = bool(recording_live())
        else:
            recording_ok = bool(getattr(ingest, "recording_active", False))
        preview_live = getattr(ingest, "preview_sink_is_live", None)
        if callable(preview_live):
            preview_ok = bool(preview_live())
        else:
            preview_ok = int(getattr(ingest, "preview_subscribers", 0) or 0) > 0
        if not recording_ok and not preview_ok:
            return False
        upstream_live = getattr(ingest, "upstream_is_live", None)
        if callable(upstream_live) and upstream_live():
            return True
        pending = None
        take_pending = getattr(ingest, "take_pending_lease", None)
        if callable(take_pending):
            try:
                pending = take_pending()
            except Exception as exc:
                _log.debug(
                    "Room %s pending lease lookup failed: %s",
                    room.room_id,
                    redact_text(exc),
                )
                pending = None
        stream_url = str((pending or {}).get("url") or "")
        headers = dict((pending or {}).get("headers") or {})
        network_context = dict(
            (pending or {}).get("network_context")
            or getattr(room, "network_context", {})
            or {}
        )
        generation = (pending or {}).get("generation")
        lease_id = str((pending or {}).get("lease_id") or "")
        if not stream_url:
            room.stream_parsed_at = 0.0
            try:
                if not self.refresh_stream_url(room.room_id, force=True,
                                               _run_in_background=_run_in_background):
                    return False
            except Exception as exc:
                _log.warning(
                    "Room %s in-place upstream refresh failed: %s",
                    room.room_id,
                    redact_text(exc),
                )
                return False
            stream_url = _get_room_stream_url(room)
            if not stream_url:
                return False
            stream_info = getattr(room, "stream_info", None)
            headers = dict(getattr(stream_info, "headers", {}) or {})
            network_context = dict(getattr(room, "network_context", {}) or {})
            lease = self._stream_leases.get(room.room_id)
            generation = getattr(lease, "generation", None) if lease is not None else None
            lease_id = str(getattr(lease, "lease_id", "") or "")
        lease = self._stream_leases.get(room.room_id)
        lease_manager = self._lease_managers.get(room.room_id)
        bind_lease = getattr(ingest, "bind_lease", None)
        if callable(bind_lease) and lease_manager is not None and lease_id:
            bind_lease(lease_manager, lease_id)
        elif callable(bind_lease) and lease is not None and lease_manager is not None:
            bind_lease(lease_manager, getattr(lease, "lease_id", ""))
        supervisor = (
            registry.get_supervisor_if_exists(room.room_id)
            if callable(getattr(registry, "get_supervisor_if_exists", None))
            else None
        )
        if supervisor is not None:
            switch_upstream = getattr(supervisor, "switch_upstream", None)
            if callable(switch_upstream):
                return bool(
                    switch_upstream(
                        stream_url,
                        headers=headers,
                        network_context=network_context,
                        generation=generation,
                        reason_code="UPSTREAM_EOF_RECOVERY",
                    )
                )
        replace = getattr(ingest, "replace_upstream", None)
        if not callable(replace):
            return False
        error = replace(
            stream_url,
            headers=headers,
            network_context=network_context,
        )
        return not str(error or "")

    def _attempt_recording_reconnect(self, room: RoomSession, error_msg: str,
                                     *, _background: bool = False) -> bool:
        """Attempt to reconnect a failed recording with exponential backoff.

        The reconnection strategy is:
        1. Validate that the error is recoverable (network hiccup, not a
           codec or permission failure).
        2. Enforce a hard cap of ``_MAX_RECONNECT_ATTEMPTS`` so we don't
           loop forever on a permanently dead stream.
        3. Compute delay = min(base * factor ** attempts, max_delay).
           The first retry happens after ~2s; subsequent retries double
           the wait up to 30s.
        4. On each attempt, stop the failed FFmpeg process, optionally
           flag the old file as corrupt (if < 1KB), re-parse the CDN URL,
           and call ``start_recording`` with the saved parameters.

        Args:
            _background: True 时落地段（原地恢复→stop→刷新→重启录制）整体提交到
                worker 池执行——URL 刷新与 FFmpeg 首帧探测可达 10-20s，不能再
                在编排线程同步跑（全局 tick 冻结、orchestrator.call 批量超时）。
                ``_reconnect_in_progress`` 由落地段结束时复位。

        Returns:
            True 表示已进入落地段（后台模式为已调度，落地完成时自复位
            ``_reconnect_in_progress``）；False 表示提前返回（未进入落地段，
            调用方须立即复位 ``_reconnect_in_progress``）。
        """
        from lsc.utils.error_messages import is_recoverable_error

        # 用户已断开/删除房间，取消重连
        if room._cancel_reconnect.is_set():
            return False

        # Check if error is recoverable
        if not is_recoverable_error(error_msg):
            # 虎牙 FFmpeg 异常退出（尤其 code 0）多为 CDN 签名/线路问题，值得换线重连；
            # 403/鉴权特征已在 watchdog 文案中保留，is_recoverable_error 仍优先匹配。
            from lsc.platforms.recovery_policy import should_force_recovery

            force_recovery = should_force_recovery(
                getattr(room, "stream_info", None),
                error_msg,
            )
            if force_recovery:
                _log.info(
                    "Room %s platform policy requested recovery: %s",
                    room.room_id, error_msg,
                )
            else:
                room.last_error = error_msg
                room.is_recording = False
                room.is_reconnecting = False
                room.record_started_at = None
                _log.warning("Room %s non-recoverable error: %s", room.room_id, error_msg)
                controller = room.controller
                if controller is not None:
                    try:
                        controller.stop_recording()
                    except Exception as exc:
                        _log.warning("Reconnect stop failed (non-recoverable) room=%s: %s", room.room_id, exc)
                return False

        if room.reconnect_attempts >= _MAX_RECONNECT_ATTEMPTS:
            room.last_error = error_msg
            room.is_recording = False
            room.is_reconnecting = False
            room.record_started_at = None
            _log.warning("Room %s reconnect exhausted (%d attempts), giving up",
                         room.room_id, room.reconnect_attempts)
            controller = room.controller
            if controller is not None:
                try:
                    controller.stop_recording()
                except Exception as exc:
                    _log.warning("Reconnect stop failed (exhausted) room=%s: %s", room.room_id, exc)
            if room.preview_enabled:
                self.stop_preview(room.room_id)
                _log.info("Room %s preview stopped after reconnect exhausted", room.room_id)
            return False

        # Calculate exponential backoff delay
        delay = min(
            _RECONNECT_DELAY_SEC * (_RECONNECT_BACKOFF_FACTOR ** room.reconnect_attempts),
            _RECONNECT_MAX_DELAY_SEC,
        )

        if room.reconnect_next_attempt_at <= 0:
            room.reconnect_next_attempt_at = _time.monotonic() + delay
            room.is_reconnecting = True
            room.last_error = f"{error_msg}，{delay:.0f}秒后尝试恢复..."
            _log.info("Room %s scheduling reconnect attempt %d/%d (delay=%.1fs)",
                      room.room_id, room.reconnect_attempts + 1, _MAX_RECONNECT_ATTEMPTS, delay)
            return False

        if _time.monotonic() < room.reconnect_next_attempt_at:
            return False

        # 用户已断开/删除房间，取消重连
        if room._cancel_reconnect.is_set():
            return False

        room.reconnect_attempts += 1
        room.reconnect_next_attempt_at = 0.0
        _log.info("Room %s attempting reconnect %d/%d",
                  room.room_id, room.reconnect_attempts, _MAX_RECONNECT_ATTEMPTS)
        room.last_error = f"正在尝试恢复录制 ({room.reconnect_attempts}/{_MAX_RECONNECT_ATTEMPTS})..."

        if _background:
            # 落地段（原地恢复 → stop → 刷新 → 重启录制）整体在 worker 池执行，
            # 不在编排线程同步跑。落地段结束时自行复位 _reconnect_in_progress。
            room_id = room.room_id
            err = error_msg
            self._submit_worker(
                lambda: self._run_reconnect_landing(room, err, background=True)
            )
            return True

        self._run_reconnect_landing(room, error_msg, background=False)
        return True

    def _run_reconnect_landing(self, room: RoomSession, error_msg: str, *,
                               background: bool) -> None:
        """重连落地段：原地恢复优先，否则停止旧 FFmpeg → 刷新流 → 重启录制。

        Args:
            background: True 时本方法运行在 worker 池线程。内部所有耗时调用
                （refresh_stream_url / start_recording）必须传
                ``_run_in_background=True`` 跳过 call 回编守卫，否则会排队回
                编排线程造成同样的全局冻结。结束时复位 ``_reconnect_in_progress``
                （同步模式由 _start_supervised_recovery 的 finally 管理）。
        """
        try:
            if self._recover_shared_upstream_in_place(room, _run_in_background=background):
                _log.info("Room %s recovered shared upstream without resetting recording", room.room_id)
                room.reconnect_attempts = 0
                room.reconnect_next_attempt_at = 0.0
                room.is_reconnecting = False
                room.last_error = ""
                return

            # 保存原始错误信息和旧文件路径
            original_error = error_msg
            old_output_path = room.record_output_path

            # Stop the failed recording gracefully
            # ponytail: shared ingest 走快速路径，避免 stop_recording_sink 先重启为 preview-only 再被 start_recording 杀死的双重重启
            registry = get_shared_ingest_registry()
            shared_ingest = registry.get(room.room_id)
            if shared_ingest is not None and getattr(shared_ingest, "recording_active", False):
                lookup_supervisor = getattr(registry, "get_supervisor_if_exists", None)
                supervisor = (
                    lookup_supervisor(room.room_id)
                    if callable(lookup_supervisor)
                    else None
                )
                if supervisor is not None:
                    # V2 recovery is sink-scoped: recording may restart while
                    # an attached preview keeps consuming the same upstream.
                    supervisor.stop_recording("reconnect fast path")
                    if shared_ingest.is_stopped or shared_ingest.preview_subscribers <= 0:
                        registry.stop_room(room.room_id, reason="reconnect fast path")
                else:
                    shared_ingest.stop(reason="reconnect fast path")
                    registry.stop_room(room.room_id, reason="reconnect fast path")
            else:
                controller = room.controller
                if controller is not None:
                    try:
                        controller.stop_recording()
                    except Exception as exc:
                        _log.warning("Reconnect attempt stop failed room=%s: %s", room.room_id, exc)
            room.is_recording = False

            # 标记旧文件可能损坏（如果存在且大小异常小）
            if old_output_path and os.path.isfile(old_output_path):
                try:
                    file_size = os.path.getsize(old_output_path)
                    if file_size < 1024:  # 小于 1KB 可能是损坏的
                        _log.warning("Room %s old recording file may be corrupted: %s (%d bytes)",
                                     room.room_id, old_output_path, file_size)
                except OSError:
                    pass

            # Re-parse the stream URL and restart recording
            if not room.reconnect_output_dir:
                room.last_error = f"恢复失败：缺少录制参数（原始错误: {original_error}）"
                return

            # CDN 地址失效后必须强制刷新，禁止复用 120s 内的死链缓存
            room.stream_parsed_at = 0.0
            try:
                if not self.refresh_stream_url(room.room_id, force=True, _run_in_background=True):
                    _log.warning(
                        "Room %s reconnect URL refresh failed, start_recording will retry parse",
                        room.room_id,
                    )
            except Exception as exc:
                _log.warning("Room %s reconnect URL refresh error: %s", room.room_id, exc)

            ok = self.start_recording(
                room.room_id,
                room.reconnect_output_dir,
                room.reconnect_encoder,
                room.reconnect_crf,
                param_mode=room.reconnect_param_mode,
                bitrate=room.reconnect_bitrate,
                bitrate_unit=room.reconnect_bitrate_unit,
                resolution=room.reconnect_resolution or None,
                framerate=room.reconnect_framerate or None,
                audio_bitrate=room.reconnect_audio_bitrate or None,
                _run_in_background=True,
            )
            if ok:
                _log.info("Room %s reconnect succeeded", room.room_id)
                room.reconnect_attempts = 0
                room.reconnect_next_attempt_at = 0.0
                room.is_reconnecting = False
            else:
                if _is_stream_offline_error(room.last_error):
                    room.is_recording = False
                    room.is_reconnecting = False
                    room.record_started_at = None
                    room.reconnect_next_attempt_at = 0.0
                    offline_msg = room.last_error or _offline_stream_error_message()
                    _log.info("Room %s reconnect stopped because stream is offline: %s",
                              room.room_id, offline_msg)
                    try:
                        # EventBus.emit 仅允许编排线程；后台落地段须回投队列执行
                        if background:
                            self.submit(
                                self.bus.emit, "recording_stopped",
                                room.room_id, 'offline', offline_msg,
                            )
                        else:
                            self.bus.emit("recording_stopped", room.room_id, 'offline', offline_msg)
                    except Exception as exc:
                        _log.debug("recording_stopped emit failed: %s", exc)
                    return
                _log.warning("Room %s reconnect attempt %d failed: %s",
                             room.room_id, room.reconnect_attempts, room.last_error)
                # 保留原始错误信息
                if not room.last_error or room.last_error == "录制启动失败":
                    room.last_error = f"恢复失败（原始错误: {original_error}）"
                # Calculate next delay with exponential backoff
                next_delay = min(
                    _RECONNECT_DELAY_SEC * (_RECONNECT_BACKOFF_FACTOR ** room.reconnect_attempts),
                    _RECONNECT_MAX_DELAY_SEC,
                )
                room.reconnect_next_attempt_at = _time.monotonic() + next_delay
                room.is_reconnecting = True
        finally:
            if background:
                room._reconnect_in_progress = False
                self._dirty_recording = True

    def _do_proactive_reconnect(self, room: RoomSession) -> None:
        """URL 过期前在 Qt 主线程重启录制（由 global tick 调用）。

        失败时回退到常规重连流程（_attempt_recording_reconnect），
        避免录制静默死亡。
        """
        reconnect_error = "流 URL 即将过期，主动刷新"
        try:
            registry = get_shared_ingest_registry()
            shared_ingest = registry.get(room.room_id)
            if shared_ingest is not None and getattr(shared_ingest, "recording_active", False):
                lookup_supervisor = getattr(registry, "get_supervisor_if_exists", None)
                supervisor = (
                    lookup_supervisor(room.room_id)
                    if callable(lookup_supervisor)
                    else None
                )
                if supervisor is not None:
                    supervisor.stop_recording("proactive reconnect")
                    if shared_ingest.is_stopped or shared_ingest.preview_subscribers <= 0:
                        registry.stop_room(room.room_id, reason="proactive reconnect")
                else:
                    shared_ingest.stop(reason="proactive reconnect")
                    registry.stop_room(room.room_id, reason="proactive reconnect")
            else:
                controller = room.controller
                if controller is not None:
                    try:
                        controller.stop_recording()
                    except Exception as exc:
                        _log.warning("Proactive reconnect stop failed room=%s: %s", room.room_id, exc)
            room.is_recording = False
            ok = self.start_recording(
                room.room_id,
                room.reconnect_output_dir,
                room.reconnect_encoder,
                room.reconnect_crf,
                param_mode=room.reconnect_param_mode,
                bitrate=room.reconnect_bitrate,
                bitrate_unit=room.reconnect_bitrate_unit,
                resolution=room.reconnect_resolution or None,
                framerate=room.reconnect_framerate or None,
                audio_bitrate=room.reconnect_audio_bitrate or None,
            )
            if ok:
                _log.info("Room %s proactive reconnect succeeded", room.room_id)
                return
            # 启动失败：记录错误并回退到常规重连流程
            reconnect_error = room.last_error or "主动刷新录制失败"
            _log.warning("Room %s proactive reconnect failed: %s, falling back to regular reconnect",
                         room.room_id, reconnect_error)
        except Exception as exc:
            reconnect_error = str(exc)
            _log.warning("Room %s proactive reconnect failed: %s, falling back to regular reconnect",
                         room.room_id, exc)
        finally:
            room.is_reconnecting = False
            self._dirty_recording = True

        # 回退到常规重连流程：通过 _attempt_recording_reconnect 进行指数退避重试
        # 这样可以在 URL 刷新后仍失败时（如下播、网络抖动）继续尝试恢复
        self._start_recording_reconnect_thread(room, reconnect_error)

    def _start_recording_reconnect_thread(self, room: RoomSession, error_msg: str) -> bool:
        """调度录制重连（由 global tick 调用）。

        重连落地段（原地恢复 → stop → 刷新 URL → 重启录制）在 worker 池执行，
        URL 刷新与 FFmpeg 首帧探测（可达 10-20s）不再阻塞编排线程——
        全局 tick、其他房间录制/预览、orchestrator.call 全部保持响应。
        """
        if getattr(room, "_reconnect_in_progress", False):
            return False
        if self._use_supervised_recovery(room):
            return self._start_supervised_recovery(room, error_msg)
        room._cancel_reconnect.clear()
        room._reconnect_in_progress = True
        try:
            entered = self._attempt_recording_reconnect(room, error_msg, _background=True)
        except Exception as exc:
            _log.error("Room %s reconnect failed: %s", room.room_id, exc)
            room._reconnect_in_progress = False
            self._dirty_recording = True
            return True
        if not entered:
            # 提前返回路径（不可恢复/重连耗尽/退避等待中）：落地段未调度，
            # 在此立即复位；后台落地段结束时由 _run_reconnect_landing 自行复位。
            room._reconnect_in_progress = False
            self._dirty_recording = True
        return True

    def _use_supervised_recovery(self, room: RoomSession) -> bool:
        """Return whether this room is running the V2 single-recovery path."""
        if not self._pipeline_component_enabled("ingest_supervisor_v2", room):
            return False
        return get_shared_ingest_registry().get_supervisor_if_exists(room.room_id) is not None

    def _start_supervised_recovery(self, room: RoomSession, error_msg: str) -> bool:
        """Run one reconnect through IngestSupervisor's unique coordinator."""
        supervisor = get_shared_ingest_registry().get_supervisor_if_exists(room.room_id)
        if supervisor is None or getattr(room, "_reconnect_in_progress", False):
            return False
        from lsc.platforms.failure import classify_failure
        from lsc.platforms.recovery_policy import recovery_action

        room._cancel_reconnect.clear()
        room._reconnect_in_progress = True
        original_next = room.reconnect_next_attempt_at
        room.reconnect_next_attempt_at = _time.monotonic()

        def recover(_recovery_id: str) -> bool:
            self._attempt_recording_reconnect(room, error_msg)
            return bool(room.is_recording and not room.is_reconnecting)

        try:
            registry = get_shared_ingest_registry()
            getter = getattr(registry, "get", None)
            ingest = getter(room.room_id) if callable(getter) else None
            saw_first_ts = bool(getattr(ingest, "_upstream_has_produced_data", False))
            action = recovery_action(
                getattr(room, "stream_info", None),
                error_msg,
                saw_first_ts=saw_first_ts,
            )
            if action == "restart_preview_sink":
                restart = getattr(supervisor, "restart_preview_sink", None)
                if callable(restart):
                    return bool(restart())
            if action == "rotate_lease":
                def rotate(_recovery_id: str) -> bool:
                    return self._recover_shared_upstream_in_place(room)

                return bool(
                    supervisor.run_recovery(
                        rotate,
                        reason_code=classify_failure(error_msg).value,
                    )
                )
            return bool(
                supervisor.run_recovery(
                    recover,
                    reason_code=classify_failure(error_msg).value,
                )
            )
        finally:
            room._reconnect_in_progress = False
            if not room.is_reconnecting and not room.is_recording:
                room.reconnect_next_attempt_at = original_next if original_next > 0 else 0.0
            self._dirty_recording = True

    def _start_global_timer(self) -> None:
        self._ensure_tick_armed()

    def _stop_global_timer(self) -> None:
        self._disarm_tick_if_empty()

    def _on_global_tick(self) -> None:
        """Layered heartbeat: high/medium/low frequency operations.

        Timer fires every 3s. Medium work is staggered across rooms
        (``_STAGGER_GROUPS``) so 12-room size/health I/O is spread out.
        Low-frequency disk guard runs every ``_LOW_FREQ_INTERVAL`` ticks.

        Layered breakdown:
        - **High-frequency (every tick)**: controller ``tick()`` for elapsed
          time, and sync of preview playback position into the controller.
        - **Medium-frequency (staggered each tick)**: file-size polling via
          ``SizeUpdateJob`` and FFmpeg watchdog health-check. Failed
          recordings trigger an auto-reconnect in a background thread to
          keep the UI responsive.
        - **Low-frequency (~12s)**: disk-space guard; if free space
          drops below ``_MIN_FREE_BYTES_WHILE_RECORDING`` the recording is
          stopped automatically to prevent a mid-write crash.

        Dirty flags are reset after signal emission so the UI only refreshes
        when something actually changed.
        """
        self._tick_counter += 1
        is_medium_tick = (self._tick_counter % _MEDIUM_FREQ_INTERVAL == 0)
        is_low_tick = (self._tick_counter % _LOW_FREQ_INTERVAL == 0)
        stagger_slot = self._tick_counter % _STAGGER_GROUPS

        for room_idx, room in enumerate(list(self._rooms.values())):
            controller = room.controller
            room_medium = is_medium_tick and (room_idx % _STAGGER_GROUPS == stagger_slot)

            # ── Shared ingest health check (controller is None in Electron) ──
            registry = get_shared_ingest_registry()
            ingest = registry.get(room.room_id)
            use_shared_health = ingest is not None and (
                room.is_recording or room.preview_enabled
            )
            if use_shared_health:
                self._maybe_prefetch_http_session_lease(room)
                if ingest is not None:
                    # 关键修复：上游退出后 recording_active 会立即变为 False。
                    # 如果错误可恢复（403/网络断开），先尝试重连再放弃；
                    # 仅在不可恢复或重连耗尽时才标记录制停止。
                    if (
                        room.is_recording
                        and not getattr(ingest, "recording_active", False)
                        and not room.is_reconnecting
                    ):
                        err = getattr(ingest, "recording_error", "") or getattr(ingest, "upstream_error", "")
                        # 标记失败的 CDN 线路，让下次 re-parse 跳过它
                        _mark_failed_candidate(room, err)
                        recoverable = self._shared_ingest_recovery_allowed(room, ingest, err)
                        if err and recoverable and room.reconnect_attempts < _MAX_RECONNECT_ATTEMPTS:
                            _log.warning(
                                "Room %s shared ingest 已停止（可恢复），触发重连: %s",
                                room.room_id, err[:120],
                            )
                            room.is_reconnecting = True
                            self._start_recording_reconnect_thread(room, err)
                        else:
                            _log.warning(
                                "Room %s shared ingest 已停止（不可恢复/重连耗尽），同步 is_recording=False: %s",
                                room.room_id, err,
                            )
                            room.is_recording = False
                            room.is_reconnecting = False
                            if err:
                                room.last_error = err
                            self._dirty_recording = True
                            self.bus.emit("recording_stopped",
                                room.room_id,
                                'shared_ingest_stopped',
                                err or "共享进样上游已停止",
                            )
                            continue
                    ingest_error = getattr(ingest, "recording_error", "") or getattr(ingest, "upstream_error", "")
                    rotate_lease = False
                    if ingest_error:
                        from lsc.platforms.recovery_policy import recovery_action as _recovery_action

                        rotate_lease = (
                            _recovery_action(
                                getattr(room, "stream_info", None),
                                ingest_error,
                                saw_first_ts=bool(
                                    getattr(ingest, "_upstream_has_produced_data", False)
                                ),
                            )
                            == "rotate_lease"
                        )
                    if rotate_lease and not room.is_reconnecting:
                        _log.info(
                            "Room %s rotating signed HTTP lease: %s",
                            room.room_id,
                            ingest_error[:120],
                        )
                        if self._use_supervised_recovery(room):
                            self._start_supervised_recovery(room, ingest_error)
                        else:
                            self._recover_shared_upstream_in_place(room)
                    elif ingest_error and not room.is_reconnecting and not self._shared_ingest_recovery_allowed(room, ingest, ingest_error):
                        if not room.is_recording:
                            continue
                        _log.warning(
                            "Room %s shared ingest terminal failure; skip reconnect: %s",
                            room.room_id,
                            ingest_error,
                        )
                        room.is_recording = False
                        room.is_reconnecting = False
                        room.last_error = ingest_error
                        self._dirty_recording = True
                        self.bus.emit(
                            "recording_stopped",
                            room.room_id,
                            "shared_ingest_terminal_failure",
                            ingest_error,
                        )
                    elif ingest_error and not room.is_reconnecting:
                        _log.warning("Room %s shared ingest error: %s", room.room_id, ingest_error)
                        if room.is_recording:
                            _mark_failed_candidate(room, ingest_error)
                            self._start_recording_reconnect_thread(room, ingest_error)
                        elif room.preview_enabled and self._use_supervised_recovery(room):
                            self._start_supervised_recovery(room, ingest_error)
                    elif room_medium and not room.is_reconnecting:
                        if getattr(ingest, "recording_active", False) and room.record_output_path:
                            stall_msg = self._check_shared_ingest_file_stall(room)
                            if stall_msg:
                                _log.warning("Room %s shared ingest stall: %s", room.room_id, stall_msg)
                                self._start_recording_reconnect_thread(room, stall_msg)
                    if (room.is_reconnecting
                            and room.reconnect_next_attempt_at > 0
                            and _time.monotonic() >= room.reconnect_next_attempt_at):
                        self._start_recording_reconnect_thread(
                            room,
                            room.last_error or "录制恢复到期",
                        )
                    # 主动流 URL 过期检测（与 controller 路径对齐）
                    if is_low_tick and room.is_recording and not room.is_reconnecting:
                        stream_url = ""
                        if room.stream_info and room.stream_info.stream_url:
                            stream_url = room.stream_info.stream_url
                        if stream_url and _is_stream_url_expiring(stream_url):
                            _log.info("Room %s stream URL expiring soon (shared ingest), proactive reconnect", room.room_id)
                            room.is_reconnecting = True
                            room._cancel_reconnect.clear()
                            self._do_proactive_reconnect(room)
                continue

            # ── High-frequency: lightweight operations ──
            if room.is_recording:
                tick_fn = getattr(controller, "tick", None)
                if callable(tick_fn):
                    try:
                        tick_fn()
                    except Exception as exc:
                        # 单个 controller 的计时异常不得杀死 RoomOrchestrator；
                        # 否则所有房间的预览重连、磁盘守卫和持续分析状态都会冻结。
                        _log.warning(
                            "Room %s controller tick failed: %s",
                            room.room_id,
                            exc,
                        )
                elif room.room_id not in self._missing_controller_tick_warned:
                    self._missing_controller_tick_warned.add(room.room_id)
                    _log.warning(
                        "Room %s controller has no tick(); heartbeat continues in degraded mode",
                        room.room_id,
                    )

            if room.preview_enabled and not room.preview_paused:
                widget = room.preview_widget
                if widget is not None:
                    pos_fn = getattr(widget, "time_pos", None)
                    if callable(pos_fn):
                        try:
                            pos = float(pos_fn() or 0.0)
                            if pos > 0:
                                controller.current_sec = pos
                        except Exception as exc:
                            _log.debug("操作异常（已忽略）: %s", exc)

            # ── Medium-frequency (staggered): file size + health check ──
            if room_medium:
                # 首帧写入校正: 仅在 recording_media_start_mono 未设置精确值时执行。
                # 共享进样模式下 _wait_for_start_mono 已写入精确值，不可被启发式覆盖。
                if (room.is_recording and room.record_output_path
                        and room.recording_start_mono
                        and not getattr(room, '_first_frame_corrected', False)
                        and not getattr(room, 'recording_media_start_mono', None)):
                    try:
                        if os.path.exists(room.record_output_path):
                            file_size = os.path.getsize(room.record_output_path)
                            if file_size > 10240:
                                media_start = _time.monotonic() - 2.5
                                room.recording_start_mono = media_start
                                room.recording_media_start_mono = media_start
                                room._first_frame_corrected = True
                                if room.recording_id:
                                    get_timeline_service().on_recording_id_change(
                                        room.room_id,
                                        room.recording_id,
                                        media_start_mono=media_start,
                                    )
                                _log.info(
                                    "Room %s recording_start_mono 首帧校正完成 (file_size=%d)",
                                    room.room_id, file_size,
                                )
                    except OSError:
                        pass

                if room.is_recording and room.record_output_path:
                    self._submit_worker(
                        SizeUpdateJob(room, room.record_output_path).run
                    )

                if room.is_recording and not room.is_reconnecting:
                    watchdog_fn = getattr(controller, "watchdog_check", None)
                    try:
                        error_msg = watchdog_fn() if callable(watchdog_fn) else None
                    except Exception as exc:
                        _log.warning(
                            "Room %s controller watchdog failed: %s",
                            room.room_id,
                            exc,
                        )
                        error_msg = None
                    if error_msg:
                        _log.warning("Room %s watchdog: %s", room.room_id, error_msg)
                        _mark_failed_candidate(room, error_msg)
                        self._start_recording_reconnect_thread(room, error_msg)

            # 重连到期必须每 tick 检查，不得被交错轮询推迟
            if (room.is_recording and room.is_reconnecting
                    and room.reconnect_next_attempt_at > 0
                    and _time.monotonic() >= room.reconnect_next_attempt_at):
                self._start_recording_reconnect_thread(
                    room,
                    room.last_error or "录制恢复到期",
                )

            # ── Low-frequency (~12s): disk space check ──
            if is_low_tick and room.is_recording:
                if self._pipeline_component_enabled("stream_lease_v2", room):
                    self._schedule_v2_lease_refresh(room)
                try:
                    rec_dir = (
                        getattr(controller, "output_dir", "")
                        or os.path.dirname(room.record_output_path or "")
                    )
                    if not rec_dir or not os.path.isdir(rec_dir):
                        _log.debug("Disk check skipped for room %s: rec_dir=%r", room.room_id, rec_dir)
                    else:
                        free = shutil.disk_usage(rec_dir).free
                        if free < _MIN_FREE_BYTES_WHILE_RECORDING:
                            _log.warning(
                                "Room %s disk space low (%.1f GB left), stopping recording",
                                room.room_id,
                                free / (1024 ** 3),
                            )
                            self.stop_recording_async(room.room_id)
                            disk_msg = f"磁盘空间不足，录制已自动停止（剩余 {free / (1024 ** 3):.1f} GB）"
                            room.last_error = disk_msg
                            self._dirty_recording = True
                            try:
                                self.bus.emit("recording_stopped",
                                    room.room_id, 'disk_full', disk_msg,
                                )
                            except Exception as exc:
                                _log.debug("recording_stopped emit failed: %s", exc)
                except Exception as exc:
                    _log.warning("Disk space check failed for room %s: %s", room.room_id, exc)

                # 主动流 URL 过期检测：在 URL 过期前重启录制以获取新 URL
                if room.is_recording and not room.is_reconnecting:
                    stream_url = ""
                    if room.stream_info and room.stream_info.stream_url:
                        stream_url = room.stream_info.stream_url
                    if stream_url and _is_stream_url_expiring(stream_url):
                        _log.info("Room %s stream URL expiring soon, proactive reconnect", room.room_id)
                        room.is_reconnecting = True
                        room._cancel_reconnect.clear()
                        self._do_proactive_reconnect(room)

        # Notify UI to refresh timelines and stats.
        # Always emit on high-frequency ticks for smooth timeline updates.
        self.bus.emit("global_tick")

        # Medium-frequency: emit signal so backend can broadcast updated
        # file sizes for recording rooms (rooms_updated with fresh record_size_mb).
        if is_medium_tick:
            self.bus.emit("medium_tick")

        if is_low_tick:
            self.bus.emit("low_tick")

        # Reset dirty flags after emission
        self._dirty_recording = False
        self._dirty_connection = False
