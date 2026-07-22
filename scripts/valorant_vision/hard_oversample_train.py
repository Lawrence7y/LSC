#!/usr/bin/env python3
"""Hard-mine annotate combat frames misclassified by current model; oversample into train.

Assumes train/val already built by organize_from_annotate.py.
Does NOT inject Pakki frames (keeps blind eval honest).
"""
from __future__ import annotations

import shutil
from pathlib import Path

import cv2

from lsc.analyzer.valorant_frame_classifier import ValorantFrameClassifier

DATA = Path.home() / "LSC" / "datasets" / "valorant_phase"
HARD_OVERSAMPLE = 3
LABELS = ("non_game", "buy", "combat", "result", "replay")


def _link(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest.unlink()
    try:
        dest.hardlink_to(src)
    except OSError:
        shutil.copy2(src, dest)


def main() -> None:
    clf = ValorantFrameClassifier()
    clf.load()
    combat_dir = DATA / "train" / "combat"
    hard: list[Path] = []
    for path in sorted(combat_dir.glob("*.jpg")):
        if path.name.startswith("hardx"):
            continue
        bgr = cv2.imread(str(path))
        if bgr is None:
            continue
        probs = clf.predict_batch([bgr])[0]
        pred_i = int(probs.argmax())
        pred = LABELS[pred_i]
        if pred in ("buy", "result") and float(probs[pred_i]) >= 0.45:
            hard.append(path)

    extra = 0
    for path in hard:
        for k in range(HARD_OVERSAMPLE):
            dest = combat_dir / f"hardx{k}_{path.name}"
            _link(path, dest)
            extra += 1

    print(f"provider={clf.provider} hard_mined={len(hard)} oversample_extra={extra}")
    for split in ("train", "val"):
        for lab in LABELS:
            n = len(list((DATA / split / lab).glob("*.jpg")))
            if n:
                print(f"  {split}/{lab}: {n}")


if __name__ == "__main__":
    main()
