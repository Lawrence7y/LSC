"""广播 _seq 从队列入队到前端收帧的端到端链路守卫（CLAUDE.md §2.1，2026-09-24 修复）。

修前三段断裂：BroadcastHub 把 _seq 写在消息**顶层**，三个广播协程只转发
type/data（顶层被丢弃），前端唯一消费点又恰好挂在设计上就不发号的
rooms_updated 上 ⇒ 「缺号告警」整体空转。本文件锁住：

- drain 合并的幸存消息带 _coalesced 记账（设计内塌号不得谎报缺号）；
- server.broadcast 把 seq/coalesced 注入 data._seq / data._coalesced；
- hub → drain → broadcast → 客户端收帧的完整链路里 _seq 可见。
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_backend_dir = os.path.join(ROOT, "python-backend")
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

from broadcast_hub import BroadcastHub  # noqa: E402
from server import LSCWebSocketServer, drain_merge_broadcasts  # noqa: E402


class _Bus:
    def subscribe(self, *_args, **_kwargs) -> None:
        return None


class _Orchestrator:
    bus = _Bus()


def _hub() -> BroadcastHub:
    return BroadcastHub(_Orchestrator())


class _FakeClient:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, message):
        self.sent.append(message)


def _server_with_client() -> tuple[LSCWebSocketServer, _FakeClient]:
    server = LSCWebSocketServer()
    client = _FakeClient()
    server.clients.add(client)
    return server, client


def test_broadcast_injects_seq_into_data_and_keeps_caller_dict_intact():
    """seq 必须注入下发 payload 的 data._seq，且不得改动调用方 dict。"""
    server, client = _server_with_client()
    original = {'job_id': 'j1', 'path': 'out.mp4'}

    asyncio.run(server.broadcast('clip_completed', original, seq=7))

    assert original == {'job_id': 'j1', 'path': 'out.mp4'}  # 未被注入污染
    payload = json.loads(client.sent[0])
    assert payload['type'] == 'clip_completed'
    assert payload['data']['_seq'] == 7


def test_broadcast_without_seq_keeps_payload_shape_unchanged():
    """未带 seq 的消息（心跳等）载荷形状不得漂移。"""
    server, client = _server_with_client()

    asyncio.run(server.broadcast('heartbeat', {'ts': 1.0}))

    payload = json.loads(client.sent[0])
    assert '_seq' not in payload['data']


def test_drain_merge_marks_coalesced_survivors():
    """同 key 合并掉的 numbered 消息必须在幸存消息上记 _coalesced。"""
    hub = _hub()
    hub.queue_broadcast({'type': 'clip_completed', 'data': {'job_id': 'j1', 'n': 1}})
    hub.queue_broadcast({'type': 'clip_completed', 'data': {'job_id': 'j1', 'n': 2}})
    hub.queue_broadcast({'type': 'clip_failed', 'data': {'job_id': 'j2'}})

    merged = drain_merge_broadcasts(hub)

    by_type = {m['type']: m for m in merged}
    survivor = by_type['clip_completed']
    assert survivor['data']['n'] == 2           # last-value：保留最新
    assert survivor['_seq'] == 2                # 幸存者是后一条（seq 2）
    assert survivor['_coalesced'] == 1          # 合并掉 1 条（seq 1）
    assert by_type['clip_failed']['_seq'] == 3
    assert '_coalesced' not in by_type['clip_failed']


def test_seq_survives_hub_to_wire_end_to_end():
    """hub → drain → broadcast → 客户端：前端缺号检测读的 data._seq 必须可见。"""
    hub = _hub()
    server, client = _server_with_client()
    hub.queue_broadcast({'type': 'clip_completed', 'data': {'job_id': 'j1', 'n': 1}})
    hub.queue_broadcast({'type': 'clip_completed', 'data': {'job_id': 'j1', 'n': 2}})
    hub.queue_broadcast({'type': 'clip_failed', 'data': {'job_id': 'j9'}})

    merged = drain_merge_broadcasts(hub)
    asyncio.run(_drain_to_clients(server, merged))

    payloads = [json.loads(m) for m in client.sent]
    seqs = sorted(p['data']['_seq'] for p in payloads)
    assert seqs == [2, 3]  # 前端按 seq 看：2 与 3 连续，无假缺口
    completed = next(p for p in payloads if p['type'] == 'clip_completed')
    assert completed['data']['_coalesced'] == 1  # 合并塌号有账可查


async def _drain_to_clients(server: LSCWebSocketServer, merged) -> None:
    for msg in merged:
        await server.broadcast(
            msg.get('type'),
            msg.get('data'),
            seq=msg.get('_seq'),
            coalesced=msg.get('_coalesced'),
        )


def test_last_value_coalesce_of_rooms_updated_still_unumbered():
    """rooms_updated 是可丢弃遥测：合并不影响它「永不带号」的既定语义。"""
    hub = _hub()
    hub.queue_broadcast({'type': 'rooms_updated', 'data': {'rooms': []}})
    hub.queue_broadcast({'type': 'rooms_updated', 'data': {'rooms': [1]}})

    merged = drain_merge_broadcasts(hub)

    assert len(merged) == 1
    assert '_seq' not in merged[0]
