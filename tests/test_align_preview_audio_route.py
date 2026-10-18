"""`align_preview_audio` 路由级用例（§9-P0.5 的"补 handler harness"这一半）。

`alignment_handlers.py` 此前 16%→23% 只覆盖了纯函数；这个 P0 入口
（多房间互相关 → 写 `content_offset`/`align_group_id` → 建公共时间轴）的
**输入校验与失败分支一直无人守** —— 里面那几条上限（≤64 房、单路 ≤20MB、
采样率 8k–48k、时长 ≤15s）存在的目的就是防超大 payload 打爆后端，
而"硬拒"与"静默跳过"的分界一旦写反，表现就是用户点"一键对齐"没反应。
"""
from __future__ import annotations

import asyncio
import base64
import os
import sys
from types import SimpleNamespace

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_backend_dir = os.path.join(ROOT, "python-backend")
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

import handlers.alignment_handlers as ah  # noqa: E402
from handlers.alignment_handlers import register_alignment_handlers  # noqa: E402

RATE = 16000


class _FakeServer:
    def __init__(self) -> None:
        self.handlers: dict = {}

    def on(self, name, *args, **kwargs):
        def _deco(fn):
            self.handlers[name] = fn
            return fn
        return _deco


class _CallOnlyManager:
    """编排线程门面：`call(fn)` 同步执行（真实现是投递到编排线程再等结果）。"""

    def __init__(self, rooms: dict) -> None:
        self.rooms = rooms

    def get_room(self, room_id: str):
        return self.rooms.get(room_id)

    def call(self, fn):
        return fn()


class _FakeBridge:
    def __init__(self, rooms: dict) -> None:
        self.manager = _CallOnlyManager(rooms)
        self.msgs: list[dict] = []

    def queue_broadcast(self, msg):
        self.msgs.append(msg)


def _room(room_id: str, **extra):
    base = {
        'room_id': room_id,
        'content_offset': 0.0,
        'align_group_id': '',
        'output_bundle_dir': '',
        'recording_media_start_mono': 1000.0,
        'recording_start_mono': 1000.0,
        'recording_id': f'rec-{room_id}',
        'preview_epoch_id': f'ep-{room_id}',
    }
    base.update(extra)
    return SimpleNamespace(**base)


@pytest.fixture()
def harness(tmp_path, monkeypatch):
    """路由 + 假房间；落盘目录钉到 tmp（真实现会 mkdir 到 ~/LSC/output）。"""
    rooms = {'a': _room('a'), 'b': _room('b')}
    server = _FakeServer()
    forced: list[bool] = []
    bridge = _FakeBridge(rooms)
    register_alignment_handlers(
        server,
        bridge=bridge,
        manager=SimpleNamespace(get_room=rooms.get),
        broadcast_rooms=lambda **kw: forced.append(bool(kw.get('force'))),
        bridge_executor=None,
        recording_executor=None,
    )
    monkeypatch.setattr(ah, '_settings_output_dir', lambda: str(tmp_path))
    return server.handlers['align_preview_audio'], rooms, bridge


def _pcm(seconds: float = 3.0, rate: int = RATE, shift_samples: int = 0, seed: int = 7) -> str:
    """非周期瞬态 + 低电平噪声。

    注意别用规则周期信号做夹具：先前用 3Hz 方波脉冲，互相关在 1/3s 的整数倍上
    都有同高尖峰，估计器锁到 0.1667s 的**混叠**解（score 还有 0.989）—— 那是夹具
    含糊，不是实现错。随机位置的宽脉冲只有唯一峰。
    """
    rng = np.random.default_rng(seed)
    n = int(seconds * rate)
    sig = rng.standard_normal(n).astype(np.float32) * 0.02
    width = int(0.006 * rate)
    for pos in rng.choice(max(1, n - width * 4), size=14, replace=False) + width * 2:
        sig[pos:pos + width] += 0.9
    if shift_samples:
        # 前置补零再截断：真正的"整段推迟"，不像 np.roll 会把尾部绕回开头
        sig = np.concatenate([np.zeros(abs(shift_samples), dtype=np.float32), sig])[:n] \
            if shift_samples > 0 else np.concatenate([sig[abs(shift_samples):],
                                                      np.zeros(abs(shift_samples), dtype=np.float32)])
    return base64.b64encode(sig.astype(np.float32).tobytes()).decode()


def _payload(**overrides):
    rooms = overrides.pop('rooms', None)
    if rooms is None:
        rooms = [
            {'room_id': 'a', 'sample_rate': RATE, 'pcm_base64': _pcm()},
            {'room_id': 'b', 'sample_rate': RATE, 'pcm_base64': _pcm(shift_samples=int(0.5 * RATE))},
        ]
    return {'rooms': rooms, **overrides}


def test_rejects_fewer_than_two_rooms(harness):
    handler, _, _ = harness
    out = asyncio.run(handler(_payload(rooms=[{'room_id': 'a', 'sample_rate': RATE, 'pcm_base64': _pcm()}])))
    assert out['success'] is False
    assert '至少需要 2 个房间' in out['error']


def test_rejects_too_many_rooms(harness):
    handler, _, _ = harness
    rooms = [{'room_id': f'r{i}', 'sample_rate': RATE, 'pcm_base64': _pcm(seconds=1.0)} for i in range(65)]
    out = asyncio.run(handler(_payload(rooms=rooms)))
    assert out['success'] is False
    assert '房间数过多' in out['error']


