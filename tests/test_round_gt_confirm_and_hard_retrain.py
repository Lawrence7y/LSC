from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.valorant_vision.merge_round_boundary_labels import merge_labels
from scripts.valorant_vision.round_gt import (
    build_preview_payload,
    find_nearest_frame,
    load_draft_rounds,
    save_confirmed_rounds,
    validate_confirmed_rounds,
)


def test_validate_confirmed_rounds_rejects_overlap_and_bad_bounds() -> None:
    rows = [
        {"start": 10.0, "end": 20.0, "end_reason": "result", "round_key": "R1"},
        {"start": 19.0, "end": 30.0, "end_reason": "next_buy", "round_key": "R2"},
    ]
    try:
        validate_confirmed_rounds(rows, duration=60.0)
    except ValueError as exc:
        assert "overlap" in str(exc).lower() or "重叠" in str(exc)
    else:
        raise AssertionError("overlapping rounds accepted")


def test_validate_confirmed_rounds_rejects_start_ge_end() -> None:
    rows = [{"start": 20.0, "end": 20.0, "end_reason": "result", "round_key": "R1"}]
    with pytest.raises(ValueError, match="bounds|invalid"):
        validate_confirmed_rounds(rows, duration=60.0)


def test_validate_confirmed_rounds_rejects_end_beyond_duration() -> None:
    rows = [{"start": 10.0, "end": 70.0, "end_reason": "result", "round_key": "R1"}]
    with pytest.raises(ValueError, match="bounds|invalid"):
        validate_confirmed_rounds(rows, duration=60.0)


def test_validate_confirmed_rounds_rejects_invalid_end_reason() -> None:
    rows = [{"start": 10.0, "end": 20.0, "end_reason": "bogus", "round_key": "R1"}]
    with pytest.raises(ValueError, match="end_reason"):
        validate_confirmed_rounds(rows, duration=60.0)


def test_validate_confirmed_rounds_rejects_duplicate_round_key() -> None:
    rows = [
        {"start": 1.0, "end": 10.0, "end_reason": "result", "round_key": "R1"},
        {"start": 11.0, "end": 20.0, "end_reason": "result", "round_key": "R1"},
    ]
    with pytest.raises(ValueError, match="duplicate round_key"):
        validate_confirmed_rounds(rows, duration=60.0)


def test_build_preview_payload_returns_rel_paths_for_start_and_end() -> None:
    frames = [
        {"timestamp_sec": 10.0, "rel_path": "s.jpg"},
        {"timestamp_sec": 20.0, "rel_path": "e.jpg"},
    ]
    payload = build_preview_payload(frames, start=10.1, end=19.8)
    assert payload["start"]["rel_path"] == "s.jpg"
    assert payload["end"]["rel_path"] == "e.jpg"


def test_find_nearest_frame_picks_closest_timestamp() -> None:
    frames = [
        {"timestamp_sec": 1.0, "rel_path": "a.jpg"},
        {"timestamp_sec": 2.0, "rel_path": "b.jpg"},
        {"timestamp_sec": 5.0, "rel_path": "c.jpg"},
    ]
    hit = find_nearest_frame(frames, 2.2)
    assert hit["rel_path"] == "b.jpg"


