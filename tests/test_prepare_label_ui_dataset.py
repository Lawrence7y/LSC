from __future__ import annotations

import json

from scripts.valorant_vision.prepare_label_ui_dataset import build_queue
from scripts.valorant_vision.ingest_recording_frames import _video_id


def test_build_queue_keeps_dataset_labels_as_review_hints(tmp_path) -> None:
    root = tmp_path / "pov"
    frame = root / "train" / "combat" / "frame.jpg"
    frame.parent.mkdir(parents=True)
    frame.write_bytes(b"jpeg")
    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text(
        json.dumps(
            {
                "video_id": "pov_1",
                "video_path": None,
                "frame_path": str(frame),
                "timestamp_sec": 12.0,
                "label": "combat",
                "coarse_label": "combat",
                "coarse_confidence": 0.812345,
                "split": "train",
                "source_type": "pov",
                "session_id": "session_1",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    rows = build_queue(manifest, root)

    assert len(rows) == 1
    assert rows[0]["rel_path"] == "train/combat/frame.jpg"
    assert rows[0]["current_label"] == "combat"
    assert rows[0]["suggested_label"] == "combat"
    assert rows[0]["coarse_label"] == "combat"
    assert rows[0]["coarse_confidence"] == 0.812345
    assert rows[0]["video_path"] is None


def test_build_queue_deduplicates_identical_frames(tmp_path) -> None:
    root = tmp_path / "pov"
    first = root / "train" / "combat" / "first.jpg"
    second = root / "train" / "combat" / "rarex_first.jpg"
    first.parent.mkdir(parents=True)
    first.write_bytes(b"same-frame")
    second.write_bytes(b"same-frame")
    manifest = tmp_path / "manifest.jsonl"
    rows = []
    for frame in (first, second):
        rows.append(
            {
                "video_id": "pov_1",
                "video_path": None,
                "frame_path": str(frame),
                "timestamp_sec": len(rows) + 1,
                "label": "combat",
                "split": "train",
                "source_type": "pov",
                "session_id": "session_1",
            }
        )
    manifest.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    queue = build_queue(manifest, root)

    assert len(queue) == 1
    assert queue[0]["duplicate_count"] == 2
    assert queue[0]["original_labels"] == ["combat"]


def test_build_queue_surfaces_conflicting_labels_for_same_frame(tmp_path) -> None:
    root = tmp_path / "pov"
    first = root / "train" / "combat" / "first.jpg"
    second = root / "train" / "non_game" / "same.jpg"
    first.parent.mkdir(parents=True)
    second.parent.mkdir(parents=True)
    first.write_bytes(b"same-frame")
    second.write_bytes(b"same-frame")
    manifest = tmp_path / "manifest.jsonl"
    rows = []
    for index, (frame, label) in enumerate(((first, "combat"), (second, "non_game")), 1):
        rows.append(
            {
                "video_id": "pov_1",
                "video_path": None,
                "frame_path": str(frame),
                "timestamp_sec": float(index),
                "label": label,
                "split": "train",
                "source_type": "pov",
                "session_id": "session_1",
            }
        )
    manifest.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    queue = build_queue(manifest, root)

    assert len(queue) == 1
    assert queue[0]["priority"] == "dataset_conflict"
    assert queue[0]["original_labels"] == ["combat", "non_game"]


def test_build_queue_accepts_unlabeled_recording_manifest(tmp_path) -> None:
    root = tmp_path / "broadcast"
    frame = root / "train" / "incoming" / "frame.jpg"
    frame.parent.mkdir(parents=True)
    frame.write_bytes(b"new-frame")
    manifest = tmp_path / "incoming.jsonl"
    manifest.write_text(
        json.dumps(
            {
                "video_id": "edg_1",
                "video_path": "D:/recordings/edg.mp4",
                "frame_path": str(frame),
                "timestamp_sec": 4.0,
                "label": None,
                "split": "train",
                "source_type": "broadcast",
                "session_id": "edg_session_1",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    queue = build_queue(manifest, root, allow_unlabeled=True)

    assert len(queue) == 1
    assert queue[0]["priority"] == "new_recording"
    assert queue[0]["current_label"] == ""


def test_recording_video_id_is_stable_and_safe() -> None:
    assert _video_id(
        __import__("pathlib").Path("2026-09-07_12-25-51_至_2026-09-07_13-22-36.mp4")
    ) == "edg_20260907_122551"
