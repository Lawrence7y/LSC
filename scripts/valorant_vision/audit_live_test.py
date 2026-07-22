#!/usr/bin/env python3
"""Audit live_test recording predictions; export suspects; write draft GT fixes."""
from __future__ import annotations

import json
import shutil
from collections import Counter
from pathlib import Path

import cv2
import numpy as np

LIVE = Path.home() / "LSC" / "datasets" / "valorant_phase" / "live_test_59475730286"
REPORT = LIVE / "eval_report.json"
FRAMES = LIVE / "frames"
OUT = LIVE / "audit"


def imread(p: Path):
    data = np.fromfile(str(p), dtype=np.uint8)
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


def save_thumb(img, path: Path, size=640) -> None:
    h, w = img.shape[:2]
    sc = size / max(h, w)
    small = cv2.resize(img, (int(w * sc), int(h * sc)))
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imencode(".jpg", small)[1].tofile(str(path))


def main() -> None:
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    details = report["details"]
    if OUT.exists():
        shutil.rmtree(OUT)
    thumbs = OUT / "thumbs"
    thumbs.mkdir(parents=True)

    suspects = []
    for d in details:
        reasons = []
        if d["pred"] == "result":
            reasons.append("pred_result")
        if d["conf"] < 0.80:
            reasons.append("low_conf")
        if d["pred"] in ("replay", "buy", "non_game") and d["conf"] < 0.90:
            reasons.append("edge_class")
        # neighbors flip
        # handled below
        if reasons:
            suspects.append({**d, "reasons": reasons})

    # transition frames: pred != prev pred
    for i, d in enumerate(details):
        if i == 0:
            continue
        if d["pred"] != details[i - 1]["pred"]:
            # export context
            entry = {**d, "reasons": list(set((d.get("reasons") or []) + ["transition"]))}
            # merge into suspects by idx
            found = next((s for s in suspects if s["idx"] == d["idx"]), None)
            if found:
                found["reasons"] = sorted(set(found["reasons"] + ["transition"]))
            else:
                suspects.append(entry)

    # unique by idx
    by_idx = {}
    for s in suspects:
        by_idx[s["idx"]] = s
    suspects = sorted(by_idx.values(), key=lambda x: x["timestamp_sec"])

    catalog = []
    for s in suspects:
        src = Path(s["path"])
        if not src.is_file():
            src = FRAMES / f"frame_{s['idx']:06d}.jpg"
        img = imread(src)
        if img is None:
            continue
        name = (
            f"t{int(s['timestamp_sec']):04d}_pred-{s['pred']}_"
            f"c{s['conf']:.2f}_{'+'.join(s['reasons'])}.jpg"
        )
        save_thumb(img, thumbs / name)
        catalog.append({**s, "thumb": name, "frame_path": str(src)})

    (OUT / "suspects.json").write_text(
        json.dumps(
            {
                "n": len(catalog),
                "by_reason": dict(
                    Counter(r for s in catalog for r in s["reasons"])
                ),
                "pred_dist": dict(Counter(s["pred"] for s in catalog)),
                "items": catalog,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"suspects={len(catalog)} -> {OUT}")
    print("by_reason", dict(Counter(r for s in catalog for r in s["reasons"])))
    for s in catalog:
        if "pred_result" in s["reasons"] or s["conf"] < 0.8:
            print(
                f"  t={s['timestamp_sec']:.0f} pred={s['pred']} conf={s['conf']:.3f} "
                f"{s['reasons']}"
            )


if __name__ == "__main__":
    main()
