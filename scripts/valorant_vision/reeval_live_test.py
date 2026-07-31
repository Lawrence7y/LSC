#!/usr/bin/env python3
"""Re-eval live_test frames with current model; compare to previous report + draft GT."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import cv2
import numpy as np

from lsc.analyzer.valorant_frame_classifier import _CLASS_NAMES, ValorantFrameClassifier

LIVE = Path.home() / "LSC" / "datasets" / "valorant_phase" / "live_test_59475730286"
OVERRIDES = {162.0: "non_game", 370.0: "non_game", 78.0: "result"}


def imread(p: Path):
    data = np.fromfile(str(p), dtype=np.uint8)
    img = cv2.imdecode(data, cv2.IMREAD_COLOR)
    return img if img is not None else cv2.imread(str(p))


def main() -> None:
    old = json.loads((LIVE / "eval_report.json").read_text(encoding="utf-8"))
    draft = LIVE / "draft_gt.json"
    gt_map = {}
    if draft.is_file():
        for item in json.loads(draft.read_text(encoding="utf-8"))["items"]:
            gt_map[float(item["timestamp_sec"])] = item["gt"]
    for t, lab in OVERRIDES.items():
        gt_map[t] = lab

    clf = ValorantFrameClassifier()
    clf.load()
    new_details = []
    batch, meta = [], []
    for d in old["details"]:
        img = imread(Path(d["path"]))
        if img is None:
            continue
        batch.append(img)
        meta.append(d)
        if len(batch) >= 16:
            probs = clf.predict_batch(batch)
            for row, pr in zip(meta, probs, strict=True):
                pi = int(pr.argmax())
                new_details.append(
                    {
                        "timestamp_sec": row["timestamp_sec"],
                        "path": row["path"],
                        "pred": _CLASS_NAMES[pi],
                        "conf": float(pr[pi]),
                        "old_pred": row["pred"],
                        "old_conf": row["conf"],
                    }
                )
            batch, meta = [], []
    if batch:
        probs = clf.predict_batch(batch)
        for row, pr in zip(meta, probs, strict=True):
            pi = int(pr.argmax())
            new_details.append(
                {
                    "timestamp_sec": row["timestamp_sec"],
                    "path": row["path"],
                    "pred": _CLASS_NAMES[pi],
                    "conf": float(pr[pi]),
                    "old_pred": row["pred"],
                    "old_conf": row["conf"],
                }
            )

    # compare on draft GT subset
    scored = []
    for d in new_details:
        t = float(d["timestamp_sec"])
        if t not in gt_map:
            continue
        gt = gt_map[t]
        scored.append({**d, "gt": gt, "ok": d["pred"] == gt, "old_ok": d["old_pred"] == gt})

    n = len(scored)
    metrics = {
        "provider": clf.provider,
        "n_scored": n,
        "acc_new": round(sum(s["ok"] for s in scored) / n, 4) if n else 0,
        "acc_old": round(sum(s["old_ok"] for s in scored) / n, 4) if n else 0,
        "pred_dist_new": dict(Counter(d["pred"] for d in new_details)),
        "pred_dist_old": dict(Counter(d["old_pred"] for d in new_details)),
        "fixed_overrides": {},
        "changed": [],
        "still_wrong": [],
    }
    for t, lab in OVERRIDES.items():
        d = next(x for x in new_details if abs(x["timestamp_sec"] - t) < 0.1)
        metrics["fixed_overrides"][str(t)] = {
            "gt": lab,
            "old": d["old_pred"],
            "new": d["pred"],
            "new_conf": d["conf"],
            "ok": d["pred"] == lab,
        }
    for d in new_details:
        if d["pred"] != d["old_pred"]:
            metrics["changed"].append(
                f"t={d['timestamp_sec']:.0f} {d['old_pred']}->{d['pred']} "
                f"({d['old_conf']:.2f}->{d['conf']:.2f})"
            )
    for s in scored:
        if not s["ok"]:
            metrics["still_wrong"].append(
                f"t={s['timestamp_sec']:.0f} gt={s['gt']} pred={s['pred']} conf={s['conf']:.2f}"
            )

    out = LIVE / "eval_report_v2.json"
    out.write_text(
        json.dumps({"metrics": metrics, "details": new_details}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(metrics, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
