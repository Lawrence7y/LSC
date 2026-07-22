#!/usr/bin/env python3
"""Eval part2 recording: classify + hybrid; write combined summary."""
from __future__ import annotations

import json
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(r"D:\Project\直播切片多人")
sys.path.insert(0, str(ROOT))

from lsc.analyzer.round_detector import detect_valorant_rounds_hybrid
from lsc.analyzer.valorant_frame_classifier import ValorantFrameClassifier, _CLASS_NAMES

SESSION = Path(r"D:\desktop\新建文件夹 (2)\live_test_435345554204_20260722_144111")
PART = SESSION / "part2"
MP4 = PART / "recording_20260722_145132.mp4"
MODEL = Path.home() / "LSC" / "models" / "valorant_phase_boundary_20260722_v2"
FFMPEG = str(ROOT / "lsc-electron" / ".bundle" / "ffmpeg" / "ffmpeg.exe")
INTERVAL = 4.0


def main() -> None:
    frames_dir = PART / "frames"
    frames_dir.mkdir(exist_ok=True)
    print("extracting frames...", flush=True)
    cap = cv2.VideoCapture(str(MP4))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    dur = n / fps if fps else 1260.0
    samples: list[dict] = []
    t = 2.0
    idx = 0
    while t < dur - 0.5:
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
        ok, fr = cap.read()
        if ok and fr is not None:
            idx += 1
            p = frames_dir / f"frame_{idx:06d}.jpg"
            cv2.imencode(".jpg", fr)[1].tofile(str(p))
            samples.append({"idx": idx, "timestamp_sec": round(t, 3), "path": str(p)})
        t += INTERVAL
    cap.release()
    print(f"frames={len(samples)} dur={dur:.1f}", flush=True)

    clf = ValorantFrameClassifier(model_dir=MODEL)
    clf.load()
    details: list[dict] = []
    batch: list = []
    meta: list = []

    def flush() -> None:
        nonlocal batch, meta
        if not batch:
            return
        probs = clf.predict_batch(batch)
        for row, pr in zip(meta, probs):
            pi = int(pr.argmax())
            details.append(
                {
                    **row,
                    "pred": _CLASS_NAMES[pi],
                    "conf": float(pr[pi]),
                    "probs": {name: float(pr[i]) for i, name in enumerate(_CLASS_NAMES)},
                }
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

    dist = Counter(d["pred"] for d in details)
    cls = {
        "recording": str(MP4),
        "duration_sec": round(dur, 2),
        "n_frames": len(details),
        "interval_sec": INTERVAL,
        "model_dir": str(MODEL),
        "provider": clf.provider,
        "pred_dist": dict(dist),
        "mean_conf": round(sum(d["conf"] for d in details) / len(details), 4) if details else 0,
        "timeline": [
            f"{d['timestamp_sec']:7.1f}s  {d['pred']:9s}  {d['conf']:.3f}" for d in details
        ],
        "details": details,
    }
    (PART / "classification_report.json").write_text(
        json.dumps(cls, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {k: cls[k] for k in ("pred_dist", "n_frames", "mean_conf", "duration_sec")},
            ensure_ascii=False,
            indent=2,
        ),
        flush=True,
    )

    print("hybrid...", flush=True)
    t0 = time.time()

    def progress(stage: str, frac: float, msg: str) -> None:
        if int(frac * 100) % 5 == 0:
            print(f"  [{stage}] {frac:.0%} {msg}", flush=True)

    rounds = detect_valorant_rounds_hybrid(
        str(MP4),
        ffmpeg_path=FFMPEG,
        model_dir=MODEL,
        progress_callback=progress,
        session_id="live_435345554204_part2",
    )
    hy = {
        "recording": str(MP4),
        "duration_sec": round(dur, 2),
        "model_dir": str(MODEL),
        "elapsed_sec": round(time.time() - t0, 1),
        "n_rounds": len(rounds),
        "n_listed": len(rounds),
        "confirm_status_dist": dict(Counter(r.get("confirm_status", "?") for r in rounds)),
        "rounds": rounds,
    }
    (PART / "hybrid_rounds.json").write_text(
        json.dumps(hy, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (PART / "analysis.json").write_text(
        json.dumps(
            {
                "video": str(MP4),
                "model_dir": str(MODEL),
                "rounds": rounds,
                "classification_pred_dist": dict(dist),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {k: hy[k] for k in ("n_rounds", "confirm_status_dist", "elapsed_sec")},
            ensure_ascii=False,
            indent=2,
        ),
        flush=True,
    )
    for r in rounds:
        print(
            f"  round {r.get('start'):.1f}-{r.get('end'):.1f} "
            f"{r.get('confirm_status')} end_by={r.get('end_by')}",
            flush=True,
        )

    p1 = json.loads((SESSION / "summary.json").read_text(encoding="utf-8"))
    combined = {
        "session": str(SESSION),
        "total_duration_sec": round(p1["duration_sec"] + dur, 2),
        "part1": {
            "duration_sec": p1["duration_sec"],
            "classification": p1["classification"],
            "hybrid": p1["hybrid"],
        },
        "part2": {
            "duration_sec": round(dur, 2),
            "classification": {
                "pred_dist": dict(dist),
                "n_frames": len(details),
                "mean_conf": cls["mean_conf"],
            },
            "hybrid": {
                "n_rounds": len(rounds),
                "confirm_status_dist": hy["confirm_status_dist"],
                "elapsed_sec": hy["elapsed_sec"],
            },
        },
        "finished_at": datetime.now().isoformat(timespec="seconds"),
    }
    (SESSION / "combined_summary.json").write_text(
        json.dumps(combined, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    part2_summary = {
        **combined["part2"],
        "recording": str(MP4),
        "finished_at": combined["finished_at"],
    }
    (PART / "summary.json").write_text(
        json.dumps(part2_summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("PART2_EVAL_DONE", json.dumps(combined, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
