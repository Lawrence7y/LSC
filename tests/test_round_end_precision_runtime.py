from lsc.analyzer.round_detector import (
    _confidence_from_probs,
    _is_strong_confidence,
    grade_round_confirmation,
    merge_listed_rounds,
)


def test_result_end_uses_result_high_prob_threshold() -> None:
    thresholds = {"stable_prob": 0.55, "high_prob": 0.8, "result_high_prob": 0.88}
    probs = {"result": 0.85, "combat": 0.10, "buy": 0.02, "non_game": 0.02, "replay": 0.01}
    conf = _confidence_from_probs(probs, "result", thresholds=thresholds)
    assert conf >= 0.8
    assert _is_strong_confidence(conf, thresholds) is True
    assert _is_strong_confidence(conf, thresholds, label="result") is False


def test_strong_result_end_passes_result_high_prob_threshold() -> None:
    thresholds = {"stable_prob": 0.55, "high_prob": 0.8, "result_high_prob": 0.88}
    probs = {"result": 0.90, "combat": 0.05, "buy": 0.02, "non_game": 0.02, "replay": 0.01}
    conf = _confidence_from_probs(probs, "result", thresholds=thresholds)
    assert _is_strong_confidence(conf, thresholds, label="result") is True


def test_weak_result_end_stays_pending_with_score_hint() -> None:
    assert grade_round_confirmation(
        start_strong=True,
        end_strong=False,
        score_confirm=True,
    ) == "pending"


def test_strong_start_and_result_end_grades_vision_confirmed() -> None:
    thresholds = {"stable_prob": 0.55, "high_prob": 0.8, "result_high_prob": 0.88}
    end_probs = {"result": 0.90, "combat": 0.05, "buy": 0.02, "non_game": 0.02, "replay": 0.01}
    end_conf = _confidence_from_probs(end_probs, "result", thresholds=thresholds)
    assert _is_strong_confidence(end_conf, thresholds, label="result") is True
    assert grade_round_confirmation(
        start_strong=True,
        end_strong=True,
        score_confirm=False,
    ) == "vision_confirmed"


def test_merge_listed_rounds_merges_overlap_and_near_gap() -> None:
    rows = [
        {"start": 10.0, "end": 20.0, "confirm_status": "pending", "listed": True},
        {"start": 18.0, "end": 30.0, "confirm_status": "vision_confirmed", "listed": True},
        {"start": 100.0, "end": 110.0, "confirm_status": "pending", "listed": True},
    ]
    merged = merge_listed_rounds(rows, iou_threshold=0.5, max_gap_sec=3.0)
    assert len(merged) == 2
    assert merged[0]["start"] == 10.0 and merged[0]["end"] == 30.0
    assert merged[0]["confirm_status"] == "vision_confirmed"


def test_merge_listed_rounds_skips_far_apart_rounds() -> None:
    rows = [
        {"start": 10.0, "end": 20.0, "confirm_status": "pending", "listed": True},
        {"start": 30.0, "end": 40.0, "confirm_status": "pending", "listed": True},
    ]
    merged = merge_listed_rounds(rows, iou_threshold=0.5, max_gap_sec=3.0)
    assert len(merged) == 2
    assert merged[0]["start"] == 10.0 and merged[0]["end"] == 20.0
    assert merged[1]["start"] == 30.0 and merged[1]["end"] == 40.0


def test_merge_listed_rounds_merges_nested_rounds() -> None:
    rows = [
        {"start": 10.0, "end": 50.0, "confirm_status": "pending", "listed": True},
        {"start": 20.0, "end": 30.0, "confirm_status": "vision_confirmed", "listed": True},
    ]
    merged = merge_listed_rounds(rows, iou_threshold=0.5, max_gap_sec=3.0)
    assert len(merged) == 1
    assert merged[0]["start"] == 10.0 and merged[0]["end"] == 50.0
    assert merged[0]["confirm_status"] == "vision_confirmed"


def test_merge_listed_rounds_merges_near_touching_gap() -> None:
    rows = [
        {"start": 10.0, "end": 20.0, "confirm_status": "pending", "listed": True},
        {"start": 22.0, "end": 30.0, "confirm_status": "pending", "listed": True},
    ]
    merged = merge_listed_rounds(rows, iou_threshold=0.5, max_gap_sec=3.0)
    assert len(merged) == 1
    assert merged[0]["start"] == 10.0 and merged[0]["end"] == 30.0


def test_merge_listed_rounds_flags_long_duration_audit() -> None:
    rows = [
        {"start": 10.0, "end": 100.0, "confirm_status": "pending", "listed": True},
        {"start": 98.0, "end": 170.0, "confirm_status": "pending", "listed": True},
    ]
    merged = merge_listed_rounds(rows, iou_threshold=0.5, max_gap_sec=3.0)
    assert len(merged) == 1
    assert merged[0]["merge_audit"] == "long_duration"
