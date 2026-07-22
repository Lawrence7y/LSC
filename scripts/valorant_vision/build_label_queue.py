#!/usr/bin/env python3
"""Build unlabeled annotation queue + lightweight local keyboard labeler.

Usage:
  python scripts/valorant_vision/build_label_queue.py
  python scripts/valorant_vision/serve_label_ui.py
Then open http://127.0.0.1:8765/
Keys: 1=non_game 2=buy 3=combat 4=result 5=replay  s=skip  z=undo  ←/→ navigate
"""
from __future__ import annotations

import json
import re
from pathlib import Path

OUT_ROOT = Path.home() / "LSC" / "datasets" / "valorant_phase" / "annotate"
INTERVAL_SEC = 4.0

SESSIONS = [
    {
        "dir": "pov_ling",
        "video_id": "pov_ling_20260720_134749",
        "session_id": "douyin_ling_12eaf3_20260720",
        "source_type": "pov",
        "split": "train",
        "video_path": r"D:\desktop\新建文件夹\新建文件夹\新建文件夹\douyin_从玲开始的异世界_12eaf3\recording_20260720_134749_493238.mp4",
    },
    {
        "dir": "broadcast_yuezi",
        "video_id": "broadcast_yuezi_20260720_202557",
        "session_id": "douyin_yuezi_2aaacc_20260720",
        "source_type": "broadcast",
        "split": "val",
        "video_path": r"D:\desktop\新建文件夹\新建文件夹\新建文件夹\douyin_月子_无畏契约解说_2aaacc\recording_20260720_202557_76e32f.mp4",
    },
    {
        "dir": "pov_beilie",
        "video_id": "pov_beilie_20260721_141303",
        "session_id": "douyin_beilie_357308_20260721",
        "source_type": "pov",
        "split": "train",
        "video_path": r"D:\desktop\新建文件夹\新建文件夹\新建文件夹\douyin_卑劣的凡_357308\recording_20260721_141303_c4d544.mp4",
    },
    {
        "dir": "pov_tangqihua",
        "video_id": "pov_tangqihua_20260721_141301",
        "session_id": "douyin_tangqihua_ead22d_20260721",
        "source_type": "pov",
        "split": "train",
        "video_path": r"D:\desktop\新建文件夹\新建文件夹\新建文件夹\douyin_唐启华_ead22d\recording_20260721_141301_8c8b34.mp4",
    },
]


def frame_index(path: Path) -> int:
    m = re.search(r"(\d+)", path.stem)
    if not m:
        raise ValueError(path)
    return int(m.group(1))


def main() -> None:
    items: list[dict] = []
    for sess in SESSIONS:
        folder = OUT_ROOT / sess["dir"]
        frames = sorted(folder.glob("frame_*.jpg"))
        for fp in frames:
            idx = frame_index(fp)
            ts = (idx - 1) * INTERVAL_SEC
            items.append(
                {
                    "id": f"{sess['video_id']}_{idx:06d}",
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
                }
            )
    queue_path = OUT_ROOT / "queue.json"
    queue_path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    labels_path = OUT_ROOT / "labels.json"
    if not labels_path.exists():
        labels_path.write_text("{}", encoding="utf-8")
    print(f"wrote {len(items)} items -> {queue_path}")


if __name__ == "__main__":
    main()
