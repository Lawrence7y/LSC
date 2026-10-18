"""停录"假失败"回归：跨线程调用超时不等于停止失败。

现场（2026-09-19 16:17）：16:17:22 下发停止 → 16:17:32 `orchestrator call timed out`
→ 16:17:34 录制才真正合段定稿。旧实现直接回 `success=False`，用户在录像完好定稿的
情况下看到"停止录制失败"。现在改成回查真实状态再回答（报告 §7.4「超时后结果未知」）。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from handlers.recording_handlers import read_room_recording_status, wait_recording_stopped


class _StatusSeq:
    """按脚本依次返回状态快照，并记录被读了几次。"""

    def __init__(self, *snapshots: dict | None) -> None:
        self._snapshots = list(snapshots)
        self.calls = 0

    def __call__(self, room_id: str) -> dict | None:
        self.calls += 1
        index = min(self.calls - 1, len(self._snapshots) - 1)
        return self._snapshots[index]


def test_timeout_recheck_waits_until_recording_actually_stops() -> None:
    """超时那一刻还在录、随后停下来 ⇒ 回查要等到它停，并按成功回答。"""
    status = _StatusSeq(
        {'exists': True, 'is_recording': True},
        {'exists': True, 'is_recording': True},
        {'exists': True, 'is_recording': False},
    )

    stopped = asyncio.run(
        wait_recording_stopped(status, 'r1', poll_sec=0.005, max_wait_sec=1.0)
    )

    assert stopped is True
    assert status.calls >= 3, "必须真的回查过，而不是超时一下就下结论"


def test_timeout_recheck_reports_failure_when_still_recording() -> None:
    """预算内始终还在录 ⇒ 如实报失败，不能为了好看一律算成功。"""
    status = _StatusSeq({'exists': True, 'is_recording': True})

    stopped = asyncio.run(
        wait_recording_stopped(status, 'r1', poll_sec=0.005, max_wait_sec=0.05)
    )

    assert stopped is False
    assert status.calls >= 1


def test_missing_room_counts_as_stopped() -> None:
    """房间已被移除时，录制不可能还在进行，按已停处理。"""
    status = _StatusSeq({'exists': False, 'is_recording': True})

    assert asyncio.run(
        wait_recording_stopped(status, 'gone', poll_sec=0.005, max_wait_sec=1.0)
    ) is True


def test_unreadable_status_is_not_optimistic() -> None:
    """读不到快照就回 False：宁可保留原来的失败结论，也不虚构成功。"""
    status = _StatusSeq(None)

    assert asyncio.run(
        wait_recording_stopped(status, 'r1', poll_sec=0.005, max_wait_sec=0.05)
    ) is False


def test_status_read_prefers_the_non_marshalling_snapshot() -> None:
    """状态读取必须优先走不经 call() 的 get_recording_status。

    编排线程正忙时才需要回查，此时任何经 call() 的读法（`get_room`）会跟着一起超时
    —— "读结果"变成第二个故障点，所以这里要确保根本没碰它。
    """
    called: list[str] = []

    def _manager() -> SimpleNamespace:
        def get_recording_status(room_id: str) -> dict:
            called.append('snapshot')
            return {'exists': True, 'is_recording': False}

        def get_room(room_id: str):  # pragma: no cover - 被调用即失败
            called.append('get_room')
            return None

        return SimpleNamespace(get_recording_status=get_recording_status, get_room=get_room)

    status = read_room_recording_status(_manager(), 'r1')

    assert status == {'exists': True, 'is_recording': False}
    assert called == ['snapshot']


def test_status_read_falls_back_to_room_object() -> None:
    """快照读法坏掉时退回房间对象，仍然拿得到 is_recording。"""

    def _boom(room_id: str) -> None:
        raise RuntimeError('snapshot reader down')

    room = SimpleNamespace(is_recording=True, last_error='boom', preview_enabled=False)
    manager = SimpleNamespace(
        get_recording_status=_boom,
        get_room=lambda room_id: room,
    )

    status = read_room_recording_status(manager, 'r1')

    assert status['is_recording'] is True
    assert status['last_error'] == 'boom'


def test_stop_handler_uses_the_recheck_instead_of_returning_failure() -> None:
    """接线：停止 handler 的异常分支必须回查后再决定，而不是直接 success=False。"""
    import inspect

    from handlers import recording_handlers

    src = inspect.getsource(recording_handlers)
    assert "stopped = await wait_recording_stopped(" in src
    # 回查失败才答失败；回查通过则按成功继续
    assert "if not stopped:" in src
    assert 'return {\'success\': False, \'error\': humanize_error(str(exc))}' in src
    assert 'success = True' in src


@pytest.mark.parametrize('bad', [object(), 'not-a-dict', 7])
def test_non_dict_snapshot_is_treated_as_unknown(bad: object) -> None:
    def _status(room_id: str) -> object:
        return bad

    assert asyncio.run(
        wait_recording_stopped(_status, 'r1', poll_sec=0.005, max_wait_sec=0.05)
    ) is False
