# lsc/core/events.py
from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

_log = logging.getLogger(__name__)

# 事件名与 MultiRoomManager Signal 一一对应
EVENT_ROOM_CONNECT_FINISHED = "room_connect_finished"
EVENT_BATCH_RECORD_PROGRESS = "batch_record_progress"
EVENT_BATCH_RECORD_FINISHED = "batch_record_finished"
EVENT_RECORDING_STOPPED = "recording_stopped"
EVENT_GLOBAL_TICK = "global_tick"
EVENT_MEDIUM_TICK = "medium_tick"
EVENT_LOW_TICK = "low_tick"


class EventBus:
    """同步事件总线。emit 仅允许在编排线程调用（bind 后强制检查）。"""

    def __init__(self) -> None:
        self._subs: dict[str, list[Callable[..., Any]]] = {}
        self._lock = threading.RLock()
        self._emitter_thread: threading.Thread | None = None

    def bind_emitter_thread(self, thread: threading.Thread) -> None:
        self._emitter_thread = thread

    def subscribe(self, event: str, callback: Callable[..., Any]) -> None:
        with self._lock:
            self._subs.setdefault(event, []).append(callback)

    def unsubscribe(self, event: str, callback: Callable[..., Any]) -> None:
        with self._lock:
            cbs = self._subs.get(event)
            if not cbs:
                return
            try:
                cbs.remove(callback)
            except ValueError:
                return

    def emit(self, event: str, *args: Any) -> None:
        if self._emitter_thread is not None and threading.current_thread() is not self._emitter_thread:
            raise RuntimeError(
                f"EventBus.emit({event!r}) must run on orchestrator thread"
            )
        with self._lock:
            callbacks = list(self._subs.get(event, ()))
        for cb in callbacks:
            try:
                cb(*args)
            except Exception:
                _log.exception("EventBus subscriber failed for %s", event)
