from __future__ import annotations

from lsc.core.models import JianyingDraftOptions, JianyingDraftResult


def test_draft_options_defaults():
    opt = JianyingDraftOptions()
    assert opt.include_recordings is True
    assert opt.include_clips is True
    assert opt.text_labels is True
    assert opt.vertical is False
    assert opt.draft_name == ""
    assert opt.non_main_volume_zero is False


def test_draft_result_fields():
    r = JianyingDraftResult(
        success=True,
        draft_name="LSC_test",
        draft_dir="C:/tmp/LSC_test",
        tracks=5,
        segments=3,
        warnings=["w"],
    )
    assert r.error == ""
    assert r.warnings == ["w"]


from lsc.exporter.jianying_draft import (
    compute_draft_origin,
    map_recording_timeranges,
    map_clip_timeranges,
    sanitize_draft_token,
    clip_source_usable,
)


def test_draft_origin_is_min_delta():
    deltas = {"a": 1.5, "b": 0.0, "c": 0.8}
    assert compute_draft_origin(deltas) == 0.0


def test_recording_timeranges():
    t_start, t_dur, s_start, s_dur = map_recording_timeranges(
        recording_to_common_delta=1.5,
        draft_origin=0.0,
        dur_sec=10.0,
    )
    assert (t_start, t_dur, s_start, s_dur) == (1.5, 10.0, 0.0, 10.0)


def test_clip_timeranges_same_target_different_source():
    a = map_clip_timeranges(5.0, 10.0, recording_to_common_delta=0.0, draft_origin=0.0)
    b = map_clip_timeranges(5.0, 10.0, recording_to_common_delta=1.5, draft_origin=0.0)
    assert a[:2] == b[:2] == (5.0, 5.0)
    assert a[2:] == (5.0, 5.0)
    assert b[2:] == (3.5, 5.0)


def test_clip_skip_when_source_before_zero():
    assert map_clip_timeranges(0.0, 2.0, 1.5, 0.0) is None


def test_clip_clamp_when_past_duration():
    r = map_clip_timeranges(8.0, 12.0, 0.0, 0.0, dur_sec=10.0)
    assert r is not None
    t_start, t_dur, s_start, s_dur, clamped = r
    assert s_start == 8.0 and abs(s_dur - 2.0) < 1e-9
    assert clamped is True


def test_discard_short_segment():
    assert map_clip_timeranges(1.0, 1.1, 0.0, 0.0, dur_sec=10.0) is None


def test_sanitize_illegal_chars():
    assert sanitize_draft_token('a<>:"/\\|?*b') == "a_________b"


def test_clip_source_usable_rejects_approx_pending():
    assert clip_source_usable(precision="exact", confirm_status="user_confirmed") is True
    assert clip_source_usable(precision="approximate", confirm_status="user_confirmed") is False
    assert clip_source_usable(precision="exact", confirm_status="pending") is False
    assert clip_source_usable(precision="exact", confirm_status="refining") is False
    assert clip_source_usable(precision="exact", confirm_status=None) is True