def test_rejects_duplicate_room_id(harness):
    handler, _, _ = harness
    one = {'room_id': 'a', 'sample_rate': RATE, 'pcm_base64': _pcm()}
    out = asyncio.run(handler(_payload(rooms=[one, dict(one)])))
    assert out['success'] is False
    assert '重复' in out['error']


@pytest.mark.parametrize('bad_rate', [4000, 96000])
def test_rejects_sample_rate_out_of_range(harness, bad_rate):
    handler, _, _ = harness
    out = asyncio.run(handler(_payload(rooms=[
        {'room_id': 'a', 'sample_rate': RATE, 'pcm_base64': _pcm()},
        {'room_id': 'b', 'sample_rate': bad_rate, 'pcm_base64': _pcm()},
    ])))
    assert out['success'] is False
    assert '采样率无效' in out['error']


def test_rejects_mismatched_sample_rates(harness):
    """两路都合法但互不相同 ⇒ 硬拒（不是静默跳过），否则互相关的采样率基准是猜的。"""
    handler, _, _ = harness
    out = asyncio.run(handler(_payload(rooms=[
        {'room_id': 'a', 'sample_rate': 16000, 'pcm_base64': _pcm()},
        {'room_id': 'b', 'sample_rate': 48000, 'pcm_base64': _pcm(seconds=1.0, rate=48000)},
    ])))
    assert out['success'] is False
    assert '采样率不一致' in out['error']


def test_rejects_audio_longer_than_limit(harness):
    handler, _, _ = harness
    out = asyncio.run(handler(_payload(rooms=[
        {'room_id': 'a', 'sample_rate': RATE, 'pcm_base64': _pcm(seconds=16.0)},
        {'room_id': 'b', 'sample_rate': RATE, 'pcm_base64': _pcm()},
    ])))
    assert out['success'] is False
    assert '15 秒上限' in out['error']


def test_undecodable_pcm_is_skipped_then_fails_as_insufficient(harness):
    """坏数据"跳过"而不是整体失败；但有效路数 <2 必须给出明确错误。"""
    handler, _, _ = harness
    out = asyncio.run(handler(_payload(rooms=[
        {'room_id': 'a', 'sample_rate': RATE, 'pcm_base64': 'not-base64!!'},
        {'room_id': 'b', 'sample_rate': RATE, 'pcm_base64': _pcm()},
    ])))
    assert out['success'] is False
    assert '有效音频不足 2 路' in out['error']


def test_pcm_not_multiple_of_float32_is_rejected_as_invalid_room(harness):
    handler, _, _ = harness
    bad = base64.b64encode(b'\x01\x02\x03').decode()
    out = asyncio.run(handler(_payload(rooms=[
        {'room_id': 'a', 'sample_rate': RATE, 'pcm_base64': bad},
        {'room_id': 'b', 'sample_rate': RATE, 'pcm_base64': _pcm()},
    ])))
    assert out['success'] is False
    assert '有效音频不足 2 路' in out['error']


def test_happy_path_writes_offsets_group_and_timeline(harness):
    handler, rooms, bridge = harness
    out = asyncio.run(handler(_payload()))
    assert out.get('success') is True, out
    offsets = out['offsets']
    assert set(offsets) == {'a', 'b'}
    # 后端把"最慢房间"归零 ⇒ 两路差值就是真偏移（0.5s），且不得出现负数
    assert min(offsets.values()) == 0.0
    assert abs(max(offsets.values()) - 0.5) < 0.15, offsets
    assert rooms['a'].align_group_id == rooms['b'].align_group_id != ''
    assert rooms['a'].content_offset == pytest.approx(offsets['a'], abs=1e-6)
    assert 'timeline' in out
    types = [m.get('type') for m in bridge.msgs]
    assert 'timeline_ready' in types, types


def test_compute_timeout_is_reported_not_hung(harness, monkeypatch):
    handler, _, _ = harness
    monkeypatch.setattr(ah, '_ALIGN_COMPUTE_TIMEOUT_SEC', 0.001)

    def slow(*_args, **_kwargs):
        import time as _t

        _t.sleep(0.3)
        raise AssertionError('不应返回')

    import lsc.editor.audio_aligner as aa

    monkeypatch.setattr(aa, 'align_audio_map', slow)
    out = asyncio.run(handler(_payload()))
    assert out['success'] is False
    assert '超时' in out['error']


def test_insufficient_trust_clears_offsets_and_returns_buffer_only(harness, monkeypatch):
    """置信度 <0.3 的房间不得写 offset/group，且整体按 buffer_only 报失败。"""
    handler, rooms, bridge = harness
    import lsc.editor.audio_aligner as aa

    class _Result:
        success = True
        offsets = {'a': 0.0, 'b': 1.5}
        reference_room_id = 'a'
        correlation_scores = {'a': 0.9, 'b': 0.1}
        method = 'preview_audio'
        error = ''

    rooms['b'].align_group_id = 'stale-group'
    rooms['b'].content_offset = 7.0
    monkeypatch.setattr(aa, 'align_audio_map', lambda *a, **k: _Result())
    out = asyncio.run(handler(_payload()))
    assert out['success'] is False
    assert out['precision'] == 'buffer_only'
    assert '可信对齐不足' in out['error']
    assert rooms['b'].content_offset == 0.0
    assert rooms['b'].align_group_id == ''
