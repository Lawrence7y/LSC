"""Task 3: begin/confirm/cancel refine handlers 契约测试。

Spec 要求：
- begin_refine_clip: 冻结 round_key，广播 refining 状态
- confirm_highlight_clip: 主房+目标房 user_confirmed，多房 upsert
- cancel_refine_clip: 取消精修，恢复 pending 状态
- OCR 升格须跳过冻结的 round_key
- 所有 handler 必须返回非 None dict（#3 回归：无 return 导致前端永久 await）
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]

_backend_dir = os.path.join(ROOT, "python-backend")
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)
_tests_dir = os.path.dirname(__file__)
if _tests_dir not in sys.path:
    sys.path.insert(0, _tests_dir)


def test_map_confirm_bounds_helper_applies_delta():
    """确认边界映射：目标房 content_offset 差应反映到 start/end。"""
    from handlers import room_handler

    main = SimpleNamespace(
        room_id="main",
        recording_start_mono=100.0,
        content_offset=10.0,
    )
    side = SimpleNamespace(
        room_id="side",
        recording_start_mono=100.0,
        content_offset=3.0,
    )
    mapped = room_handler._map_highlight_to_room(
        {"start": 30.0, "end": 45.0}, main, side,
    )
    assert abs(float(mapped["start"]) - 37.0) < 1e-6
    assert abs(float(mapped["end"]) - 52.0) < 1e-6
    assert abs(float(mapped["offset_delta"]) - 7.0) < 1e-6


# ── #3 regression: handlers must return a non-None dict ──────────────

# Reuse the fakes from the lifecycle test to avoid duplicating the
# elaborate _FakeServer/_FakeBridge/_FakeManager infrastructure that
# register_room_handlers requires.
from test_room_handler_lifecycle import _FakeBridge, _FakeManager, _FakeServer  # noqa: E402


def _register_refine_handlers():
    """Register handlers with fakes and return (server, bridge)."""
    from handlers import room_handler
    server = _FakeServer()
    bridge = _FakeBridge(_FakeManager([]))
    room_handler.register_room_handlers(server, bridge)
    return server, bridge


def test_begin_refine_clip_returns_dict():
    """begin_refine_clip must return a non-None dict, not None (#3)."""
    server, _ = _register_refine_handlers()

    async def scenario():
        return await server.handlers["begin_refine_clip"]({
            "room_id": "room-1",
            "round_key": "round-001",
            "start": 10.0,
            "end": 40.0,
        })

    result = asyncio.run(scenario())
    assert result is not None, "begin_refine_clip returned None - frontend would hang forever"
    assert isinstance(result, dict)
    assert result.get("success") is True
    assert result.get("round_key") == "round-001"


def test_confirm_highlight_clip_returns_dict():
    """confirm_highlight_clip must return a non-None dict (#3)."""
    server, _ = _register_refine_handlers()

    async def scenario():
        return await server.handlers["confirm_highlight_clip"]({
            "room_id": "room-1",
            "round_key": "round-001",
            "start": 10.0,
            "end": 40.0,
            "target_room_ids": [],
        })

    result = asyncio.run(scenario())
    assert result is not None, "confirm_highlight_clip returned None - frontend would hang forever"
    assert isinstance(result, dict)
    assert result.get("success") is True
    assert result.get("status") == "user_confirmed"


def test_cancel_refine_clip_returns_dict():
    """cancel_refine_clip must return a non-None dict (#3)."""
    server, _ = _register_refine_handlers()

    async def scenario():
        return await server.handlers["cancel_refine_clip"]({
            "room_id": "room-1",
            "round_key": "round-001",
            "start": 10.0,
            "end": 40.0,
        })

    result = asyncio.run(scenario())
    assert result is not None, "cancel_refine_clip returned None - frontend would hang forever"
    assert isinstance(result, dict)
    assert result.get("success") is True


def test_refine_handlers_return_error_on_missing_round_key():
    """All three handlers must return an error dict (not None) when round_key is missing."""
    server, _ = _register_refine_handlers()

    for handler_name in ("begin_refine_clip", "confirm_highlight_clip", "cancel_refine_clip"):
        async def scenario(name=handler_name):
            return await server.handlers[name]({"room_id": "room-1"})

        result = asyncio.run(scenario())
        assert result is not None, f"{handler_name} returned None on missing round_key"
        assert isinstance(result, dict)
        assert result.get("success") is False


# ── #4 regression: input length limits ────────────────────────────────


def test_save_douyin_cookies_rejects_oversized_input():
    """save_douyin_cookies must reject payloads exceeding the 1MB limit (#4)."""
    from handlers import room_handler
    server, _ = _register_refine_handlers()

    oversized = "x" * (2 * 1024 * 1024)  # 2 MB

    async def scenario():
        return await server.handlers["save_douyin_cookies"]({"cookies": oversized})

    result = asyncio.run(scenario())
    assert result is not None
    assert result.get("success") is False
    assert "过大" in result.get("error", "") or "large" in result.get("error", "").lower()


def test_align_preview_audio_rejects_too_many_rooms():
    """align_preview_audio must reject > 64 rooms (#4)."""
    server, _ = _register_refine_handlers()

    rooms = [{"room_id": f"r{i}", "sample_rate": 16000, "pcm_base64": "AAAA"} for i in range(65)]

    async def scenario():
        return await server.handlers["align_preview_audio"]({"rooms": rooms})

    result = asyncio.run(scenario())
    assert result is not None
    assert result.get("success") is False
    assert "过多" in result.get("error", "") or "many" in result.get("error", "").lower()
