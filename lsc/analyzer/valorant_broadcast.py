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
BROADCAST_AUDIT_TAIL_LOOKBACK_SEC = 30.0
PAUSE_MIN_SEC = 2.5
# 入点门禁：定稿/离线审计时，候选起点必须由连续 visual combat 确认，
# 不允许把 replay/non_game/result 或超长固定切块的块头直接当作回合起点。
# 普通候选只看开头一小段即可；split_from_oversize 子块没有真实起点，必须
# 扫描到 MAX_BROADCAST_ROUND_SEC 以寻找块内真实 combat 锚点。
START_GATE_SCAN_LIMIT_SEC = 15.0
START_GATE_COMBAT_MIN_SEC = 2.0
START_GATE_MAX_GAP_SEC = 2.0
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
        return

    # 情况 2：OCR 具备明确的 next_prep 出点且复核通过
    orig_end_by = str(item.get("end_by", "") or "").strip().lower()
    if orig_end_by == "next_prep":
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


def _start_gate_decision(
    samples: list[tuple[float, str, float]],
    *,
    start: float,
    split_from_oversize: bool,
) -> tuple[float | None, str | None]:
    """Decide whether a broadcast candidate starts on real combat.

    Returns ``(new_start, reason)``:
    - ``(start, None)``: existing start is already a stable combat onset.
    - ``(new_start, "moved_from_non_combat")``: move to the first stable combat.
    - ``(None, "no_stable_combat")``: no reliable combat onset in the scanned
      prefix; caller should reject (or merge/delete at a higher level).
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
    if new_start <= float(start) + 1e-6:
        return float(start), None
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

        clf = ValorantFrameClassifier()
    else:
        clf = classifier
    clf.load()
    stable_prob = float(clf.thresholds.get("stable_prob", 0.55))
    class_stable_prob = getattr(clf, "class_stable_prob", {})
    output: list[dict[str, Any]] = []
    for original in rounds:
        if cancel_check and cancel_check():
            raise FFmpegCancelled("cancelled during broadcast audit")
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
        cache_key = f"{round(start, 1):.1f}"
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
        has_strong_ocr_end = (
            item.get("result_ts") is not None
            or str(item.get("end_by", "")).lower() in ("buy_phase", "next_prep")
        )
        effective_lookahead = (
            45.0
            if (finalize or available_end is None or has_strong_ocr_end)
            else float(lookahead_sec if lookahead_sec is not None else END_LOOKAHEAD_SEC)
        )
        scan_end = min(
            start + MAX_BROADCAST_ROUND_SEC,
            max(end, end + effective_lookahead),
        )
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
        # 在线/收尾/离线统一执行：候选的起点是已录制的过去，头部 15s 必然可用，
        # 因此候选一旦形成就立即判定入点，强停时回放开头候选也会被当场拦截，
        # 不再等收尾才执行。split_from_oversize 固定块头照旧整块重找锚点。
        run_start_gate = (
            not cache_item.get("start_gate_done")
            and (bool(item.get("split_from_oversize")) or not item.get("start_delta"))
        )
        gate_scan_end: float | None = None
        gate_window_covered = True
        if run_start_gate:
            gate_scan_end = min(
                float(end),
                float(start) + (
                    MAX_BROADCAST_ROUND_SEC
                    if item.get("split_from_oversize")
                    else START_GATE_SCAN_LIMIT_SEC
                ),
            )
            # 在线极端情况：头部窗口还没被当前录制覆盖时，不能用截断的头部下
            # 结论，跳过判定交给 pending_lookahead 重试，防止把尚未写入的
            # 后续 combat 误判为“找不到真实入点”而提前拒绝。
            gate_window_covered = (
                available_end is None
                or float(available_end) + 0.5 >= gate_scan_end
            )
        if run_start_gate and gate_window_covered:
            # 起点门禁的头部样本单独存放到 start_gate_samples，不混入尾部审计
            # 的 samples/scanned_end。否则在线首轮会因缓存“已扫过头部”而从
            # 头部开始连续扫完整回合，暴露回合中段的 replay/result 转场，导致
            # 出点被过早截断、回合不完整。
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
                gate_frames = extract_frames_cancellable(
                    video_path,
                    start_sec=start,
                    end_sec=min(gate_scan_end, existing_gate_min),
                    fps=max(0.5, float(sample_fps)),
                    ffmpeg_path=ffmpeg_path,
                    cancel_check=cancel_check,
                    overlap_sec=0.0,
                )
            elif gate_scan_end > existing_gate_cover + 0.5:
                gate_frames = extract_frames_cancellable(
                    video_path,
                    start_sec=max(start, existing_gate_cover),
                    end_sec=gate_scan_end,
                    fps=max(0.5, float(sample_fps)),
                    ffmpeg_path=ffmpeg_path,
                    cancel_check=cancel_check,
                    overlap_sec=0.0,
                )
            else:
                gate_frames = []
            if gate_frames or existing_gate_samples:
                merged_gate_samples = {
                    round(float(ts), 3): (float(ts), str(label), float(conf))
                    for ts, label, conf in existing_gate_samples
                }
                if gate_frames:
                    gate_probs = _predict_broadcast_batch(clf, [img for _, img in gate_frames])
                    for _gate_index, ((gate_ts, _), gate_row) in enumerate(
                        zip(gate_frames, gate_probs, strict=True)
                    ):
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
                cache_item["start_gate_done"] = True
                original_start = float(start)
                new_start, gate_reason = _start_gate_decision(
                    decision_samples,
                    start=start,
                    split_from_oversize=bool(item.get("split_from_oversize")),
                )
                if gate_reason == "no_stable_combat":
                    item["broadcast_start_gate"] = gate_reason
                    item["broadcast_start_gate_scan_end"] = round(gate_scan_end, 3)
                    item["broadcast_audit"] = "rejected_no_stable_combat_start"
                    item["broadcast_review_required"] = True
                    cache_item["start_gate_rejected"] = True
                    cache_item["start_gate_reason"] = gate_reason
                    cache_item["start_gate_scan_end"] = round(gate_scan_end, 3)
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
                    continue
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
            result_ts = item.get("result_ts")
            if isinstance(result_ts, (int, float)):
                audit_start = min(audit_start, max(start, float(result_ts) - 10.0))
        extract_start = max(start, audit_start)
        if cached_samples and cached_scanned_end >= effective_scan_end - 0.5:
            extract_start = effective_scan_end
        frames = extract_frames_cancellable(
            video_path,
            start_sec=extract_start,
            end_sec=effective_scan_end,
            fps=max(0.5, float(sample_fps)),
            ffmpeg_path=ffmpeg_path,
            cancel_check=cancel_check,
            overlap_sec=0.0,
        )
        if not frames and not cached_samples:
            if lookahead_incomplete:
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
        for index, (ts, image) in enumerate(frames):
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
                (label == "combat" and index % timer_stride == 0)
                or (
                    label in _TIMER_OCR_LABELS
                    and (
                        label != prev_sample_label
                        or (float(ts) - last_timer_ocr_ts >= 2.0)
                    )
                )
            ):
                should_run_timer = True

            if should_run_timer:
                timer = None
                try:
                    timer, _, _ = _read_top_anchors(image)
                except Exception as exc:  # noqa: BLE001 - OCR 是辅助证据
                    _log.debug("赛事暂停计时器审计 OCR 失败: %s", exc)
                timer_samples.append((float(ts), timer, label))
                last_timer_ocr_ts = float(ts)
            prev_sample_label = label
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
        if (
            not any(label == "combat" for _, label, _ in samples)
            and extract_start > start + 0.5
            and not cache_item.get("fallback_full_scanned")
        ):
            cache_item["fallback_full_scanned"] = True
            # 只补抽尚未扫描的头部 [start, extract_start]：尾窗 [extract_start,
            # effective_scan_end] 的样本已在 samples 中，重抽整段会重复抽帧+重复
            # 推理（收尾超时主因之一）。按 ts 合并后覆盖区间与重抽整段完全一致。
            full_frames = extract_frames_cancellable(
                video_path,
                start_sec=start,
                end_sec=extract_start,
                fps=max(0.5, float(sample_fps)),
                ffmpeg_path=ffmpeg_path,
                cancel_check=cancel_check,
                overlap_sec=0.0,
            )
            if full_frames:
                full_probs = _predict_broadcast_batch(clf, [img for _, img in full_frames])
                full_samples: list[tuple[float, str, float]] = []
                for _, ((full_ts, _), full_row) in enumerate(zip(full_frames, full_probs, strict=True)):
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
        samples = _stabilize_broadcast_samples(samples)
        item_score_cutoff = item.get("score_cutoff") or item.get("score_end_ts")
        cand_score_cutoff = float(item_score_cutoff) if item_score_cutoff is not None else None
        cutoff, reason = audit_broadcast_phase_sequence(
            samples,
            timer_samples,
            freeze_samples,
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
                refine_frames = extract_frames_cancellable(
                    video_path,
                    start_sec=refine_start,
                    end_sec=refine_end,
                    fps=2.0,
                    ffmpeg_path=ffmpeg_path,
                    cancel_check=cancel_check,
                    overlap_sec=0.0,
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
        if cutoff is None and lookahead_incomplete:
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
        item["broadcast_audit_scan_end"] = round(scan_end, 3)
        # 证据驱动盖章：仅当真实发现截断或 OCR next_prep 复核通过时才标 passed/confirmed，
        # reason=none 保持 pending，严禁伪造 broadcast_exclusion。
        _stamp_broadcast_decision(item, clf, cutoff=cutoff, reason=reason)
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
            "broadcast_model_version": item.get("broadcast_model_version"),
            "broadcast_model_provider": item.get("broadcast_model_provider"),
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
                if item.get("broadcast_audit") == "pending_no_exclusion"
                else "accepted"
            ),
            candidate=item,
            reason=(
                "no_exclusion_evidence"
                if item.get("broadcast_audit") == "pending_no_exclusion"
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
) -> list[BroadcastAuditOutcome]:
    """Audit candidates and return an explicit disposition for each one.

    The legacy list-returning function remains available for post-hoc callers.
    Compatibility synthesis below also keeps existing test doubles and plugin
    wrappers that only implement that older contract working.
    """
    outcomes: list[BroadcastAuditOutcome] = []
    returned = audit_broadcast_rounds(
        rounds,
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


__all__ = [
    "BroadcastAuditOutcome",
    "BroadcastAuditStatus",
    "MAX_BROADCAST_ROUND_SEC",
    "audit_broadcast_phase_sequence",
    "audit_broadcast_rounds",
    "audit_broadcast_rounds_with_outcomes",
]
