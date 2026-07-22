"""Build hard-miss frame queues from eval misses and end-point errors."""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

from scripts.valorant_vision.build_round_boundary_queue import (
    _extract_frames,
    _video_duration,
    validate_sample_times,
)

DEFAULT_END_ERR_THRESHOLD = 0.8


def _float_field(row: dict[str, Any], *names: str, default: float | None = None) -> float | None:
    for name in names:
        if name in row:
            try:
                value = float(row[name])
            except (TypeError, ValueError):
                return None
            if math.isfinite(value):
                return value
            return None
    return default


def _overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def _merge_windows(windows: list[dict[str, float]]) -> list[dict[str, float]]:
    if not windows:
        return []
    ordered = sorted(windows, key=lambda row: (row["start"], row["end"]))
    merged: list[dict[str, float]] = [dict(ordered[0])]
    for window in ordered[1:]:
        previous = merged[-1]
        overlap = _overlap(previous["start"], previous["end"], window["start"], window["end"])
        min_span = min(previous["end"] - previous["start"], window["end"] - window["start"])
        if overlap > 0 and (min_span <= 0 or overlap / min_span >= 0.5):
            previous["start"] = min(previous["start"], window["start"])
            previous["end"] = max(previous["end"], window["end"])
        else:
            merged.append(dict(window))
    return merged


def build_hard_windows(
    *,
    missed_rounds: list[dict],
    end_errors: list[dict],
    radius: float,
) -> list[dict]:
    """Build hard sampling windows for missed rounds and end-point errors."""

    try:
        radius_value = float(radius)
    except (TypeError, ValueError) as exc:
        raise ValueError("radius must be a finite number") from exc
    if not math.isfinite(radius_value) or radius_value < 0:
        raise ValueError("radius must be a finite non-negative number")

    windows: list[dict[str, float]] = []
    for row in missed_rounds:
        start = _float_field(row, "start", "start_sec")
        end = _float_field(row, "end", "end_sec")
        if start is None or end is None or start >= end:
            continue
        windows.append({
            "start": round(start - radius_value, 3),
            "end": round(end + radius_value, 3),
            "round_key": str(row.get("round_key") or ""),
            "kind": "missed_round",
        })

    for row in end_errors:
        gt_end = _float_field(row, "gt_end", "end", "end_sec")
        if gt_end is None:
            continue
        windows.append({
            "start": round(gt_end - radius_value, 3),
            "end": round(gt_end + radius_value, 3),
            "round_key": str(row.get("round_key") or ""),
            "kind": "end_error",
        })

    return _merge_windows(windows)


def _match_ground_truth(
    pred: dict[str, Any],
    ground_truth: list[dict[str, Any]],
    *,
    matched_gt: set[int],
) -> int | None:
    p0 = _float_field(pred, "start_sec", "start", default=0.0) or 0.0
    p1 = _float_field(pred, "end_sec", "end", default=0.0) or 0.0
    pred_video_id = pred.get("video_id")
    best_idx: int | None = None
    best_iou = 0.0
    for idx, gt in enumerate(ground_truth):
        if idx in matched_gt:
            continue
        gt_video_id = gt.get("video_id")
        if (
            pred_video_id is not None
            and gt_video_id is not None
            and str(pred_video_id) != str(gt_video_id)
        ):
            continue
        g0 = _float_field(gt, "start_sec", "start", default=0.0) or 0.0
        g1 = _float_field(gt, "end_sec", "end", default=0.0) or 0.0
        inter = _overlap(p0, p1, g0, g1)
        union = max(p1, g1) - min(p0, g0)
        iou = inter / union if union > 0 else 0.0
        if iou > best_iou:
            best_iou = iou
            best_idx = idx
    if best_idx is None or best_iou < 0.3:
        return None
    return best_idx


