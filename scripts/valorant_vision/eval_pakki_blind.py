#!/usr/bin/env python3
"""Re-evaluate Pakki blind set with current installed model."""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

import cv2

from lsc.analyzer.valorant_frame_classifier import ValorantFrameClassifier

LABELS = ["non_game", "buy", "combat", "result", "replay"]
ROOT = Path.home() / "LSC" / "datasets" / "valorant_phase" / "blind_pakki"


def main() -> None:
    gt = json.loads((ROOT / "ground_truth.json").read_text(encoding="utf-8"))
    frames = sorted(ROOT.glob("frame_*.jpg"))
    clf = ValorantFrameClassifier()
    clf.load()
    print("provider", clf.provider)

    details = []
    for i, path in enumerate(frames, 1):
        probs = clf.predict_batch([cv2.imread(str(path))])[0]
        idx = int(probs.argmax())
        pred = LABELS[idx]
        conf = float(probs[idx])
        g = gt[str(i)]
        details.append({"idx": i, "gt": g, "pred": pred, "conf": round(conf, 4), "ok": g == pred})

    n = len(details)
    correct = sum(d["ok"] for d in details)
    print(f"accuracy={correct / n:.4f} ({correct}/{n})")

    cm: dict[str, Counter[str]] = defaultdict(Counter)
    for d in details:
        cm[d["gt"]][d["pred"]] += 1
    print("confusion (rows=gt):")
    print("".ljust(10) + "".join(c.rjust(8) for c in LABELS))
    for gt_l in LABELS:
        print(gt_l.ljust(10) + "".join(str(cm[gt_l][pr]).rjust(8) for pr in LABELS))

    wrong = [d for d in details if not d["ok"]]
    print("errors", len(wrong), dict(Counter((d["gt"], d["pred"]) for d in wrong)))
    combat_br = sum(1 for d in wrong if d["gt"] == "combat" and d["pred"] in ("buy", "result"))
    print("combat->buy+result", combat_br)
    for thr in (0.55, 0.7, 0.8):
        sub = [d for d in details if d["conf"] >= thr]
        if not sub:
            continue
        acc = sum(d["ok"] for d in sub) / len(sub)
        print(f"acc@conf>={thr:.2f}: {acc:.4f} ({sum(d['ok'] for d in sub)}/{len(sub)})")

    (ROOT / "predictions_v2.json").write_text(
        json.dumps(details, ensure_ascii=False, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
