#!/usr/bin/env python3
"""Apply coarse interval labels then export manifest_labeled.jsonl.

Intervals are inclusive frame indices (ffmpeg fps=1/4 → timestamp=(idx-1)*4).
Refine with serve_label_ui.py for buy/result/replay boundaries.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path.home() / "LSC" / "datasets" / "valorant_phase" / "annotate"
QUEUE = ROOT / "queue.json"
LABELS = ROOT / "labels.json"
MANIFEST = ROOT / "manifest_labeled.jsonl"
INTERVAL = 4.0

# (start_idx, end_idx, label, notes) — 1-based frame indices matching frame_%06d.jpg
BROADCAST_INTERVALS: list[tuple[int, int, str, str]] = [
    (1, 3, "non_game", "stream interstitial / REPLAY cityscape"),
    (4, 22, "non_game", "tactical timeout + stage camera"),
    (23, 24, "non_game", "stage cam / monitor close-up transition"),
    (25, 118, "combat", "VCT broadcast combat POV (coarse; refine buy/result)"),
    (119, 125, "buy", "blue barrier / buy-phase markers around t~476s"),
    (126, 250, "combat", "post-buy combat through mid-map (coarse)"),
    (251, 276, "non_game", "HUG CAM / HALFTIME / arena crowd"),
]

# POV surveyed keyframes (every ~50–100 frames). Refine buy/result edges in UI.
POV_INTERVALS: list[tuple[int, int, str, str]] = [
    (1, 148, "combat", "early match combat (sampled)"),
    (149, 210, "buy", "购买阶段 banner ~t=596–800s"),
    (211, 380, "combat", "includes 赛点 late-round; refine result edges in UI"),
    (381, 450, "non_game", "agent select / lobby (Sunset)"),
    (451, 520, "buy", "new match buy phase score 0-0"),
    (521, 585, "combat", "post-buy combat"),
    (586, 615, "buy", "购买阶段 ~t=2340s"),
    (616, 875, "combat", "mid match combat"),
    (876, 930, "non_game", "settings menu overlay during stream"),
    (931, 1025, "buy", "购买阶段 + combat report overlay"),
    (1026, 1113, "combat", "late/OT combat (加时赛 sampled)"),
]


def apply_intervals(video_id: str, intervals: list[tuple[int, int, str, str]], labels: dict) -> int:
    n = 0
    for start, end, label, notes in intervals:
        for idx in range(start, end + 1):
            key = f"{video_id}_{idx:06d}"
            labels[key] = {
                "label": label,
                "notes": notes,
                "timestamp_sec": (idx - 1) * INTERVAL,
                "video_id": video_id,
                "annotator": "agent_interval_v1",
            }
            n += 1
    return n


def export_manifest(queue: list[dict], labels: dict) -> int:
    lines: list[str] = []
    for item in queue:
        lab = labels.get(item["id"])
        if not lab:
            continue
        row = {
            "video_id": item["video_id"],
            "video_path": item["video_path"],
            "timestamp_sec": item["timestamp_sec"],
            "label": lab["label"],
            "split": item["split"],
            "source_type": item["source_type"],
            "session_id": item["session_id"],
            "notes": lab.get("notes") or "",
        }
        lines.append(json.dumps(row, ensure_ascii=False))
    MANIFEST.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return len(lines)


def main() -> None:
    queue = json.loads(QUEUE.read_text(encoding="utf-8"))
    labels: dict = {}
    if LABELS.exists():
        labels = json.loads(LABELS.read_text(encoding="utf-8"))

    n1 = apply_intervals("broadcast_yuezi_20260720_202557", BROADCAST_INTERVALS, labels)
    n2 = apply_intervals("pov_ling_20260720_134749", POV_INTERVALS, labels)
    LABELS.write_text(json.dumps(labels, ensure_ascii=False, indent=2), encoding="utf-8")
    count = export_manifest(queue, labels)

    from collections import Counter

    c = Counter(v["label"] for v in labels.values())
    print(f"labeled keys: {len(labels)} (broadcast intervals wrote {n1}, pov {n2})")
    print(f"class counts: {dict(c)}")
    print(f"manifest rows: {count} -> {MANIFEST}")


if __name__ == "__main__":
    main()
