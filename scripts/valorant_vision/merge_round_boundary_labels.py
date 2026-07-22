"""Merge human round-boundary phase labels into train/val dataset layout.

Reads a labeling queue + labels JSON, validates rows, splits each recording by
continuous time block (no random adjacent-frame leakage), and writes frames plus
manifests under ``--out-dir``. Blind/test paths may be read but are never written.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

VALID_LABELS = frozenset({"non_game", "buy", "combat", "result", "replay"})
_PROTECTED_DIR_NAMES = frozenset({"blind", "test"})


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_protected_path(path: Path) -> bool:
    return any(part.lower() in _PROTECTED_DIR_NAMES for part in path.parts)


def _assert_writable_out_dir(path: Path) -> None:
    resolved = path.resolve()
    if _is_protected_path(resolved):
        raise ValueError(f"refusing to write into blind/test path: {resolved}")


def validate_labels(rows: list[dict[str, Any]]) -> None:
    """Validate labeled rows: legal labels, existing frames, unique ids and timestamps."""

    seen_ids: set[str] = set()
    seen_video_times: set[tuple[str, float]] = set()
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise ValueError(f"row {index}: expected object")
        row_id = str(row.get("id") or "")
        if not row_id:
            raise ValueError(f"row {index}: missing id")
        if row_id in seen_ids:
            raise ValueError(f"duplicate id: {row_id}")
        seen_ids.add(row_id)

        label = row.get("label")
        if label not in VALID_LABELS:
            raise ValueError(f"invalid label for {row_id}: {label!r}")

        video_id = str(row.get("video_id") or "")
        if not video_id:
            raise ValueError(f"row {index}: missing video_id")
        try:
            timestamp = float(row["timestamp_sec"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"row {index}: missing or invalid timestamp_sec") from exc
        if not math.isfinite(timestamp):
            raise ValueError(f"row {index}: timestamp_sec must be finite")

        video_time = (video_id, round(timestamp, 3))
        if video_time in seen_video_times:
            raise ValueError(
                f"duplicate (video_id, timestamp_sec): {video_id} @ {timestamp:.3f}"
            )
        seen_video_times.add(video_time)

        abs_path = row.get("abs_path")
        if not abs_path:
            raise ValueError(f"missing frame path for {row_id}")
        frame_path = Path(str(abs_path))
        if not frame_path.is_file():
            raise ValueError(f"missing frame file for {row_id}: {frame_path}")


def split_by_time_block(
    rows: list[dict[str, Any]],
    *,
    validation_fraction: float = 1 / 3,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split each recording into contiguous train/val blocks by timestamp order."""

    if not 0.0 < validation_fraction < 1.0:
        raise ValueError("validation_fraction must be between 0 and 1")

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["video_id"])].append(row)

    train: list[dict[str, Any]] = []
    val: list[dict[str, Any]] = []
    for video_id in sorted(grouped):
        session_rows = sorted(grouped[video_id], key=lambda item: float(item["timestamp_sec"]))
        count = len(session_rows)
        if count == 0:
            continue
        val_count = max(1, int(round(count * validation_fraction))) if count > 1 else 0
        val_count = min(val_count, count - 1) if count > 1 else 0
        split_at = count - val_count
        train.extend(session_rows[:split_at])
        val.extend(session_rows[split_at:])
    return train, val


def _frame_filename(row: dict[str, Any]) -> str:
    video_id = str(row["video_id"])
    timestamp_ms = int(round(float(row["timestamp_sec"]) * 1000.0))
    return f"{video_id}_{timestamp_ms}.jpg"


def _copy_frame(row: dict[str, Any], destination: Path, *, link: bool = False) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    source = Path(str(row["abs_path"]))
    if link:
        if destination.exists():
            destination.unlink()
        destination.symlink_to(source)
    else:
        shutil.copy2(source, destination)
    return destination


def _load_round_manifest_draft(queue_path: Path) -> list[dict[str, Any]]:
    draft_path = queue_path.parent / "round_manifest.draft.json"
    if not draft_path.is_file():
        return []
    data = json.loads(draft_path.read_text(encoding="utf-8"))
    videos = data.get("videos") if isinstance(data, dict) else None
    if not isinstance(videos, list):
        return []
    return videos


