#!/usr/bin/env python3
"""Build a source-balanced labeling queue from session JSON and coarse frames.

Usage:
  python scripts/valorant_vision/build_label_queue.py \\
    --sessions sessions.json --frame-root annotations --output queue.json
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

VALID_SOURCES = {"pov", "broadcast"}


@dataclass(frozen=True)
class VideoSession:
    video_id: str
    video_path: str
    frame_dir: Path
    source_type: str
    session_id: str
    timestamp_offset_sec: float = 0.0

    @classmethod
    def from_dict(cls, row: dict) -> VideoSession:
        source = str(row["source_type"])
        if source not in VALID_SOURCES:
            raise ValueError(f"invalid source_type: {source}")
        return cls(
            video_id=str(row["video_id"]),
            video_path=str(row["video_path"]),
            frame_dir=Path(row["frame_dir"]),
            source_type=source,
            session_id=str(row["session_id"]),
            timestamp_offset_sec=float(row.get("timestamp_offset_sec", 0.0)),
        )


def frame_index(path: Path) -> int:
    m = re.search(r"(\d+)", path.stem)
    if not m:
        raise ValueError(path)
    return int(m.group(1))


def choose_split(timestamp_sec: float, duration_sec: float, *, gap_sec: float = 30.0) -> str | None:
    if duration_sec <= 0 or not 0 <= timestamp_sec <= duration_sec:
        raise ValueError("timestamp/duration out of range")
    train_end = duration_sec * 0.65
    val_end = duration_sec * 0.80
    half_gap = gap_sec / 2.0
    if abs(timestamp_sec - train_end) <= half_gap or abs(timestamp_sec - val_end) <= half_gap:
        return None
    if timestamp_sec < train_end:
        return "train"
    if timestamp_sec < val_end:
        return "val"
    return "test"


def build_queue(
    sessions: list[VideoSession],
    durations: dict[str, float],
    *,
    frame_root: Path,
    interval_sec: float,
    gap_sec: float = 30.0,
) -> list[dict]:
    rows: list[dict] = []
    for session in sessions:
        duration = durations[session.video_id]
        for frame in sorted(session.frame_dir.glob("frame_*.jpg")):
            index = frame_index(frame)
            timestamp = (index - 1) * interval_sec + session.timestamp_offset_sec
            split = choose_split(timestamp, duration, gap_sec=gap_sec)
            if split is None:
                continue
            rows.append({
                "id": f"{session.video_id}_{int(round(timestamp * 1000)):010d}",
                "rel_path": frame.resolve().relative_to(frame_root.resolve()).as_posix(),
                "abs_path": str(frame.resolve()),
                "video_id": session.video_id,
                "video_path": session.video_path,
                "timestamp_sec": timestamp,
                "source_type": session.source_type,
                "session_id": session.session_id,
                "split": split,
            })
    return rows


def _ensure_under_root(path: Path, root: Path) -> None:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"frame_dir outside frame_root: {path}") from exc


def probe_duration(video_path: str) -> float:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=nw=1:nk=1",
            video_path,
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    duration = float(result.stdout.strip())
    if duration <= 0:
        raise ValueError(f"non-positive duration for {video_path}")
    return duration


def main() -> None:
    parser = argparse.ArgumentParser(description="Build Valorant phase label queue")
    parser.add_argument("--sessions", required=True, type=Path, help="JSON file listing video sessions")
    parser.add_argument("--frame-root", required=True, type=Path, help="Root directory for frame images")
    parser.add_argument("--output", required=True, type=Path, help="Output queue JSON path")
    parser.add_argument("--interval", required=True, type=float, help="Seconds between extracted frames")
    parser.add_argument("--gap-sec", type=float, default=30.0, help="Gap around split boundaries (seconds)")
    args = parser.parse_args()

    frame_root = args.frame_root.resolve()
    raw_sessions = json.loads(args.sessions.read_text(encoding="utf-8"))
    sessions = [VideoSession.from_dict(row) for row in raw_sessions]

    for session in sessions:
        _ensure_under_root(session.frame_dir, frame_root)

    durations = {session.video_id: probe_duration(session.video_path) for session in sessions}
    rows = build_queue(
        sessions,
        durations,
        frame_root=frame_root,
        interval_sec=args.interval,
        gap_sec=args.gap_sec,
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {len(rows)} items -> {args.output}")


if __name__ == "__main__":
    main()
