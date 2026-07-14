"""无畏契约回合相位调度器（Phase Scheduler）。

纯逻辑模块：profile 参数、状态枚举、状态转移、扫描预算。
不依赖 Qt/FFmpeg/异步，可单测。

设计依据：标准回合阶段顺序几乎固定：买枪(标准30s / 半场首局45s) → 屏障解除
→ 交战(20–100s+) → 结算钟声/结算字 → 尾部垃圾(10–30s) → 下一买枪。
真正需要密算的只有起、终点两个转场；中段假安静（架枪/转点）不值得密集 OCR。
本模块用该先验做调度，而不是再堆更重的识别模型。
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from lsc.analyzer.round_clock_predictor import RoundClockPrediction


PROFILE_POV = "pov"
PROFILE_BROADCAST = "broadcast"

_MAX_CATCHUP_SEC = 480.0  # 单次 tick 最多向前追赶的新内容时长（与旧路径 _VALORANT_MAX_CATCHUP_SEC 一致）

# 不受 min_dwell 压制的强制转移 detail
_FORCED_TRANSITION_DETAILS = frozenset({
    "confirmed",
    "reanchor",
    "pre_combat_miss",
    "post_timeout",
    "enter_post",
    "intermission_timeout",
    "anchored",
    "intermission_exit_buy",
})


class RoundPhase(str, Enum):
    UNKNOWN = "unknown"
    BUY = "buy"
    PRE_COMBAT = "pre_combat"
    COMBAT = "combat"
    POST_COMBAT = "post_combat"
    INTERMISSION = "intermission"


@dataclass(frozen=True)
class ValorantProfile:
    """无畏契约相位调度 profile 参数。

    pov 与 broadcast 共用同一状态机，仅参数与信号权重不同。
    """

    name: str
    buy_sleep_sec: float
    pre_combat_window_sec: float
    post_combat_window_sec: float
    rms_trust_high: bool
    ocr_sparse_interval_sec: float
    ocr_dense_interval_sec: float
    unknown_reanchor_sec: float
    max_combat_force_post_sec: float
    min_dwell_sec: float = 3.0
    lookback_sec: float = 45.0  # 相位短窗，远小于旧 240s 默认密扫语义
    # 回合时钟先验（联网核实：标准买枪 30s，半场/OT 首局 45s）
    buy_duration_sec: float = 30.0
    buy_duration_pistol_sec: float = 45.0
    buy_wake_early_sec: float = 5.0
    post_round_sec: float = 5.0
    intermission_enter_sec: float = 45.0
    intermission_max_sec: float = 90.0
    intermission_ocr_interval_sec: float = 15.0


_PROFILES = {
    PROFILE_POV: ValorantProfile(
        name=PROFILE_POV,
        # buy_sleep ≈ buy_duration(30) - wake_early(5) = 25
        buy_sleep_sec=25.0,
        pre_combat_window_sec=12.0,
        post_combat_window_sec=25.0,
        rms_trust_high=True,
        ocr_sparse_interval_sec=12.0,
        ocr_dense_interval_sec=2.0,
        unknown_reanchor_sec=45.0,
        max_combat_force_post_sec=130.0,
        lookback_sec=40.0,
        buy_duration_sec=30.0,
        buy_duration_pistol_sec=45.0,
        buy_wake_early_sec=5.0,
        post_round_sec=5.0,
        intermission_enter_sec=45.0,
        intermission_max_sec=90.0,
        intermission_ocr_interval_sec=15.0,
    ),
    PROFILE_BROADCAST: ValorantProfile(
        name=PROFILE_BROADCAST,
        # buy_sleep ≈ 30 - 8 = 22（解说需更早醒来）
        buy_sleep_sec=22.0,
        pre_combat_window_sec=18.0,
        post_combat_window_sec=35.0,
        rms_trust_high=False,
        ocr_sparse_interval_sec=7.0,
        ocr_dense_interval_sec=1.5,
        unknown_reanchor_sec=30.0,
        max_combat_force_post_sec=130.0,
        lookback_sec=55.0,
        buy_duration_sec=30.0,
        buy_duration_pistol_sec=45.0,
        buy_wake_early_sec=8.0,
        post_round_sec=5.0,
        intermission_enter_sec=45.0,
        intermission_max_sec=120.0,
        intermission_ocr_interval_sec=10.0,
    ),
}


def get_profile(name: str | None) -> ValorantProfile:
    """按名称获取 profile；None 或无法识别时返回 pov 默认。"""
    key = (name or PROFILE_POV).strip().lower()
    if key in ("hvv", "commentary", "broadcast"):
        key = PROFILE_BROADCAST
    if key in ("game", "client", "pov"):
        key = PROFILE_POV
    return _PROFILES.get(key, _PROFILES[PROFILE_POV])


@dataclass
class PhaseTransition:
    """状态转移结果。just_confirmed=True 表示本回合刚闭合可入列。"""

    phase: RoundPhase
    just_confirmed: bool = False
    detail: str = ""


@dataclass
class PhaseScanBudget:
    """扫描预算：告知 worker 本轮该扫哪段、是否需要 OCR、OCR 间隔多少。"""

    scan_start: float
    scan_end: float
    need_audio: bool
    need_ocr: bool
    ocr_interval_sec: float
    lookback_sec: float


def next_round_phase(
    current: RoundPhase,
    cfg: ValorantProfile,
    *,
    now_mono: float,
    phase_entered_at: float,
    signals: dict[str, Any],
) -> PhaseTransition:
    """根据当前相位、驻留时长与信号，计算下一个相位。

    signals 键：
      energy_rise      — RMS 能量抬升（pov 可驱动 buy→pre_combat）
      left_buy_ocr     — OCR 显示已离开买枪阶段
      chime            — 回合结束钟声
      energy_collapse  — 能量塌陷（post_combat 辅助信号）
      has_start        — 已有可信起点（OCR 或等效）
      has_end          — 已有可信终点（OCR 结算或下一买枪）
      next_buy_seen    — 看到了下一回合买枪（用于确认后立即转 buy）
    """
    dwell = max(0.0, now_mono - phase_entered_at)
    energy_rise = bool(signals.get("energy_rise"))
    left_buy = bool(signals.get("left_buy_ocr"))
    chime = bool(signals.get("chime"))
    has_start = bool(signals.get("has_start"))
    has_end = bool(signals.get("has_end"))
    next_buy = bool(signals.get("next_buy_seen"))
    energy_collapse = bool(signals.get("energy_collapse"))

    result = _next_round_phase_raw(
        current,
        cfg,
        dwell=dwell,
        energy_rise=energy_rise,
        left_buy=left_buy,
        chime=chime,
        has_start=has_start,
        has_end=has_end,
        next_buy=next_buy,
        energy_collapse=energy_collapse,
    )

    # min_dwell 防抖：非强制转移且驻留不足时压制相位抖动
    if (
        result.phase != current
        and dwell < float(cfg.min_dwell_sec)
        and result.detail not in _FORCED_TRANSITION_DETAILS
        and not result.just_confirmed
    ):
        return PhaseTransition(current, detail="min_dwell")
    return result


def _next_round_phase_raw(
    current: RoundPhase,
    cfg: ValorantProfile,
    *,
    dwell: float,
    energy_rise: bool,
    left_buy: bool,
    chime: bool,
    has_start: bool,
    has_end: bool,
    next_buy: bool,
    energy_collapse: bool,
) -> PhaseTransition:
    # 全局超时重锚（仅 PRE_COMBAT 适用；BUY/COMBAT/POST_COMBAT 各有自己的超时逻辑）
    if current == RoundPhase.PRE_COMBAT and dwell >= cfg.unknown_reanchor_sec and not has_end:
        return PhaseTransition(RoundPhase.UNKNOWN, detail="reanchor")

    if current == RoundPhase.UNKNOWN:
        # 长时间无锚点且安静 → 局间暂停（换图/半场），避免 unknown 狂扫
        if (
            dwell >= float(cfg.intermission_enter_sec)
            and not left_buy
            and not energy_rise
            and not has_start
            and not next_buy
        ):
            return PhaseTransition(RoundPhase.INTERMISSION, detail="enter_intermission")
        if left_buy or energy_rise:
            return PhaseTransition(
                RoundPhase.BUY if not left_buy else RoundPhase.PRE_COMBAT,
                detail="anchored",
            )
        return PhaseTransition(RoundPhase.UNKNOWN, detail="seeking_anchor")

    if current == RoundPhase.BUY:
        # 买枪休眠：sleep 内不转移
        if dwell < cfg.buy_sleep_sec:
            return PhaseTransition(RoundPhase.BUY, detail="buy_sleep")
        # 休眠结束：pov 允许能量单独推进；broadcast 必须 OCR 确认离开买枪
        if left_buy or (cfg.rms_trust_high and energy_rise):
            return PhaseTransition(RoundPhase.PRE_COMBAT, detail="wake_pre_combat")
        return PhaseTransition(RoundPhase.BUY, detail="buy_wait")

    if current == RoundPhase.PRE_COMBAT:
        if has_start or left_buy:
            return PhaseTransition(RoundPhase.COMBAT, detail="combat_locked")
        # 加密窗超时未锁到起点 → 回 unknown 重锚
        if dwell >= cfg.pre_combat_window_sec * 2:
            return PhaseTransition(RoundPhase.UNKNOWN, detail="pre_combat_miss")
        return PhaseTransition(RoundPhase.PRE_COMBAT, detail="locking_start")

    if current == RoundPhase.COMBAT:
        # 钟声或能量塌陷触发进入 post_combat（不从 combat 直接进 intermission）
        if chime or energy_collapse or dwell >= cfg.max_combat_force_post_sec:
            return PhaseTransition(RoundPhase.POST_COMBAT, detail="enter_post")
        return PhaseTransition(RoundPhase.COMBAT, detail="in_combat")

    if current == RoundPhase.POST_COMBAT:
        # 双可信（has_start + has_end）→ 确认闭合，立即转 buy 或 unknown
        if has_start and has_end:
            nxt = RoundPhase.BUY if next_buy else RoundPhase.UNKNOWN
            return PhaseTransition(nxt, just_confirmed=True, detail="confirmed")
        # 长时间锁终点失败且安静 → 局间暂停
        if (
            dwell >= float(cfg.intermission_enter_sec)
            and not has_end
            and not next_buy
            and not energy_rise
            and not left_buy
        ):
            return PhaseTransition(RoundPhase.INTERMISSION, detail="enter_intermission")
        # 收尾窗超时 → 回 unknown
        if dwell >= cfg.post_combat_window_sec * 2:
            return PhaseTransition(RoundPhase.UNKNOWN, detail="post_timeout")
        return PhaseTransition(RoundPhase.POST_COMBAT, detail="locking_end")

    if current == RoundPhase.INTERMISSION:
        if left_buy or next_buy:
            return PhaseTransition(RoundPhase.BUY, detail="intermission_exit_buy")
        if dwell >= float(cfg.intermission_max_sec):
            return PhaseTransition(RoundPhase.UNKNOWN, detail="intermission_timeout")
        return PhaseTransition(RoundPhase.INTERMISSION, detail="intermission_wait")

    return PhaseTransition(RoundPhase.UNKNOWN, detail="fallback")


def scan_budget_for_phase(
    phase: RoundPhase,
    cfg: ValorantProfile,
    *,
    last_analyzed: float,
    current_dur: float,
    pending_start: float | None = None,
    prediction: RoundClockPrediction | None = None,
) -> PhaseScanBudget:
    """计算本轮扫描预算：短窗扫描，不跳过中间。

    lookback 随相位调整：unknown 时加宽以寻找锚点，post_combat/pre_combat
    适度加宽以覆盖转场 overlap，intermission 缩短以避免狂扫。
    prediction 可选：买枪末期 / 预测 dense 窗可提前打开稀疏 OCR。
    """
    lookback = cfg.lookback_sec
    if phase == RoundPhase.POST_COMBAT:
        lookback = max(lookback, cfg.post_combat_window_sec + 10.0)
    elif phase == RoundPhase.PRE_COMBAT:
        lookback = max(lookback, cfg.pre_combat_window_sec + 10.0)
    elif phase == RoundPhase.UNKNOWN:
        lookback = max(lookback, 90.0)
    elif phase == RoundPhase.INTERMISSION:
        lookback = min(lookback, 30.0)

    if last_analyzed <= 0:
        start, end = 0.0, min(float(current_dur), _MAX_CATCHUP_SEC)
    else:
        start = max(0.0, float(last_analyzed) - lookback)
        end = min(float(current_dur), float(last_analyzed) + _MAX_CATCHUP_SEC)
        if pending_start is not None:
            start = min(start, max(0.0, float(pending_start) - 5.0))

    # OCR 帧率按相位在 sparse / dense / off 之间切换
    dense = phase in (RoundPhase.PRE_COMBAT, RoundPhase.POST_COMBAT, RoundPhase.UNKNOWN)
    # 预测窗：买枪已到 wake 时刻 → 打开稀疏 OCR，便于更早看到 left_buy
    pred_dense = bool(prediction is not None and prediction.in_dense_window)
    if phase == RoundPhase.BUY and pred_dense:
        need_ocr = True
        interval = cfg.ocr_sparse_interval_sec
    elif phase == RoundPhase.INTERMISSION:
        need_ocr = True
        interval = float(cfg.intermission_ocr_interval_sec)
    elif phase == RoundPhase.COMBAT:
        need_ocr = False
        interval = cfg.ocr_sparse_interval_sec
    elif phase == RoundPhase.BUY:
        need_ocr = False
        interval = cfg.ocr_sparse_interval_sec
    else:
        need_ocr = dense
        interval = cfg.ocr_dense_interval_sec if dense else cfg.ocr_sparse_interval_sec

    return PhaseScanBudget(
        scan_start=round(start, 3),
        scan_end=round(end, 3),
        need_audio=True,
        need_ocr=need_ocr,
        ocr_interval_sec=interval if need_ocr else 999.0,
        lookback_sec=lookback,
    )


PHASE_DETAIL_ZH = {
    "buy_sleep": "买枪休眠中",
    "buy_wait": "买枪侦测中",
    "wake_pre_combat": "等待屏障解除",
    "locking_start": "锁定回合起点",
    "in_combat": "交战中",
    "enter_post": "等待回合结束",
    "locking_end": "锁定回合终点",
    "confirmed": "回合已确认",
    "seeking_anchor": "寻找回合锚点",
    "reanchor": "相位重锚",
    "anchored": "已锚定",
    "combat_locked": "交战已锁定",
    "combat_timeout": "交战超时",
    "pre_combat_miss": "开战窗错过",
    "post_timeout": "收尾超时",
    "fallback": "重置",
    "min_dwell": "相位防抖",
    "enter_intermission": "局间暂停",
    "intermission_wait": "局间暂停中",
    "intermission_exit_buy": "暂停结束进入买枪",
    "intermission_timeout": "暂停超时重锚",
}


__all__ = [
    "PROFILE_POV",
    "PROFILE_BROADCAST",
    "RoundPhase",
    "ValorantProfile",
    "PhaseTransition",
    "PhaseScanBudget",
    "PHASE_DETAIL_ZH",
    "get_profile",
    "next_round_phase",
    "scan_budget_for_phase",
]
