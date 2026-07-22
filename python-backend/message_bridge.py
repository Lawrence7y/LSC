"""线程安全桥接：连接 WebSocket handler 线程与 Qt 主线程。

通过 Qt 信号槽机制将 handler 的调用请求转发到主线程执行，
并维护线程安全的广播队列，供主线程向 WebSocket 客户端推送状态更新。
"""
from __future__ import annotations

import logging
import queue
import threading
import traceback
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, Signal

_log = logging.getLogger(__name__)

_TERMINAL_TYPES = frozenset({
    "clip_completed", "clip_failed",
    "recording_stopped", "recording_started",
    "reconnect_failed", "continuous_highlights",
})
_DROPPABLE_TYPES = frozenset({
    "rooms_updated", "mse_segment", "export_progress", "analysis_progress",
    "continuous_analysis_status", "system_stats",
})


class _CallRequest:
    """一次跨线程函数调用的请求封装。"""

    def __init__(self, fn: Callable, args: tuple, kwargs: dict):
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.result: Any = None
        self.exception: BaseException | None = None
        self.traceback: str | None = None
        self.event = threading.Event()
        self.cancelled = False


class QtManagerBridge(QObject):
    """运行在主线程（Qt 事件循环线程）。"""

    # 内部信号：用于在 Qt 线程中执行外部提交的函数
    _execute = Signal(object)
    # 最大待执行请求数，防止 timed-out 但仍在 Qt 线程执行的长任务堆积（#21）
    _MAX_PENDING_REQUESTS = 8

    def __init__(self, manager: Any):
        """初始化桥接器，绑定 manager 信号到广播方法。"""
        super().__init__()
        self._manager = manager
        self._execute.connect(self._on_execute)
        self._pending_count = 0
        self._pending_lock = threading.Lock()

        # 连接 manager 信号 -> 广播到 WebSocket 客户端
        # 注意：MultiRoomManager 只有 room_connect_finished / batch_record_progress /
        # batch_record_finished / global_tick 信号，没有 recording_started/recording_stopped。
        manager.room_connect_finished.connect(self._on_connect_finished)
        manager.batch_record_progress.connect(self._on_batch_record_progress)
        manager.recording_stopped.connect(self._on_recording_stopped)

        self._broadcast_queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1000)
        # 事件驱动唤醒：queue_broadcast 后通知 asyncio broadcaster，避免 100ms 空转轮询
        self._wake = threading.Event()
        self._async_loop: asyncio.AbstractEventLoop | None = None
        self._async_wake: Any = None  # asyncio.Event，由 WS 线程 bind
        _log.info("QtManagerBridge initialized (broadcast_queue maxsize=1000)")

    def bind_async_wake(self, loop: Any, event: Any) -> None:
        """绑定 asyncio 事件循环与 Event，供 queue_broadcast 跨线程唤醒。"""
        self._async_loop = loop
        self._async_wake = event

    def notify_broadcast(self) -> None:
        """唤醒等待中的 broadcaster（线程安全）。"""
        self._wake.set()
        loop = self._async_loop
        event = self._async_wake
        if loop is None or event is None:
            return
        try:
            loop.call_soon_threadsafe(event.set)
        except RuntimeError:
            _log.debug("notify_broadcast: event loop closed")

    def _on_execute(self, req: _CallRequest):
        """在 Qt 主线程执行请求函数并设置结果/异常。"""
        if req.cancelled:
            with self._pending_lock:
                self._pending_count -= 1
            return
        try:
            req.result = req.fn(*req.args, **req.kwargs)
            _log.debug("executed %s successfully", getattr(req.fn, '__name__', '?'))
        except Exception as exc:
            req.exception = exc
            req.traceback = traceback.format_exc()
            _log.error("executed %s raised %s", getattr(req.fn, '__name__', '?'), exc, exc_info=True)
        finally:
            with self._pending_lock:
                self._pending_count -= 1
            req.event.set()

    def _on_connect_finished(self, room_id: str, success: bool, error: str):
        """房间连接完成，将结果推入广播队列。"""
        _log.debug("room_connect_finished: room_id=%s success=%s error=%s", room_id, success, error)
        self.queue_broadcast({
            'type': 'room_connect_finished',
            'data': {'room_id': room_id, 'success': success, 'error': error},
        })

    def _on_batch_record_progress(self, room_id: str, success: bool):
        """批量录制每个房间启动完成时广播，前端据此刷新房间卡片状态。

        success 为 True 表示该房间录制启动成功，False 表示失败。
        """
        _log.debug("batch_record_progress: room_id=%s success=%s", room_id, success)
        self.queue_broadcast({
            'type': 'recording_started',
            'data': {'room_id': room_id, 'success': success, 'error': ''},
        })

    def _on_recording_stopped(self, room_id: str, reason: str, message: str):
        """录制停止（含磁盘满、断流等），前端据此更新状态并强提示。"""
        _log.debug("recording_stopped: room_id=%s reason=%s", room_id, reason)
        self.queue_broadcast({
            'type': 'recording_stopped',
            'data': {'room_id': room_id, 'reason': reason, 'message': message},
        })

    def call(self, fn: Callable, *args, timeout: float = 10.0, **kwargs) -> Any:
        """从 WebSocket handler 线程调用 Qt 主线程中的函数并等待结果。"""
        if threading.current_thread() is threading.main_thread():
            _log.debug("call on main thread, executing directly")
            return fn(*args, **kwargs)

        # #21 待执行上限保护：防止 timed-out 长任务堆积阻塞 Qt 线程
        with self._pending_lock:
            if self._pending_count >= self._MAX_PENDING_REQUESTS:
                _log.error(
                    "bridge.call rejected: pending=%d >= max=%d, fn=%s, timeout=%.1fs",
                    self._pending_count, self._MAX_PENDING_REQUESTS,
                    getattr(fn, '__name__', '?'), timeout,
                )
                raise TimeoutError(
                    f'Qt manager too busy ({self._pending_count} pending, '
                    f'max {self._MAX_PENDING_REQUESTS})'
                )
            self._pending_count += 1

        req = _CallRequest(fn, args, kwargs)
        self._execute.emit(req)
        if not req.event.wait(timeout=timeout):
            req.cancelled = True
            _log.warning(
                "bridge.call timed out after %.1fs but Qt thread still executing: %s, pending=%d",
                timeout, getattr(fn, '__name__', '?'), self._pending_count,
            )
            raise TimeoutError('Qt manager call timed out')
        if req.exception is not None:
            # 显式打印完整 traceback，便于调试（__traceback__ 技术上保留但日志不可见）
            if req.traceback:
                print(req.traceback)
            raise req.exception
        return req.result

    def submit(self, fn: Callable, *args, **kwargs) -> None:
        """Fire-and-forget 提交：发射信号到主线程执行，不等待结果，不抛异常。"""
        if threading.current_thread() is threading.main_thread():
            try:
                fn(*args, **kwargs)
            except Exception:
                _log.error("submit on main thread raised %s", getattr(fn, '__name__', '?'), exc_info=True)
            return

        req = _CallRequest(fn, args, kwargs)
        self._execute.emit(req)
        _log.debug("submit fire-and-forget: %s", getattr(fn, '__name__', '?'))

    def get_broadcast(self, block: bool = False, timeout: float | None = None) -> dict[str, Any] | None:
        """从广播队列获取一条待发送的消息。"""
        try:
            return self._broadcast_queue.get(block=block, timeout=timeout)
        except queue.Empty:
            return None

    def queue_broadcast(self, msg: dict[str, Any]) -> None:
        """线程安全地投递一条广播消息到队列，供 WebSocket 线程消费。"""
        msg_type = msg.get('type')
        try:
            self._broadcast_queue.put_nowait(msg)
            _log.debug("queued broadcast: type=%s", msg_type or '?')
            self.notify_broadcast()
            return
        except queue.Full:
            pass

        if msg_type in _DROPPABLE_TYPES:
            _log.debug(
                "broadcast queue full, dropping droppable message: type=%s",
                msg_type or '?',
            )
            return

        self._enqueue_preserving_terminal(msg)

    def _enqueue_preserving_terminal(self, msg: dict[str, Any]) -> None:
        """Evict droppable messages to make room; never discard terminal types."""
        msg_type = msg.get('type')
        survivors: list[dict[str, Any]] = []
        while True:
            try:
                item = self._broadcast_queue.get_nowait()
            except queue.Empty:
                break
            if item.get('type') not in _DROPPABLE_TYPES:
                survivors.append(item)

        for item in survivors:
            self._broadcast_queue.put_nowait(item)

        try:
            self._broadcast_queue.put_nowait(msg)
            _log.debug("queued broadcast after eviction: type=%s", msg_type or '?')
            self.notify_broadcast()
            return
        except queue.Full:
            pass

        self._expand_broadcast_queue()
        try:
            self._broadcast_queue.put(msg, block=True, timeout=5.0)
            _log.error(
                "broadcast queue expanded and blocked to enqueue critical message: type=%s",
                msg_type or '?',
            )
            self.notify_broadcast()
        except queue.Full:
            _log.error(
                "broadcast queue still full after expansion, terminal message may be delayed: type=%s",
                msg_type or '?',
            )

    def _expand_broadcast_queue(self) -> None:
        """Replace the broadcast queue with a larger one, preserving queued items."""
        old_queue = self._broadcast_queue
        old_max = old_queue.maxsize
        new_max = old_max + max(old_max // 4, 100)
        new_queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=new_max)
        while True:
            try:
                new_queue.put_nowait(old_queue.get_nowait())
            except queue.Empty:
                break
            except queue.Full:
                break
        self._broadcast_queue = new_queue
        _log.error("broadcast queue expanded: maxsize %d -> %d", old_max, new_max)

    @property
    def manager(self) -> Any:
        """返回绑定的 MultiRoomManager 实例。"""
        return self._manager
