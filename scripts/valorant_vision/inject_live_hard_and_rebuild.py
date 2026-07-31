#!/usr/bin/env python3
"""Inject live_test hard examples (with visual GT fixes) into train and rebuild."""
from __future__ import annotations

import json
import shutil
from collections import Counter
from pathlib import Path

import cv2
import numpy as np

from lsc.analyzer.valorant_frame_classifier import _CLASS_NAMES

LIVE = Path.home() / "LSC" / "datasets" / "valorant_phase" / "live_test_59475730286"
DATA = Path.home() / "LSC" / "datasets" / "valorant_phase"
CODEX = Path(
    r"D:\Project\直播切片多人\.worktrees\valorant-frame-labeling-pilot"
    r"\datasets\valorant_phase\annotations\new_broadcast_20260721"
)
ANN = Path.home() / "LSC" / "datasets" / "valorant_phase" / "annotate"
BLIND = Path.home() / "LSC" / "datasets" / "valorant_phase" / "blind_pakki"

# Visual GT overrides for live_test (timestamp_sec -> label)
LIVE_OVERRIDES: dict[float, str] = {
    162.0: "non_game",  # desk cam, model said result
    370.0: "non_game",  # players stand up, model said result
}

# Extra oversample multipliers for live hard tags
LIVE_HARD_COPY = 5
RESULT_BANNER_COPY = 4
REPLAY_BADGE_COPY = 3


def imread(p: Path):
    data = np.fromfile(str(p), dtype=np.uint8)
    img = cv2.imdecode(data, cv2.IMREAD_COLOR)
    return img if img is not None else cv2.imread(str(p))


def _link(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest.unlink()
    try:
        dest.hardlink_to(src)
    except OSError:
        shutil.copy2(src, dest)


def main() -> None:
    # First rebuild base via existing script logic by calling it
    import subprocess
    import sys

    root = Path(__file__).resolve().parents[2]
    subprocess.check_call(
        [sys.executable, str(root / "scripts/valorant_vision/build_broadcast_hard_dataset.py")],
        cwd=str(root),
    )

    report = json.loads((LIVE / "eval_report.json").read_text(encoding="utf-8"))
    details = report["details"]
    live_labels = []
    for d in details:
        t = float(d["timestamp_sec"])
        if t in LIVE_OVERRIDES:
            gt = LIVE_OVERRIDES[t]
            tag = "override"
        elif d["conf"] >= 0.90:
            gt = d["pred"]
            tag = "high_conf"
        elif d["conf"] >= 0.80 and d["pred"] in ("combat", "buy", "replay"):
            gt = d["pred"]
            tag = "med_conf"
        else:
            continue  # skip ambiguous
        live_labels.append({**d, "gt": gt, "tag": tag})

    # Force-include override frames even if already covered
    for d in details:
        t = float(d["timestamp_sec"])
        if t in LIVE_OVERRIDES and not any(x["timestamp_sec"] == t for x in live_labels):
            live_labels.append({**d, "gt": LIVE_OVERRIDES[t], "tag": "override"})

    counts: Counter[str] = Counter()
    hard_pairs = []

    for row in live_labels:
        src = Path(row["path"])
        if not src.is_file():
            continue
        gt = row["gt"]
        name = f"live_yuezi_t{int(row['timestamp_sec']):04d}_{gt}.jpg"
        dest = DATA / "train" / gt / name
        _link(src, dest)
        counts[f"train/{gt}"] += 1

        # hard: model disagreed with GT, or was result/stage confusion
        if row["pred"] != gt or row["tag"] == "override":
            hard_pairs.append((src, gt, f"livehard_{row['pred']}_to_{gt}_t{int(row['timestamp_sec']):04d}.jpg"))
        elif gt == "result":
            hard_pairs.append((src, gt, f"liveresult_t{int(row['timestamp_sec']):04d}.jpg"))
        elif gt == "replay" and row["conf"] >= 0.95:
            hard_pairs.append((src, gt, f"livereplay_t{int(row['timestamp_sec']):04d}.jpg"))

    # Also hard-mine current model on live GT set again after base rebuild? use stored pred
    extra = 0
    for src, gt, tag in hard_pairs:
        ncopy = LIVE_HARD_COPY
        if gt == "result":
            ncopy = RESULT_BANNER_COPY
        if gt == "replay":
            ncopy = REPLAY_BADGE_COPY
        if "to_non_game" in tag:
            ncopy = LIVE_HARD_COPY + 2
        for k in range(ncopy):
            dest = DATA / "train" / gt / f"hardlive{k}_{tag}"
            _link(src, dest)
            extra += 1
            counts[f"train/{gt}"] += 1

    # Persist live GT for later eval
    (LIVE / "draft_gt.json").write_text(
        json.dumps(
            {
                "n": len(live_labels),
                "overrides": {str(k): v for k, v in LIVE_OVERRIDES.items()},
                "gt_dist": dict(Counter(x["gt"] for x in live_labels)),
                "items": live_labels,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(f"live_labeled={len(live_labels)} hard_pairs={len(hard_pairs)} extra={extra}")
    print("live_gt_dist", dict(Counter(x["gt"] for x in live_labels)))
    for lab in _CLASS_NAMES:
        n = len(list((DATA / "train" / lab).glob("*.jpg")))
        print(f"  train/{lab}: {n}")
    print(f"  val total: {sum(len(list((DATA/'val'/lab).glob('*.jpg'))) for lab in _CLASS_NAMES)}")
    print(f"  test total: {sum(len(list((DATA/'test'/lab).glob('*.jpg'))) for lab in _CLASS_NAMES)}")


if __name__ == "__main__":
    main()
