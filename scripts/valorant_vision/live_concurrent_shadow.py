#!/usr/bin/env python3
"""Run a concurrent live recording + frame analysis shadow probe.

One FFmpeg process records the live stream while a second FFmpeg process
decodes a sampling stream for online model inference.  This mirrors the
production separation between recording and preview/analysis consumers and
reports live backlog instead of post-hoc processing time.
"""
from __future__ import annotations

import argparse
import json
import queue
import shutil
import subprocess
import sys
import threading
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lsc.analyzer.valorant_frame_classifier import _CLASS_NAMES, ValorantFrameClassifier
from lsc.platforms.registry import parse_stream


def _ffmpeg_path() -> str:
    bundled = ROOT / "lsc-electron" / ".bundle" / "ffmpeg" / "ffmpeg.exe"
    if bundled.is_file():
        return str(bundled)
    found = shutil.which("ffmpeg")
    if found:
        return found
    raise SystemExit("ffmpeg not found")


def _read_jpegs(stream, frames: queue.Queue, stop: threading.Event) -> None:
    buffer = bytearray()
    while not stop.is_set():
        chunk = stream.read(64 * 1024)
        if not chunk:
            break
        buffer.extend(chunk)
        while True:
            start = buffer.find(b"\xff\xd8")
            if start < 0:
                if len(buffer) > 2 * 1024 * 1024:
                    del buffer[:-2]
                break
            end = buffer.find(b"\xff\xd9", start + 2)
            if end < 0:
                if start > 0:
                    del buffer[:start]
                break
            payload = bytes(buffer[start : end + 2])
            del buffer[: end + 2]
            image = cv2.imdecode(np.frombuffer(payload, dtype=np.uint8), cv2.IMREAD_COLOR)
            if image is not None:
                try:
                    frames.put((time.monotonic(), image), timeout=1.0)
                except queue.Full:
                    # The main loop reports the queue/backlog; never silently
                    # let an unbounded decoder queue consume all memory.
                    continue


