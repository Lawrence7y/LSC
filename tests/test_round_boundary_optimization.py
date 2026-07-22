from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from lsc.analyzer.round_detector import grade_round_confirmation
from scripts.valorant_vision.build_round_boundary_queue import (
    _extract_frames,
    build_sample_times,
    validate_round_rows,
    validate_sample_times,
)
from scripts.valorant_vision.merge_round_boundary_labels import (
    merge_labels,
    split_by_time_block,
    validate_labels,
)
from scripts.valorant_vision.serve_label_ui import default_root, resolve_paths
from scripts.valorant_vision.train_export import experiment_metadata


def test_weak_model_result_stays_pending_even_with_score_hint() -> None:
    assert grade_round_confirmation(
        start_strong=False,
        end_strong=False,
        score_confirm=True,
    ) == "pending"


def test_sample_times_cover_boundaries_gaps_and_video_edges() -> None:
    rows = [
        {"start": 10.0, "end": 20.0},
        {"start": 40.0, "end": 50.0},
    ]

    times = build_sample_times(rows, duration=60.0, radius=1.0, fps=1.0)

    assert times == sorted(set(times))
    assert 0.0 in times
    assert 60.0 in times
    for boundary in (10.0, 20.0, 40.0, 50.0):
        assert boundary in times
        assert boundary - 1.0 in times
        assert boundary + 1.0 in times
    assert any(20.0 < t < 40.0 for t in times)
    assert all(value == round(value, 3) for value in times)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_sample_times_are_rejected(bad: float) -> None:
    with pytest.raises(ValueError, match="finite sample timestamp"):
        validate_sample_times([bad], duration=10.0)


def test_non_three_decimal_duration_keeps_last_timestamp_in_bounds() -> None:
    times = build_sample_times([], duration=60.9999, radius=1.0, fps=1.0)

    assert times[-1] == 60.999
    assert times[-1] <= 60.9999
    assert times[-1] == round(times[-1], 3)


def test_gap_is_sampled_at_fps_step_without_missing_intervals() -> None:
    times = build_sample_times(
        [{"start": 100.0, "end": 140.0}, {"start": 220.0, "end": 250.0}],
        duration=300.0,
        radius=0.0,
        fps=2.0,
    )

    assert all(round(140.0 + i * 0.5, 3) in times for i in range(161))


def test_extract_frames_validates_times_against_video_duration(tmp_path, monkeypatch) -> None:
    class FakeCapture:
        def __init__(self):
            self.released = False

        def isOpened(self):
            return True

        def get(self, prop):
            return {5: 10.0, 7: 100.0}.get(prop, 0.0)

        def set(self, prop, value):
            return True

        def read(self):
            return True, object()

        def release(self):
            self.released = True

    capture = FakeCapture()

    def fake_imwrite(path, _frame):
        with open(path, "wb") as handle:
            return handle.write(b"jpeg") > 0

    fake_cv2 = SimpleNamespace(
        CAP_PROP_FPS=5,
        CAP_PROP_FRAME_COUNT=7,
        CAP_PROP_POS_MSEC=9,
        VideoCapture=lambda _path: capture,
        imwrite=fake_imwrite,
    )
    monkeypatch.setitem(sys.modules, "cv2", fake_cv2)

    with pytest.raises(ValueError, match="bounds"):
        _extract_frames(tmp_path / "video.mp4", [10.001], tmp_path / "frames")
    assert capture.released


