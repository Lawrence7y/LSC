#!/usr/bin/env python3
"""从**任意** Valorant 赛事/直播回放录像里挖「回放标记 ROI」样本。

为什么需要它（2026-09-10 的教训）
--------------------------------
标记支路在 test 上把 `replay` 召回从 0.344 拉到 0.969（见
`docs/reports/valorant-broadcast-b1-retrain-result-20260910.md`），但 `val` 上出现
**7 个误报、且全部来自同一个会话** `binggan_20260731_181220` —— 根因是训练集只有
**约 4 个广播会话**，模型（无论主模型还是标记支路）都会去抓该会话 UI 的特有线索
而不是"REPLAY 字形"本身。解法就是**加会话**。

本脚本把"加素材"这件事做成纯自动流程：录像 → 抽帧 → 逐 ROI 裁剪 → OCR 判标 →
直接产出可与 `build_marker_roi_dataset.py` 产物合并的训练样本。**不需要人工标注**
（OCR 在标记上实测 72/72、置信度 ≥0.99，两种风格都能读）。

用法
----
    python scripts/valorant_vision/mine_marker_roi_from_videos.py \
        --videos "D:/desktop/新建文件夹 (2)/新建文件夹/EDG夺冠回顾" \
        --out-dir D:/lsc_models/broadcast_marker_roi_mined \
        --interval 2.0 --workers 4

产物目录结构与 `build_marker_roi_dataset.py` 一致（`<split>/<label>/*.jpg` +
`manifest_marker_roi.jsonl` + `manifest_marker_roi_nodistill.jsonl`），
`split` 由 `--split`（默认 `train`）指定；合并进训练集时把 `frame_path` 追加进清单即可。
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from build_marker_roi_dataset import (  # noqa: E402
    NEGATIVE_LABEL,
    POSITIVE_LABEL,
    ROIS,
    _roi_name,
    crop_roi,
    detect_marker_text,
    upscale,
)

VIDEO_SUFFIXES = (".mp4", ".mkv", ".flv", ".ts", ".mov")


def collect_videos(targets: list[Path], exclude: list[str] | None = None) -> list[Path]:
    """展开目录/文件列表，按名字排序（保证可复现）。

    ``exclude`` 是按**文件名子串**排除——用于挡住**数据泄漏**：`test/replay` 的
    帧就来自 `12-00-36` / `02-06-12` 这两个录像，把它们挖进训练集会让 test 指标虚高。
    """
    videos: list[Path] = []
    for target in targets:
        path = target.expanduser()
        if path.is_dir():
            videos.extend(
                p for p in sorted(path.rglob("*")) if p.suffix.lower() in VIDEO_SUFFIXES
            )
        elif path.is_file() and path.suffix.lower() in VIDEO_SUFFIXES:
            videos.append(path)
    for pattern in exclude or []:
        videos = [v for v in videos if pattern not in v.name]
    return videos


def probe_duration(video: Path, ffprobe: str = "ffprobe") -> float:
    try:
        out = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(video)],
            capture_output=True, text=True, timeout=60, check=False,
        )
        return float((out.stdout or "0").strip() or 0.0)
    except (OSError, ValueError, subprocess.SubprocessError):
        return 0.0


def extract_frames(video: Path, out_dir: Path, interval: float, ffmpeg: str = "ffmpeg") -> list[tuple[Path, float]]:
    """按固定间隔抽帧，返回 [(帧文件, 秒)]。时间戳由抽帧序号 × 间隔推得。

    `fps=1/interval` 让 ffmpeg 直接输出等间隔帧，省掉自己做 seek（远快于逐帧 seek）。
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    pattern = out_dir / "f_%06d.jpg"
    cmd = [
        ffmpeg, "-y", "-loglevel", "error", "-i", str(video),
        "-vf", f"fps=1/{interval}", "-q:v", "3", str(pattern),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=3600, check=False)
    if result.returncode != 0:
        print(f"!! ffmpeg 失败 ({video.name}): {result.stderr[-300:]}", file=sys.stderr)
        return []
    frames = sorted(out_dir.glob("f_*.jpg"))
    # ffmpeg 的 fps 滤镜从第 0 帧开始按 1/interval 计步，帧序 i 对应约 i*interval 秒
    return [(path, index * interval) for index, path in enumerate(frames)]


def _process(task: tuple) -> dict:
    """单帧：逐 ROI 裁剪 + OCR 判标 + 落盘（顶层函数，进程池要能 pickle）。"""
    frame_path, seconds, video_name, split, rois, out_dir_str = task
    from build_marker_roi_dataset import _read_image, _write_image

    out_dir = Path(out_dir_str)
    try:
        image = _read_image(Path(frame_path))
    except ValueError:
        return {"readable": False, "rows": []}
    rows: list[dict] = []
    for roi, box in rois.items():
        crop = crop_roi(image, box)
        if crop is None:
            continue
        text, score = detect_marker_text(crop)
        target = POSITIVE_LABEL if text else NEGATIVE_LABEL
        out_class = out_dir / split / target
        out_class.mkdir(parents=True, exist_ok=True)
        stem = f"{video_name}_{int(seconds):06d}s"
        destination = out_class / _roi_name(stem, roi)
        _write_image(destination, upscale(crop))
        rows.append(
            {
                "frame_path": str(destination),
                "label": target,
                "split": split,
                "source_frame": frame_path,
                "source_video": video_name,
                "source_seconds": seconds,
                "roi": roi,
                "marker_text": text,
                "marker_score": round(score, 6),
                "label_source": "ocr_per_crop",
                "notes": "从录像挖掘（mine_marker_roi_from_videos）",
            }
        )
    return {"readable": True, "rows": rows}


