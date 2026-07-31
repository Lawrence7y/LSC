"""离线数据增强脚本：预生成多语言增强帧。

用于预览增强效果或生成独立的增强数据集。
在线增强（train_export.py 中的 MultiLanguageUIAugment）是主方案，
此脚本仅作为辅助工具。

用法:
    # 预览模式：仅增强 50 帧
    python scripts/valorant_vision/augment/aug_offline.py \\
        --input-dir ~/LSC/datasets/valorant_phase/train \\
        --output-dir ~/tmp/aug_preview \\
        --limit 50

    # 全量增强
    python scripts/valorant_vision/augment/aug_offline.py \\
        --input-dir ~/LSC/datasets/valorant_phase/train \\
        --output-dir ~/LSC/datasets/valorant_phase_aug/train \\
        --aug-per-frame 2
"""
from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

import numpy as np

try:
    from PIL import Image
    _HAS_PIL = True
except ImportError:
    _HAS_PIL = False

try:
    from .transforms import (
        MultiLanguageUIAugment,
        dilate_ui_regions,
        enhance_ui_texture,
        random_ui_erasing,
    )
except ImportError:
    # 直接运行脚本时的 fallback
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent))
    from transforms import (
        MultiLanguageUIAugment,
        dilate_ui_regions,
        enhance_ui_texture,
        random_ui_erasing,
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="离线增强 Valorant 训练帧，模拟多语言直播流视觉差异"
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        required=True,
        help="输入数据集目录（包含 <label>/ 子目录）",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="输出增强帧目录",
    )
    parser.add_argument(
        "--aug-per-frame",
        type=int,
        default=2,
        help="每帧生成的增强版本数（默认 2）",
    )
    parser.add_argument(
        "--strategy",
        choices=["dilate", "texture", "erasing", "all"],
        default="all",
        help="增强策略（默认 all）",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="限制处理的帧数（0=全部，用于预览）",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="随机种子（默认 42）",
    )
    return parser.parse_args()


def _augment_frame(frame: Image.Image, strategy: str) -> Image.Image:
    """对单帧应用指定策略的增强。"""
    if strategy == "dilate":
        kernel_size = random.choice([2, 3])
        iterations = random.choice([1, 2])
        return dilate_ui_regions(frame, kernel_size, iterations)
    elif strategy == "texture":
        strength = random.uniform(0.15, 0.4)
        return enhance_ui_texture(frame, strength)
    elif strategy == "erasing":
        n_boxes = random.randint(1, 3)
        return random_ui_erasing(frame, n_boxes)
    else:  # all
        augment = MultiLanguageUIAugment()
        return augment(frame)


def main() -> None:
    args = _parse_args()

    if not _HAS_PIL:
        print("错误: 需要 Pillow (pip install Pillow)", file=sys.stderr)
        sys.exit(1)

    random.seed(args.seed)
    np.random.seed(args.seed)

    input_dir = args.input_dir.expanduser().resolve()
    output_dir = args.output_dir.expanduser().resolve()

    if not input_dir.is_dir():
        print(f"错误: 输入目录不存在: {input_dir}", file=sys.stderr)
        sys.exit(1)

    # 收集所有帧
    frames: list[tuple[Path, str]] = []  # (path, label)
    for label_dir in sorted(input_dir.iterdir()):
        if not label_dir.is_dir():
            continue
        label = label_dir.name
        for frame_path in sorted(label_dir.glob("*.jpg")):
            frames.append((frame_path, label))

    if not frames:
        print(f"错误: 未找到任何帧: {input_dir}", file=sys.stderr)
        sys.exit(1)

    if args.limit > 0:
        frames = random.sample(frames, min(args.limit, len(frames)))

    print(f"输入: {len(frames)} 帧来自 {input_dir}")
    print(f"输出: {output_dir}")
    print(f"策略: {args.strategy}, 每帧增强: {args.aug_per_frame}")

    total_generated = 0
    for frame_path, label in frames:
        try:
            with Image.open(frame_path) as img:
                img = img.convert("RGB")
        except Exception as exc:
            print(f"  跳过 {frame_path.name}: {exc}")
            continue

        for aug_idx in range(args.aug_per_frame):
            aug_img = _augment_frame(img, strategy=args.strategy)

            # 保存：原始名 + 增强索引
            out_dir = output_dir / label
            out_dir.mkdir(parents=True, exist_ok=True)
            out_name = f"{frame_path.stem}_aug{aug_idx}.jpg"
            out_path = out_dir / out_name
            aug_img.save(out_path, quality=90)
            total_generated += 1

    print(f"\\n完成: 生成 {total_generated} 增强帧到 {output_dir}")


if __name__ == "__main__":
    main()
