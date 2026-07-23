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
    clip_allowed_for_draft,
    clip_source_usable,
    compute_draft_origin,
    map_clip_timeranges,
    map_recording_timeranges,
    resolve_common_range,
    sanitize_draft_token,
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


def test_clip_allowed_for_draft_matches_export_policy():
    assert clip_allowed_for_draft({"confirm_status": "refining"}) is False
    assert clip_allowed_for_draft({"mark_precision": "approximate"}) is False
    assert clip_allowed_for_draft({"confirm_status": "ocr_confirmed"}) is True


def test_resolve_common_range_exact_fields():
    assert resolve_common_range({"common_start": 0.5, "common_end": 3.0}, None) == (
        0.5,
        3.0,
        "exact",
    )


import json
import shutil
import subprocess
from pathlib import Path

import pytest

from lsc.exporter.jianying_draft import (
    ClipDraftSource,
    RoomDraftSource,
    build_session_draft,
)


def _ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def _make_color_mp4(path: Path, duration: float = 3.0, color: str = "red") -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c={color}:s=320x240:d={duration}",
            "-f",
            "lavfi",
            "-i",
            f"sine=f=440:d={duration:.3f}",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(path),
        ],
        check=True,
        capture_output=True,
    )


@pytest.mark.skipif(not _ffmpeg_available(), reason="ffmpeg required")
def test_build_session_draft_e2e_two_rooms(tmp_path: Path):
    a = tmp_path / "a.mp4"
    b = tmp_path / "b.mp4"
    _make_color_mp4(a, 4.0, "red")
    _make_color_mp4(b, 4.0, "blue")
    draft_root = tmp_path / "drafts"
    draft_root.mkdir()

    rooms = [
        RoomDraftSource("r1", "主房", str(a), 0.0, is_main=True),
        RoomDraftSource("r2", "副房", str(b), 1.5, is_main=False),
    ]
    clips = [
        ClipDraftSource(
            "c1",
            1.5,
            3.0,
            "R1 测试",
            precision="exact",
            confirm_status="user_confirmed",
        ),
    ]
    result = build_session_draft(
        rooms=rooms,
        clips=clips,
        options=JianyingDraftOptions(draft_name="LSC_test_e2e", text_labels=True),
        draft_root=str(draft_root),
    )
    assert result.success, result.error
    assert result.tracks >= 5  # 2 rec + 2 clip + 1 text
    content = Path(result.draft_dir) / "draft_content.json"
    assert content.is_file()
    data = json.loads(content.read_text(encoding="utf-8"))
    track_names = [
        t.get("name", "")
        for t in data.get("tracks", data.get("materials", {}).get("tracks", []))
        if isinstance(t, dict)
    ]
    assert track_names or result.segments >= 3
    assert result.segments >= 3


@pytest.mark.skipif(not _ffmpeg_available(), reason="ffmpeg required")
def test_build_overwrite_same_name(tmp_path: Path):
    a = tmp_path / "a.mp4"
    _make_color_mp4(a, 2.0, "green")
    draft_root = tmp_path / "drafts"
    draft_root.mkdir()
    rooms = [RoomDraftSource("r1", "nobody", str(a), 0.0, is_main=True)]
    opt = JianyingDraftOptions(
        draft_name="LSC_overwrite", include_clips=False, text_labels=False
    )
    r1 = build_session_draft(rooms=rooms, clips=[], options=opt, draft_root=str(draft_root))
    assert r1.success
    r2 = build_session_draft(rooms=rooms, clips=[], options=opt, draft_root=str(draft_root))
    assert r2.success
    assert any("覆盖" in w or "同名" in w for w in r2.warnings)


def test_build_rejects_missing_library(monkeypatch, tmp_path: Path):
    import lsc.exporter.jianying_draft as mod

    def boom(*_a, **_k):
        raise ImportError("nope")

    monkeypatch.setattr(mod, "_import_draft_lib", boom)
    r = build_session_draft(
        rooms=[RoomDraftSource("r1", "x", str(tmp_path / "no.mp4"), 0.0, True)],
        clips=[],
        options=JianyingDraftOptions(include_recordings=False, include_clips=False),
        draft_root=str(tmp_path),
    )
    assert r.success is False
    assert r.error_code == "library_missing"
