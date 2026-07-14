from __future__ import annotations

from lsc.analyzer.phase_scheduler import (
    PROFILE_POV,
    PROFILE_BROADCAST,
    get_profile,
    next_round_phase,
    scan_budget_for_phase,
    RoundPhase,
)
from lsc.analyzer.round_clock_predictor import predict_round_clock


def test_get_profile_defaults_and_aliases() -> None:
    assert get_profile(None).name == PROFILE_POV
    assert get_profile("broadcast").name == PROFILE_BROADCAST
    # broadcast wake_early 更大 → buy_sleep 仍可小于或等于 pov，取决于参数
    assert get_profile("pov").buy_duration_sec == 30.0
    assert get_profile("broadcast").buy_duration_sec == 30.0


def test_buy_sleep_calibrated_to_30s_model() -> None:
    pov = get_profile("pov")
    assert pov.buy_sleep_sec == pov.buy_duration_sec - pov.buy_wake_early_sec
    assert pov.buy_sleep_sec == 25.0
    bc = get_profile("broadcast")
    assert bc.buy_sleep_sec == bc.buy_duration_sec - bc.buy_wake_early_sec
    assert bc.buy_sleep_sec == 22.0


def test_buy_sleep_then_pre_combat_on_wake() -> None:
    cfg = get_profile("pov")
    # 刚进 buy，未满 sleep → 仍 buy，且预算几乎不 OCR
    st = next_round_phase(
        RoundPhase.BUY,
        cfg,
        now_mono=100.0,
        phase_entered_at=90.0,  # 仅过 10s < 25
        signals={"energy_rise": True, "left_buy_ocr": False, "chime": False, "has_end": False, "has_start": False},
    )
    assert st.phase == RoundPhase.BUY
    budget = scan_budget_for_phase(st.phase, cfg, last_analyzed=50.0, current_dur=80.0)
    assert budget.need_ocr is False
    assert budget.ocr_interval_sec >= 9.0


def test_buy_prediction_opens_sparse_ocr_near_wake() -> None:
    cfg = get_profile("pov")
    pred = predict_round_clock(
        RoundPhase.BUY, cfg, phase_anchor_sec=0.0, now_sec=26.0,
    )
    assert pred.in_dense_window is True
    budget = scan_budget_for_phase(
        RoundPhase.BUY, cfg, last_analyzed=20.0, current_dur=30.0, prediction=pred,
    )
    assert budget.need_ocr is True
    assert budget.ocr_interval_sec == cfg.ocr_sparse_interval_sec


def test_broadcast_forbids_energy_only_pre_combat() -> None:
    cfg = get_profile("broadcast")
    st = next_round_phase(
        RoundPhase.BUY,
        cfg,
        now_mono=200.0,
        phase_entered_at=100.0,  # 已过 sleep
        signals={"energy_rise": True, "left_buy_ocr": False, "chime": False, "has_end": False, "has_start": False},
    )
    assert st.phase == RoundPhase.BUY  # 低 rms_trust：不能仅凭能量进 pre_combat


def test_chime_wakes_post_combat() -> None:
    cfg = get_profile("pov")
    st = next_round_phase(
        RoundPhase.COMBAT,
        cfg,
        now_mono=300.0,
        phase_entered_at=200.0,
        signals={"energy_rise": False, "left_buy_ocr": False, "chime": True, "has_end": False, "has_start": True},
    )
    assert st.phase == RoundPhase.POST_COMBAT


def test_post_combat_confirm_returns_to_buy_or_unknown() -> None:
    cfg = get_profile("pov")
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


def test_intermission_from_unknown_after_quiet_dwell() -> None:
    cfg = get_profile("pov")
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


def test_intermission_exits_to_buy_on_left_buy() -> None:
    cfg = get_profile("pov")
    st = next_round_phase(
        RoundPhase.INTERMISSION,
        cfg,
        now_mono=300.0,
        phase_entered_at=250.0,
        signals={"left_buy_ocr": True, "next_buy_seen": False, "energy_rise": False},
    )
    assert st.phase == RoundPhase.BUY
    assert st.detail == "intermission_exit_buy"


def test_intermission_scan_budget_sparse() -> None:
    cfg = get_profile("pov")
    budget = scan_budget_for_phase(
        RoundPhase.INTERMISSION, cfg, last_analyzed=100.0, current_dur=160.0,
    )
    assert budget.need_ocr is True
    assert budget.ocr_interval_sec == cfg.intermission_ocr_interval_sec
    assert budget.lookback_sec <= 30.0


def test_min_dwell_suppresses_jitter() -> None:
    cfg = get_profile("pov")
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
    cfg = get_profile("pov")
    st = next_round_phase(
        RoundPhase.COMBAT,
        cfg,
        now_mono=101.0,
        phase_entered_at=100.0,  # dwell 1s < 3
        signals={"chime": True, "has_start": True, "has_end": False},
    )
    assert st.phase == RoundPhase.POST_COMBAT
    assert st.detail == "enter_post"
