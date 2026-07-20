from __future__ import annotations

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
