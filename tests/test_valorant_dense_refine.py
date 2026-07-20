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
    ) == "vision_confirmed"
