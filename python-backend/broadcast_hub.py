"""纯 Python 广播队列：订阅 RoomOrchestrator 事件并推送给 WebSocket。

无 Qt / QObject / Signal；跨线程写调用由 RoomOrchestrator.call 承担。
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Any

from lsc.platforms.diagnostics import note_degradation
from lsc.platforms.redaction import redact_mapping, redact_text

_log = logging.getLogger(__name__)

# 降级计数走平台层同一个注册表（`lsc/platforms/diagnostics`）：键空间固定为
# ("backend", kind)，与解析器过期 / Cookie 失效等一起在 `system_stats` 里可见。
_DEGRADATION_KEY = "backend"

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
        # 序列号的分配必须与入队**同一把锁**：生产者是 orchestrator 线程 + 一批
        # executor 线程，原先 `self._seq += 1` 与 `put_nowait` 之间没有任何同步，
        # 于是队列顺序可以与序列号顺序不一致 —— 前端 `useWebSocket` 只在
        # `_seq > last + 1` 时报缺号，倒序到达既不报警、还会把 last 拉低从而
        # **掩盖真正的缺口**。这条防线当时是假信号（见债务报告 §7.4 / §11.17）。
        self._seq_lock = threading.Lock()
        self._dropped_droppable = 0
        self._terminal_drops = 0
        self._wake_failures = 0
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
            # 唤醒失败 = 消息已在队列里但没人会被叫醒去取。原先只 `_log.debug`，
            # 生产日志里根本看不见（debug 被过滤），表现就是"前端卡在旧状态"。
            # 计数 + 首次 WARN：饱和/退出阶段的重复失败不再刷屏，但一定留痕。
            self._wake_failures += 1
            if note_degradation(
                _DEGRADATION_KEY, "broadcast_wake_lost", reason="event_loop_closed"
            ):
                _log.warning(
                    "broadcast wake lost: asyncio loop closed; queued broadcasts will "
                    "only be delivered on the next successful wake (further repeats counted)"
                )
        except Exception:  # noqa: BLE001 — 唤醒失败不能把异常抛回生产者（executor 线程会直接死亡）
            self._wake_failures += 1
            _log.warning("broadcast wake raised unexpectedly", exc_info=True)

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

    def _offer_locked(self, msg: dict[str, Any], *, block: bool = False, timeout: float | None = None) -> bool:
        """**持锁**分配序列号并入队；返回是否成功入队。

        两条不变式，缺一条前端的丢消息判定就不成立：
        ① 只给非 `_DROPPABLE_TYPES` 的消息发号 —— 遥测类被主动丢弃是设计行为，
           不该在"关键消息缺号"这条告警里制造假缺口；
        ② 队列内顺序 == 序列号顺序 —— 所以发号和 `put` 必须在同一次持锁里完成。
        入队失败时**不提交**号段（不留假缺口）。
        """
        critical = msg.get('type') not in _DROPPABLE_TYPES
        nxt = self._seq + 1
        if critical:
            msg['_seq'] = nxt
        try:
            if block:
                self._broadcast_queue.put(msg, block=True, timeout=timeout)
            else:
                self._broadcast_queue.put_nowait(msg)
        except queue.Full:
            msg.pop('_seq', None)
            return False
        if critical:
            self._seq = nxt
        return True

    def _offer(self, msg: dict[str, Any]) -> bool:
        """`_offer_locked` 的加锁入口（成功时顺带唤醒 broadcaster）。"""
        with self._seq_lock:
            ok = self._offer_locked(msg)
        if ok:
            self.notify_broadcast()
        return ok

    def queue_broadcast(self, msg: dict[str, Any]) -> None:
        """线程安全地投递一条广播消息到队列，供 WebSocket 线程消费。"""
        msg_type = msg.get('type')
        if self._offer(msg):
            _log.debug("queued broadcast: type=%s", msg_type or '?')
            return

        if msg_type in _DROPPABLE_TYPES:
            self._dropped_droppable += 1
            if note_degradation(
                _DEGRADATION_KEY, "broadcast_queue_full_drop", reason=str(msg_type or '?')
            ):
                _log.warning(
                    "broadcast queue full, dropping droppable message type=%s "
                    "(关键消息不会走这条路径；重复丢弃只计数)",
                    msg_type or '?',
                )
            return

        self._enqueue_preserving_terminal(msg)

    def broadcast_health(self) -> dict[str, Any]:
        """广播通道自检快照（进 `system_stats.extra`，现场复盘用）。"""
        with self._seq_lock:
            return {
                'seq': self._seq,
                'queued': self._broadcast_queue.qsize(),
                'queue_maxsize': self._broadcast_queue.maxsize,
                'dropped_droppable': self._dropped_droppable,
                'terminal_dropped': self._terminal_drops,
                'wake_failures': self._wake_failures,
            }

    def _enqueue_preserving_terminal(self, msg: dict[str, Any]) -> None:
        """Evict droppable messages to make room; never discard terminal types."""
        msg_type = msg.get('type')
        # 排空→重放→入队整段持锁：否则别的生产者会插进来，把刚建立的
        # "队列顺序 == 序列号顺序"再打乱（`_offer_locked` 的不变式 ②）。
        with self._seq_lock:
            survivors: list[dict[str, Any]] = []
            while True:
                try:
                    item = self._broadcast_queue.get_nowait()
                except queue.Empty:
                    break
                if item.get('type') not in _DROPPABLE_TYPES:
                    survivors.append(item)

            for item in survivors:
                try:
                    self._broadcast_queue.put_nowait(item)
                except queue.Full:
                    # 放不下的幸存者直接丢弃并记日志，绝不能让异常沿
                    # queue_broadcast 逃逸杀死调用线程（executor 线程会直接死亡）。
                    _log.warning("broadcast queue refill race, dropping survivor: type=%s", item.get('type'))
                    break

            if self._offer_locked(msg):
                queued = True
            else:
                queued = False
        if queued:
            _log.debug("queued broadcast after eviction: type=%s", msg_type or '?')
            self.notify_broadcast()
            return

        # 饱和慢路径：**不**长时间持序列号锁（否则一个线程等 5s 会把所有生产者
        # 一起堵死）。先扩容，再有界重试非阻塞入队；始终进不去时做一次不带号
        # 的阻塞入队 —— 宁可这条不参与前端的缺号判定（无 `_seq` 即被跳过），
        # 也不要像原实现那样在 `put` 超时后把关键消息整条丢掉。
        self._expand_broadcast_queue()
        _log.error(
            "broadcast queue saturated, retrying critical message enqueue: type=%s",
            msg_type or '?',
        )
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if self._offer(msg):
                return
            time.sleep(0.05)
        msg.pop('_seq', None)
        try:
            self._broadcast_queue.put(msg, block=True, timeout=5.0)
            self.notify_broadcast()
            _log.error(
                "broadcast queue still full after expansion, enqueued critical message "
                "without sequence number: type=%s",
                msg_type or '?',
            )
        except queue.Full:
            self._terminal_drops += 1
            note_degradation(_DEGRADATION_KEY, "broadcast_terminal_dropped", reason=str(msg_type or '?'))
            _log.error(
                "broadcast queue still full after expansion, terminal message may be delayed: type=%s",
                msg_type or '?',
            )

    def _expand_broadcast_queue(self) -> None:
        """Replace the broadcast queue with a larger one, preserving queued items."""
        # 换队列期间必须挡住其它生产者：否则别的线程往**旧**队列里塞，
        # 这条消息就随旧队列一起蒸发（而且已经发过号 ⇒ 前端看到缺口却查不到原因）。
        with self._seq_lock:
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
                    # 新队列已满：只能丢最旧的；带号的说明它是关键消息，单独计数
                    try:
                        lost = old_queue.get_nowait()
                    except queue.Empty:
                        break
                    dropped += 1
                    if '_seq' in lost:
                        self._terminal_drops += 1
                        note_degradation(
                            _DEGRADATION_KEY,
                            "broadcast_terminal_dropped",
                            reason=str(lost.get('type') or '?'),
                        )
            self._broadcast_queue = new_queue
            _log.error(
                "broadcast queue expanded: maxsize %d -> %d (dropped %d)", old_max, new_max, dropped
            )
