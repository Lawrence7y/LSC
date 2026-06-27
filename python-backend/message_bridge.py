"""线程安全的桥接模块：让 WebSocket handler 线程调用 Qt 主线程中的 MultiRoomManager。"""
from __future__ import annotations

import queue
import threading
import time
import traceback
from typing import Any, Callable

from PySide6.QtCore import QObject, Signal, QCoreApplication


class _CallRequest:
    def __init__(self, fn: Callable, args: tuple, kwargs: dict):
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.result: Any = None
        self.exception: BaseException | None = None
        self.traceback: str | None = None
        self.event = threading.Event()


class QtManagerBridge(QObject):
    """运行在主线程（Qt 事件循环线程）。"""

    # 内部信号：用于在 Qt 线程中执行外部提交的函数
    _execute = Signal(object)

    def __init__(self, manager: Any):
        super().__init__()
        self._manager = manager
        self._execute.connect(self._on_execute)

        # 连接 manager 信号 -> 广播到 WebSocket 客户端
        # 注意：MultiRoomManager 只有 room_connect_finished / batch_record_progress /
        # batch_record_finished / global_tick 信号，没有 recording_started/recording_stopped。
        manager.room_connect_finished.connect(self._on_connect_finished)
        manager.batch_record_progress.connect(self._on_batch_record_progress)

        self._broadcast_queue: queue.Queue[dict[str, Any]] = queue.Queue()

    def _on_execute(self, req: _CallRequest):
        try:
            req.result = req.fn(*req.args, **req.kwargs)
        except BaseException as exc:
            req.exception = exc
            req.traceback = traceback.format_exc()
        finally:
            req.event.set()

    def _on_connect_finished(self, room_id: str, success: bool, error: str):
        self._broadcast_queue.put({
            'type': 'room_connect_finished',
            'data': {'room_id': room_id, 'success': success, 'error': error},
        })

    def _on_batch_record_progress(self, room_id: str, success: bool):
        """批量录制每个房间启动完成时广播，前端据此刷新房间卡片状态。

        success 为 True 表示该房间录制启动成功，False 表示失败。
        """
        self._broadcast_queue.put({
            'type': 'recording_started',
            'data': {'room_id': room_id, 'success': success, 'error': ''},
        })

    def call(self, fn: Callable, *args, timeout: float = 10.0, **kwargs) -> Any:
        """从 WebSocket handler 线程调用 Qt 主线程中的函数并等待结果。"""
        if threading.current_thread() is threading.main_thread():
            return fn(*args, **kwargs)

        req = _CallRequest(fn, args, kwargs)
        self._execute.emit(req)
        if not req.event.wait(timeout=timeout):
            raise TimeoutError('Qt manager call timed out')
        if req.exception is not None:
            # 显式打印完整 traceback，便于调试（__traceback__ 技术上保留但日志不可见）
            if req.traceback:
                print(req.traceback)
            raise req.exception
        return req.result

    def get_broadcast(self, block: bool = False, timeout: float | None = None) -> dict[str, Any] | None:
        try:
            return self._broadcast_queue.get(block=block, timeout=timeout)
        except queue.Empty:
            return None

    def queue_broadcast(self, msg: dict[str, Any]) -> None:
        """线程安全地投递一条广播消息到队列，供 WebSocket 线程消费。

        供 Qt 主线程（信号槽回调）等无法直接访问 asyncio 事件循环的场景使用，
        确保异步操作完成后房间状态能及时同步到前端。
        """
        self._broadcast_queue.put(msg)

    @property
    def manager(self) -> Any:
        return self._manager
