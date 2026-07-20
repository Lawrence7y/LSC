"""Valorant 混合视觉评估指标与发布门槛（纯函数，可供 CLI 与单元测试导入）。"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

CLASS_NAMES = ("non_game", "buy", "combat", "result", "replay")
SOURCE_TYPES = ("broadcast", "pov")

# 发布门槛（与设计文档一致）
GATE_MACRO_F1_MIN = 0.94
GATE_BUY_PRECISION_MIN = 0.97
GATE_RESULT_PRECISION_MIN = 0.97
GATE_REPLAY_RECALL_MIN = 0.95
GATE_NON_GAME_RECALL_MIN = 0.95
GATE_ROUND_RECALL_MIN = 0.90
GATE_LISTED_PRECISION_MIN = 0.97
GATE_VC_ERR_P95_MAX = 0.8
GATE_VC_ERR_MAX = 2.0
FORCED_CLOSE_TARGETS = (150.0, 180.0)
FORCED_CLOSE_TOLERANCE = 1.5


@dataclass
class ClassMetrics:
    precision: float
    recall: float
    f1: float
    support: int


@dataclass
class ClassificationReport:
    confusion: dict[str, dict[str, int]]
    per_class: dict[str, ClassMetrics]
    macro_f1: float
    total: int
    by_source_type: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass
class RoundReport:
    recall: float
    listed_precision: float
    start_err_p95: float
    end_err_p95: float
    start_err_max: float
    end_err_max: float
    vision_confirmed_err_p95: float
    vision_confirmed_err_max: float
    vision_confirmed_count: int
    forced_close_150: int
    forced_close_180: int
    duplicate_keys: int
    non_game_listed_count: int
    matched: int
    ground_truth_count: int
    listed_count: int


@dataclass
class GateFailure:
    check: str
    message: str


def _safe_div(num: float, den: float) -> float:
    if den <= 0:
        return 0.0
    return num / den


def _f1(precision: float, recall: float) -> float:
    if precision + recall <= 0:
        return 0.0
    return 2.0 * precision * recall / (precision + recall)


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(abs(v) for v in values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * (pct / 100.0)
    lo = int(math.floor(rank))
    hi = int(math.ceil(rank))
    if lo == hi:
        return ordered[lo]
    frac = rank - lo
    return ordered[lo] * (1.0 - frac) + ordered[hi] * frac


def prediction_key(row: dict[str, Any]) -> tuple[str, float]:
    vid = str(row.get("video_id") or row.get("id") or "")
    ts = float(row.get("timestamp_sec", row.get("timestamp", 0.0)))
    return vid, round(ts, 3)


def label_from_row(row: dict[str, Any]) -> str:
    return str(row.get("label") or row.get("true_label") or row.get("gt_label"))


def pred_from_row(row: dict[str, Any]) -> str:
    return str(
        row.get("predicted_label")
        or row.get("pred_label")
        or row.get("prediction")
        or row.get("label_pred")
    )


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            stripped = line.strip()
            if stripped:
                rows.append(json.loads(stripped))
    return rows


def compute_confusion(
    y_true: list[str],
    y_pred: list[str],
    *,
    labels: tuple[str, ...] = CLASS_NAMES,
) -> dict[str, dict[str, int]]:
    matrix = {g: {p: 0 for p in labels} for g in labels}
    for truth, pred in zip(y_true, y_pred, strict=True):
        gt = truth if truth in matrix else "non_game"
        pr = pred if pred in matrix[gt] else "non_game"
        matrix[gt][pr] += 1
    return matrix


def metrics_from_confusion(
    confusion: dict[str, dict[str, int]],
    *,
    labels: tuple[str, ...] = CLASS_NAMES,
) -> tuple[dict[str, ClassMetrics], float]:
    per_class: dict[str, ClassMetrics] = {}
    f1s: list[float] = []
    for name in labels:
        tp = confusion.get(name, {}).get(name, 0)
        fp = sum(confusion.get(g, {}).get(name, 0) for g in labels if g != name)
        fn = sum(confusion.get(name, {}).get(p, 0) for p in labels if p != name)
        precision = _safe_div(tp, tp + fp)
        recall = _safe_div(tp, tp + fn)
        f1 = _f1(precision, recall)
        support = tp + fn
        per_class[name] = ClassMetrics(
            precision=precision,
            recall=recall,
            f1=f1,
            support=support,
        )
        if support > 0:
            f1s.append(f1)
    macro_f1 = sum(f1s) / len(f1s) if f1s else 0.0
    return per_class, macro_f1


def build_classification_report(
    y_true: list[str],
    y_pred: list[str],
    source_types: list[str | None] | None = None,
) -> ClassificationReport:
    confusion = compute_confusion(y_true, y_pred)
    per_class, macro_f1 = metrics_from_confusion(confusion)
    by_source: dict[str, dict[str, Any]] = {}
    if source_types:
        for st in SOURCE_TYPES:
            idxs = [i for i, s in enumerate(source_types) if s == st]
            if not idxs:
                continue
            sub_true = [y_true[i] for i in idxs]
            sub_pred = [y_pred[i] for i in idxs]
            sub_conf = compute_confusion(sub_true, sub_pred)
            sub_pc, sub_macro = metrics_from_confusion(sub_conf)
            by_source[st] = {
                "macro_f1": sub_macro,
                "per_class": {
                    k: {
                        "precision": v.precision,
                        "recall": v.recall,
                        "f1": v.f1,
                        "support": v.support,
                    }
                    for k, v in sub_pc.items()
                },
                "total": len(idxs),
            }
    return ClassificationReport(
        confusion=confusion,
        per_class=per_class,
        macro_f1=macro_f1,
        total=len(y_true),
        by_source_type=by_source,
    )


def align_predictions_labels(
    predictions: Iterable[dict[str, Any]],
    labels: Iterable[dict[str, Any]],
) -> tuple[list[str], list[str], list[str | None]]:
    label_map = {prediction_key(r): label_from_row(r) for r in labels}
    source_map = {prediction_key(r): r.get("source_type") for r in labels}
    y_true: list[str] = []
    y_pred: list[str] = []
    sources: list[str | None] = []
    for row in predictions:
        key = prediction_key(row)
        if key not in label_map:
            continue
        y_true.append(label_map[key])
        y_pred.append(pred_from_row(row))
        st = row.get("source_type") or source_map.get(key)
        sources.append(str(st) if st else None)
    return y_true, y_pred, sources


def classification_report_to_dict(report: ClassificationReport) -> dict[str, Any]:
    return {
        "confusion_matrix": report.confusion,
        "per_class": {
            k: {
                "precision": v.precision,
                "recall": v.recall,
                "f1": v.f1,
                "support": v.support,
            }
            for k, v in report.per_class.items()
        },
        "macro_f1": report.macro_f1,
        "total": report.total,
        "by_source_type": report.by_source_type,
    }


def _overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def _match_ground_truth(
    pred: dict[str, Any],
    ground_truth: list[dict[str, Any]],
    *,
    matched_gt: set[int],
) -> int | None:
    p0 = float(pred.get("start_sec", pred.get("start", 0.0)))
    p1 = float(pred.get("end_sec", pred.get("end", 0.0)))
    best_idx: int | None = None
    best_iou = 0.0
    for idx, gt in enumerate(ground_truth):
        if idx in matched_gt:
            continue
        g0 = float(gt.get("start_sec", gt.get("start", 0.0)))
        g1 = float(gt.get("end_sec", gt.get("end", 0.0)))
        inter = _overlap(p0, p1, g0, g1)
        union = max(p1, g1) - min(p0, g0)
        iou = _safe_div(inter, union)
        if iou > best_iou:
            best_iou = iou
            best_idx = idx
    if best_idx is None or best_iou < 0.3:
        return None
    return best_idx


def _is_forced_close(duration: float, target: float) -> bool:
    return abs(duration - target) <= FORCED_CLOSE_TOLERANCE


def compute_round_report(rounds: dict[str, Any]) -> RoundReport:
    ground_truth = list(rounds.get("ground_truth") or rounds.get("gt") or [])
    predictions = list(rounds.get("predictions") or rounds.get("preds") or [])

    matched_gt: set[int] = set()
    start_errs: list[float] = []
    end_errs: list[float] = []
    vc_start: list[float] = []
    vc_end: list[float] = []
    listed = 0
    listed_tp = 0
    non_game_listed = 0
    forced_150 = 0
    forced_180 = 0
    seen_keys: dict[str, int] = {}

    for pred in predictions:
        is_listed = bool(pred.get("listed", pred.get("in_list", False)))
        if is_listed:
            listed += 1
            if pred.get("contains_non_game"):
                non_game_listed += 1

        duration = float(
            pred.get("duration_sec")
            or pred.get("duration")
            or (
                float(pred.get("end_sec", pred.get("end", 0.0)))
                - float(pred.get("start_sec", pred.get("start", 0.0)))
            )
        )
        if _is_forced_close(duration, 150.0) or pred.get("forced_close_150"):
            forced_150 += 1
        if _is_forced_close(duration, 180.0) or pred.get("forced_close_180"):
            forced_180 += 1

        rk = str(pred.get("round_key") or pred.get("key") or "")
        if rk:
            seen_keys[rk] = seen_keys.get(rk, 0) + 1

        gt_idx = _match_ground_truth(pred, ground_truth, matched_gt=matched_gt)
        if gt_idx is None:
            if is_listed and pred.get("is_false_positive") is True:
                pass  # FP already counted in listed
            continue
        matched_gt.add(gt_idx)
        gt = ground_truth[gt_idx]
        g0 = float(gt.get("start_sec", gt.get("start", 0.0)))
        g1 = float(gt.get("end_sec", gt.get("end", 0.0)))
        p0 = float(pred.get("start_sec", pred.get("start", 0.0)))
        p1 = float(pred.get("end_sec", pred.get("end", 0.0)))
        start_errs.append(p0 - g0)
        end_errs.append(p1 - g1)
        if str(pred.get("confirm_status", "")) == "vision_confirmed":
            vc_start.append(p0 - g0)
            vc_end.append(p1 - g1)
        if is_listed:
            if pred.get("is_false_positive") is True:
                pass
            elif pred.get("is_true_positive") is False:
                pass
            else:
                listed_tp += 1

    recall = _safe_div(len(matched_gt), len(ground_truth))
    listed_precision = _safe_div(listed_tp, listed) if listed else 1.0
    duplicate_keys = sum(1 for c in seen_keys.values() if c > 1)

    all_vc_abs = [abs(x) for x in vc_start + vc_end]
    vc_p95 = _percentile(all_vc_abs, 95) if all_vc_abs else 0.0
    vc_max = max(all_vc_abs, default=0.0)

    return RoundReport(
        recall=recall,
        listed_precision=listed_precision,
        start_err_p95=_percentile(start_errs, 95),
        end_err_p95=_percentile(end_errs, 95),
        start_err_max=max((abs(x) for x in start_errs), default=0.0),
        end_err_max=max((abs(x) for x in end_errs), default=0.0),
        vision_confirmed_err_p95=vc_p95,
        vision_confirmed_err_max=vc_max,
        vision_confirmed_count=len(vc_start),
        forced_close_150=forced_150,
        forced_close_180=forced_180,
        duplicate_keys=duplicate_keys,
        non_game_listed_count=non_game_listed,
        matched=len(matched_gt),
        ground_truth_count=len(ground_truth),
        listed_count=listed,
    )


def round_report_to_dict(report: RoundReport) -> dict[str, Any]:
    return {
        "recall": report.recall,
        "listed_precision": report.listed_precision,
        "start_err_p95": report.start_err_p95,
        "end_err_p95": report.end_err_p95,
        "start_err_max": report.start_err_max,
        "end_err_max": report.end_err_max,
        "vision_confirmed_err_p95": report.vision_confirmed_err_p95,
        "vision_confirmed_err_max": report.vision_confirmed_err_max,
        "vision_confirmed_count": report.vision_confirmed_count,
        "forced_close_150": report.forced_close_150,
        "forced_close_180": report.forced_close_180,
        "duplicate_keys": report.duplicate_keys,
        "non_game_listed_count": report.non_game_listed_count,
        "matched": report.matched,
        "ground_truth_count": report.ground_truth_count,
        "listed_count": report.listed_count,
    }


def check_classification_gates(
    report: ClassificationReport | dict[str, Any],
) -> list[GateFailure]:
    failures: list[GateFailure] = []
    if isinstance(report, ClassificationReport):
        macro_f1 = report.macro_f1
        per_class = report.per_class
        by_source = report.by_source_type
    else:
        macro_f1 = float(report.get("macro_f1", 0.0))
        per_class_raw = report.get("per_class") or {}
        per_class = {
            k: ClassMetrics(
                precision=float(v.get("precision", 0.0)),
                recall=float(v.get("recall", 0.0)),
                f1=float(v.get("f1", 0.0)),
                support=int(v.get("support", 0)),
            )
            for k, v in per_class_raw.items()
        }
        by_source = report.get("by_source_type") or {}

    if macro_f1 < GATE_MACRO_F1_MIN:
        failures.append(
            GateFailure("macro_f1", f"Macro F1 {macro_f1:.4f} < {GATE_MACRO_F1_MIN}")
        )

    for cls_name, threshold, metric in (
        ("buy", GATE_BUY_PRECISION_MIN, "precision"),
        ("result", GATE_RESULT_PRECISION_MIN, "precision"),
        ("replay", GATE_REPLAY_RECALL_MIN, "recall"),
        ("non_game", GATE_NON_GAME_RECALL_MIN, "recall"),
    ):
        m = per_class.get(cls_name)
        if m is None:
            failures.append(GateFailure(f"{cls_name}_{metric}", f"缺少 {cls_name} 指标"))
            continue
        value = m.precision if metric == "precision" else m.recall
        if value < threshold:
            failures.append(
                GateFailure(
                    f"{cls_name}_{metric}",
                    f"{cls_name} {metric} {value:.4f} < {threshold}",
                )
            )

    for st in SOURCE_TYPES:
        block = by_source.get(st)
        if not block:
            continue
        st_macro = float(block.get("macro_f1", 0.0))
        if st_macro < GATE_MACRO_F1_MIN:
            failures.append(
                GateFailure(
                    f"{st}_macro_f1",
                    f"{st} Macro F1 {st_macro:.4f} < {GATE_MACRO_F1_MIN}",
                )
            )
    return failures


def check_round_gates(report: RoundReport | dict[str, Any]) -> list[GateFailure]:
    failures: list[GateFailure] = []
    if isinstance(report, RoundReport):
        recall = report.recall
        listed_precision = report.listed_precision
        err_p95 = report.vision_confirmed_err_p95
        err_max = report.vision_confirmed_err_max
        forced_150 = report.forced_close_150
        forced_180 = report.forced_close_180
        duplicate_keys = report.duplicate_keys
    else:
        recall = float(report.get("recall", 0.0))
        listed_precision = float(report.get("listed_precision", 0.0))
        err_p95 = float(
            report.get(
                "vision_confirmed_err_p95",
                max(
                    float(report.get("start_err_p95", 0.0)),
                    float(report.get("end_err_p95", 0.0)),
                ),
            )
        )
        err_max = float(
            report.get(
                "vision_confirmed_err_max",
                max(
                    float(report.get("start_err_max", 0.0)),
                    float(report.get("end_err_max", 0.0)),
                ),
            )
        )
        forced_150 = int(report.get("forced_close_150", 0))
        forced_180 = int(report.get("forced_close_180", 0))
        duplicate_keys = int(report.get("duplicate_keys", 0))

    if recall < GATE_ROUND_RECALL_MIN:
        failures.append(
            GateFailure("round_recall", f"round recall {recall:.4f} < {GATE_ROUND_RECALL_MIN}")
        )
    if listed_precision < GATE_LISTED_PRECISION_MIN:
        failures.append(
            GateFailure(
                "listed_precision",
                f"listed precision {listed_precision:.4f} < {GATE_LISTED_PRECISION_MIN}",
            )
        )
    if err_p95 > GATE_VC_ERR_P95_MAX:
        failures.append(
            GateFailure(
                "vision_confirmed_err_p95",
                f"vision_confirmed |err| P95 {err_p95:.4f}s > {GATE_VC_ERR_P95_MAX}s",
            )
        )
    if err_max > GATE_VC_ERR_MAX:
        failures.append(
            GateFailure(
                "vision_confirmed_err_max",
                f"vision_confirmed max |err| {err_max:.4f}s > {GATE_VC_ERR_MAX}s",
            )
        )
    if forced_150 > 0:
        failures.append(
            GateFailure("forced_close_150", f"发现 {forced_150} 条 150s 强制闭合切片")
        )
    if forced_180 > 0:
        failures.append(
            GateFailure("forced_close_180", f"发现 {forced_180} 条 180s 强制闭合切片")
        )
    if duplicate_keys > 0:
        failures.append(
            GateFailure("duplicate_keys", f"发现 {duplicate_keys} 个重复 round_key")
        )
    return failures


def check_all_gates(
    classification: ClassificationReport | dict[str, Any] | None,
    rounds: RoundReport | dict[str, Any] | None,
) -> list[GateFailure]:
    failures: list[GateFailure] = []
    if classification is not None:
        failures.extend(check_classification_gates(classification))
    if rounds is not None:
        failures.extend(check_round_gates(rounds))
    return failures
