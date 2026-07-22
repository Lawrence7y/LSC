"""Round ground-truth validation and draft/confirmed manifest helpers."""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

VALID_END_REASONS = frozenset({"result", "next_buy", "score", "unknown"})


def validate_confirmed_rounds(rows: list[dict[str, Any]], *, duration: float) -> None:
    """Validate round rows: legal bounds, non-overlap, unique keys, valid end_reason."""

    if not math.isfinite(duration) or duration < 0:
        raise ValueError("duration must be a finite non-negative number")

    ordered = sorted(rows, key=lambda row: float(row["start"]))
    previous_end = -1.0
    seen_keys: set[str] = set()
    for index, row in enumerate(ordered, start=1):
        start = float(row["start"])
        end = float(row["end"])
        reason = str(row.get("end_reason") or "")
        key = str(row.get("round_key") or f"R{index}")
        if reason not in VALID_END_REASONS:
            raise ValueError(f"invalid end_reason: {reason}")
        if key in seen_keys:
            raise ValueError(f"duplicate round_key: {key}")
        seen_keys.add(key)
        if not (0.0 <= start < end <= duration):
            raise ValueError(f"invalid bounds: {start}-{end}")
        if start < previous_end:
            raise ValueError(f"overlapping rounds near {start}")
        previous_end = end


def find_nearest_frame(frames: list[dict[str, Any]], timestamp_sec: float) -> dict[str, Any]:
    """Return the frame dict whose timestamp is closest to ``timestamp_sec``."""

    if not frames:
        raise ValueError("no frames available for preview")
    return min(
        frames,
        key=lambda frame: abs(float(frame["timestamp_sec"]) - float(timestamp_sec)),
    )


def build_preview_payload(
    frames: list[dict[str, Any]], start: float, end: float
) -> dict[str, Any]:
    return {
        "start": find_nearest_frame(frames, start),
        "end": find_nearest_frame(frames, end),
    }


def load_draft_rounds(root: Path) -> dict[str, Any]:
    """Load ``round_manifest.draft.json`` from ``root``."""

    draft_path = root / "round_manifest.draft.json"
    if not draft_path.is_file():
        raise ValueError(f"missing round_manifest.draft.json: {draft_path}")
    data = json.loads(draft_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"invalid round_manifest.draft.json: {draft_path}")
    videos = data.get("videos")
    if not isinstance(videos, list):
        raise ValueError(f"invalid round_manifest.draft.json: {draft_path}")
    return data


def save_confirmed_rounds(root: Path, payload: dict[str, Any]) -> Path:
    """Validate and write ``rounds_confirmed.json`` under ``root``."""

    if not isinstance(payload, dict):
        raise ValueError("payload must be an object")
    videos = payload.get("videos")
    if not isinstance(videos, list):
        raise ValueError("payload.videos must be a list")

    for index, video in enumerate(videos, start=1):
        if not isinstance(video, dict):
            raise ValueError(f"videos[{index}] must be an object")
        ground_truth = video.get("ground_truth")
        if not isinstance(ground_truth, list):
            raise ValueError(f"videos[{index}].ground_truth must be a list")
        try:
            duration = float(video["duration_sec"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"videos[{index}]: missing or invalid duration_sec") from exc
        validate_confirmed_rounds(ground_truth, duration=duration)

    out_path = root / "rounds_confirmed.json"
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path
