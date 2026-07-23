#!/usr/bin/env python3
"""Apply coarse interval label candidates to a labeling queue.

Intervals use half-open [start_sec, end_sec) semantics. Human labels
(annotator == "human") are never overwritten. Refine boundaries with
serve_label_ui.py before training export.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

VALID_LABELS = {"non_game", "buy", "combat", "result", "replay"}


def label_at(intervals: list[dict], timestamp_sec: float) -> dict | None:
    for interval in intervals:
        label = str(interval["label"])
        if label not in VALID_LABELS:
            raise ValueError(f"invalid label: {label}")
        if float(interval["start_sec"]) <= timestamp_sec < float(interval["end_sec"]):
            return interval
    return None


def apply_interval_candidates(queue: list[dict], labels: dict, intervals_by_video: dict) -> int:
    written = 0
    for item in queue:
        current = labels.get(item["id"])
        if current and current.get("annotator") == "human":
            continue
        interval = label_at(intervals_by_video.get(item["video_id"], []), item["timestamp_sec"])
        if interval is None:
            continue
        labels[item["id"]] = {
            "label": interval["label"],
            "notes": interval.get("notes", ""),
            "annotator": "interval_candidate_v1",
        }
        written += 1
    return written


def export_manifest(queue: list[dict], labels: dict, output_path: Path) -> int:
    """Write human-reviewed rows only; candidates never enter training manifests."""
    lines: list[str] = []
    for item in queue:
        lab = labels.get(item["id"])
        if not lab or lab.get("annotator") != "human":
            continue
        row = {
            "video_id": item["video_id"],
            "video_path": item.get("video_path", ""),
            "timestamp_sec": item["timestamp_sec"],
            "label": lab["label"],
            "split": item.get("split", ""),
            "source_type": item.get("source_type", ""),
            "session_id": item.get("session_id", ""),
            "notes": lab.get("notes") or "",
        }
        lines.append(json.dumps(row, ensure_ascii=False))
    text = "\n".join(lines) + ("\n" if lines else "")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(text, encoding="utf-8")
    return len(lines)


def _atomic_write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply interval label candidates to a queue")
    parser.add_argument("--queue", type=Path, required=True, help="Labeling queue JSON")
    parser.add_argument("--intervals", type=Path, required=True, help="video_id -> intervals JSON")
    parser.add_argument("--labels", type=Path, required=True, help="Labels JSON (read/write)")
    parser.add_argument("--manifest", type=Path, help="Optional manifest_labeled.jsonl output")
    args = parser.parse_args()

    queue = json.loads(args.queue.read_text(encoding="utf-8"))
    intervals_by_video = json.loads(args.intervals.read_text(encoding="utf-8"))
    labels: dict = {}
    if args.labels.exists():
        labels = json.loads(args.labels.read_text(encoding="utf-8"))

    written = apply_interval_candidates(queue, labels, intervals_by_video)
    _atomic_write_json(args.labels, labels)

    manifest_rows = 0
    if args.manifest is not None:
        manifest_rows = export_manifest(queue, labels, args.manifest)

    print(f"interval candidates written: {written}")
    print(f"total labeled items: {sum(1 for item in queue if item['id'] in labels)}")
    if args.manifest is not None:
        print(f"manifest rows: {manifest_rows} -> {args.manifest}")


if __name__ == "__main__":
    main()
