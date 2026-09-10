#!/usr/bin/env python3
"""构建「回放标记 ROI」数据集：把两种风格的标记区按**原生分辨率**裁出、缩放到 224×224。

为什么需要它
------------
广播档 5 分类模型吃的是**整帧压缩到 224×224**，而回放标记在这个尺度下只有
**≈20×8 px**（右上角风格）/ **≈21×14 px**（右下角风格），整帧里仅占 **8%** 的边缘能量
（实测 18.86 vs 17.37）→ 模型只能改抓画面内容。2026-09-10 实跑证明：直接纠正
标签重训会让 `combat` 召回从 0.9490 塌到 0.7937（见
`docs/reports/valorant-broadcast-b1-retrain-result-20260910.md`）。

本脚本把标记区**单独裁出来放大**，作为模型的一条**独立输入支路**——标记从
"整帧 8% 的边缘能量"变成"整幅输入的 100%"。

标签口径：**逐裁剪判标（OCR 当离线标注员），不是按帧标签**
------------------------------------------------------------
⚠️ 这里踩过一个坑，别再走回去：一开始按"帧标签"给裁剪打标（replay 帧的两个角
**都**标成 `replay`），但实测 `train/replay` 里约一半帧的标记在右上角、一半在右下角
（120 帧抽样：右上 101 / 右下 17 / 居中 2）——于是**约一半正样本里根本没有标记**，
模型学到的是噪声（val replay F1 只有 0.13）。

正确做法：对**每一路裁剪**单独 OCR，读到 `REPLAY`/`REFLASH` 才算正样本。
OCR 在标记上的表现已实测可复现（72/72 帧、置信度 ≥0.99、两种风格都能读），
适合当**离线**标注员——推理时不需要 OCR，支路是纯 ONNX。

标签语义：裁剪里**有标记** → `replay`；**没有** → `non_game`。
`non_game` 只是"无标记"的占位类名（角落裁图推断不出原帧类别，按原帧类别打标
会注入纯噪声梯度），借用它是为了复用同一份 ONNX 契约（五类 `class_names` 不变）。

用法
----
    python scripts/valorant_vision/build_marker_roi_dataset.py --check      # 抽样估计，不落盘
    python scripts/valorant_vision/build_marker_roi_dataset.py \
        --data-dir datasets/valorant_phase_broadcast \
        --out-dir  D:/lsc_models/broadcast_marker_roi
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

CLASSES = ("non_game", "buy", "combat", "result", "replay")
POSITIVE_LABEL = "replay"
NEGATIVE_LABEL = "non_game"
CROP_SIZE = 224
OCR_SCALE = 2
# 判为"标记"的关键词（操作者约定：回放都会有 REPLAY 字样）
MARKER_KEYWORDS = ("REPLAY", "REFLASH")

# 回放标记的**四个互斥位置**（实测 200 帧 train/replay 抽样：同帧不会共存）。
# 归一化 (x, y, w, h)，都留了边：
#   右上角       x[0.88,0.97] y[0.02,0.06] 172×39px → 126/200 = 63%
#   顶部偏左小字 x[0.29,0.32] y[0.01,0.02]  59×15px →  62/200 = 31%  ← 最容易漏
#   右下角       x[0.85,0.94] y[0.90,0.97] 180×67px →   3/200 = 1.5%（train）
#                ↑ 但 test/replay 有 31/32 是这一种 → 训练正样本极稀（见下）
#   居中大字     x[0.23,0.76] y[0.38,0.63] 大字    →   6/200 =  3%
# 前三个框的覆盖率 = 95.5%（191/200）；居中大字不做 ROI —— 它本身很大
# （原字高 269–416px），整帧压到 224×224 后仍可见，整帧分支自己就能处理。
#
# ⚠️ 风格/position 与 split 的分布**极不均衡**：train 里几乎全是右上角与顶部偏左，
# 而 test/replay 几乎全是右下角。所以右下角那一路的正样本会很少（实测 17 个），
# 必须单独测它的召回，不能只看整体。
ROIS: dict[str, tuple[float, float, float, float]] = {
    "top_right": (0.80, 0.00, 0.20, 0.10),
    "top_center": (0.36, 0.00, 0.24, 0.11),
    "top_left_small": (0.20, 0.00, 0.16, 0.06),
    "bottom_right": (0.78, 0.84, 0.22, 0.16),
}
# 实测到的**四种**互斥标记位置（归一化框，含留边）+ 第五种不做 ROI：
#   右上角       x[0.878,0.968] y[0.020,0.056] 172×39px  ← 训练/验证集主流
#   顶部居中     x[0.46,0.54]   y[0.01,0.07]   150×60px  ← 2026 进化者杯等赛事流
#   顶部偏左小字 x[0.292,0.323] y[0.006,0.020]  59×15px  ← 最小的一种，最容易被漏
#   右下角       x[0.848,0.942] y[0.905,0.967] 180×67px  ← 真实录像（12-00-36 等）
#   居中大字     x[0.23,0.76]   y[0.38,0.63]   大字      ← 不做 ROI：原字高 269–416px，
#       整帧压到 224×224 后仍可见，整帧分支自己就能处理。
#
# ⚠️ 两个必须记住的事实：
# 1) **帧分辨率不统一**：本数据集有 1920×1080 与 640×360 两档（同宽高比 1.78，
#    是 3× 缩放关系）→ 归一化 ROI 成立，但 360p 下"顶部偏左小字"只有约 20×5px，
#    裁出来放大也读不出，属**固有不可标**（不是脚本 bug）。
# 2) 位置是**会新增的**：2026-09-11 实测 2026 进化者杯的标记落在"顶部居中"，
#    此前只用 右上角+右下角 两个框时，该整段素材命中 **0**（1200 个裁剪全负），
#    所以新增素材后**必须复核每个来源的命中率**，0 就是"框没覆盖到"的信号。
# 3) `top_left_small` 目前是**惰性 ROI**：它在数据集与挖掘素材里的正样本实测都是 **0**
#    （逐裁剪 OCR 读不出该字号的字形）——留着只多一路推理开销、不产生检出。
#    修法：标注口径换成"整帧 OCR 取框 + 按几何归属到 ROI"（整帧 2× 下该字置信度 ≥0.99），
#    但要重跑 数据集+挖掘+训练，故暂留并在此留档。
DEFAULT_DATA_DIR = _ROOT / "datasets/valorant_phase_broadcast"
DEFAULT_OUT_DIR = Path("D:/lsc_models/broadcast_marker_roi")


def _read_image(path: Path):
    import cv2
    import numpy as np

    image = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"failed to read image: {path}")
    return image


def _write_image(path: Path, image) -> None:
    """中文路径安全写盘 + 写后断言（`cv2.imwrite` 会**静默失败**，踩过）。"""
    import cv2

    ok, buffer = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
    if not ok:
        raise RuntimeError(f"encode failed: {path}")
    buffer.tofile(str(path))
    if not path.is_file() or path.stat().st_size == 0:
        raise RuntimeError(f"write failed (silent): {path}")


def crop_roi(image, box: tuple[float, float, float, float]):
    """按归一化 (x, y, w, h) 裁出原生分辨率裁剪；非法区域返回 None。"""
    import cv2

    height, width = image.shape[:2]
    x0, y0, bw, bh = box
    left, top = int(x0 * width), int(y0 * height)
    right, bottom = int((x0 + bw) * width), int((y0 + bh) * height)
    left, top = max(0, left), max(0, top)
    right, bottom = min(width, right), min(height, bottom)
    if right - left < 2 or bottom - top < 2:
        return None
    return image[top:bottom, left:right]


def upscale(crop, size: int = CROP_SIZE):
    """放大到模型输入尺寸。**必须用 CUBIC**：推理侧 `marker_roi_evidence()` 也用
    CUBIC，两边口径必须一致（用 INTER_AREA 放大会把字形退化成最近邻）。"""
    import cv2

    return cv2.resize(crop, (size, size), interpolation=cv2.INTER_CUBIC)


def detect_marker_text(crop) -> tuple[str, float]:
    """对一路裁剪做 OCR，返回 (命中的标记文本, 置信度)；无命中返回 ("", 0.0)。"""
    import cv2

    from lsc.analyzer.ocr_detector import _get_ocr

    ocr = _get_ocr()
    enlarged = cv2.resize(crop, None, fx=OCR_SCALE, fy=OCR_SCALE, interpolation=cv2.INTER_CUBIC)
    lines, _ = ocr(enlarged)
    best = ("", 0.0)
    for _box, text, score in (lines or []):
        upper = str(text).upper()
        if any(keyword in upper for keyword in MARKER_KEYWORDS):
            value = float(score)
            if value >= best[1]:
                best = (str(text), value)
    return best


def _roi_name(stem: str, roi: str) -> str:
    return f"{stem}__roi_{roi}.jpg"


def check(data_dir: Path, sample: int, seed: int) -> dict:
    """抽样估计：正负比、命中率、与帧标签的冲突数（不落盘）。"""
    import random

    report: dict = {"sample_per_class": sample, "rois": {name: {"positive": 0, "total": 0}
                                                        for name in ROIS}, "conflicts": []}
    rng = random.Random(seed)
    for label in CLASSES:
        directory = data_dir / "train" / label
        if not directory.is_dir():
            continue
        files = sorted(directory.glob("*.jpg"))
        if len(files) > sample:
            files = rng.sample(files, sample)
        for path in files:
            image = _read_image(path)
            for roi, box in ROIS.items():
                crop = crop_roi(image, box)
                if crop is None:
                    continue
                text, score = detect_marker_text(crop)
                report["rois"][roi]["total"] += 1
                if text:
                    report["rois"][roi]["positive"] += 1
                    if label != POSITIVE_LABEL:
                        report["conflicts"].append(
                            {"frame": str(path), "roi": roi, "frame_label": label,
                             "marker_text": text, "marker_score": round(score, 4)}
                        )
    for name, stats in report["rois"].items():
        total = max(stats["total"], 1)
        stats["positive_rate"] = round(stats["positive"] / total, 4)
    report["conflict_count"] = len(report["conflicts"])
    return report


def _process_frame(task: tuple) -> dict:
    """处理单帧：为每一路 ROI 裁图 + OCR 判标 + 落盘。**顶层函数**（进程池要能 pickle）。"""
    path_str, split, source_label, rois, out_dir_str = task
    path = Path(path_str)
    out_dir = Path(out_dir_str)
    rows: list[dict] = []
    try:
        image = _read_image(path)
    except ValueError:
        return {"path": path_str, "readable": False, "rows": []}
    conflicts: list[dict] = []
    for roi, box in rois.items():
        crop = crop_roi(image, box)
        if crop is None:
            continue
        text, score = detect_marker_text(crop)
        target = POSITIVE_LABEL if text else NEGATIVE_LABEL
        out_class = out_dir / split / target
        out_class.mkdir(parents=True, exist_ok=True)
        destination = out_class / _roi_name(path.stem, roi)
        _write_image(destination, upscale(crop))
        if text and source_label != POSITIVE_LABEL:
            conflicts.append(
                {"frame": str(path), "roi": roi, "frame_label": source_label,
                 "marker_text": text, "marker_score": round(score, 4)}
            )
        rows.append(
            {
                "frame_path": str(destination),
                "label": target,
                "split": split,
                "source_frame": str(path),
                "source_label": source_label,
                "roi": roi,
                "marker_text": text,
                "marker_score": round(score, 6),
                "label_source": "ocr_per_crop",
                "notes": "标记支路专用；融合只消费 replay 列",
            }
        )
    return {"path": path_str, "readable": True, "rows": rows, "conflicts": conflicts}


def build(
    *,
    data_dir: Path,
    out_dir: Path,
    rois: dict[str, tuple[float, float, float, float]],
    limit: int | None = None,
    progress_every: int = 200,
    workers: int = 1,
) -> dict:
    report: dict = {
        "rois": {k: list(v) for k, v in rois.items()},
        "label_source": "ocr_per_crop",
        "workers": workers,
        "splits": {},
        "unreadable": [],
        "frame_label_conflicts": [],
    }
    manifest_rows: list[dict] = []
    for split in ("train", "val", "test"):
        split_summary: dict = {"frames": 0, "crops": 0, "labels": {}, "roi_positive": {}}
        started = time.perf_counter()
        tasks: list[tuple] = []
        for label in CLASSES:
            directory = data_dir / split / label
            if not directory.is_dir():
                continue
            files = sorted(directory.glob("*.jpg"))
            if limit is not None:
                files = files[:limit]
            tasks.extend((str(path), split, label, rois, str(out_dir)) for path in files)
        if not tasks:
            continue

        def _consume(result: dict) -> None:
            if not result["readable"]:
                report["unreadable"].append(result["path"])
                return
            split_summary["frames"] += 1
            report["frame_label_conflicts"].extend(result.get("conflicts") or [])
            for row in result["rows"]:
                manifest_rows.append(row)
                split_summary["crops"] += 1
                split_summary["labels"][row["label"]] = (
                    split_summary["labels"].get(row["label"], 0) + 1
                )
                if row["marker_text"]:
                    split_summary["roi_positive"][row["roi"]] = (
                        split_summary["roi_positive"].get(row["roi"], 0) + 1
                    )
            if split_summary["frames"] % progress_every == 0:
                print(
                    f"  [{split}] {split_summary['frames']}/{len(tasks)} 帧 / "
                    f"{split_summary['crops']} 裁剪 …",
                    file=sys.stderr, flush=True,
                )

        if workers > 1:
            from concurrent.futures import ProcessPoolExecutor

            with ProcessPoolExecutor(max_workers=workers) as pool:
                for result in pool.map(_process_frame, tasks, chunksize=4):
                    _consume(result)
        else:
            for task in tasks:
                _consume(_process_frame(task))

        split_summary["elapsed_sec"] = round(time.perf_counter() - started, 1)
        report["splits"][split] = split_summary

    out_dir.mkdir(parents=True, exist_ok=True)
    uniform = out_dir / "manifest_marker_roi.jsonl"
    uniform.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in manifest_rows),
        encoding="utf-8",
    )
    # 教师模型在 ROI 裁图上是**彻底 OOD**（实测 p_replay=0.002）→ 蒸馏必须全关，
    # 否则 KL 项会把标记支路往教师那种"看不懂标记"的解上拉。
    hard = out_dir / "manifest_marker_roi_nodistill.jsonl"
    hard.write_text(
        "".join(
            json.dumps(
                {
                    "frame_path": row["frame_path"],
                    "label": row["label"],
                    "hard_weight": 1.0,
                    "hard_distill_weight": 0.0,
                    "reason": "标记支路：教师整帧模型在 ROI 裁图上完全 OOD（p_replay≈0.002）→ 关闭蒸馏",
                },
                ensure_ascii=False,
            )
            + "\n"
            for row in manifest_rows
        ),
        encoding="utf-8",
    )
    report["manifest"] = str(uniform)
    report["hard_manifest"] = str(hard)
    report["total_crops"] = len(manifest_rows)
    report["conflict_count"] = len(report["frame_label_conflicts"])
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--limit", type=int, default=None, help="每个类最多取多少帧（调试用）")
    parser.add_argument("--check", action="store_true", help="抽样估计正负比，不落盘")
    parser.add_argument("--workers", type=int, default=1,
                        help="并行进程数。OCR 走 DirectML（GPU）时**不是越多越好**："
                             "本机实测 workers=1 → 4.7/s、4 → 8.9/s、6 → 3.0/s（抢 GPU）")
    parser.add_argument("--check-sample", type=int, default=60)
    parser.add_argument("--seed", type=int, default=20260910)
    args = parser.parse_args(argv)

    data_dir = args.data_dir.expanduser().resolve()
    if args.check:
        report = check(data_dir, args.check_sample, args.seed)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    report = build(
        data_dir=data_dir,
        out_dir=args.out_dir.expanduser().resolve(),
        rois=ROIS,
        limit=args.limit,
        workers=max(1, int(args.workers)),
    )
    report["frame_label_conflicts"] = report["frame_label_conflicts"][:20]
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
