"""Manager for multi-room workbench sessions.

MultiRoomManager is the central orchestration layer for the multi-room
live-stream recording and preview workbench. It owns the lifecycle of every
``RoomSession``—connecting to live streams, starting/stopping previews and
recordings, persisting room state across restarts, and broadcasting
heartbeat signals so the UI can refresh timelines without polling.

Key concepts:
- **RoomSession**: A lightweight data object (see ``session.py``) that
  tracks a single room's connection state, controller, preview widget,
  recording path, and user selections.
- **Controller**: An FFmpeg-backed ``RecordingController`` created lazily per
  room. It is responsible for actual capture, export, and metadata probing.
- **Preview widget**: An ``MpvWidget`` (libmpv) instance created lazily; up
  to ``MAX_CONCURRENT_PREVIEWS`` can be active simultaneously to limit
  resource consumption.
- **Global heartbeat**: A 1-second ``QTimer`` that fires layered updates
  (high/medium/low frequency) for elapsed time, file-size tracking, and
  disk-space monitoring.
- **Audio alignment**: An offline cross-correlation pass that computes
  per-room time offsets so multi-room clips can be synchronised to a common
  reference frame.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import time as _time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from threading import Lock
from typing import Any
from uuid import uuid4

from PySide6.QtCore import (
    QCoreApplication,
    QObject,
    QRunnable,
    QThread,
    QThreadPool,
    QTimer,
    Signal,
)

from lsc.config import ExportProfile, load_config
from lsc.core.services.ingest_registry import get_shared_ingest_registry
from lsc.core.services.recording_service import RecordingService
from lsc.core.services.timeline_service import get_timeline_service
from lsc.platforms.base import StreamInfo
from lsc.platforms.registry import parse_stream, select_quality

from .session import RoomSession

ControllerFactory = Callable[[], object]
PreviewFactory = Callable[[], object]


class SizeUpdateRunnable(QRunnable):
    """Background task to query file size without blocking the GUI thread.

    Includes caching to avoid redundant file system calls.
    """
    _size_cache: dict[str, tuple[float, float]] = {}  # path -> (size_mb, timestamp)
    _cache_lock: Lock = Lock()
    _CACHE_TTL = 2.0  # Cache for 2 seconds

    def __init__(self, room: RoomSession, path: str) -> None:
        super().__init__()
        self.room = room
        self.path = path

    def run(self):
        try:
            import time as _time
            now = _time.time()

            # Check cache first
            with self._cache_lock:
                cached = self._size_cache.get(self.path)
                if cached and (now - cached[1]) < self._CACHE_TTL:
                    self.room.record_size_mb = cached[0]
                    return

            # Query file system
            size = os.path.getsize(self.path) / (1024 * 1024)
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
# High-frequency: elapsed time, playback position (every tick)
_HIGH_FREQ_INTERVAL = 1
# Medium-frequency: file size, FFmpeg health (every N ticks)
_MEDIUM_FREQ_INTERVAL = 5
_SHARED_INGEST_STALL_CHECKS = 6  # 6 × 5s medium tick ≈ 30s，与 capture.check_health 对齐
# Low-frequency: disk space check (every N ticks)
_LOW_FREQ_INTERVAL = 10

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
        import time as _time_mod
        from urllib.parse import parse_qs
        from urllib.parse import urlparse as _urlparse
        params = parse_qs(_urlparse(url).query)
        now = _time_mod.time()
        for key in ('expire', 'expires', 'wsTime'):
            vals = params.get(key, [])
            if not vals:
                continue
            raw = vals[0]
            try:
                ts = int(raw, 16) if all(c in '0123456789abcdefABCDEF' for c in raw) and len(raw) >= 6 else int(raw)
            except (ValueError, OverflowError):
                continue
            if now > ts - threshold_sec:
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


class _ConnectWorker(QThread):
    """Background thread for non-blocking stream URL parsing.

    Carries the parsed StreamInfo back to the main thread via signal so
    that the registry is only hit once per connection attempt.
    """

    connect_finished = Signal(str, bool, str, object)  # room_id, success, error, StreamInfo|None

    def __init__(self, room_id: str, url: str, quality_preset: str = "原画"):
        super().__init__()
        self._room_id = room_id
        self._url = url
        self._quality_preset = quality_preset

    def run(self):
        try:
            # 允许外部通过 requestInterruption() 优雅地取消解析
            if self.isInterruptionRequested():
                self.connect_finished.emit(self._room_id, False, "已取消", None)
                return
            info = parse_stream(self._url)
            if self.isInterruptionRequested():
                self.connect_finished.emit(self._room_id, False, "已取消", None)
                return
            if info.is_live and self._quality_preset:
                stream_url, selected_quality = select_quality(info, self._quality_preset)
                if stream_url:
                    info.stream_url = stream_url
                    info.selected_quality = selected_quality
            success = bool(info.is_live and info.stream_url)
            error = "" if success else (info.error or "连接失败")
            self.connect_finished.emit(self._room_id, success, error, info)
        except Exception as exc:
            self.connect_finished.emit(self._room_id, False, str(exc), None)


class _MetadataProbeWorker(QThread):
    """Background thread for non-blocking ffprobe of stream resolution/fps.

    连接成功后异步探测直播流分辨率与帧率，避免 ffprobe 子进程阻塞 UI。
    结果通过 ``probe_finished`` 信号回传主线程，由 manager 回填 RoomSession。
    """

    probe_finished = Signal(str, str, str)  # room_id, resolution, fps

    def __init__(self, room_id: str, stream_url: str, controller: object):
        super().__init__()
        self._room_id = room_id
        self._stream_url = stream_url
        self._controller = controller

    def run(self):
        try:
            if self.isInterruptionRequested():
                return
            probe_fn = getattr(self._controller, "probe_stream_metadata", None)
            if not callable(probe_fn):
                return
            resolution, fps = probe_fn(self._stream_url)
            if self.isInterruptionRequested():
                return
            self.probe_finished.emit(self._room_id, resolution, fps)
        except Exception as exc:
            _log.debug("Metadata probe failed for room %s: %s", self._room_id, exc)
            self.probe_finished.emit(self._room_id, "", "")


class _BatchRecordWorker(QThread):
    """Background thread for parallel batch recording start.

    Submits start_recording tasks to an internal ThreadPoolExecutor
    (up to 4 concurrent), emitting progress per room as each completes.

    Threading note: calls manager.start_recording() which writes room
    state attributes (is_recording, record_output_path, etc.) from
    worker threads. This is safe because:
    1. Python GIL makes simple attribute writes atomic.
    2. UI refreshes are signal-driven (room_started → main thread),
       not polling, so no torn reads occur in practice.
    """

    room_started = Signal(str, bool)  # room_id, success
    batch_finished = Signal(int, int)  # started_count, total_count

    def __init__(self, manager: MultiRoomManager, room_ids: list[str],
                 output_dir: str, encoder: str, crf: int,
                 param_mode: str = "CRF 质量", bitrate: str | None = None,
                 bitrate_unit: str = "kbps"):
        super().__init__()
        self._manager = manager
        self._room_ids = room_ids
        self._output_dir = output_dir
        self._encoder = encoder
        self._crf = crf
        self._param_mode = param_mode
        self._bitrate = bitrate
        self._bitrate_unit = bitrate_unit

    def run(self):
        started = 0
        worker_count = min(4, len(self._room_ids))
        with ThreadPoolExecutor(max_workers=worker_count) as pool:
            futures = {}
            for room_id in self._room_ids:
                if self.isInterruptionRequested():
                    break
                fut = pool.submit(
                    self._manager.start_recording,
                    room_id, self._output_dir, self._encoder, self._crf,
                    param_mode=self._param_mode,
                    bitrate=self._bitrate,
                    bitrate_unit=self._bitrate_unit,
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
                self.room_started.emit(room_id, ok)
        self.batch_finished.emit(started, len(self._room_ids))


class MultiRoomManager(QObject):
    """Own room session lifecycle and batch operations."""

    room_connect_finished = Signal(str, bool, str)  # room_id, success, error
    batch_record_progress = Signal(str, bool)  # room_id, success
    batch_record_finished = Signal(int, int)  # started_count, total_count
    recording_stopped = Signal(str, str, str)  # room_id, reason, message
    # Emitted on every global tick so the UI can refresh timelines and
    # recording elapsed-time displays without polling on its own timer.
    global_tick = Signal()
    # Emitted on medium-frequency ticks (every 5s) when recording rooms
    # have updated file sizes, so the backend can broadcast rooms_updated.
    medium_tick = Signal()
    # Emitted on low-frequency ticks (every 10s) for disk space checks
    # and other low-frequency monitoring tasks.
    low_tick = Signal()

    def __init__(
        self,
        controller_factory: ControllerFactory | None = None,
        preview_factory: PreviewFactory | None = None,
    ) -> None:
        super().__init__()
        self._controller_factory = controller_factory
        self._preview_factory = preview_factory
        self._rooms: dict[str, RoomSession] = {}
        self._connect_workers: dict[str, _ConnectWorker] = {}
        self._metadata_probe_workers: dict[str, _MetadataProbeWorker] = {}
        self._batch_record_worker: _BatchRecordWorker | None = None

        # Global heartbeat timer — created lazily when QCoreApplication exists
        self._global_timer: QTimer | None = None

        # Heartbeat tick counter for layered frequency control
        self._tick_counter: int = 0
        # Dirty flags to avoid redundant UI updates
        self._dirty_recording: bool = False
        self._dirty_connection: bool = False

        # 选区循环试听状态
        self._preview_loop_timer: QTimer | None = None
        self._loop_room_id: str | None = None
        self._loop_start: float = 0.0
        self._loop_end: float = 0.0
        self._loop_native: bool = False

    def _ensure_global_timer(self) -> QTimer | None:
        """Create the global timer if a QCoreApplication is available."""
        if self._global_timer is not None:
            return self._global_timer
        if QCoreApplication.instance() is None:
            return None  # No Qt app (e.g. in unit tests)
        self._global_timer = QTimer(self)
        self._global_timer.setInterval(1000)
        self._global_timer.timeout.connect(self._on_global_tick)
        return self._global_timer

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
        """Add a room. Returns None if MAX_ROOMS limit is reached."""
        if len(self._rooms) >= MAX_ROOMS:
            _log.warning("Room limit reached (%d), cannot add more", MAX_ROOMS)
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
        if len(self._rooms) == 1:
            self._start_global_timer()

        # Persist the updated room list (skip during batch load)
        if not getattr(self, "_batch_loading", False):
            self.save_rooms()

        return room

    def get_room(self, room_id: str) -> RoomSession | None:
        """Return the ``RoomSession`` for ``room_id``, or ``None`` if not found."""
        return self._rooms.get(room_id)

    def list_rooms(self) -> list[RoomSession]:
        """Return all currently managed ``RoomSession`` objects."""
        return list(self._rooms.values())

    def room_count(self) -> int:
        """Return the number of rooms currently managed."""
        return len(self._rooms)

    def max_rooms(self) -> int:
        """Return the hard upper limit on concurrently managed rooms."""
        return MAX_ROOMS

    def remove_room(self, room_id: str) -> bool:
        """Remove a room and clean up all associated resources.

        Stops any active preview or recording, cancels pending async workers
        (connect, metadata probe, refresh), disposes the controller and
        preview widget, and persists the updated room list. If this was the
        last room, the global heartbeat timer is also stopped.

        Returns:
            True if the room was found and removed; False otherwise.
        """
        room = self._rooms.pop(room_id, None)
        if room is None:
            return False
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
        probe = self._metadata_probe_workers.pop(room_id, None)
        if probe is not None and probe.isRunning():
            probe.requestInterruption()
            probe.wait(1000)

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
        if not self._rooms:
            self._stop_global_timer()

        # Persist the updated room list
        self.save_rooms()

        return True

    # ── Persistence ─────────────────────────────────────────

    def _config_file_path(self) -> str:
        """Return the JSON config file path for room persistence."""
        # Prefer user data directory; fallback to ./config/rooms.json
        app = QCoreApplication.instance()
        if app is not None:
            org_name = app.organizationName() or "LSC"
            app_name = app.applicationName() or "LiveStreamClipper"
            base = os.path.join(
                os.path.expanduser("~"),
                f".{org_name.lower()}",
                app_name,
            )
        else:
            base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config")
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
        """Persist the current room list atomically.

        Writes to a temporary file first, then renames it into place.
        Keeps a .bak copy of the previous config so load_rooms can recover
        from a corrupt primary file.

        Returns number of saved rooms.
        """
        import json

        data = {
            "version": 2,
            "saved_at": datetime.now().isoformat(timespec="seconds"),
            "rooms": [self._serialize_room(room) for room in self._rooms.values()],
        }

        path = self._config_file_path()
        tmp_path = self._temp_config_path()
        bak_path = self._backup_config_path()

        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())

            # Keep a backup of the existing config before overwriting it.
            if os.path.isfile(path):
                try:
                    os.replace(path, bak_path)
                except Exception as exc:
                    _log.warning("Failed to create config backup: %s", exc)

            os.replace(tmp_path, path)
            _log.info("Saved %d rooms to %s", len(self._rooms), path)
            return len(self._rooms)
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
        room = self.get_room(room_id)
        if room is None:
            return False

        if async_mode:
            return self._connect_room_async(room, quality_preset=quality_preset)
        return self._connect_room_sync(room, quality_preset=quality_preset)

    def _connect_room_sync(self, room: RoomSession, quality_preset: str = "原画") -> bool:
        info = parse_stream(room.room_url)
        if info.is_live and quality_preset:
            stream_url, selected_quality = select_quality(info, quality_preset)
            if stream_url:
                info.stream_url = stream_url
                info.selected_quality = selected_quality
        return self._apply_stream_info(room, info)

    def _connect_room_async(self, room: RoomSession, quality_preset: str = "原画") -> bool:
        if room.room_id in self._connect_workers:
            return False  # Already connecting

        room.is_connecting = True
        room.last_error = ""

        worker = _ConnectWorker(room.room_id, room.room_url, quality_preset)
        worker.connect_finished.connect(self._on_connect_finished)
        worker.finished.connect(worker.deleteLater)
        self._connect_workers[room.room_id] = worker
        worker.start()
        return True

    def _on_connect_finished(self, room_id: str, success: bool, error: str,
                             info: StreamInfo | None) -> None:
        self._connect_workers.pop(room_id, None)
        room = self.get_room(room_id)
        if room is None or room.disconnect_requested:
            return

        if success and info is not None:
            # Reuse the StreamInfo parsed in the worker thread — no second HTTP request.
            self._apply_stream_info(room, info)
            # 异步探测分辨率/帧率回填详情面板（原详情面板"帧率"恒为 --）。
            if info.stream_url:
                self._probe_metadata_async(room_id, info.stream_url)
        else:
            room.set_error(error or "连接失败")

        self.room_connect_finished.emit(room_id, success, error)

    def _probe_metadata_async(self, room_id: str, stream_url: str) -> None:
        """启动后台 ffprobe 探测直播流分辨率/帧率，结果回填 RoomSession。"""
        room = self.get_room(room_id)
        if room is None or room.controller is None:
            return
        # 若已有探测在跑，先取消旧的
        existing = self._metadata_probe_workers.pop(room_id, None)
        if existing is not None and existing.isRunning():
            existing.requestInterruption()
            existing.wait(2000)
        worker = _MetadataProbeWorker(room_id, stream_url, room.controller)
        worker.probe_finished.connect(self._on_probe_finished)
        worker.finished.connect(worker.deleteLater)
        self._metadata_probe_workers[room_id] = worker
        worker.start()

    def _on_probe_finished(self, room_id: str, resolution: str, fps: str) -> None:
        """ffprobe 探测完成回调（主线程执行）：回填分辨率/帧率并刷新 UI。"""
        self._metadata_probe_workers.pop(room_id, None)
        room = self.get_room(room_id)
        if room is None:
            return
        room.stream_resolution = resolution
        room.stream_fps = fps
        self._dirty_connection = True


    def _apply_stream_info(self, room: RoomSession, info: StreamInfo) -> bool:
        """Apply parsed StreamInfo to room session and controller."""
        room.apply_stream_info(info)
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
        worker = self._connect_workers.pop(room_id, None)
        if worker and worker.isRunning():
            # _ConnectWorker 重写了 run(),没有事件循环,quit() 无效。
            # 用 requestInterruption() 让 run() 主动退出,并等待更长时间。
            worker.requestInterruption()
            if not worker.wait(10000):
                # D-6: 不调用 terminate()（可能导致锁未释放/状态不一致），
                # 改为记录警告，让 daemon 行为自然清理
                _log.warning("Connect worker for room %s did not stop in 10s, leaving as daemon", room_id)

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
        room = self.get_room(room_id)
        if room is None or not room.is_connected:
            return False

        # Enforce preview concurrency limit
        if self.get_active_preview_count() >= MAX_CONCURRENT_PREVIEWS:
            _log.warning("Preview limit reached (%d), cannot start more", MAX_CONCURRENT_PREVIEWS)
            room.preview_error = f"预览数已达上限 ({MAX_CONCURRENT_PREVIEWS})"
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
            self._preview_loop_timer = QTimer(self)
            self._preview_loop_timer.setInterval(50)
            self._preview_loop_timer.timeout.connect(self._on_loop_tick)
            self._preview_loop_timer.start()

    def stop_range_loop(self) -> None:
        """停止选区循环播放。"""
        if self._loop_native:
            room = self.get_room(self._loop_room_id or "")
            widget = room.preview_widget if room else None
            if widget is not None and hasattr(widget, "clear_ab_loop"):
                try:
                    widget.clear_ab_loop()
                except Exception as exc:
                    _log.debug("操作异常（已忽略）: %s", exc)
            self._loop_native = False
        if self._preview_loop_timer is not None:
            self._preview_loop_timer.stop()
            self._preview_loop_timer = None
        self._loop_room_id = None

    def is_range_loop_active(self) -> bool:
        """返回当前是否正在循环试听。"""
        return self._loop_room_id is not None

    def _on_loop_tick(self) -> None:
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

    def refresh_stream_url(self, room_id: str, *, force: bool = False) -> bool:
        """Re-parse the stream to get a fresh CDN URL. Returns True on success.

        Args:
            force: If True, bypass the 30s parse cache and force a fresh
                   HTTP request. Used by MSE reconnect to avoid getting
                   a cached (possibly expired) stream URL.
        """
        room = self.get_room(room_id)
        if room is None:
            return False
        # 连接刚完成时复用房间级流缓存，避免预览再打一轮 10s+ 平台解析
        if not force and _room_stream_is_reusable(room):
            room.is_connected = True
            _sync_controller_stream(room)
            _log.info("refresh_stream_url reuse cached stream for %s (age<=%.0fs)", room_id, _STREAM_CACHE_REUSE_SEC)
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
        room = self.get_room(room_id)
        if room is None:
            callback(room_id, False)
            return

        class _RefreshWorker(QThread):
            finished = Signal(str, bool)

            def __init__(self, manager, rid):
                super().__init__()
                self._manager = manager
                self._rid = rid
                self.finished.connect(callback)

            def run(self):
                ok = self._manager.refresh_stream_url(self._rid)
                self.finished.emit(self._rid, ok)

        worker = _RefreshWorker(self, room_id)
        worker.finished.connect(lambda *_: worker.deleteLater())
        worker.start()
        # Store reference to prevent garbage collection
        self._connect_workers[f"_refresh_{room_id}"] = worker

    # ── Mute ─────────────────────────────────────────────────

    def mute_room(self, room_id: str, muted: bool) -> None:
        self.set_preview_muted(room_id, muted)

    # ── Recording ────────────────────────────────────────────

    def _refresh_room_stream_for_recording(self, room: RoomSession) -> bool:
        """Refresh short-lived CDN URLs before starting FFmpeg recording."""
        # 刚连接成功时直接复用缓存，避免录制前再解析一次（抖音/B站可达 10s+）
        if _room_stream_is_reusable(room):
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
            info = parse_stream(room.room_url, force_refresh=False)
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

    def start_recording(self, room_id: str, output_dir: str, encoder: str, crf: int,
                        param_mode: str = "CRF 质量", bitrate: str | None = None,
                        bitrate_unit: str = "kbps",
                        resolution: str | None = None,
                        framerate: str | None = None,
                        audio_bitrate: str | None = None) -> bool:
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
        _log.info("[录制诊断] start_recording called for room_id=%s", room_id)
        room = self.get_room(room_id)
        if room is None:
            _log.warning("[录制诊断] room not found: %s", room_id)
            return False
        controller = room.controller
        if controller is None:
            _log.warning("[录制诊断] controller is None for room %s (is_connected=%s)", room_id, room.is_connected)
            room.last_error = "录制控制器未初始化"
            return False
        _log.info("[录制诊断] controller OK, is_connected=%s, stream_url=%s", room.is_connected, bool(getattr(controller, 'stream_url', None)))
        if not room.is_connected:
            # 预览刷新失败可能误清 is_connected，但流缓存仍可用
            if not _heal_connected_flag(room):
                room.last_error = "房间未连接"
                return False
        # Pre-flight disk space check (2GB threshold per project memory constraint)
        preflight = RecordingService.preflight_check(output_dir, concurrent_streams=1)
        if preflight:
            # Fallback chain for unwritable / full output directories:
            #   1. If the configured dir fails, try ~/.lsc/output (user home, usually writable).
            #   2. If that also fails, surface the error and abort so FFmpeg
            #      doesn't start and immediately die mid-write.
            fallback_base = os.path.join(os.path.expanduser('~'), '.lsc', 'output')
            if os.path.abspath(fallback_base) != os.path.abspath(output_dir):
                _log.warning("预检失败 %s，回退到 %s", output_dir, fallback_base)
                fallback_preflight = RecordingService.preflight_check(fallback_base, concurrent_streams=1)
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
            encoder, crf, param_mode, bitrate, bitrate_unit, resolution, framerate, audio_bitrate,
        )
        shared_output, shared_media_start, shared_error = self._start_shared_recording_if_enabled(
            room, room_output_dir, stream_url, shared_profile,
        )
        if shared_output:
            output_path = shared_output
            media_start_mono = shared_media_start
            room.is_recording = True
            room.record_output_path = output_path
            room.record_started_at = datetime.now()
            # 共享进样模式也需要同步 controller.video_path，否则导出时找不到文件
            if controller is not None:
                controller.video_path = output_path
            room.recording_start_mono = _time.monotonic()
            room.recording_media_start_mono = media_start_mono or None
            room._first_frame_corrected = False
            room._shared_ingest_last_file_size = 0
            room._shared_ingest_stall_checks = 0
            room.recording_id = uuid4().hex
            get_timeline_service().on_recording_id_change(room_id, room.recording_id)
            self._dirty_recording = True
            room.reconnect_output_dir = room_output_dir
            room.reconnect_encoder = encoder
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
            return False

        ok, output_path, _encoder_used, error_msg = controller.start_recording_with_crf(
            stream_url,
            room_output_dir,
            encoder,
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
        room.record_started_at = datetime.now() if ok else None
        if ok:
            room.recording_start_mono = getattr(controller, 'recording_start_mono', 0.0) or _time.monotonic()
            room.recording_media_start_mono = None
            # 重置首帧校正标记, 以便中频 tick 重新校正 (重连场景)
            room._first_frame_corrected = False
            room._shared_ingest_last_file_size = 0
            room._shared_ingest_stall_checks = 0
            room.recording_id = uuid4().hex
            get_timeline_service().on_recording_id_change(room_id, room.recording_id)
        else:
            room.recording_start_mono = None
            room.recording_media_start_mono = None
            room.recording_id = ''
        # Mark state changed for UI refresh
        self._dirty_recording = True
        if ok:
            # Save recording params for auto-reconnect
            room.reconnect_output_dir = room_output_dir
            room.reconnect_encoder = encoder
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
        return ExportProfile(
            codec=encoder,
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
        if not getattr(cfg, "shared_ingest_enabled", False):
            return "", 0.0, ""

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        unique_suffix = uuid4().hex[:6]
        output_path = os.path.join(room_output_dir, f"recording_{timestamp}_{unique_suffix}.mp4")
        headers = {}
        if room.stream_info is not None:
            headers = dict(getattr(room.stream_info, "headers", {}) or {})

        registry = get_shared_ingest_registry()
        ingest = registry.get_or_create(room.room_id, url=stream_url, headers=headers)
        try:
            result = ingest.start_recording(output_path, profile=profile)
        except Exception as exc:
            registry.stop_room(room.room_id, reason="shared recording start exception")
            _log.warning("shared ingest recording failed room=%s: %s", room.room_id, exc)
            return "", 0.0, str(exc)
        if result.ok:
            return output_path, getattr(ingest, "recording_media_start_mono", 0.0), ""
        registry.stop_room(room.room_id, reason="shared recording start failed")
        _log.warning("shared ingest recording failed room=%s: %s", room.room_id, result.error)
        return "", 0.0, result.error

    def stop_recording(self, room_id: str) -> bool:
        """Stop FFmpeg recording for a room and reset reconnect state.

        Delegates to ``RecordingController.stop_recording``, clears the
        room's recording flags, and triggers an async duration probe so the
        UI can show the final clip length once FFprobe reports back.

        Returns:
            True if the controller accepted the stop request.
        """
        room = self.get_room(room_id)
        if room is None:
            return False
        controller = room.controller
        registry = get_shared_ingest_registry()
        shared_ingest = registry.get(room_id)
        if shared_ingest is not None and getattr(shared_ingest, "recording_active", False):
            output_path = room.record_output_path or getattr(shared_ingest, "_recording_path", "")
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
                def _on_probed(duration):
                    if duration > 0:
                        controller.total_sec = int(duration)
                probe_fn(on_probed=_on_probed)
        return ok

    def stop_recording_async(self, room_id: str) -> bool:
        """Non-blocking stop: marks state immediately, FFmpeg cleaned up in background.

        Avoids blocking the Qt main thread for up to 13 seconds (5+3+5 three-level stop).
        The caller can continue immediately; FFmpeg is cleaned up in a background thread.
        """
        room = self.get_room(room_id)
        if room is None:
            return False
        controller = room.controller
        registry = get_shared_ingest_registry()
        shared_ingest = registry.get(room_id)
        if shared_ingest is not None and getattr(shared_ingest, "recording_active", False):
            output_path = room.record_output_path or getattr(shared_ingest, "_recording_path", "")
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
                def _on_probed(duration):
                    if duration > 0:
                        controller.total_sec = int(duration)
                probe_fn(on_probed=_on_probed)
        return True

    def start_recording_all(self, output_dir: str, encoder: str, crf: int,
                            param_mode: str = "CRF 质量", bitrate: str | None = None,
                            bitrate_unit: str = "kbps") -> dict[str, bool]:
        """Start recording on every managed room (synchronous, blocking)."""
        rooms = self.list_rooms()
        # Pre-flight: ensure disk space scales with concurrent stream count.
        if rooms:
            preflight = RecordingService.preflight_check(
                output_dir, concurrent_streams=len(rooms)
            )
            if preflight:
                _log.warning("批量录制预检失败: %s", preflight)
                return {r.room_id: False for r in rooms}
        return {
            r.room_id: self.start_recording(
                r.room_id,
                output_dir,
                encoder,
                crf,
                param_mode=param_mode,
                bitrate=bitrate,
                bitrate_unit=bitrate_unit,
            )
            for r in rooms
        }

    def start_recording_all_async(self, output_dir: str, encoder: str, crf: int,
                                  param_mode: str = "CRF 质量", bitrate: str | None = None,
                                  bitrate_unit: str = "kbps") -> bool:
        """Start recording on all connected rooms without blocking the UI thread.

        Pre-flight disk space check runs synchronously on the caller thread;
        if it fails, returns False and emits no signals. Otherwise a
        background worker iterates over rooms, emitting
        ``batch_record_progress`` per room and ``batch_record_finished``
        when done.

        Returns True if the worker was started, False on pre-flight failure
        or if a batch is already running.
        """
        if self._batch_record_worker is not None and self._batch_record_worker.isRunning():
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
        worker = _BatchRecordWorker(
            self,
            room_ids,
            output_dir,
            encoder,
            crf,
            param_mode=param_mode,
            bitrate=bitrate,
            bitrate_unit=bitrate_unit,
        )
        worker.room_started.connect(self.batch_record_progress)
        worker.batch_finished.connect(self.batch_record_finished)
        # 完成后清理引用并释放 QObject
        worker.batch_finished.connect(lambda: setattr(self, '_batch_record_worker', None))
        worker.batch_finished.connect(worker.deleteLater)
        # Keep a reference so the worker isn't garbage-collected mid-run.
        self._batch_record_worker = worker
        worker.start()
        return True

    def stop_recording_all(self) -> dict[str, bool]:
        """Stop recording on every managed room and return per-room results."""
        return {r.room_id: self.stop_recording(r.room_id) for r in self.list_rooms()}

    def shutdown(self, timeout_sec: float = 10.0) -> dict[str, int]:
        """Release all runtime resources owned by the manager.

        This is an application-exit cleanup path. It intentionally does not
        call save_rooms(), because shutdown should not overwrite the user's
        persisted room list with an empty runtime state.
        """
        timeout_ms = max(0, int(timeout_sec * 1000))
        stats = {
            "rooms": len(self._rooms),
            "recordings_stopped": 0,
            "previews_stopped": 0,
            "workers_cancelled": 0,
            "controllers_cleaned": 0,
            "previews_cleaned": 0,
        }

        self.stop_range_loop()
        self._stop_global_timer()

        def _stop_worker(worker: object | None) -> bool:
            if worker is None:
                return False
            try:
                if hasattr(worker, "requestInterruption"):
                    worker.requestInterruption()
                is_running = getattr(worker, "isRunning", None)
                if callable(is_running) and is_running():
                    wait = getattr(worker, "wait", None)
                    if callable(wait) and not wait(timeout_ms):
                        _log.warning("Worker %s did not stop within %.1fs", worker, timeout_sec)
                return True
            except Exception as exc:
                _log.warning("Worker shutdown failed: %s", exc)
                return False

        for worker in list(self._connect_workers.values()):
            if _stop_worker(worker):
                stats["workers_cancelled"] += 1
        self._connect_workers.clear()

        for worker in list(self._metadata_probe_workers.values()):
            if _stop_worker(worker):
                stats["workers_cancelled"] += 1
        self._metadata_probe_workers.clear()

        if _stop_worker(self._batch_record_worker):
            stats["workers_cancelled"] += 1
        self._batch_record_worker = None

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
        self._dirty_recording = False
        self._dirty_connection = False
        _log.info("MultiRoomManager shutdown complete: %s", stats)
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
        return [r for r in self.list_rooms() if r.include_in_cut]

    def get_total_recording_size_mb(self) -> float:
        """返回所有房间当前录制文件的总大小（MB）。"""
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

    def _attempt_recording_reconnect(self, room: RoomSession, error_msg: str) -> None:
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
        """
        from lsc.utils.error_messages import is_recoverable_error

        # 用户已断开/删除房间，取消重连
        if room._cancel_reconnect.is_set():
            return

        # Check if error is recoverable
        if not is_recoverable_error(error_msg):
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
            return

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
            return

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
            return

        if _time.monotonic() < room.reconnect_next_attempt_at:
            return

        # 用户已断开/删除房间，取消重连
        if room._cancel_reconnect.is_set():
            return

        room.reconnect_attempts += 1
        room.reconnect_next_attempt_at = 0.0
        _log.info("Room %s attempting reconnect %d/%d",
                  room.room_id, room.reconnect_attempts, _MAX_RECONNECT_ATTEMPTS)
        room.last_error = f"正在尝试恢复录制 ({room.reconnect_attempts}/{_MAX_RECONNECT_ATTEMPTS})..."

        # 保存原始错误信息和旧文件路径
        original_error = error_msg
        old_output_path = room.record_output_path

        # Stop the failed recording gracefully
        # ponytail: shared ingest 走快速路径，避免 stop_recording_sink 先重启为 preview-only 再被 start_recording 杀死的双重重启
        registry = get_shared_ingest_registry()
        shared_ingest = registry.get(room.room_id)
        if shared_ingest is not None and getattr(shared_ingest, "recording_active", False):
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
            if not self.refresh_stream_url(room.room_id, force=True):
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
                    self.recording_stopped.emit(room.room_id, 'offline', offline_msg)
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

    def _start_recording_reconnect_thread(self, room: RoomSession, error_msg: str) -> bool:
        t = getattr(room, '_reconnect_thread', None)
        try:
            if t is not None and hasattr(t, 'is_alive') and t.is_alive():
                return False
        except Exception as exc:
            _log.debug("Reconnect thread state check failed for room %s: %s", room.room_id, exc)

        import threading
        room._cancel_reconnect.clear()

        def _reconnect_in_background(room=room, error_msg=error_msg):
            try:
                self._attempt_recording_reconnect(room, error_msg)
            except Exception as exc:
                _log.error("Room %s reconnect failed: %s", room.room_id, exc)
            finally:
                self._dirty_recording = True

        t = threading.Thread(target=_reconnect_in_background, daemon=True)
        room._reconnect_thread = t
        t.start()
        return True

    def _start_global_timer(self) -> None:
        timer = self._ensure_global_timer()
        if timer is not None and not timer.isActive():
            timer.start()

    def _stop_global_timer(self) -> None:
        if self._global_timer is not None and self._global_timer.isActive():
            self._global_timer.stop()

    def _on_global_tick(self) -> None:
        """Layered heartbeat: high/medium/low frequency operations.

        Runs every 1 second. A modulo counter gates medium-frequency
        work (every 5 ticks) and low-frequency work (every 10 ticks).
        This single-timer design avoids spawning multiple QTimer objects
        and keeps all room updates in one deterministic pass.

        Layered breakdown:
        - **High-frequency (every tick)**: controller ``tick()`` for elapsed
          time, and sync of preview playback position into the controller.
        - **Medium-frequency (every 5s)**: file-size polling via
          ``SizeUpdateRunnable`` and FFmpeg watchdog health-check. Failed
          recordings trigger an auto-reconnect in a background thread to
          keep the UI responsive.
        - **Low-frequency (every 10s)**: disk-space guard; if free space
          drops below ``_MIN_FREE_BYTES_WHILE_RECORDING`` the recording is
          stopped automatically to prevent a mid-write crash.

        Dirty flags are reset after signal emission so the UI only refreshes
        when something actually changed.
        """
        self._tick_counter += 1
        is_medium_tick = (self._tick_counter % _MEDIUM_FREQ_INTERVAL == 0)
        is_low_tick = (self._tick_counter % _LOW_FREQ_INTERVAL == 0)

        for room in list(self._rooms.values()):
            controller = room.controller

            # ── Shared ingest health check (controller is None in Electron) ──
            if controller is None and room.is_recording:
                registry = get_shared_ingest_registry()
                ingest = registry.get(room.room_id)
                if ingest is not None:
                    ingest_error = getattr(ingest, "recording_error", "") or getattr(ingest, "upstream_error", "")
                    if ingest_error and not room.is_reconnecting:
                        _log.warning("Room %s shared ingest error: %s", room.room_id, ingest_error)
                        self._start_recording_reconnect_thread(room, ingest_error)
                    elif is_medium_tick and not room.is_reconnecting:
                        if getattr(ingest, "recording_active", False) and room.record_output_path:
                            stall_msg = self._check_shared_ingest_file_stall(room)
                            if stall_msg:
                                _log.warning("Room %s shared ingest stall: %s", room.room_id, stall_msg)
                                self._start_recording_reconnect_thread(room, stall_msg)
                    elif is_medium_tick and room.is_reconnecting:
                        if (room.reconnect_next_attempt_at > 0
                                and _time.monotonic() >= room.reconnect_next_attempt_at):
                            self._start_recording_reconnect_thread(
                                room,
                                room.last_error or "录制恢复到期",
                            )
                continue

            # ── High-frequency (every 1s): lightweight operations ──
            if room.is_recording:
                controller.tick()

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

            # ── Medium-frequency (every 5s): file size + health check ──
            if is_medium_tick:
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
                                _log.info(
                                    "Room %s recording_start_mono 首帧校正完成 (file_size=%d)",
                                    room.room_id, file_size,
                                )
                    except OSError:
                        pass

                if room.is_recording and room.record_output_path:
                    QThreadPool.globalInstance().start(
                        SizeUpdateRunnable(room, room.record_output_path)
                    )

                if room.is_recording and room.is_reconnecting:
                    if (room.reconnect_next_attempt_at > 0
                            and _time.monotonic() >= room.reconnect_next_attempt_at):
                        self._start_recording_reconnect_thread(
                            room,
                            room.last_error or "录制恢复到期",
                        )
                elif room.is_recording:
                    error_msg = controller.watchdog_check()
                    if error_msg:
                        _log.warning("Room %s watchdog: %s", room.room_id, error_msg)
                        self._start_recording_reconnect_thread(room, error_msg)

            # ── Low-frequency (every 30s): disk space check ──
            if is_low_tick and room.is_recording:
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
                                self.recording_stopped.emit(
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
                        import threading
                        def _proactive_reconnect(room=room):
                            try:
                                registry = get_shared_ingest_registry()
                                shared_ingest = registry.get(room.room_id)
                                if shared_ingest is not None and getattr(shared_ingest, "recording_active", False):
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
                                else:
                                    _log.warning("Room %s proactive reconnect failed: %s", room.room_id, room.last_error)
                            except Exception as exc:
                                _log.warning("Room %s proactive reconnect failed: %s", room.room_id, exc)
                            finally:
                                room.is_reconnecting = False
                                self._dirty_recording = True
                        threading.Thread(target=_proactive_reconnect, daemon=True).start()

        # Notify UI to refresh timelines and stats.
        # Always emit on high-frequency ticks for smooth timeline updates.
        self.global_tick.emit()

        # Medium-frequency: emit signal so backend can broadcast updated
        # file sizes for recording rooms (rooms_updated with fresh record_size_mb).
        if is_medium_tick:
            self.medium_tick.emit()

        if is_low_tick:
            self.low_tick.emit()

        # Reset dirty flags after emission
        self._dirty_recording = False
        self._dirty_connection = False
