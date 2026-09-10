#!/usr/bin/env python3
"""广播档「回放识别」验收程序：在**任意录像或真实直播**上跑完整链路并出报告。

为什么需要它
------------
离线评测（`eval_source_dataset --mode broadcast_runtime`）只看数据集里的帧，
而"模型能不能在真实直播上认出回放"是另一回事。本程序把两者接上：

1. 取源：本地 mp4，或**直播/点播 URL**（走仓库自带的平台适配器，huya 等；
   直播会用 ffmpeg 录 `--capture-seconds` 秒，复用适配器给出的 Referer/UA —— 
   实测裸直链直连会被 CDN 以 5XX 拒绝）；
2. 固定间隔抽帧；
3. 主模型 + **回放标记支路**推理 → 回放证据与稳定标签；
4. **独立用 OCR 再查一遍标记**（与模型完全不同的通道）→ 两者逐帧对照；
5. 输出一致性/误报/漏报与逐标签分布，并按 `--min-agreement` 给 PASS/FAIL。

判读
----
`branch>=threshold` 与 `OCR 有 REPLAY 标记` 应逐帧一致：
- **误报**（支路说有、OCR 说没有）在真实直播上应为 0——支路只该在有标记时报警；
- **漏报**（OCR 说有、支路没到阈值）指向标记位置/字号不在 ROI 覆盖内。

用法
----
    # 本地录像
    python scripts/valorant_vision/verify_broadcast_replay.py \
        --input D:/valorant_vods/_live_huya_29701502.mp4 \
        --model-dir C:/lsc_models/broadcast_with_marker_merged_20260911 \
        --json docs/reports/live-verify-20260911.json

    # 真实直播（自动录 180 秒）
    python scripts/valorant_vision/verify_broadcast_replay.py \
        --input https://www.huya.com/29701502 --capture-seconds 180
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

CLASSES = ("non_game", "buy", "combat", "result", "replay")
REPLAY_INDEX = CLASSES.index("replay")
VIDEO_SUFFIXES = (".mp4", ".mkv", ".flv", ".ts", ".mov", ".avi")


def _read_image(path: Path):
    import cv2
    import numpy as np

    return cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)


def resolve_input(raw: str, *, capture_seconds: int, work_dir: Path) -> tuple[Path, dict]:
    """把输入统一成"本地视频文件"。URL 走仓库平台适配器，直播额外录一段。"""
    from lsc.platforms.registry import parse_stream

    if Path(raw).suffix.lower() in VIDEO_SUFFIXES and Path(raw).is_file():
        return Path(raw), {"kind": "file", "input": raw}

    info = parse_stream(raw)
    stream_url = str(getattr(info, "stream_url", "") or "")
    if not stream_url:
        raise SystemExit(f"平台适配器没能解析出流地址: {raw}")
    headers = dict(getattr(info, "headers", {}) or {})
    target = work_dir / "captured.mp4"
    cmd = ["ffmpeg", "-y", "-loglevel", "error"]
    for key, value in headers.items():
        cmd += ["-headers", f"{key}: {value}\r\n"]
    cmd += ["-i", stream_url, "-t", str(max(1, capture_seconds)), "-c", "copy", str(target)]
    started = time.perf_counter()
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=capture_seconds + 180, check=False)
    if result.returncode != 0 or not target.is_file():
        raise SystemExit(f"录制失败: {result.stderr[-300:]}")
    return target, {
        "kind": "live" if getattr(info, "is_live", False) else "stream",
        "input": raw,
        "title": getattr(info, "title", None),
        "capture_seconds": capture_seconds,
        "capture_elapsed_sec": round(time.perf_counter() - started, 1),
    }


def extract_frames(video: Path, out_dir: Path, interval: float) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error", "-i", str(video),
        "-vf", f"fps=1/{max(interval, 0.05)}", "-q:v", "3", str(out_dir / "f_%05d.jpg"),
    ]
    subprocess.run(cmd, capture_output=True, text=True, timeout=1800, check=False)
    return sorted(out_dir.glob("f_*.jpg"))


def ocr_has_marker(image, ocr) -> bool:
    import cv2

    enlarged = cv2.resize(image, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    lines, _ = ocr(enlarged)
    return any("REPLAY" in str(text).upper() for _box, text, _score in (lines or []))


def run(
    *,
    video: Path,
    model_dir: Path,
    interval: float,
    threshold: float,
    batch_size: int,
) -> dict:
    import numpy as np

    from lsc.analyzer.ocr_detector import _get_ocr
    from lsc.analyzer.valorant_frame_classifier import ValorantFrameClassifier, marker_roi_config

    frames = extract_frames(video, video.parent / f"{video.stem}_frames", interval)
    if not frames:
        raise SystemExit(f"抽帧为空: {video}")

    classifier = ValorantFrameClassifier(model_dir)
    classifier.load()
    config = marker_roi_config(classifier._meta)
    ocr = _get_ocr()

    evidence: list[float] = []
    ocr_flags: list[bool] = []
    labels: Counter[str] = Counter()
    for start in range(0, len(frames), batch_size):
        chunk = frames[start : start + batch_size]
        images = [_read_image(path) for path in chunk]
        images = [image for image in images if image is not None]
        if not images:
            continue
        branch = classifier.marker_roi_evidence(images)
        if branch is None:
            branch = np.zeros(len(images), dtype=np.float32)
        evidence.extend(float(value) for value in branch)
        probs = classifier.predict_broadcast_batch(images)
        for row in probs:
            labels[CLASSES[int(np.argmax(row))]] += 1
        ocr_flags.extend(ocr_has_marker(image, ocr) for image in images)

    hits = [value >= threshold for value in evidence]
    false_positive = sum(1 for hit, marker in zip(hits, ocr_flags, strict=True) if hit and not marker)
    missed = sum(1 for hit, marker in zip(hits, ocr_flags, strict=True) if marker and not hit)
    agree = sum(1 for hit, marker in zip(hits, ocr_flags, strict=True) if hit == marker)
    total = len(evidence)
    return {
        "video": str(video),
        "model_dir": str(model_dir),
        "marker_branch_enabled": config is not None,
        "marker_rois": sorted(config["rois"]) if config else [],
        "interval_sec": interval,
        "threshold": threshold,
        "frames": total,
        "ocr_marker_frames": int(sum(ocr_flags)),
        "branch_frames": int(sum(hits)),
        "agreement": round(agree / total, 4) if total else 0.0,
        "false_positive": false_positive,
        "missed": missed,
        "evidence_median": round(float(np.median(evidence)), 4) if evidence else 0.0,
        "evidence_max": round(float(np.max(evidence)), 4) if evidence else 0.0,
        "label_counts": dict(labels),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input", required=True, help="本地视频文件，或直播/点播 URL")
    parser.add_argument("--model-dir", type=Path, required=True,
                        help="带标记支路声明的模型目录（如 compose_marker_roi_model.py 的产物）")
    parser.add_argument("--interval", type=float, default=1.0, help="抽帧间隔（秒）")
    parser.add_argument("--threshold", type=float, default=0.77,
                        help="判定为回放的证据阈值（与 class_stable_prob.replay 对齐）")
    parser.add_argument("--capture-seconds", type=int, default=180, help="URL 时录多少秒")
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--min-agreement", type=float, default=0.98, help="PASS 门槛")
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args(argv)

    with tempfile.TemporaryDirectory(prefix="verify_broadcast_") as tmp:
        video, source = resolve_input(
            args.input, capture_seconds=args.capture_seconds, work_dir=Path(tmp),
        )
        report = run(
            video=video,
            model_dir=args.model_dir.expanduser().resolve(),
            interval=args.interval,
            threshold=args.threshold,
            batch_size=args.batch_size,
        )
        report["source"] = source

    print("=" * 78)
    print("广播档回放识别验收")
    print("=" * 78)
    print(f"输入      : {args.input}")
    print(f"模型      : {args.model_dir}")
    print(f"标记支路  : {'已启用' if report['marker_branch_enabled'] else '未启用'} {report['marker_rois']}")
    print(f"帧数      : {report['frames']}（每 {report['interval_sec']}s 一帧）")
    print(f"OCR 有标记: {report['ocr_marker_frames']}    支路≥{report['threshold']}: {report['branch_frames']}")
    print(f"一致率    : {report['agreement']:.2%}    误报 {report['false_positive']}    漏报 {report['missed']}")
    print(f"逐类标签  : {report['label_counts']}")
    passed = report["agreement"] >= args.min_agreement and report["false_positive"] == 0
    print(f"结论      : {'PASS' if passed else 'FAIL'}"
          f"（门槛 一致率≥{args.min_agreement:.0%} 且 误报=0）")

    if args.json:
        out = args.json.expanduser().resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"已写出 JSON: {out}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
