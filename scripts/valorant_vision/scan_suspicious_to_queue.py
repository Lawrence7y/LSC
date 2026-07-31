#!/usr/bin/env python3
"""逐帧扫描数据集，将可疑画面（疑似含 Tab 数据栏）注入标注队列供人工复核。

使用比 reclassify_scoreboard_to_nongame.py 更宽松的阈值，
找出"可能有数据栏但未被严格检测器命中"的边界帧。

用法:
  python scripts/valorant_vision/scan_suspicious_to_queue.py [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

DATASET_ROOT = Path.home() / "LSC" / "datasets" / "valorant_phase"
ANNOTATE_ROOT = DATASET_ROOT / "annotate"
QUEUE_PATH = ANNOTATE_ROOT / "queue.json"
LABELS_PATH = ANNOTATE_ROOT / "labels.json"

LABELS = ("non_game", "buy", "combat", "result", "replay")
SPLITS = ("train", "val", "test")

# 从文件名解析 video_id 和 timestamp_ms
# 格式: [prefix_]<video_id>_<timestamp_ms>.jpg
# 例: ann_broadcast_yuezi_20260720_202557_232000.jpg
#     rarex4_ann_broadcast_yuezi_20260720_202557_620000.jpg
#     bc_broadcast_hanghang_20260721_478000.jpg
FILENAME_RE = re.compile(r"^(.+?)_(\d+)\.jpg$", re.IGNORECASE)

# 已知 augmentation 前缀
AUG_PREFIXES = ("rarex4_", "ann_")


def strip_aug_prefix(stem: str) -> str:
    """去除增强前缀，还原原始 video_id + timestamp。"""
    for prefix in AUG_PREFIXES:
        if stem.startswith(prefix):
            return stem[len(prefix):]
    return stem


def parse_filename(filename: str) -> tuple[str, float] | None:
    """从文件名解析 (video_id, timestamp_sec)。"""
    stem = Path(filename).stem
    clean = strip_aug_prefix(stem)
    m = FILENAME_RE.match(clean + ".jpg")
    if not m:
        return None
    video_id = m.group(1)
    ts_ms = int(m.group(2))
    return video_id, ts_ms / 1000.0


def infer_source_type(video_id: str) -> str:
    if "broadcast" in video_id:
        return "broadcast"
    return "pov"


def scoreboard_score(img_bgr: np.ndarray) -> tuple[float, str]:
    """返回 (可疑分数 0~1, 原因描述)。分数越高越像数据栏。

    核心逻辑：数据栏必须同时具备「绿+红大面积」「垂直分离」「宽表格」，
    缺少任何一项则分数很低，避免正常战斗画面误报。
    """
    h, w = img_bgr.shape[:2]
    hsv = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2HSV)

    y0, y1 = int(h * 0.15), int(h * 0.85)
    x0, x1 = int(w * 0.10), int(w * 0.90)
    roi = hsv[y0:y1, x0:x1]
    roi_h, roi_w = roi.shape[:2]
    total_px = roi_h * roi_w
    if total_px == 0:
        return 0.0, ""

    # 绿色 (守方)
    green_lo = np.array([35, 35, 35])
    green_hi = np.array([95, 255, 255])
    green_mask = cv2.inRange(roi, green_lo, green_hi)

    # 红色 (攻方)
    red_lo1 = np.array([0, 40, 40])
    red_hi1 = np.array([12, 255, 255])
    red_lo2 = np.array([155, 40, 40])
    red_hi2 = np.array([180, 255, 255])
    red_mask = cv2.inRange(roi, red_lo1, red_hi1) | cv2.inRange(roi, red_lo2, red_hi2)

    green_ratio = np.count_nonzero(green_mask) / total_px
    red_ratio = np.count_nonzero(red_mask) / total_px

    reasons: list[str] = []

    # === 门控：绿和红都必须达到最低阈值，否则直接返回 0 ===
    # 数据栏必然同时包含两队颜色，单一颜色不构成可疑
    GREEN_MIN = 0.015
    RED_MIN = 0.015
    if green_ratio < GREEN_MIN or red_ratio < RED_MIN:
        return 0.0, ""

    reasons.append(f"绿{green_ratio:.1%}")
    reasons.append(f"红{red_ratio:.1%}")

    # === 分项评分 (满分 1.0) ===
    score = 0.0

    # 1) 颜色占比 (权重 0.40)：绿红各 0.20
    g_color = min(green_ratio / 0.04, 1.0)  # 4% 以上满分
    r_color = min(red_ratio / 0.04, 1.0)
    score += 0.20 * g_color + 0.20 * r_color

    # 2) 垂直分离 (权重 0.35)：绿上红下
    green_ys = np.where(green_mask.any(axis=1))[0]
    red_ys = np.where(red_mask.any(axis=1))[0]
    if len(green_ys) > 0 and len(red_ys) > 0:
        green_cy = green_ys.mean()
        red_cy = red_ys.mean()
        sep = (red_cy - green_cy) / roi_h
        if sep > 0:
            sep_score = min(sep / 0.15, 1.0)  # 15% 分离度满分
            score += 0.35 * sep_score
            reasons.append(f"分离{sep:.2f}")
        else:
            # 绿色不在红色上方，不像数据栏
            return 0.0, ""
    else:
        return 0.0, ""

    # 3) 水平延展 (权重 0.25)：表格应横跨画面
    green_cols = np.where(green_mask.any(axis=0))[0]
    red_cols = np.where(red_mask.any(axis=0))[0]
    width_score = 0.0
    if len(green_cols) > 0:
        gw = (green_cols.max() - green_cols.min()) / roi_w
        width_score += min(gw / 0.5, 1.0) * 0.125
    if len(red_cols) > 0:
        rw = (red_cols.max() - red_cols.min()) / roi_w
        width_score += min(rw / 0.5, 1.0) * 0.125
    score += width_score
    if width_score > 0.1:
        reasons.append("宽表格")

    return score, "+".join(reasons)


def main() -> int:
    parser = argparse.ArgumentParser(description="扫描数据集可疑帧并注入标注队列")
    parser.add_argument("--dry-run", action="store_true", help="只统计，不写入")
    parser.add_argument("--data-dir", type=Path, default=DATASET_ROOT)
    parser.add_argument(
        "--threshold", type=float, default=0.5,
        help="可疑分数阈值 (默认 0.5)",
    )
    parser.add_argument(
        "--include-non-game", action="store_true",
        help="同时扫描 non_game 目录（验证是否有误移）",
    )
    args = parser.parse_args()

    data_dir = args.data_dir.expanduser().resolve()
    if not data_dir.is_dir():
        print(f"错误: 数据集目录不存在: {data_dir}", file=sys.stderr)
        return 2

    # 加载现有队列
    if QUEUE_PATH.is_file():
        queue: list[dict] = json.loads(QUEUE_PATH.read_text(encoding="utf-8"))
    else:
        queue = []
    existing_ids = {item["id"] for item in queue}

    scanned = 0
    suspicious_count = 0
    new_entries: list[dict] = []
    errors = 0

    scan_labels = list(LABELS) if args.include_non_game else [lab for lab in LABELS if lab != "non_game"]

    for split in SPLITS:
        split_dir = data_dir / split
        if not split_dir.is_dir():
            continue
        for label in scan_labels:
            label_dir = split_dir / label
            if not label_dir.is_dir():
                continue

            for img_path in sorted(label_dir.iterdir()):
                if img_path.suffix.lower() not in (".jpg", ".jpeg", ".png"):
                    continue
                scanned += 1
                try:
                    img = cv2.imread(str(img_path))
                    if img is None:
                        errors += 1
                        continue
                    score, reason = scoreboard_score(img)
                    if score < args.threshold:
                        continue

                    suspicious_count += 1

                    # 解析文件名
                    parsed = parse_filename(img_path.name)
                    if parsed:
                        video_id, ts_sec = parsed
                    else:
                        video_id = img_path.stem
                        ts_sec = 0.0

                    # 构造队列 ID（避免与已有冲突）
                    ts_ms_str = f"{int(ts_sec * 1000):06d}"
                    entry_id = f"{video_id}_{ts_ms_str}"
                    if entry_id in existing_ids:
                        entry_id = f"{entry_id}_rescan"
                    if entry_id in existing_ids:
                        continue  # 已存在，跳过

                    # rel_path 通过 junction: annotate/dataset -> dataset root
                    rel_path = f"dataset/{split}/{label}/{img_path.name}"

                    suggested = "non_game" if label != "non_game" else None
                    entry = {
                        "id": entry_id,
                        "rel_path": rel_path,
                        "abs_path": str(img_path),
                        "video_id": video_id,
                        "video_path": "",
                        "timestamp_sec": ts_sec,
                        "source_type": infer_source_type(video_id),
                        "session_id": f"scoreboard_rescan_{datetime.now():%Y%m%d}",
                        "split": split,
                        "label": None,
                        "notes": f"当前={label} 可疑分={score:.2f} ({reason})",
                        "priority": "review_scoreboard",
                        "current_label": label,
                        "suggested_label": suggested,
                        "reason": f"疑似数据栏: {reason} (score={score:.2f})",
                    }
                    new_entries.append(entry)
                    existing_ids.add(entry_id)

                    if args.dry_run:
                        print(f"[SUSPECT] {split}/{label}/{img_path.name} score={score:.2f} ({reason})")

                except Exception as exc:
                    print(f"[ERROR] {img_path}: {exc}", file=sys.stderr)
                    errors += 1

    print(f"\n扫描完成: {scanned} 帧, 可疑 {suspicious_count} 帧, 新增队列 {len(new_entries)} 条, 错误 {errors}")

    if args.dry_run:
        print("(dry-run 模式，未写入)")
        return 0

    if not new_entries:
        print("无新增可疑帧。")
        return 0

    # 备份原队列
    backup_dir = ANNOTATE_ROOT / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if QUEUE_PATH.is_file():
        import shutil
        shutil.copy2(QUEUE_PATH, backup_dir / f"queue_{stamp}.json")

    # 新条目放在队列前面（优先复核）
    updated_queue = new_entries + queue
    QUEUE_PATH.write_text(json.dumps(updated_queue, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已写入 {QUEUE_PATH} (总计 {len(updated_queue)} 条，新增 {len(new_entries)} 条在最前)")
    print("启动标注 UI: python scripts/valorant_vision/serve_label_ui.py")
    print("打开 http://127.0.0.1:8765/ 点击「只看未标」开始复核")
    return 0


if __name__ == "__main__":
    sys.exit(main())