def test_extract_frames_resolves_relative_output_path(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    class FakeCapture:
        def isOpened(self):
            return True

        def get(self, prop):
            return {5: 10.0, 7: 100.0}.get(prop, 0.0)

        def set(self, _prop, _value):
            return True

        def read(self):
            return True, object()

        def release(self):
            return None

    def fake_imwrite(path, _frame):
        with open(path, "wb") as handle:
            return handle.write(b"jpeg") > 0

    fake_cv2 = SimpleNamespace(
        CAP_PROP_FPS=5,
        CAP_PROP_FRAME_COUNT=7,
        CAP_PROP_POS_MSEC=9,
        VideoCapture=lambda _path: FakeCapture(),
        imwrite=fake_imwrite,
    )
    monkeypatch.setitem(sys.modules, "cv2", fake_cv2)

    records = _extract_frames(Path("video.mp4"), [1.0], Path("frames"))

    assert records[0]["rel_path"] == "frame_000001.jpg"
    assert records[0]["abs_path"] == str((tmp_path / "frames/frame_000001.jpg").resolve())


def test_extract_frames_reports_decode_failure_and_releases_capture(tmp_path, monkeypatch) -> None:
    class FakeCapture:
        def __init__(self):
            self.released = False

        def isOpened(self):
            return True

        def get(self, prop):
            return {5: 10.0, 7: 100.0}.get(prop, 0.0)

        def set(self, _prop, _value):
            return True

        def read(self):
            return False, None

        def release(self):
            self.released = True

    capture = FakeCapture()
    fake_cv2 = SimpleNamespace(
        CAP_PROP_FPS=5,
        CAP_PROP_FRAME_COUNT=7,
        CAP_PROP_POS_MSEC=9,
        VideoCapture=lambda _path: capture,
        imwrite=lambda _path, _frame: True,
    )
    monkeypatch.setitem(sys.modules, "cv2", fake_cv2)

    with pytest.raises(RuntimeError, match="decode failed"):
        _extract_frames(
            tmp_path / "video.mp4",
            [1.0],
            tmp_path / "frames",
            duration=10.0,
        )
    assert capture.released


def test_invalid_round_bounds_are_rejected() -> None:
    with pytest.raises(ValueError, match=r"bounds|end"):
        validate_round_rows([{"start": -1.0, "end": 2.0}], duration=10.0)

    with pytest.raises(ValueError, match=r"bounds|end"):
        validate_round_rows([{"start": 8.0, "end": 11.0}], duration=10.0)


def test_overlapping_or_unsorted_round_rows_are_rejected() -> None:
    with pytest.raises(ValueError, match="overlap"):
        validate_round_rows(
            [{"start": 1.0, "end": 5.0}, {"start": 4.0, "end": 8.0}],
            duration=10.0,
        )

    with pytest.raises(ValueError, match="sorted"):
        validate_round_rows(
            [{"start": 5.0, "end": 6.0}, {"start": 1.0, "end": 2.0}],
            duration=10.0,
        )


def test_invalid_sample_times_are_rejected() -> None:
    with pytest.raises(ValueError, match="bounds"):
        validate_sample_times([0.0, 10.001], duration=10.0)
    with pytest.raises(ValueError, match="duplicate"):
        validate_sample_times([0.0, 1.0, 1.0], duration=10.0)


def test_split_by_time_block_keeps_one_session_out_of_both_splits() -> None:
    rows = [{"timestamp_sec": t, "video_id": "nick"} for t in (1, 2, 3, 4, 5, 6)]
    train, val = split_by_time_block(rows, validation_fraction=1 / 3)
    assert {r["video_id"] for r in train} == {"nick"}
    assert {r["video_id"] for r in val} == {"nick"}
    assert max(r["timestamp_sec"] for r in train) < min(r["timestamp_sec"] for r in val)


def test_validate_labels_rejects_unknown_label_and_missing_frame(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="label"):
        validate_labels([
            {
                "id": "x",
                "label": "unknown",
                "abs_path": str(tmp_path / "missing.jpg"),
                "video_id": "nick",
                "timestamp_sec": 1.0,
            }
        ])

    missing = tmp_path / "missing.jpg"
    with pytest.raises(ValueError, match="frame"):
        validate_labels([
            {
                "id": "y",
                "label": "buy",
                "abs_path": str(missing),
                "video_id": "nick",
                "timestamp_sec": 2.0,
            }
        ])


def test_merge_labels_refuses_blind_or_test_out_dir(tmp_path: Path) -> None:
    queue_path = tmp_path / "queue.json"
    labels_path = tmp_path / "labels.json"
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    queue_path.write_text("[]", encoding="utf-8")
    labels_path.write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="blind/test"):
        merge_labels(
            queue_path=queue_path,
            labels_path=labels_path,
            data_dir=data_dir,
            out_dir=tmp_path / "blind" / "out",
        )