def _load_confirmed_rounds(labels_path: Path) -> list[dict[str, Any]] | None:
    """Optional human-confirmed rounds beside labels.json (rounds_confirmed.json)."""

    confirmed_path = labels_path.parent / "rounds_confirmed.json"
    if not confirmed_path.is_file():
        return None
    data = json.loads(confirmed_path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        videos = data.get("videos")
        if isinstance(videos, list):
            return videos
    raise ValueError(f"invalid rounds_confirmed.json: {confirmed_path}")


def _build_labeled_rows(queue: list[dict[str, Any]], labels: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in queue:
        if not isinstance(item, dict):
            raise ValueError("queue entries must be objects")
        item_id = str(item.get("id") or "")
        label_entry = labels.get(item_id)
        if not label_entry:
            continue
        if not isinstance(label_entry, dict):
            raise ValueError(f"labels[{item_id}] must be an object")
        rows.append({
            "id": item_id,
            "label": label_entry["label"],
            "abs_path": item.get("abs_path") or "",
            "video_id": item.get("video_id") or "",
            "video_path": item.get("video_path") or "",
            "timestamp_sec": item.get("timestamp_sec"),
            "session_id": item.get("session_id") or item.get("video_id") or "",
            "source_type": item.get("source_type") or "",
            "notes": label_entry.get("notes") or "",
        })
    return rows


def merge_labels(
    *,
    queue_path: Path,
    labels_path: Path,
    data_dir: Path,
    out_dir: Path,
    validation_fraction: float = 1 / 3,
    link_frames: bool = False,
) -> dict[str, Any]:
    data_dir = data_dir.resolve()
    if not data_dir.is_dir():
        raise ValueError(f"data-dir does not exist: {data_dir}")
    # data_dir is audit metadata + read-only context; never write under it.
    _assert_writable_out_dir(out_dir)

    queue = json.loads(queue_path.read_text(encoding="utf-8"))
    if not isinstance(queue, list):
        raise ValueError("queue must be a JSON array")
    labels = json.loads(labels_path.read_text(encoding="utf-8"))
    if not isinstance(labels, dict):
        raise ValueError("labels must be a JSON object")

    labeled_rows = _build_labeled_rows(queue, labels)
    if not labeled_rows:
        raise ValueError("no labeled rows to merge")
    validate_labels(labeled_rows)
    train_rows, val_rows = split_by_time_block(
        labeled_rows, validation_fraction=validation_fraction
    )

    out_dir = out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    for split_name, split_rows in (("train", train_rows), ("val", val_rows)):
        for row in split_rows:
            label = str(row["label"])
            destination = out_dir / split_name / label / _frame_filename(row)
            _assert_writable_out_dir(destination.parent)
            _copy_frame(row, destination, link=link_frames)

    label_counts = Counter(str(row["label"]) for row in labeled_rows)
    timestamps = [float(row["timestamp_sec"]) for row in labeled_rows]
    sessions = sorted({str(row.get("session_id") or row["video_id"]) for row in labeled_rows})
    videos = sorted({str(row["video_id"]) for row in labeled_rows})
    video_paths = sorted({str(row.get("video_path") or "") for row in labeled_rows if row.get("video_path")})

    input_hashes = {
        "queue_sha256": _sha256_file(queue_path),
        "labels_sha256": _sha256_file(labels_path),
    }

    confirmed_videos = _load_confirmed_rounds(labels_path)
    draft_videos = _load_round_manifest_draft(queue_path)
    if confirmed_videos is not None:
        round_videos = confirmed_videos
        ground_truth_source = "rounds_confirmed.json"
        human_confirmed = True
    elif draft_videos:
        round_videos = draft_videos
        ground_truth_source = "round_manifest.draft.json"
        human_confirmed = False
    else:
        round_videos = [
            {
                "video_id": video_id,
                "session_id": video_id,
                "video_path": next(
                    (
                        str(row.get("video_path") or "")
                        for row in labeled_rows
                        if str(row["video_id"]) == video_id
                    ),
                    "",
                ),
                "ground_truth": [],
            }
            for video_id in videos
        ]
        ground_truth_source = "empty"
        human_confirmed = False

    boundary_manifest = {
        "source_videos": video_paths,
        "video_ids": videos,
        "session_ids": sessions,
        "data_dir": str(data_dir),
        "out_dir": str(out_dir),
        "train_count": len(train_rows),
        "val_count": len(val_rows),
        "label_counts": dict(sorted(label_counts.items())),
        "min_timestamp_sec": min(timestamps) if timestamps else None,
        "max_timestamp_sec": max(timestamps) if timestamps else None,
        "input_hashes": input_hashes,
        "validation_fraction": validation_fraction,
        "ground_truth_source": ground_truth_source,
        "human_confirmed": human_confirmed,
    }
    boundary_manifest_path = out_dir / "boundary_dataset_manifest.json"
    boundary_manifest_path.write_text(
        json.dumps(boundary_manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    round_manifest = {
        "videos": round_videos,
        "ground_truth_source": ground_truth_source,
        "human_confirmed": human_confirmed,
    }
    round_manifest_path = out_dir / "round_manifest.json"
    round_manifest_path.write_text(
        json.dumps(round_manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    return {
        "boundary_manifest_path": boundary_manifest_path,
        "round_manifest_path": round_manifest_path,
        "train_count": len(train_rows),
        "val_count": len(val_rows),
        "human_confirmed": human_confirmed,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Merge human round-boundary labels into train/val dataset layout",
    )
    parser.add_argument("--queue", type=Path, required=True, help="Labeling queue JSON")
    parser.add_argument("--labels", type=Path, required=True, help="Human labels JSON")
    parser.add_argument(
        "--data-dir",
        type=Path,
        required=True,
        help="Existing valorant_phase dataset root (audit metadata only; never written)",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        required=True,
        help="Output directory for train/val frames and manifests",
    )
    parser.add_argument(
        "--validation-fraction",
        type=float,
        default=1 / 3,
        help="Fraction of each recording reserved for validation (default: 1/3)",
    )
    parser.add_argument(
        "--link",
        action="store_true",
        help="Symlink frames instead of copying",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if not args.queue.is_file():
            raise ValueError(f"queue does not exist: {args.queue}")
        if not args.labels.is_file():
            raise ValueError(f"labels does not exist: {args.labels}")
        if not args.data_dir.is_dir():
            raise ValueError(f"data-dir does not exist: {args.data_dir}")
        result = merge_labels(
            queue_path=args.queue,
            labels_path=args.labels,
            data_dir=args.data_dir,
            out_dir=args.out_dir,
            validation_fraction=args.validation_fraction,
            link_frames=args.link,
        )
        print(
            f"merged {result['train_count']} train + {result['val_count']} val frames\n"
            f"boundary manifest: {result['boundary_manifest_path']}\n"
            f"round manifest: {result['round_manifest_path']}"
        )
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
