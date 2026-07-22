#!/usr/bin/env python3
"""Rebuild train/val with annotate + Codex broadcast (train/val only).

- Codex ``test`` split goes to data-dir/test only (blind holdout).
- Hard-mine remaining error types (replay->combat, non_game->result, result->combat)
  and oversample into train.
"""
from __future__ import annotations

import json
import random
import shutil
from collections import Counter, defaultdict
from pathlib import Path

import cv2
import numpy as np

from lsc.analyzer.valorant_frame_classifier import ValorantFrameClassifier, _CLASS_NAMES

ANN = Path.home() / "LSC" / "datasets" / "valorant_phase" / "annotate"
DATA = Path.home() / "LSC" / "datasets" / "valorant_phase"
CODEX = Path(
    r"D:\Project\直播切片多人\.worktrees\valorant-frame-labeling-pilot"
    r"\datasets\valorant_phase\annotations\new_broadcast_20260721"
)
BLIND = Path.home() / "LSC" / "datasets" / "valorant_phase" / "blind_pakki"
VAL_RATIO = 0.18
SEED = 42
HARD_OVERSAMPLE = 4
RARE_OVERSAMPLE = {"replay": 5, "result": 4, "buy": 1}


def imread(p: Path):
    data = np.fromfile(str(p), dtype=np.uint8)
    img = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if img is None:
        img = cv2.imread(str(p))
    return img


def _link(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest.unlink()
    try:
        dest.hardlink_to(src)
    except OSError:
        shutil.copy2(src, dest)


def main() -> None:
    rng = random.Random(SEED)
    counts: Counter[str] = Counter()

    for split in ("train", "val", "test"):
        d = DATA / split
        if d.exists():
            shutil.rmtree(d)

    # --- 1) Annotate POV (exclude unlabeled dianjing/hanghang) ---
    queue = json.loads((ANN / "queue.json").read_text(encoding="utf-8"))
    labels = json.loads((ANN / "labels.json").read_text(encoding="utf-8"))
    by_label: dict[str, list] = defaultdict(list)
    for item in queue:
        if item["id"] not in labels:
            continue
        rel = item.get("rel_path", "").replace("\\", "/")
        if rel.startswith("pov_dianjing/") or rel.startswith("pov_hanghang/"):
            continue
        by_label[labels[item["id"]]["label"]].append(item)

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
            name = f"ann_{item['video_id']}_{ts_ms}.jpg"
            dest = DATA / split / lab / name
            _link(src, dest)
            counts[f"{split}/{lab}"] += 1

    # --- 2) Codex broadcast: honor its train/val/test splits ---
    cq = json.loads((CODEX / "queue.json").read_text(encoding="utf-8"))
    clabels = json.loads((CODEX / "labels.json").read_text(encoding="utf-8"))
    codex_train_items = []
    for item in cq:
        lab = clabels[item["id"]]["label"]
        split = item.get("split") or "train"
        if split not in ("train", "val", "test"):
            split = "train"
        src = CODEX / item["rel_path"]
        if not src.is_file():
            continue
        ts_ms = int(round(float(item["timestamp_sec"]) * 1000))
        name = f"bc_{item['video_id']}_{ts_ms}.jpg"
        dest = DATA / split / lab / name
        _link(src, dest)
        counts[f"{split}/{lab}"] += 1
        if split == "train":
            codex_train_items.append((src, lab, name, item["id"]))

    # --- 3) Pakki rare/hard (keep prior gains; still not in Codex blind) ---
    pakki_preds = BLIND / "predictions.json"
    pakki_gt = BLIND / "ground_truth.json"
    if pakki_preds.is_file() and pakki_gt.is_file():
        preds = json.loads(pakki_preds.read_text(encoding="utf-8"))
        gt = json.loads(pakki_gt.read_text(encoding="utf-8"))
        for row in preds:
            idx = str(row["idx"])
            g = gt.get(idx)
            if g not in ("buy", "result", "combat", "replay", "non_game"):
                continue
            # inject all pakki into train (small); combat only if misclassified
            if g == "combat" and row.get("pred") == "combat":
                continue
            src = Path(row.get("path") or "")
            if not src.is_file():
                src = BLIND / row["frame"]
            if not src.is_file():
                continue
            name = f"pakki_{g}_{int(row['idx']):06d}.jpg"
            _link(src, DATA / "train" / g / name)
            counts[f"train/{g}"] += 1

    # --- 4) Hard-mine on Codex train frames with current model ---
    clf = ValorantFrameClassifier()
    clf.load()
    hard: list[tuple[Path, str, str]] = []  # src, label, tag
    for src, lab, name, _iid in codex_train_items:
        img = imread(src)
        if img is None:
            continue
        probs = clf.predict_batch([img])[0]
        pred_i = int(probs.argmax())
        pred = _CLASS_NAMES[pred_i]
        conf = float(probs[pred_i])
        if pred == lab or conf < 0.55:
            continue
        # focus on known weak pairs
        pair = (lab, pred)
        if pair in {
            ("replay", "combat"),
            ("replay", "non_game"),
            ("non_game", "result"),
            ("result", "combat"),
            ("buy", "combat"),
            ("buy", "non_game"),
        }:
            hard.append((src, lab, f"hm_{lab}_as_{pred}_{name}"))

    for src, lab, tag in hard:
        for k in range(HARD_OVERSAMPLE):
            dest = DATA / "train" / lab / f"hardx{k}_{tag}"
            _link(src, dest)
            counts[f"train/{lab}"] += 1

    # --- 5) Rare-class oversample from train folders ---
    rare_extra = 0
    for lab, mult in RARE_OVERSAMPLE.items():
        paths = [p for p in (DATA / "train" / lab).glob("*.jpg") if not p.name.startswith("hardx")]
        for path in paths:
            for k in range(mult):
                dest = DATA / "train" / lab / f"rarex{k}_{path.name}"
                _link(path, dest)
                rare_extra += 1
                counts[f"train/{lab}"] += 1

    print(f"provider={clf.provider}")
    print(f"hard_mined={len(hard)} hard_extra={len(hard)*HARD_OVERSAMPLE} rare_extra={rare_extra}")
    for k in sorted(counts):
        print(f"  {k}: {counts[k]}")
    print(f"total={sum(counts.values())}")
    # write summary
    (DATA / "dataset_build_summary.json").write_text(
        json.dumps(
            {
                "counts": dict(counts),
                "hard_mined": len(hard),
                "provider": clf.provider,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
