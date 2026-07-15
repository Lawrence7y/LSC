from __future__ import annotations

from lsc.analyzer.phase_scheduler import (
    PROFILE_VALORANT,
    get_profile,
    next_round_phase,
    scan_budget_for_phase,
    RoundPhase,
)
from lsc.analyzer.round_clock_predictor import predict_round_clock


def test_get_profile_unified_aliases() -> None:
    """pov / broadcast / None 一律映射到统一 valorant 档。"""
    unified = get_profile(None)
    assert unified.name == PROFILE_VALORANT
    assert get_profile("pov") is unified
    assert get_profile("broadcast") is unified
    assert get_profile("hvv") is unified
    assert get_profile("valorant") is unified
    assert unified.rms_trust_high is False
    assert unified.lookback_sec == 120.0
    assert unified.buy_duration_sec == 30.0
    assert unified.buy_sleep_sec == 22.0
    assert unified.buy_wake_early_sec == 8.0


def test_buy_sleep_calibrated_to_30s_model() -> None:
    cfg = get_profile(None)
    assert cfg.buy_sleep_sec == cfg.buy_duration_sec - cfg.buy_wake_early_sec
    assert cfg.buy_sleep_sec == 22.0


def test_buy_sleep_then_pre_combat_on_wake() -> None:
    cfg = get_profile(None)
    # 刚进 buy，未满 sleep → 仍 buy；质量档仍开稀疏 OCR
    st = next_round_phase(
        RoundPhase.BUY,
        cfg,
        now_mono=100.0,
        phase_entered_at=90.0,  # 仅过 10s < 22
        signals={"energy_rise": True, "left_buy_ocr": False, "chime": False, "has_end": False, "has_start": False},
    )
    assert st.phase == RoundPhase.BUY
    budget = scan_budget_for_phase(st.phase, cfg, last_analyzed=50.0, current_dur=80.0)
    assert budget.need_ocr is True
    assert budget.ocr_interval_sec == cfg.ocr_sparse_interval_sec
    assert cfg.ocr_sparse_interval_sec <= 1.5
    assert cfg.lookback_sec >= 90.0


def test_buy_prediction_opens_sparse_ocr_near_wake() -> None:
    cfg = get_profile(None)
    pred = predict_round_clock(
        RoundPhase.BUY, cfg, phase_anchor_sec=0.0, now_sec=26.0,
    )
    assert pred.in_dense_window is True
    budget = scan_budget_for_phase(
        RoundPhase.BUY, cfg, last_analyzed=20.0, current_dur=30.0, prediction=pred,
    )
    assert budget.need_ocr is True
    assert budget.ocr_interval_sec == cfg.ocr_sparse_interval_sec


def test_unified_forbids_energy_only_pre_combat() -> None:
    """统一档 rms_trust_high=False：买枪休眠结束后不能仅凭能量进 pre_combat。"""
    cfg = get_profile(None)
    st = next_round_phase(
        RoundPhase.BUY,
        cfg,
        now_mono=200.0,
        phase_entered_at=100.0,  # 已过 sleep
        signals={"energy_rise": True, "left_buy_ocr": False, "chime": False, "has_end": False, "has_start": False},
    )
    assert st.phase == RoundPhase.BUY


def test_chime_wakes_post_combat() -> None:
    cfg = get_profile(None)
    st = next_round_phase(
        RoundPhase.COMBAT,
        cfg,
        now_mono=300.0,
        phase_entered_at=200.0,
        signals={"energy_rise": False, "left_buy_ocr": False, "chime": True, "has_end": False, "has_start": True},
    )
    assert st.phase == RoundPhase.POST_COMBAT


def test_post_combat_confirm_returns_to_buy_or_unknown() -> None:
    cfg = get_profile(None)
    st = next_round_phase(
        RoundPhase.POST_COMBAT,
        cfg,
        now_mono=400.0,
        phase_entered_at=380.0,
        signals={
            "energy_rise": False,
            "left_buy_ocr": False,
            "chime": False,
            "has_end": True,
            "has_start": True,
            "next_buy_seen": True,
        },
    )
    assert st.phase == RoundPhase.BUY
    assert st.just_confirmed is True


