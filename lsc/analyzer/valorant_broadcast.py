"""Valorant 赛事/二路转播的保守边界审计。

普通 POV 不进入本模块。赛事流中，OCR 只负责提供回合候选；本模块使用
五分类视觉模型和计时器连续性检查，把 Replay、官方暂停和买枪/非游戏段
从候选区间前截断。由于当前 Clip 是单一连续区间，暂停/回放后的恢复段
不会被强行拼回同一个切片，宁可交给人工确认也不跨越污染区间。
"""
from __future__ import annotations

import logging
import math
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from lsc.analyzer.valorant_frame_classifier import ValorantFrameClassifier

_log = logging.getLogger(__name__)

MIN_ACTIVE_SEC = 10.0
MAX_BROADCAST_ROUND_SEC = 150.0
# OCR 的 end 是粗边界，赛事画面中短暂的转场、比分板或模型抖动不能直接
# 把回合截断。Replay/result 的高置信度连续证据可快速生效；其它排除类
# 必须形成更长的稳定游程。
EXCLUSION_STABLE_FRAMES = 4
# 默认后视最大窗口（非强证据保底）；强证据为 45s；
# 运行时支持由调用方传入 lookahead_sec 动态控制
END_LOOKAHEAD_SEC = 90.0
# 暂停计时器只用于长时间冻结兜底，不需要跟随视觉模型逐帧 OCR。
# 低频取样足以确认官方暂停；非 combat 疑点仍会即时补取计时器。
BROADCAST_TIMER_SAMPLE_INTERVAL_SEC = 4.0
# 1fps 主扫描找到终点后，在终点附近用 2fps 做一次小范围复核，避免
# 低频采样把短 Replay 的前 1～2 秒带入切片。
BROADCAST_BOUNDARY_REFINE_SEC = 6.0
# 首次审计优先检查候选结束点附近；候选起点已经由 OCR 连续交战确认，
# 只有尾部没有看到 combat 时才需要扩展回完整候选区间。
#
# ⚠️ 30s 不够（2026-09-12 19:14 现场实测 round-000071）：OCR 粗出点现在落在
# **下一回合买枪首帧**（≤45s 计时器判据删除后的正常形态），而回合的真实出点常在其
# 前 30~60s。回看 30s 会让窗口从"回合已结束之后"开始：尾部没看到 combat，
# `_first_stable_exclusion` 就认不出"战斗→终态"的边界 ⇒ `no_exclusion_evidence`
# ⇒ 整条真实回合进不了草稿。A/B（同一候选、同一录像）：
#   lookback=30 → manual_review/no_exclusion_evidence（丢回合）
#   lookback=45 → accepted, end=803.25, broadcast_exclusion, precise
#   lookback=60 → 同上
BROADCAST_AUDIT_TAIL_LOOKBACK_SEC = 60.0
# 弱出点候选的「向前回扫」取证预算（秒）。
#
# 为什么需要：OCR 以 next_combat / open_tail 闭合时，出点常比真出点晚 30-80s
# （赛后回放 + 买枪都很长）。尾窗只从 end-60 开始，真出点之前的转场
# （combat -> replay/result/non_game/buy）若早于该点就完全落在窗外：
# _first_stable_exclusion 认不出边界 -> reason=none -> 过晚的粗出点被
# pending_no_exclusion 当成终态保留（切片尾部把整段回放和买枪一起切进去，
# 用户侧表现为「边界不准 + 永远待确认」）。
# 把回看从 30s 放宽到 60s 只救回「差一点」的候选；本常量把这类取证升级为
# **有界向前回扫**：复用既有的 fallback_full_* 游标机制，每步仍受
# max_media_step_sec 约束，定稿前必须把该候选自己的区间扫完，否则不得盖章
# pending_no_exclusion。上限只是安全阀（非分裂候选跨度 <= 150s，回扫空间天然 <= 90s）。
BROADCAST_AUDIT_BACKWARD_SWEEP_MAX_SEC = 150.0
AUDIT_MICRO_STEP_SEC = 18.0
NEXT_PREP_COMBAT_VETO_WINDOW_SEC = 8.0
# 分裂块专用：截断点之后重新出现的满钟阈值（与 OCR FSM _NEW_ROUND_CLOCK_MIN 同值）。
# 用于区分"回放/非游戏夹在两回合之间"（新回合满钟）与"同一回合仍在交战"。
FRESH_ROUND_CLOCK_MIN = 85.0
NEXT_PREP_COMBAT_VETO_MIN_FRAMES = 3
# L1 区间内边界自检：跨回合候选的前缀裁剪上限（超过则整条拒绝，避免"为凑切片而编造起点"）
INTERIOR_TRIM_MAX_SEC = 20.0
INTERIOR_TRIM_MAX_FRACTION = 0.5
RESULT_PRESENTATION_TAIL_SEC = 2.5
PAUSE_MIN_SEC = 2.5
# 入点门禁：定稿/离线审计时，候选起点必须由连续 visual combat 确认，
# 不允许把 replay/non_game/result 或超长固定切块的块头直接当作回合起点。
# 普通候选只看开头一小段即可；split_from_oversize 子块没有真实起点，必须
# 扫描到 MAX_BROADCAST_ROUND_SEC 以寻找块内真实 combat 锚点。
# 普通候选若头部一小段找不到 combat，允许向后再找一段：赛事回放段可能
# 覆盖开头 15s，真实交战随后才开始；仍找不到才真正拒绝。
START_GATE_SCAN_LIMIT_SEC = 15.0
START_GATE_EXTENDED_SCAN_LIMIT_SEC = 35.0
START_GATE_COMBAT_MIN_SEC = 2.0
START_GATE_MAX_GAP_SEC = 2.0
# 起点门禁的入点容差：候选起点与"窗口内首个稳定 combat 游程起点"的最大允许偏差。
# 1fps 采样下候选起点那一帧若恰好落在低置信/unknown 过渡帧，首个 combat 游程会
# 晚一个采样间隔出现；旧实现要求偏差 ≤1e-6s，于是整条真实回合被拒并从前端列表删除
# （实测 2026-09-12 11:32：start=230.187 首帧 unknown(0.3565) → onset 231.187 →
# rejected_no_stable_combat_start；同样内容 start=230.200 首帧 combat 即通过）。
# 给 2.5 个采样间隔：抖动被吸收，而"窗口内先回放、10s 后才交战"的伪候选偏差远大于
# 容差仍被拒；纯回放/非游戏窗口本来就没有 combat 游程，不受影响。
START_GATE_ONSET_TOLERANCE_SEC = 2.5
# A5 缩窗余量：审计要在回放块起点之后取到稳定终态游程（EXCLUSION_STABLE_FRAMES 帧）
# 并允许 ±6s 边界复核，故窗口压到「回放块起点 + 8s」；压窗后无 cutoff 会回退完整窗口。
A5_WINDOW_CAP_MARGIN_SEC = 8.0
_CLASS_NAMES = ("non_game", "buy", "combat", "result", "replay")
_EXCLUSION_LABELS = frozenset({"non_game", "buy", "result", "replay"})
_TERMINAL_LABELS = frozenset({"non_game", "result", "replay"})
# 计时器 OCR 结果的实际消费方只需要这些标签的帧：combat 走 _first_frozen_timer
# （官方暂停检测），non_game/buy/result 走 has_active_timer（交战钟抑制尾点误判）。
# unknown/replay 帧的计时器从不被读取，跳过可省掉审计中最贵的冗余 OCR。
_TIMER_OCR_LABELS = frozenset({"non_game", "buy", "result"})


def _stable_visual_label(
    probabilities: Any,
    *,
    stable_prob: float,
    class_stable_prob: dict[str, float] | None = None,
) -> tuple[str, float]:
    """Convert one probability row to a stable label with optional class gating."""
    class_index = max(range(len(_CLASS_NAMES)), key=lambda index: float(probabilities[index]))
    confidence = float(probabilities[class_index])
    candidate = _CLASS_NAMES[class_index]
    threshold = float((class_stable_prob or {}).get(candidate, stable_prob))
    return (candidate if confidence >= threshold else "unknown"), confidence


def _log_model_confidence(
    classifier: Any,
    *,
    stage: str,
    timestamp_sec: float,
    probabilities: Any,
    stable_label: str,
    confidence: float,
    threshold: float,
) -> None:
    """Log the final per-sample visual confidence used by broadcast auditing.

    Keep this at INFO so the production backend log contains an auditable
    confidence trail.  The raw candidate is logged separately from the
    threshold-gated label because a sample may be ``unknown`` even when the
    model still has a clear argmax class.
    """
    if not _log.isEnabledFor(logging.INFO):
        return
    try:
        raw_values = [float(probabilities[index]) for index in range(len(_CLASS_NAMES))]
        candidate_index = max(range(len(raw_values)), key=raw_values.__getitem__)
        candidate = _CLASS_NAMES[candidate_index]
        prob_summary = ",".join(
            f"{name}:{value:.4f}" for name, value in zip(_CLASS_NAMES, raw_values, strict=True)
        )
    except (TypeError, ValueError, IndexError):
        # The classifier contract validates this before reaching the audit, but
        # confidence logging must never turn a valid audit result into a crash.
        candidate = "unknown"
        prob_summary = "unavailable"
    model_version = getattr(classifier, "model_version", None) or "unknown"
    provider = getattr(classifier, "provider", None) or "unknown"
    _log.info(
        "Valorant model confidence: stage=%s ts=%.3f model=%s provider=%s "
        "candidate=%s label=%s confidence=%.4f threshold=%.4f probs=%s",
        stage,
        float(timestamp_sec),
        model_version,
        provider,
        candidate,
        stable_label,
        float(confidence),
        float(threshold),
        prob_summary,
    )


def _predict_broadcast_batch(classifier: Any, frames: list[Any]) -> Any:
    """Use configured broadcast fusion while keeping test/fallback classifiers compatible."""
    predictor = getattr(classifier, "predict_broadcast_batch", None)
    if callable(predictor):
        return predictor(frames)
    return classifier.predict_batch(frames)


def _stabilize_broadcast_samples(
    samples: list[tuple[float, str, float]],
) -> list[tuple[float, str, float]]:
    """Repair one uncertain sample when both temporal neighbors agree.

    This deliberately changes only an ``unknown``/low-evidence middle sample;
    a confident class is never overwritten by temporal majority.  It removes
    isolated inference glitches without allowing a single Replay/Result frame
    to create a terminal run on its own.
    """
    if len(samples) < 3:
        return list(samples)
    stabilized = list(samples)
    for index in range(1, len(samples) - 1):
        left = samples[index - 1]
        current = samples[index]
        right = samples[index + 1]
        if (
            left[1] == right[1]
            and left[1] != "unknown"
            and current[1] != left[1]
            and (current[1] == "unknown" or float(current[2]) < 0.55)
        ):
            stabilized[index] = (current[0], left[1], current[2])
    return stabilized

BroadcastAuditStatus = Literal["accepted", "pending", "manual_review", "rejected"]


@dataclass(frozen=True, slots=True)
class BroadcastAuditOutcome:
    """Explicit queue disposition for one broadcast candidate."""

    status: BroadcastAuditStatus
    candidate: dict[str, Any]
    reason: str
    retry_after_duration: float | None = None


def _record_audit_outcome(
    sink: list[BroadcastAuditOutcome] | None,
    *,
    status: BroadcastAuditStatus,
    candidate: dict[str, Any],
    reason: str,
    retry_after_duration: float | None = None,
) -> None:
    if sink is None:
        return
    sink.append(
        BroadcastAuditOutcome(
            status=status,
            candidate=dict(candidate),
            reason=str(reason),
            retry_after_duration=retry_after_duration,
        )
    )


def _first_stable_exclusion(
    samples: Iterable[tuple[float, str, float]],
    *,
    min_frames: int = EXCLUSION_STABLE_FRAMES,
    timer_samples: Iterable[tuple[float, float | None, str]] | None = None,
) -> float | None:
    """Return the first stable terminal/non-game run after observed combat."""
    timer_points = [
        (float(ts), float(timer))
        for ts, timer, _ in (timer_samples or [])
        if timer is not None
    ]

    def has_active_timer(ts: float) -> bool:
        # 赛事流计时器是低频补充证据，而模型采样通常为 1fps；允许一个采样
        # 周期的误差。交战钟仍在递减时，non_game/unknown 短误判不能截断回合。
        return any(
            abs(point_ts - ts) <= 1.25 and timer > 45.0
            for point_ts, timer in timer_points
        )

    active_seen = False
    run_label: str | None = None
    run_start: float | None = None
    run_count = 0
    strong_terminal_count = 0
    replay_gap_frames = 0
    for ts, label, confidence in samples:
        label = str(label)
        ts_val = float(ts)
        if label == "combat" and confidence >= 0.0:
            # 抗回放感染：若在已有强证据的 terminal/replay 游程中遇到孤立 combat，
            # 且此时 HUD 计时器并未恢复活跃交战钟（timer > 45），不轻易将游程清零。
            if (
                run_label == "terminal"
                and strong_terminal_count >= 1
                and not has_active_timer(ts_val)
                and replay_gap_frames < 1
            ):
                replay_gap_frames += 1
                continue
            active_seen = True
            run_label = None
            run_start = None
            run_count = 0
            strong_terminal_count = 0
            replay_gap_frames = 0
            continue
        # Replay/settle evidence can briefly become ``unknown`` while a
        # transition overlay is on screen.  Keep a short gap inside the
        # terminal run; the gap itself never contributes evidence and a real
        # combat sample still resets the run below.
        if (
            active_seen
            and run_label == "terminal"
            and label == "unknown"
            and strong_terminal_count >= 1
            and replay_gap_frames < 2
        ):
            replay_gap_frames += 1
            continue
        if not active_seen or label not in _EXCLUSION_LABELS:
            run_label = None
            run_start = None
            run_count = 0
            strong_terminal_count = 0
            replay_gap_frames = 0
            continue
        if label != "replay" and has_active_timer(ts_val):
            # 这是最常见的赛事流尾点误判：直播 HUD 的交战钟仍在走，模型
            # 因比分板/特效短暂给出 non_game。清空游程，等待真正脱离游戏。
            run_label = None
            run_start = None
            run_count = 0
            strong_terminal_count = 0
            replay_gap_frames = 0
            continue
        # result/replay/non_game 都可能出现在同一段官方回放转场中，不能
        # 要求三者标签完全一致；把它们视为一个 terminal 游程。
        run_key = "terminal" if label in _TERMINAL_LABELS else label
        if run_key == run_label:
            run_count += 1
        else:
            run_label = run_key
            run_start = ts_val
            run_count = 1
            strong_terminal_count = 0
            replay_gap_frames = 0
        if run_key == "terminal" and label in {"result", "replay"} and confidence >= 0.70:
            strong_terminal_count += 1
        # Replay/result 需要至少两个高置信度采样；允许少量 unknown 间隔，
        # 避免瞬态转场把同一段 Replay 证据切断。弱预测以及 non_game/buy
        # 则使用更长的稳定游程。
        if run_count >= min_frames or (
            run_key == "terminal"
            and strong_terminal_count >= 2
            and run_count >= 2
        ):
            return run_start
    return None


def _first_frozen_timer(
    timer_samples: Iterable[tuple[float, float | None, str]],
    *,
    min_duration: float = PAUSE_MIN_SEC,
) -> float | None:
    """Return the start of a combat timer frozen for a pause-like duration."""
    previous_ts: float | None = None
    previous_timer: float | None = None
    frozen_start: float | None = None
    for ts, timer, label in timer_samples:
        ts = float(ts)
        if label != "combat" or timer is None:
            previous_ts = ts
            previous_timer = None
            frozen_start = None
            continue
        timer = float(timer)
        if (
            previous_timer is not None
            and previous_ts is not None
            and abs(timer - previous_timer) <= 0.5
        ):
            if frozen_start is None:
                frozen_start = previous_ts
            if ts - frozen_start >= min_duration:
                return frozen_start
        else:
            frozen_start = None
        previous_ts = ts
        previous_timer = timer
    return None


def _has_decreasing_combat_after(
    cutoff: float,
    timer_samples: Iterable[tuple[float, float | None, str]],
    *,
    min_decreases: int = 2,
    fresh_clock_min: float | None = None,
) -> bool:
    """Hard veto: combat timer still ticking down after a proposed exclusion.

    A true round end is followed by replay/non-game, not by an ongoing combat
    timer. If the proposed ``cutoff`` is before a run of combat frames whose
    timer keeps decreasing, the exclusion is premature and ``broadcast_exclusion``
    must not be finalized.

    ``fresh_clock_min``（分裂块专用）：截断点之后若重新出现**满钟**的交战计时器，
    说明那是一段**新回合**（回放/非游戏夹在两回合之间），不是"同一回合仍在交战"，
    此时不得否决截断——否则固定切块永远无法在块内回放处定稿（实测：块内 22s 回放
    之后接下一回合满钟，旧逻辑把正确的截断否决掉了）。
    """
    try:
        boundary = float(cutoff)
    except (TypeError, ValueError):
        return False
    decreasing_pairs = 0
    previous_timer: float | None = None
    for ts, timer, label in timer_samples:
        ts_val = float(ts)
        if ts_val < boundary:
            # Keep the last combat timer before the proposed cutoff as the base
            # for the first post-cutoff comparison; a decreasing run may start
            # right at the boundary.
            if str(label) == "combat" and timer is not None:
                previous_timer = float(timer)
            continue
        if str(label) != "combat" or timer is None:
            decreasing_pairs = 0
            previous_timer = None
            continue
        timer_val = float(timer)
        if (
            fresh_clock_min is not None
            and previous_timer is None
            and timer_val >= float(fresh_clock_min)
        ):
            return False
        if previous_timer is not None and timer_val < previous_timer:
            decreasing_pairs += 1
            if decreasing_pairs >= max(1, int(min_decreases)):
                return True
        else:
            decreasing_pairs = 0
        previous_timer = timer_val
    return False


