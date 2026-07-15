"""无畏契约回合时钟预测器（Round Clock Predictor）。

以可信锚点 + 固定时序先验推演下一转场期望时刻，只用于扫描调度（何时加密 OCR），
不得单独驱动自动入列。确认门仍由 OCR 双可信负责。

标准 Buy Phase = 30s；半场/OT 首局 = 45s（联网核实）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from lsc.analyzer.phase_scheduler import RoundPhase, ValorantProfile


@dataclass(frozen=True)
class RoundClockPrediction:
    """回合时钟推演结果（录音时间轴秒）。"""

    predicted_wake_at: float | None
    predicted_phase: RoundPhase | None
    in_dense_window: bool
    buy_expected_end: float | None
    post_expected_at: float | None
    detail: str = ""


def _buy_duration(cfg: ValorantProfile, *, pistol_round: bool) -> float:
    if pistol_round:
        return float(cfg.buy_duration_pistol_sec)
    return float(cfg.buy_duration_sec)


def predict_round_clock(
    phase: RoundPhase,
    cfg: ValorantProfile,
    *,
    phase_anchor_sec: float,
    now_sec: float,
    signals: dict[str, Any] | None = None,
    pistol_round: bool = False,
    combat_start_sec: float | None = None,
    combat_end_hint_sec: float | None = None,
) -> RoundClockPrediction:
    """由相位锚点推演期望转场时刻。

    Parameters
    ----------
    phase:
        当前调度相位。
    phase_anchor_sec:
        进入当前相位（或上一可信边界）在录像时间轴上的秒数。
    now_sec:
        当前录像进度（通常为 current_dur）。
    pistol_round:
        True 时使用 45s 买枪先验。
    combat_start_sec / combat_end_hint_sec:
        可选：交战起点 / 已观测到的终点提示（钟声等），用于 post 预测。
    """
    signals = signals or {}
    anchor = max(0.0, float(phase_anchor_sec))
    now = max(0.0, float(now_sec))
    buy_dur = _buy_duration(cfg, pistol_round=pistol_round)
    buy_end = anchor + buy_dur
    wake_at = max(0.0, buy_end - float(cfg.buy_wake_early_sec))

    if phase == RoundPhase.BUY:
        in_dense = now >= wake_at
        return RoundClockPrediction(
            predicted_wake_at=wake_at,
            predicted_phase=RoundPhase.PRE_COMBAT if in_dense else RoundPhase.BUY,
            in_dense_window=in_dense,
            buy_expected_end=buy_end,
            post_expected_at=None,
            detail="buy_clock",
        )

    if phase == RoundPhase.PRE_COMBAT:
        # 屏障解除窗：整段视为 dense
        return RoundClockPrediction(
            predicted_wake_at=wake_at,
            predicted_phase=RoundPhase.COMBAT,
            in_dense_window=True,
            buy_expected_end=buy_end,
            post_expected_at=None,
            detail="pre_combat_window",
        )

    if phase == RoundPhase.COMBAT:
        # 经验分布：典型交战 ~80s，硬上限 max_combat_force_post_sec。
        # 进入「预计收尾窗」时打开 dense，供调度加密 OCR 锁 round_end（非入列门）。
        combat_start = float(combat_start_sec) if combat_start_sec is not None else anchor
        typical = float(getattr(cfg, "typical_combat_sec", 80.0) or 80.0)
        soft_end = combat_start + typical
        force_wake = combat_start + float(cfg.max_combat_force_post_sec) - float(
            cfg.buy_wake_early_sec
        )
        wake_at = max(0.0, soft_end - float(cfg.buy_wake_early_sec))
        # 已观测钟声/终点提示：立即视为收尾窗
        if combat_end_hint_sec is not None or bool(signals.get("chime")):
            in_dense = True
            detail = "combat_end_hint"
        else:
            in_dense = now >= min(wake_at, force_wake)
            detail = "combat_post_window" if in_dense else "combat_soft_end"
        return RoundClockPrediction(
            predicted_wake_at=wake_at,
            predicted_phase=RoundPhase.POST_COMBAT if in_dense else RoundPhase.COMBAT,
            in_dense_window=in_dense,
            buy_expected_end=None,
            post_expected_at=soft_end,
            detail=detail,
        )

    if phase == RoundPhase.POST_COMBAT:
        end_hint = combat_end_hint_sec
        if end_hint is None and bool(signals.get("chime")):
            end_hint = now
        post_at = None
        if end_hint is not None:
            post_at = float(end_hint) + float(cfg.post_round_sec)
        return RoundClockPrediction(
            predicted_wake_at=post_at,
            predicted_phase=RoundPhase.BUY,
            in_dense_window=True,
            buy_expected_end=None,
            post_expected_at=post_at,
            detail="post_round_pad",
        )

    if phase == RoundPhase.INTERMISSION:
        return RoundClockPrediction(
            predicted_wake_at=anchor + float(cfg.intermission_max_sec),
            predicted_phase=RoundPhase.BUY,
            in_dense_window=False,
            buy_expected_end=None,
            post_expected_at=None,
            detail="intermission_wait",
        )

    # UNKNOWN：加宽寻锚，视为 dense
    return RoundClockPrediction(
        predicted_wake_at=None,
        predicted_phase=None,
        in_dense_window=True,
        buy_expected_end=None,
        post_expected_at=None,
        detail="seeking",
    )


__all__ = [
    "RoundClockPrediction",
    "predict_round_clock",
]
