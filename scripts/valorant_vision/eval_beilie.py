#!/usr/bin/env python3
"""Evaluate current model on pov_beilie frames against human labels."""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

import cv2

from lsc.analyzer.valorant_frame_classifier import ValorantFrameClassifier

LABELS = ["non_game", "buy", "combat", "result", "replay"]
ANN = Path.home() / "LSC" / "datasets" / "valorant_phase" / "annotate"
DATA = Path.home() / "LSC" / "datasets" / "valorant_phase"


def report(name: str, rows: list[dict]) -> None:
    if not rows:
        return
    n = len(rows)
    c = sum(r["ok"] for r in rows)
    print(f"\n=== {name} n={n} acc={c / n:.4f} ({c}/{n}) ===")
    cm: dict[str, Counter[str]] = defaultdict(Counter)
    for r in rows:
        cm[r["gt"]][r["pred"]] += 1
    print("".ljust(10) + "".join(x.rjust(8) for x in LABELS))
    for g in LABELS:
        print(g.ljust(10) + "".join(str(cm[g][p]).rjust(8) for p in LABELS))
    wrong = [r for r in rows if not r["ok"]]
    print("errors", len(wrong), dict(Counter((r["gt"], r["pred"]) for r in wrong)))
    for thr in (0.55, 0.7, 0.8):
        sub = [r for r in rows if r["conf"] >= thr]
        if not sub:
            continue
        acc = sum(r["ok"] for r in sub) / len(sub)
        print(f"acc@conf>={thr:.2f}: {acc:.4f} ({sum(r['ok'] for r in sub)}/{len(sub)})")


def main() -> None:
    queue = json.loads((ANN / "queue.json").read_text(encoding="utf-8"))
    labels = json.loads((ANN / "labels.json").read_text(encoding="utf-8"))
    train_names = {p.name for p in (DATA / "train").rglob("pov_beilie_*.jpg")}
    val_names = {p.name for p in (DATA / "val").rglob("pov_beilie_*.jpg")}

    items = [x for x in queue if x["rel_path"].startswith("pov_beilie/")]
    print("beilie_frames", len(items))

    clf = ValorantFrameClassifier()
    clf.load()
    print("provider", clf.provider)

    details = []
    for item in items:
        gt = labels[item["id"]]["label"]
        src = Path(item["abs_path"])
        if not src.is_file():
            src = ANN / item["rel_path"]
        probs = clf.predict_batch([cv2.imread(str(src))])[0]
        idx = int(probs.argmax())
        pred = LABELS[idx]
        conf = float(probs[idx])
        ts_ms = int(round(float(item["timestamp_sec"]) * 1000))
        fname = f"{item['video_id']}_{ts_ms}.jpg"
        split = "train" if fname in train_names else ("val" if fname in val_names else "unknown")
        details.append(
            {
                "rel_path": item["rel_path"],
                "gt": gt,
                "pred": pred,
                "conf": round(conf, 4),
                "ok": gt == pred,
                "split": split,
                "ts": item["timestamp_sec"],
            }
        )

    report("ALL", details)
    report("VAL_only", [r for r in details if r["split"] == "val"])
    report("TRAIN_only", [r for r in details if r["split"] == "train"])

    out = ANN / "eval_beilie.json"
    out.write_text(json.dumps(details, ensure_ascii=False, indent=2), encoding="utf-8")
    print("wrote", out)


if __name__ == "__main__":
    main()
