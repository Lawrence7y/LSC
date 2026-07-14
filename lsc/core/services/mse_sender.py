"""MSE 发送器 — 每房间独立，背压控制。

职责：
- 每房间独立队列，硬上限 2 个媒体片且 2 MiB
- init/error/control 高优先级（直接发送，不排队）
- 拥塞时只丢最旧预览片
- 编码（base64）在出队时进行，减少入队时 CPU 开销
- 录制链路永不丢数据（录制数据不走此 sender）
"""
from __future__ import annotations

import base64
import threading
from collections import deque
from collections.abc import Callable


class MseSender:
    """每房间独立的 MSE 发送器，带背压控制。

    线程安全：所有方法通过 self._lock 保护。
    """

    MAX_QUEUED_SEGMENTS = 2
    MAX_QUEUED_BYTES = 2 * 1024 * 1024  # 2 MiB

    # 高优先级类型（直接发送，不排队）
    _PRIORITY_TYPES = frozenset({"init", "error", "control"})

    def __init__(self, room_id: str, broadcast_fn: Callable[[str, dict], None]) -> None:
        self.room_id = room_id
        self._broadcast = broadcast_fn
        self._queue: deque[tuple[str, bytes]] = deque()
        self._queued_bytes = 0
        self._dropped = 0
        self._lock = threading.Lock()

    @property
    def queue(self) -> deque[tuple[str, bytes]]:
        """只读访问队列（测试用）。"""
        return self._queue

    @property
    def queued_bytes(self) -> int:
        """当前队列字节数。"""
        return self._queued_bytes

    @property
    def dropped(self) -> int:
        """丢弃的段数。"""
        return self._dropped

    def push(self, kind: str, data: bytes) -> None:
        """推入数据。高优先级直接发送，媒体片排队。

        Args:
            kind: 消息类型（init/media/error/control）
            data: 原始字节数据（未编码）
        """
        if kind in self._PRIORITY_TYPES:
            self._do_send(kind, data)
            return

        with self._lock:
            # 背压检查：超出限制时丢最旧
            while (len(self._queue) >= self.MAX_QUEUED_SEGMENTS or
                   self._queued_bytes + len(data) > self.MAX_QUEUED_BYTES):
                if not self._queue:
                    break
                _, old = self._queue.popleft()
                self._queued_bytes -= len(old)
                self._dropped += 1

            self._queue.append((kind, data))
            self._queued_bytes += len(data)

    def drain(self) -> None:
        """排空队列，发送所有排队的数据。

        编码（base64）在出队时进行。
        """
        while True:
            with self._lock:
                if not self._queue:
                    return
                kind, data = self._queue.popleft()
                self._queued_bytes -= len(data)
            self._do_send(kind, data)

    def _do_send(self, kind: str, data: bytes) -> None:
        """实际发送 — 编码在出队时进行。"""
        encoded = base64.b64encode(data).decode('ascii')
        self._broadcast(f'mse_{kind}', {
            'room_id': self.room_id,
            'data': encoded,
        })
