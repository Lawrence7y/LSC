"""回合时钟预测器单测。"""
from __future__ import annotations

from lsc.analyzer.phase_scheduler import RoundPhase, get_profile
from lsc.analyzer.round_clock_predictor import predict_round_clock


def test_buy_clock_standard_30s_wake() -> None:
    cfg = get_profile(None)
    assert cfg.buy_duration_sec == 30.0
    assert cfg.buy_wake_early_sec == 8.0
    # buy_sleep ≈ 30 - 8 = 22（统一保守档）
    assert cfg.buy_sleep_sec == 22.0

    pred = predict_round_clock(
        RoundPhase.BUY,
        cfg,
        phase_anchor_sec=100.0,
        now_sec=118.0,  # 买枪进行 18s，未到 wake(122)
        pistol_round=False,
    )
    assert pred.buy_expected_end == 130.0
    assert pred.predicted_wake_at == 122.0
    assert pred.in_dense_window is False
    assert pred.predicted_phase == RoundPhase.BUY

    pred2 = predict_round_clock(
        RoundPhase.BUY,
        cfg,
        phase_anchor_sec=100.0,
        now_sec=123.0,
        pistol_round=False,
    )
    assert pred2.in_dense_window is True
    assert pred2.predicted_phase == RoundPhase.PRE_COMBAT


def test_pistol_buy_uses_45s() -> None:
    cfg = get_profile(None)
    pred = predict_round_clock(
        RoundPhase.BUY,
        cfg,
        phase_anchor_sec=0.0,
        now_sec=10.0,
        pistol_round=True,
    )
    assert pred.buy_expected_end == 45.0
    assert pred.predicted_wake_at == 37.0  # 45 - 8


def test_intermission_prediction_not_dense() -> None:
    cfg = get_profile(None)
    pred = predict_round_clock(
        RoundPhase.INTERMISSION,
        cfg,
        phase_anchor_sec=500.0,
        now_sec=560.0,
    )
    assert pred.in_dense_window is False
    assert pred.detail == "intermission_wait"


def test_post_combat_adds_post_round_pad() -> None:
    cfg = get_profile(None)
    pred = predict_round_clock(
        RoundPhase.POST_COMBAT,
        cfg,
        phase_anchor_sec=200.0,
        now_sec=210.0,
        combat_end_hint_sec=205.0,
    )
    assert pred.post_expected_at == 210.0  # 205 + 5
    assert pred.in_dense_window is True


def test_combat_near_soft_end_opens_dense_window() -> None:
    """交战接近典型 80s 收尾窗时必须 in_dense_window，供调度加密 OCR。"""
    cfg = get_profile(None)
    pred_early = predict_round_clock(
        RoundPhase.COMBAT,
        cfg,
        phase_anchor_sec=100.0,
        now_sec=140.0,  # 交战仅 40s，未到 wake(175)
        combat_start_sec=100.0,
    )
    assert pred_early.in_dense_window is False
    assert pred_early.predicted_phase == RoundPhase.COMBAT
    assert pred_early.detail == "combat_soft_end"

    pred_late = predict_round_clock(
        RoundPhase.COMBAT,
        cfg,
        phase_anchor_sec=100.0,
        now_sec=176.0,  # soft_end=180, wake=175
        combat_start_sec=100.0,
    )
    assert pred_late.in_dense_window is True
    assert pred_late.predicted_phase == RoundPhase.POST_COMBAT
    assert pred_late.detail == "combat_post_window"
    assert pred_late.post_expected_at == 180.0


def test_combat_chime_hint_forces_dense() -> None:
    cfg = get_profile(None)
    pred = predict_round_clock(
        RoundPhase.COMBAT,
        cfg,
        phase_anchor_sec=100.0,
        now_sec=130.0,
        combat_start_sec=100.0,
        signals={"chime": True},
        combat_end_hint_sec=128.0,
    )
    assert pred.in_dense_window is True
    assert pred.detail == "combat_end_hint"
