#!/usr/bin/env python3
"""Re-eval POV fish live parts against draft_gt.json with current model."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import cv2
import numpy as np

from lsc.analyzer.valorant_frame_classifier import _CLASS_NAMES, ValorantFrameClassifier

LIVE = Path.home() / "LSC" / "datasets" / "valorant_phase" / "live_test_84927583848"


def imread(p: Path):
    data = np.fromfile(str(p), dtype=np.uint8)
    img = cv2.imdecode(data, cv2.IMREAD_COLOR)
    return img if img is not None else cv2.imread(str(p))


def main() -> None:
    draft = json.loads((LIVE / "draft_gt.json").read_text(encoding="utf-8"))
    items = draft["items"]
    clf = ValorantFrameClassifier()
    clf.load()

    details = []
    batch, meta = [], []
    for it in items:
        img = imread(Path(it["path"]))
        if img is None:
            continue
        batch.append(img)
        meta.append(it)
        if len(batch) >= 16:
            probs = clf.predict_batch(batch)
            for row, pr in zip(meta, probs, strict=True):
                pi = int(pr.argmax())
                pred = _CLASS_NAMES[pi]
                details.append(
                    {
                        "part": row.get("part"),
                        "timestamp_sec": row["timestamp_sec"],
                        "gt": row["gt"],
                        "old_pred": row["pred"],
                        "pred": pred,
                        "conf": float(pr[pi]),
                        "ok": pred == row["gt"],
                        "old_ok": row["pred"] == row["gt"],
                    }
                )
            batch, meta = [], []
    if batch:
        probs = clf.predict_batch(batch)
        for row, pr in zip(meta, probs, strict=True):
            pi = int(pr.argmax())
            pred = _CLASS_NAMES[pi]
            details.append(
                {
                    "part": row.get("part"),
                    "timestamp_sec": row["timestamp_sec"],
                    "gt": row["gt"],
                    "old_pred": row["pred"],
                    "pred": pred,
                    "conf": float(pr[pi]),
                    "ok": pred == row["gt"],
                    "old_ok": row["pred"] == row["gt"],
                }
            )

    n = len(details)
    metrics = {
        "provider": clf.provider,
        "n": n,
        "acc_new": round(sum(d["ok"] for d in details) / n, 4) if n else 0,
        "acc_old": round(sum(d["old_ok"] for d in details) / n, 4) if n else 0,
        "gt_dist": dict(Counter(d["gt"] for d in details)),
        "pred_dist": dict(Counter(d["pred"] for d in details)),
        "error_types": {
            f"{a}->{b}": c
            for (a, b), c in Counter(
                (d["gt"], d["pred"]) for d in details if not d["ok"]
            ).most_common()
        },
        "per_class_recall": {},
        "fixed_from_old": sum(1 for d in details if (not d["old_ok"]) and d["ok"]),
        "regressed": sum(1 for d in details if d["old_ok"] and (not d["ok"])),
    }
    for lab in _CLASS_NAMES:
        rows = [d for d in details if d["gt"] == lab]
        if rows:
            metrics["per_class_recall"][lab] = round(
                sum(r["ok"] for r in rows) / len(rows), 4
            )

    out = LIVE / "eval_after_opt.json"
    out.write_text(
        json.dumps({"metrics": metrics, "details": details}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(metrics, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