def _predict(model: ValorantFrameClassifier, images: list[np.ndarray]) -> list[dict[str, Any]]:
    probs = model.predict_broadcast_batch(images)
    return [
        {
            "label": _CLASS_NAMES[int(row.argmax())],
            "confidence": float(row.max()),
            "probs": {name: float(row[index]) for index, name in enumerate(_CLASS_NAMES)},
        }
        for row in probs
    ]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="虎牙直播边录边抽帧边 Shadow 分析")
    parser.add_argument("--url", required=True)
    parser.add_argument("--duration-sec", type=int, default=900)
    parser.add_argument("--sample-interval", type=float, default=4.0)
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--model-dir", type=Path, default=None)
    parser.add_argument("--shadow-model-dir", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.duration_sec <= 0 or args.sample_interval <= 0 or args.batch_size <= 0:
        raise SystemExit("duration-sec, sample-interval and batch-size must be positive")
    output_root = args.output_root.expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    frames_dir = output_root / "online_frames"
    frames_dir.mkdir(exist_ok=True)

    print("parsing…", flush=True)
    info = parse_stream(args.url)
    metadata = {
        "url": args.url,
        "platform": info.platform,
        "title": info.title,
        "streamer": info.streamer,
        "is_live": info.is_live,
        "error": info.error,
        "error_code": info.error_code,
        "selected_quality": info.selected_quality,
        "stream_url_prefix": (info.stream_url or "")[:120],
    }
    print(json.dumps(metadata, ensure_ascii=False, indent=2), flush=True)
    if not info.is_live or not info.stream_url:
        (output_root / "meta.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        raise SystemExit(f"not live or no stream: {info.error or info.error_code}")

    ffmpeg = _ffmpeg_path()
    header_text = "".join(f"{key}: {value}\r\n" for key, value in (info.headers or {}).items())
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    recording = output_root / f"recording_{stamp}.ts"
    record_cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y"]
    if header_text:
        record_cmd += ["-headers", header_text]
    record_cmd += ["-i", info.stream_url, "-t", str(args.duration_sec), "-c", "copy", "-f", "mpegts", str(recording)]
    analyze_cmd = [ffmpeg, "-hide_banner", "-loglevel", "error"]
    if header_text:
        analyze_cmd += ["-headers", header_text]
    analyze_cmd += [
        "-i", info.stream_url,
        "-t", str(args.duration_sec),
        "-vf", f"fps=1/{args.sample_interval:g},scale=960:-2",
        "-q:v", "5",
        "-f", "image2pipe", "-vcodec", "mjpeg", "pipe:1",
    ]

    record_proc = subprocess.Popen(record_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    analyze_proc = subprocess.Popen(analyze_cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    assert analyze_proc.stdout is not None
    frame_queue: queue.Queue[tuple[float, np.ndarray]] = queue.Queue(maxsize=max(32, args.batch_size * 8))
    stop_reader = threading.Event()
    reader = threading.Thread(
        target=_read_jpegs,
        args=(analyze_proc.stdout, frame_queue, stop_reader),
        name="live-frame-reader",
        daemon=True,
    )
    reader.start()

    production = ValorantFrameClassifier(args.model_dir)
    shadow = ValorantFrameClassifier(args.shadow_model_dir)
    production.load()
    shadow.load()
    print(f"recording={recording}", flush=True)
    print(f"production_provider={production.provider} shadow_provider={shadow.provider}", flush=True)

    started = time.monotonic()
    first_frame_at: float | None = None
    last_frame_at: float | None = None
    frame_count = 0
    last_report_at = started
    production_details: list[dict[str, Any]] = []
    shadow_details: list[dict[str, Any]] = []
    deadline = started + float(args.duration_sec) + 15.0
    pending_batch: list[tuple[float, np.ndarray]] = []
    try:
        while time.monotonic() < deadline:
            try:
                received_at, image = frame_queue.get(timeout=1.0)
                pending_batch.append((received_at, image))
            except queue.Empty:
                if record_proc.poll() is not None and analyze_proc.poll() is not None:
                    break
                continue
            if len(pending_batch) < args.batch_size and time.monotonic() < started + args.duration_sec:
                continue
            images = [item[1] for item in pending_batch]
            received_times = [item[0] for item in pending_batch]
            production_batch = _predict(production, images)
            shadow_batch = _predict(shadow, images)
            for p_item, s_item, received_at in zip(production_batch, shadow_batch, received_times, strict=True):
                frame_count += 1
                if first_frame_at is None:
                    first_frame_at = received_at
                last_frame_at = received_at
                media_covered = frame_count * args.sample_interval
                live_elapsed = max(0.0, received_at - started)
                p_item["timestamp_sec"] = round(media_covered - args.sample_interval, 3)
                s_item["timestamp_sec"] = p_item["timestamp_sec"]
                p_item["received_after_sec"] = round(live_elapsed, 3)
                s_item["received_after_sec"] = round(live_elapsed, 3)
                production_details.append(p_item)
                shadow_details.append(s_item)
            pending_batch = []
            now = time.monotonic()
            if now - last_report_at >= 30.0:
                media_covered = frame_count * args.sample_interval
                live_elapsed = max(0.001, now - started)
                lag = max(0.0, live_elapsed - media_covered)
                print(
                    f"online frames={frame_count} media={media_covered:.1f}s "
                    f"wall={live_elapsed:.1f}s ratio={media_covered/live_elapsed:.2f}x "
                    f"lag≈{lag:.1f}s queue={frame_queue.qsize()}",
                    flush=True,
                )
                last_report_at = now
            if time.monotonic() >= started + float(args.duration_sec):
                break
    finally:
        stop_reader.set()
        for proc in (analyze_proc, record_proc):
            if proc.poll() is None:
                proc.terminate()
        for proc in (analyze_proc, record_proc):
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        reader.join(timeout=5)

    ended = time.monotonic()
    media_covered = frame_count * args.sample_interval
    wall_elapsed = max(0.001, ended - started)
    shadow_disagreements = [
        {
            "timestamp_sec": p_item["timestamp_sec"],
            "production": {"label": p_item["label"], "confidence": p_item["confidence"]},
            "shadow": {"label": s_item["label"], "confidence": s_item["confidence"]},
        }
        for p_item, s_item in zip(production_details, shadow_details, strict=True)
        if p_item["label"] != s_item["label"]
    ]
    report = {
        "metadata": metadata,
        "recording": str(recording),
        "recording_format": "mpegts",
        "duration_sec_requested": args.duration_sec,
        "sample_interval_sec": args.sample_interval,
        "frame_count": frame_count,
        "media_covered_sec": round(media_covered, 3),
        "wall_elapsed_sec": round(wall_elapsed, 3),
        "online_coverage_ratio": round(media_covered / wall_elapsed, 4),
        "analysis_lag_estimate_sec": round(max(0.0, wall_elapsed - media_covered), 3),
        "first_frame_after_sec": round(max(0.0, first_frame_at - started), 3) if first_frame_at else None,
        "last_frame_after_sec": round(max(0.0, last_frame_at - started), 3) if last_frame_at else None,
        "models": {
            "production": {
                "provider": production.provider,
                "telemetry": production.telemetry,
                "pred_dist": dict(Counter(item["label"] for item in production_details)),
            },
            "shadow": {
                "provider": shadow.provider,
                "telemetry": shadow.telemetry,
                "pred_dist": dict(Counter(item["label"] for item in shadow_details)),
            },
        },
        "shadow_disagreement_count": len(shadow_disagreements),
        "shadow_disagreement_rate": round(len(shadow_disagreements) / frame_count, 4) if frame_count else 0.0,
        "shadow_disagreements": shadow_disagreements,
        "production_timeline": production_details,
        "shadow_timeline": shadow_details,
    }
    report_path = output_root / "concurrent_shadow_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "frame_count": frame_count,
        "media_covered_sec": round(media_covered, 3),
        "wall_elapsed_sec": round(wall_elapsed, 3),
        "online_coverage_ratio": report["online_coverage_ratio"],
        "analysis_lag_estimate_sec": report["analysis_lag_estimate_sec"],
        "shadow_disagreement_count": len(shadow_disagreements),
        "report": str(report_path),
    }, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