def _has_immediate_combat_after(
    samples: Iterable[tuple[float, str, float]],
    boundary: float,
    *,
    window_sec: float = NEXT_PREP_COMBAT_VETO_WINDOW_SEC,
    min_frames: int = NEXT_PREP_COMBAT_VETO_MIN_FRAMES,
) -> bool:
    """Return whether a purported next-prep boundary is followed by combat."""
    start = float(boundary)
    end = start + max(1.0, float(window_sec))
    run = 0
    for ts, label, _confidence in samples:
        point = float(ts)
        if point <= start + 0.25 or point > end:
            continue
        if str(label) == "combat":
            run += 1
            if run >= max(1, int(min_frames)):
                return True
        else:
            run = 0
    return False


def _interior_round_boundary(
    samples: Iterable[tuple[float, str, float]],
    *,
    start: float,
    end: float,
    min_terminal_frames: int = EXCLUSION_STABLE_FRAMES,
    min_resume_frames: int | None = None,
) -> float | None:
    """候选区间**内部**「回合边界之后重新开战」的时间点（取最后一个；None = 无内部边界）。

    形态：``combat → 终态游程(result/non_game/replay ≥N 帧) → [buy] → combat``，
    且全部落在 ``(start, end)`` 内。命中即说明该区间跨回合：起点属于前一回合，
    出点属于后一回合——现场 round-000123 的 ``[1232, 1420.75]`` 内含回合 A 的结束
    （1315→1349：result/non_game/replay + buy），出点却是回合 B 的结束，于是与
    round-000135 认领同一条回合 B（"重叠 68.75s"的真实成因）。

    注意：**只看区间内部**。区间之后的下一回合（``ts >= end``）不参与判定，
    因此 135 这类"出点就是排除点、后面才接下一回合"的候选不会被误伤。

    2026-09-12 修复：**重新开战必须是稳定游程**（≥ ``min_resume_frames``，默认与终态
    游程同阈值 4 帧）。此前终态游程后出现**单帧** combat 即算重新开战，于是回合结束
    转场里的 2 帧低置信 combat（实测 round-000099：``result 1081-1083 → combat 1084
    (0.554) / 1085 (0.648) → non_game/replay 1086+``）被误当成跨回合证据，整条真实
    回合被 `rejected_interior_boundary` 丢掉。真实重开战是整段交战（≥30s），不受影响。
    """
    boundary_start = float(start)
    boundary_end = float(end)
    need = max(1, int(min_terminal_frames))
    need_resume = max(1, int(min_resume_frames if min_resume_frames is not None else need))
    terminal_run = 0
    combat_run = 0
    combat_run_start: float | None = None
    combat_after_terminal = False
    last_resume: float | None = None
    for ts, label, _confidence in samples:
        point = float(ts)
        if point <= boundary_start or point >= boundary_end:
            continue
        text = str(label)
        if text == "combat":
            # 不要求区间内先出现 combat：审计按**尾部窗口**取样本，现场 123 的样本
            # 正好从回合 A 的终态游程（1316）开始，若要求"先见 combat"就永远判不出边界。
            if combat_run == 0:
                combat_run_start = point
                # 在游程首帧一次性锁存「本段 combat 之前确有足量终态游程」：
                # 之后 terminal_run 会被清零，不能再依赖它判断。
                combat_after_terminal = terminal_run >= need
            combat_run += 1
            if combat_after_terminal and combat_run >= need_resume:
                last_resume = combat_run_start
            terminal_run = 0
            continue
        if text in _TERMINAL_LABELS:
            terminal_run += 1
            combat_run = 0
            combat_run_start = None
            combat_after_terminal = False
    return last_resume


def _interior_boundary_verdict(
    *,
    start: float,
    end: float,
    resume: float,
) -> tuple[str, float, float]:
    """跨回合前缀的处理裁决：小幅前缀→裁剪起点；大面积错位→整条拒绝。

    返回 ``(action, trimmed_sec, kept_sec)``，``action ∈ {"trim", "reject"}``。
    阈值：裁剪量既超过 ``INTERIOR_TRIM_MAX_SEC`` 又超过总时长的
    ``INTERIOR_TRIM_MAX_FRACTION``，或裁剪后剩余不足 ``MIN_ACTIVE_SEC`` ⇒ 拒绝。
    现场 123：总长 188.75s、前缀 118s、剩余 70.75s ⇒ 拒绝（不编造新起点）。
    """
    span = max(0.0, float(end) - float(start))
    trimmed = max(0.0, float(resume) - float(start))
    kept = max(0.0, float(end) - float(resume))
    if kept < MIN_ACTIVE_SEC:
        return "reject", trimmed, kept
    if trimmed > INTERIOR_TRIM_MAX_SEC and trimmed > INTERIOR_TRIM_MAX_FRACTION * span:
        return "reject", trimmed, kept
    return "trim", trimmed, kept


def _apply_interior_boundary_trim(
    item: dict[str, Any],
    *,
    resume: float,
    trimmed: float,
) -> None:
    """把起点前移到内部边界之后的重开战处，并**作废原起点的密扫证据**。

    原 ``start_delta`` / ``start_confidence`` 是针对旧起点算出来的，裁剪后不再成立；
    不清理会让下游（例如落库去重排序里"起点证据强"的判据）误以为这是一条起点
    有密扫证据的切片——现场形态：123 的起点本在回合 A 内部，裁到回合 B 起点后
    并没有新的密扫，必须按"起点待复核"对待。
    """
    item["start"] = round(float(resume), 3)
    item["start_refined"] = round(float(resume), 3)
    item["start_by"] = "interior_boundary_trim"
    item["interior_boundary_trim_sec"] = round(float(trimmed), 3)
    item["start_quality"] = "coarse"
    item["start_review_required"] = True
    # 起点密扫证据随起点作废（0.70 与 _stamp_broadcast_decision 的"未密扫"默认一致）
    item["start_delta"] = None
    item["start_confidence"] = 0.70
    item["boundary_refined"] = False
    item["broadcast_review_required"] = True


def _first_frozen_frames(
    samples: Iterable[tuple[float, str, float]],
    *,
    min_duration: float = PAUSE_MIN_SEC,
) -> float | None:
    """Return the start of an almost identical combat-frame run.

    This is a fallback for technical pauses where the HUD is not readable and
    therefore the timer-freeze detector has no samples.
    """
    run_start: float | None = None
    previous_ts: float | None = None
    previous_label: str | None = None
    for ts, label, delta in samples:
        ts = float(ts)
        if label == "combat" and previous_label == "combat" and delta <= 2.0 and previous_ts is not None:
            if run_start is None:
                run_start = previous_ts
            if ts - run_start >= min_duration:
                return run_start
        else:
            run_start = None
        previous_ts = ts
        previous_label = label
    return None


def audit_broadcast_phase_sequence(
    samples: list[tuple[float, str, float]],
    timer_samples: list[tuple[float, float | None, str]] | None = None,
    freeze_samples: list[tuple[float, str, float]] | None = None,
    *,
    start: float,
    end: float,
    scan_end: float | None = None,
    score_cutoff: float | None = None,
) -> tuple[float | None, str | None]:
    """Pure boundary decision used by production and unit tests.

    Returns ``(cutoff, reason)``. ``None`` means no exclusion was proven.
    """
    exclusion = _first_stable_exclusion(samples, timer_samples=timer_samples)
    frozen = _first_frozen_timer(timer_samples or [])
    frozen_frames = _first_frozen_frames(freeze_samples or [])
    # 若 OCR 已确认计时器正常递减，静态镜头不能单独被视为暂停；只有
    # 计时器不可读时才启用逐帧冻结兜底。
    freeze_candidate = frozen if frozen is not None else frozen_frames
    candidates = [item for item in (exclusion, freeze_candidate) if item is not None]

    # 阶段二（A-05）：双模级联融合 Observer HUD 比分跳变强证据
    # 比分跳变是官方赛事最权威的结束物理事实。若视觉模型已在更早前识别出 Replay
    # 或导播切出，则取较早者切除慢动作回放杂质；若视觉模型未能完全确凿识别回放，
    # 比分跳变时刻直接作为高置信度出点截断，彻底消除官方流因无结算横幅导致的丢片问题。
    if score_cutoff is not None and float(score_cutoff) > float(start):
        sc = float(score_cutoff)
        if candidates:
            earliest_visual = min(candidates)
            if earliest_visual < sc + 2.0:
                cutoff = earliest_visual
                reason = "broadcast_replay_or_non_game" if exclusion is not None else "broadcast_pause"
            else:
                cutoff = sc
                reason = "broadcast_observer_score_delta"
        else:
            cutoff = sc
            reason = "broadcast_observer_score_delta"
        boundary_end = float(end) if scan_end is None else float(scan_end)
        if cutoff - float(start) < MIN_ACTIVE_SEC:
            return float(start), "broadcast_no_active_span"
        return min(boundary_end, max(float(start), cutoff - 0.25)), reason

    if not candidates:
        return None, None
    cutoff = min(candidates)
    if cutoff > float(end):
        # 后视窗口只有在证明 OCR end 之后仍然处于交战时才允许延展；
        # 否则可能把下一回合的 Replay 误当成当前回合的结束。
        continued_combat = any(
            float(end) <= float(ts) < cutoff
            and label == "combat"
            and confidence >= 0.0
            for ts, label, confidence in samples
        )
        if not continued_combat:
            return None, None
    if cutoff - float(start) < MIN_ACTIVE_SEC:
        return float(start), "broadcast_no_active_span"
    reason = "broadcast_replay_or_non_game" if exclusion is not None else "broadcast_pause"
    boundary_end = float(end) if scan_end is None else float(scan_end)
    return min(boundary_end, max(float(start), cutoff - 0.25)), reason


# 赛事流入列门禁（room_handler._BROADCAST_VALID_END_BY）只认这两个出点来源。
_BROADCAST_VALID_END_BY = frozenset({"next_prep", "broadcast_exclusion"})


def _stamp_broadcast_decision(
    item: dict[str, Any],
    clf: ValorantFrameClassifier,
    *,
    cutoff: float | None = None,
    reason: str | None = None,
    cutoff_confidence: float | None = None,
) -> None:
    """证据驱动的赛事审计结果盖章。

    严禁在无物理截断证据（reason=none）且原出点为 next_combat/open_tail 时
    将 end_by 伪造成 broadcast_exclusion 或盲目提升为 vision_confirmed。
    """
    start = float(item.get("start", 0.0))
    end = float(item.get("end", 0.0))

    # 双向粗边界保留
    start_coarse = float(item.get("start_coarse", start))
    end_coarse = float(item.get("end_coarse", end))
    item["start_coarse"] = start_coarse
    item["end_coarse"] = end_coarse
    item["start_by"] = str(item.get("start_by") or "ocr_combat")

    # 起点证据：若已有真实精修结果则保留，否则保持未密扫状态（start_delta 为 None）
    if "start_refined" not in item:
        item["start_refined"] = start
    if "start_delta" not in item:
        item["start_delta"] = None
    if "start_confidence" not in item:
        item["start_confidence"] = 0.95 if item["start_delta"] is not None else 0.70

    item["broadcast_model_version"] = clf.model_version
    item["broadcast_model_provider"] = clf.provider

    # 双向物理证据完整才允许视为“边界已精修”；broadcast_review_required 与
    # auto-export 门禁必须跟随该结论，严禁只有出点证据就伪装成精确切片。
    _start_evidence = (
        item.get("start_delta") is not None
        and item.get("start_confidence") is not None
    )

    # 情况 1：找到稳定视觉排除证据 (replay, non_game, pause)
    if cutoff is not None and reason:
        item["end_refined"] = round(float(cutoff), 3)
        item["end"] = item["end_refined"]
        item["end_delta"] = round(abs(item["end_refined"] - end_coarse), 3)
        item["end_confidence"] = float(cutoff_confidence if cutoff_confidence is not None else 0.92)
        item["end_by"] = "broadcast_exclusion"
        item["broadcast_excluded_reason"] = reason
        item["broadcast_audit_reason"] = reason
        item["broadcast_audit"] = "passed"
        item["confirm_status"] = "vision_confirmed"
        _full_evidence = bool(
            _start_evidence
            and item.get("end_delta") is not None
            and item.get("end_confidence") is not None
        )
        item["boundary_refined"] = _full_evidence
        item["boundary_refined_by"] = "broadcast_audit_v2"
        item["broadcast_review_required"] = not _full_evidence
        item["start_quality"] = (
            "precise"
            if _start_evidence and float(item.get("start_confidence") or 0.0) >= 0.85
            else "coarse"
        )
        item["end_quality"] = "precise"
        item["start_review_required"] = item["start_quality"] != "precise"
        item["end_review_required"] = False
        return

    # 情况 2：OCR 具备明确的 next_prep 出点且复核通过
    orig_end_by = str(item.get("end_by", "") or "").strip().lower()
    if orig_end_by == "next_prep" and not item.get(
        "broadcast_next_prep_invalidated"
    ):
        item["end_refined"] = float(item.get("end_refined", end))
        if "end_delta" not in item:
            item["end_delta"] = round(abs(item["end_refined"] - end_coarse), 3) if item.get("end_refined_done") else None
        item["end_confidence"] = float(item.get("end_confidence", 0.90))
        item["end_by"] = "next_prep"
        item["broadcast_audit_reason"] = "next_prep"
        item["broadcast_audit"] = "passed"
        item["confirm_status"] = "vision_confirmed"
        _full_evidence = bool(
            _start_evidence
            and item.get("end_delta") is not None
            and item.get("end_confidence") is not None
        )
        item["boundary_refined"] = _full_evidence
        item["boundary_refined_by"] = "broadcast_audit_v2"
        item["broadcast_review_required"] = not _full_evidence
        item["start_quality"] = (
            "precise"
            if _start_evidence and float(item.get("start_confidence") or 0.0) >= 0.85
            else "coarse"
        )
        item["end_quality"] = "precise" if item.get("end_delta") is not None else "coarse"
        item["start_review_required"] = item["start_quality"] != "precise"
        item["end_review_required"] = item["end_quality"] != "precise"
        return

    # 情况 3：无截断证据 (reason=none/None)，且出点非 next_prep (如 next_combat / open_tail)
    # 严禁伪造 broadcast_exclusion，保持原 end_by 与 pending 状态。
    # 这是“已审计但待人工确认”，不能和尚未取得后视窗口的
    # pending_lookahead 混用同一队列状态。
    item["end_refined"] = float(end)
    item["end_delta"] = None
    item["end_confidence"] = 0.50
    item["broadcast_audit"] = "pending_no_exclusion"
    item["broadcast_audit_reason"] = "none"
    item["confirm_status"] = "pending"
    item["boundary_refined"] = False
    item["broadcast_review_required"] = True
    item["start_quality"] = "coarse"
    item["end_quality"] = "coarse"
    item["start_review_required"] = True
    item["end_review_required"] = True


def _stamp_broadcast_passed(
    item: dict[str, Any],
    clf: ValorantFrameClassifier,
    *,
    cutoff: float | None = None,
    reason: str | None = None,
    cutoff_confidence: float | None = None,
) -> None:
    """兼容旧接口的盖章入口，委托给证据驱动的 _stamp_broadcast_decision。"""
    _stamp_broadcast_decision(
        item,
        clf,
        cutoff=cutoff,
        reason=reason,
        cutoff_confidence=cutoff_confidence,
    )


def _stable_combat_run_start(
    samples: Iterable[tuple[float, str, float]],
    *,
    min_duration: float = START_GATE_COMBAT_MIN_SEC,
    require_after_non_combat: bool = False,
) -> float | None:
    """Return the first timestamp of a stable combat run within ``samples``.

    ``min_duration`` is wall-clock seconds, not frame count, so the gate remains
    meaningful if sampling rate changes.  When ``require_after_non_combat`` is
    true, a run starting at the very first scanned sample is not treated as a
    real onset; split-from-oversize chunks must show that their fixed chunk head
    did not accidentally start inside combat after a replay/non-game segment.
    """
    run_start: float | None = None
    run_last_ts: float | None = None
    seen_non_combat = False
    for ts, label, _confidence in samples:
        ts_val = float(ts)
        label = str(label)
        if label == "combat":
            if run_start is None:
                if require_after_non_combat and not seen_non_combat:
                    continue
                run_start = ts_val
            elif run_last_ts is not None and ts_val - run_last_ts > START_GATE_MAX_GAP_SEC:
                # 长时间缺帧/断层不能当作同一段连续交战；在已看到非游戏
                # 前缀时允许从断层后的 combat 重新作为潜在入点。
                if require_after_non_combat and not seen_non_combat:
                    run_start = None
                    continue
                run_start = ts_val
            run_last_ts = ts_val
            if run_start is not None and ts_val - run_start >= min_duration:
                return run_start
        else:
            seen_non_combat = True
            run_start = None
            run_last_ts = None
    return None


# A4/A3：起点实测视觉一致性窗口（秒）。
#
# ⚠️ 语义是**前视**窗口 ``[start, start + window]``，不是对称 ±window。
# 实测依据（2026-09-10 真实赛事录像 7 个回合，见
# docs/plans/valorant-broadcast-inpoint-workstream-20260910.md §2 2.2）：用对称
# ±2s 会得到 6/7 个回合 <0.8（例：健康入点的样本是 `buy,unknown,combat,combat`
# ——起点前本就该是购买阶段），从而把正常回合批量降级；改为前视后只有真正起点
# 可疑的回合（如样本为 `unknown,non_game,replay,replay`，即起点落在回放里）低分。
# 无样本时**不写值**（保留既有兜底），避免因缺样本批量降级。
_START_VISUAL_WINDOW_SEC = 2.0


