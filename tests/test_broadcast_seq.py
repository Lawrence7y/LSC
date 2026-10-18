"""广播通道序列号与唤醒失败的回归测试（2026-09-18 债务收口 §11.17）。

守的是**"前端的丢消息判定本身可不可信"**这件事：

- 修前 `queue_broadcast` 先 `self._seq += 1`、再 `put_nowait`，两步之间没有任何同步
  ⇒ 队列里可以是 `[6, 5]`。前端 `useWebSocket` 只在 `_seq > last + 1` 时报警，
  于是倒序既不报警、还把 `last` 拉低，**真正的缺口被掩盖**；
- 修前可丢弃遥测（`system_stats` 等）也占号 ⇒ 队列满时的**设计内丢弃**会制造假缺口；
- 修前 asyncio loop 已关闭时 `notify_broadcast` 只 `_log.debug` ⇒ 生产日志里不可见。
"""
from __future__ import annotations

import logging
import os
import queue
import sys
import threading
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_backend_dir = os.path.join(ROOT, "python-backend")
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

from broadcast_hub import BroadcastHub  # noqa: E402

from lsc.platforms import diagnostics  # noqa: E402


class _Bus:
    def subscribe(self, *_args, **_kwargs) -> None:
        return None


class _Orchestrator:
    bus = _Bus()


@pytest.fixture(autouse=True)
def _isolate_degradation_counters():
    """降级计数是进程级注册表：用例之间必须隔离，否则"只 WARN 一次"互相污染。"""
    diagnostics.reset()
    yield
    diagnostics.reset()


@pytest.fixture()
def hub() -> BroadcastHub:
    return BroadcastHub(_Orchestrator())


def _drain_seqs(h: BroadcastHub) -> list[int]:
    out: list[int] = []
    while True:
        item = h.get_broadcast(block=False)
        if item is None:
            return out
        if '_seq' in item:
            out.append(int(item['_seq']))


def test_critical_messages_keep_queue_order_equal_to_seq_order(hub, monkeypatch):
    """并发生产者下，队列里的序列号必须严格递增且连续。

    `put_nowait` 被换成"先让出 GIL 再入队"，用来稳定复现修前的
    「先发号、后入队」窗口 —— 没有这一步，CPython 的 GIL 会让竞态很少发生，
    用例就成了碰运气。
    """
    real_put = queue.Queue.put_nowait

    def slow_put(self, item):
        time.sleep(0)
        real_put(self, item)

    monkeypatch.setattr(queue.Queue, "put_nowait", slow_put)

    producers, per_producer = 8, 25

    def produce(idx: int) -> None:
        for n in range(per_producer):
            hub.queue_broadcast({'type': 'clip_completed', 'data': {'p': idx, 'n': n}})

    threads = [threading.Thread(target=produce, args=(i,)) for i in range(producers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    seqs = _drain_seqs(hub)
    total = producers * per_producer
    assert len(seqs) == total
    assert seqs == list(range(1, total + 1)), "队列顺序与序列号顺序不一致 ⇒ 前端缺号判定不可信"


def test_droppable_telemetry_never_consumes_a_sequence_number(hub):
    """遥测被丢弃是设计行为，不能被算进"关键消息缺号"这条告警里。"""
    hub.queue_broadcast({'type': 'system_stats', 'data': {}})
    telemetry = hub.get_broadcast(block=False)
    assert telemetry is not None
    assert '_seq' not in telemetry

    hub.queue_broadcast({'type': 'clip_completed', 'data': {}})
    critical = hub.get_broadcast(block=False)
    assert critical is not None
    assert critical['_seq'] == 1


def test_full_queue_drop_leaves_no_phantom_gap(hub):
    """队列满时丢一条遥测：下一条关键消息的号必须是连续的 2，而不是跳过 2。"""
    hub._broadcast_queue = queue.Queue(maxsize=1)
    hub.queue_broadcast({'type': 'clip_completed', 'data': {'n': 1}})
    hub.queue_broadcast({'type': 'rooms_updated', 'data': {}})
    hub.queue_broadcast({'type': 'clip_completed', 'data': {'n': 2}})

    assert _drain_seqs(hub) == [1, 2]
    assert hub.broadcast_health()['dropped_droppable'] == 1


def test_lost_wake_is_counted_and_warned_once(hub, caplog):
    """loop 已关闭 ⇒ 唤醒失败必须计数并留一条 WARN（原先只有 debug，生产不可见）。"""

    class _DeadLoop:
        def call_soon_threadsafe(self, *_args, **_kwargs) -> None:
            raise RuntimeError('event loop is closed')

    hub.bind_async_wake(_DeadLoop(), threading.Event())
    with caplog.at_level(logging.WARNING):
        for n in range(5):
            hub.queue_broadcast({'type': 'clip_completed', 'data': {'n': n}})

    health = hub.broadcast_health()
    assert health['wake_failures'] == 5
    warnings = [r for r in caplog.records if 'broadcast wake lost' in r.getMessage()]
    assert len(warnings) == 1
    # 计数还要能经 `system_stats` 出去（诊断快照与日志同源）
    assert diagnostics.degradation_snapshot()['backend']['broadcast_wake_lost'] == 5


def test_wake_exception_never_escapes_into_the_producer(hub):
    """唤醒路径里的任何异常都必须被吞在 hub 内部：生产者多是 executor 线程，
    异常逃逸会直接杀死线程（本文件其余部分同源的设计约束）。"""

    class _ExplodingLoop:
        def call_soon_threadsafe(self, *_args, **_kwargs) -> None:
            raise ValueError('unexpected')

    hub.bind_async_wake(_ExplodingLoop(), threading.Event())
    hub.queue_broadcast({'type': 'clip_completed', 'data': {}})  # 不抛
    assert hub.broadcast_health()['wake_failures'] == 1
    assert hub.get_broadcast(block=False)['_seq'] == 1


def test_broadcast_health_snapshot_is_serializable(hub):
    import json

    hub.queue_broadcast({'type': 'clip_completed', 'data': {}})
    health = hub.broadcast_health()
    assert json.loads(json.dumps(health)) == health
    assert health['seq'] == 1
    assert health['queued'] == 1
    assert health['queue_maxsize'] == 1000
