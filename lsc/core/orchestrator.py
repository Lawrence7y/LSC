from __future__ import annotations

import logging
import os
import queue
import re
import threading
import time
import traceback
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from lsc.core.events import EventBus
from lsc.gui.multi_room.session import RoomSession
from lsc.platforms.base import StreamInfo

_log = logging.getLogger(__name__)

ControllerFactory = Callable[[], object]
PreviewFactory = Callable[[], object]

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
        from urllib.parse import parse_qs
        from urllib.parse import urlparse as _urlparse
        params = parse_qs(_urlparse(url).query)
        now = time.time()
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


class RoomOrchestrator:
    _MAX_PENDING_REQUESTS = 8

    def __init__(
        self,
        controller_factory: ControllerFactory | None = None,
        preview_factory: PreviewFactory | None = None,
    ) -> None:
        self._controller_factory = controller_factory
        self._preview_factory = preview_factory
        self.bus = EventBus()
        self._cmd_queue: queue.Queue[Any] = queue.Queue()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._pending_count = 0
        self._pending_lock = threading.Lock()
        self._rooms: dict = {}
        self._lock = threading.RLock()
        self._tick_counter = 0
        self._next_tick_deadline: float | None = None
        self._loop_deadline: float | None = None
        self._worker_pool = ThreadPoolExecutor(max_workers=6, thread_name_prefix="orch-worker")

    @property
    def thread_ident(self) -> int | None:
        t = self._thread
        return t.ident if t else None

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
                self._on_deadlines()
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
            self._on_deadlines()

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

    def call(self, fn: Callable, *args, timeout: float = 10.0, **kwargs) -> Any:
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
        self._cmd_queue.put(req)
        if not req.event.wait(timeout=timeout):
            req.cancelled = True
            raise TimeoutError("orchestrator call timed out")
        if req.exception is not None:
            raise req.exception
        return req.result

    def submit(self, fn: Callable, *args, **kwargs) -> None:
        if self._thread and threading.current_thread() is self._thread:
            try:
                fn(*args, **kwargs)
            except Exception:
                _log.exception("submit on orch thread failed")
            return
        self._cmd_queue.put(lambda: fn(*args, **kwargs))

    def _ensure_tick_armed(self) -> None:
        if self._next_tick_deadline is None:
            self._next_tick_deadline = time.monotonic() + (_TICK_INTERVAL_MS / 1000.0)

    def _disarm_tick_if_empty(self) -> None:
        with self._lock:
            empty = len(self._rooms) == 0
        if empty:
            self._next_tick_deadline = None

    def _on_global_tick(self) -> None:
        self.bus.emit("global_tick")

    def _on_preview_loop_tick(self) -> None:
        pass

    def shutdown(self, timeout_sec: float = 10.0) -> dict[str, int]:
        self._stop.set()
        self._cmd_queue.put(None)
        if self._thread:
            self._thread.join(timeout=timeout_sec)
        self._worker_pool.shutdown(wait=False, cancel_futures=True)
        return {
            "rooms": 0,
            "recordings_stopped": 0,
            "previews_stopped": 0,
            "workers_cancelled": 0,
            "controllers_cleaned": 0,
            "previews_cleaned": 0,
        }
