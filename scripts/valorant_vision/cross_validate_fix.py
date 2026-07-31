#!/usr/bin/env python3
"""用训练好的模型对数据集做交叉推理，找出预测与标签不一致的帧（疑似标注错误）。

高置信度不一致帧直接自动修正，低置信度帧送入标注队列供人工复核。

用法:
  python scripts/valorant_vision/cross_validate_fix.py [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from lsc.analyzer.valorant_frame_classifier import ValorantFrameClassifier

DATASET_ROOT = Path.home() / "LSC" / "datasets" / "valorant_phase"
ANNOTATE_ROOT = DATASET_ROOT / "annotate"
QUEUE_PATH = ANNOTATE_ROOT / "queue.json"
MODEL_DIR = Path.home() / "LSC" / "models" / "valorant_phase_v1"

CLASS_NAMES = ("non_game", "buy", "combat", "result", "replay")
LABELS = CLASS_NAMES
SPLITS = ("train", "val")

# 高置信度阈值：模型预测概率超过此值且与标签不一致 → 自动修正
AUTO_FIX_THRESHOLD = 0.90
# 低置信度阈值：超过此值但不满足自动修正 → 送标注队列
REVIEW_THRESHOLD = 0.65
BATCH_SIZE = 32


def main() -> int:
    parser = argparse.ArgumentParser(description="交叉推理找标注错误")
    parser.add_argument("--dry-run", action="store_true", help="只统计，不修改")
    parser.add_argument("--data-dir", type=Path, default=DATASET_ROOT)
    parser.add_argument("--model-dir", type=Path, default=MODEL_DIR)
    parser.add_argument(
        "--auto-fix-threshold", type=float, default=AUTO_FIX_THRESHOLD,
        help=f"自动修正置信度阈值 (默认 {AUTO_FIX_THRESHOLD})",
    )
    parser.add_argument(
        "--review-threshold", type=float, default=REVIEW_THRESHOLD,
        help=f"送审置信度阈值 (默认 {REVIEW_THRESHOLD})",
    )
    args = parser.parse_args()

    data_dir = args.data_dir.expanduser().resolve()
    model_dir = args.model_dir.expanduser().resolve()

    if not data_dir.is_dir():
        print(f"错误: 数据集目录不存在: {data_dir}", file=sys.stderr)
        return 2

    # 加载模型
    clf = ValorantFrameClassifier(model_dir=model_dir)
    clf.load()
    print(f"模型已加载: {model_dir} (provider={clf.provider})")

    # 收集所有帧
    frames_info: list[dict] = []  # {path, split, label, video_id, ts_sec}
    for split in SPLITS:
        split_dir = data_dir / split
        if not split_dir.is_dir():
            continue
        for label in LABELS:
            label_dir = split_dir / label
            if not label_dir.is_dir():
                continue
            for img_path in sorted(label_dir.iterdir()):
                if img_path.suffix.lower() not in (".jpg", ".jpeg", ".png"):
                    continue
                # 解析 video_id 和 timestamp
                stem = img_path.stem
                parts = stem.rsplit("_", 1)
                if len(parts) == 2 and parts[1].isdigit():
                    video_id = parts[0]
                    ts_sec = int(parts[1]) / 1000.0
                else:
                    video_id = stem
                    ts_sec = 0.0
                frames_info.append({
                    "path": img_path,
                    "split": split,
                    "label": label,
                    "video_id": video_id,
                    "ts_sec": ts_sec,
                })

    print(f"数据集共 {len(frames_info)} 帧，开始批量推理...")

    # 批量推理
    auto_fix: list[dict] = []  # 高置信度不一致 → 自动修正
    review: list[dict] = []    # 中置信度不一致 → 送审
    correct = 0
    errors = 0

    for offset in range(0, len(frames_info), BATCH_SIZE):
        batch = frames_info[offset:offset + BATCH_SIZE]
        batch_frames = []
        valid_indices = []
        for i, info in enumerate(batch):
            img = cv2.imread(str(info["path"]))
            if img is None:
                errors += 1
                continue
            batch_frames.append(img)
            valid_indices.append(i)

        if not batch_frames:
            continue

        probs = clf.predict_batch(batch_frames)
        for j, vi in enumerate(valid_indices):
            info = batch[vi]
            prob_row = probs[j]
            pred_idx = int(np.argmax(prob_row))
            pred_label = CLASS_NAMES[pred_idx]
            pred_conf = float(prob_row[pred_idx])
            true_label = info["label"]

            if pred_label == true_label:
                correct += 1
                continue

            # 不一致
            entry = {
                **info,
                "pred_label": pred_label,
                "pred_conf": pred_conf,
                "true_prob": float(prob_row[LABELS.index(true_label)]),
            }

            if pred_conf >= args.auto_fix_threshold:
                auto_fix.append(entry)
            elif pred_conf >= args.review_threshold:
                review.append(entry)

        if (offset // BATCH_SIZE) % 20 == 0:
            done = offset + len(batch)
            print(f"  进度: {done}/{len(frames_info)}", end="\r")

    print(f"\n推理完成: 正确 {correct}, 高置信不一致 {len(auto_fix)}, 中置信不一致 {len(review)}, 错误 {errors}")

    # 混淆矩阵
    print("\n=== 不一致分布 (真实→预测) ===")
    confusion: dict[str, int] = {}
    for entry in auto_fix + review:
        key = f"{entry['label']}→{entry['pred_label']}"
        confusion[key] = confusion.get(key, 0) + 1
    for key, count in sorted(confusion.items(), key=lambda x: -x[1]):
        print(f"  {key}: {count}")

    if args.dry_run:
        print(f"\n[dry-run] 自动修正 {len(auto_fix)} 帧, 送审 {len(review)} 帧 (未执行)")
        return 0

    # === 自动修正：移动文件到正确标签目录 ===
    fixed = 0
    for entry in auto_fix:
        src = entry["path"]
        dest_dir = data_dir / entry["split"] / entry["pred_label"]
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / src.name
        if dest.exists():
            stem = src.stem
            suffix = src.suffix
            counter = 1
            while dest.exists():
                dest = dest_dir / f"{stem}_dup{counter}{suffix}"
                counter += 1
        shutil.move(str(src), str(dest))
        fixed += 1

    print(f"\n已自动修正 {fixed} 帧 (置信度 ≥ {args.auto_fix_threshold})")

    # === 送审：注入标注队列 ===
    if review:
        if QUEUE_PATH.is_file():
            queue = json.loads(QUEUE_PATH.read_text(encoding="utf-8"))
        else:
            queue = []
        existing_ids = {item["id"] for item in queue}

        new_entries = []
        for entry in review:
            ts_ms_str = f"{int(entry['ts_sec'] * 1000):06d}"
            entry_id = f"{entry['video_id']}_{ts_ms_str}_xval"
            if entry_id in existing_ids:
                continue
            rel_path = f"dataset/{entry['split']}/{entry['label']}/{entry['path'].name}"
            new_entries.append({
                "id": entry_id,
                "rel_path": rel_path,
                "abs_path": str(entry["path"]),
                "video_id": entry["video_id"],
                "video_path": "",
                "timestamp_sec": entry["ts_sec"],
                "source_type": "broadcast" if "broadcast" in entry["video_id"] else "pov",
                "session_id": f"xval_{datetime.now():%Y%m%d}",
                "split": entry["split"],
                "label": None,
                "notes": f"当前={entry['label']} 模型={entry['pred_label']}({entry['pred_conf']:.2f})",
                "priority": "review_xval",
                "current_label": entry["label"],
                "suggested_label": entry["pred_label"],
                "reason": f"模型预测{entry['pred_label']}(conf={entry['pred_conf']:.2f}) vs 标签{entry['label']}(prob={entry['true_prob']:.2f})",
            })
            existing_ids.add(entry_id)

        if new_entries:
            # 备份
            backup_dir = ANNOTATE_ROOT / "backups"
            backup_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            if QUEUE_PATH.is_file():
                shutil.copy2(QUEUE_PATH, backup_dir / f"queue_{stamp}.json")
            updated_queue = new_entries + queue
            QUEUE_PATH.write_text(json.dumps(updated_queue, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"已送审 {len(new_entries)} 帧至标注队列 (总计 {len(updated_queue)} 条)")

    # 最终统计
    print("\n=== 修正后数据集分布 ===")
    for split in SPLITS:
        split_dir = data_dir / split
        if not split_dir.is_dir():
            continue
        parts = []
        for label in LABELS:
            label_dir = split_dir / label
            count = len(list(label_dir.iterdir())) if label_dir.is_dir() else 0
            parts.append(f"{label}={count}")
        print(f"  {split}: {', '.join(parts)}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