def _derive_misses_from_pairs(
    ground_truth: list[dict[str, Any]],
    predictions: list[dict[str, Any]],
    *,
    end_err_threshold: float = DEFAULT_END_ERR_THRESHOLD,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    matched_gt: set[int] = set()
    pred_by_gt: dict[int, dict[str, Any]] = {}
    for pred in predictions:
        gt_idx = _match_ground_truth(pred, ground_truth, matched_gt=matched_gt)
        if gt_idx is None:
            continue
        matched_gt.add(gt_idx)
        pred_by_gt[gt_idx] = pred

    missed_rounds: list[dict[str, Any]] = []
    for idx, gt in enumerate(ground_truth):
        if idx in matched_gt:
            continue
        start = _float_field(gt, "start_sec", "start")
        end = _float_field(gt, "end_sec", "end")
        if start is None or end is None:
            continue
        missed_rounds.append({
            "round_key": str(gt.get("round_key") or f"R{idx + 1}"),
            "start": start,
            "end": end,
        })

    end_errors: list[dict[str, Any]] = []
    for idx, gt in enumerate(ground_truth):
        pred = pred_by_gt.get(idx)
        if pred is None:
            continue
        gt_end = _float_field(gt, "end_sec", "end")
        pred_end = _float_field(pred, "end_sec", "end", "pred_end")
        if gt_end is None or pred_end is None:
            continue
        if abs(pred_end - gt_end) > end_err_threshold:
            end_errors.append({
                "round_key": str(gt.get("round_key") or pred.get("round_key") or f"R{idx + 1}"),
                "gt_end": gt_end,
                "pred_end": pred_end,
            })

    return missed_rounds, end_errors


def extract_misses_from_report(
    report: dict[str, Any],
    *,
    end_err_threshold: float = DEFAULT_END_ERR_THRESHOLD,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Extract per-round misses from an eval report.

  Returns ``([], [])`` when the report only contains aggregate metrics and no
  per-round ``missed_rounds`` / ``end_errors`` lists or GT/prediction pairs.
    """

    if not isinstance(report, dict):
        return [], []

    explicit_missed = report.get("missed_rounds")
    explicit_end_errors = report.get("end_errors")
    if isinstance(explicit_missed, list) and isinstance(explicit_end_errors, list):
        return list(explicit_missed), list(explicit_end_errors)
    if isinstance(explicit_missed, list):
        return list(explicit_missed), list(explicit_end_errors or [])
    if isinstance(explicit_end_errors, list):
        return list(explicit_missed or []), list(explicit_end_errors)

    rounds_block = report.get("rounds")
    if isinstance(rounds_block, dict):
        ground_truth = list(rounds_block.get("ground_truth") or rounds_block.get("gt") or [])
        predictions = list(rounds_block.get("predictions") or rounds_block.get("preds") or [])
        if ground_truth and predictions:
            return _derive_misses_from_pairs(
                ground_truth,
                predictions,
                end_err_threshold=end_err_threshold,
            )

    ground_truth = list(report.get("ground_truth") or report.get("gt") or [])
    predictions = list(report.get("predictions") or report.get("preds") or [])
    if ground_truth and predictions:
        return _derive_misses_from_pairs(
            ground_truth,
            predictions,
            end_err_threshold=end_err_threshold,
        )

    return [], []


def _window_sample_times(
    windows: list[dict[str, Any]],
    *,
    duration: float,
    fps: float,
) -> list[float]:
    try:
        fps_value = float(fps)
        duration_value = float(duration)
    except (TypeError, ValueError) as exc:
        raise ValueError("duration/fps must be finite numbers") from exc
    if not math.isfinite(fps_value) or fps_value <= 0:
        raise ValueError("fps must be a finite positive number")
    if not math.isfinite(duration_value) or duration_value < 0:
        raise ValueError("duration must be a finite non-negative number")

    step = 1.0 / fps_value
    values: set[float] = set()
    for window in windows:
        lo = max(0.0, float(window["start"]))
        hi = min(duration_value, float(window["end"]))
        if lo > hi:
            continue
        index = 0
        while True:
            current = lo + index * step
            if current > hi + 1e-9:
                break
            values.add(round(min(hi, current), 3))
            index += 1
        values.add(round(hi, 3))
    return sorted(value for value in values if 0.0 <= value <= duration_value)


def _load_confirmed_ground_truth(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        videos = data.get("videos")
        if isinstance(videos, list):
            rows: list[dict[str, Any]] = []
            for video in videos:
                if not isinstance(video, dict):
                    continue
                rows.extend(video.get("ground_truth") or [])
            return rows
        rows = data.get("ground_truth")
        if isinstance(rows, list):
            return rows
    if isinstance(data, list):
        return data
    raise ValueError("confirmed rounds JSON must contain videos[].ground_truth or a rounds list")


def _load_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"expected JSON object: {path}")
    return data


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a Valorant hard-miss frame queue")
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--confirmed-rounds", type=Path, required=True)
    parser.add_argument("--eval-report", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--radius-sec", type=float, default=8.0)
    parser.add_argument("--fps", type=float, default=2.0)
    parser.add_argument(
        "--end-errors-json",
        type=Path,
        default=None,
        help="Optional sidecar JSON with an end_errors list when the eval report lacks detail",
    )
    parser.add_argument(
        "--fallback-all-gt",
        action="store_true",
        help="When the eval report has no per-round detail, sample every confirmed GT round",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if not args.video.is_file():
            raise ValueError(f"video does not exist: {args.video}")
        if not args.confirmed_rounds.is_file():
            raise ValueError(f"confirmed rounds JSON does not exist: {args.confirmed_rounds}")
        if not args.eval_report.is_file():
            raise ValueError(f"eval report does not exist: {args.eval_report}")

        try:
            import cv2
        except ImportError as exc:
            raise RuntimeError("OpenCV (cv2) is required for frame extraction") from exc

        confirmed_rows = _load_confirmed_ground_truth(args.confirmed_rounds)
        report = _load_json(args.eval_report)
        missed_rounds, end_errors = extract_misses_from_report(report)

        if args.end_errors_json is not None:
            if not args.end_errors_json.is_file():
                raise ValueError(f"end-errors sidecar does not exist: {args.end_errors_json}")
            sidecar = _load_json(args.end_errors_json)
            sidecar_errors = sidecar.get("end_errors")
            if isinstance(sidecar_errors, list):
                end_errors = list(sidecar_errors)

        if not missed_rounds and not end_errors:
            if args.fallback_all_gt:
                missed_rounds = [
                    {
                        "round_key": str(row.get("round_key") or f"R{index + 1}"),
                        "start": float(row["start"]),
                        "end": float(row["end"]),
                    }
                    for index, row in enumerate(confirmed_rows)
                    if _float_field(row, "start", "start_sec") is not None
                    and _float_field(row, "end", "end_sec") is not None
                ]
            else:
                print(
                    "warning: eval report has no per-round missed/end-error detail; "
                    "producing empty hard window set (use --fallback-all-gt to sample all GT rounds)",
                    file=sys.stderr,
                )

        windows = build_hard_windows(
            missed_rounds=missed_rounds,
            end_errors=end_errors,
            radius=args.radius_sec,
        )

        capture = cv2.VideoCapture(str(args.video))
        try:
            if not capture.isOpened():
                raise RuntimeError(f"unable to open video: {args.video}")
            duration = _video_duration(capture)
        finally:
            capture.release()

        times = _window_sample_times(windows, duration=duration, fps=args.fps)
        validate_sample_times(times, duration=duration)

        out_dir = args.out_dir.resolve()
        frame_records = _extract_frames(args.video, times, out_dir, duration=duration)
        video_id = args.video.stem
        queue = [
            {
                **record,
                "id": record["id"].replace("boundary_", "hard_"),
                "video_id": video_id,
                "video_path": str(args.video),
                "source_type": "hard_miss",
                "session_id": video_id,
                "split": "review",
                "label": None,
                "notes": "",
            }
            for record in frame_records
        ]
        out_dir.mkdir(parents=True, exist_ok=True)
        queue_path = (out_dir / "queue.json").resolve()
        queue_path.write_text(
            json.dumps(queue, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        manifest = {
            "videos": [{
                "video_id": video_id,
                "session_id": video_id,
                "video_path": str(args.video),
                "duration_sec": round(duration, 3),
                "ground_truth": confirmed_rows,
                "queue_path": str(queue_path),
            }],
            "hard_windows": windows,
            "missed_rounds": missed_rounds,
            "end_errors": end_errors,
            "sample_times": times,
            "radius_sec": args.radius_sec,
            "fps": args.fps,
        }
        (out_dir / "hard_manifest.draft.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return 0
    except (ImportError, OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
