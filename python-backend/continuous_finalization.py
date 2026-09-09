"""持续分析收尾的可持久化状态和纯函数规则。

本模块不启动线程、不访问 WebSocket，也不触碰录制文件。它承载收尾流程
需要共享的覆盖账本、fresh rescan 判定和边界质量分级，便于后端与测试复用。
"""
from __future__ import annotations

import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

FINALIZATION_SCHEMA_VERSION = 2
DEFAULT_FULL_RESCAN_LAG_SEC = 30.0
DEFAULT_COVERAGE_EPSILON_SEC = 2.0
DEFAULT_PRECISE_BOUNDARY_DELTA_SEC = 1.0
# Broadcast 的粗 OCR 到视觉精修可能跨越多个 1fps 采样点；只要双边界
# 物理证据完整且审计通过，3s 内的修正仍属于可导出的精确边界。POV 继续
# 使用更严格的 1s 兼容门槛。
BROADCAST_PRECISE_BOUNDARY_DELTA_SEC = 3.0


def merge_ranges(
    ranges: Iterable[tuple[float, float]],
    *,
    adjacency_epsilon: float = 0.001,
) -> list[tuple[float, float]]:
    """合并重叠或相邻的合法时间区间，并按起点排序。"""
    normalized: list[tuple[float, float]] = []
    for raw_start, raw_end in ranges:
        try:
            start = float(raw_start)
            end = float(raw_end)
        except (TypeError, ValueError):
            continue
        if start < 0.0 or end <= start:
            continue
        normalized.append((start, end))

    normalized.sort(key=lambda item: (item[0], item[1]))
    merged: list[tuple[float, float]] = []
    for start, end in normalized:
        if not merged or start > merged[-1][1] + max(0.0, adjacency_epsilon):
            merged.append((start, end))
            continue
        merged[-1] = (merged[-1][0], max(merged[-1][1], end))
    return merged


def uncovered_ranges(
    ranges: Iterable[tuple[float, float]],
    start: float,
    end: float,
    *,
    epsilon: float = 0.001,
) -> list[tuple[float, float]]:
    """返回目标区间内没有被 coverage 覆盖的子区间。"""
    target_start = max(0.0, float(start))
    target_end = max(target_start, float(end))
    if target_end <= target_start:
        return []

    clipped: list[tuple[float, float]] = []
    for item_start, item_end in merge_ranges(ranges):
        clipped_start = max(target_start, item_start)
        clipped_end = min(target_end, item_end)
        if clipped_end > clipped_start:
            clipped.append((clipped_start, clipped_end))

    gaps: list[tuple[float, float]] = []
    cursor = target_start
    for covered_start, covered_end in clipped:
        if covered_start > cursor + max(0.0, epsilon):
            gaps.append((cursor, covered_start))
        cursor = max(cursor, covered_end)
    if target_end > cursor + max(0.0, epsilon):
        gaps.append((cursor, target_end))
    return gaps


def finalization_requires_full_rescan(
    *,
    final_duration: float,
    last_analyzed: float,
    coverage_ranges: Iterable[tuple[float, float]],
    scan_error: bool = False,
    force: bool = False,
    lag_threshold_sec: float = DEFAULT_FULL_RESCAN_LAG_SEC,
    coverage_epsilon_sec: float = DEFAULT_COVERAGE_EPSILON_SEC,
) -> bool:
    """判断停录后是否必须从零开始 fresh full rescan。

    ``coverage_ranges`` 是成功扫描的权威账本。只要账本连续覆盖到
    ``last_analyzed``，停录后只需从该游标附近补扫尾部，即使录制沿与分析沿
    相差超过 ``lag_threshold_sec`` 也不应重复扫描整段历史。延迟本身只代表
    尚未覆盖的尾部长度；是否存在历史缺口由 coverage 账本判断。
    """
    del lag_threshold_sec
    if force or scan_error:
        return True
    duration = max(0.0, float(final_duration))
    analyzed = max(0.0, float(last_analyzed))
    cursor = min(duration, analyzed)
    # A gap before the analyzed cursor means a previous scan may have advanced
    # state without proving the whole history, so a fresh pass is required.
    return bool(
        uncovered_ranges(
            coverage_ranges,
            0.0,
            cursor,
            epsilon=max(0.0, float(coverage_epsilon_sec)),
        )
    )


