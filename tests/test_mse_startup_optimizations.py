"""MSE 启动速度与稳定性优化守卫（F1 启动期 on_error 忽略 / F2 软解回退探测减半 / F3 streaming 延迟到 init）。

全部为源码/行为守卫，不依赖真实 FFmpeg 或网络。
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[1]
ROOM_HANDLER = (ROOT / "python-backend/handlers/room_handler.py").read_text(encoding="utf-8")
MSE_STREAMER = (ROOT / "lsc/core/services/mse_streamer.py").read_text(encoding="utf-8")


# ── F1: 启动期间 on_error 必须被忽略（防硬解→软解回退与重连循环并发） ──

def test_legacy_on_mse_error_ignores_startup_period():
    """_on_mse_error 入口必须检查 _mse_starting，启动期直接 return。"""
    body = ROOM_HANDLER.split("async def _on_mse_error", 1)[1].split("while True:", 1)[0]
    assert "if room_id in _mse_starting:" in body
    assert "ignored" in body or "ignore" in body


def test_shared_on_mse_error_ignores_startup_period():
    """_shared_mse_on_error 入口同样检查 _mse_starting。"""
    body = ROOM_HANDLER.split("async def _shared_mse_on_error", 1)[1].split("while True:", 1)[0]
    assert "if room_id in _mse_starting:" in body


# ── F2: 硬解失败后的软解重试探测超时减半（上限 2s） ──

def test_sw_retry_probe_timeout_halved():
    """软解重试探测超时必须 min(2.0, startup_probe_timeout)。"""
    body = MSE_STREAMER.split("_cleanup_after_failed_start()", 1)[1].split("def _try_start", 1)[0]
    assert "min(2.0, startup_probe_timeout)" in body


# ── F3: phase 'streaming' 延迟到首个 init 段产出 ──

def test_startup_no_premature_streaming_broadcast():
    """_handle_mse_preview 启动成功处不得再立即广播 phase=streaming。"""
    # 启动成功返回段（mse streaming started 附近）不得含 streaming 广播
    idx = ROOM_HANDLER.find("note='mse streaming started'")
    assert idx > 0
    window = ROOM_HANDLER[max(0, idx - 600) : idx]
    assert "'phase': 'streaming'" not in window
    assert "preview_phase" not in window


def test_push_mse_segment_broadcasts_streaming_on_first_init():
    """_push_mse_segment 在首个 init 段时广播 preview_phase=streaming（仅一次）。"""
    idx = ROOM_HANDLER.find("def _push_mse_segment")
    assert idx > 0
    window = ROOM_HANDLER[idx : idx + 900]
    assert "_mse_live_phase" in window
    assert "if normalized_kind == \"init\" and room_id not in _mse_live_phase:" in window
    assert "'phase': 'streaming'" in window


@pytest.mark.asyncio
async def test_push_mse_segment_behavior_single_streaming_broadcast():
    """行为验证：init 首次到达广播一次 streaming；重复 init/segment 不重复广播。"""
    import sys

    sys.path.insert(0, str(ROOT / "python-backend"))

    from handlers.room_handler import _mse_live_phase, _push_mse_segment

    _mse_live_phase.clear()
    room_id = "room_f3_test"
    ws = MagicMock()
    loop = asyncio.get_running_loop()
    received: asyncio.Queue = asyncio.Queue()

    async def _fake_broadcast(msg_type, data):
        await received.put((msg_type, data))

    async def _fake_broadcast_mse(*_args, **_kwargs):
        pass

    ws.broadcast.side_effect = _fake_broadcast
    ws.broadcast_mse.side_effect = _fake_broadcast_mse

    # 首次 init：广播 preview_phase streaming（run_coroutine_threadsafe 投递到当前 loop）
    _push_mse_segment(ws, loop, "mse_init", room_id, b"\x00\x00\x00\x18ftyp")
    msg_type, data = await asyncio.wait_for(received.get(), timeout=2.0)
    assert msg_type == "preview_phase"
    assert data["phase"] == "streaming"
    assert data["room_id"] == room_id
    assert room_id in _mse_live_phase

    # 重复 init：不再广播（live 标志已置位）
    _push_mse_segment(ws, loop, "mse_init", room_id, b"\x00\x00\x00\x18ftyp")
    try:
        await asyncio.wait_for(received.get(), timeout=0.3)
        extra = True
    except asyncio.TimeoutError:
        extra = False
    assert not extra, "重复 init 不应再广播 preview_phase"

    # media 段：不广播 phase
    _push_mse_segment(ws, loop, "mse_segment", room_id, b"\x00\x00\x00\x10moof")
    try:
        await asyncio.wait_for(received.get(), timeout=0.3)
        extra = True
    except asyncio.TimeoutError:
        extra = False
    assert not extra, "media 段不应广播 preview_phase"

    _mse_live_phase.clear()
