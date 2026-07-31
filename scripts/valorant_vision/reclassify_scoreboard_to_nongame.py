#!/usr/bin/env python3
"""检测数据集中包含 Tab 数据栏（记分板/Scoreboard）的帧，将其移至 non_game。

Tab 数据栏特征：画面中央出现大面积绿色（守方）+ 红色（攻方）半透明表格面板。

用法:
  python scripts/valorant_vision/reclassify_scoreboard_to_nongame.py [--dry-run]
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

import cv2
import numpy as np

DATASET_ROOT = Path.home() / "LSC" / "datasets" / "valorant_phase"
LABELS = ("non_game", "buy", "combat", "result", "replay")
SPLITS = ("train", "val", "test")


def has_scoreboard(img_bgr: np.ndarray) -> bool:
    """检测帧中是否包含 Tab 数据栏（大面积绿+红面板）。

    判据：
    1. 画面中央水平带（y: 20%-80%）中，绿色像素占比 > 3%
    2. 同区域红色像素占比 > 3%
    3. 绿色和红色在垂直方向上有分离（绿在上、红在下）
    """
    h, w = img_bgr.shape[:2]
    hsv = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2HSV)

    # 中央水平带
    y0, y1 = int(h * 0.15), int(h * 0.85)
    x0, x1 = int(w * 0.10), int(w * 0.90)
    roi = hsv[y0:y1, x0:x1]
    roi_h, roi_w = roi.shape[:2]
    total_px = roi_h * roi_w

    # 绿色范围 (Valorant 守方 ~ 青绿色)
    green_lo = np.array([35, 40, 40])
    green_hi = np.array([95, 255, 255])
    green_mask = cv2.inRange(roi, green_lo, green_hi)

    # 红色范围 (Valorant 攻方 ~ 红/品红)
    red_lo1 = np.array([0, 50, 50])
    red_hi1 = np.array([10, 255, 255])
    red_lo2 = np.array([160, 50, 50])
    red_hi2 = np.array([180, 255, 255])
    red_mask = cv2.inRange(roi, red_lo1, red_hi1) | cv2.inRange(roi, red_lo2, red_hi2)

    green_ratio = np.count_nonzero(green_mask) / total_px
    red_ratio = np.count_nonzero(red_mask) / total_px

    # 基本阈值：绿和红都要有一定占比
    if green_ratio < 0.025 or red_ratio < 0.025:
        return False

    # 垂直分离检测：绿色重心应在红色重心之上
    green_ys = np.where(green_mask.any(axis=1))[0]
    red_ys = np.where(red_mask.any(axis=1))[0]
    if len(green_ys) == 0 or len(red_ys) == 0:
        return False

    green_center_y = green_ys.mean()
    red_center_y = red_ys.mean()

    # 绿色重心应明显在红色上方（至少差 5% 的 ROI 高度）
    if green_center_y >= red_center_y - roi_h * 0.03:
        return False

    # 额外验证：绿色和红色区域应有足够的水平延展（表格宽度）
    green_cols = np.where(green_mask.any(axis=0))[0]
    red_cols = np.where(red_mask.any(axis=0))[0]
    if len(green_cols) == 0 or len(red_cols) == 0:
        return False

    green_width_ratio = (green_cols.max() - green_cols.min()) / roi_w
    red_width_ratio = (red_cols.max() - red_cols.min()) / roi_w

    return green_width_ratio >= 0.3 and red_width_ratio >= 0.3


def main() -> int:
    parser = argparse.ArgumentParser(description="将包含 Tab 数据栏的帧重分类为 non_game")
    parser.add_argument("--dry-run", action="store_true", help="只打印，不移动")
    parser.add_argument("--data-dir", type=Path, default=DATASET_ROOT, help="数据集根目录")
    args = parser.parse_args()

    data_dir = args.data_dir.expanduser().resolve()
    if not data_dir.is_dir():
        print(f"错误: 数据集目录不存在: {data_dir}", file=sys.stderr)
        return 2

    moved = 0
    scanned = 0
    errors = 0

    for split in SPLITS:
        split_dir = data_dir / split
        if not split_dir.is_dir():
            continue
        for label in LABELS:
            if label == "non_game":
                continue  # 已经是 non_game，跳过
            label_dir = split_dir / label
            if not label_dir.is_dir():
                continue
            dest_dir = split_dir / "non_game"
            dest_dir.mkdir(parents=True, exist_ok=True)

            for img_path in sorted(label_dir.iterdir()):
                if img_path.suffix.lower() not in (".jpg", ".jpeg", ".png"):
                    continue
                scanned += 1
                try:
                    img = cv2.imread(str(img_path))
                    if img is None:
                        errors += 1
                        continue
                    if has_scoreboard(img):
                        dest = dest_dir / img_path.name
                        # 避免文件名冲突
                        if dest.exists():
                            stem = img_path.stem
                            suffix = img_path.suffix
                            counter = 1
                            while dest.exists():
                                dest = dest_dir / f"{stem}_dup{counter}{suffix}"
                                counter += 1
                        if args.dry_run:
                            print(f"[DRY-RUN] {img_path.relative_to(data_dir)} -> non_game/")
                        else:
                            shutil.move(str(img_path), str(dest))
                            print(f"[MOVED] {split}/{label}/{img_path.name} -> {split}/non_game/")
                        moved += 1
                except Exception as exc:
                    print(f"[ERROR] {img_path}: {exc}", file=sys.stderr)
                    errors += 1

    print(f"\n完成: 扫描 {scanned} 帧, 检测到数据栏 {moved} 帧, 错误 {errors}")
    if args.dry_run:
        print("(dry-run 模式，未实际移动)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