def classify_boundary_quality(
    *,
    confirm_status: str | None,
    end_by: str | None,
    boundary_refined: bool,
    start_confidence: float | None = None,
    end_confidence: float | None = None,
    start_delta: float | None = None,
    end_delta: float | None = None,
    precise_delta_sec: float = DEFAULT_PRECISE_BOUNDARY_DELTA_SEC,
    source_profile: str | None = None,
    broadcast_audit: str | None = None,
    broadcast_audit_reason: str | None = None,
) -> str:
    """将语义确认和物理边界精度分开分级。

    返回值为 ``precise``、``coarse``、``pending`` 或 ``invalid``。
    ``pending`` 优先于精度判定，因为缺少完整出点证据时不能自动导出。

    B-04 规则：
    1. POV / 历史数据保留 legacy 兼容规则；
    2. source_profile="broadcast" 采用严格门禁：
       - 必须已通过视觉审计 (broadcast_audit == "passed")；
       - next_combat / open_tail / reason_none 严禁进入 precise；
       - 必须具备完整的双向物理证据 (start/end confidence & delta 缺一不可)；
       - 缺少任一证据时最高评为 coarse/pending，严禁自动导出；
       - 回放/暂停截断（broadcast_exclusion + audit passed）的 end_delta 为
         结构性截断距离，豁免采样容差判定（终点由审计证据背书）。
    """
    status = str(confirm_status or "").strip().lower()
    boundary_end = str(end_by or "").strip().lower()
    is_broadcast = str(source_profile or "").strip().lower() == "broadcast"
    bcast_audit = str(broadcast_audit or "").strip().lower()
    bcast_reason = str(broadcast_audit_reason or "").strip().lower()

    if status != "vision_confirmed" or boundary_end in {"next_combat", "open_tail"}:
        return "pending"

    # broadcast 专有严格门禁
    if is_broadcast:
        if bcast_audit and bcast_audit not in {"passed"}:
            return "pending"
        if bcast_reason in {"none", "reason_none"}:
            return "pending" if boundary_end != "next_prep" else "coarse"
        if (
            start_confidence is None
            or end_confidence is None
            or start_delta is None
            or end_delta is None
        ):
            return "coarse"

    deltas = (start_delta, end_delta)
    if any(delta is not None and float(delta) < 0.0 for delta in deltas):
        return "invalid"
    delta_limit = (
        BROADCAST_PRECISE_BOUNDARY_DELTA_SEC
        if is_broadcast
        else precise_delta_sec
    )
    # 回放/暂停截断（broadcast_exclusion 且审计通过）是结构性修正而非采样
    # 漂移：粗 OCR 出点在回放/买枪之后，视觉审计把终点截回真实交战结束，
    # end_delta 可达数十秒（实测 15–65s）。终点证据由审计本身
    # （end_confidence + 截断帧 + 最短交战时长守卫）背书，不再套用 ±3s 采样
    # 容差，否则所有被回放过滤的正常回合都会被误判 invalid 并永久 pending。
    # 起点精修仍是细粒度密扫，继续受容差约束。
    _end_exempt = (
        is_broadcast
        and boundary_end == "broadcast_exclusion"
        and bcast_audit == "passed"
    )
    if (
        start_delta is not None
        and float(start_delta) > max(0.0, float(delta_limit))
    ):
        return "invalid"
    if (
        not _end_exempt
        and end_delta is not None
        and float(end_delta) > max(0.0, float(delta_limit))
    ):
        return "invalid"

    if not boundary_refined:
        return "coarse"

    confidence_values = (start_confidence, end_confidence)
    if any(
        value is not None and not 0.0 <= float(value) <= 1.0
        for value in confidence_values
    ):
        return "invalid"
    if any(value is not None and float(value) < 0.8 for value in confidence_values):
        return "coarse"
    # Legacy refined results do not carry confidence/delta fields. The
    # refinement pass itself is the available evidence until the richer audit
    # payload is populated; supplied evidence still tightens the decision.
    if any(delta is not None for delta in deltas) and any(delta is None for delta in deltas):
        return "coarse"
    return "precise"


