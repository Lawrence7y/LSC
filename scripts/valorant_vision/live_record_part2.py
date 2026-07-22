#!/usr/bin/env python3
"""Record part2 for live_test_84927583848 (append without wiping part1)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from lsc.analyzer.valorant_frame_classifier import ValorantFrameClassifier, _CLASS_NAMES
from lsc.platforms.registry import parse_stream

URL = os.environ.get("LSC_LIVE_URL", "https://live.douyin.com/84927583848")
OUT = Path.home() / "LSC" / "datasets" / "valorant_phase" / "live_test_84927583848"
PART = OUT / "part2"
RECORD_SEC = int(os.environ.get("LSC_LIVE_RECORD_SEC", "600"))


def main() -> None:
    PART.mkdir(parents=True, exist_ok=True)
    frames = PART / "frames"
    frames.mkdir(exist_ok=True)
    print("parsing…", flush=True)
    info = parse_stream(URL)
    print(
        json.dumps(
            {
                "is_live": info.is_live,
                "title": info.title,
                "streamer": info.streamer,
                "error": info.error,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    if not info.is_live or not info.stream_url:
        raise SystemExit("offline")

    ff = ROOT / "lsc-electron" / ".bundle" / "ffmpeg" / "ffmpeg.exe"
    mp4 = PART / f"recording_{datetime.now():%Y%m%d_%H%M%S}.mp4"
    hdr = "".join(f"{k}: {v}\r\n" for k, v in (info.headers or {}).items())
    cmd = [str(ff), "-hide_banner", "-loglevel", "warning", "-y"]
    if hdr:
        cmd += ["-headers", hdr]
    cmd += [
        "-i",
        info.stream_url,
        "-t",
        str(RECORD_SEC),
        "-c",
        "copy",
        "-bsf:a",
        "aac_adtstoasc",
        str(mp4),
    ]
    print("recording part2…", flush=True)
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    print(
        f"ffmpeg exit={r.returncode} size={mp4.stat().st_size if mp4.exists() else 0}",
        flush=True,
    )
    if r.returncode != 0 or not mp4.is_file() or mp4.stat().st_size < 100_000:
        print(r.stderr[-1500:] if r.stderr else "")
        raise SystemExit(1)

    cap = cv2.VideoCapture(str(mp4))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    dur = n / fps if fps else float(RECORD_SEC)
    samples = []
    t = 2.0
    idx = 0
    while t < dur - 0.5:
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
        ok, fr = cap.read()
        if ok and fr is not None:
            idx += 1
            p = frames / f"frame_{idx:06d}.jpg"
            cv2.imencode(".jpg", fr)[1].tofile(str(p))
            samples.append({"idx": idx, "timestamp_sec": round(t, 3), "path": str(p)})
        t += 4.0
    cap.release()
    print(f"frames={len(samples)} dur={dur:.1f}", flush=True)

    clf = ValorantFrameClassifier()
    clf.load()
    details = []
    batch, meta = [], []

    def flush() -> None:
        nonlocal batch, meta
        if not batch:
            return
        probs = clf.predict_batch(batch)
        for row, pr in zip(meta, probs):
            pi = int(pr.argmax())
            details.append(
                {**row, "pred": _CLASS_NAMES[pi], "conf": float(pr[pi])}
            )
        batch, meta = [], []

    for s in samples:
        data = np.fromfile(s["path"], dtype=np.uint8)
        img = cv2.imdecode(data, cv2.IMREAD_COLOR)
        if img is None:
            continue
        batch.append(img)
        meta.append(s)
        if len(batch) >= 16:
            flush()
    flush()

    rep = {
        "part": "part2",
        "n_frames": len(details),
        "pred_dist": dict(Counter(d["pred"] for d in details)),
        "mean_conf": round(sum(d["conf"] for d in details) / len(details), 4)
        if details
        else 0,
        "recording": str(mp4),
        "details": details,
        "timeline": [
            f"{d['timestamp_sec']:6.1f}s  {d['pred']:9s}  {d['conf']:.3f}"
            for d in details
        ],
    }
    (PART / "eval_report.json").write_text(
        json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {k: rep[k] for k in ("n_frames", "pred_dist", "mean_conf")},
            ensure_ascii=False,
            indent=2,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
