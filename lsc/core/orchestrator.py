from __future__ import annotations

import logging
import queue
import threading
import time
import traceback
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from lsc.core.events import EventBus

_log = logging.getLogger(__name__)

ControllerFactory = Callable[[], object]
PreviewFactory = Callable[[], object]

MAX_ROOMS = 12
MAX_CONCURRENT_PREVIEWS = 4
_TICK_INTERVAL_MS = 3000
_HIGH_FREQ_INTERVAL = 1
_MEDIUM_FREQ_INTERVAL = 1
_LOW_FREQ_INTERVAL = 4
_STAGGER_GROUPS = 3


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