def test_post_combat_without_next_buy_goes_unknown() -> None:
    cfg = get_profile(None)
    st = next_round_phase(
        RoundPhase.POST_COMBAT,
        cfg,
        now_mono=400.0,
        phase_entered_at=380.0,
        signals={
            "energy_rise": False,
            "left_buy_ocr": False,
            "chime": False,
            "has_end": True,
            "has_start": True,
            "next_buy_seen": False,
        },
    )
    assert st.phase == RoundPhase.UNKNOWN
    assert st.just_confirmed is True


def test_intermission_exits_on_buy() -> None:
    cfg = get_profile(None)
    st = next_round_phase(
        RoundPhase.INTERMISSION,
        cfg,
        now_mono=500.0,
        phase_entered_at=450.0,
        signals={"left_buy_ocr": True, "next_buy_seen": False, "energy_rise": False},
    )
    assert st.phase == RoundPhase.BUY


def test_combat_scan_budget_keeps_ocr() -> None:
    cfg = get_profile(None)
    budget = scan_budget_for_phase(
        RoundPhase.COMBAT, cfg, last_analyzed=100.0, current_dur=200.0,
    )
    assert budget.need_ocr is True
    assert budget.need_audio is True


def test_buy_scan_budget_keeps_ocr() -> None:
    cfg = get_profile(None)
    budget = scan_budget_for_phase(
        RoundPhase.BUY, cfg, last_analyzed=100.0, current_dur=200.0,
    )
    assert budget.need_ocr is True


def test_unknown_uses_dense_ocr() -> None:
    cfg = get_profile(None)
    budget = scan_budget_for_phase(
        RoundPhase.UNKNOWN, cfg, last_analyzed=10.0, current_dur=50.0,
    )
    assert budget.need_ocr is True
    assert budget.ocr_interval_sec == cfg.ocr_dense_interval_sec


def test_intermission_from_unknown_after_quiet_dwell() -> None:
    cfg = get_profile(None)
    st = next_round_phase(
        RoundPhase.UNKNOWN,
        cfg,
        now_mono=200.0,
        phase_entered_at=100.0,  # dwell 100 >= 45
        signals={
            "energy_rise": False,
            "left_buy_ocr": False,
            "chime": False,
            "has_end": False,
            "has_start": False,
            "next_buy_seen": False,
        },
    )
    assert st.phase == RoundPhase.INTERMISSION
    assert st.detail == "enter_intermission"


def test_intermission_scan_budget_sparse() -> None:
    cfg = get_profile(None)
    budget = scan_budget_for_phase(
        RoundPhase.INTERMISSION, cfg, last_analyzed=100.0, current_dur=160.0,
    )
    assert budget.need_ocr is True
    assert budget.ocr_interval_sec == cfg.intermission_ocr_interval_sec
    assert budget.lookback_sec <= 30.0


def test_min_dwell_suppresses_jitter() -> None:
    cfg = get_profile(None)
    # PRE_COMBAT 刚进入 1s，has_start 想进 COMBAT → 被 min_dwell 压制
    st = next_round_phase(
        RoundPhase.PRE_COMBAT,
        cfg,
        now_mono=101.0,
        phase_entered_at=100.0,
        signals={"has_start": True, "left_buy_ocr": False, "has_end": False},
    )
    assert st.phase == RoundPhase.PRE_COMBAT
    assert st.detail == "min_dwell"


def test_min_dwell_does_not_block_forced_chime() -> None:
    cfg = get_profile(None)
    st = next_round_phase(
        RoundPhase.COMBAT,
        cfg,
        now_mono=101.0,
        phase_entered_at=100.0,  # dwell 1s < 3
        signals={"chime": True, "has_start": True, "has_end": False},
    )
    assert st.phase == RoundPhase.POST_COMBAT
    assert st.detail == "enter_post"
