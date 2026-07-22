#!/usr/bin/env python3
"""Audit live_test_84927583848: export low-conf + flips for visual GT."""
from __future__ import annotations

import json
import shutil
from collections import Counter
from pathlib import Path

import cv2
import numpy as np

LIVE = Path.home() / "LSC" / "datasets" / "valorant_phase" / "live_test_84927583848"
OUT = LIVE / "audit"


def imread(p: Path):
    data = np.fromfile(str(p), dtype=np.uint8)
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


def save(img, path: Path) -> None:
    h, w = img.shape[:2]
    sc = 640 / max(h, w)
    small = cv2.resize(img, (int(w * sc), int(h * sc)))
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imencode(".jpg", small)[1].tofile(str(path))


def main() -> None:
    report = json.loads((LIVE / "eval_report.json").read_text(encoding="utf-8"))
    details = report["details"]
    if OUT.exists():
        shutil.rmtree(OUT)
    thumbs = OUT / "thumbs"
    thumbs.mkdir(parents=True)

    picks = []
    for i, d in enumerate(details):
        reasons = []
        if d["conf"] < 0.70:
            reasons.append("low_conf")
        if d["pred"] in ("replay", "result", "buy"):
            reasons.append(f"pred_{d['pred']}")
        if i and d["pred"] != details[i - 1]["pred"]:
            reasons.append("flip")
        if reasons:
            picks.append({**d, "reasons": reasons})

    # also sample stable high-conf combat / non_game for reference
    for d in details:
        if d["pred"] == "combat" and d["conf"] >= 0.95:
            picks.append({**d, "reasons": ["ref_combat"]})
            break
    for d in details:
        if d["pred"] == "non_game" and d["conf"] >= 0.95:
            picks.append({**d, "reasons": ["ref_non_game"]})
            break

    by_idx = {}
    for p in picks:
        by_idx[p["idx"]] = p
    picks = sorted(by_idx.values(), key=lambda x: x["timestamp_sec"])

    items = []
    for p in picks:
        src = Path(p["path"])
        img = imread(src)
        if img is None:
            continue
        name = (
            f"t{int(p['timestamp_sec']):04d}_pred-{p['pred']}_"
            f"c{p['conf']:.2f}_{'+'.join(p['reasons'][:3])}.jpg"
        )
        save(img, thumbs / name)
        items.append({**p, "thumb": name})

    (OUT / "suspects.json").write_text(
        json.dumps(
            {
                "n": len(items),
                "mean_conf": report["mean_conf"],
                "pred_dist": report["pred_dist"],
                "low_conf_n": sum(1 for d in details if d["conf"] < 0.7),
                "items": items,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"picks={len(items)} low_conf={sum(1 for d in details if d['conf']<0.7)}/{len(details)}")
    print("pred_dist", report["pred_dist"], "mean_conf", report["mean_conf"])


if __name__ == "__main__":
    main()
