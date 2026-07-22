#!/usr/bin/env python3
"""Organize annotated JPEGs into stratified train/val for train_export.py."""
from __future__ import annotations

import json
import random
import shutil
from collections import Counter, defaultdict
from pathlib import Path

ANN = Path.home() / "LSC" / "datasets" / "valorant_phase" / "annotate"
DATA = Path.home() / "LSC" / "datasets" / "valorant_phase"
VAL_RATIO = 0.18
SEED = 42


def main() -> None:
    queue = json.loads((ANN / "queue.json").read_text(encoding="utf-8"))
    labels = json.loads((ANN / "labels.json").read_text(encoding="utf-8"))

    by_label: dict[str, list[dict]] = defaultdict(list)
    for item in queue:
        lab = labels[item["id"]]["label"]
        by_label[lab].append(item)

    rng = random.Random(SEED)
    assignments: list[tuple[dict, str, str]] = []  # item, label, split
    for lab, items in by_label.items():
        items = list(items)
        rng.shuffle(items)
        n_val = max(1, int(round(len(items) * VAL_RATIO))) if len(items) >= 6 else max(1, len(items) // 5)
        n_val = min(n_val, len(items) - 1) if len(items) > 1 else 0
        for i, item in enumerate(items):
            split = "val" if i < n_val else "train"
            assignments.append((item, lab, split))

    for split in ("train", "val", "test"):
        d = DATA / split
        if d.exists():
            shutil.rmtree(d)

    counts: Counter[str] = Counter()
    missing = 0
    for item, lab, split in assignments:
        src = Path(item["abs_path"])
        if not src.is_file():
            src = ANN / item["rel_path"]
        if not src.is_file():
            missing += 1
            continue
        ts_ms = int(round(float(item["timestamp_sec"]) * 1000))
        dest = DATA / split / lab / f"{item['video_id']}_{ts_ms}.jpg"
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            dest.unlink()
        try:
            dest.hardlink_to(src)
        except OSError:
            shutil.copy2(src, dest)
        counts[f"{split}/{lab}"] += 1

    print(f"organized {sum(counts.values())} missing {missing} (stratified val_ratio={VAL_RATIO})")
    for k in sorted(counts):
        print(f"  {k}: {counts[k]}")


if __name__ == "__main__":
    main()