def test_merge_labels_writes_manifests_and_train_val_layout(tmp_path: Path) -> None:
    frame_a = tmp_path / "frame_a.jpg"
    frame_b = tmp_path / "frame_b.jpg"
    frame_c = tmp_path / "frame_c.jpg"
    for path in (frame_a, frame_b, frame_c):
        path.write_bytes(b"jpeg")

    queue = [
        {
            "id": "a",
            "abs_path": str(frame_a),
            "video_id": "nick",
            "video_path": "nick.mp4",
            "timestamp_sec": 1.0,
            "session_id": "nick",
        },
        {
            "id": "b",
            "abs_path": str(frame_b),
            "video_id": "nick",
            "video_path": "nick.mp4",
            "timestamp_sec": 2.0,
            "session_id": "nick",
        },
        {
            "id": "c",
            "abs_path": str(frame_c),
            "video_id": "nick",
            "video_path": "nick.mp4",
            "timestamp_sec": 3.0,
            "session_id": "nick",
        },
    ]
    labels = {
        "a": {"label": "buy"},
        "b": {"label": "combat"},
        "c": {"label": "result"},
    }
    queue_path = tmp_path / "queue.json"
    labels_path = tmp_path / "labels.json"
    queue_path.write_text(json.dumps(queue), encoding="utf-8")
    labels_path.write_text(json.dumps(labels), encoding="utf-8")
    (tmp_path / "round_manifest.draft.json").write_text(
        json.dumps({
            "videos": [{
                "video_id": "nick",
                "video_path": "nick.mp4",
                "ground_truth": [{"start": 1.0, "end": 3.0}],
            }]
        }),
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

    assert result["train_count"] == 2
    assert result["val_count"] == 1
    assert result["human_confirmed"] is False
    boundary = json.loads((out_dir / "boundary_dataset_manifest.json").read_text(encoding="utf-8"))
    assert boundary["train_count"] == 2
    assert boundary["val_count"] == 1
    assert boundary["label_counts"]["buy"] == 1
    assert "queue_sha256" in boundary["input_hashes"]
    assert boundary["ground_truth_source"] == "round_manifest.draft.json"
    rounds = json.loads((out_dir / "round_manifest.json").read_text(encoding="utf-8"))
    assert rounds["human_confirmed"] is False
    assert rounds["videos"][0]["ground_truth"][0]["end"] == 3.0
    assert (out_dir / "train" / "buy").is_dir()
    assert (out_dir / "val" / "result").is_dir()
    assert not (out_dir / "blind").exists()
    assert not (out_dir / "test").exists()


def test_serve_label_ui_default_root_and_root_override() -> None:
    assert default_root() == Path.home() / "LSC" / "datasets" / "valorant_phase" / "annotate"
    custom = Path("/tmp/custom-annotate")
    paths = resolve_paths(custom)
    assert paths.root == custom.resolve()
    assert paths.queue == custom.resolve() / "queue.json"
    assert paths.labels == custom.resolve() / "labels.json"
    assert paths.manifest == custom.resolve() / "manifest_labeled.jsonl"


def test_experiment_metadata_contains_seed_and_dataset_digest() -> None:
    meta = experiment_metadata(seed=20260722, train_count=10, val_count=4, digest="abc")
    assert meta == {
        "seed": 20260722,
        "train_count": 10,
        "val_count": 4,
        "dataset_digest": "abc",
    }


def test_eval_codex_broadcast_accepts_annotation_dir_model_dir_and_output() -> None:
    from scripts.valorant_vision.eval_codex_broadcast import parse_args

    args = parse_args(
        [
            "--annotation-dir",
            "/tmp/annotations",
            "--model-dir",
            "/tmp/models",
            "--output",
            "/tmp/report.json",
            "--split",
            "test",
        ]
    )
    assert args.annotation_dir == Path("/tmp/annotations")
    assert args.model_dir == Path("/tmp/models")
    assert args.output == Path("/tmp/report.json")
    assert args.split == "test"