def boundary_quality_reason_code(
    *,
    confirm_status: str | None,
    end_by: str | None,
    boundary_refined: bool,
    start_confidence: float | None = None,
    end_confidence: float | None = None,
    start_delta: float | None = None,
    end_delta: float | None = None,
    precise_delta_sec: float = DEFAULT_PRECISE_BOUNDARY_DELTA_SEC,
    source_profile: str | None = None,
    broadcast_audit: str | None = None,
    broadcast_audit_reason: str | None = None,
) -> str:
    """Return a stable machine-readable explanation for the quality grade."""
    status = str(confirm_status or "").strip().lower()
    boundary_end = str(end_by or "").strip().lower()
    is_broadcast = str(source_profile or "").strip().lower() == "broadcast"
    audit = str(broadcast_audit or "").strip().lower()
    reason = str(broadcast_audit_reason or "").strip().lower()
    if is_broadcast and (
        audit == "pending_no_exclusion" or reason in {"none", "reason_none"}
    ):
        return "broadcast_no_exclusion_evidence"
    if status != "vision_confirmed":
        return "confirm_status_not_vision_confirmed"
    if boundary_end in {"next_combat", "open_tail"}:
        return f"end_boundary_{boundary_end}"
    if is_broadcast:
        if audit and audit != "passed":
            return f"broadcast_audit_{audit}"
        if reason in {"none", "reason_none"}:
            return "broadcast_no_exclusion_evidence"
        if any(value is None for value in (
            start_confidence,
            end_confidence,
            start_delta,
            end_delta,
        )):
            return "missing_bidirectional_boundary_evidence"
    if any(delta is not None and float(delta) < 0.0 for delta in (start_delta, end_delta)):
        return "negative_boundary_delta"
    limit = BROADCAST_PRECISE_BOUNDARY_DELTA_SEC if is_broadcast else precise_delta_sec
    if start_delta is not None and float(start_delta) > max(0.0, float(limit)):
        return "start_delta_exceeds_tolerance"
    if not (
        is_broadcast
        and boundary_end == "broadcast_exclusion"
        and audit == "passed"
    ) and end_delta is not None and float(end_delta) > max(0.0, float(limit)):
        return "end_delta_exceeds_tolerance"
    if not boundary_refined:
        return "boundary_refinement_incomplete"
    if any(
        value is not None and not 0.0 <= float(value) <= 1.0
        for value in (start_confidence, end_confidence)
    ):
        return "boundary_confidence_out_of_range"
    if any(value is not None and float(value) < 0.8 for value in (start_confidence, end_confidence)):
        return "boundary_confidence_below_threshold"
    if any(delta is not None for delta in (start_delta, end_delta)) and any(
        delta is None for delta in (start_delta, end_delta)
    ):
        return "partial_boundary_delta_evidence"
    return "ok"


def _set_boundary_quality(round_dict: dict[str, Any]) -> str:
    """Convenience helper to classify and apply boundary_quality to a round dictionary."""
    quality = classify_boundary_quality(
        confirm_status=round_dict.get("confirm_status"),
        end_by=round_dict.get("end_by"),
        boundary_refined=bool(round_dict.get("boundary_refined")),
        start_confidence=round_dict.get("start_confidence"),
        end_confidence=round_dict.get("end_confidence"),
        start_delta=round_dict.get("start_delta"),
        end_delta=round_dict.get("end_delta"),
        source_profile=round_dict.get("source_profile"),
        broadcast_audit=round_dict.get("broadcast_audit"),
        broadcast_audit_reason=round_dict.get("broadcast_audit_reason"),
    )
    round_dict["boundary_quality"] = quality
    round_dict["boundary_review_required"] = (quality != "precise")
    return quality



