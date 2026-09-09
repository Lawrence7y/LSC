#!/usr/bin/env python3
"""Rebuild source-separated datasets for Valorant phase recognition:
- datasets/valorant_phase_broadcast (train / val / test)
- datasets/valorant_phase_pov (train / val / test)

Strictly adheres to manifest_schema.md:
1. Grouping by session_id / video_id without intra-session frame leakage between splits.
2. Clean evaluation sets (val and test have unique frames only, no duplicated oversamples).
3. Manifests output as manifest_broadcast.jsonl and manifest_pov.jsonl.
"""
from __future__ import annotations

import json
import re
import shutil
from collections import Counter
from pathlib import Path

CLASSES = ("non_game", "buy", "combat", "result", "replay")
ROOT = Path(__file__).resolve().parents[2]
SOURCE_DATA = ROOT / "datasets" / "valorant_phase"
OUT_BROADCAST = ROOT / "datasets" / "valorant_phase_broadcast"
OUT_POV = ROOT / "datasets" / "valorant_phase_pov"
MANIFEST_BCAST = ROOT / "scripts" / "valorant_vision" / "manifest_broadcast.jsonl"
MANIFEST_POV = ROOT / "scripts" / "valorant_vision" / "manifest_pov.jsonl"


def _is_broadcast(stem: str) -> bool:
    if "broadcast" in stem or stem.startswith("pakki_"):
        return True
    if any(k in stem for k in ("yuezi", "valorant_esports", "hanghang", "binggan", "pakki")):
        return True
    return False


def _extract_broadcast_session(stem: str) -> tuple[str, bool]:
    """Returns (session_id, is_raw_unique)."""
    is_oversample = bool(re.match(r"^(rarex\d+_|replay_boost\d+_)+", stem))
    if "yuezi" in stem:
        sess = "yuezi_20260720_202557"
    elif "valorant_esports" in stem:
        sess = "valorant_esports_20260721"
    elif "hanghang" in stem:
        sess = "hanghang_20260721"
    elif "binggan" in stem:
        sess = "binggan_20260731_181220"
    elif "pakki" in stem:
        sess = "pakki_tournament"
    else:
        sess = "broadcast_unknown"
    return sess, not is_oversample


def _extract_pov_session(stem: str) -> tuple[str, bool]:
    """Returns (session_id, is_raw_unique)."""
    is_oversample = bool(re.match(r"^(rarex\d+_|hardpov\d+_|low_)+", stem))
    if "ling" in stem:
        sess = "ling_20260720_134749"
    elif "tangqihua" in stem:
        sess = "tangqihua_20260721_141301"
    elif "fish" in stem:
        sess = "fish_live"
    elif any(k in stem for k in ("hardpov", "low_", "false_replay")):
        sess = "hard_pov_mined"
    else:
        sess = "pov_other"
    return sess, not is_oversample


def _extract_timestamp(stem: str) -> float:
    # Hard-mined copies use names such as ``yuezi_keep_combat_t0262_x4``.
    # The ``t`` token is the original timestamp; the trailing ``x4`` is only
    # the oversample index and must never become timestamp 4s.
    tagged = re.search(r"(?:^|_)t(\d{4})(?:_|$)", stem)
    if tagged:
        return float(int(tagged.group(1)))

    trailing = re.search(r"(\d+)$", stem)
    if trailing:
        val = int(trailing.group(1))
        # If milliseconds (e.g. 1000000 -> 1000.0s)
        if val >= 10000:
            return round(val / 1000.0, 3)
        return float(val)
    return 0.0


def _assign_split(source_type: str, session_id: str) -> str:
    """Assign one split to a whole recording session.

    A source session must never be split by timestamp.  Doing so makes
    adjacent frames from the same broadcast appear in both train and val and
    gives an over-optimistic domain score.
    """
    if source_type == "broadcast":
        if session_id in {
            "yuezi_20260720_202557",
            "valorant_esports_20260721",
        }:
            return "train"
        if session_id in {"hanghang_20260721", "binggan_20260731_181220"}:
            return "val"
        if session_id == "pakki_tournament":
            return "test"
        return "train"

    if session_id in {"ling_20260720_134749", "hard_pov_mined", "pov_other"}:
        return "train"
    if session_id == "tangqihua_20260721_141301":
        return "val"
    if session_id == "fish_live":
        return "test"
    return "train"


