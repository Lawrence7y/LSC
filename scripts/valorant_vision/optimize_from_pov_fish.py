#!/usr/bin/env python3
"""Build draft GT for POV live_test_84927583848 and inject hard examples.

POV streamer: Range/menu/leave → non_game; match play → combat/buy.
Model often mislabels Range as replay — primary hard negative.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path

LIVE = Path.home() / "LSC" / "datasets" / "valorant_phase" / "live_test_84927583848"
DATA = Path.home() / "LSC" / "datasets" / "valorant_phase"
ROOT = Path(__file__).resolve().parents[2]

# timestamp_sec -> label (visual)
EXPLICIT = {
    # Range practice (mis-pred as replay/buy/combat)
    2.0: "non_game",
    6.0: "non_game",
    10.0: "non_game",
    14.0: "non_game",
    18.0: "non_game",
    22.0: "non_game",
    26.0: "non_game",
    30.0: "non_game",
    34.0: "non_game",
    38.0: "non_game",
    42.0: "non_game",
    46.0: "non_game",
    50.0: "non_game",
    # leave-match dialog / lobby
    58.0: "non_game",
    62.0: "non_game",
    66.0: "non_game",
    70.0: "non_game",
    74.0: "non_game",
    # clear combat refs
    106.0: "combat",
    110.0: "combat",
}


def _link(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest.unlink()
    try:
        dest.hardlink_to(src)
    except OSError:
        shutil.copy2(src, dest)


def draft_gt(details: list[dict]) -> list[dict]:
    rows = []
    for d in details:
        t = float(d["timestamp_sec"])
        pred, conf = d["pred"], float(d["conf"])
        if t in EXPLICIT:
            gt, tag = EXPLICIT[t], "explicit"
        elif t <= 54.0:
            # early segment: Range / warm-up
            gt, tag = "non_game", "range_window"
        elif 54.0 < t <= 76.0:
            gt, tag = "non_game", "lobby_window"
        elif pred == "replay":
            # POV almost never has official REPLAY badge in this stream
            gt, tag = "combat", "pov_false_replay"
        elif pred == "result" and conf < 0.6:
            gt, tag = "combat", "low_result"
        elif pred == "buy" and conf < 0.7:
            gt, tag = "combat", "low_buy"
        elif conf >= 0.85:
            gt, tag = pred, "high_conf"
        elif pred == "combat" and conf >= 0.65:
            gt, tag = "combat", "med_combat"
        elif pred == "non_game" and conf >= 0.7:
            gt, tag = "non_game", "med_non_game"
        else:
            continue
        rows.append({**d, "gt": gt, "tag": tag})
    return rows


def main() -> None:
    # rebuild base dataset
    subprocess.check_call(
        [sys.executable, str(ROOT / "scripts/valorant_vision/build_broadcast_hard_dataset.py")],
        cwd=str(ROOT),
    )
    # keep previous yuezi live hard if present via force files already in tree from last run
    # — rebuild wipes train; re-inject yuezi critical fixes + this POV

    reports = []
    p1 = LIVE / "eval_report.json"
    if p1.is_file():
        reports.append(("part1", json.loads(p1.read_text(encoding="utf-8"))))
    p2 = LIVE / "part2" / "eval_report.json"
    if p2.is_file():
        reports.append(("part2", json.loads(p2.read_text(encoding="utf-8"))))

    all_rows = []
    for part, rep in reports:
        rows = draft_gt(rep["details"])
        for r in rows:
            r["part"] = part
        all_rows.extend(rows)

    # also re-inject prior yuezi live critical frames if still on disk
    yuezi = Path.home() / "LSC/datasets/valorant_phase/live_test_59475730286"
    yuezi_fix = [
        (78.0, "result"),
        (162.0, "non_game"),
        (370.0, "non_game"),
        (262.0, "combat"),
        (478.0, "non_game"),
    ]
    if (yuezi / "eval_report.json").is_file():
        yd = {
            float(d["timestamp_sec"]): d
            for d in json.loads((yuezi / "eval_report.json").read_text(encoding="utf-8"))[
                "details"
            ]
        }
        for t, lab in yuezi_fix:
            d = yd.get(t)
            if not d:
                continue
            src = Path(d["path"])
            if not src.is_file():
                continue
            for k in range(5):
                _link(src, DATA / "train" / lab / f"yuezi_keep_{lab}_t{int(t):04d}_x{k}.jpg")

    counts: Counter[str] = Counter()
    hard_n = 0
    for r in all_rows:
        src = Path(r["path"])
        if not src.is_file():
            continue
        gt = r["gt"]
        name = f"pov_fish_{r['part']}_t{int(r['timestamp_sec']):04d}_{gt}.jpg"
        _link(src, DATA / "train" / gt / name)
        counts[f"train/{gt}"] += 1
        # oversample disagreements & range-as-replay
        if r["pred"] != gt or r["tag"] in ("range_window", "pov_false_replay", "explicit"):
            ncopy = 5 if r["tag"] in ("range_window", "pov_false_replay", "explicit") else 3
            for k in range(ncopy):
                _link(
                    src,
                    DATA
                    / "train"
                    / gt
                    / f"hardpov{k}_{r['tag']}_{r['pred']}_to_{gt}_t{int(r['timestamp_sec']):04d}.jpg",
                )
                hard_n += 1
                counts[f"train/{gt}"] += 1

    (LIVE / "draft_gt.json").write_text(
        json.dumps(
            {
                "n": len(all_rows),
                "gt_dist": dict(Counter(r["gt"] for r in all_rows)),
                "tag_dist": dict(Counter(r["tag"] for r in all_rows)),
                "pred_vs_gt": dict(
                    Counter(f"{r['pred']}->{r['gt']}" for r in all_rows if r["pred"] != r["gt"])
                ),
                "items": all_rows,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    # accuracy of old model vs draft GT
    ok = sum(1 for r in all_rows if r["pred"] == r["gt"])
    print(
        json.dumps(
            {
                "draft_n": len(all_rows),
                "old_acc_vs_draft": round(ok / len(all_rows), 4) if all_rows else 0,
                "gt_dist": dict(Counter(r["gt"] for r in all_rows)),
                "errors": dict(
                    Counter(
                        f"{r['pred']}->{r['gt']}" for r in all_rows if r["pred"] != r["gt"]
                    )
                ),
                "hard_extra": hard_n,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    for lab in ("non_game", "buy", "combat", "result", "replay"):
        print(f"train/{lab}: {len(list((DATA / 'train' / lab).glob('*.jpg')))}")


if __name__ == "__main__":
    main()
