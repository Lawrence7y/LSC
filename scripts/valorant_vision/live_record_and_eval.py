#!/usr/bin/env python3
"""Record a Douyin live briefly and evaluate Valorant phase classifier."""
from __future__ import annotations

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


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    frames_dir = OUT / "frames"
    frames_dir.mkdir(exist_ok=True)

    print("parsing…", flush=True)
    info = parse_stream(URL)
    meta = {
        "url": URL,
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
        (OUT / "meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        raise SystemExit(f"not live or no stream: {info.error or info.error_code}")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_mp4 = OUT / f"recording_{stamp}.mp4"
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
        str(RECORD_SEC),
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

    # extract frames every INTERVAL
    print("extracting frames…", flush=True)
    for old in frames_dir.glob("*.jpg"):
        old.unlink()
    cap = cv2.VideoCapture(str(out_mp4))
    if not cap.isOpened():
        raise SystemExit("cannot open recording")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    n_frames_total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    dur_sec = n_frames_total / fps if fps > 0 else float(RECORD_SEC)

    samples = []
    t = 2.0
    idx = 0
    while t < dur_sec - 0.5:
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
        ok, frame = cap.read()
        if not ok or frame is None:
            t += INTERVAL
            continue
        idx += 1
        name = f"frame_{idx:06d}.jpg"
        path = frames_dir / name
        cv2.imencode(".jpg", frame)[1].tofile(str(path))
        samples.append({"idx": idx, "timestamp_sec": round(t, 3), "path": str(path)})
        t += INTERVAL
    cap.release()
    print(f"frames={len(samples)} duration≈{dur_sec:.1f}s", flush=True)

    clf = ValorantFrameClassifier()
    clf.load()
    print(f"provider={clf.provider}", flush=True)

    details = []
    batch, meta_b = [], []
    for s in samples:
        img = imread_unicode(Path(s["path"]))
        if img is None:
            continue
        batch.append(img)
        meta_b.append(s)
        if len(batch) >= 16:
            probs = clf.predict_batch(batch)
            for row, pr in zip(meta_b, probs, strict=True):
                pi = int(pr.argmax())
                details.append(
                    {
                        **row,
                        "pred": _CLASS_NAMES[pi],
                        "conf": float(pr[pi]),
                        "probs": {n: float(pr[i]) for i, n in enumerate(_CLASS_NAMES)},
                    }
                )
            batch, meta_b = [], []
    if batch:
        probs = clf.predict_batch(batch)
        for row, pr in zip(meta_b, probs, strict=True):
            pi = int(pr.argmax())
            details.append(
                {
                    **row,
                    "pred": _CLASS_NAMES[pi],
                    "conf": float(pr[pi]),
                    "probs": {n: float(pr[i]) for i, n in enumerate(_CLASS_NAMES)},
                }
            )

    dist = Counter(d["pred"] for d in details)
    timeline = [
        f"{d['timestamp_sec']:6.1f}s  {d['pred']:9s}  {d['conf']:.3f}" for d in details
    ]
    report = {
        "meta": meta,
        "recording": str(out_mp4),
        "record_sec_requested": RECORD_SEC,
        "n_frames": len(details),
        "interval_sec": INTERVAL,
        "pred_dist": dict(dist),
        "mean_conf": round(sum(d["conf"] for d in details) / len(details), 4) if details else 0,
        "timeline": timeline,
        "details": details,
    }
    (OUT / "eval_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (OUT / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("pred_dist", "n_frames", "mean_conf", "recording")}, ensure_ascii=False, indent=2), flush=True)
    print("--- timeline ---", flush=True)
    for line in timeline:
        print(line, flush=True)


if __name__ == "__main__":
    main()
