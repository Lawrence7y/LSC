"""Multi-room manager — thin Qt shell over RoomOrchestrator.

.. deprecated::
    PySide6 原生 GUI 已弃用，Electron 为唯一前端。
    此类保留供历史参考，不再维护。新代码请使用 lsc.core.orchestrator.RoomOrchestrator。
"""
from __future__ import annotations

import logging
import time as _time
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QCoreApplication, QObject, Signal  # noqa: F401  # kept for backward compat

from lsc.config import ExportProfile, load_config
from lsc.core.orchestrator import (
    _HIGH_FREQ_INTERVAL,
    _LOW_FREQ_INTERVAL,
    _MEDIUM_FREQ_INTERVAL,
    _MIN_FREE_BYTES_WHILE_RECORDING,
    _SHARED_INGEST_STALL_CHECKS,
    _STAGGER_GROUPS,
    _TICK_INTERVAL_MS,
    MAX_CONCURRENT_PREVIEWS,
    MAX_ROOMS,
    RoomOrchestrator,
    _is_stream_offline_error,
    _offline_stream_error_message,
)
from lsc.core.session import RoomSession  # noqa: F401  # re-export for backward compat
from lsc.platforms.registry import parse_stream, select_quality

_log = logging.getLogger(__name__)

ControllerFactory = Callable[[], object]
PreviewFactory = Callable[[], object]

__all__ = [
    "MultiRoomManager",
    "MAX_ROOMS",
    "MAX_CONCURRENT_PREVIEWS",
    "_is_stream_offline_error",
    "RoomSession",
]

# Re-exports kept for monkeypatch / external import compatibility.
_ = (
    load_config, parse_stream, select_quality, ExportProfile, _time,
    _HIGH_FREQ_INTERVAL, _LOW_FREQ_INTERVAL, _MEDIUM_FREQ_INTERVAL,
    _MIN_FREE_BYTES_WHILE_RECORDING, _SHARED_INGEST_STALL_CHECKS,
    _STAGGER_GROUPS, _TICK_INTERVAL_MS, _offline_stream_error_message,
)

_DELEGATE_METHODS = (
    "save_rooms", "flush_save_rooms", "load_rooms", "connect_room", "disconnect_room",
    "get_active_preview_count", "start_preview", "play_preview_stream", "pause_preview",
    "resume_preview", "stop_preview", "set_preview_muted", "seek_preview",
    "get_preview_position", "get_preview_duration", "align_previews_to_live",
    "start_range_loop", "stop_range_loop", "is_range_loop_active", "seek_selected_previews",
    "refresh_stream_url", "refresh_stream_url_async", "mute_room", "start_recording",
    "stop_recording", "stop_recording_async", "start_recording_all",
    "start_recording_all_async", "stop_recording_all", "start_export", "cancel_export",
    "get_rooms_for_cut", "get_total_recording_size_mb",
)


def _safe_emit(sig: Signal, *args: Any) -> None:
    try:
        if QCoreApplication.instance() is None:
            return
        sig.emit(*args)
    except RuntimeError:
        _log.debug("signal emit skipped (no receiver / no app)")


def _make_delegate(name: str):
    def _fn(self: MultiRoomManager, *args: Any, **kwargs: Any) -> Any:
        return getattr(self._orch, name)(*args, **kwargs)
    _fn.__name__ = name
    _fn.__qualname__ = f"MultiRoomManager.{name}"
    return _fn