@dataclass(slots=True)
class FinalizationJob:
    """可序列化的收尾任务状态。"""

    job_id: str
    room_id: str
    recording_id: str
    source_path: str
    final_duration: float
    valorant_profile: str = "pov"
    phase: str = "pending"
    scan_cursor: float = 0.0
    coverage_ranges: list[tuple[float, float]] = field(default_factory=list)
    scan_attempts: dict[str, int] = field(default_factory=dict)
    failed_ranges: list[dict[str, Any]] = field(default_factory=list)
    candidate_count: int = 0
    final_round_count: int = 0
    last_error: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    pending_candidates: list[dict[str, Any]] = field(default_factory=list)
    # Accepted/manual-review results are kept in a durable delivery queue until
    # the main analysis loop has merged them into its authoritative result set.
    # This is deliberately separate from pending_candidates: an accepted result
    # must not disappear merely because a later audit candidate timed out.
    refine_result_queue: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def create(
        cls,
        *,
        job_id: str,
        room_id: str,
        recording_id: str,
        source_path: str,
        final_duration: float,
        valorant_profile: str = "pov",
    ) -> FinalizationJob:
        return cls(
            job_id=str(job_id),
            room_id=str(room_id),
            recording_id=str(recording_id),
            source_path=str(source_path),
            final_duration=max(0.0, float(final_duration)),
            valorant_profile=str(valorant_profile or "pov"),
        )

    def add_coverage(self, start: float, end: float) -> None:
        """登记成功完成的扫描范围，并移除已完全修复的失败窗口。"""
        self.coverage_ranges = merge_ranges([*self.coverage_ranges, (start, end)])
        self.scan_cursor = max(self.scan_cursor, float(end))
        active_failures: list[dict[str, Any]] = []
        for failure in self.failed_ranges:
            try:
                remaining = uncovered_ranges(
                    self.coverage_ranges,
                    float(failure["start"]),
                    float(failure["end"]),
                )
            except (KeyError, TypeError, ValueError):
                remaining = [(0.0, 1.0)]
            if remaining:
                active_failures.append(failure)
        self.failed_ranges = active_failures
        self.updated_at = time.time()

    def add_failure(
        self,
        start: float,
        end: float,
        error: str,
        *,
        attempt: int | None = None,
    ) -> None:
        """记录失败窗口，但不推进覆盖游标。"""
        key = _range_key(start, end)
        current_attempt = int(self.scan_attempts.get(key, 0) or 0) + 1
        self.scan_attempts[key] = max(current_attempt, int(attempt or 0))
        self.failed_ranges.append(
            {
                "start": float(start),
                "end": float(end),
                "error": str(error),
                "attempt": self.scan_attempts[key],
                "updated_at": time.time(),
            }
        )
        self.last_error = str(error)
        self.updated_at = time.time()

    def update_pending_candidates(self, candidates: Iterable[dict[str, Any]]) -> None:
        """更新持久化的待审/候选回合列表（按 start 去重合并），防止收尾重试或异常退出时候选丢失。"""
        merged: dict[float, dict[str, Any]] = {}
        for item in self.pending_candidates:
            if isinstance(item, dict) and "start" in item:
                try:
                    merged[round(float(item["start"]), 3)] = dict(item)
                except (TypeError, ValueError):
                    continue
        for cand in candidates:
            if isinstance(cand, dict) and "start" in cand:
                try:
                    merged[round(float(cand["start"]), 3)] = dict(cand)
                except (TypeError, ValueError):
                    continue
        self.pending_candidates = list(merged.values())
        self.candidate_count = max(self.candidate_count, len(self.pending_candidates))
        self.updated_at = time.time()

    def replace_pending_candidates(self, candidates: Iterable[dict[str, Any]]) -> None:
        """Replace the current recoverable queue while keeping total count."""
        historical_count = int(self.candidate_count or 0)
        self.pending_candidates = []
        self.update_pending_candidates(candidates)
        self.candidate_count = max(historical_count, self.candidate_count)

    def enqueue_refine_result(
        self,
        candidate: dict[str, Any],
        delivery_key: str,
        *,
        outcome: str = "accepted",
    ) -> bool:
        """Append one idempotent refine result to the durable delivery queue."""
        key = str(delivery_key or "").strip()
        if not key or not isinstance(candidate, dict):
            return False
        if any(
            str(item.get("delivery_key") or "") == key
            for item in self.refine_result_queue
            if isinstance(item, dict)
        ):
            return False
        self.refine_result_queue.append(
            {
                "delivery_key": key,
                "outcome": str(outcome or "accepted"),
                "candidate": dict(candidate),
            }
        )
        self.updated_at = time.time()
        return True

    def ack_refine_results(self, delivery_keys: Iterable[str]) -> int:
        """Remove results successfully consumed by the main loop."""
        keys = {str(key) for key in delivery_keys if str(key).strip()}
        if not keys:
            return 0
        before = len(self.refine_result_queue)
        self.refine_result_queue = [
            item
            for item in self.refine_result_queue
            if not isinstance(item, dict)
            or str(item.get("delivery_key") or "") not in keys
        ]
        removed = before - len(self.refine_result_queue)
        if removed:
            self.updated_at = time.time()
        return removed

    def is_fully_covered(self, *, epsilon: float = DEFAULT_COVERAGE_EPSILON_SEC) -> bool:
        """判断 coverage 是否覆盖到最终文件尾。"""
        return not uncovered_ranges(
            self.coverage_ranges,
            0.0,
            self.final_duration,
            epsilon=max(0.0, float(epsilon)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": FINALIZATION_SCHEMA_VERSION,
            "job_id": self.job_id,
            "room_id": self.room_id,
            "recording_id": self.recording_id,
            "source_path": self.source_path,
            "final_duration": self.final_duration,
            "valorant_profile": self.valorant_profile,
            "phase": self.phase,
            "scan_cursor": self.scan_cursor,
            "coverage_ranges": [list(item) for item in self.coverage_ranges],
            "scan_attempts": dict(self.scan_attempts),
            "failed_ranges": [dict(item) for item in self.failed_ranges],
            "candidate_count": self.candidate_count,
            "final_round_count": self.final_round_count,
            "last_error": self.last_error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "pending_candidates": [
                dict(item) for item in self.pending_candidates if isinstance(item, dict)
            ],
            "refine_result_queue": [
                dict(item)
                for item in self.refine_result_queue
                if isinstance(item, dict)
            ],
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> FinalizationJob:
        if not isinstance(payload, dict):
            raise ValueError("finalization job payload must be a mapping")
        ranges = payload.get("coverage_ranges") or []
        parsed_ranges: list[tuple[float, float]] = []
        for item in ranges:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                parsed_ranges.append((float(item[0]), float(item[1])))
        return cls(
            job_id=str(payload.get("job_id") or ""),
            room_id=str(payload.get("room_id") or ""),
            recording_id=str(payload.get("recording_id") or ""),
            source_path=str(payload.get("source_path") or ""),
            final_duration=max(0.0, float(payload.get("final_duration", 0.0) or 0.0)),
            valorant_profile=str(payload.get("valorant_profile") or "pov"),
            phase=str(payload.get("phase") or "pending"),
            scan_cursor=max(0.0, float(payload.get("scan_cursor", 0.0) or 0.0)),
            coverage_ranges=merge_ranges(parsed_ranges),
            scan_attempts={
                str(key): int(value or 0)
                for key, value in (payload.get("scan_attempts") or {}).items()
            },
            failed_ranges=[
                dict(item)
                for item in (payload.get("failed_ranges") or [])
                if isinstance(item, dict)
            ],
            candidate_count=int(payload.get("candidate_count", 0) or 0),
            final_round_count=int(payload.get("final_round_count", 0) or 0),
            last_error=str(payload.get("last_error") or ""),
            created_at=float(payload.get("created_at", time.time()) or time.time()),
            updated_at=float(payload.get("updated_at", time.time()) or time.time()),
            pending_candidates=[
                dict(item)
                for item in (payload.get("pending_candidates") or [])
                if isinstance(item, dict)
            ],
            refine_result_queue=[
                dict(item)
                for item in (payload.get("refine_result_queue") or [])
                if isinstance(item, dict)
            ],
        )


def _range_key(start: float, end: float) -> str:
    return f"{float(start):.3f}:{float(end):.3f}"


__all__ = [
    "BROADCAST_PRECISE_BOUNDARY_DELTA_SEC",
    "DEFAULT_COVERAGE_EPSILON_SEC",
    "DEFAULT_FULL_RESCAN_LAG_SEC",
    "DEFAULT_PRECISE_BOUNDARY_DELTA_SEC",
    "FINALIZATION_SCHEMA_VERSION",
    "FinalizationJob",
    "boundary_quality_reason_code",
    "classify_boundary_quality",
    "finalization_requires_full_rescan",
    "merge_ranges",
    "uncovered_ranges",
]
