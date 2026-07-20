from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from lsc.analyzer.round_detector import detect_valorant_rounds_hybrid

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "valorant_vision"

_CLASS_INDEX = {
    "non_game": 0,
    "buy": 1,
    "combat": 2,
    "result": 3,
    "replay": 4,
}


def _make_class_frame(cls: str) -> np.ndarray:
    img = np.zeros((240, 320, 3), dtype=np.uint8)
    img[0, 0, 0] = _CLASS_INDEX[cls]
    return img


def _class_at_time(ts: float) -> str:
    if ts < 2.5:
        return "buy"
    if ts < 39.5:
        return "combat"
    if ts < 42.0:
        return "result"
    return "combat"


def _fake_extract(
    video_path: str,
    *,
    start_sec: float,
    end_sec: float,
    fps: float,
    ffmpeg_path: str,
    cancel_check=None,
    overlap_sec: float = 2.0,
) -> list[tuple[float, np.ndarray]]:
    del video_path, ffmpeg_path, overlap_sec
    if cancel_check and cancel_check():
        from lsc.utils.cancellable_ffmpeg import FFmpegCancelled

        raise FFmpegCancelled("cancelled")
    frames: list[tuple[float, np.ndarray]] = []
    step = 1.0 / max(fps, 0.1)
    t = start_sec
    while t <= end_sec + 1e-6:
        frames.append((t, _make_class_frame(_class_at_time(t))))
        t += step
    return frames


class _EncodedClassifier:
    model_version = "test-v1"

    @property
    def thresholds(self) -> dict[str, float]:
        return {"stable_prob": 0.55, "high_prob": 0.8}

    def load(self) -> None:
        return None

    def predict_batch(self, frames_bgr: list[np.ndarray]) -> np.ndarray:
        rows = []
        for frame in frames_bgr:
            idx = int(frame[0, 0, 0])
            row = np.full(5, 0.02, dtype=np.float32)
            row[idx] = 0.92
            rows.append(row)
        return np.stack(rows)


def _fake_anchors(frame_bgr: np.ndarray) -> tuple[float | None, int | None, int | None]:
    if int(frame_bgr[0, 0, 0]) == _CLASS_INDEX["result"]:
        return 99.0, 1, 0
    return None, None, None


@pytest.fixture
def hybrid_deps():
    return {
        "classifier": _EncodedClassifier(),
        "extract_fn": _fake_extract,
        "read_anchors_fn": _fake_anchors,
    }


def test_hybrid_detect_sets_boundary_source_and_round_key(tmp_path, hybrid_deps) -> None:
    video = tmp_path / "x.mp4"
    video.write_bytes(b"fake")

    rounds = detect_valorant_rounds_hybrid(
        str(video),
        time_range=(0.0, 60.0),
        model_dir=FIXTURE_DIR,
        cancel_check=lambda: False,
        session_id="sess1",
        **hybrid_deps,
    )

    assert rounds
    assert rounds[0]["boundary_source"] == "valorant_hybrid_v1"
    assert rounds[0]["round_key"] == "hybrid-sess1-3"
    assert rounds[0]["phase"] == "combat"
    assert rounds[0]["start_by"] == "model_buy_exit"
    assert rounds[0]["end_by"] in {"model_result", "model_score"}
    assert "boundary_evidence" in rounds[0]
    assert "model_version" in rounds[0]


def test_dense_refine_does_not_change_round_key(tmp_path, hybrid_deps) -> None:
    video = tmp_path / "x.mp4"
    video.write_bytes(b"fake")

    rounds = detect_valorant_rounds_hybrid(
        str(video),
        time_range=(0.0, 60.0),
        session_id="sess1",
        **hybrid_deps,
    )

    assert len(rounds) == 1
    assert rounds[0]["round_key"] == "hybrid-sess1-3"
    assert rounds[0]["start"] != 3.0 or rounds[0]["end"] != rounds[0]["start"]


def test_discarded_and_open_rounds_not_in_output(tmp_path, hybrid_deps) -> None:
    video = tmp_path / "x.mp4"
    video.write_bytes(b"fake")

    def combat_only_extract(
        video_path: str,
        *,
        start_sec: float,
        end_sec: float,
        fps: float,
        ffmpeg_path: str,
        cancel_check=None,
        overlap_sec: float = 2.0,
    ) -> list[tuple[float, np.ndarray]]:
        del video_path, ffmpeg_path, overlap_sec
        frames = []
        step = 1.0 / max(fps, 0.1)
        t = start_sec
        while t <= end_sec + 1e-6:
            frames.append((t, _make_class_frame("combat")))
            t += step
        return frames

    rounds = detect_valorant_rounds_hybrid(
        str(video),
        time_range=(0.0, 200.0),
        extract_fn=combat_only_extract,
        classifier=hybrid_deps["classifier"],
        read_anchors_fn=hybrid_deps["read_anchors_fn"],
    )
    assert rounds == []


def test_cancel_check_returns_empty(tmp_path, hybrid_deps) -> None:
    video = tmp_path / "x.mp4"
    video.write_bytes(b"fake")

    cancelled = {"value": False}

    def cancel_after_first(*_args, **_kwargs):
        if cancelled["value"]:
            from lsc.utils.cancellable_ffmpeg import FFmpegCancelled

            raise FFmpegCancelled("cancelled")
        cancelled["value"] = True
        return _fake_extract(*_args, **_kwargs)

    rounds = detect_valorant_rounds_hybrid(
        str(video),
        time_range=(0.0, 60.0),
        cancel_check=lambda: True,
        extract_fn=cancel_after_first,
        classifier=hybrid_deps["classifier"],
        read_anchors_fn=hybrid_deps["read_anchors_fn"],
    )
    assert rounds == []
