"""回合时钟预测器单测。"""
from __future__ import annotations

from lsc.analyzer.phase_scheduler import RoundPhase, get_profile
from lsc.analyzer.round_clock_predictor import predict_round_clock


def test_buy_clock_standard_30s_wake() -> None:
    cfg = get_profile("pov")
    assert cfg.buy_duration_sec == 30.0
    assert cfg.buy_wake_early_sec == 5.0
    # buy_sleep ≈ 30 - 5 = 25
    assert cfg.buy_sleep_sec == 25.0

    pred = predict_round_clock(
        RoundPhase.BUY,
        cfg,
        phase_anchor_sec=100.0,
        now_sec=120.0,  # 买枪进行 20s，未到 wake(125)
        pistol_round=False,
    )
    assert pred.buy_expected_end == 130.0
    assert pred.predicted_wake_at == 125.0
    assert pred.in_dense_window is False
    assert pred.predicted_phase == RoundPhase.BUY

    pred2 = predict_round_clock(
        RoundPhase.BUY,
        cfg,
        phase_anchor_sec=100.0,
        now_sec=126.0,
        pistol_round=False,
    )
    assert pred2.in_dense_window is True
    assert pred2.predicted_phase == RoundPhase.PRE_COMBAT


def test_pistol_buy_uses_45s() -> None:
    cfg = get_profile("pov")
    pred = predict_round_clock(
        RoundPhase.BUY,
        cfg,
        phase_anchor_sec=0.0,
        now_sec=10.0,
        pistol_round=True,
    )
    assert pred.buy_expected_end == 45.0
    assert pred.predicted_wake_at == 40.0


def test_intermission_prediction_not_dense() -> None:
    cfg = get_profile("broadcast")
    pred = predict_round_clock(
        RoundPhase.INTERMISSION,
        cfg,
        phase_anchor_sec=500.0,
        now_sec=560.0,
    )
    assert pred.in_dense_window is False
    assert pred.detail == "intermission_wait"


def test_post_combat_adds_post_round_pad() -> None:
    cfg = get_profile("pov")
    pred = predict_round_clock(
        RoundPhase.POST_COMBAT,
        cfg,
        phase_anchor_sec=200.0,
        now_sec=210.0,
        combat_end_hint_sec=205.0,
    )
    assert pred.post_expected_at == 210.0  # 205 + 5
    assert pred.in_dense_window is True
