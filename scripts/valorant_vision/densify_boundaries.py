#!/usr/bin/env python3
"""Densify phase boundary frames around human-reviewed label transitions.

Usage:
  python scripts/valorant_vision/densify_boundaries.py \\
    --queue annotations/queue.json \\
    --labels annotations/labels.json \\
    --sessions annotations/sessions.json \\
    --output-root annotations/frames_dense \\
    --radius 3 --step 0.5
"""
from __future__ import annotations

import argparse
import json
import logging
import math
from collections import defaultdict
from pathlib import Path

from build_label_queue import VideoSession, probe_duration
from extract_frames import ManifestRow, extract_single_frame

_log = logging.getLogger(__name__)

_PLACEHOLDER_LABEL = "non_game"


def transition_centers(queue: list[dict], labels: dict) -> list[tuple[str, str, float]]:
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for item in queue:
        label = labels.get(item["id"])
        if label and label.get("annotator") == "human":
            groups[(item["video_id"], item["split"])].append(item)
    out: list[tuple[str, str, float]] = []
    for (video_id, split), items in groups.items():
        ordered = sorted(items, key=lambda row: row["timestamp_sec"])
        for left, right in zip(ordered, ordered[1:]):
            if labels[left["id"]]["label"] != labels[right["id"]]["label"]:
                out.append((video_id, split, (left["timestamp_sec"] + right["timestamp_sec"]) / 2.0))
    return out


def dense_timestamps(
    centers: list[float],
    *,
    duration: float,
    radius: float = 3.0,
    step: float = 0.5,
) -> list[float]:
    if not math.isfinite(duration) or duration < 0:
        raise ValueError("duration must be a finite non-negative number")
    if not math.isfinite(radius) or radius < 0:
        raise ValueError("radius must be a finite non-negative number")
    if not math.isfinite(step) or step <= 0:
        raise ValueError("step must be a finite positive number")

    values: set[float] = set()
    for center in centers:
        current = max(0.0, center - radius)
        end = min(duration, center + radius)
        while current <= end + 1e-9:
            values.add(round(current, 3))
            current += step
    return sorted(values)


def _atomic_write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _dense_frame_path(output_root: Path, video_id: str, timestamp_sec: float) -> Path:
    ts_ms = int(round(timestamp_sec * 1000))
    return output_root / video_id / f"{video_id}_{ts_ms:010d}.jpg"


def _queue_item_id(video_id: str, timestamp_sec: float) -> str:
    return f"{video_id}_{int(round(timestamp_sec * 1000)):010d}"


def densify_queue(
    queue: list[dict],
    labels: dict,
    sessions: list[VideoSession],
    *,
    output_root: Path,
    durations: dict[str, float],
    radius: float,
    step: float,
    ffmpeg: str,
    timeout_sec: float,
    dry_run: bool,
) -> int:
    sessions_by_id = {session.video_id: session for session in sessions}
    existing_ids = {item["id"] for item in queue}
    annotations_root = output_root.parent.resolve()
    added = 0

    for video_id, split, center in transition_centers(queue, labels):
        session = sessions_by_id.get(video_id)
        if session is None:
            _log.warning("跳过未知 video_id: %s", video_id)
            continue
        duration = durations[video_id]
        timestamps = dense_timestamps([center], duration=duration, radius=radius, step=step)
        for timestamp_sec in timestamps:
            item_id = _queue_item_id(video_id, timestamp_sec)
            if item_id in existing_ids:
                continue
            dest = _dense_frame_path(output_root, video_id, timestamp_sec)
            row = ManifestRow(
                video_id=video_id,
                video_path=session.video_path,
                timestamp_sec=timestamp_sec,
                label=_PLACEHOLDER_LABEL,
                split=split,
                source_type=session.source_type,
                session_id=session.session_id,
            )
            if dry_run:
                _log.info("dry-run %s @ %.3fs -> %s", session.video_path, timestamp_sec, dest)
                existing_ids.add(item_id)
                added += 1
                continue
            extract_single_frame(
                row,
                dest,
                ffmpeg=ffmpeg,
                timeout_sec=timeout_sec,
            )
            queue.append({
                "id": item_id,
                "rel_path": dest.resolve().relative_to(annotations_root).as_posix(),
                "abs_path": str(dest.resolve()),
                "video_id": video_id,
                "video_path": session.video_path,
                "timestamp_sec": timestamp_sec,
                "source_type": session.source_type,
                "session_id": session.session_id,
                "split": split,
            })
            existing_ids.add(item_id)
            added += 1
    return added


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Densify Valorant phase boundary frames")
    parser.add_argument("--queue", type=Path, required=True, help="Labeling queue JSON (read/write)")
    parser.add_argument("--labels", type=Path, required=True, help="Human labels JSON")
    parser.add_argument("--sessions", type=Path, required=True, help="Video sessions JSON")
    parser.add_argument("--output-root", type=Path, required=True, help="Dense frame output root")
    parser.add_argument("--radius", type=float, default=3.0, help="Seconds around each transition center")
    parser.add_argument("--step", type=float, default=0.5, help="Dense sampling step in seconds")
    parser.add_argument("--ffmpeg", default="ffmpeg", help="FFmpeg executable path")
    parser.add_argument("--timeout", type=float, default=120.0, help="Per-frame FFmpeg timeout seconds")
    parser.add_argument("--dry-run", action="store_true", help="Plan frames without calling FFmpeg")
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )

    queue = json.loads(args.queue.read_text(encoding="utf-8"))
    labels = json.loads(args.labels.read_text(encoding="utf-8"))
    raw_sessions = json.loads(args.sessions.read_text(encoding="utf-8"))
    sessions = [VideoSession.from_dict(row) for row in raw_sessions]
    durations = {session.video_id: probe_duration(session.video_path) for session in sessions}

    try:
        added = densify_queue(
            queue,
            labels,
            sessions,
            output_root=args.output_root.resolve(),
            durations=durations,
            radius=args.radius,
            step=args.step,
            ffmpeg=args.ffmpeg,
            timeout_sec=args.timeout,
            dry_run=args.dry_run,
        )
    except (RuntimeError, ValueError) as exc:
        _log.error("%s", exc)
        return 1

    if not args.dry_run:
        _atomic_write_json(args.queue, queue)
    print(f"added {added} dense queue items -> {args.queue}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
