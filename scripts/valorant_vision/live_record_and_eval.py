#!/usr/bin/env python3
"""Record a Douyin live briefly and evaluate Valorant phase classifier."""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import os

from lsc.analyzer.valorant_frame_classifier import _CLASS_NAMES, ValorantFrameClassifier
from lsc.platforms.registry import parse_stream

URL = os.environ.get("LSC_LIVE_URL", "https://live.douyin.com/59475730286")
_room = URL.rstrip("/").split("?")[0].split("/")[-1]
OUT = Path.home() / "LSC" / "datasets" / "valorant_phase" / f"live_test_{_room}"
RECORD_SEC = int(os.environ.get("LSC_LIVE_RECORD_SEC", "600"))
INTERVAL = float(os.environ.get("LSC_LIVE_INTERVAL", "4.0"))


def find_ffmpeg() -> str:
    bundled = ROOT / "lsc-electron" / ".bundle" / "ffmpeg" / "ffmpeg.exe"
    if bundled.is_file():
        return str(bundled)
    which = shutil.which("ffmpeg")
    if which:
        return which
    raise SystemExit("ffmpeg not found")


def imread_unicode(p: Path):
    data = np.fromfile(str(p), dtype=np.uint8)
    img = cv2.imdecode(data, cv2.IMREAD_COLOR)
    return img if img is not None else cv2.imread(str(p))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="录制直播并执行 Valorant 官方解说 Shadow 对比")
    parser.add_argument("--url", default=URL, help="直播间 URL")
    parser.add_argument("--record-sec", type=int, default=RECORD_SEC, help="录制秒数")
    parser.add_argument("--interval", type=float, default=INTERVAL, help="抽帧间隔秒数")
    parser.add_argument("--output-root", type=Path, default=None, help="输出目录，默认按房间号写入 ~/LSC")
    parser.add_argument("--model-dir", type=Path, default=None, help="生产模型目录，默认使用内置模型")
    parser.add_argument("--shadow-model-dir", type=Path, default=None, help="可选 Shadow 候选模型目录")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    url = str(args.url).strip()
    if not url:
        raise SystemExit("url must not be empty")
    if args.record_sec <= 0 or args.interval <= 0:
        raise SystemExit("record-sec and interval must be positive")
    room = url.rstrip("/").split("?")[0].split("/")[-1]
    out = (
        args.output_root.expanduser().resolve()
        if args.output_root is not None
        else Path.home() / "LSC" / "datasets" / "valorant_phase" / f"live_test_{room}"
    )
    out.mkdir(parents=True, exist_ok=True)
    frames_dir = out / "frames"
    frames_dir.mkdir(exist_ok=True)

    print("parsing…", flush=True)
    info = parse_stream(url)
    meta = {
        "url": url,
        "platform": info.platform,
        "title": info.title,
        "streamer": info.streamer,
        "is_live": info.is_live,
        "error": info.error,
        "error_code": info.error_code,
        "selected_quality": info.selected_quality,
        "stream_url_prefix": (info.stream_url or "")[:120],
    }
    print(json.dumps(meta, ensure_ascii=False, indent=2), flush=True)
    if not info.is_live or not info.stream_url:
        (out / "meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        raise SystemExit(f"not live or no stream: {info.error or info.error_code}")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_mp4 = out / f"recording_{stamp}.mp4"
    ffmpeg = find_ffmpeg()
    headers = info.headers or {}
    header_str = "".join(f"{k}: {v}\r\n" for k, v in headers.items())

    cmd = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "warning",
        "-y",
    ]
    if header_str:
        cmd += ["-headers", header_str]
    cmd += [
        "-i",
        info.stream_url,
        "-t",
        str(args.record_sec),
        "-c",
        "copy",
        "-bsf:a",
        "aac_adtstoasc",
        str(out_mp4),
    ]
    print("recording…", " ".join(cmd[:8]), "…", flush=True)
    t0 = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    elapsed = time.time() - t0
    print(f"ffmpeg exit={proc.returncode} elapsed={elapsed:.1f}s size={out_mp4.stat().st_size if out_mp4.exists() else 0}", flush=True)
    if proc.returncode != 0 or not out_mp4.is_file() or out_mp4.stat().st_size < 100_000:
        print(proc.stderr[-2000:] if proc.stderr else "(no stderr)", flush=True)
        raise SystemExit("record failed")

    # extract frames every interval
    print("extracting frames…", flush=True)
    for old in frames_dir.glob("*.jpg"):
        old.unlink()
    cap = cv2.VideoCapture(str(out_mp4))
    if not cap.isOpened():
        raise SystemExit("cannot open recording")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    n_frames_total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    dur_sec = n_frames_total / fps if fps > 0 else float(args.record_sec)

    samples = []
    t = 2.0
    idx = 0
    while t < dur_sec - 0.5:
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
        ok, frame = cap.read()
        if not ok or frame is None:
            t += args.interval
            continue
        idx += 1
        name = f"frame_{idx:06d}.jpg"
        path = frames_dir / name
        cv2.imencode(".jpg", frame)[1].tofile(str(path))
        samples.append({"idx": idx, "timestamp_sec": round(t, 3), "path": str(path)})
        t += args.interval
    cap.release()
    print(f"frames={len(samples)} duration≈{dur_sec:.1f}s", flush=True)

    model_specs = [("production", ValorantFrameClassifier(args.model_dir))]
    if args.shadow_model_dir is not None:
        model_specs.append(("shadow", ValorantFrameClassifier(args.shadow_model_dir)))
    model_reports = {}
    for model_name, clf in model_specs:
        clf.load()
        print(f"{model_name}_provider={clf.provider}", flush=True)
        details = []
        batch, meta_b = [], []
        for sample in samples:
            img = imread_unicode(Path(sample["path"]))
            if img is None:
                continue
            batch.append(img)
            meta_b.append(sample)
            if len(batch) >= 16:
                probs = clf.predict_broadcast_batch(batch)
                for row, pr in zip(meta_b, probs, strict=True):
                    pi = int(pr.argmax())
                    details.append({
                        **row,
                        "pred": _CLASS_NAMES[pi],
                        "conf": float(pr[pi]),
                        "probs": {n: float(pr[i]) for i, n in enumerate(_CLASS_NAMES)},
                    })
                batch, meta_b = [], []
        if batch:
            probs = clf.predict_broadcast_batch(batch)
            for row, pr in zip(meta_b, probs, strict=True):
                pi = int(pr.argmax())
                details.append({
                    **row,
                    "pred": _CLASS_NAMES[pi],
                    "conf": float(pr[pi]),
                    "probs": {n: float(pr[i]) for i, n in enumerate(_CLASS_NAMES)},
                })
        model_reports[model_name] = {
            "provider": clf.provider,
            "telemetry": clf.telemetry,
            "pred_dist": dict(Counter(d["pred"] for d in details)),
            "details": details,
        }

    production_details = model_reports["production"]["details"]
    timeline = [
        f"{d['timestamp_sec']:6.1f}s  {d['pred']:9s}  {d['conf']:.3f}"
        for d in production_details
    ]
    shadow_disagreements = []
    if "shadow" in model_reports:
        shadow_by_ts = {
            round(float(d["timestamp_sec"]), 3): d
            for d in model_reports["shadow"]["details"]
        }
        for item in production_details:
            other = shadow_by_ts.get(round(float(item["timestamp_sec"]), 3))
            if other and item["pred"] != other["pred"]:
                shadow_disagreements.append({
                    "timestamp_sec": item["timestamp_sec"],
                    "production": {"label": item["pred"], "confidence": item["conf"]},
                    "shadow": {"label": other["pred"], "confidence": other["conf"]},
                })
    report = {
        "meta": meta,
        "recording": str(out_mp4),
        "record_sec_requested": args.record_sec,
        "n_frames": len(production_details),
        "interval_sec": args.interval,
        "models": model_reports,
        "shadow_disagreement_count": len(shadow_disagreements),
        "shadow_disagreements": shadow_disagreements,
        "timeline": timeline,
    }
    (out / "eval_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "n_frames": report["n_frames"],
        "recording": report["recording"],
        "models": {
            name: {"provider": value["provider"], "pred_dist": value["pred_dist"], "telemetry": value["telemetry"]}
            for name, value in model_reports.items()
        },
        "shadow_disagreement_count": len(shadow_disagreements),
    }, ensure_ascii=False, indent=2), flush=True)
    print("--- timeline ---", flush=True)
    for line in timeline:
        print(line, flush=True)


if __name__ == "__main__":
    main()
