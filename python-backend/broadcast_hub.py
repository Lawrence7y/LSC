"""纯 Python 广播队列：订阅 RoomOrchestrator 事件并推送给 WebSocket。

无 Qt / QObject / Signal；跨线程写调用由 RoomOrchestrator.call 承担。
"""
from __future__ import annotations

import logging
import queue
import threading
from typing import Any

from lsc.platforms.redaction import redact_mapping, redact_text

_log = logging.getLogger(__name__)

_TERMINAL_TYPES = frozenset({
    "clip_completed", "clip_failed",
    "recording_stopped", "recording_started",
    "reconnect_failed", "continuous_highlights",
})
# continuous_analysis_status 不列入：phase 迁移（stopping/idle）属关键状态，
# 丢弃会导致前端按钮状态与后端任务槽脱节（启动被拒/「点两次」）。
# drain 端对该类型做 last-value coalesce，不丢也不会积压。
_DROPPABLE_TYPES = frozenset({
    "rooms_updated", "mse_segment", "export_progress", "analysis_progress",
    "system_stats",
})


class BroadcastHub:
    """纯 Python 广播队列 + orchestrator 事件订阅。"""

    def __init__(self, orchestrator: Any):
        self._orch = orchestrator
        # 兼容旧代码 bridge.manager 读法（register_room_handlers 等）
        self.manager = orchestrator
        self._broadcast_queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1000)
        self._wake = threading.Event()
        self._seq = 0  # 广播序列号，前端用于检测丢消息
        self._async_loop: Any = None
        self._async_wake: Any = None
        self._subscribe_orchestrator_events()
        _log.info("BroadcastHub initialized (broadcast_queue maxsize=1000)")

    def _subscribe_orchestrator_events(self) -> None:
        bus = self._orch.bus
        bus.subscribe("room_connect_finished", self._on_connect_finished)
        bus.subscribe("batch_record_progress", self._on_batch_record_progress)
        bus.subscribe("recording_stopped", self._on_recording_stopped)
        bus.subscribe("runtime_event", self._on_runtime_event)

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

    def _on_connect_finished(self, room_id: str, success: bool, error: str) -> None:
        """房间连接完成，将结果推入广播队列。"""
        safe_error = redact_text(error)
        _log.debug("room_connect_finished: room_id=%s success=%s error=%s", room_id, success, safe_error)
        self.queue_broadcast({
            'type': 'room_connect_finished',
            'data': {'room_id': room_id, 'success': success, 'error': safe_error},
        })

    def _on_batch_record_progress(self, room_id: str, success: bool) -> None:
        """批量录制每个房间启动完成时广播，前端据此刷新房间卡片状态。

        success 为 True 表示该房间录制启动成功，False 表示失败。
        """
        _log.debug("batch_record_progress: room_id=%s success=%s", room_id, success)
        self.queue_broadcast({
            'type': 'recording_started',
            'data': {'room_id': room_id, 'success': success, 'error': ''},
        })

    def _on_recording_stopped(self, room_id: str, reason: str, message: str) -> None:
        """录制停止（含磁盘满、断流等），前端据此更新状态并强提示。"""
        safe_reason = redact_text(reason)
        safe_message = redact_text(message)
        _log.debug("recording_stopped: room_id=%s reason=%s", room_id, safe_reason)
        self.queue_broadcast({
            'type': 'recording_stopped',
            'data': {'room_id': room_id, 'reason': safe_reason, 'message': safe_message},
        })

    def _on_runtime_event(self, event: Any) -> None:
        payload = event.to_dict() if hasattr(event, "to_dict") else dict(event or {})
        # Runtime events can originate from compatibility plugins that return
        # a plain mapping rather than IngestEvent. Apply the same final public
        # redaction boundary in both cases before the payload enters the WS
        # queue.
        safe = redact_mapping(payload)
        _log.info(
            "runtime_event room=%s type=%s component=%s %s->%s failure=%s",
            safe.get("room_id", ""),
            safe.get("event_type", ""),
            safe.get("component", ""),
            safe.get("state_from", ""),
            safe.get("state_to") or safe.get("state", ""),
            safe.get("failure_kind", ""),
        )
        self.queue_broadcast({"type": "runtime_event", "data": safe})

    def get_broadcast(self, block: bool = False, timeout: float | None = None) -> dict[str, Any] | None:
        """从广播队列获取一条待发送的消息。"""
        try:
            return self._broadcast_queue.get(block=block, timeout=timeout)
        except queue.Empty:
            return None

    def queue_broadcast(self, msg: dict[str, Any]) -> None:
        """线程安全地投递一条广播消息到队列，供 WebSocket 线程消费。"""
        msg_type = msg.get('type')
        # 附加序列号，前端用于检测丢消息
        self._seq += 1
        msg['_seq'] = self._seq
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
        # 限制最大队列大小为 5000，防止无界增长导致 OOM
        new_max = min(old_max + max(old_max // 4, 100), 5000)
        new_queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=new_max)
        dropped = 0
        while True:
            try:
                new_queue.put_nowait(old_queue.get_nowait())
            except queue.Empty:
                break
            except queue.Full:
                # 新队列已满，丢弃最旧的消息
                try:
                    old_queue.get_nowait()
                    dropped += 1
                except queue.Empty:
                    break
        self._broadcast_queue = new_queue
        _log.error("broadcast queue expanded: maxsize %d -> %d (dropped %d)", old_max, new_max, dropped)
