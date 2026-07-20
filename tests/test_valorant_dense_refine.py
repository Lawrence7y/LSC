from __future__ import annotations

import pytest

from lsc.analyzer.round_detector import build_frame_evidence


def test_ocr_failure_leaves_scores_none_not_zero() -> None:
    ev = build_frame_evidence(
        timestamp=12.0,
        probs={"non_game": 0.01, "buy": 0.02, "combat": 0.9, "result": 0.05, "replay": 0.02},
        predicted_class="combat",
        timer_seconds=None,
        left_score=None,
        right_score=None,
        model_version="stub",
    )
    assert ev.left_score is None
    assert ev.right_score is None
    assert ev.timer_seconds is None


def test_dense_start_is_first_stable_combat_frame() -> None:
    from lsc.analyzer.round_detector import refine_boundary_from_sequence

    seq = [
        (0.0, "buy"), (0.1, "buy"), (0.2, "buy"),
        (0.3, "combat"), (0.4, "combat"), (0.5, "combat"),
    ]
    start = refine_boundary_from_sequence(seq, target="combat", min_stable=3, fps=10)
    assert start == 0.3


def test_end_keeps_1_5s_result_tail_unless_replay() -> None:
    from lsc.analyzer.round_detector import compute_clip_end

    assert compute_clip_end(result_event_ts=10.0, next_states=[]) == 11.5
    assert compute_clip_end(
        result_event_ts=10.0,
        next_states=[(10.8, "replay")],
    ) == 10.8


def test_no_strong_end_does_not_emit_vision_confirmed() -> None:
    from lsc.analyzer.round_detector import grade_round_confirmation

    status = grade_round_confirmation(
        start_strong=True,
        end_strong=False,
        score_confirm=False,
    )
    assert status == "pending"


def test_strong_boundaries_emit_vision_confirmed() -> None:
    from lsc.analyzer.round_detector import grade_round_confirmation

    assert grade_round_confirmation(
        start_strong=True,
        end_strong=True,
        score_confirm=True,
    ) == "vision_confirmed"
    assert grade_round_confirmation(
        start_strong=True,
        end_strong=True,
        score_confirm=False,
    ) == "vision_confirmed"
    assert grade_round_confirmation(
        start_strong=True,
        end_strong=False,
        score_confirm=True,
    ) == "pending"


def test_low_confidence_argmax_is_unknown() -> None:
    import numpy as np
    from lsc.analyzer.round_detector import _predict_class_from_probs

    label = _predict_class_from_probs(
        np.asarray([0.20, 0.21, 0.20, 0.20, 0.19], dtype=np.float32),
        thresholds={"stable_prob": 0.55, "high_prob": 0.8},
    )

    assert label == "unknown"


def test_extract_frames_raises_on_ffmpeg_failure(tmp_path, monkeypatch) -> None:
    from types import SimpleNamespace
    from lsc.analyzer.round_detector import extract_frames_cancellable
    import lsc.utils.cancellable_ffmpeg as cancellable

    class _FailedFFmpeg:
        def __init__(self, *_args, **_kwargs):
            pass

        def start(self) -> None:
            pass

        def wait(self, timeout_sec: float):
            del timeout_sec
            return SimpleNamespace(returncode=1, stdout=b"", stderr=b"decode failed")

    monkeypatch.setattr(cancellable, "CancellableFFmpeg", _FailedFFmpeg)
    video = tmp_path / "broken.mp4"
    video.write_bytes(b"broken")

    with pytest.raises(RuntimeError, match="hybrid frame extract failed"):
        extract_frames_cancellable(
            str(video),
            start_sec=0.0,
            end_sec=10.0,
            fps=1.0,
            ffmpeg_path="ffmpeg",
        )


def test_digit_anchors_ignore_timer_digits(monkeypatch) -> None:
    import numpy as np
    import lsc.analyzer.ocr_detector as ocr_detector
    from lsc.analyzer.round_detector import read_top_digit_anchors

    def _box(x: float):
        return [[x, 0.0], [x + 10.0, 0.0], [x + 10.0, 10.0], [x, 10.0]]

    def _ocr(_path: str):
        return ([
            (_box(90.0), "1:23", 0.99),
            (_box(10.0), "5", 0.99),
            (_box(170.0), "4", 0.99),
        ], None)

    monkeypatch.setattr(ocr_detector, "_get_ocr", lambda: _ocr)

    timer, left, right = read_top_digit_anchors(
        np.zeros((200, 200, 3), dtype=np.uint8)
    )

    assert timer == 83.0
    assert (left, right) == (5, 4)