def _start_visual_combat_ratio(
    samples: list[tuple[float, str, float]] | None,
    *,
    start: float,
    window: float = _START_VISUAL_WINDOW_SEC,
) -> float | None:
    """起点**向前** window 秒内视觉判为 ``combat`` 的样本占比。

    这是任务 A3 要求的**交叉证据**：入点是否正确，不能只看 ``start_delta``
    （粗扫与密扫之差，同源同盲），还要看起点之后的画面是否真在交战。
    返回 ``None`` 表示窗口内无样本——调用方须保留既有取值，不得写 0.0。
    """
    if not samples:
        return None
    try:
        start_f = float(start)
        window_f = float(window)
    except (TypeError, ValueError):
        return None
    in_window = [
        row
        for row in samples
        if len(row) >= 2 and start_f <= float(row[0]) <= start_f + window_f
    ]
    if not in_window:
        return None
    combat = sum(1 for row in in_window if str(row[1]) == "combat")
    return round(combat / len(in_window), 3)


def _start_window_replay_evidence(
    samples: list[tuple[float, str, float]] | None,
    *,
    start: float,
    window: float = _START_VISUAL_WINDOW_SEC,
) -> bool:
    """起点前视窗口内是否出现**回放/非游戏**帧（任务 A2 的显式化标注）。

    回放转场或回放水印（模型判 ``replay``）以及 ``non_game`` 画面出现在起点处，
    说明该处的"交战钟"读数很可能是回放画面里的——按 A2，**回放帧上的交战钟
    不得作为入点锚点**。

    ⚠️ 可达边界（2026-09-10 实测）：视觉模型**分不清"回放中的实战镜头"与实时交战**
    （该类 ``p_replay`` 仅 0.001–0.041，见根因文档 §1.3），故本判据只覆盖
    "起点落在回放转场/水印/非游戏画面"这一部分；"回放中的实战镜头"需要前置的
    OCR 侧回放检测（属 A7 范畴）。本函数只用于标注与审计，**不改动入点**。
    """
    if not samples:
        return False
    try:
        start_f = float(start)
        window_f = float(window)
    except (TypeError, ValueError):
        return False
    for row in samples:
        if len(row) < 2:
            continue
        ts = float(row[0])
        if start_f <= ts <= start_f + window_f and str(row[1]) in {"replay", "non_game"}:
            return True
    return False


# 入点门禁窗口内的「结构性否定」证据阈值：标签属于终态（回放/非游戏/结算）且连续
# 出现至少这么多帧，才认定「起点确实落在非交战内容里」（有证据的拒绝）。
_START_GATE_TERMINAL_EVIDENCE_FRAMES = 2


def _start_window_evidence(
    samples: list[tuple[float, str, float]] | None,
    *,
    start: float,
    window: float,
) -> str | None:
    """门禁窗口内是否存在**结构性否定**证据（回放 / 非游戏 / 结算）。

    存在的意义（2026-09-15，P1-4）：``no_stable_combat``（窗口里找不到稳定 combat
    锚点）有两种完全不同的成因，必须分开处置 ——

    - **有证据的否定**：窗口里出现连续 >=2 帧 ``replay``/``non_game``/``result``
      ⇒ 起点确实落在上一回合的回放尾段/结算画面里，这是既有拒绝路径的目标形态
      （旧实现整条 ``rejected_no_stable_combat_start`` 拒绝是正确的）；
    - **模型不确定**：窗口里只有 ``unknown``（或 ``buy`` 这类非终态标签）⇒
      模型看不清，不等于候选是假的。旧实现把两者一起拒绝，而拒绝是终态、
      人工确认也不复活，于是「交战在起点之后 15-35s 才开始」「前段画面模型读不准」
      的真实回合被永久丢弃（现场：漏回合投诉的主要成因之一）。

    返回拒绝理由字符串；无结构性证据返回 ``None``（= 不确定）。
    """
    if not samples:
        return None
    try:
        lower = float(start) - 0.5
        upper = float(start) + max(0.0, float(window)) + 0.5
    except (TypeError, ValueError):
        return None
    run = 0
    for ts, label, _confidence in samples:
        point = float(ts)
        if point < lower or point > upper:
            continue
        if str(label) in _TERMINAL_LABELS:
            run += 1
            if run >= _START_GATE_TERMINAL_EVIDENCE_FRAMES:
                return "replay_at_start"
            continue
        run = 0
    return None


def _apply_start_visual_confidence(
    item: dict[str, Any],
    samples: list[tuple[float, str, float]] | None,
) -> float | None:
    """用实测视觉一致性替换 ``start_confidence`` 的二值代理（任务 A4）。

    原实现（``_stamp_broadcast_decision`` 内）写的是
    ``0.95 if start_delta is not None else 0.70``——它只复述"有没有 delta"，
    于是 ``continuous_finalization`` 的 ``confidence < 0.8 → coarse`` 门永远
    给不出 ``boundary_refined`` 之外的额外证据。换成实测占比后，该门才真正
    具备判别力：起点不是交战画面的候选**评不上 precise**。

    窗口内无样本时返回 ``None`` 且**不修改** ``item``（保留兜底取值）。
    """
    ratio = _start_visual_combat_ratio(samples, start=float(item.get("start") or 0.0))
    if ratio is None:
        return None
    item["start_confidence"] = ratio
    item["start_confidence_source"] = "visual_combat_ratio"
    return ratio


def _a5_window_cap(item: dict[str, Any], *, start: float, end: float) -> float | None:
    """用 OCR 赛后回放块（A5 ``replay_segments``）压审计扫描窗口（不改出点判据）。

    粗 OCR 出点普遍比真出点晚 30~80s（实测 2026-09-12：167.5→134.11、855.8→775.05、
    1517.1→1496.75），审计尾部窗口 ``[end-30, end+45]`` 跟着一起漂后：抽帧/推理更贵，
    真出点还有被挤出窗口的风险（000035 的真出点 449.875 落在 448.125 之外）。

    OCR 侧 A5 已给出"赛后回放块"首帧，审计只需扫到该起点 + 小幅余量即可形成稳定终态
    游程。**只压窗口**：压窗后若拿不到 cutoff，调用方回退完整窗口重审（``a5_cap_retry``），
    因此判据与最终结论都不变。
    """
    segments = item.get("replay_segments")
    if not isinstance(segments, (list, tuple)) or not segments:
        return None
    starts: list[float] = []
    for seg in segments:
        if not isinstance(seg, (list, tuple)) or len(seg) < 2:
            continue
        try:
            seg_start = float(seg[0])
        except (TypeError, ValueError):
            continue
        # 只认候选区间内部、且不贴头的回放块（贴头的属于上一回合尾段）
        if start + MIN_ACTIVE_SEC <= seg_start <= end:
            starts.append(seg_start)
    if not starts:
        return None
    return min(starts) + A5_WINDOW_CAP_MARGIN_SEC


def _weak_ocr_end(item: dict[str, Any]) -> bool:
    """OCR 出点是否缺少强证据（可能晚到下一回合满钟/文件尾，需要向前回扫取证）。

    - next_prep / buy_phase：真出点就在其附近，或已由 next_prep_invalidated 走
      90s 扩窗路径 -> 不需要向前回扫；
    - result_ts 存在：尾窗左界已按 result_ts-10 锚定（转场必在窗内）-> 同样不需要；
    - next_combat / open_tail：只说明后面还有内容，不能证明切点没落在回放/买枪里
      —— 这正是「真出点在尾窗之前 60s 以外」的形态。
    """
    if item.get("result_ts") is not None:
        return False
    return str(item.get("end_by") or "").strip().lower() not in {"next_prep", "buy_phase"}


def _effective_lookahead_sec(
    *,
    finalize: bool,
    available_end: float | None,
    has_strong_ocr_end: bool,
    next_prep_invalidated: bool,
    lookahead_sec: float | None = None,
) -> float:
    """Return the tail lookahead budget used to search for the real end.

    OCR 强证据出点（result_ts / next_prep）只需 45s 后视；收尾/离线时录像已定格，
    也无谓拉长窗口。但**视觉已否决 OCR 出点**（``next_prep_invalidated``）时必须
    给足 ``END_LOOKAHEAD_SEC``：否则 ``scan_end`` 恒 == ``end + 45``，而"否决后继续
    扩展"的判定条件恒成立 → 候选无限 pending，收尾永远算不出真出点。
    实测 2026-09-12：351.312 的假出点 403.125 连跑 8 轮都停在 448.125，而真出点
    449.875 就在窗外一格（给足 90s 后一次即定稿 broadcast_exclusion/precise）。
    """
    if has_strong_ocr_end:
        return 45.0
    if not next_prep_invalidated and (finalize or available_end is None):
        return 45.0
    return float(lookahead_sec if lookahead_sec is not None else END_LOOKAHEAD_SEC)


def _start_gate_decision(
    samples: list[tuple[float, str, float]],
    *,
    start: float,
    split_from_oversize: bool,
    onset_tolerance_sec: float = START_GATE_ONSET_TOLERANCE_SEC,
) -> tuple[float | None, str | None]:
    """Decide whether a broadcast candidate starts on real combat.

    Returns ``(new_start, reason)``:
    - ``(start, None)``: existing start is already a stable combat onset.
    - ``(new_start, "moved_from_non_combat")``: only for split_from_oversize
      fixed chunks, move to the first stable combat inside the chunk.
    - ``(None, "no_stable_combat")``: no reliable combat onset at the OCR start
      (ordinary candidates) or nowhere inside a split chunk; caller rejects.
    """
    if not samples:
        return None, "no_stable_combat"
    # 固定切块若块头恰好落在 replay/non_game/result 上，必须重新找到块内
    # 真实 combat 锚点；若块头本来就是 combat（可能恰为真实回合起点），
    # 则连续 combat 游程即可证明入点有效，不必强制要求块内先出现非游戏段。
    require_after_non_combat = bool(split_from_oversize) and str(samples[0][1]) != "combat"
    new_start = _stable_combat_run_start(
        samples,
        require_after_non_combat=require_after_non_combat,
    )
    if new_start is None:
        return None, "no_stable_combat"
    if new_start <= float(start) + max(0.0, float(onset_tolerance_sec)):
        # 偏差在容差内视为同一入点：候选起点那一帧的低置信/unknown 抖动不得
        # 否决整条真实回合（见 START_GATE_ONSET_TOLERANCE_SEC 注释）。
        return float(start), None
    # 普通 OCR 候选起点必须已经落在真实 combat 上。官方解说流中 replay/result/
    # non_game 开头通常属于上一回合的回放尾段；后移生成“下一条真回合”会与后续
    # OCR 候选重复，因此直接拒绝，只有 split_from_oversize 固定块才允许块内后移。
    if not split_from_oversize:
        return None, "no_stable_combat"
    return float(new_start), "moved_from_non_combat"


