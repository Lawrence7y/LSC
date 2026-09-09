#!/usr/bin/env python3
"""Extract a recording directory into an unlabeled source manifest.

The output is intended for ``prepare_label_ui_dataset.py``. It keeps the
original video path in each row, but leaves ``label`` empty until a human
reviews the frame in ``serve_label_ui``.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

VIDEO_SUFFIXES = {".mp4", ".mkv", ".flv", ".mov", ".ts"}


def _video_id(video_path: Path) -> str:
    match = re.search(r"(\d{4}-\d{2}-\d{2})_(\d{2})-(\d{2})-(\d{2})", video_path.stem)
    if match:
        date, hour, minute, second = match.groups()
        return f"edg_{date.replace('-', '')}_{hour}{minute}{second}"
    safe = re.sub(r"[^0-9A-Za-z]+", "_", video_path.stem).strip("_")
    return f"edg_{safe or 'recording'}"


def _extract(video_path: Path, frame_dir: Path, *, interval: float, scale: int) -> list[Path]:
    frame_dir.mkdir(parents=True, exist_ok=True)
    existing = sorted(frame_dir.glob("frame_*.jpg"))
    if not existing:
        subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(video_path),
                "-vf",
                f"fps=1/{interval:g},scale={scale}:-2",
                "-q:v",
                "3",
                str(frame_dir / "frame_%06d.jpg"),
            ],
            check=True,
        )
    return sorted(frame_dir.glob("frame_*.jpg"))


def build_manifest(
    video_dir: Path,
    frame_root: Path,
    *,
    interval: float = 4.0,
    scale: int = 960,
    source_type: str = "broadcast",
) -> list[dict]:
    if interval <= 0 or scale <= 0:
        raise ValueError("interval and scale must be positive")
    videos = sorted(
        path for path in video_dir.iterdir()
        if path.is_file() and path.suffix.lower() in VIDEO_SUFFIXES
    )
    if not videos:
        raise ValueError(f"no supported videos under {video_dir}")

    rows: list[dict] = []
    for video_path in videos:
        video_id = _video_id(video_path)
        session_id = f"edg_champion_review_{video_id.removeprefix('edg_')}"
        frame_dir = frame_root / video_id
        frames = _extract(video_path, frame_dir, interval=interval, scale=scale)
        for index, frame_path in enumerate(frames):
            rows.append(
                {
                    "video_id": video_id,
                    "video_path": str(video_path.resolve()),
                    "frame_path": str(frame_path.resolve()),
                    "timestamp_sec": round(index * interval, 3),
                    "label": None,
                    "split": "train",
                    "source_type": source_type,
                    "session_id": session_id,
                    "notes": "EDG夺冠回顾新录制，等待人工标注",
                }
            )
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="将录制目录抽帧为未标注数据集 manifest")
    parser.add_argument("--video-dir", type=Path, required=True)
    parser.add_argument("--frame-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--interval", type=float, default=4.0)
    parser.add_argument("--scale", type=int, default=960)
    args = parser.parse_args(argv)

    rows = build_manifest(
        args.video_dir.resolve(),
        args.frame_root.resolve(),
        interval=args.interval,
        scale=args.scale,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    tmp = args.output.with_suffix(args.output.suffix + ".tmp")
    tmp.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )
    tmp.replace(args.output)
    print(f"videos={len({row['video_id'] for row in rows})} frames={len(rows)} output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
