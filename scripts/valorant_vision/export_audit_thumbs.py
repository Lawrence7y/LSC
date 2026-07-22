#!/usr/bin/env python3
"""Export visual samples for Codex label audit review."""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np

CODEX = Path(
    r"D:\Project\直播切片多人\.worktrees\valorant-frame-labeling-pilot"
    r"\datasets\valorant_phase\annotations\new_broadcast_20260721"
)
ANN = Path.home() / "LSC" / "datasets" / "valorant_phase" / "annotate"
THUMB = ANN / "label_audit_thumbs"


def imread(p: Path):
    return cv2.imdecode(np.fromfile(str(p), dtype=np.uint8), cv2.IMREAD_COLOR)


def save_img(img, path: Path) -> None:
    h, w = img.shape[:2]
    sc = 640 / max(h, w)
    small = cv2.resize(img, (int(w * sc), int(h * sc)))
    cv2.imencode(".jpg", small)[1].tofile(str(path))


def main() -> None:
    THUMB.mkdir(parents=True, exist_ok=True)
    for old in THUMB.glob("*.jpg"):
        old.unlink()

    data = json.loads((ANN / "label_audit_codex_suspects.json").read_text(encoding="utf-8"))
    labels = json.loads((CODEX / "labels.json").read_text(encoding="utf-8"))
    queue_list = json.loads((CODEX / "queue.json").read_text(encoding="utf-8"))
    by_vid: dict[str, list] = defaultdict(list)
    for item in queue_list:
        by_vid[item["video_id"]].append(item)
    for items in by_vid.values():
        items.sort(key=lambda x: x["timestamp_sec"])

    # islands + neighbors
    for s in data["islands"]:
        items = by_vid[s["video_id"]]
        i = next(k for k, it in enumerate(items) if it["id"] == s["id"])
        for j, tag in ((i - 1, "prev"), (i, "isl"), (i + 1, "next")):
            if not (0 <= j < len(items)):
                continue
            it = items[j]
            img = imread(CODEX / it["rel_path"])
            if img is None:
                continue
            gt = labels[it["id"]]["label"]
            name = (
                f"ctx_{s['video_id'].split('_')[1]}_"
                f"t{int(s['timestamp_sec']):04d}_{tag}_lab-{gt}.jpg"
            )
            save_img(img, THUMB / name)

    # high-conf samples by error type
    hc = [s for s in data["suspects"] if "high_conf" in s["kind"]]
    by_err: dict[str, list] = defaultdict(list)
    for s in hc:
        by_err[f"{s['gt']}->{s['pred']}"].append(s)

    exported = []
    for et, rows in sorted(by_err.items(), key=lambda x: -len(x[1])):
        rows = sorted(rows, key=lambda x: -(x["conf"] or 0))
        picks = rows[:2]
        if len(rows) > 4:
            picks.append(rows[len(rows) // 2])
        for s in picks:
            img = imread(CODEX / s["rel_path"])
            if img is None:
                continue
            name = (
                f"hc_{et.replace('->','_to_')}_"
                f"{s['video_id'].split('_')[1]}_"
                f"t{int(s['timestamp_sec']):04d}_c{s['conf']:.2f}.jpg"
            )
            save_img(img, THUMB / name)
            exported.append(
                {
                    "file": name,
                    "error": et,
                    "id": s["id"],
                    "gt": s["gt"],
                    "pred": s["pred"],
                    "conf": s["conf"],
                    "timestamp_sec": s["timestamp_sec"],
                    "video_id": s["video_id"],
                    "rel_path": s["rel_path"],
                }
            )

    (ANN / "label_audit_visual_samples.json").write_text(
        json.dumps({"samples": exported, "n": len(exported)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"thumbs={len(list(THUMB.glob('*.jpg')))}")
    print(f"hc_samples={len(exported)}")
    for et, rows in sorted(by_err.items(), key=lambda x: -len(x[1])):
        print(f"  {et}: n={len(rows)} top={rows[0]['conf']:.3f} t={rows[0]['timestamp_sec']}")


if __name__ == "__main__":
    main()
