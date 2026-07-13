"""TimelineContext delta 符号与 common↔preview 换算一致性。"""
from __future__ import annotations

from lsc.core.services.timeline_service import TimelineService, build_room_snapshots_from_align


def test_reference_room_has_zero_preview_delta():
    snaps = build_room_snapshots_from_align(
        "r0",
        {"r0": 0.0, "r1": 0.8},
        {"r0": 1.0, "r1": 0.9},
        {
            "r0": {"recording_id": "a", "media_start_mono": 1000.0, "preview_epoch_id": "e0"},
            "r1": {"recording_id": "b", "media_start_mono": 1000.0, "preview_epoch_id": "e1"},
        },
    )
    assert snaps["r0"].preview_to_common_delta == 0.0
    assert abs(snaps["r1"].preview_to_common_delta - 0.8) < 1e-9


def test_common_preview_roundtrip():
    svc = TimelineService()
    snaps = build_room_snapshots_from_align(
        "r0",
        {"r0": 0.0, "r1": 0.8},
        {"r0": 1.0, "r1": 0.9},
        {
            "r0": {"recording_id": "a", "media_start_mono": 1000.0},
            "r1": {"recording_id": "b", "media_start_mono": 1000.0},
        },
    )
    ctx = svc.create_timeline("r0", snaps, required_room_ids=["r0", "r1"])
    assert ctx is not None
    common_t = 20.0
    assert abs(ctx.common_to_preview("r1", common_t) - 19.2) < 1e-6
    rec_r1 = ctx.common_to_recording("r1", common_t)
    assert abs(rec_r1 - (common_t - 1000.8)) < 1e-6
