"""Task 3: begin/confirm/cancel refine handlers 契约测试。

Spec 要求：
- begin_refine_clip: 冻结 round_key，广播 refining 状态
- confirm_highlight_clip: 主房+目标房 user_confirmed，多房 upsert
- cancel_refine_clip: 取消精修，恢复 pending 状态
- OCR 升格须跳过冻结的 round_key
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_map_confirm_bounds_helper_applies_delta():
    """确认边界映射：目标房 content_offset 差应反映到 start/end。"""
    from types import SimpleNamespace
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