def _expand_oversize_candidates(
    rounds: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """把超长候选切成 ≤MAX_BROADCAST_ROUND_SEC 的子候选，避免整条丢弃。

    粗 OCR FSM 在官方解说流上漏检 prep 横幅时，会把“交战+结算+回放+买枪”
    合并成一个超长候选（实测 328s，内部包含 2–3 个真实回合）。旧实现直接
    rejected_long_or_invalid，内部回合全部丢失。按固定长度切块后，每个子块
    走同一套视觉审计：块内首先遇到稳定 Replay/暂停截断就能恢复出第一个
    真实回合，后续块内的回合同样有机会被检出。

    子块继承父候选的 start_by/end_by/round_key（追加 -sN 后缀避免去重冲突），
    并把 end_coarse 重置为块尾，使截断后的 end_delta 保持在正常小量级。
    """
    expanded: list[dict[str, Any]] = []
    for candidate in rounds:
        if not isinstance(candidate, dict):
            expanded.append(candidate)
            continue
        try:
            start = float(candidate.get("start", 0.0) or 0.0)
            end = float(candidate.get("end", 0.0) or 0.0)
        except (TypeError, ValueError):
            expanded.append(candidate)
            continue
        if end - start <= MAX_BROADCAST_ROUND_SEC:
            expanded.append(candidate)
            continue
        _log.warning(
            "赛事候选超长分裂: %.1f-%.1f (%.1fs) -> %d 个子候选",
            start,
            end,
            end - start,
            int((end - start) // MAX_BROADCAST_ROUND_SEC) + 1,
        )
        chunk_start = start
        index = 0
        while end - chunk_start > MAX_BROADCAST_ROUND_SEC:
            chunk_end = chunk_start + MAX_BROADCAST_ROUND_SEC
            chunk = dict(candidate)
            chunk["start"] = round(chunk_start, 3)
            chunk["end"] = round(chunk_end, 3)
            chunk["start_coarse"] = round(chunk_start, 3)
            chunk["end_coarse"] = round(chunk_end, 3)
            chunk["split_from_oversize"] = True
            chunk["split_index"] = index
            base_key = str(chunk.get("round_key") or "").strip()
            if not base_key:
                base_key = f"round-{int(round(chunk_start / 10.0)):06d}"
            chunk["round_key"] = f"{base_key}-s{index}" if not base_key.endswith(f"-s{index}") else base_key
            expanded.append(chunk)
            chunk_start = chunk_end
            index += 1
        last_chunk = dict(candidate)
        last_chunk["start"] = round(chunk_start, 3)
        last_chunk["end"] = round(end, 3)
        last_chunk["start_coarse"] = round(chunk_start, 3)
        last_chunk["end_coarse"] = round(end, 3)
        last_chunk["split_from_oversize"] = True
        last_chunk["split_index"] = index
        base_key = str(last_chunk.get("round_key") or "").strip()
        if not base_key:
            base_key = f"round-{int(round(chunk_start / 10.0)):06d}"
        last_chunk["round_key"] = f"{base_key}-s{index}" if not base_key.endswith(f"-s{index}") else base_key
        expanded.append(last_chunk)
    return expanded


# 同父分裂碎片「头尾合并」：只补偿丢失的回合前半段，不放宽任何出点判据。
#
# 现场（2026-09-14，round-000070）：真实回合 791.6-930.8（139s），被
# MAX_BROADCAST_ROUND_SEC=150 切成两半**独立审计**：
#   * 头碎片 791.6-845.6 → 头内找不到出点证据（845.6 只是块边界，不是回合结束）
#     → pending_no_exclusion → 导出门禁丢弃；
#   * 尾碎片 845.6-963.6 → 审计出真出点 930.75（result_ts 928.609 + 2.5s 结算尾）
#     → passed / broadcast_exclusion。
# 最终导出的是"同一真实回合的后半段"（845.6-930.75，85s），**前半段 54s 丢失**，
# 切片从回合中间开始。这里把头碎片的（已过入点门禁的）起点并回尾碎片，
# 出点仍取尾碎片的审计结论——起点证据取自头、出点证据取自尾，不伪造任一边界。
_SPLIT_MERGE_GAP_SEC = 2.0  # 头尾相接容差（块边界同点，实测 gap=0）
_SPLIT_MERGE_MIN_EXTEND_SEC = 5.0  # 至少能往前扩这么多才有意义
# 允许被合并的"头碎片"必须是**未定论**（没拿到自己的出点证据）：它既没被拒
# （被拒说明入点/区间有问题，不能把它的起点并进别人的回合），也没 accepted
# （accepted 说明它自己就是一个真实回合，那就该各成一片，不许粘连）。
_SPLIT_HEAD_INCONCLUSIVE_AUDITS = frozenset({
    "pending_no_exclusion",
    "pending_lookahead",
    "skipped",
})
# 合并后从"头碎片"继承的起点字段：合并起点 == 头碎片起点，故这些字段必须一起搬，
# 否则 start/start_coarse/start_refined 互相矛盾（例如 start 早于 start_coarse）。
_SPLIT_MERGE_START_FIELDS = (
    "start",
    "start_coarse",
    "start_refined",
    "start_delta",
    "start_confidence",
    "start_confidence_source",
    "start_by",
    "start_quality",
    "start_review_required",
    "broadcast_start_gate",
    "broadcast_start_gate_from",
    "broadcast_start_gate_to",
    "broadcast_start_gate_scan_end",
    "broadcast_start_gate_detail",
)


def _split_family_base_key(round_key: object) -> str:
    """``round-000070-s1`` → ``round-000070``；非分裂子块返回空串。"""
    key = str(round_key or "").strip()
    if not key:
        return ""
    base, sep, suffix = key.rpartition("-s")
    if not sep or not suffix.isdigit() or not base:
        return ""
    return base


def _split_fragment_index(item: dict[str, Any]) -> int | None:
    if not item.get("split_from_oversize"):
        return None
    try:
        return int(item.get("split_index"))
    except (TypeError, ValueError):
        return None


def _numeric(item: dict[str, Any], key: str) -> float | None:
    try:
        value = item.get(key)
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _split_merge_geometry_ok(head: dict[str, Any], tail: dict[str, Any]) -> bool:
    """合并的几何判据（不含任何结论判据）——台账链式传递也复用它。"""
    head_end = _numeric(head, "end")
    tail_start = _numeric(tail, "start")
    head_start = _numeric(head, "start")
    tail_end = _numeric(tail, "end")
    if None in (head_end, tail_start, head_start, tail_end):
        return False
    # 头尾相接（块边界同点）——不接纳中间隔着内容的碎片，避免把两段拼成假回合
    if abs(head_end - tail_start) > _SPLIT_MERGE_GAP_SEC:
        return False
    # 扩展量有意义，且合并后不越过"超长回合"红线（>150s 本身就是异常形态）
    if tail_start - head_start < _SPLIT_MERGE_MIN_EXTEND_SEC:
        return False
    merged_duration = tail_end - head_start
    if merged_duration < MIN_ACTIVE_SEC or merged_duration > MAX_BROADCAST_ROUND_SEC:
        return False
    # 头碎片的起点必须是 OCR 交战锚点（不是门禁后移前的位置推算）
    return str(head.get("start_by") or "").strip().lower() in ("ocr_combat", "refined_combat")


def _can_absorb_split_head(head: dict[str, Any], tail: dict[str, Any]) -> bool:
    """头碎片能否并入尾碎片（结论判据 + 几何判据，任一不满足即保持现状）。"""
    if tail.get("split_merged"):
        return False  # 幂等：已合并过的尾碎片不再重复吸收
    if str(tail.get("broadcast_audit") or "").lower() != "passed":
        return False
    if str(tail.get("end_by") or "").lower() != "broadcast_exclusion":
        return False
    if str(tail.get("end_quality") or "").lower() != "precise":
        return False
    if tail.get("end_review_required"):
        return False
    if str(head.get("broadcast_audit") or "").lower() not in _SPLIT_HEAD_INCONCLUSIVE_AUDITS:
        return False
    return _split_merge_geometry_ok(head, tail)


def _absorb_split_head(tail: dict[str, Any], head: dict[str, Any]) -> None:
    """把尾碎片的起点并回头的起点，并留下可追溯的合并来源。"""
    original_start = _numeric(tail, "start")
    for field in _SPLIT_MERGE_START_FIELDS:
        if field in head:
            tail[field] = head[field]
    tail["split_merged"] = True
    absorbed = [str(head.get("round_key") or "")]
    chained = head.get("chained_from")
    if isinstance(chained, list):
        # 链式：起点其实来自更早的碎片（如 s2 借的是 s1 记录的 s0 起点），
        # 来源必须把中间碎片一起列出来，否则无法从字段回溯真实起点出处。
        absorbed.extend(str(key) for key in chained if str(key) not in absorbed)
    tail["split_merged_from"] = absorbed
    tail["split_merged_original_start"] = original_start
    tail["split_merged_gap_sec"] = round(
        abs(float(_numeric(head, "end") or 0.0) - float(original_start or 0.0)), 3
    )
    # 头碎片若在**本批**里（真实 item），标注被接管；若来自台账（跨批，头碎片
    # 早已交付过），这里只改到临时还原的 dict，真实条目靠 tail 的合并来源字段回溯。
    # 无论哪条路径都不改 broadcast_audit——门禁的失败关闭语义必须原样保留。
    head["superseded_by_round_key"] = str(tail.get("round_key") or "")
    head["broadcast_audit_reason"] = "superseded_by_split_merge"


# 分裂族台账存进 audit_cache 的专用键（前缀限制在候选 key 命名空间之外）。
# 必须跨调用存活：正常运行期 `deferred_audit=True`（room_handler:7520），审计由
# 后台微步骤**一次只推进一个子块**（audit_targets = expanded_rounds[:1]），
# 头尾碎片根本不在同一批里——同批合并覆盖不到主路径，故需要台账。
_SPLIT_FAMILY_CACHE_KEY = "__split_family_ledger__"

# 已定稿「真实回合」跨度台账（同样存 audit_cache）：收尾缺口补扫据此排除已覆盖区间。
#
# 为什么必须放 audit_cache（而不是某个调用方的 runtime_state）：正常运行期
# `deferred_audit=True`，**插件不审计**，定稿由 room_handler 的后台微步完成；收尾期
# 又是插件在同步审计。两条路径都调本函数，而 audit_cache 是两条路径共享的同一份
# （`_rs_state['broadcast_audit_cache']`）⇒ 只有写在这里，补扫才看得见另一条路径的结论。
# 现场（2026-09-14）：round-000015-s0(154.1-246.7)、round-000071-s0(712.2-802.2) 由
# 后台路径在 11:43/11:52 定稿，11:54 收尾补扫仍把 153-257 / 711-813 判为"无候选"
# 并合成新候选（最终以父键定稿）⇒ 同一回合两条重叠条目。
_FINALIZED_SPANS_CACHE_KEY = "__finalized_round_spans__"
_FINALIZED_SPANS_MAX = 128


def _is_finalized_round(item: dict[str, Any]) -> bool:
    """是否已定稿为**真实回合**（passed）。

    只认 passed：只有"这段确实有回合"才足以把它从补扫缺口里去掉。被拒的区间
    **保持可补扫**——那正是缺口补扫这张网的意义（宁可多扫一次，也不要因为一条
    拒绝结论把后面真漏掉的回合永久遮住）。
    """
    return str(item.get("broadcast_audit") or "").strip().lower() == "passed"


def _remember_finalized_spans(
    audit_cache: dict[str, Any] | None,
    items: Iterable[dict[str, Any]],
) -> None:
    """把已定稿真实回合的跨度记进 audit_cache（去重 + 有界）。"""
    if not isinstance(audit_cache, dict):
        return
    spans = audit_cache.get(_FINALIZED_SPANS_CACHE_KEY)
    if not isinstance(spans, list):
        spans = []
    changed = False
    for item in items:
        if not isinstance(item, dict) or not _is_finalized_round(item):
            continue
        try:
            start = float(item.get("start"))
            end = float(item.get("end"))
        except (TypeError, ValueError):
            continue
        if end <= start:
            continue
        span = [round(start, 3), round(end, 3)]
        if span not in spans:
            spans.append(span)
            changed = True
    if changed:
        audit_cache[_FINALIZED_SPANS_CACHE_KEY] = spans[-_FINALIZED_SPANS_MAX:]


def finalized_spans(audit_cache: dict[str, Any] | None) -> list[list[float]]:
    """已定稿真实回合的跨度（供收尾缺口补扫排除已覆盖区间）。"""
    if not isinstance(audit_cache, dict):
        return []
    spans = audit_cache.get(_FINALIZED_SPANS_CACHE_KEY)
    if not isinstance(spans, list):
        return []
    out: list[list[float]] = []
    for span in spans:
        if isinstance(span, (list, tuple)) and len(span) >= 2:
            try:
                out.append([float(span[0]), float(span[1])])
            except (TypeError, ValueError):
                continue
    return out


def _split_head_record(item: dict[str, Any]) -> dict[str, Any]:
    """台账里保存的"头碎片事实"：足以让后续批次沿用它的起点。"""
    return {
        "round_key": str(item.get("round_key") or ""),
        "end": _numeric(item, "end"),
        "audit": str(item.get("broadcast_audit") or "").lower(),
        "start_fields": {
            field: item[field] for field in _SPLIT_MERGE_START_FIELDS if field in item
        },
    }


def _split_head_from_record(record: Any) -> dict[str, Any] | None:
    """把台账记录还原成"头碎片形状"的 dict，复用同一套判据与合并实现。"""
    if not isinstance(record, dict):
        return None
    start_fields = record.get("start_fields")
    if not isinstance(start_fields, dict):
        return None
    head = dict(start_fields)
    head["round_key"] = record.get("round_key")
    head["end"] = record.get("end")
    head["broadcast_audit"] = record.get("audit")
    chained = record.get("chained_from")
    if isinstance(chained, list):
        head["chained_from"] = list(chained)
    return head


def _split_family_ledger(audit_cache: dict[str, Any] | None) -> dict[str, Any]:
    """取（必要时创建）分裂族台账。audit_cache 为 None 时返回空台账（不跨调用）。"""
    if not isinstance(audit_cache, dict):
        return {}
    ledger = audit_cache.get(_SPLIT_FAMILY_CACHE_KEY)
    if not isinstance(ledger, dict):
        ledger = {}
        audit_cache[_SPLIT_FAMILY_CACHE_KEY] = ledger
    return ledger


def _record_split_fragment(ledger: dict[str, Any], item: dict[str, Any]) -> None:
    """把本碎片记进台账，并把"有效起点"沿族链传递。

    链式传递是必要的：3 块以上的族里，中段碎片自己可能也**未定论**，但它仍
    承接了更前面碎片的起点。台账记"有效起点"（而非碎片自身起点），最后的
    定稿尾碎片才能一次借到整族最前面的真实起点；否则链断在中段、前半段依旧丢失。
    结论判据不参与链式传递（它只决定"能否导出"，不决定"这一族的真实起点在哪"）。
    """
    if not isinstance(ledger, dict):
        return
    index = _split_fragment_index(item)
    base = _split_family_base_key(item.get("round_key"))
    if index is None or not base:
        return
    record = _split_head_record(item)
    if index > 0:
        prev_head = _split_head_from_record((ledger.get(base) or {}).get(index - 1))
        current_flat = _split_head_from_record(record)
        # 注意：判据吃的是**扁平** dict（start/end/start_by 在顶层），
        # 台账记录是嵌套结构，必须先还原再判，否则 _numeric 全取到 None、链式静默失效。
        if (
            prev_head is not None
            and current_flat is not None
            and _split_merge_geometry_ok(prev_head, current_flat)
        ):
            record["start_fields"] = {
                field: prev_head[field]
                for field in _SPLIT_MERGE_START_FIELDS
                if field in prev_head
            }
            record["chained_from"] = [
                str(prev_head.get("round_key") or ""),
                *(prev_head.get("chained_from") or []),
            ]
    ledger.setdefault(base, {})[index] = record


def _merge_split_family_fragments(
    items: list[dict[str, Any]],
    sink: list[BroadcastAuditOutcome] | None = None,
) -> int:
    """同父分裂碎片合并（返回合并条数）。见本段顶部现场说明。"""
    families: dict[str, dict[int, dict[str, Any]]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        index = _split_fragment_index(item)
        if index is None:
            continue
        base = _split_family_base_key(item.get("round_key"))
        if not base:
            continue
        # 同族同 index 只保留一个（重复 key 不应出现；出现则先到者胜，保持确定性）
        families.setdefault(base, {}).setdefault(index, item)

    merged = 0
    touched: list[dict[str, Any]] = []
    for by_index in families.values():
        for index in sorted(by_index):
            head = by_index.get(index)
            tail = by_index.get(index + 1)
            if head is None or tail is None or head is tail:
                continue
            if not _can_absorb_split_head(head, tail):
                continue
            original_tail_start = _numeric(tail, "start")
            _absorb_split_head(tail, head)
            touched.extend((tail, head))
            merged += 1
            new_start = _numeric(tail, "start")
            _log.warning(
                "赛事分裂碎片头尾合并: %s + %s -> %.1f-%.1f (补回 %.1fs 前半段), "
                "出点仍取尾碎片审计结论 end_by=%s",
                head.get("round_key"),
                tail.get("round_key"),
                float(new_start or 0.0),
                float(_numeric(tail, "end") or 0.0),
                float(original_tail_start or 0.0) - float(new_start or 0.0),
                tail.get("end_by"),
            )

    if merged and sink:
        # outcome 在记录时做了浅拷贝（``dict(candidate)``），且 dataclass 是 frozen
        # ⇒ 只能按 key 重建 outcome，否则调用方拿到的是合并前的旧边界
        # （「审计结果永不丢失」的反面）。
        by_key = {
            str(item.get("round_key") or ""): item
            for item in touched
            if isinstance(item, dict)
        }
        for index, outcome in enumerate(sink):
            candidate = getattr(outcome, "candidate", None)
            if not isinstance(candidate, dict):
                continue
            current = by_key.get(str(candidate.get("round_key") or ""))
            if current is None:
                continue
            sink[index] = BroadcastAuditOutcome(
                status=outcome.status,
                candidate=current,
                reason=outcome.reason,
                retry_after_duration=outcome.retry_after_duration,
            )
    return merged


def _split_tail_is_authoritative(item: dict[str, Any]) -> bool:
    return (
        str(item.get("broadcast_audit") or "").lower() == "passed"
        and str(item.get("end_by") or "").lower() == "broadcast_exclusion"
        and str(item.get("end_quality") or "").lower() == "precise"
        and not item.get("end_review_required")
    )


def _reconcile_split_family_fragments(
    items: list[dict[str, Any]],
    audit_cache: dict[str, Any] | None,
    sink: list[BroadcastAuditOutcome] | None = None,
) -> int:
    """分裂族合并（返回合并条数）：同批成对 + 跨批台账。

    为什么必须两路都做：正常运行期 `deferred_audit=True`，审计由后台微步骤
    **一次推进一个子块**，头碎片与尾碎片天然不在同一批；只在同批里合并等于
    在主路径上不生效。台账（存 audit_cache，按房间跨调用存活）保存头碎片的
    "起点事实"，待尾碎片在后续批次定稿时再补回前半段。

    只做一件事：把**头碎片的起点**接到尾碎片上。出点永远取尾碎片自己的审计
    结论，任何一边都不伪造——头碎片仍以原来的未定论状态入列（不导出）。
    """
    merged = _merge_split_family_fragments(items, sink)
    if not isinstance(audit_cache, dict):
        # 没有 audit_cache（个别调用方不传）⇒ 无法跨批；同批合并已经做完
        return merged
    ledger = _split_family_ledger(audit_cache)

    newly_merged: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        index = _split_fragment_index(item)
        base = _split_family_base_key(item.get("round_key"))
        if index is None or not base:
            continue
        # 尾碎片在后续批次定稿：用台账里 index-1 的起点事实补回前半段
        if index > 0 and not item.get("split_merged") and _split_tail_is_authoritative(item):
            record = (ledger.get(base) or {}).get(index - 1)
            head = _split_head_from_record(record)
            if head is not None and _can_absorb_split_head(head, item):
                original_tail_start = _numeric(item, "start")
                _absorb_split_head(item, head)
                newly_merged.append(item)
                merged += 1
                new_start = _numeric(item, "start")
                _log.warning(
                    "赛事分裂碎片跨批合并: %s(台账) + %s -> %.1f-%.1f (补回 %.1fs 前半段), "
                    "出点仍取尾碎片审计结论 end_by=%s",
                    head.get("round_key"),
                    item.get("round_key"),
                    float(new_start or 0.0),
                    float(_numeric(item, "end") or 0.0),
                    float(original_tail_start or 0.0) - float(new_start or 0.0),
                    item.get("end_by"),
                )

    for item in items:
        _record_split_fragment(ledger, item)

    if newly_merged and sink:
        by_key = {
            str(item.get("round_key") or ""): item for item in newly_merged
        }
        for index, outcome in enumerate(sink):
            candidate = getattr(outcome, "candidate", None)
            if not isinstance(candidate, dict):
                continue
            current = by_key.get(str(candidate.get("round_key") or ""))
            if current is None:
                continue
            sink[index] = BroadcastAuditOutcome(
                status=outcome.status,
                candidate=current,
                reason=outcome.reason,
                retry_after_duration=outcome.retry_after_duration,
            )
    return merged


def _fill_pending_for_undecided(
    candidates: list[dict[str, Any]],
    sink: list[BroadcastAuditOutcome] | None,
    *,
    available_end: float | None,
    reason: str = "cancelled_before_decision",
) -> int:
    """取消路径：为尚未记录的候选补发 pending，使 outcome 批次保持完整。

    超长候选分裂后一次调用会产出多个 outcome，调用方按"整批"消费：只要批次里
    还有非终态项，原槽位就保留；批次全为终态才弹出槽位。若审计被 cancel_check
    中断，已定稿的子候选（rejected/accepted）会留在 sink 里，而**尚未开始**的
    子候选没有记录——调用方会把这批误判为完整终态并弹掉槽位，未审计的子候选
    静默消失。这里补齐 pending，使"取消时的批次"与"正常结束时的批次"语义一致。
    """
    if sink is None:
        return 0
    decided = set()
    for outcome in sink:
        candidate = getattr(outcome, "candidate", None)
        if isinstance(candidate, dict):
            key = str(candidate.get("round_key") or "").strip()
            if key:
                decided.add(key)
    filled = 0
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        key = str(candidate.get("round_key") or "").strip()
        if key and key in decided:
            continue
        retry_after = available_end
        if retry_after is None:
            try:
                retry_after = float(candidate.get("end", 0.0))
            except (TypeError, ValueError):
                retry_after = None
        _record_audit_outcome(
            sink,
            status="pending",
            candidate=candidate,
            reason=reason,
            retry_after_duration=retry_after,
        )
        filled += 1
    if filled:
        _log.info("赛事审计取消：补发 pending 保持批次完整 %d 项", filled)
    return filled


def audit_broadcast_rounds(
    rounds: list[dict[str, Any]],
    video_path: str,
    *,
    ffmpeg_path: str = "ffmpeg",
    cancel_check: Callable[[], bool] | None = None,
    classifier: ValorantFrameClassifier | None = None,
    sample_fps: float = 1.0,
    available_end: float | None = None,
    audit_cache: dict[str, Any] | None = None,
    finalize: bool = False,
    lookahead_sec: float | None = None,
    _outcome_sink: list[BroadcastAuditOutcome] | None = None,
    frame_provider: Any | None = None,
    max_media_step_sec: float | None = None,
) -> list[dict[str, Any]]:
    """Audit OCR candidates; fail closed when the visual model is unavailable.

    ``available_end`` is used by continuous recording. If the lookahead reaches
    beyond the currently written file, the candidate is returned as
    ``pending_lookahead`` so the caller can retry it after the next scan instead
    of publishing an unverified early end.
    """
    if not rounds:
        return []
    rounds = _expand_oversize_candidates(rounds)
    import numpy as np

    from lsc.analyzer.valorant_ocr_rounds import (
        _read_top_anchors,
        extract_frames_cancellable,
    )
    from lsc.utils.cancellable_ffmpeg import FFmpegCancelled

    if classifier is None:
        from lsc.analyzer.valorant_frame_classifier import ValorantFrameClassifier

        clf = ValorantFrameClassifier(profile="broadcast")
    else:
        clf = classifier
    clf.load()
    stable_prob = float(clf.thresholds.get("stable_prob", 0.55))
    class_stable_prob = getattr(clf, "class_stable_prob", {})
    output: list[dict[str, Any]] = []

    def _raise_if_cancelled(stage: str) -> None:
        if cancel_check and cancel_check():
            raise FFmpegCancelled(f"cancelled during broadcast audit ({stage})")

    def _extract(
        start_sec: float,
        end_sec: float,
        fps: float,
        *,
        overlap_sec: float = 0.0,
    ) -> list[tuple[float, Any]]:
        if frame_provider is not None:
            return frame_provider.get_frames(
                video_path,
                start_sec=start_sec,
                end_sec=end_sec,
                fps=fps,
                ffmpeg_path=ffmpeg_path,
                cancel_check=cancel_check,
                overlap_sec=overlap_sec,
            )
        return extract_frames_cancellable(
            video_path,
            start_sec=start_sec,
            end_sec=end_sec,
            fps=fps,
            ffmpeg_path=ffmpeg_path,
            cancel_check=cancel_check,
            overlap_sec=overlap_sec,
        )

    # Batch callers (offline/finalization or a future multi-candidate scheduler)
    # can pay one FFmpeg seek for overlapping 1fps gate/tail windows.  Per-item
    # calls below then reuse the provider and only fill genuinely missing gaps.
    if frame_provider is not None and len(rounds) > 1 and hasattr(
        frame_provider, "prefetch_ranges"
    ):
        prefetch_ranges: list[tuple[float, float]] = []
        for candidate in rounds:
            if not isinstance(candidate, dict):
                continue
            try:
                candidate_start = max(0.0, float(candidate.get("start", 0.0)))
                candidate_end = float(candidate.get("end", 0.0))
            except (TypeError, ValueError):
                continue
            if candidate_end <= candidate_start:
                continue
            gate_limit = (
                MAX_BROADCAST_ROUND_SEC
                if candidate.get("split_from_oversize")
                else START_GATE_SCAN_LIMIT_SEC
            )
            gate_end = min(candidate_end, candidate_start + gate_limit)
            if max_media_step_sec is not None:
                # 在线微步骤：预取也必须受同一个媒体预算约束。分裂块的门禁窗口
                # 是 MAX_BROADCAST_ROUND_SEC(150s)，4 块一次性预取实测 ≈514 帧
                # ≈23s，已超过 20s 墙钟预算 ⇒ 每轮都在预取阶段被腰斩、零结论交付。
                # 预取只是批量加速；门禁/尾部自己的 _extract 仍会按需抽帧并经
                # FrameProvider 缓存，判定窗口与语义完全不变。
                gate_end = min(
                    gate_end,
                    candidate_start + max(1.0, float(max_media_step_sec)),
                )
            if gate_end > candidate_start:
                prefetch_ranges.append((candidate_start, gate_end))

            tail_start = max(
                candidate_start,
                candidate_end - BROADCAST_AUDIT_TAIL_LOOKBACK_SEC,
            )
            result_ts = candidate.get("result_ts")
            if isinstance(result_ts, (int, float)):
                tail_start = min(
                    tail_start,
                    max(candidate_start, float(result_ts) - 10.0),
                )
            has_strong_end = (
                result_ts is not None
                or str(candidate.get("end_by", "")).lower()
                in ("buy_phase", "next_prep")
            )
            tail_lookahead = (
                45.0
                if (finalize or available_end is None or has_strong_end)
                else float(
                    lookahead_sec
                    if lookahead_sec is not None
                    else END_LOOKAHEAD_SEC
                )
            )
            tail_end = min(
                candidate_start + MAX_BROADCAST_ROUND_SEC,
                candidate_end + tail_lookahead,
            )
            if available_end is not None:
                tail_end = min(tail_end, max(candidate_start, float(available_end)))
            if max_media_step_sec is not None:
                tail_end = min(
                    tail_end,
                    tail_start + max(1.0, float(max_media_step_sec)),
                )
            if tail_end > tail_start:
                prefetch_ranges.append((tail_start, tail_end))
        if prefetch_ranges:
            frame_provider.prefetch_ranges(
                video_path,
                prefetch_ranges,
                fps=max(0.5, float(sample_fps)),
                ffmpeg_path=ffmpeg_path,
                cancel_check=cancel_check,
                merge_gap_sec=2.0,
            )

    for original in rounds:
        _raise_if_cancelled("candidate")
        item = dict(original)
        try:
            start = float(item["start"])
            end = float(item["end"])
        except (KeyError, TypeError, ValueError):
            item["broadcast_audit"] = "rejected_invalid_timestamp"
            _record_audit_outcome(
                _outcome_sink,
                status="rejected",
                candidate=item,
                reason="invalid_timestamp",
            )
            continue

        start_coarse = float(item.get("start_coarse", start))
        end_coarse = float(item.get("end_coarse", end))
        item["start_coarse"] = start_coarse
        item["end_coarse"] = end_coarse
        if end <= start or end - start > MAX_BROADCAST_ROUND_SEC:
            item["broadcast_audit"] = "rejected_long_or_invalid"
            _log.warning(
                "赛事回合审计拒绝异常时长: %.1f-%.1f (%.1fs)",
                start, end, end - start,
            )
            _record_audit_outcome(
                _outcome_sink,
                status="rejected",
                candidate=item,
                reason="long_or_invalid",
            )
            continue
        # Prefer the immutable round identity.  Start-gating/refinement may move
        # ``start`` by tens of seconds; a start-derived key would orphan the
        # cached gate/tail evidence and repeat the whole audit under a new key.
        cache_key = str(item.get("round_key") or f"{round(start, 1):.1f}")
        cache_item: dict[str, Any] | None = None
        if isinstance(audit_cache, dict):
            existing_cache = audit_cache.get(cache_key)
            if isinstance(existing_cache, dict):
                cache_item = existing_cache
                if cache_item.get("completed"):
                    cached_end = cache_item.get("final_end")
                    if isinstance(cached_end, (int, float)) and float(cached_end) > start:
                        item["end"] = round(float(cached_end), 3)
                    item["broadcast_audit_scan_end"] = round(
                        float(cache_item.get("scan_end", end)), 3
                    )
                    stamped = cache_item.get("stamped_decision")
                    if isinstance(stamped, dict):
                        item.update(stamped)
                        cached_start_refined = stamped.get("start_refined")
                        if (
                            isinstance(cached_start_refined, (int, float))
                            and float(cached_start_refined) > 0
                        ):
                            item["start"] = round(float(cached_start_refined), 3)
                    else:
                        _stamp_broadcast_decision(item, clf)
                    output.append(item)
                    _record_audit_outcome(
                        _outcome_sink,
                        status="accepted",
                        candidate=item,
                        reason="cached_complete",
                    )
                    continue
            if cache_item is not None and cache_item.get("start_gate_rejected"):
                # 入点门禁已判定该候选没有真实 combat 锚点，跨重试保持拒绝，
                # 防止后续带尾部样本的调用把同一伪回合重新放行。
                item["broadcast_start_gate"] = str(
                    cache_item.get("start_gate_reason") or "no_stable_combat"
                )
                item["broadcast_start_gate_scan_end"] = cache_item.get("start_gate_scan_end")
                item["broadcast_audit"] = "rejected_no_stable_combat_start"
                item["broadcast_review_required"] = True
                _record_audit_outcome(
                    _outcome_sink,
                    status="rejected",
                    candidate=item,
                    reason="no_stable_combat_start",
                )
                continue
        if cache_item is not None and cache_item.get("start_gate_inconclusive"):
            # 入点结论是「模型不确定」（见下方 inconclusive 分支）：门禁窗口在过去，
            # 证据不会变，本轮不必重判，但标记必须每轮都带上 —— 否则尾部审计的
            # pending 分支会让候选看起来像「入点已通过」，最终盖章时又会把
            # confirm_status 改回 vision_confirmed（变成可自动导出）。
            item["broadcast_start_gate"] = "inconclusive"
            item["broadcast_start_gate_scan_end"] = cache_item.get("start_gate_scan_end")
            item["start_quality"] = "coarse"
            item["start_review_required"] = True
            item["broadcast_review_required"] = True
        # 复用在之前在线轮次已完成的入点门禁结论：在线阶段常先返回
        # pending_lookahead，后移后的起点必须跨重试保留，否则重审会用回
        # 原始的（错误）起点。
        if cache_item is not None and cache_item.get("start_gate_done"):
            moved_to = cache_item.get("start_gate_moved_to")
            if isinstance(moved_to, (int, float)) and float(moved_to) > 0:
                item["start"] = round(float(moved_to), 3)
                item["start_refined"] = item["start"]
                item["start_delta"] = None
                item["broadcast_start_gate"] = str(
                    cache_item.get("start_gate_action") or "moved_from_non_combat"
                )
                item["broadcast_start_gate_from"] = cache_item.get("start_gate_from")
                item["broadcast_start_gate_to"] = round(float(moved_to), 3)
                start = float(item["start"])
        # OCR 只提供候选结束点，不能把它当作赛事流的最终出点。向后再看一
        # 段时间：如果初步 end 后仍是交战，直到真正 Replay/非游戏画面才截断。
        # 普通 POV 不经过本模块，因此不会增加普通直播的分析成本或改变其边界。
        # 停录收尾或 OCR 强证据（明确胜利结算横幅或进入下回合买枪）时，
        # lookahead 适度收窄至 45s（已足以覆盖 5-10s 横幅 + 10-20s 回放 + 观察缓冲），
        # 避免在录像已结束时继续抽无谓的长窗口，大幅减轻抽帧与 DirectML 推理耗时。
        next_prep_invalidated = bool(
            cache_item is not None
            and cache_item.get("next_prep_invalidated")
        )
        if next_prep_invalidated:
            item["broadcast_next_prep_invalidated"] = True
        has_strong_ocr_end = (
            not next_prep_invalidated
            and (
                item.get("result_ts") is not None
                or str(item.get("end_by", "")).lower() in ("buy_phase", "next_prep")
            )
        )
        effective_lookahead = _effective_lookahead_sec(
            finalize=finalize,
            available_end=available_end,
            has_strong_ocr_end=has_strong_ocr_end,
            next_prep_invalidated=next_prep_invalidated,
            lookahead_sec=lookahead_sec,
        )
        scan_end = min(
            start + MAX_BROADCAST_ROUND_SEC,
            max(end, end + effective_lookahead),
        )
        # A5 缩窗：OCR 已给出赛后回放块起点时，只需扫到该起点 + 余量即可判定截断。
        # 压窗后若拿不到 cutoff，本轮结束会走 a5_cap_retry 回退到完整窗口（见下方分支），
        # 因此这里只影响成本、不影响判据；重试过的不再压窗。
        _a5_cap = None
        if not (cache_item or {}).get("a5_cap_retried"):
            _a5_cap = _a5_window_cap(item, start=start, end=end)
        if _a5_cap is not None and _a5_cap < scan_end:
            scan_end = _a5_cap
            item["broadcast_audit_window_uncapped_end"] = round(float(scan_end), 3)
            item["broadcast_audit_window_cap"] = round(float(_a5_cap), 3)
        effective_scan_end = scan_end
        lookahead_incomplete = (
            available_end is not None
            and float(available_end) + 1.0 < scan_end
        )
        if available_end is not None:
            effective_scan_end = min(scan_end, max(start, float(available_end)))
        if cache_item is None:
            cache_item = {
                "samples": [],
                "timer_samples": [],
                "freeze_samples": [],
                "scanned_end": start,
            }
            if isinstance(audit_cache, dict):
                audit_cache[cache_key] = cache_item

        # ---- 入点门禁（start gating）----
        # 在线/收尾/离线统一执行：候选的起点是已录制的过去，头部必然可用，
        # 因此候选一旦形成就立即判定入点，强停时回放开头候选也会被当场拦截，
        # 不再等收尾才执行。split_from_oversize 固定块头照旧整块重找锚点。
        # 普通候选只查 START_GATE_SCAN_LIMIT_SEC；起点不在真实 combat 上时
        # 直接拒绝，不再扩展扫描/后移——那只会把上一回合的回放尾段误生成新切片。
        run_start_gate = (
            not cache_item.get("start_gate_done")
            and (bool(item.get("split_from_oversize")) or not item.get("start_delta"))
        )
        _start_gate_rejected_candidate = False
        _start_gate_pending_candidate = False
        if run_start_gate:
            # 起点门禁的头部样本单独存放到 start_gate_samples，不混入尾部审计
            # 的 samples/scanned_end。否则在线首轮会因缓存“已扫过头部”而从
            # 头部开始连续扫完整回合，暴露回合中段的 replay/result 转场，导致
            # 出点被过早截断、回合不完整。
            gate_limit = float(
                cache_item.get("start_gate_next_limit")
                or (
                    MAX_BROADCAST_ROUND_SEC
                    if item.get("split_from_oversize")
                    else START_GATE_SCAN_LIMIT_SEC
                )
            )
            gate_scan_end: float | None = None
            gate_window_covered = True
            while True:
                gate_scan_end = min(
                    float(end),
                    float(start) + float(gate_limit),
                )
                # 在线极端情况：头部窗口还没被当前录制覆盖时，不能用截断的头部下
                # 结论，跳过判定交给 pending_lookahead 重试，防止把尚未写入的
                # 后续 combat 误判为“找不到真实入点”而提前拒绝。
                gate_window_covered = (
                    available_end is None
                    or float(available_end) + 0.5 >= gate_scan_end
                )
                if not gate_window_covered:
                    break
                existing_gate_samples = [
                    tuple(row)
                    for row in cache_item.get("start_gate_samples", [])
                    if isinstance(row, (list, tuple)) and len(row) == 3
                ]
                existing_gate_cover = (
                    max(float(ts) for ts, _, _ in existing_gate_samples)
                    if existing_gate_samples
                    else start
                )
                existing_gate_min = (
                    min(float(ts) for ts, _, _ in existing_gate_samples)
                    if existing_gate_samples
                    else start
                )
                # 注意：pending 缓存可能只覆盖尾部（例如 120s~150s），max 很高但
                # 头部是空的；此时必须补抽 [start, existing_min) 这一段头部。
                if existing_gate_min > start + 0.5:
                    gate_frames = _extract(
                        start,
                        min(gate_scan_end, existing_gate_min),
                        max(0.5, float(sample_fps)),
                    )
                elif gate_scan_end > existing_gate_cover + 0.5:
                    gate_frames = _extract(
                        max(start, existing_gate_cover),
                        gate_scan_end,
                        max(0.5, float(sample_fps)),
                    )
                else:
                    gate_frames = []
                if not gate_frames and not existing_gate_samples:
                    # 无任何可判定帧：保持未定稿，由后续尾部审计决定 pending/拒绝。
                    break
                merged_gate_samples = {
                    round(float(ts), 3): (float(ts), str(label), float(conf))
                    for ts, label, conf in existing_gate_samples
                }
                if gate_frames:
                    _raise_if_cancelled("start gate inference")
                    gate_probs = _predict_broadcast_batch(clf, [img for _, img in gate_frames])
                    for _gate_index, ((gate_ts, _), gate_row) in enumerate(
                        zip(gate_frames, gate_probs, strict=True)
                    ):
                        _raise_if_cancelled("start gate samples")
                        gate_label, gate_confidence = _stable_visual_label(
                            gate_row,
                            stable_prob=stable_prob,
                            class_stable_prob=class_stable_prob,
                        )
                        gate_candidate_index = max(
                            range(len(_CLASS_NAMES)),
                            key=lambda class_index: float(gate_row[class_index]),
                        )
                        _log_model_confidence(
                            clf,
                            stage="start_gate",
                            timestamp_sec=float(gate_ts),
                            probabilities=gate_row,
                            stable_label=gate_label,
                            confidence=gate_confidence,
                            threshold=float(
                                class_stable_prob.get(
                                    _CLASS_NAMES[gate_candidate_index], stable_prob
                                )
                            ),
                        )
                        merged_gate_samples[round(float(gate_ts), 3)] = (
                            float(gate_ts),
                            gate_label,
                            gate_confidence,
                        )
                gate_samples = [
                    merged_gate_samples[key]
                    for key in sorted(merged_gate_samples)
                ]
                gate_samples = _stabilize_broadcast_samples(gate_samples)
                cache_item["start_gate_samples"] = gate_samples
                # 入点门禁只看门禁窗口内的样本；不混入尾部审计 samples。
                decision_samples = [
                    sample
                    for sample in gate_samples
                    if float(sample[0]) <= gate_scan_end + 0.5
                ]
                original_start = float(start)
                new_start, gate_reason = _start_gate_decision(
                    decision_samples,
                    start=start,
                    split_from_oversize=bool(item.get("split_from_oversize")),
                )
                # P1-4（2026-09-15）：起点缺少稳定 combat 锚点时先分清「有证据的
                # 否定」与「模型不确定」——结构性否定证据（回放/非游戏/结算）在窗口
                # 里出现才算证据；只有 unknown/buy 这类非终态标签时，模型看不清 ≠
                # 候选是假的，不许直接判死。不确定时先把门禁窗 15s→35s 再判一次
                # （样本续存 start_gate_samples，重判只多抽 ≤20s 媒体）。
                _gate_terminal_evidence = _start_window_evidence(
                    decision_samples,
                    start=original_start,
                    window=float(gate_scan_end) - original_start,
                )
                if (
                    gate_reason == "no_stable_combat"
                    and not bool(item.get("split_from_oversize"))
                    and _gate_terminal_evidence is None
                    and float(gate_limit) < float(START_GATE_EXTENDED_SCAN_LIMIT_SEC)
                    # 候选自身必须还有可扩的头部（短候选的门禁窗本来就到 end，
                    # 扩窗等于白跑一轮微步骤）。
                    and float(end) > float(start) + float(gate_limit) + 0.5
                    and (
                        available_end is None
                        or float(available_end) + 0.5
                        >= float(start) + float(START_GATE_EXTENDED_SCAN_LIMIT_SEC)
                    )
                ):
                    cache_item["start_gate_next_limit"] = float(
                        START_GATE_EXTENDED_SCAN_LIMIT_SEC
                    )
                    if max_media_step_sec is not None:
                        # 在线：扩窗单独算一个微步骤，本批不为此多烧预算；下一轮
                        # 用更宽的门禁窗复判（start_gate_next_limit 已被读入）。
                        item["broadcast_audit"] = "pending_lookahead"
                        item["broadcast_audit_step"] = "start_gate_extend"
                        item["_audit_continue_ready"] = True
                        output.append(item)
                        _record_audit_outcome(
                            _outcome_sink,
                            status="pending",
                            candidate=item,
                            reason="start_gate_extend",
                            retry_after_duration=float(available_end or gate_scan_end),
                        )
                        _log.info(
                            "赛事回合入点门禁不确定，扩窗至 %.0fs 复判（下一微步骤）: %.1f-%.1f",
                            float(START_GATE_EXTENDED_SCAN_LIMIT_SEC),
                            original_start,
                            end,
                        )
                        _start_gate_pending_candidate = True
                        break
                    # 离线/收尾单发调用：无重试队列，直接在本函数内扩窗重判。
                    gate_limit = float(START_GATE_EXTENDED_SCAN_LIMIT_SEC)
                    _log.info(
                        "赛事回合入点门禁不确定，就地扩窗至 %.0fs 复判: %.1f-%.1f",
                        float(START_GATE_EXTENDED_SCAN_LIMIT_SEC),
                        original_start,
                        end,
                    )
                    continue
                # 普通候选不做起点后移：起点不是真实 combat 就当场拒绝，
                # 省掉额外抽帧/推理（缓解解说流滞后）；split_from_oversize 已经
                # 一次扫到整块上限，无需扩展。
                cache_item.pop("start_gate_next_limit", None)
                cache_item["start_gate_done"] = True
                if gate_reason == "no_stable_combat":
                    if _gate_terminal_evidence is None:
                        # 扩窗后仍无 combat 锚点、也无结构性否定证据 ⇒ 属于**模型
                        # 不确定**，不是「有证据的拒绝」：保留候选（出点审计照常跑），
                        # 入点按 coarse + 待复核交付，并强制 confirm_status=pending
                        # 禁止自动导出（人工确认后可导出，与「入点 coarse 只是起得
                        # 略早」的既有产品规则一致）。旧实现把它记成终态拒绝并在前端
                        # 删除，人工确认也不复活 —— 真实回合就此永久丢失。
                        item["broadcast_start_gate"] = "inconclusive"
                        item["broadcast_start_gate_scan_end"] = round(gate_scan_end, 3)
                        item["broadcast_start_gate_detail"] = "no_visual_evidence"
                        item["start_quality"] = "coarse"
                        item["start_review_required"] = True
                        item["broadcast_review_required"] = True
                        cache_item["start_gate_inconclusive"] = True
                        cache_item["start_gate_reason"] = gate_reason
                        cache_item["start_gate_scan_end"] = round(gate_scan_end, 3)
                        _log.info(
                            "赛事回合入点门禁不确定（无 combat 锚点、无回放/非游戏证据）: "
                            "%.1f-%.1f (scan_end=%.1f) —— 保留候选待人工确认",
                            original_start,
                            end,
                            gate_scan_end,
                        )
                        break
                    item["broadcast_start_gate"] = gate_reason
                    item["broadcast_start_gate_scan_end"] = round(gate_scan_end, 3)
                    item["broadcast_audit"] = "rejected_no_stable_combat_start"
                    item["broadcast_review_required"] = True
                    cache_item["start_gate_rejected"] = True
                    cache_item["start_gate_reason"] = gate_reason
                    cache_item["start_gate_scan_end"] = round(gate_scan_end, 3)
                    # A2 显式化：若起点前视窗口内出现回放/非游戏帧，单独标注
                    # "起点落在回放里"——拒绝结论不变（仍走既有 no_stable_combat
                    # 链路），只增加可审计依据，便于统计该成因占比。
                    if _start_window_replay_evidence(decision_samples, start=original_start):
                        item["broadcast_start_gate_detail"] = "replay_at_start"
                        cache_item["start_gate_detail"] = "replay_at_start"
                        _log.info(
                            "赛事回合入点回放否决(A2): start=%.1f 起点窗口含回放/非游戏帧",
                            original_start,
                        )
                    elif _gate_terminal_evidence is not None:
                        # 结构性否定证据出现在窗口更远处（15-35s 扩展窗内）：同样是
                        # 「起点落在非交战内容里」，只是不在前 2s。单独标注成因，
                        # 与 A2 的前 2s 回放证据区分开，便于统计占比。
                        item["broadcast_start_gate_detail"] = "non_combat_in_gate_window"
                        cache_item["start_gate_detail"] = "non_combat_in_gate_window"
                        _log.info(
                            "赛事回合入点门禁拒绝(扩展窗内非交战): start=%.1f, scan_end=%.1f",
                            original_start,
                            gate_scan_end,
                        )
                    _log.info(
                        "赛事回合入点门禁拒绝: %.1f-%.1f (split=%s, scan_end=%.1f)",
                        original_start,
                        end,
                        bool(item.get("split_from_oversize")),
                        gate_scan_end,
                    )
                    _record_audit_outcome(
                        _outcome_sink,
                        status="rejected",
                        candidate=item,
                        reason="no_stable_combat_start",
                    )
                    _start_gate_rejected_candidate = True
                    break
                if new_start is not None and abs(new_start - original_start) > 1e-6:
                    item["start"] = round(new_start, 3)
                    item["start_refined"] = round(new_start, 3)
                    # 视觉门禁只提供 1fps 的粗锚点，不伪造精修 delta；保留
                    # start_delta=None 使 broadcast_review_required 仍为 true，
                    # 人工仍可复核后再导出/进草稿。
                    item["start_delta"] = None
                    item["start_by"] = str(item.get("start_by") or "ocr_combat")
                    item["broadcast_start_gate"] = "moved_from_non_combat"
                    item["broadcast_start_gate_from"] = round(original_start, 3)
                    item["broadcast_start_gate_to"] = round(new_start, 3)
                    # 在线阶段可能先返回 pending_lookahead，后移结论必须写入
                    # cache，重审时复用（见上方 start_gate_done 复用块）。
                    cache_item["start_gate_moved_to"] = round(new_start, 3)
                    cache_item["start_gate_action"] = "moved_from_non_combat"
                    cache_item["start_gate_from"] = round(original_start, 3)
                    _log.info(
                        "赛事回合入点门禁后移: %.1f -> %.1f (reason=%s)",
                        original_start,
                        new_start,
                        gate_reason or "moved_from_non_combat",
                    )
                    start = float(item["start"])
                else:
                    item["broadcast_start_gate"] = "ok"
                    item["broadcast_start_gate_from"] = round(original_start, 3)
                    cache_item["start_gate_action"] = "ok"
                # A4/A3：入点门禁已通过，用**实测**视觉一致性替换 start_confidence
                # 的二值代理（0.95/0.70）。窗口内无样本时不写值，保留兜底，
                # 避免因缺样本把广播回合批量降级为 coarse。
                _apply_start_visual_confidence(item, gate_samples)
                break
        if _start_gate_rejected_candidate:
            continue
        if _start_gate_pending_candidate:
            continue
        if (
            run_start_gate
            and max_media_step_sec is not None
            and cache_item.get("start_gate_done")
        ):
            # A completed start gate is one bounded micro-step by itself.  Tail
            # lookahead starts on the next turn so one candidate cannot hold
            # the analysis resource for gate + tail + dense refine at once.
            item["broadcast_audit"] = "pending_lookahead"
            item["broadcast_audit_step"] = "start_gate_complete"
            item["_audit_continue_ready"] = True
            output.append(item)
            _record_audit_outcome(
                _outcome_sink,
                status="pending",
                candidate=item,
                reason="start_gate_complete",
                retry_after_duration=float(available_end or end),
            )
            continue
        cached_samples = [
            tuple(row)
            for row in cache_item.get("samples", [])
            if isinstance(row, (list, tuple)) and len(row) == 3
        ]
        cached_timer_samples = [
            tuple(row)
            for row in cache_item.get("timer_samples", [])
            if isinstance(row, (list, tuple)) and len(row) == 3
        ]
        cached_freeze_samples = [
            tuple(row)
            for row in cache_item.get("freeze_samples", [])
            if isinstance(row, (list, tuple)) and len(row) == 3
        ]
        try:
            cached_scanned_end = float(cache_item.get("scanned_end", start))
        except (TypeError, ValueError):
            cached_scanned_end = start

        # 首次扫描只看候选尾部，减少已经由 OCR 确认的交战区间的重复抽帧。
        # 如果 OCR 提供了 result_ts，则把它作为更早的尾部锚点，覆盖“结束点
        # 误判得过晚”的情况；后续增量扫描从 cache 的 scanned_end 继续。
        if cached_samples:
            audit_start = max(start, cached_scanned_end - 1.0)
        else:
            audit_start = max(start, end - BROADCAST_AUDIT_TAIL_LOOKBACK_SEC)
            if item.get("split_from_oversize"):
                # 分裂块是固定长度切块，没有真实 OCR 出点：回放/非游戏可能落在
                # 块内任意位置，只扫尾部 30s 会漏掉块中部的回合边界（实测：块内
                # 22s 回放未被发现，切片跨回合且带污染内容）。整块从头扫起，
                # 单次仍按 max_media_step_sec 分块（微步骤预算不变）。
                audit_start = max(start, start)
            result_ts = item.get("result_ts")
            if isinstance(result_ts, (int, float)):
                audit_start = min(audit_start, max(start, float(result_ts) - 10.0))
            # 回扫锚点：**真正的尾窗左界**（只在首轮、decided 之前记录一次）。
            # 微步骤模式下后续调用的 extract_start 会漂到 scanned_end 附近，
            # 若用它当回扫起点，回扫会先把已扫过的尾部重扫一遍（实测要多花
            # 9 个微步骤）——回扫必须从尾窗左界开始向前走。
            cache_item["backward_sweep_anchor"] = round(float(audit_start), 3)
        extract_start = max(start, audit_start)
        if cached_samples and cached_scanned_end >= effective_scan_end - 0.5:
            extract_start = effective_scan_end
        micro_step_incomplete = False
        if max_media_step_sec is not None:
            media_budget = max(1.0, float(max_media_step_sec))
            step_base = max(extract_start, cached_scanned_end)
            if effective_scan_end > step_base + media_budget:
                effective_scan_end = step_base + media_budget
                micro_step_incomplete = True
        frames = _extract(
            extract_start,
            effective_scan_end,
            max(0.5, float(sample_fps)),
        )
        _raise_if_cancelled("tail extraction")
        if not frames and not cached_samples:
            # 收尾/离线（finalize）时录制文件已定格：后视窗口结构性不足
            # （scan_end 超出可用末尾）不再是"等待更多媒体"的理由。若继续返回
            # pending，候选会被写回待审计队列且 `_last_audit_dur` 被钉到文件
            # 时长，此后 room_handler 的 ready/probe 判定（`_dur >= c_end+needed`
            # 与 `_dur >= last_dur+12`）永不再满足 → 候选永久滞留 → 收尾
            # `pending_audit` 恒真 → 无限补扫 / 卡死。finalize 阶段必须收敛，
            # 直接按无帧判定终态。
            if lookahead_incomplete and not finalize:
                item["broadcast_audit"] = "pending_lookahead"
                item["broadcast_audit_scan_end"] = round(scan_end, 3)
                item["broadcast_audit_available_end"] = round(float(available_end), 3)
                output.append(item)
                _record_audit_outcome(
                    _outcome_sink,
                    status="pending",
                    candidate=item,
                    reason="lookahead_incomplete",
                    retry_after_duration=scan_end,
                )
                continue
            item["broadcast_audit"] = "rejected_no_frames"
            _record_audit_outcome(
                _outcome_sink,
                status="rejected",
                candidate=item,
                reason="no_frames",
            )
            continue
        probs = _predict_broadcast_batch(clf, [img for _, img in frames]) if frames else []
        _raise_if_cancelled("tail inference")
        samples: list[tuple[float, str, float]] = list(cached_samples)
        timer_samples: list[tuple[float, float | None, str]] = list(cached_timer_samples)
        freeze_samples: list[tuple[float, str, float]] = list(cached_freeze_samples)
        # 1fps 已足够覆盖持续数秒的赛事 Replay/结果转场；计时器 OCR 只在
        # 低频周期或模型给出非 combat 疑点时触发，避免每个窗口重复跑大量 OCR。
        timer_stride = max(
            1,
            int(round(max(1.0, float(sample_fps)) * BROADCAST_TIMER_SAMPLE_INTERVAL_SEC)),
        )
        last_timer_ocr_ts = -999.0
        prev_sample_label: str | None = None
        online_timer_ocr_count = 0
        for index, (ts, image) in enumerate(frames):
            _raise_if_cancelled("tail samples")
            row = probs[index]
            label, confidence = _stable_visual_label(
                row, stable_prob=stable_prob, class_stable_prob=class_stable_prob,
            )
            candidate_index = max(
                range(len(_CLASS_NAMES)), key=lambda class_index: float(row[class_index])
            )
            _log_model_confidence(
                clf,
                stage="tail",
                timestamp_sec=float(ts),
                probabilities=row,
                stable_label=label,
                confidence=confidence,
                threshold=float(
                    class_stable_prob.get(_CLASS_NAMES[candidate_index], stable_prob)
                ),
            )
            samples.append((float(ts), label, confidence))
            if index == 0:
                frame_delta = 255.0
            else:
                previous_image = frames[index - 1][1]
                frame_delta = float(
                    np.mean(np.abs(image.astype(np.float32) - previous_image.astype(np.float32)))
                )
            freeze_samples.append((float(ts), label, frame_delta))
            # 计时器 OCR 是审计最贵步骤（真实解说流实测占单回合 ~92%，CPU 下 ~1.2s/帧）。
            # 阶段一（A-04）：实施惰性采样——
            #   - combat 帧按 stride：_first_frozen_timer 需 combat 计时器序列检测暂停
            #   - non_game/buy/result 帧：当标签发生转移（首次进入），或距上一次采点 >= 2.0s 时触发；
            #     避免对连续相同 non_game 帧每一帧都跑通用 OCR，大幅减轻 CPU 负荷。
            should_run_timer = False
            if (
                (
                    max_media_step_sec is None
                    and label == "combat"
                    and index % timer_stride == 0
                )
                or (
                    label in _TIMER_OCR_LABELS
                    and (
                        label != prev_sample_label
                        or (float(ts) - last_timer_ocr_ts >= 2.0)
                    )
                    # 在线微步骤只需一个排除帧计时器来否决
                    # “交战钟仍在走”。正常 combat 的周期 OCR 在
                    # DirectML 机器上可比整个视觉批次还慢，暂停
                    # 仍由连续静帧证据检出；收尾/离线审计保留
                    # 完整计时器序列。
                    and (
                        max_media_step_sec is None
                        or online_timer_ocr_count < 1
                    )
                )
            ):
                should_run_timer = True

            if should_run_timer:
                _raise_if_cancelled("timer OCR")
                timer = None
                try:
                    timer, _, _ = _read_top_anchors(image)
                except Exception as exc:  # noqa: BLE001 - OCR 是辅助证据
                    _log.debug("赛事暂停计时器审计 OCR 失败: %s", exc)
                timer_samples.append((float(ts), timer, label))
                last_timer_ocr_ts = float(ts)
                if max_media_step_sec is not None:
                    online_timer_ocr_count += 1
            prev_sample_label = label
            # 微步骤可能在下一帧 OCR 期间被抢占。每帧都将
            # 已完成的证据写入 cache，重试从最后成功 PTS 继续，
            # 避免超时后反复支付同一批推理/OCR 成本。
            if max_media_step_sec is not None:
                cache_item["samples"] = list(samples)
                cache_item["timer_samples"] = list(timer_samples)
                cache_item["freeze_samples"] = list(freeze_samples)
                cache_item["scanned_end"] = max(
                    cached_scanned_end,
                    float(ts),
                )
            _raise_if_cancelled("tail checkpoint")
        # 仅保留每个 PTS 的最新值，避免 pending 回合跨窗口重审时列表膨胀。
        samples_by_ts = {round(float(ts), 3): (float(ts), label, float(conf)) for ts, label, conf in samples}
        timer_by_ts = {
            round(float(ts), 3): (float(ts), timer, label)
            for ts, timer, label in timer_samples
        }
        freeze_by_ts = {
            round(float(ts), 3): (float(ts), label, float(delta))
            for ts, label, delta in freeze_samples
        }
        samples = [samples_by_ts[key] for key in sorted(samples_by_ts)]
        timer_samples = [timer_by_ts[key] for key in sorted(timer_by_ts)]
        freeze_samples = [freeze_by_ts[key] for key in sorted(freeze_by_ts)]
        cache_item["samples"] = samples
        cache_item["timer_samples"] = timer_samples
        cache_item["freeze_samples"] = freeze_samples
        cache_item["scanned_end"] = max(cached_scanned_end, effective_scan_end)

        # 尾部窗口若完全没有 combat，说明候选 end 可能落在长回放之后；
        # 扩展回候选起点做一次兜底，优先保证结束边界不被尾部窗口误放行。
        # 兜底全扫单回合至多做一次，避免跨增量窗口或收尾重试重复全区间抽帧。
        # 触发条件二：弱出点候选在尾窗内拿不到任何排除证据时，由下方
        # broadcast_audit_step="backward_sweep" 分派显式申请一次向前回扫。
        # 必须用**持久标志**而不是复判样本内容：回扫第一步就会把本回合的 combat
        # 帧带进 samples，若仍用「无 combat」当条件，回扫会在第一步之后立刻停住，
        # 永远走不到真出点那次转场（旧实现的一次性 fallback 正是这个形态）。
        _need_backward_sweep = (
            not any(label == "combat" for _, label, _ in samples)
            or bool(cache_item.get("backward_sweep_requested"))
        )
        if (
            _need_backward_sweep
            and extract_start > start + 0.5
            and not cache_item.get("fallback_full_scanned")
        ):
            fallback_target_end = float(
                cache_item.get("fallback_full_target_end")
                or cache_item.get("backward_sweep_anchor")
                or extract_start
            )
            cache_item["fallback_full_target_end"] = fallback_target_end
            # 从尾窗向前搜索，优先找到紧邻 Replay/结算的
            # 最后一段 combat。正向从候选起点扫在 100s 回合上
            # 通常要等 4–5 个调度周期，倒序分块多数只需
            # 1–2 步；样本合并后仍按 PTS 排序，判定语义不变。
            fallback_end = min(
                fallback_target_end,
                float(
                    cache_item.get("fallback_full_cursor")
                    or fallback_target_end
                ),
            )
            fallback_start = float(start)
            if max_media_step_sec is not None:
                fallback_start = max(
                    fallback_start,
                    fallback_end - max(1.0, float(max_media_step_sec)),
                )
            # 有界向前回扫的安全阀：不得越过「尾窗左界 - 回扫预算」。非分裂候选
            # 跨度 <= MAX_BROADCAST_ROUND_SEC(150s) 时该项恒 <= start，仅兜住异常数据；
            # clamp 到 fallback_end 保证 _extract 的 [start,end) 合法。
            fallback_start = max(
                fallback_start,
                float(start),
                float(end)
                - BROADCAST_AUDIT_TAIL_LOOKBACK_SEC
                - BROADCAST_AUDIT_BACKWARD_SWEEP_MAX_SEC,
            )
            fallback_start = min(fallback_start, fallback_end)
            _raise_if_cancelled("fallback extraction")
            # 只补抽尚未扫描的头部 [start, extract_start]：尾窗 [extract_start,
            # effective_scan_end] 的样本已在 samples 中，重抽整段会重复抽帧+重复
            # 推理（收尾超时主因之一）。按 ts 合并后覆盖区间与重抽整段完全一致。
            full_frames = _extract(
                fallback_start,
                fallback_end,
                max(0.5, float(sample_fps)),
            )
            _raise_if_cancelled("fallback inference")
            if full_frames:
                full_probs = _predict_broadcast_batch(clf, [img for _, img in full_frames])
                full_samples: list[tuple[float, str, float]] = []
                for _, ((full_ts, _), full_row) in enumerate(zip(full_frames, full_probs, strict=True)):
                    _raise_if_cancelled("fallback samples")
                    full_label, full_confidence = _stable_visual_label(
                        full_row,
                        stable_prob=stable_prob,
                        class_stable_prob=class_stable_prob,
                    )
                    full_candidate_index = max(
                        range(len(_CLASS_NAMES)),
                        key=lambda class_index: float(full_row[class_index]),
                    )
                    _log_model_confidence(
                        clf,
                        stage="full",
                        timestamp_sec=float(full_ts),
                        probabilities=full_row,
                        stable_label=full_label,
                        confidence=full_confidence,
                        threshold=float(
                            class_stable_prob.get(
                                _CLASS_NAMES[full_candidate_index], stable_prob
                            )
                        ),
                    )
                    full_samples.append((float(full_ts), full_label, full_confidence))
                samples_by_ts = {
                    round(float(ts), 3): (float(ts), label, float(conf))
                    for ts, label, conf in [*samples, *full_samples]
                }
                samples = [samples_by_ts[key] for key in sorted(samples_by_ts)]
                cache_item["samples"] = samples
            cache_item["fallback_full_cursor"] = fallback_start
            if cache_item.get("backward_sweep_requested"):
                cache_item["backward_sweep_scanned_to"] = round(fallback_start, 3)
            if fallback_start > float(start) + 0.5:
                # 原实现在此绕过 18s 媒体预算，一次回扫
                # 90s+ 并长时间占住粗扫共用的 OCR/ONNX 锁。
                # 现在回扫也严格分块，且持久化 cursor。
                micro_step_incomplete = True
            else:
                cache_item["fallback_full_scanned"] = True
                cache_item.pop("fallback_full_cursor", None)
                cache_item.pop("fallback_full_target_end", None)
        samples = _stabilize_broadcast_samples(samples)
        if (
            str(item.get("end_by", "")).lower() == "next_prep"
            and not cache_item.get("next_prep_invalidated")
            and _has_immediate_combat_after(samples, end)
        ):
            cache_item["next_prep_invalidated"] = True
            item["broadcast_next_prep_invalidated"] = True
            item["broadcast_next_prep_invalidated_reason"] = (
                "combat_continues_after_ocr_next_prep"
            )
            # 可观测性：记录被否决的 OCR 出点原值。该出点此时只是"搜索锚点"，
            # 真出点必须由视觉证据给出（见 _effective_lookahead_sec：否决后不再
            # 钉死 +45s 窄窗，否则收尾永远算不出真出点、候选无限 pending）。
            item["broadcast_ocr_end_invalidated"] = round(float(end), 3)
            _log.warning(
                "赛事回合 OCR next_prep 被视觉否决（出点后仍持续交战）: "
                "start=%.1f, false_end=%.1f",
                start,
                end,
            )
        item_score_cutoff = item.get("score_cutoff") or item.get("score_end_ts")
        cand_score_cutoff = float(item_score_cutoff) if item_score_cutoff is not None else None
        cutoff, reason = audit_broadcast_phase_sequence(
            samples,
            timer_samples,
            # 分裂块从块头整段起扫：逐帧"冻结"兜底会把块头静态画面（买枪/观察位）
            # 误判为技术暂停并给出贴头 cutoff，进而整块被 no_active_span 拒绝。
            # 固定切块的块头没有语义，故只保留计时器级冻结判定（暂停时 HUD 通常
            # 可读）；普通候选保持原语义不变。
            [] if item.get("split_from_oversize") else freeze_samples,
            start=start,
            end=end,
            scan_end=scan_end,
            score_cutoff=cand_score_cutoff,
        )
        if cutoff is not None and reason == "broadcast_replay_or_non_game":
            # 主扫描降到 1fps 以保证实时性，但短 Replay 可能只占一个采样
            # 点。仅在候选终点附近补一小段 2fps 复核，不再为整段视频付出
            # 2fps + OCR 的成本。
            # 将局部窗口起点对齐到整秒，避免不同 seek 起点让短 Replay
            # 恰好落在采样间隙里，导致复核仍然晚一个采样周期。
            refine_start = max(
                start,
                math.floor(float(cutoff) - BROADCAST_BOUNDARY_REFINE_SEC),
            )
            refine_end = min(effective_scan_end, float(cutoff) + 4.0)
            if refine_end > refine_start:
                refine_frames = _extract(
                    refine_start,
                    refine_end,
                    2.0,
                )
                if refine_frames:
                    refine_probs = _predict_broadcast_batch(clf, [img for _, img in refine_frames])
                    refine_samples: list[tuple[float, str, float]] = []
                    for refine_ts, refine_row in zip(refine_frames, refine_probs, strict=True):
                        refine_label, refine_confidence = _stable_visual_label(
                            refine_row,
                            stable_prob=stable_prob,
                            class_stable_prob=class_stable_prob,
                        )
                        refine_candidate_index = max(
                            range(len(_CLASS_NAMES)),
                            key=lambda class_index: float(refine_row[class_index]),
                        )
                        _log_model_confidence(
                            clf,
                            stage="refine",
                            timestamp_sec=float(refine_ts[0]),
                            probabilities=refine_row,
                            stable_label=refine_label,
                            confidence=refine_confidence,
                            threshold=float(
                                class_stable_prob.get(
                                    _CLASS_NAMES[refine_candidate_index], stable_prob
                                )
                            ),
                        )
                        refine_samples.append(
                            (float(refine_ts[0]), refine_label, refine_confidence)
                        )
                    refine_samples = _stabilize_broadcast_samples(refine_samples)
                    refined_cutoff, refined_reason = audit_broadcast_phase_sequence(
                        refine_samples,
                        start=start,
                        end=scan_end,
                        scan_end=scan_end,
                    )
                    if (
                        refined_cutoff is not None
                        and refined_reason == "broadcast_replay_or_non_game"
                        and refined_cutoff < cutoff
                    ):
                        cutoff = refined_cutoff
                        reason = refined_reason
                        _log.debug(
                            "赛事回合终点局部复核提前截断: %.1f",
                            float(cutoff),
                        )
        if (
            cutoff is not None
            and reason == "broadcast_replay_or_non_game"
            and not item.get("broadcast_next_prep_invalidated")
            and float(item.get("end_coarse", end)) - float(cutoff) >= 3.0
        ):
            # 常规赛事候选的粗出点通常包含结算+回放，视觉
            # 审计会向前截到 ROUND WIN/THRIFTY 刚出现的首帧。
            # 保留 2.5s 结算尾巴，但“假 next_prep 被否决后向后
            # 延长”的候选不加尾巴，避免带入选手席/真 Replay。
            cutoff = min(
                float(scan_end),
                float(cutoff) + RESULT_PRESENTATION_TAIL_SEC,
            )
            item["broadcast_result_tail_sec"] = RESULT_PRESENTATION_TAIL_SEC
        if reason == "broadcast_no_active_span" or cutoff is not None and cutoff <= start:
            item["broadcast_audit"] = "rejected_no_stable_combat"
            _log.info("赛事回合审计拒绝无稳定交战: %.1f-%.1f", start, end)
            _record_audit_outcome(
                _outcome_sink,
                status="rejected",
                candidate=item,
                reason="no_stable_combat",
            )
            continue
        # 硬否决：若提议截断点之后仍有连续递减的交战计时器，说明回合并未真正
        # 结束（可能是转场/回放夹在回合中），禁止把该点定稿为 broadcast_exclusion。
        if (
            cutoff is not None
            and reason
            and _has_decreasing_combat_after(
                cutoff,
                timer_samples,
                # 回合化（2026-09-12）：所有候选都允许「满钟=新回合」逃逸。
                # 该规则本就为「回放后接下一回合满钟」设计（原仅用于固定切块），
                # 普通候选遇到的正是同一形态——跨回合的钟表递减会误否决正确截断：
                # 实测 round-000135 的真实出点 1423.2 被误否决（→next_prep/coarse），
                # 且窗口放宽后 045 的正确截断 514.0 也会被下一回合的钟表误否决
                # （→重搜接受 672.485，跨回合粘连 +158s）。
                fresh_clock_min=FRESH_ROUND_CLOCK_MIN,
            )
        ):
            _log.info(
                "赛事回合终点硬否决（截断后交战计时器仍递减）: %.1f-%.1f, cutoff=%.1f",
                start,
                end,
                float(cutoff),
            )
            cutoff = None
            reason = None
        if cutoff is not None and reason:
            new_end = min(float(scan_end), float(cutoff))
            if new_end - start < MIN_ACTIVE_SEC:
                item["broadcast_audit"] = "rejected_no_stable_combat"
                _log.info("赛事回合审计截断后过短: %.1f-%.1f", start, new_end)
                _record_audit_outcome(
                    _outcome_sink,
                    status="rejected",
                    candidate=item,
                    reason="exclusion_too_short",
                )
                continue
            item["end"] = round(new_end, 3)
            item["end_by"] = "broadcast_exclusion"
            item["broadcast_excluded_reason"] = reason
            item["broadcast_excluded_from"] = round(new_end, 3)
        if (
            cutoff is None
            and cache_item.get("next_prep_invalidated")
            and scan_end < min(
                start + MAX_BROADCAST_ROUND_SEC,
                end + END_LOOKAHEAD_SEC,
            ) - 0.5
        ):
            # 首次按“强 next_prep”只审计 45s；一旦视觉证明该出点是假的，
            # 下轮扩展到完整 90s/max-round 后视范围，直到找到真实边界。
            item["broadcast_audit"] = "pending_lookahead"
            item["broadcast_audit_step"] = "next_prep_veto_extend"
            item["_audit_continue_ready"] = True
            output.append(item)
            _record_audit_outcome(
                _outcome_sink,
                status="pending",
                candidate=item,
                reason="next_prep_invalidated",
                retry_after_duration=float(available_end or scan_end),
            )
            continue
        if cutoff is None and not (cache_item or {}).get("a5_cap_retried") and (
            item.get("broadcast_audit_window_cap") is not None
        ):
            # 压窗后没拿到截断证据 ⇒ 回退到完整窗口重审（只一次），避免缩窗把真出点挡在
            # 窗口外。旧实现只对 next_prep 否决做扩展，这里补上"缩窗失败"这一路。
            cache_item["a5_cap_retried"] = True
            item.pop("broadcast_audit_window_cap", None)
            item.pop("broadcast_audit_window_uncapped_end", None)
            item["broadcast_audit"] = "pending_lookahead"
            item["broadcast_audit_step"] = "a5_cap_retry"
            item["_audit_continue_ready"] = True
            output.append(item)
            _record_audit_outcome(
                _outcome_sink,
                status="pending",
                candidate=item,
                reason="a5_cap_retry",
                retry_after_duration=float(available_end or end),
            )
            _log.info(
                "赛事回合审计缩窗未获证据，回退完整窗口重审: %.1f-%.1f", start, end,
            )
            continue
        if cutoff is None and micro_step_incomplete:
            item["broadcast_audit"] = "pending_lookahead"
            item["broadcast_audit_step"] = "tail"
            item["broadcast_audit_step_end"] = round(effective_scan_end, 3)
            item["_audit_continue_ready"] = True
            output.append(item)
            _record_audit_outcome(
                _outcome_sink,
                status="pending",
                candidate=item,
                reason="audit_micro_step",
                retry_after_duration=float(available_end or effective_scan_end),
            )
            continue
        if cutoff is None and lookahead_incomplete:
            item.pop("_audit_continue_ready", None)
            item["broadcast_audit"] = "pending_lookahead"
            item["broadcast_audit_scan_end"] = round(scan_end, 3)
            item["broadcast_audit_available_end"] = round(float(available_end), 3)
            output.append(item)
            _record_audit_outcome(
                _outcome_sink,
                status="pending",
                candidate=item,
                reason="lookahead_incomplete",
                retry_after_duration=scan_end,
            )
            _log.info(
                "赛事回合审计等待后视窗口: %.1f-%.1f, available_end=%.1f, need=%.1f",
                start,
                end,
                float(available_end),
                scan_end,
            )
            continue
        if (
            cutoff is None
            and _weak_ocr_end(item)
            and not cache_item.get("fallback_full_scanned")
            and not cache_item.get("backward_sweep_requested")
            and extract_start > start + 0.5
        ):
            # 弱出点（next_combat / open_tail）+ 尾窗内没有任何排除证据：真出点很可能
            # 落在尾窗之前（赛后回放 + 买枪比 60s 回看更长）。先安排一次有界向前回扫
            # 取证，由下一个微步骤续跑；绝不在还没看过该区间时就盖章
            # pending_no_exclusion —— 那会把「晚 30-80s 的粗出点」冻结成终态，切片尾部
            # 一直带着回放/买枪，且此后无人再审。
            cache_item["backward_sweep_requested"] = True
            cache_item["backward_sweep_from"] = round(float(extract_start), 3)
            item["broadcast_audit"] = "pending_lookahead"
            item["broadcast_audit_step"] = "backward_sweep"
            item["broadcast_audit_step_from"] = round(float(extract_start), 3)
            item["_audit_continue_ready"] = True
            output.append(item)
            _record_audit_outcome(
                _outcome_sink,
                status="pending",
                candidate=item,
                reason="backward_sweep",
                retry_after_duration=float(available_end or scan_end),
            )
            _log.info(
                "赛事回合审计尾窗无排除证据，向前回扫取证: %.1f-%.1f, from=%.1f, end_by=%s",
                start,
                end,
                float(extract_start),
                item.get("end_by"),
            )
            continue
        item.pop("_audit_continue_ready", None)
        item.pop("broadcast_audit_step", None)
        item.pop("broadcast_audit_step_end", None)
        item["broadcast_audit_scan_end"] = round(scan_end, 3)
        if cache_item.get("backward_sweep_requested"):
            # 可观测性：本次定稿前做了向前回扫（回扫到的位置 + 当时尾窗左界），
            # 供现场按 candidate 复查「是不是粗出点太晚才需要回扫」。
            item["broadcast_backward_swept_to"] = cache_item.get("backward_sweep_scanned_to")
            item["broadcast_backward_sweep_from"] = cache_item.get("backward_sweep_from")
        # 证据驱动盖章：仅当真实发现截断或 OCR next_prep 复核通过时才标 passed/confirmed，
        # reason=none 保持 pending，严禁伪造 broadcast_exclusion。
        _stamp_broadcast_decision(item, clf, cutoff=cutoff, reason=reason)
        if cache_item.get("start_gate_inconclusive"):
            # 入点是「模型不确定」：出点审计照常定稿（end_by/end_quality 保留审计结论），
            # 但不允许自动导出 —— confirm_status 固定 pending，交人工确认。
            # （人工确认后即可导出，与「入点 coarse 只是起得略早」的既有产品规则一致；
            #   auto-export 路径要求 confirm_status == vision_confirmed，故自动导出被挡。）
            item["confirm_status"] = "pending"
            item["broadcast_start_gate"] = "inconclusive"
            item["start_quality"] = "coarse"
            item["start_review_required"] = True
            item["broadcast_review_required"] = True
        # L1：区间内边界自检。出点定稿不等于区间干净——OCR 起点可能落在上一回合内部，
        # 区间里含一个完整回合边界，出点却属于后一回合（round-000123 ↔ round-000135）。
        _interior_resume = _interior_round_boundary(
            samples, start=start, end=float(item.get("end") or end)
        )
        if _interior_resume is not None:
            _action, _trimmed, _kept = _interior_boundary_verdict(
                start=start, end=float(item["end"]), resume=_interior_resume
            )
            if _action == "reject":
                item["broadcast_audit"] = "rejected_interior_boundary"
                item["broadcast_audit_reason"] = "interior_round_boundary"
                item["interior_boundary_resume_sec"] = round(_interior_resume, 3)
                item["interior_boundary_trim_sec"] = round(_trimmed, 3)
                item["confirm_status"] = "pending"
                # 关键：**不得**把该候选标记为 cache completed。缓存命中分支
                # （audit_cache[key]["completed"]）会 `item.update(stamped_decision)` 或
                # 重新 `_stamp_broadcast_decision()` 后直接 append 到 output——拒绝路径
                # 没有 stamped_decision，下一轮就会被重新盖章成 accepted，把拒绝悄悄翻案。
                # 与既有拒绝路径（no_stable_combat_start / long_or_invalid）一致：留给下一轮
                # 重新审一遍，纯函数判定使其结论稳定。
                _log.info(
                    "赛事回合区间跨回合拒绝: %.1f-%.1f, 内部边界后重开战=%.1f, 前缀=%.1fs 剩余=%.1fs",
                    start,
                    float(item["end"]),
                    _interior_resume,
                    _trimmed,
                    _kept,
                )
                _record_audit_outcome(
                    _outcome_sink,
                    status="rejected",
                    candidate=item,
                    reason="interior_round_boundary",
                )
                continue
            _apply_interior_boundary_trim(
                item, resume=_interior_resume, trimmed=_trimmed
            )
            _log.info(
                "赛事回合起点前缀裁剪: %.1f-%.1f → 起点 %.1f（裁掉跨回合前缀 %.1fs）",
                start,
                float(item["end"]),
                _interior_resume,
                _trimmed,
            )
        cache_item["completed"] = True
        cache_item["final_end"] = float(item["end"])
        cache_item["scan_end"] = scan_end
        cache_item["stamped_decision"] = {
            "start_coarse": item.get("start_coarse"),
            "start_refined": item.get("start_refined"),
            "start_delta": item.get("start_delta"),
            "start_confidence": item.get("start_confidence"),
            "start_by": item.get("start_by"),
            "end_coarse": item.get("end_coarse"),
            "end_refined": item.get("end_refined"),
            "end_delta": item.get("end_delta"),
            "end_confidence": item.get("end_confidence"),
            "end_by": item.get("end_by"),
            "broadcast_excluded_reason": item.get("broadcast_excluded_reason"),
            "broadcast_audit_reason": item.get("broadcast_audit_reason"),
            "broadcast_audit": item.get("broadcast_audit"),
            "confirm_status": item.get("confirm_status"),
            "boundary_refined": item.get("boundary_refined"),
            "boundary_refined_by": item.get("boundary_refined_by"),
            "broadcast_review_required": item.get("broadcast_review_required"),
            "start_quality": item.get("start_quality"),
            "end_quality": item.get("end_quality"),
            "start_review_required": item.get("start_review_required"),
            "end_review_required": item.get("end_review_required"),
            "broadcast_result_tail_sec": item.get("broadcast_result_tail_sec"),
            "broadcast_model_version": item.get("broadcast_model_version"),
            "broadcast_model_provider": item.get("broadcast_model_provider"),
            "broadcast_backward_swept_to": item.get("broadcast_backward_swept_to"),
            "broadcast_backward_sweep_from": item.get("broadcast_backward_sweep_from"),
        }
        # 定稿回合不再保留全部采样列表；相同 round_key 后续只需复用 final_end。
        cache_item["samples"] = []
        cache_item["timer_samples"] = []
        cache_item["freeze_samples"] = []
        output.append(item)
        _record_audit_outcome(
            _outcome_sink,
            status=(
                "manual_review"
                if (
                    item.get("broadcast_audit") == "pending_no_exclusion"
                    or item.get("broadcast_start_gate") == "inconclusive"
                )
                else "accepted"
            ),
            candidate=item,
            reason=(
                "no_exclusion_evidence"
                if item.get("broadcast_audit") == "pending_no_exclusion"
                else "start_gate_inconclusive"
                if item.get("broadcast_start_gate") == "inconclusive"
                else str(
                    item.get("broadcast_audit_reason")
                    or item.get("broadcast_audit")
                    or "accepted"
                )
            ),
        )
        _log.info(
            "赛事回合审计完成: %.1f-%.1f -> %.1f-%.1f, audit=%s, status=%s, end_by=%s, reason=%s",
            start,
            end,
            float(item["start"]),
            float(item["end"]),
            item.get("broadcast_audit"),
            item.get("confirm_status"),
            item.get("end_by"),
            reason or "none",
        )
    # 超长分裂族：头碎片丢失的回合前半段在这里补回（放最后一步，此时本批结论
    # 都已定稿）。同批成对合并之外还有跨批台账——正常运行期 deferred_audit 下
    # 审计一次只推进一个子块，同批永远凑不齐头尾，台账才是主路径。
    _reconcile_split_family_fragments(output, audit_cache, _outcome_sink)
    # 已定稿真实回合的跨度进共享台账：收尾缺口补扫据此排除"已经有人管"的区间，
    # 不再把同一回合合成第二遍（两条审计路径共用同一个 audit_cache）。
    _remember_finalized_spans(audit_cache, output)
    return output


def audit_broadcast_rounds_with_outcomes(
    rounds: list[dict[str, Any]],
    video_path: str,
    *,
    ffmpeg_path: str = "ffmpeg",
    cancel_check: Callable[[], bool] | None = None,
    classifier: ValorantFrameClassifier | None = None,
    sample_fps: float = 1.0,
    available_end: float | None = None,
    audit_cache: dict[str, Any] | None = None,
    finalize: bool = False,
    lookahead_sec: float | None = None,
    frame_provider: Any | None = None,
    max_media_step_sec: float | None = None,
    outcome_sink: list[BroadcastAuditOutcome] | None = None,
) -> list[BroadcastAuditOutcome]:
    """Audit candidates and return an explicit disposition for each one.

    The legacy list-returning function remains available for post-hoc callers.
    Compatibility synthesis below also keeps existing test doubles and plugin
    wrappers that only implement that older contract working.

    ``outcome_sink`` 允许调用方持有 outcome 列表本身。审计被 ``cancel_check``
    中断（超预算/被抢占/停止）时会抛 ``FFmpegCancelled``，此时**已定稿**的
    outcome 仍留在该列表里；未开始判定的候选会补发 pending，保证批次完整
    （见 ``_fill_pending_for_undecided``）。调用方必须消费它——这是
    「审计结果永不丢失」契约在取消路径上的落点。
    """
    outcomes: list[BroadcastAuditOutcome] = (
        outcome_sink if outcome_sink is not None else []
    )
    # 与 audit_broadcast_rounds 内部同源的纯函数展开（幂等），用于在取消路径上
    # 识别"哪些子候选尚未判定"。
    expanded_rounds = _expand_oversize_candidates(
        [dict(item) for item in rounds if isinstance(item, dict)]
    )
    # 在线微步骤（max_media_step_sec 非空）：一次调用只推进一个候选/子块。
    # 分裂出的 4 块若一轮跑完，实测 ≈40s，远超 20s 墙钟预算 ⇒ 每次都在中途被
    # 腰斩；其余子块以 pending 交回调用方队列，下一轮从 audit_cache 续扫。
    audit_targets = expanded_rounds
    deferred_rounds: list[dict[str, Any]] = []
    if max_media_step_sec is not None and len(expanded_rounds) > 1:
        audit_targets = expanded_rounds[:1]
        deferred_rounds = expanded_rounds[1:]
    from lsc.utils.cancellable_ffmpeg import FFmpegCancelled

    try:
        returned = audit_broadcast_rounds(
            audit_targets,
            video_path,
            ffmpeg_path=ffmpeg_path,
            cancel_check=cancel_check,
            classifier=classifier,
            sample_fps=sample_fps,
            available_end=available_end,
            audit_cache=audit_cache,
            finalize=finalize,
            lookahead_sec=lookahead_sec,
            _outcome_sink=outcomes,
            frame_provider=frame_provider,
            max_media_step_sec=max_media_step_sec,
        )
    except FFmpegCancelled:
        _fill_pending_for_undecided(
            expanded_rounds, outcomes, available_end=available_end
        )
        raise
    if deferred_rounds:
        # 在线微步骤只推进第一个子块；其余以 pending 交回调用方队列续扫
        # （audit_cache 保证下一轮不重复抽帧）。
        _fill_pending_for_undecided(
            deferred_rounds,
            outcomes,
            available_end=available_end,
            reason="deferred_oversize_sibling",
        )
    if outcomes or not rounds:
        return outcomes

    remaining = [dict(item) for item in returned if isinstance(item, dict)]
    for original in rounds:
        try:
            start = round(float(original.get("start")), 3)
        except (TypeError, ValueError):
            start = None
        match_index = next(
            (
                index
                for index, item in enumerate(remaining)
                if start is not None
                and isinstance(item.get("start"), (int, float))
                and round(float(item["start"]), 3) == start
            ),
            None,
        )
        if match_index is None:
            candidate = dict(original)
            candidate["broadcast_audit"] = "rejected_legacy_filtered"
            outcomes.append(
                BroadcastAuditOutcome("rejected", candidate, "legacy_filtered")
            )
            continue
        candidate = remaining.pop(match_index)
        is_pending = candidate.get("broadcast_audit") == "pending_lookahead"
        is_manual_review = candidate.get("broadcast_audit") == "pending_no_exclusion"
        outcomes.append(
            BroadcastAuditOutcome(
                "pending" if is_pending else ("manual_review" if is_manual_review else "accepted"),
                candidate,
                (
                    "lookahead_incomplete"
                    if is_pending
                    else ("no_exclusion_evidence" if is_manual_review else "legacy_accepted")
                ),
            )
        )
    return outcomes



# ── 收尾缺口补扫（2026-09-12）─────────────────────────────────────────────
# 在线增量扫描会漏掉"画面静止"区间里的回合：实测 2026-09-12 15:16 会话，录像里
# 1530-1632 与 1740-1802 两段真实交战（抽帧顶中计时器 1:09 / 1:18 清晰可读）
# 在 live 扫描里一个候选都没产出，而同一段用 finalize 重扫能检出。
# 收尾时录像已定格、时间预算充足，故对「无候选」区间做一次低频视觉巡检，
# 把检出的交战段合成候选，交给**同一套**审计与门禁（判据不变）。
GAP_SWEEP_BOUNDARY_SOURCE = "valorant_vision_sweep_v1"
GAP_SWEEP_MIN_GAP_SEC = 60.0        # 只巡「连续 ≥60s 无候选」的区间
GAP_SWEEP_SAMPLE_FPS = 0.5          # 0.5fps：巡检成本约为正扫的 1/2
GAP_SWEEP_MIN_COMBAT_SEC = 20.0     # 交战段过短不当回合
GAP_SWEEP_MAX_LABEL_GAP_SEC = 6.0   # 交战段内的短暂空档（观战/击杀镜头切换）
GAP_SWEEP_SPLIT_TERMINAL_FRAMES = EXCLUSION_STABLE_FRAMES  # 段内终态游程 = 回合边界
# 入点前留的余量必须 ≤ START_GATE_ONSET_TOLERANCE_SEC：起点门禁只允许"稳定 combat
# 游程起点"距候选起点 ≤ 该容差，否则整条被判 rejected_no_stable_combat_start。
# 2026-09-12 19:14 现场实测：补扫把 17 个回合全检出来了，但 5s 的 pad 让 15 条
# 全部被门禁拒绝（`入点门禁拒绝: 1619.0-1705.0` …）——pad 必须小于容差。
GAP_SWEEP_START_PAD_SEC = 1.0
GAP_SWEEP_END_PAD_SEC = 15.0        # 出点后留结算/回放，供审计定位真出点


def _merged_span_gaps(
    spans: Iterable[tuple[float, float]], *, duration: float, min_gap_sec: float,
) -> list[tuple[float, float]]:
    """``[0, duration]`` 减去已覆盖区间后的缺口（按起点排序，只留 ≥min_gap_sec 的）。"""
    merged: list[list[float]] = []
    for start, end in sorted((float(a), float(b)) for a, b in spans if float(b) > float(a)):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    gaps: list[tuple[float, float]] = []
    cursor = 0.0
    for start, end in merged:
        if start - cursor >= min_gap_sec:
            gaps.append((cursor, start))
        cursor = max(cursor, end)
    if duration - cursor >= min_gap_sec:
        gaps.append((cursor, duration))
    return gaps


def _combat_run_candidates(
    samples: list[tuple[float, str, float]],
    *,
    duration: float,
    min_combat_sec: float,
    max_label_gap_sec: float,
    split_terminal_frames: int,
) -> list[tuple[float, float]]:
    """把巡检标签序列切成"回合级别的交战段"。

    段内允许 ≤max_label_gap_sec 的空档（观战/击杀切换）；段内出现 ≥N 帧终态游程
    （result/non_game/replay）后**又见 combat** 视为跨回合 → 在该处切段。
    """
    runs: list[tuple[float, float]] = []
    combat_start: float | None = None
    last_combat: float | None = None
    terminal_run = 0
    for ts, label, _conf in samples:
        point = float(ts)
        text = str(label)
        if text == "combat":
            if combat_start is None:
                combat_start = point
            elif last_combat is not None and terminal_run >= split_terminal_frames:
                # 终态游程后重开战 = 新回合：先收上一段
                if last_combat - combat_start >= min_combat_sec:
                    runs.append((combat_start, last_combat))
                combat_start = point
            elif last_combat is not None and point - last_combat > max_label_gap_sec:
                # 长时间没有 combat（>max_label_gap）：原段就此结束
                if last_combat - combat_start >= min_combat_sec:
                    runs.append((combat_start, last_combat))
                combat_start = point
            last_combat = point
            terminal_run = 0
            continue
        if text in _TERMINAL_LABELS:
            terminal_run += 1
    if combat_start is not None and last_combat is not None and last_combat - combat_start >= min_combat_sec:
        runs.append((combat_start, last_combat))
    return [(s, min(float(duration), e)) for s, e in runs]


def sweep_gap_rounds(
    video_path: str,
    candidates: Iterable[dict[str, Any]],
    *,
    duration: float,
    classifier: Any,
    ffmpeg_path: str = "ffmpeg",
    cancel_check: Callable[[], bool] | None = None,
    sample_fps: float = GAP_SWEEP_SAMPLE_FPS,
    min_gap_sec: float = GAP_SWEEP_MIN_GAP_SEC,
    min_combat_sec: float = GAP_SWEEP_MIN_COMBAT_SEC,
    frame_chunk_sec: float = 120.0,
) -> list[dict[str, Any]]:
    """在「无候选」区间做低频视觉巡检，把漏掉的交战段合成为候选。

    只**补出候选**，不判定出点：合成候选（``end_by=next_combat`` + 出点后留
    ``GAP_SWEEP_END_PAD_SEC``）与 OCR 候选走同一套 ``audit_broadcast_rounds``
    与同一套入列/草稿门禁，因此本函数不放宽任何判据。
    """
    if not candidates or duration <= 0:
        return []
    from lsc.analyzer.valorant_ocr_rounds import _round_key, extract_frames_cancellable

    spans = []
    for item in candidates:
        if not isinstance(item, dict):
            continue
        try:
            spans.append((float(item.get("start") or 0.0), float(item.get("end") or 0.0)))
        except (TypeError, ValueError):
            continue
    gaps = _merged_span_gaps(spans, duration=float(duration), min_gap_sec=min_gap_sec)
    if not gaps:
        return []

    stable_prob = float(classifier.thresholds.get("stable_prob", 0.55))
    class_stable_prob = getattr(classifier, "class_stable_prob", {})
    out: list[dict[str, Any]] = []
    for gap_start, gap_end in gaps:
        samples: list[tuple[float, str, float]] = []
        cursor = gap_start
        while cursor < gap_end:
            if cancel_check and cancel_check():
                return out
            chunk_end = min(gap_end, cursor + max(1.0, float(frame_chunk_sec)))
            try:
                frames = extract_frames_cancellable(
                    video_path, start_sec=cursor, end_sec=chunk_end,
                    fps=max(0.1, float(sample_fps)), ffmpeg_path=ffmpeg_path,
                    cancel_check=cancel_check, overlap_sec=0.0,
                )
            except Exception as exc:  # noqa: BLE001 - 巡检失败不得影响收尾主流程
                _log.warning("缺口补扫抽帧失败 %.1f-%.1f: %s", cursor, chunk_end, exc)
                frames = []
            if frames:
                probs = _predict_broadcast_batch(classifier, [img for _ts, img in frames])
                for (ts, _img), row in zip(frames, probs, strict=True):
                    label, conf = _stable_visual_label(
                        row, stable_prob=stable_prob, class_stable_prob=class_stable_prob,
                    )
                    samples.append((float(ts), label, float(conf)))
            cursor = chunk_end
        if not samples:
            continue
        samples = _stabilize_broadcast_samples(sorted(samples, key=lambda item: item[0]))
        for run_start, run_end in _combat_run_candidates(
            samples,
            duration=float(duration),
            min_combat_sec=float(min_combat_sec),
            max_label_gap_sec=GAP_SWEEP_MAX_LABEL_GAP_SEC,
            split_terminal_frames=GAP_SWEEP_SPLIT_TERMINAL_FRAMES,
        ):
            # 起点就是 combat 游程首帧（只留 1s 余量，见 GAP_SWEEP_START_PAD_SEC 注释）
            start = max(0.0, run_start - GAP_SWEEP_START_PAD_SEC)
            end = min(float(duration), run_end + GAP_SWEEP_END_PAD_SEC)
            if end <= start:
                continue
            out.append({
                # round_key 必须自带（10s 桶，与 OCR 候选同一约定）：审计缓存键、
                # 超长分裂后缀、去重与前端身份都依赖它，缺了会退化成 start 派生/None。
                "round_key": _round_key(start),
                "start": round(start, 3),
                "end": round(end, 3),
                "start_by": "vision_gap_sweep",
                "end_by": "next_combat",
                "boundary_source": GAP_SWEEP_BOUNDARY_SOURCE,
                "confirm_status": "pending",
                "phase": "combat",
                "reason": "缺口补扫回合交战阶段",
                "score": 0.7,
                "gap_sweep": {"gap": [round(gap_start, 3), round(gap_end, 3)],
                              "combat": [round(run_start, 3), round(run_end, 3)]},
            })
    if out:
        _log.warning(
            "收尾缺口补扫: 巡检 %d 个无候选区间，合成 %d 条候选: %s",
            len(gaps), len(out),
            ", ".join(f"{c['start']:.1f}-{c['end']:.1f}" for c in out),
        )
    return out


__all__ = [
    "BroadcastAuditOutcome",
    "BroadcastAuditStatus",
    "MAX_BROADCAST_ROUND_SEC",
    "audit_broadcast_phase_sequence",
    "audit_broadcast_rounds",
    "audit_broadcast_rounds_with_outcomes",
    "sweep_gap_rounds",
    "GAP_SWEEP_BOUNDARY_SOURCE",
]
