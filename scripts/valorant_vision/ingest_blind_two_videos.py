#!/usr/bin/env python3
"""Extract + classify two new POV videos and push into label UI for GT."""
from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

import cv2

from lsc.analyzer.valorant_frame_classifier import ValorantFrameClassifier

ANN = Path.home() / "LSC" / "datasets" / "valorant_phase" / "annotate"
QUEUE = ANN / "queue.json"
LABELS = ANN / "labels.json"
BACKUP = ANN / "backups"
INTERVAL = 4.0
LABELS_ORDER = ["non_game", "buy", "combat", "result", "replay"]

SESSIONS = [
    {
        "dir": "pov_dianjing",
        "video_id": "pov_dianjing_20260721_170951",
        "session_id": "douyin_dianjing_c701d9_20260721",
        "source_type": "pov",
        "split": "test",
        "video_path": r"D:\desktop\新建文件夹\新建文件夹\douyin_无畏契约电竞_c701d9\recording_20260721_170951_7d8bb9.mp4",
    },
    {
        "dir": "pov_hanghang",
        "video_id": "pov_hanghang_20260721_173052",
        "session_id": "douyin_hanghang_8eaa2b_20260721",
        "source_type": "pov",
        "split": "test",
        "video_path": r"D:\desktop\新建文件夹\新建文件夹\douyin_HangHang_8eaa2b\recording_20260721_173052_197c5c.mp4",
    },
]


def extract(video: Path, out_dir: Path) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("frame_*.jpg"):
        old.unlink()
    pattern = str(out_dir / "frame_%06d.jpg")
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(video),
        "-vf",
        f"fps=1/{INTERVAL:g},scale=640:-2",
        "-q:v",
        "3",
        pattern,
    ]
    subprocess.run(cmd, check=True)
    return len(list(out_dir.glob("frame_*.jpg")))


def main() -> None:
    clf = ValorantFrameClassifier()
    clf.load()
    print("provider", clf.provider)

    new_items: list[dict] = []
    pred_summary: dict[str, dict] = {}

    for sess in SESSIONS:
        video = Path(sess["video_path"])
        out_dir = ANN / sess["dir"]
        n = extract(video, out_dir)
        print(f"extracted {n} -> {out_dir}")

        frames = sorted(out_dir.glob("frame_*.jpg"))
        counts = {k: 0 for k in LABELS_ORDER}
        low = 0
        for i, fp in enumerate(frames, 1):
            ts = (i - 1) * INTERVAL
            probs = clf.predict_batch([cv2.imread(str(fp))])[0]
            idx = int(probs.argmax())
            pred = LABELS_ORDER[idx]
            conf = float(probs[idx])
            counts[pred] += 1
            if conf < 0.7:
                low += 1
            item = {
                "id": f"{sess['video_id']}_{i:06d}",
                "rel_path": f"{sess['dir']}/{fp.name}",
                "abs_path": str(fp),
                "video_id": sess["video_id"],
                "video_path": sess["video_path"],
                "timestamp_sec": ts,
                "source_type": sess["source_type"],
                "session_id": sess["session_id"],
                "split": sess["split"],
                "label": None,
                "notes": "",
                "priority": "blind_new",
                "current_label": None,
                "suggested_label": pred,
                "reason": f"模型预测 {pred} conf={conf:.3f}",
            }
            new_items.append(item)
        pred_summary[sess["dir"]] = {"n": n, "pred": counts, "low_conf": low}
        print(f"  pred={counts} low_conf<0.7={low}")

    BACKUP.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    shutil.copy2(QUEUE, BACKUP / f"queue_{stamp}.json")
    shutil.copy2(LABELS, BACKUP / f"labels_{stamp}.json")

    queue = json.loads(QUEUE.read_text(encoding="utf-8"))
    labels = json.loads(LABELS.read_text(encoding="utf-8"))

    # drop previous entries for these videos if any
    drop_ids = {x["id"] for x in new_items}
    queue = [q for q in queue if q["id"] not in drop_ids]
    for iid in list(labels.keys()):
        if iid in drop_ids:
            del labels[iid]

    # new blind videos first
    new_queue = new_items + queue
    QUEUE.write_text(json.dumps(new_queue, ensure_ascii=False, indent=2), encoding="utf-8")
    LABELS.write_text(json.dumps(labels, ensure_ascii=False, indent=2), encoding="utf-8")

    summary_path = ANN / "blind_new_pred_summary.json"
    summary_path.write_text(json.dumps(pred_summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"queued {len(new_items)} unlabeled at head; total_queue={len(new_queue)} labels={len(labels)}")
    print("Refresh http://127.0.0.1:8765/ → 只看未标")


if __name__ == "__main__":
    main()
