from __future__ import annotations

from pathlib import Path

from lsc.core.models import RoomTimeSnapshot, TimelineContext
from lsc.exporter.jianying_draft import clip_allowed_for_draft, resolve_common_range

_ERROR_CODES = (
    "draft_dir_missing",
    "no_rooms",
    "no_aligned_context",
    "library_missing",
    "write_failed",
    "invalid_state",
)


def test_load_settings_default_includes_jianying_draft_dir():
    text = Path("python-backend/handlers/room_handler.py").read_text(encoding="utf-8")
    assert "'jianying_draft_dir'" in text or '"jianying_draft_dir"' in text


def test_jianying_handlers_module_exists():
    from handlers import jianying_handlers  # noqa: F401


def test_register_exports_expected_message_names():
    text = Path("python-backend/handlers/jianying_handlers.py").read_text(encoding="utf-8")
    assert "get_jianying_draft_dir" in text
    assert "generate_jianying_draft" in text
    for code in _ERROR_CODES:
        assert code in text


def test_handler_reads_include_pending_flag() -> None:
    src = Path("python-backend/handlers/jianying_handlers.py").read_text(encoding="utf-8")
    assert "include_pending" in src


def test_clip_allowed_rejects_pending_and_approx():
    assert clip_allowed_for_draft({"confirm_status": "pending"}) is False
    assert clip_allowed_for_draft({"mark_precision": "approximate"}) is False
    assert clip_allowed_for_draft(
        {"confirm_status": "user_confirmed", "mark_precision": "exact"}
    ) is True
    assert clip_allowed_for_draft({}) is True


def test_resolve_common_range_prefers_common_fields():
    r = resolve_common_range({"common_start": 1.0, "common_end": 2.5}, None)
    assert r == (1.0, 2.5, "exact")


def test_resolve_common_range_wallclock_with_ctx():
    ctx = TimelineContext(
        timeline_id="t1",
        reference_room_id="r1",
        room_snapshots={
            "r1": RoomTimeSnapshot(room_id="r1", media_start_mono=100.0),
            "r2": RoomTimeSnapshot(room_id="r2", media_start_mono=101.5),
        },
    )
    r = resolve_common_range(
        {"mark_in_wallclock": 105.0, "mark_out_wallclock": 108.0},
        ctx,
    )
    assert r == (5.0, 8.0, "exact")


def test_resolve_common_range_returns_none_without_common_or_wallclock():
    assert resolve_common_range({"mark_in_wallclock": 1.0}, None) is None
    assert resolve_common_range({}, None) is None


def test_handler_derives_room_deltas_from_clips_without_ctx():
    """ctx 缺失（预览重启/未对齐）时，从切片内联坐标反推每房 delta 兜底。"""
    from handlers.jianying_handlers import _derive_room_deltas_from_clips

    clips = [
        {"room_id": "r1", "start": 3.0, "common_start": 3.0},
        {"room_id": "r1", "start": 50.0, "common_start": 50.0},
        {"room_id": "r2", "start": 0.0, "common_start": 4.94},
        {"room_id": "r2", "start": 100.0, "common_start": 104.94},
        {"room_id": "", "start": 1.0, "common_start": 1.0},  # 无 room_id 忽略
        {"start": 1.0, "common_start": 2.0},  # 缺 room_id 忽略
        {"room_id": "r3", "start": 1.0},  # 缺 common 忽略
        {"room_id": "r4", "start": "x", "common_start": 2.0},  # 非数值忽略
    ]
    deltas = _derive_room_deltas_from_clips(clips)
    assert deltas.keys() == {"r1", "r2"}
    assert abs(deltas["r1"] - 0.0) < 1e-9
    assert abs(deltas["r2"] - 4.94) < 1e-9
    assert "r3" not in deltas and "r4" not in deltas


def test_handler_ctx_less_fallback_builds_sources(monkeypatch):
    """无对齐上下文时多房草稿不再直接拒绝：按切片反推 delta 构建房间源。"""

    from handlers import jianying_handlers as h

    class FakeRoom:
        room_id = ""
        streamer_name = ""
        record_output_path = "C:/tmp/a.mp4"
        record_manifest_path = ""

    r1 = FakeRoom()
    r1.room_id = "r1"
    r1.streamer_name = "主房"
    r2 = FakeRoom()
    r2.room_id = "r2"
    r2.streamer_name = "副房"

    class FakeManager:
        def list_rooms(self):
            return [r1, r2]

        def get_room(self, rid):
            return r1 if rid == "r1" else r2

    class FakeTimeline:
        def get_active_timeline_for_room(self, _rid):
            return None

        def get_clip_snapshot(self, _cid):
            return None

    monkeypatch.setattr(h, "get_timeline_service", lambda: FakeTimeline())
    err, sources, clip_sources, options, warnings = h._collect_draft_inputs(
        FakeManager(),
        {
            "room_ids": ["r1", "r2"],
            "main_room_id": "r1",
            "clips": [
                {
                    "clip_id": "c1",
                    "room_id": "r1",
                    "start": 3.0,
                    "end": 80.2,
                    "common_start": 3.0,
                    "common_end": 80.2,
                    "confirm_status": "vision_confirmed",
                },
                {
                    "clip_id": "c2",
                    "room_id": "r2",
                    "start": 0.0,
                    "end": 75.3,
                    "common_start": 4.94,
                    "common_end": 80.24,
                    "confirm_status": "vision_confirmed",
                },
            ],
            "options": {},
        },
    )
    assert err is None, err
    assert {s.room_id: s.recording_to_common_delta for s in sources} == {
        "r1": 0.0,
        "r2": 4.94,
    }
    assert {c.room_id for c in clip_sources} == {"r1", "r2"}
    assert any("反推" in w for w in warnings)
    assert all("需先一键对齐" not in w for w in warnings)


def test_handler_ctx_less_fallback_still_rejects_without_coords(monkeypatch):
    """无 ctx 且切片也没有坐标时，仍按原契约返回 no_aligned_context。"""

    from handlers import jianying_handlers as h

    class FakeRoom:
        room_id = "r1"
        streamer_name = "x"
        record_output_path = "C:/tmp/a.mp4"
        record_manifest_path = ""

    class FakeManager:
        def list_rooms(self):
            return [FakeRoom()]

        def get_room(self, rid):
            return FakeRoom()

    class FakeTimeline:
        def get_active_timeline_for_room(self, _rid):
            return None

        def get_clip_snapshot(self, _cid):
            return None

    monkeypatch.setattr(h, "get_timeline_service", lambda: FakeTimeline())
    err, _s, _c, _o, _w = h._collect_draft_inputs(
        FakeManager(),
        {
            "room_ids": ["r1", "r2"],
            "clips": [{"clip_id": "c1", "room_id": "r1"}],
            "options": {},
        },
    )
    assert err is not None
    assert err["error_code"] == "no_aligned_context"
