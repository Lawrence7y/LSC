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
        _log.info("QtManagerBridge initialized (broadcast_queue maxsize=1000)")

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
        while True:
            try:
                self._broadcast_queue.put_nowait(msg)
                _log.debug("queued broadcast: type=%s", msg_type or '?')
                break
            except queue.Full:
                _log.warning("broadcast queue full, dropping oldest message")
                try:
                    self._broadcast_queue.get_nowait()
                except queue.Empty:
                    break

    @property
    def manager(self) -> Any:
        """返回绑定的 MultiRoomManager 实例。"""
        return self._manager
