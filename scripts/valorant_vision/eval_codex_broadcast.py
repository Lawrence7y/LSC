#!/usr/bin/env python3
"""Evaluate current ONNX model on Codex broadcast labels (optionally test-only)."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

import cv2
import numpy as np

from lsc.analyzer.valorant_frame_classifier import ValorantFrameClassifier, _CLASS_NAMES

DEFAULT_ANNOTATION_DIR = Path(
    r"D:\Project\直播切片多人\.worktrees\valorant-frame-labeling-pilot"
    r"\datasets\valorant_phase\annotations\new_broadcast_20260721"
)
DEFAULT_OUT_DIR = Path.home() / "LSC" / "datasets" / "valorant_phase" / "annotate"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="all", choices=["all", "test", "train", "val"])
    ap.add_argument(
        "--annotation-dir",
        type=Path,
        default=None,
        help="Annotation root with queue.json and labels.json (default: built-in Codex path)",
    )
    ap.add_argument("--model-dir", type=Path, default=None)
    ap.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Full path for evaluation report JSON (overrides --out-name)",
    )
    ap.add_argument("--out-name", default="blind_codex_eval.json")
    return ap.parse_args(argv)


def imread(p: Path):
    data = np.fromfile(str(p), dtype=np.uint8)
    img = cv2.imdecode(data, cv2.IMREAD_COLOR)
    return img if img is not None else cv2.imread(str(p))


def main() -> None:
    args = parse_args()
    annotation_dir = (args.annotation_dir or DEFAULT_ANNOTATION_DIR).expanduser().resolve()
    if args.output is not None:
        out = args.output.expanduser().resolve()
    else:
        out = DEFAULT_OUT_DIR / args.out_name

    queue = json.loads((annotation_dir / "queue.json").read_text(encoding="utf-8"))
    labels = json.loads((annotation_dir / "labels.json").read_text(encoding="utf-8"))
    if args.split != "all":
        queue = [x for x in queue if x.get("split") == args.split]

    clf = ValorantFrameClassifier(args.model_dir)
    clf.load()

    details = []
    batch, meta = [], []
    for item in queue:
        if item["id"] not in labels:
            continue
        img = imread(annotation_dir / item["rel_path"])
        if img is None:
            continue
        batch.append(img)
        meta.append(item)
        if len(batch) >= 32:
            probs = clf.predict_batch(batch)
            for it, pr in zip(meta, probs):
                pi = int(pr.argmax())
                pred = _CLASS_NAMES[pi]
                gt = labels[it["id"]]["label"]
                details.append(
                    {
                        "id": it["id"],
                        "video_id": it["video_id"],
                        "timestamp_sec": it["timestamp_sec"],
                        "split": it.get("split"),
                        "gt": gt,
                        "pred": pred,
                        "conf": float(pr[pi]),
                        "ok": pred == gt,
                    }
                )
            batch, meta = [], []
    if batch:
        probs = clf.predict_batch(batch)
        for it, pr in zip(meta, probs):
            pi = int(pr.argmax())
            pred = _CLASS_NAMES[pi]
            gt = labels[it["id"]]["label"]
            details.append(
                {
                    "id": it["id"],
                    "video_id": it["video_id"],
                    "timestamp_sec": it["timestamp_sec"],
                    "split": it.get("split"),
                    "gt": gt,
                    "pred": pred,
                    "conf": float(pr[pi]),
                    "ok": pred == gt,
                }
            )

    n = len(details)
    correct = sum(d["ok"] for d in details)
    metrics = {
        "split": args.split,
        "provider": clf.provider,
        "n": n,
        "accuracy": round(correct / n, 4) if n else 0,
        "correct": correct,
        "errors": n - correct,
        "gt_dist": dict(Counter(d["gt"] for d in details)),
        "pred_dist": dict(Counter(d["pred"] for d in details)),
        "error_types": {
            f"{a}->{b}": c
            for (a, b), c in Counter(
                (d["gt"], d["pred"]) for d in details if not d["ok"]
            ).most_common()
        },
        "by_video": {},
        "per_class_recall": {},
    }
    for vid in sorted({d["video_id"] for d in details}):
        rows = [d for d in details if d["video_id"] == vid]
        metrics["by_video"][vid] = {
            "n": len(rows),
            "acc": round(sum(r["ok"] for r in rows) / len(rows), 4),
        }
    for lab in _CLASS_NAMES:
        rows = [d for d in details if d["gt"] == lab]
        if rows:
            metrics["per_class_recall"][lab] = round(
                sum(r["ok"] for r in rows) / len(rows), 4
            )

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(metrics, ensure_ascii=False, indent=2))
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
