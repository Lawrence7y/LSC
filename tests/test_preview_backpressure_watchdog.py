"""预览 MSE backpressure 暂停的超时自愈守卫（2026-09-15 现场事故）。

现场（2026-09-15 11:52-12:06）：
  * 长预览后前端播放器媒体时钟冻结、stall 恢复预算耗尽 → 报「预览恢复失败，请手动重新开启预览」；
  * 前端在 pending>=10 时发过 mse_backpressure pause，而它的 resume 只在队列排空时才发；
    管线卡住 ⇒ 队列永远排不空 ⇒ 永远不发 resume；
  * 后端 _mse_push_paused 于是永久保留该房，_push_mse_segment 把该房后续所有 media 段
    全部丢弃（日志 12:03:42 pause 之后再无 resume）⇒ 前端重建播放器、重放 init 都拿不到
    media，只有「重开预览」（走 stop → 清标记）能恢复。

本文件钉住：暂停必须记录时刻；超时由**推送侧**自动恢复（前端 resume 不可信），
并且 init 段永不受暂停门控（从错误恢复必须还能拿到 init）。
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path


def _room_handler():
    import handlers.room_handler as room_handler

    return room_handler


class _FakeWsServer:
    def __init__(self) -> None:
        self.pushed: list[tuple[str, str, int]] = []

    async def broadcast_mse(self, kind, room_id, seg):
        self.pushed.append((kind, room_id, len(seg)))

    async def broadcast(self, *_args, **_kwargs):
        return None


def _pause_room(rh, room_id: str, *, age_sec: float, dropped: int = 0) -> None:
    with rh._mse_push_paused_lock:
        rh._mse_push_paused.add(room_id)
        rh._mse_push_paused_at[room_id] = time.monotonic() - float(age_sec)
        rh._mse_push_dropped[room_id] = dropped


def _drain(loop: asyncio.AbstractEventLoop) -> None:
    """让 run_coroutine_threadsafe 排入的回调与任务真的执行（两拍足够）。"""
    loop.run_until_complete(asyncio.sleep(0))
    loop.run_until_complete(asyncio.sleep(0))


def test_auto_resume_decision_thresholds() -> None:
    rh = _room_handler()
    now = 1000.0

    assert rh._mse_pause_should_auto_resume(now, None) is False
    assert rh._mse_pause_should_auto_resume(now, now - 1.0) is False
    assert rh._mse_pause_should_auto_resume(now, now - rh._MSE_PUSH_PAUSE_MAX_SEC) is True
    assert rh._mse_pause_should_auto_resume(now, now - 999.0) is True
    assert rh._mse_pause_should_auto_resume(now, "bad") is False
    assert rh._mse_pause_should_auto_resume(now, now - 2.0, max_pause_sec=1.0) is True


def test_stale_pause_auto_resumes_and_pushes_again() -> None:
    """暂停超时后：分段必须继续推送，且暂停标记被清掉（前端可重新暂停）。"""
    rh = _room_handler()
    room = "room-stale-pause"
    rh._clear_mse_push_paused(room)
    _pause_room(rh, room, age_sec=rh._MSE_PUSH_PAUSE_MAX_SEC + 5.0, dropped=7)
    server = _FakeWsServer()

    loop = asyncio.new_event_loop()
    try:
        rh._push_mse_segment(server, loop, "mse_segment", room, b"seg")
        _drain(loop)
    finally:
        loop.close()

    assert server.pushed == [("segment", room, 3)], "超时暂停不得再吞分段"
    paused, paused_at, dropped = rh._mse_pause_snapshot(room)
    assert paused is False
    assert paused_at is None and dropped == 0
    rh._clear_mse_push_paused(room)


def test_fresh_pause_still_drops_segments_and_counts() -> None:
    """未超时的暂停语义不变（仍然丢分段），但要记账便于事后统计。"""
    rh = _room_handler()
    room = "room-fresh-pause"
    rh._clear_mse_push_paused(room)
    _pause_room(rh, room, age_sec=0.5)
    server = _FakeWsServer()

    loop = asyncio.new_event_loop()
    try:
        rh._push_mse_segment(server, loop, "mse_segment", room, b"seg")
        rh._push_mse_segment(server, loop, "mse_segment", room, b"seg")
        _drain(loop)
    finally:
        loop.close()

    assert server.pushed == []
    paused, _paused_at, dropped = rh._mse_pause_snapshot(room)
    assert paused is True
    assert dropped == 2
    rh._clear_mse_push_paused(room)


def test_init_segments_never_gated_by_backpressure() -> None:
    """init 段不受暂停门控：前端从错误恢复必须还能拿到 init。"""
    rh = _room_handler()
    room = "room-init-pause"
    rh._clear_mse_push_paused(room)
    _pause_room(rh, room, age_sec=0.0)
    server = _FakeWsServer()

    loop = asyncio.new_event_loop()
    try:
        rh._push_mse_segment(server, loop, "mse_init", room, b"init")
        _drain(loop)
    finally:
        loop.close()

    assert server.pushed == [("init", room, 4)]
    paused, _paused_at, dropped = rh._mse_pause_snapshot(room)
    assert paused is True and dropped == 0
    rh._clear_mse_push_paused(room)


def test_clear_pause_releases_all_bookkeeping() -> None:
    """清标记必须同时清时刻与计数，否则下次暂停会带着旧时刻被立刻自动恢复。"""
    rh = _room_handler()
    room = "room-clear"
    _pause_room(rh, room, age_sec=1.0, dropped=3)

    rh._clear_mse_push_paused(room)

    assert rh._mse_pause_snapshot(room) == (False, None, 0)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def test_player_frontend_contract_source_guard() -> None:
    """源守卫：前端播放器三处契约（离开暂停态必发 resume / 有界硬重建 / 错误快照）。"""
    src = (_repo_root() / "lsc-electron/src/services/mediaSourcePlayer.ts").read_text(
        encoding="utf-8"
    )

    assert "_emitBackpressureResume(" in src
    assert "_emitBackpressureResume('player error')" in src
    assert "_emitBackpressureResume('player stop')" in src
    assert "private _tryHardResetRecovery(" in src
    assert "private _resetStreamPipeline(" in src
    # 三条恢复路径（stall 耗尽 / 强制 seek 耗尽 / 非配额 append 失败）都要先尝试硬重建
    assert src.count("if (this._tryHardResetRecovery(") >= 3
    assert src.count("this._handleError('预览恢复失败，请手动重新开启预览')") == 2
    assert "console.error(" in src and "[MsePlayer] ERROR:" in src
    assert "private _healthSnapshot(" in src


def test_main_process_forwards_player_warn_error() -> None:
    """源守卫：[MsePlayer] 的 WARN/ERROR 必须进 debug.log（现场唯一证据通道）。"""
    src = (_repo_root() / "lsc-electron/electron/main.ts").read_text(encoding="utf-8")

    assert "function shouldSkipRendererConsole(message: string, level?: number)" in src
    assert "if (message.includes('[MsePlayer]')) return (level ?? 0) < 2" in src
    assert "shouldSkipRendererConsole(message, level)" in src