class MultiRoomManager(QObject):
    """Thin Qt façade: Signals + delegation to RoomOrchestrator."""

    room_connect_finished = Signal(str, bool, str)
    batch_record_progress = Signal(str, bool)
    batch_record_finished = Signal(int, int)
    recording_stopped = Signal(str, str, str)
    global_tick = Signal()
    medium_tick = Signal()
    low_tick = Signal()

    def __init__(
        self,
        controller_factory: ControllerFactory | None = None,
        preview_factory: PreviewFactory | None = None,
    ) -> None:
        super().__init__()
        self._orch = RoomOrchestrator(
            controller_factory=controller_factory,
            preview_factory=preview_factory,
        )
        self._orch.start()
        self._lock = self._orch._lock
        self._rooms = self._orch._rooms
        self._connect_workers: dict[str, Any] = {}
        self._metadata_probe_workers: dict[str, Any] = {}
        self._batch_record_worker: Any | None = None
        self._shut_down = False
        self._wire_bus()

    def _wire_bus(self) -> None:
        b = self._orch.bus
        b.subscribe("room_connect_finished", lambda *a: _safe_emit(self.room_connect_finished, *a))
        b.subscribe("batch_record_progress", lambda *a: _safe_emit(self.batch_record_progress, *a))
        b.subscribe("batch_record_finished", lambda *a: _safe_emit(self.batch_record_finished, *a))
        b.subscribe("recording_stopped", lambda *a: _safe_emit(self.recording_stopped, *a))
        b.subscribe("global_tick", lambda: _safe_emit(self.global_tick))
        b.subscribe("medium_tick", lambda: _safe_emit(self.medium_tick))
        b.subscribe("low_tick", lambda: _safe_emit(self.low_tick))

    @property
    def _tick_counter(self) -> int:
        return self._orch._tick_counter

    @_tick_counter.setter
    def _tick_counter(self, value: int) -> None:
        self._orch._tick_counter = int(value)

    def _orch_alive(self) -> bool:
        if self._shut_down:
            return False
        t = self._orch._thread
        return t is not None and t.is_alive()

    def add_room(self, url: str):
        return None if not self._orch_alive() else self._orch.add_room(url)

    def get_room(self, room_id: str):
        if not self._orch_alive():
            with self._lock:
                return self._rooms.get(room_id)
        return self._orch.get_room(room_id)

    def list_rooms(self):
        if not self._orch_alive():
            with self._lock:
                return list(self._rooms.values())
        return self._orch.list_rooms()

    def room_count(self) -> int:
        if not self._orch_alive():
            with self._lock:
                return len(self._rooms)
        return self._orch.room_count()

    def max_rooms(self) -> int:
        return MAX_ROOMS

    def remove_room(self, room_id: str) -> bool:
        return False if not self._orch_alive() else self._orch.remove_room(room_id)

    def _on_global_tick(self) -> None:
        return self._orch.call(self._orch._on_global_tick)

    def _attempt_recording_reconnect(self, room: RoomSession, error_msg: str) -> None:
        return self._orch.call(self._orch._attempt_recording_reconnect, room, error_msg)

    def _refresh_room_stream_for_recording(self, room: RoomSession) -> bool:
        return self._orch.call(self._orch._refresh_room_stream_for_recording, room)

    def _stop_legacy_workers(self, timeout_sec: float) -> int:
        """Cancel Qt-style fake workers injected by stock shutdown tests."""
        timeout_ms = max(0, int(timeout_sec * 1000))
        cancelled = 0

        def _stop(worker: object | None) -> bool:
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

        for w in list(self._connect_workers.values()):
            if _stop(w):
                cancelled += 1
        self._connect_workers.clear()
        for w in list(self._metadata_probe_workers.values()):
            if _stop(w):
                cancelled += 1
        self._metadata_probe_workers.clear()
        if _stop(self._batch_record_worker):
            cancelled += 1
        self._batch_record_worker = None
        return cancelled

    def shutdown(self, timeout_sec: float = 10.0) -> dict[str, int]:
        empty = {
            "rooms": 0, "recordings_stopped": 0, "previews_stopped": 0,
            "workers_cancelled": 0, "controllers_cleaned": 0, "previews_cleaned": 0,
        }
        legacy = self._stop_legacy_workers(timeout_sec)
        if self._shut_down or not self._orch_alive():
            self._shut_down = True
            empty["workers_cancelled"] = legacy
            return empty
        stats = self._orch.shutdown(timeout_sec=timeout_sec)
        self._shut_down = True
        if legacy:
            stats = dict(stats)
            stats["workers_cancelled"] = int(stats.get("workers_cancelled", 0)) + legacy
        return stats


for _name in _DELEGATE_METHODS:
    setattr(MultiRoomManager, _name, _make_delegate(_name))
