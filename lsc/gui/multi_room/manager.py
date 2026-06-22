"""Manager for multi-room workbench sessions."""
from __future__ import annotations

import logging
import os
from collections.abc import Callable
from datetime import datetime
from uuid import uuid4

from PySide6.QtCore import QCoreApplication, QObject, QThread, QTimer, Signal, QRunnable, QThreadPool

from lsc.platforms.base import StreamInfo
from lsc.platforms.registry import parse_stream, select_quality

from .session import RoomSession

ControllerFactory = Callable[[], object]
PreviewFactory = Callable[[], object]


class SizeUpdateRunnable(QRunnable):
    """Background task to query file size without blocking the GUI thread."""

    def __init__(self, room, path):
        super().__init__()
        self.room = room
        self.path = path

    def run(self):
        try:
            size = os.path.getsize(self.path) / (1024 * 1024)
            self.room.record_size_mb = size
        except OSError:
            pass

_log = logging.getLogger(__name__)

# ── Resource limits ──────────────────────────────────────────
MAX_ROOMS = 12
MAX_CONCURRENT_PREVIEWS = 4


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


class _BatchRecordWorker(QThread):
    """Background thread for non-blocking batch recording start.

    Iterates over rooms and starts recording on each, emitting progress
    so the UI thread can refresh cards without freezing.

    Threading note: calls manager.start_recording() which writes room
    state attributes (is_recording, record_output_path, etc.) from this
    thread. This is safe because:
    1. Python GIL makes simple attribute writes atomic.
    2. UI refreshes are signal-driven (room_started → main thread),
       not polling, so no torn reads occur in practice.
    """

    room_started = Signal(str, bool)  # room_id, success
    batch_finished = Signal(int, int)  # started_count, total_count

    def __init__(self, manager: "MultiRoomManager", room_ids: list[str],
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
        for room_id in self._room_ids:
            if self.isInterruptionRequested():
                _log.info("批量录制任务被中断")
                break
            ok = self._manager.start_recording(
                room_id, self._output_dir, self._encoder, self._crf,
                param_mode=self._param_mode,
                bitrate=self._bitrate,
                bitrate_unit=self._bitrate_unit,
            )
            if ok:
                started += 1
            self.room_started.emit(room_id, ok)
        self.batch_finished.emit(started, len(self._room_ids))


class MultiRoomManager(QObject):
    """Own room session lifecycle and batch operations."""

    room_connect_finished = Signal(str, bool, str)  # room_id, success, error
    batch_record_progress = Signal(str, bool)  # room_id, success
    batch_record_finished = Signal(int, int)  # started_count, total_count
    # Emitted on every global tick so the UI can refresh timelines and
    # recording elapsed-time displays without polling on its own timer.
    global_tick = Signal()

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
        self._batch_record_worker: _BatchRecordWorker | None = None

        # Global heartbeat timer — created lazily when QCoreApplication exists
        self._global_timer: QTimer | None = None

        # 选区循环试听状态
        self._preview_loop_timer: QTimer | None = None
        self._loop_room_id: str | None = None
        self._loop_start: float = 0.0
        self._loop_end: float = 0.0

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
        from lsc.gui.pages.recording_controller import RecordingController
        controller = RecordingController()
        # 初始化录制和导出组件，否则录制功能无法使用
        controller.init_capture()
        controller.init_exporter()
        return controller

    def _create_preview(self) -> object:
        if self._preview_factory is not None:
            return self._preview_factory()
        from lsc.gui.components.mpv_widget import MpvWidget
        return MpvWidget()

    # ── Room CRUD ────────────────────────────────────────────

    def add_room(self, url: str) -> RoomSession | None:
        """Add a room. Returns None if MAX_ROOMS limit is reached."""
        if len(self._rooms) >= MAX_ROOMS:
            _log.warning("Room limit reached (%d), cannot add more", MAX_ROOMS)
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

        # Persist the updated room list
        self.save_rooms()

        return room

    def get_room(self, room_id: str) -> RoomSession | None:
        return self._rooms.get(room_id)

    def list_rooms(self) -> list[RoomSession]:
        return list(self._rooms.values())

    def room_count(self) -> int:
        return len(self._rooms)

    def max_rooms(self) -> int:
        return MAX_ROOMS

    def remove_room(self, room_id: str) -> bool:
        room = self._rooms.pop(room_id, None)
        if room is None:
            return False
        if room.preview_enabled:
            self.stop_preview(room_id)
        if room.is_recording:
            self.stop_recording(room_id)

        # Cancel pending async connect
        worker = self._connect_workers.pop(room_id, None)
        if worker and worker.isRunning():
            # _ConnectWorker 重写了 run()，没有事件循环，quit() 无效。
            # 使用 requestInterruption() 让 run() 主动退出，并等待一段时间。
            worker.requestInterruption()
            if not worker.wait(3000):
                _log.warning("Connect worker for room %s did not stop in time", room_id)
                worker.terminate()
                worker.wait(1000)

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

    def save_rooms(self) -> int:
        """Persist the current room list atomically.

        Writes to a temporary file first, then renames it into place.
        Keeps a .bak copy of the previous config so load_rooms can recover
        from a corrupt primary file.

        Returns number of saved rooms.
        """
        import json

        data = {
            "version": 1,
            "saved_at": datetime.now().isoformat(timespec="seconds"),
            "rooms": [{"url": room.room_url} for room in self._rooms.values()],
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

    def _load_json_file(self, path: str) -> dict | None:
        """Load and parse a JSON config file. Returns None on any failure."""
        import json
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else None
        except Exception:
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
        for item in rooms:
            if not isinstance(item, dict):
                continue
            url = item.get("url", "").strip()
            if not url:
                continue
            if self.add_room(url) is not None:
                loaded += 1

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
        if room is None:
            return

        if success and info is not None:
            # Reuse the StreamInfo parsed in the worker thread — no second HTTP request.
            self._apply_stream_info(room, info)
        else:
            room.set_error(error or "连接失败")

        self.room_connect_finished.emit(room_id, success, error)

    def _apply_stream_info(self, room: RoomSession, info) -> bool:
        """Apply parsed StreamInfo to room session and controller."""
        room.apply_stream_info(info)
        room.preview_error = ""
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

    def disconnect_room(self, room_id: str) -> bool:
        room = self.get_room(room_id)
        if room is None:
            return False
        if room.preview_enabled:
            self.stop_preview(room_id)
        room.is_connected = False
        room.preview_error = ""
        return True

    # ── Preview ──────────────────────────────────────────────

    def get_active_preview_count(self) -> int:
        return sum(1 for r in self._rooms.values()
                   if r.preview_enabled and not r.preview_paused)

    def start_preview(self, room_id: str) -> bool:
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

        # Actually play the stream via mpv widget
        self._play_stream(room)
        return True

    def pause_preview(self, room_id: str) -> bool:
        room = self.get_room(room_id)
        if room is None or not room.preview_enabled:
            return False
        room.preview_paused = True

        widget = room.preview_widget
        if widget is not None:
            widget.pause()
        return True

    def resume_preview(self, room_id: str) -> bool:
        room = self.get_room(room_id)
        if room is None or not room.preview_enabled:
            return False
        room.preview_paused = False

        widget = room.preview_widget
        if widget is not None:
            widget.resume()
        return True

    def stop_preview(self, room_id: str) -> bool:
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
                except Exception:
                    pass
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
                except Exception:
                    pass
        return 0.0

    def align_previews_to_live(self) -> int:
        """Seek every active preview to its live edge (latest position).

        This gives users a one-click way to re-synchronise all multi-room
        previews to "now" after seeking backwards in one of them. Returns
        the number of previews that were aligned.
        """
        aligned = 0
        for room in list(self._rooms.values()):
            if not room.preview_enabled or room.preview_widget is None:
                continue
            duration = self.get_preview_duration(room.room_id)
            if duration <= 0:
                duration = float(getattr(room.controller, "total_sec", 0) or 0)
            if duration > 0:
                self.seek_preview(room.room_id, duration)
                aligned += 1
        return aligned

    # ── Range loop preview ─────────────────────────────────

    def start_range_loop(self, room_id: str, start: float, end: float) -> None:
        """循环播放 [start, end]，每 200ms 检查位置，越界则 seek 回 start。"""
        self.stop_range_loop()
        self._loop_room_id = room_id
        self._loop_start = start
        self._loop_end = end
        self.seek_preview(room_id, start)
        self._preview_loop_timer = QTimer(self)
        self._preview_loop_timer.setInterval(200)
        self._preview_loop_timer.timeout.connect(self._on_loop_tick)
        self._preview_loop_timer.start()

    def stop_range_loop(self) -> None:
        """停止选区循环播放。"""
        if self._preview_loop_timer is not None:
            self._preview_loop_timer.stop()
            self._preview_loop_timer = None
        self._loop_room_id = None

    def is_range_loop_active(self) -> bool:
        """返回当前是否正在循环试听。"""
        return self._preview_loop_timer is not None and self._preview_loop_timer.isActive()

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

        widget.play(stream_url)
        widget.set_muted(room.preview_muted)

    # ── Mute ─────────────────────────────────────────────────

    def mute_room(self, room_id: str, muted: bool) -> None:
        self.set_preview_muted(room_id, muted)

    # ── Recording ────────────────────────────────────────────

    def _refresh_room_stream_for_recording(self, room: RoomSession) -> bool:
        """Refresh short-lived CDN URLs before starting FFmpeg recording."""
        previous_quality = room.selected_quality
        try:
            info = parse_stream(room.room_url, force_refresh=True)
        except Exception as exc:
            room.last_error = f"刷新直播流失败: {exc}"
            return False

        if info.is_live and previous_quality and previous_quality in info.quality_urls:
            info.stream_url = info.quality_urls[previous_quality]
            info.selected_quality = previous_quality

        if not info.is_live or not info.stream_url:
            room.set_error(info.error or "刷新直播流失败")
            return False

        return self._apply_stream_info(room, info)

    def start_recording(self, room_id: str, output_dir: str, encoder: str, crf: int,
                        param_mode: str = "CRF 质量", bitrate: str | None = None,
                        bitrate_unit: str = "kbps") -> bool:
        room = self.get_room(room_id)
        controller = None if room is None else room.controller
        if room is None or controller is None:
            return False
        if not room.is_connected:
            room.last_error = "房间未连接"
            return False
        if not self._refresh_room_stream_for_recording(room):
            return False
        stream_url = controller.stream_url
        input_args = controller.input_args

        # 为每个房间创建独立子目录，避免多房间同时录制时文件名冲突导致覆盖
        import os
        room_output_dir = os.path.join(output_dir, room.room_id)
        os.makedirs(room_output_dir, exist_ok=True)

        ok, output_path, _encoder_used, error_msg = controller.start_recording_with_crf(
            stream_url,
            room_output_dir,
            encoder,
            crf,
            param_mode=param_mode,
            bitrate=bitrate,
            bitrate_unit=bitrate_unit,
            input_args=input_args or None,
        )
        room.is_recording = ok
        room.record_output_path = output_path
        room.record_started_at = datetime.now() if ok else None
        if not ok:
            room.last_error = error_msg or "录制启动失败"
        return ok

    def stop_recording(self, room_id: str) -> bool:
        room = self.get_room(room_id)
        controller = None if room is None else room.controller
        if room is None or controller is None:
            return False
        ok, _size_mb, output_path = controller.stop_recording()
        room.is_recording = False
        room.record_started_at = None
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

    def start_recording_all(self, output_dir: str, encoder: str, crf: int,
                            param_mode: str = "CRF 质量", bitrate: str | None = None,
                            bitrate_unit: str = "kbps") -> dict[str, bool]:
        rooms = self.list_rooms()
        # Pre-flight: ensure disk space scales with concurrent stream count.
        if rooms:
            from lsc.gui.pages.recording_controller import RecordingController
            preflight = RecordingController.preflight_recording(
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
        from lsc.gui.pages.recording_controller import RecordingController
        preflight = RecordingController.preflight_recording(
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
        # Keep a reference so the worker isn't garbage-collected mid-run.
        self._batch_record_worker = worker
        worker.start()
        return True

    def stop_recording_all(self) -> dict[str, bool]:
        return {r.room_id: self.stop_recording(r.room_id) for r in self.list_rooms()}

    # ── Export ───────────────────────────────────────────────

    def start_export(self, room_id: str, start_sec: float, end_sec: float,
                     output_dir: str, title: str = "",
                     on_done: Callable | None = None,
                     on_progress: Callable | None = None,
                     profile=None) -> bool:
        """Start async clip export for a room's recording.

        Parameters
        ----------
        on_progress : callable | None
            进度回调 ``callback(percent: float, elapsed: float, total: float)``。
        profile : ExportProfile | None
            编码配置。若为 None 则使用默认配置。
        """
        room = self.get_room(room_id)
        if room is None:
            return False
        controller = room.controller
        if controller is None:
            return False
        return controller.start_export(start_sec, end_sec, output_dir, title, on_done,
                                       profile=profile, on_progress=on_progress)

    # ── Cut ──────────────────────────────────────────────────

    def get_rooms_for_cut(self) -> list[RoomSession]:
        return [r for r in self.list_rooms() if r.include_in_cut]

    def get_total_recording_size_mb(self) -> float:
        """返回所有房间当前录制文件的总大小（MB）。"""
        return sum(r.record_size_mb for r in self._rooms.values() if r.is_recording)

    # ── Global heartbeat ─────────────────────────────────────

    def _start_global_timer(self) -> None:
        timer = self._ensure_global_timer()
        if timer is not None and not timer.isActive():
            timer.start()

    def _stop_global_timer(self) -> None:
        if self._global_timer is not None and self._global_timer.isActive():
            self._global_timer.stop()

    def _on_global_tick(self) -> None:
        """Single 1-second heartbeat for all rooms."""
        for room in list(self._rooms.values()):
            controller = room.controller
            if controller is None:
                continue
            # Update elapsed time for recording rooms
            if room.is_recording:
                controller.tick()
            # 录制文件大小追踪（任务 2.3）
            if room.is_recording and room.record_output_path:
                QThreadPool.globalInstance().start(SizeUpdateRunnable(room, room.record_output_path))
            # Sync playback position from the preview widget so the
            # timeline cursor follows the actual video without the UI
            # having to poll the widget on its own timer.
            if room.preview_enabled and not room.preview_paused:
                widget = room.preview_widget
                if widget is not None:
                    pos_fn = getattr(widget, "time_pos", None)
                    if callable(pos_fn):
                        try:
                            pos = float(pos_fn() or 0.0)
                            if pos > 0:
                                controller.current_sec = pos
                        except Exception:
                            pass
            # Watchdog: check FFmpeg health
            if room.is_recording:
                error_msg = controller.watchdog_check()
                if error_msg:
                    room.last_error = error_msg
                    _log.warning("Room %s watchdog: %s", room.room_id, error_msg)
        # Notify UI to refresh timelines and stats once per tick.
        self.global_tick.emit()
