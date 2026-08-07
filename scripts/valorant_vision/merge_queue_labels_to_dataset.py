#!/usr/bin/env python3
"""把 annotate 队列中的人工标注帧增量合并进训练数据集目录。

与 organize_from_annotate.py 不同：本脚本**不重建** train/val/test，
仅把 labels.json 中 annotator=human 的帧按队列项自带的 split 追加到
<DATA>/<split>/<label>/<video_id>_<ts_ms>.jpg，目标已存在则跳过（幂等）。

用法:
  python scripts/valorant_vision/merge_queue_labels_to_dataset.py [--dry-run]
"""
from __future__ import annotations

import argparse
import re
import shutil
from collections import Counter
from pathlib import Path

ANN = Path.home() / "LSC" / "datasets" / "valorant_phase" / "annotate"
DATA = Path.home() / "LSC" / "datasets" / "valorant_phase"
VALID_LABELS = {"non_game", "buy", "combat", "result", "replay"}
VALID_SPLITS = {"train", "val", "test"}


def _safe_video_id(video_id: str) -> str:
    value = re.sub(r"[\\/]", "_", str(video_id))
    value = value.replace("..", "_").strip(" .")
    return value or "unknown"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    import json

    queue = json.loads((ANN / "queue.json").read_text(encoding="utf-8"))
    labels = json.loads((ANN / "labels.json").read_text(encoding="utf-8"))

    added = Counter()
    skipped_exists = 0
    missing = 0
    for item in queue:
        lab = labels.get(item["id"])
        if not lab or lab.get("annotator") != "human":
            continue
        label = lab.get("label")
        if label not in VALID_LABELS:
            continue
        split = item.get("split") or "train"
        if split not in VALID_SPLITS:
            split = "train"
        src = Path(item.get("abs_path") or "")
        if not src.is_file():
            src = ANN / item.get("rel_path", "")
        if not src.is_file():
            missing += 1
            continue
        ts_ms = int(round(float(item.get("timestamp_sec", 0.0)) * 1000))
        dest = DATA / split / label / f"{_safe_video_id(item['video_id'])}_{ts_ms}.jpg"
        if not dest.resolve().is_relative_to(DATA.resolve()):
            continue
        if dest.exists():
            skipped_exists += 1
            continue
        if not args.dry_run:
            dest.parent.mkdir(parents=True, exist_ok=True)
            try:
                dest.hardlink_to(src)
            except OSError:
                shutil.copy2(src, dest)
        added[f"{split}/{label}"] += 1

    print(f"新增 {sum(added.values())} 帧（已存在跳过 {skipped_exists}，源缺失 {missing}）")
    for key in sorted(added):
        print(f"  {key}: {added[key]}")
    if args.dry_run:
        print("(dry-run 未写入)")


if __name__ == "__main__":
    main()
