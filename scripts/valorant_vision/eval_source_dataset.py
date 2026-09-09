#!/usr/bin/env python3
"""Evaluate a Valorant phase model directory against a source dataset split (train/val/test)
and check against eval_gates release thresholds.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

try:
    import numpy as np
except ModuleNotFoundError:  # pragma: no cover - runtime dependency diagnostic
    np = None

try:
    import cv2
except ModuleNotFoundError:  # pragma: no cover - runtime dependency diagnostic
    cv2 = None

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from eval_gates import (
    CLASS_NAMES,
    GATE_BUY_PRECISION_MIN,
    GATE_MACRO_F1_MIN,
    GATE_NON_GAME_RECALL_MIN,
    GATE_REPLAY_RECALL_MIN,
    GATE_RESULT_PRECISION_MIN,
    build_classification_report,
    check_all_gates,
    classification_report_to_dict,
    compute_round_report,
    round_report_to_dict,
)
from lsc.analyzer.valorant_broadcast import (
    _predict_broadcast_batch,
    _stable_visual_label,
    _stabilize_broadcast_samples,
)
def imread(p: Path):
    if cv2 is None or np is None:
        raise RuntimeError("numpy and opencv-python are required for frame evaluation")
    data = np.fromfile(str(p), dtype=np.uint8)
    img = cv2.imdecode(data, cv2.IMREAD_COLOR)
    return img if img is not None else cv2.imread(str(p))


def _load_manifest_index(path: Path | None) -> dict[str, dict[str, str]]:
    """Load optional manifest provenance keyed by frame path/basename."""
    if path is None or not path.is_file():
        return {}
    index: dict[str, dict[str, str]] = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                continue
            frame_path = str(row.get("frame_path") or "")
            if not frame_path:
                continue
            meta = {
                key: str(row[key])
                for key in ("source_type", "session_id", "video_id", "timestamp_sec")
                if row.get(key) is not None
            }
            index[frame_path] = meta
            index[Path(frame_path).name] = meta
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        return {}
    return index


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def evaluate(
    model_dir: Path,
    data_dir: Path,
    split: str = "val",
    batch_size: int = 64,
    mode: str = "plain_frame",
    rounds_path: Path | None = None,
    manifest_path: Path | None = None,
) -> dict:
    if mode not in {"plain_frame", "broadcast_runtime"}:
        raise ValueError(f"unsupported evaluation mode: {mode}")
    if cv2 is None or np is None:
        raise RuntimeError("numpy and opencv-python are required for frame evaluation")
    from lsc.analyzer.valorant_frame_classifier import ValorantFrameClassifier
    clf = ValorantFrameClassifier(model_dir)
    clf.load()
    print(f"Loaded model from {model_dir}: version={clf.model_version}, provider={clf.provider}")

    split_dir = data_dir / split
    if not split_dir.is_dir():
        print(f"Error: Split directory does not exist: {split_dir}", file=sys.stderr)
        sys.exit(1)

    y_true: list[str] = []
    y_pred: list[str] = []
    raw_predictions: list[dict] = []
    source_types: list[str | None] = []
    confidences: list[float] = []

    manifest_index = _load_manifest_index(manifest_path)
    samples: list[tuple[Path, str]] = []
    for cls in CLASS_NAMES:
        cls_dir = split_dir / cls
        if cls_dir.is_dir():
            for f in sorted(cls_dir.glob("*.jpg")):
                samples.append((f, cls))

    default_source_type = (
        "broadcast" if "broadcast" in data_dir.name.lower()
        else ("pov" if "pov" in data_dir.name.lower() else None)
    )
    if manifest_index:
        def _sample_order(item: tuple[Path, str]) -> tuple[str, float, str]:
            meta = manifest_index.get(str(item[0])) or manifest_index.get(item[0].name, {})
            try:
                timestamp = float(meta.get("timestamp_sec", "0"))
            except (TypeError, ValueError):
                timestamp = 0.0
            return (
                str(meta.get("video_id") or meta.get("session_id") or ""),
                timestamp,
                str(item[0]),
            )
        samples.sort(key=_sample_order)

    print(f"Evaluating {len(samples)} frames in {split_dir}...")
    if not samples:
        print("Error: No frames found for evaluation.", file=sys.stderr)
        sys.exit(1)

    for i in range(0, len(samples), batch_size):
        batch = samples[i : i + batch_size]
        images = [imread(p) for p, _ in batch]
        valid_indices = [idx for idx, img in enumerate(images) if img is not None]
        if len(valid_indices) < len(batch):
            print(f"Warning: Failed to read {len(batch) - len(valid_indices)} frames.")

        sub_images = [images[idx] for idx in valid_indices]
        probs = (
            _predict_broadcast_batch(clf, sub_images)
            if mode == "broadcast_runtime"
            else clf.predict_batch(sub_images)
        )
        for idx, p_dist in zip(valid_indices, probs):
            if mode == "broadcast_runtime":
                pred_cls, confidence = _stable_visual_label(
                    p_dist,
                    stable_prob=clf.thresholds.get("stable_prob", 0.55),
                    class_stable_prob=clf.class_stable_prob,
                )
            else:
                pred_idx = int(p_dist.argmax())
                pred_cls = CLASS_NAMES[pred_idx]
                confidence = float(p_dist[pred_idx])
            y_pred.append(pred_cls)
            y_true.append(batch[idx][1])
            confidences.append(confidence)
            raw_predictions.append({
                "path": str(batch[idx][0]),
                "true_label": batch[idx][1],
                "predicted_label": pred_cls,
                "confidence": confidence,
                "probabilities": [float(value) for value in p_dist],
            })
            provenance = manifest_index.get(str(batch[idx][0])) or manifest_index.get(
                batch[idx][0].name, {}
            )
            source_types.append(provenance.get("source_type") or default_source_type)
            raw_predictions[-1]["source_type"] = source_types[-1]
            if provenance.get("session_id"):
                raw_predictions[-1]["session_id"] = provenance["session_id"]
            if provenance.get("video_id"):
                raw_predictions[-1]["video_id"] = provenance["video_id"]

    if mode == "broadcast_runtime" and raw_predictions:
        # Temporal stabilization is applied only to a deterministic sample
        # sequence. The source directory loader is intentionally sorted above;
        # manifests with timestamps should be pre-ordered by the extraction
        # pipeline before evaluation.
        stabilized = _stabilize_broadcast_samples([
            (float(index), row["predicted_label"], row["confidence"])
            for index, row in enumerate(raw_predictions)
        ])
        y_pred = [item[1] for item in stabilized]
        for row, item in zip(raw_predictions, stabilized, strict=True):
            row["predicted_label"] = item[1]
            row["stable_label"] = item[1]

    unknown_count = sum(1 for label in y_pred if label == "unknown")

    report = build_classification_report(y_true, y_pred, source_types)
    rounds: dict | None = None
    if rounds_path:
        try:
            rounds_payload = json.loads(rounds_path.read_text(encoding="utf-8"))
            if isinstance(rounds_payload, dict):
                rounds = round_report_to_dict(compute_round_report(rounds_payload))
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            print(f"Warning: invalid rounds report: {exc}", file=sys.stderr)

    classification = classification_report_to_dict(report)
    failures = check_all_gates(classification, rounds)
    session_ids = {
        row.get("session_id")
        for row in raw_predictions
        if row.get("session_id")
    }
    sessions_by_source: dict[str, set[str]] = defaultdict(set)
    for row in raw_predictions:
        source = row.get("source_type")
        session = row.get("session_id")
        if source and session:
            sessions_by_source[str(source)].add(str(session))
    data_summary = {
        "frame_count": len(y_true),
        "split": split,
        "class_support": dict(Counter(y_true)),
        "unknown_prediction_count": unknown_count,
        "source_session_count": len(session_ids),
        "source_sessions_by_type": {
            source: len(values) for source, values in sessions_by_source.items()
        },
        "source_type_count": dict(Counter(item for item in source_types if item)),
    }

    # Print Confusion Matrix
    print("\n" + "=" * 65)
    print(f"CONFUSION MATRIX [{split.upper()}] (rows = true, cols = pred)")
    print("=" * 65)
    header = "true\\pred".ljust(12) + "".join(c.rjust(10) for c in CLASS_NAMES)
    print(header)
    print("-" * len(header))
    for gt in CLASS_NAMES:
        row = gt.ljust(12) + "".join(
            str(report.confusion.get(gt, {}).get(pr, 0)).rjust(10) for pr in CLASS_NAMES
        )
        print(row)

    # Print Class Metrics
    print("\n" + "=" * 65)
    print(f"PER-CLASS METRICS [{split.upper()}]")
    print("=" * 65)
    print(f"{'Class':<12} {'Precision':<12} {'Recall':<12} {'F1':<12} {'Support':<8}")
    print("-" * 65)
    for cls in CLASS_NAMES:
        m = report.per_class[cls]
        print(f"{cls:<12} {m.precision:<12.4f} {m.recall:<12.4f} {m.f1:<12.4f} {m.support:<8}")
    print("-" * 65)
    print(f"{'Macro F1':<12} {'':<12} {'':<12} {report.macro_f1:<12.4f} {report.total:<8}")

    # Check Gates
    print("\n" + "=" * 65)
    print("RELEASE GATES CHECK")
    print("=" * 65)
    gates = [
        ("Macro F1", report.macro_f1, GATE_MACRO_F1_MIN, ">=", "total"),
        ("Replay Recall", report.per_class["replay"].recall, GATE_REPLAY_RECALL_MIN, ">=", "replay"),
        ("Non-Game Recall", report.per_class["non_game"].recall, GATE_NON_GAME_RECALL_MIN, ">=", "non_game"),
        ("Buy Precision", report.per_class["buy"].precision, GATE_BUY_PRECISION_MIN, ">=", "buy"),
        ("Result Precision", report.per_class["result"].precision, GATE_RESULT_PRECISION_MIN, ">=", "result"),
    ]

    all_passed = True
    for name, val, thresh, op, target_cls in gates:
        support = (
            report.per_class[target_cls].support
            if target_cls != "total"
            else report.total
        )
        passed = (val >= thresh) if op == ">=" else (val <= thresh)
        status = "PASS" if passed else "FAIL"
        if not passed:
            all_passed = False
        print(f"  [{status}] {name:<20}: {val:.4f} (gate {op} {thresh:.4f}, support={support})")

    if not failures:
        print("\n=> ALL RELEASE GATES PASSED")
    else:
        print("\n=> SOME GATES FAILED")

    # The release decision is shared with eval_gates instead of duplicating a
    # second set of thresholds in this script. ``all_passed`` above is retained
    # in the console output for backward-compatible readability.
    return {
        **classification,
        # Keep the historical key for existing report consumers.
        "confusion": report.confusion,
        "model_sha256": _sha256(model_dir / "valorant_phase_v1.onnx"),
        "evaluation_mode": mode,
        "model_version": clf.model_version,
        "provider": clf.provider,
        "data_summary": data_summary,
        "rounds": rounds,
        "gate_failures": [
            {"check": item.check, "message": item.message} for item in failures
        ],
        "gates_passed": not failures,
        "predictions": raw_predictions,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-dir", type=Path, default=Path("lsc/analyzer/models"))
    ap.add_argument("--data-dir", type=Path, default=Path("datasets/valorant_phase_broadcast"))
    ap.add_argument("--split", choices=["train", "val", "test"], default="val")
    ap.add_argument("--output", type=Path, default=None)
    ap.add_argument(
        "--mode",
        choices=["plain_frame", "broadcast_runtime"],
        default="plain_frame",
        help="推理口径：普通整帧或复用生产 broadcast 融合/阈值/稳定器",
    )
    ap.add_argument(
        "--rounds",
        type=Path,
        default=None,
        help="可选完整录像回合 GT/预测 JSON，用于回合级门禁",
    )
    ap.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help="可选 JSONL 清单，用于 source_type/session_id 溯源",
    )
    args = ap.parse_args()

    res = evaluate(
        args.model_dir,
        args.data_dir,
        split=args.split,
        mode=args.mode,
        rounds_path=args.rounds,
        manifest_path=args.manifest,
    )
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(res, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