def test_load_draft_rounds_reads_manifest(tmp_path: Path) -> None:
    payload = {
        "videos": [
            {
                "video_id": "nick",
                "video_path": "nick.mp4",
                "ground_truth": [{"start": 1.0, "end": 3.0}],
            }
        ]
    }
    (tmp_path / "round_manifest.draft.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
    assert load_draft_rounds(tmp_path) == payload


def test_merge_prefers_rounds_confirmed_and_sets_human_confirmed(tmp_path: Path) -> None:
    frame = tmp_path / "f.jpg"
    frame.write_bytes(b"jpeg")
    queue = [{
        "id": "a",
        "abs_path": str(frame),
        "video_id": "nick",
        "video_path": "nick.mp4",
        "timestamp_sec": 1.0,
        "session_id": "nick",
    }, {
        "id": "b",
        "abs_path": str(frame),
        "video_id": "nick",
        "video_path": "nick.mp4",
        "timestamp_sec": 2.0,
        "session_id": "nick",
    }, {
        "id": "c",
        "abs_path": str(frame),
        "video_id": "nick",
        "video_path": "nick.mp4",
        "timestamp_sec": 3.0,
        "session_id": "nick",
    }]
    labels = {"a": {"label": "buy"}, "b": {"label": "combat"}, "c": {"label": "result"}}
    queue_path = tmp_path / "queue.json"
    labels_path = tmp_path / "labels.json"
    queue_path.write_text(json.dumps(queue), encoding="utf-8")
    labels_path.write_text(json.dumps(labels), encoding="utf-8")
    (tmp_path / "round_manifest.draft.json").write_text(
        json.dumps({"videos": [{"video_id": "nick", "video_path": "nick.mp4",
                                "ground_truth": [{"start": 1.0, "end": 9.0}]}]}),
        encoding="utf-8",
    )
    (tmp_path / "rounds_confirmed.json").write_text(
        json.dumps({"videos": [{"video_id": "nick", "video_path": "nick.mp4",
                                "ground_truth": [{"start": 1.0, "end": 3.0,
                                                 "end_reason": "result",
                                                 "round_key": "R1"}]}]}),
        encoding="utf-8",
    )
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    out_dir = tmp_path / "out"
    result = merge_labels(
        queue_path=queue_path,
        labels_path=labels_path,
        data_dir=data_dir,
        out_dir=out_dir,
    )
    assert result["human_confirmed"] is True
    rounds = json.loads((out_dir / "round_manifest.json").read_text(encoding="utf-8"))
    assert rounds["human_confirmed"] is True
    assert rounds["videos"][0]["ground_truth"][0]["end"] == 3.0


from scripts.valorant_vision.build_hard_miss_queue import (
    build_hard_windows,
    extract_misses_from_report,
)


def test_build_hard_windows_covers_missed_round_and_end_error() -> None:
    missed = [{"round_key": "R1", "start": 100.0, "end": 140.0}]
    end_errors = [{"round_key": "R2", "gt_end": 200.0, "pred_end": 230.0}]
    windows = build_hard_windows(
        missed_rounds=missed,
        end_errors=end_errors,
        radius=8.0,
    )
    assert any(w["start"] <= 100.0 and w["end"] >= 140.0 for w in windows)
    assert any(w["start"] <= 192.0 and w["end"] >= 208.0 for w in windows)


def test_extract_misses_from_report_returns_empty_without_per_round_detail() -> None:
    report = {
        "recall": 0.625,
        "end_err_p95": 36.0,
        "ground_truth_count": 8,
        "matched": 5,
    }
    missed, end_errors = extract_misses_from_report(report)
    assert missed == []
    assert end_errors == []


def test_extract_misses_from_report_reads_explicit_lists() -> None:
    report = {
        "missed_rounds": [{"round_key": "R1", "start": 1.0, "end": 2.0}],
        "end_errors": [{"round_key": "R2", "gt_end": 10.0, "pred_end": 12.0}],
    }
    missed, end_errors = extract_misses_from_report(report)
    assert missed == report["missed_rounds"]
    assert end_errors == report["end_errors"]


def test_save_confirmed_rounds_writes_validated_file(tmp_path: Path) -> None:
    payload = {
        "videos": [
            {
                "video_id": "nick",
                "video_path": "nick.mp4",
                "duration_sec": 60.0,
                "ground_truth": [
                    {
                        "start": 1.0,
                        "end": 3.0,
                        "end_reason": "result",
                        "round_key": "R1",
                    }
                ],
            }
        ]
    }
    save_confirmed_rounds(tmp_path, payload)
    written = json.loads((tmp_path / "rounds_confirmed.json").read_text(encoding="utf-8"))
    assert written == payload