def mine(
    *,
    videos: list[Path],
    out_dir: Path,
    interval: float,
    split: str,
    rois: dict[str, tuple[float, float, float, float]],
    workers: int,
    keep_frames: Path | None,
) -> dict:
    report: dict = {"videos": [], "interval": interval, "split": split, "rois": list(rois)}
    all_rows: list[dict] = []
    scratch = Path(tempfile.mkdtemp(prefix="marker_mine_"))
    try:
        for video in videos:
            duration = probe_duration(video)
            frame_dir = scratch / video.stem
            frames = extract_frames(video, frame_dir, interval)
            tasks = [
                (str(path), seconds, video.stem, split, rois, str(out_dir))
                for path, seconds in frames
            ]
            entry = {"video": str(video), "duration_sec": round(duration, 1),
                     "frames": len(tasks), "crops": 0, "positives": 0}
            started = time.perf_counter()
            results: list[dict] = []
            if workers > 1 and tasks:
                from concurrent.futures import ProcessPoolExecutor

                with ProcessPoolExecutor(max_workers=workers) as pool:
                    results = list(pool.map(_process, tasks, chunksize=4))
            else:
                results = [_process(task) for task in tasks]
            for result in results:
                for row in result["rows"]:
                    all_rows.append(row)
                    entry["crops"] += 1
                    if row["label"] == POSITIVE_LABEL:
                        entry["positives"] += 1
            entry["elapsed_sec"] = round(time.perf_counter() - started, 1)
            report["videos"].append(entry)
            print(
                f"  {video.name}: {entry['frames']} 帧 → {entry['crops']} 裁剪，"
                f"命中标记 {entry['positives']}（{entry['elapsed_sec']}s）",
                file=sys.stderr, flush=True,
            )
            if keep_frames is None:
                shutil.rmtree(frame_dir, ignore_errors=True)
            else:
                target = keep_frames / video.stem
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists():
                    shutil.rmtree(target, ignore_errors=True)
                shutil.move(str(frame_dir), str(target))
    finally:
        if keep_frames is None:
            shutil.rmtree(scratch, ignore_errors=True)

    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = out_dir / "manifest_marker_roi.jsonl"
    # 与已有清单合并（幂等：按 frame_path 去重）
    existing: dict[str, dict] = {}
    if manifest.is_file():
        for line in manifest.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                existing[row["frame_path"]] = row
    for row in all_rows:
        existing[row["frame_path"]] = row
    merged = sorted(existing.values(), key=lambda r: r["frame_path"])
    manifest.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in merged),
        encoding="utf-8",
    )
    hard = out_dir / "manifest_marker_roi_nodistill.jsonl"
    hard.write_text(
        "".join(
            json.dumps(
                {
                    "frame_path": row["frame_path"],
                    "label": row["label"],
                    "hard_weight": 1.0,
                    "hard_distill_weight": 0.0,
                    "reason": "标记支路：教师整帧模型在 ROI 裁图上完全 OOD → 关闭蒸馏",
                },
                ensure_ascii=False,
            )
            + "\n"
            for row in merged
        ),
        encoding="utf-8",
    )
    report["total_crops"] = len(all_rows)
    report["total_positives"] = sum(1 for row in all_rows if row["label"] == POSITIVE_LABEL)
    report["merged_rows"] = len(merged)
    report["manifest"] = str(manifest)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--videos", nargs="+", type=Path, required=True,
                        help="录像文件或目录（目录会递归找 mp4/mkv/flv/ts/mov）")
    # --check 只做盘点，不需要输出目录
    parser.add_argument("--out-dir", type=Path, default=None)
    parser.add_argument("--interval", type=float, default=2.0, help="抽帧间隔（秒）")
    parser.add_argument("--split", default="train")
    parser.add_argument("--workers", type=int, default=4,
                        help="并行进程数。OCR 走 DirectML 时不是越多越好（实测 4 最优）")
    parser.add_argument("--keep-frames", type=Path, default=None,
                        help="保留抽出的原始帧（默认抽完即删，省磁盘）")
    parser.add_argument("--exclude", nargs="*", default=[],
                        help="按文件名子串排除录像（挡泄漏：test 帧的来源录像不能进 train）")
    parser.add_argument("--check", action="store_true", help="只列录像与时长，不抽帧")
    args = parser.parse_args(argv)

    videos = collect_videos(args.videos, args.exclude)
    if not videos:
        print("没有找到任何录像文件", file=sys.stderr)
        return 2
    if args.check:
        total = 0.0
        for video in videos:
            duration = probe_duration(video)
            total += duration
            print(f"  {video}  {duration:.0f}s")
        print(f"共 {len(videos)} 个，合计 {total/60:.1f} 分钟；"
              f"按 {args.interval}s 抽帧预计 {int(total/args.interval)} 帧")
        return 0
    if args.out_dir is None:
        print("--out-dir 必须提供", file=sys.stderr)
        return 2
    report = mine(
        videos=videos,
        out_dir=args.out_dir.expanduser().resolve(),
        interval=args.interval,
        split=args.split,
        rois=ROIS,
        workers=max(1, int(args.workers)),
        keep_frames=args.keep_frames,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
