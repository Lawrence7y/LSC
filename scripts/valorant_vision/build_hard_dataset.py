#!/usr/bin/env python3
"""Build train/val with hard-negative oversampling for combat death/Tab UI.

1. Stratified organize from annotate labels.
2. Hard-mine: annotate frames where GT=combat but current model predicts buy/result.
3. Inject Pakki blind-test error frames (GT combat) into train.
4. Oversample hard combat copies into train.
"""
from __future__ import annotations

import json
import random
import shutil
from collections import Counter, defaultdict
from pathlib import Path

import cv2
from lsc.analyzer.valorant_frame_classifier import ValorantFrameClassifier

ANN = Path.home() / "LSC" / "datasets" / "valorant_phase" / "annotate"
DATA = Path.home() / "LSC" / "datasets" / "valorant_phase"
BLIND = Path.home() / "LSC" / "datasets" / "valorant_phase" / "blind_pakki"
VAL_RATIO = 0.18
SEED = 42
HARD_OVERSAMPLE = 3  # extra copies of each hard combat frame in train


def _link(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest.unlink()
    try:
        dest.hardlink_to(src)
    except OSError:
        shutil.copy2(src, dest)


def main() -> None:
    queue = json.loads((ANN / "queue.json").read_text(encoding="utf-8"))
    labels = json.loads((ANN / "labels.json").read_text(encoding="utf-8"))
    preds = json.loads((BLIND / "predictions.json").read_text(encoding="utf-8"))
    gt = json.loads((BLIND / "ground_truth.json").read_text(encoding="utf-8"))

    by_label: dict[str, list[dict]] = defaultdict(list)
    for item in queue:
        lab = labels[item["id"]]["label"]
        by_label[lab].append(item)

    rng = random.Random(SEED)
    assignments: list[tuple[Path, str, str, str]] = []  # src, label, split, name
    for lab, items in by_label.items():
        items = list(items)
        rng.shuffle(items)
        n_val = (
            max(1, int(round(len(items) * VAL_RATIO)))
            if len(items) >= 6
            else max(1, len(items) // 5)
        )
        n_val = min(n_val, len(items) - 1) if len(items) > 1 else 0
        for i, item in enumerate(items):
            split = "val" if i < n_val else "train"
            src = Path(item["abs_path"])
            if not src.is_file():
                src = ANN / item["rel_path"]
            if not src.is_file():
                continue
            ts_ms = int(round(float(item["timestamp_sec"]) * 1000))
            name = f"{item['video_id']}_{ts_ms}.jpg"
            assignments.append((src, lab, split, name))

    for split in ("train", "val", "test"):
        d = DATA / split
        if d.exists():
            shutil.rmtree(d)

    counts: Counter[str] = Counter()
    for src, lab, split, name in assignments:
        dest = DATA / split / lab / name
        _link(src, dest)
        counts[f"{split}/{lab}"] += 1

    # Hard-mine with current model on annotate combat samples (train only sources)
    clf = ValorantFrameClassifier()
    clf.load()
    hard_paths: list[Path] = []
    combat_train = list((DATA / "train" / "combat").glob("*.jpg"))
    for path in combat_train:
        bgr = cv2.imread(str(path))
        if bgr is None:
            continue
        probs = clf.predict_batch([bgr])[0]
        pred_i = int(probs.argmax())
        pred = ("non_game", "buy", "combat", "result", "replay")[pred_i]
        if pred in ("buy", "result") and float(probs[pred_i]) >= 0.45:
            hard_paths.append(path)

    # Pakki blind errors (GT combat) — always train
    pakki_hard = 0
    for row in preds:
        idx = str(row["idx"])
        if gt.get(idx) != "combat":
            continue
        if row["pred"] == "combat":
            continue
        src = Path(row["path"])
        if not src.is_file():
            src = BLIND / row["frame"]
        if not src.is_file():
            continue
        name = f"pakki_hard_{int(row['idx']):06d}.jpg"
        dest = DATA / "train" / "combat" / name
        _link(src, dest)
        hard_paths.append(dest)
        pakki_hard += 1
        counts["train/combat"] += 1

    # Also inject true Pakki buy/result (few) so HUD style not only combat hard
    rare_added = 0
    for row in preds:
        idx = str(row["idx"])
        g = gt.get(idx)
        if g not in ("buy", "result"):
            continue
        src = Path(row["path"])
        if not src.is_file():
            src = BLIND / row["frame"]
        if not src.is_file():
            continue
        name = f"pakki_{g}_{int(row['idx']):06d}.jpg"
        dest = DATA / "train" / g / name
        _link(src, dest)
        rare_added += 1
        counts[f"train/{g}"] += 1

    # Oversample hard combat into train
    extra = 0
    for path in hard_paths:
        for k in range(HARD_OVERSAMPLE):
            dest = DATA / "train" / "combat" / f"hardx{k}_{path.name}"
            _link(path, dest)
            extra += 1
            counts["train/combat"] += 1

    print(f"base organized; hard_mine_annotate≈{len(hard_paths) - pakki_hard}")
    print(f"pakki_hard_combat={pakki_hard} pakki_rare={rare_added} oversample_extra={extra}")
    print(f"provider={clf.provider}")
    for k in sorted(counts):
        print(f"  {k}: {counts[k]}")
    print(f"total={sum(counts.values())}")


if __name__ == "__main__":
    main()