def main() -> None:
    print(f"Scanning source data from {SOURCE_DATA}...")
    all_files = list(SOURCE_DATA.glob("*/*/*.jpg"))
    if not all_files:
        print(f"Error: No image files found in {SOURCE_DATA}")
        return

    # Clean destination directories
    for out_dir in (OUT_BROADCAST, OUT_POV):
        if out_dir.exists():
            shutil.rmtree(out_dir)
        for split in ("train", "val", "test"):
            for cls in CLASSES:
                (out_dir / split / cls).mkdir(parents=True, exist_ok=True)

    bcast_records = []
    pov_records = []

    bcast_counts = Counter()
    pov_counts = Counter()

    # Track unique frames in val/test to prevent duplicate copies
    bcast_val_seen = set()
    bcast_test_seen = set()
    pov_val_seen = set()
    pov_test_seen = set()

    for file_path in all_files:
        stem = file_path.stem
        label = file_path.parent.name
        if label not in CLASSES:
            continue

        if _is_broadcast(stem):
            session_id, _ = _extract_broadcast_session(stem)
            ts_sec = _extract_timestamp(stem)
            unique_key = f"{session_id}_{int(round(ts_sec * 1000))}"

            split = _assign_split("broadcast", session_id)
            if split == "val":
                if unique_key in bcast_val_seen:
                    continue
                bcast_val_seen.add(unique_key)
            elif split == "test":
                if unique_key in bcast_test_seen:
                    continue
                bcast_test_seen.add(unique_key)

            dest_name = f"{session_id}_{int(round(ts_sec * 1000))}_{stem}.jpg"
            if len(dest_name) > 120:
                dest_name = f"{session_id}_{int(round(ts_sec * 1000))}_{file_path.name}"
            dest_path = OUT_BROADCAST / split / label / dest_name
            shutil.copy2(file_path, dest_path)

            bcast_counts[f"{split}/{label}"] += 1
            bcast_records.append({
                "video_id": session_id,
                # The source tree contains materialized frames, not the
                # original videos. Keep the distinction explicit instead of
                # pretending a JPEG is a source video path.
                "video_path": None,
                "frame_path": str(dest_path.as_posix()),
                "timestamp_sec": ts_sec,
                "label": label,
                "split": split,
                "source_type": "broadcast",
                "session_id": session_id,
                "notes": f"source_stem={stem}",
            })
        else:
            session_id, _ = _extract_pov_session(stem)
            ts_sec = _extract_timestamp(stem)
            unique_key = f"{session_id}_{int(round(ts_sec * 1000))}"

            split = _assign_split("pov", session_id)
            if split == "val":
                if unique_key in pov_val_seen:
                    continue
                pov_val_seen.add(unique_key)
            elif split == "test":
                if unique_key in pov_test_seen:
                    continue
                pov_test_seen.add(unique_key)

            dest_name = f"{session_id}_{int(round(ts_sec * 1000))}_{stem}.jpg"
            if len(dest_name) > 120:
                dest_name = f"{session_id}_{int(round(ts_sec * 1000))}_{file_path.name}"
            dest_path = OUT_POV / split / label / dest_name
            shutil.copy2(file_path, dest_path)

            pov_counts[f"{split}/{label}"] += 1
            pov_records.append({
                "video_id": session_id,
                "video_path": None,
                "frame_path": str(dest_path.as_posix()),
                "timestamp_sec": ts_sec,
                "label": label,
                "split": split,
                "source_type": "pov",
                "session_id": session_id,
                "notes": f"source_stem={stem}",
            })

    # Write JSONL manifests
    with open(MANIFEST_BCAST, "w", encoding="utf-8") as f:
        for r in bcast_records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"\nManifest written: {MANIFEST_BCAST} ({len(bcast_records)} records)")

    with open(MANIFEST_POV, "w", encoding="utf-8") as f:
        for r in pov_records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"Manifest written: {MANIFEST_POV} ({len(pov_records)} records)")

    # Print summaries
    print("\n" + "=" * 60)
    print("BROADCAST DATASET SUMMARY (datasets/valorant_phase_broadcast)")
    print("=" * 60)
    for split in ("train", "val", "test"):
        total = sum(v for k, v in bcast_counts.items() if k.startswith(f"{split}/"))
        print(f"[{split.upper()}] Total: {total}")
        for cls in CLASSES:
            print(f"  {cls:<10}: {bcast_counts[f'{split}/{cls}']}")

    print("\n" + "=" * 60)
    print("POV DATASET SUMMARY (datasets/valorant_phase_pov)")
    print("=" * 60)
    for split in ("train", "val", "test"):
        total = sum(v for k, v in pov_counts.items() if k.startswith(f"{split}/"))
        print(f"[{split.upper()}] Total: {total}")
        for cls in CLASSES:
            print(f"  {cls:<10}: {pov_counts[f'{split}/{cls}']}")


if __name__ == "__main__":
    main()
