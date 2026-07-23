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
