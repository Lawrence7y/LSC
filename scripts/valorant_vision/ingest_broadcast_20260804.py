#!/usr/bin/env python3
"""赛事/解说直播域补标（2026-08-04 需求）：抽帧 + 预打标 + 注入标注队列。

背景：valorant_phase_v1 在赛事 OB/解说流上 combat 召回不足
（2026-08-04 实测多个真实回合 visual_combat=0 被假回合过滤丢弃）。
本脚本把三段赛事/解说录制按 4s 间隔抽帧，用现有模型预打标后
推入 annotate 队列供人工复核，产出 v2 训练/验证数据。

划分：
- 月子解说流（46min）→ train：主要域补充
- 雪乃荔荔枝两段赛事流（共 24min）→ val：v2 改进效果的域内验证集

用法:
  python scripts/valorant_vision/ingest_broadcast_20260804.py
"""
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
        "dir": "broadcast_yuezi_20260801",
        "video_id": "broadcast_yuezi_20260801_233455",
        "session_id": "douyin_yuezi_jieshuo_5abc45_20260801",
        "source_type": "broadcast",
        "split": "train",
        "video_path": r"D:\desktop\新建文件夹 (2)\douyin_月子_无畏契约解说_5abc45\recording_20260801_233455_ae8d09.mp4",
    },
    {
        "dir": "broadcast_xuenai_cc13c1",
        "video_id": "broadcast_xuenai_cc13c1_20260804_180834",
        "session_id": "douyin_xuenai_cc13c1_20260804",
        "source_type": "broadcast",
        "split": "val",
        "video_path": r"D:\desktop\新建文件夹 (2)\新建文件夹\douyin_雪乃荔荔枝_cc13c1\recording_20260804_180834_c49b45.mp4",
    },
    {
        "dir": "broadcast_xuenai_a4ddcf",
        "video_id": "broadcast_xuenai_a4ddcf_20260804_190627",
        "session_id": "douyin_xuenai_a4ddcf_20260804",
        "source_type": "broadcast",
        "split": "val",
        "video_path": r"D:\desktop\新建文件夹 (2)\新建文件夹\douyin_雪乃荔荔枝_a4ddcf\recording_20260804_190627_e61e93.mp4",
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
        if not video.is_file():
            print(f"!! missing video: {video}")
            continue
        out_dir = ANN / sess["dir"]
        n = extract(video, out_dir)
        print(f"extracted {n} -> {out_dir}")

        frames = sorted(out_dir.glob("frame_*.jpg"))
        counts = {k: 0 for k in LABELS_ORDER}
        low = 0
        batch_imgs: list = []
        batch_meta: list[tuple[Path, float]] = []
        for i, fp in enumerate(frames, 1):
            ts = (i - 1) * INTERVAL
            img = cv2.imread(str(fp))
            if img is None:
                continue
            batch_imgs.append(img)
            batch_meta.append((fp, ts))
            if len(batch_imgs) >= 32 or i == len(frames):
                probs_batch = clf.predict_batch(batch_imgs)
                for (fp2, ts2), probs in zip(batch_meta, probs_batch):
                    idx = int(probs.argmax())
                    pred = LABELS_ORDER[idx]
                    conf = float(probs[idx])
                    counts[pred] += 1
                    if conf < 0.7:
                        low += 1
                    frame_no = int(fp2.stem.split("_")[-1])
                    item = {
                        "id": f"{sess['video_id']}_{frame_no:06d}",
                        "rel_path": f"{sess['dir']}/{fp2.name}",
                        "abs_path": str(fp2),
                        "video_id": sess["video_id"],
                        "video_path": sess["video_path"],
                        "timestamp_sec": ts2,
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
                batch_imgs = []
                batch_meta = []
        pred_summary[sess["dir"]] = {"n": n, "pred": counts, "low_conf": low}
        print(f"  pred={counts} low_conf<0.7={low}")

    BACKUP.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    shutil.copy2(QUEUE, BACKUP / f"queue_{stamp}.json")
    shutil.copy2(LABELS, BACKUP / f"labels_{stamp}.json")

    queue = json.loads(QUEUE.read_text(encoding="utf-8"))
    labels = json.loads(LABELS.read_text(encoding="utf-8"))

    # 幂等：先移除本批次旧条目
    drop_ids = {x["id"] for x in new_items}
    queue = [q for q in queue if q["id"] not in drop_ids]
    for iid in list(labels.keys()):
        if iid in drop_ids:
            del labels[iid]

    # 新批次置于队首，优先标注
    new_queue = new_items + queue
    QUEUE.write_text(json.dumps(new_queue, ensure_ascii=False, indent=2), encoding="utf-8")
    LABELS.write_text(json.dumps(labels, ensure_ascii=False, indent=2), encoding="utf-8")

    summary_path = ANN / "broadcast_20260804_pred_summary.json"
    summary_path.write_text(json.dumps(pred_summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"queued {len(new_items)} unlabeled at head; total_queue={len(new_queue)} labels={len(labels)}")
    print("Refresh http://127.0.0.1:8765/ → 只看未标")


if __name__ == "__main__":
    main()
