from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts" / "valorant_vision"
sys.path.insert(0, str(SCRIPTS))

import pytest

from apply_interval_labels import apply_interval_candidates, export_manifest, label_at
from build_label_queue import VideoSession, build_queue, choose_split
from densify_boundaries import dense_timestamps, densify_queue, transition_centers
from serve_label_ui import build_manifest_rows, resolve_frame_path, validate_labels


def test_choose_split_uses_temporal_blocks_and_gap() -> None:
    assert choose_split(100.0, 1000.0, gap_sec=30.0) == "train"
    assert choose_split(650.0, 1000.0, gap_sec=30.0) is None
    assert choose_split(700.0, 1000.0, gap_sec=30.0) == "val"
    assert choose_split(800.0, 1000.0, gap_sec=30.0) is None
    assert choose_split(900.0, 1000.0, gap_sec=30.0) == "test"


def test_build_queue_keeps_both_sources_in_each_split(tmp_path: Path) -> None:
    sessions = []
    for source in ("pov", "broadcast"):
        frame_dir = tmp_path / source
        frame_dir.mkdir()
        for index in (1, 176, 226):
            (frame_dir / f"frame_{index:06d}.jpg").write_bytes(b"jpg")
        sessions.append(VideoSession(source, f"{source}.mp4", frame_dir, source, source))

    rows = build_queue(
        sessions,
        {"pov": 1000.0, "broadcast": 1000.0},
        frame_root=tmp_path,
        interval_sec=4.0,
    )

    assert {(row["source_type"], row["split"]) for row in rows} == {
        ("pov", "train"), ("pov", "val"), ("pov", "test"),
        ("broadcast", "train"), ("broadcast", "val"), ("broadcast", "test"),
    }


def test_label_at_uses_half_open_intervals() -> None:
    intervals = [
        {"start_sec": 0.0, "end_sec": 10.0, "label": "buy", "notes": "barrier"},
        {"start_sec": 10.0, "end_sec": 20.0, "label": "combat", "notes": "live"},
    ]
    assert label_at(intervals, 9.999)["label"] == "buy"
    assert label_at(intervals, 10.0)["label"] == "combat"
    assert label_at(intervals, 20.0) is None


def test_interval_candidates_never_overwrite_human_label() -> None:
    queue = [{"id": "v_1", "video_id": "v", "timestamp_sec": 4.0}]
    labels = {"v_1": {"label": "result", "annotator": "human"}}
    intervals = {"v": [{"start_sec": 0.0, "end_sec": 8.0, "label": "combat"}]}

    apply_interval_candidates(queue, labels, intervals)

    assert labels["v_1"]["label"] == "result"
    assert labels["v_1"]["annotator"] == "human"


def test_export_manifest_skips_interval_candidates(tmp_path: Path) -> None:
    queue = [{
        "id": "v_1", "video_id": "v", "video_path": "v.mp4",
        "timestamp_sec": 4.0, "split": "train", "source_type": "pov",
        "session_id": "s",
    }]
    labels = {"v_1": {"label": "combat", "annotator": "interval_candidate_v1"}}
    out = tmp_path / "manifest.jsonl"
    assert export_manifest(queue, labels, out) == 0
    assert out.read_text(encoding="utf-8") == ""


def test_resolve_frame_path_rejects_escape(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    with pytest.raises(ValueError, match="outside frame root"):
        resolve_frame_path(root, "../secret.jpg")


def test_validate_labels_rejects_unknown_class() -> None:
    with pytest.raises(ValueError, match="invalid label"):
        validate_labels({"v_1": {"label": "loading"}})


def test_transition_centers_never_cross_video_or_split() -> None:
    queue = [
        {"id": "a1", "video_id": "a", "split": "train", "timestamp_sec": 4.0},
        {"id": "a2", "video_id": "a", "split": "train", "timestamp_sec": 8.0},
        {"id": "a3", "video_id": "a", "split": "val", "timestamp_sec": 700.0},
    ]
    labels = {
        "a1": {"label": "buy", "annotator": "human"},
        "a2": {"label": "combat", "annotator": "human"},
        "a3": {"label": "result", "annotator": "human"},
    }
    assert transition_centers(queue, labels) == [("a", "train", 6.0)]


def test_dense_timestamps_are_clamped_and_deduplicated() -> None:
    assert dense_timestamps([1.0, 2.0], duration=4.0, radius=1.0, step=0.5) == [
        0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0,
    ]


@pytest.mark.parametrize(("radius", "step"), [(-0.1, 0.5), (1.0, 0.0), (1.0, -0.5)])
def test_dense_timestamps_rejects_invalid_sampling(radius: float, step: float) -> None:
    with pytest.raises(ValueError, match="radius|step"):
        dense_timestamps([1.0], duration=4.0, radius=radius, step=step)


def test_densify_dry_run_does_not_mutate_queue(tmp_path: Path) -> None:
    queue = [
        {"id": "v_1", "video_id": "v", "split": "train", "timestamp_sec": 4.0},
        {"id": "v_2", "video_id": "v", "split": "train", "timestamp_sec": 8.0},
    ]
    labels = {
        "v_1": {"label": "buy", "annotator": "human"},
        "v_2": {"label": "combat", "annotator": "human"},
    }
    before = [dict(item) for item in queue]

    added = densify_queue(
        queue,
        labels,
        [VideoSession("v", "v.mp4", tmp_path / "coarse", "pov", "s")],
        output_root=tmp_path / "annotations" / "frames_dense",
        durations={"v": 12.0},
        radius=1.0,
        step=0.5,
        ffmpeg="ffmpeg",
        timeout_sec=1.0,
        dry_run=True,
    )

    assert added == 5
    assert queue == before


def test_build_queue_applies_explicit_frame_timestamp_offset(tmp_path: Path) -> None:
    frame_dir = tmp_path / "pov"
    frame_dir.mkdir()
    (frame_dir / "frame_000001.jpg").write_bytes(b"jpg")
    (frame_dir / "frame_000002.jpg").write_bytes(b"jpg")
    session = VideoSession(
        "pov",
        "pov.mp4",
        frame_dir,
        "pov",
        "pov",
        timestamp_offset_sec=2.0,
    )

    rows = build_queue(
        [session],
        {"pov": 10.0},
        frame_root=tmp_path,
        interval_sec=4.0,
        gap_sec=0.0,
    )

    assert [row["timestamp_sec"] for row in rows] == [2.0, 6.0]
    assert [row["id"] for row in rows] == ["pov_0000002000", "pov_0000006000"]


def test_manifest_export_contains_only_human_reviewed_rows() -> None:
    queue = [{
        "id": "v_1", "video_id": "v", "video_path": "v.mp4",
        "timestamp_sec": 4.0, "split": "train", "source_type": "pov",
        "session_id": "s",
    }]
    candidate = {"v_1": {"label": "combat", "annotator": "interval_candidate_v1"}}
    human = {"v_1": {"label": "combat", "annotator": "human", "notes": "checked"}}
    assert build_manifest_rows(queue, candidate) == []
    assert build_manifest_rows(queue, human)[0]["notes"] == "checked"
