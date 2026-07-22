"""为一次完整录制建立 Valorant 回合边界抽帧队列。

该脚本只负责读取已有分析结果、生成候选时间戳和抽取 JPEG；不会修改原视频或分析结果。
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any


def _number(row: dict[str, Any], *names: str, row_index: int) -> float:
    for name in names:
        if name in row:
            try:
                value = float(row[name])
            except (TypeError, ValueError) as exc:
                raise ValueError(f"row {row_index}: invalid {name}") from exc
            if not math.isfinite(value):
                raise ValueError(f"row {row_index}: invalid {name} (not finite)")
            return value
    raise ValueError(f"row {row_index}: missing start/end bounds")


def _normalised_rows(rows: Iterable[dict[str, Any]], *, duration: float) -> list[tuple[float, float]]:
    try:
        video_duration = float(duration)
    except (TypeError, ValueError) as exc:
        raise ValueError("duration bounds must be a finite non-negative number") from exc
    if not math.isfinite(video_duration) or video_duration < 0:
        raise ValueError("duration bounds must be a finite non-negative number")

    normalised: list[tuple[float, float]] = []
    for row_index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise ValueError(f"row {row_index}: invalid round bounds (expected object)")
        start = _number(row, "start", "start_sec", "round_start_sec", row_index=row_index)
        end = _number(row, "end", "end_sec", "round_end_sec", row_index=row_index)
        if not (0.0 <= start < end <= video_duration):
            raise ValueError(
                f"row {row_index}: invalid bounds start={start:.3f} end={end:.3f}; "
                f"expected 0 <= start < end <= duration ({video_duration:.3f})"
            )
        if normalised:
            previous_start, previous_end = normalised[-1]
            if start < previous_start:
                raise ValueError(
                    f"row {row_index}: rounds must be time sorted; "
                    f"start={start:.3f} follows {previous_start:.3f}"
                )
            if start < previous_end:
                raise ValueError(
                    f"row {row_index}: round bounds overlap; "
                    f"start={start:.3f} is before previous end={previous_end:.3f}"
                )
        normalised.append((start, end))
    return normalised


def validate_round_rows(rows: Iterable[dict[str, Any]], *, duration: float) -> None:
    """校验回合 bounds 在视频范围内、按时间排序且互不重叠。"""

    _normalised_rows(rows, duration=duration)


def validate_sample_times(times: Iterable[float], *, duration: float) -> None:
    """校验抽帧时间戳有限、位于视频 bounds 内且没有重复。"""

    try:
        video_duration = float(duration)
    except (TypeError, ValueError) as exc:
        raise ValueError("sample timestamp bounds require a finite duration") from exc
    if not math.isfinite(video_duration) or video_duration < 0:
        raise ValueError("sample timestamp bounds require a finite non-negative duration")

    seen: set[float] = set()
    for index, raw_timestamp in enumerate(times, start=1):
        try:
            timestamp = float(raw_timestamp)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"sample timestamp {index} is invalid") from exc
        if not math.isfinite(timestamp):
            raise ValueError(f"finite sample timestamp required at index {index}")
        if not 0.0 <= timestamp <= video_duration:
            raise ValueError(
                f"sample timestamp bounds invalid at index {index}: "
                f"{timestamp:.3f} not in [0, {video_duration:.3f}]"
            )
        if timestamp in seen:
            raise ValueError(f"duplicate sample timestamp: {timestamp:.3f}")
        seen.add(timestamp)


def _window_times(boundary: float, *, duration: float, radius: float, step: float) -> list[float]:
    lo = max(0.0, boundary - radius)
    hi = min(duration, boundary + radius)
    values: list[float] = []
    index = 0
    while True:
        current = lo + index * step
        if current > hi + 1e-9:
            break
        values.append(min(hi, current))
        index += 1
    if not values or values[-1] < hi - 1e-9:
        values.append(hi)
    return values


def build_sample_times(
    rows: Iterable[dict[str, Any]],
    *,
    duration: float,
    radius: float,
    fps: float,
) -> list[float]:
    """生成边界窗口、候选间 gap 以及视频两端的去重时间戳（精确到 3 位）。"""

    normalised = _normalised_rows(rows, duration=duration)
    try:
        radius_value = float(radius)
        fps_value = float(fps)
    except (TypeError, ValueError) as exc:
        raise ValueError("radius/fps must be finite numbers") from exc
    if not math.isfinite(radius_value) or radius_value < 0:
        raise ValueError("radius must be a finite non-negative number")
    if not math.isfinite(fps_value) or fps_value <= 0:
        raise ValueError("fps must be a finite positive number")
    duration_value = float(duration)
    step = 1.0 / fps_value
    # 3 位时间戳不能向上越过实际视频时长。
    duration_edge = round(math.floor(duration_value * 1000.0) / 1000.0, 3)
    values: set[float] = {0.0, duration_edge}

    boundaries = [boundary for row in normalised for boundary in row]
    for boundary in boundaries:
        # 即使步长不能整除窗口，也必须保留候选边界本身。
        values.add(round(boundary, 3))
        values.update(round(value, 3) for value in _window_times(
            boundary, duration=duration_value, radius=radius_value, step=step
        ))

    # 候选间隔逐点扫描，避免漏掉分析器未列出的回合。
    for (_, previous_end), (next_start, _) in zip(normalised, normalised[1:], strict=False):
        if previous_end < next_start:
            index = 0
            while True:
                timestamp = previous_end + index * step
                if timestamp > next_start + 1e-9:
                    break
                values.add(round(timestamp, 3))
                index += 1
            values.add(round(next_start, 3))

    return sorted(value for value in values if 0.0 <= value <= duration_value)


def _load_round_rows(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        rows = data
    elif isinstance(data, dict):
        rows = data.get("rounds")
        if rows is None:
            rows = data.get("round_metrics")
        if rows is None:
            rows = data.get("highlights")
        if rows is None and isinstance(data.get("results"), dict):
            results = data["results"]
            rows = results.get("rounds") or results.get("highlights")
    else:
        rows = None
    if not isinstance(rows, list):
        raise ValueError("analysis JSON must contain a rounds/highlights list")
    return rows


def _meta_duration(capture: Any) -> float:
    import cv2

    fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
    count = float(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0)
    # Prefer last-frame timestamp; count/fps can sit past the final sample.
    if fps > 0 and count >= 1:
        duration = (count - 1.0) / fps
    else:
        duration = 0.0
    if not math.isfinite(duration) or duration < 0:
        raise RuntimeError("unable to determine video duration")
    if duration == 0.0 and count < 1:
        raise RuntimeError("unable to determine video duration")
    return duration


def _probe_readable_duration(capture: Any, *, meta_duration: float) -> float:
    """Binary-search the largest timestamp that OpenCV can actually decode."""

    import cv2

    if not math.isfinite(meta_duration) or meta_duration < 0:
        raise RuntimeError("unable to determine video duration")
    if meta_duration == 0.0:
        return 0.0

    end = round(math.floor(meta_duration * 1000.0) / 1000.0, 3)
    capture.set(cv2.CAP_PROP_POS_MSEC, end * 1000.0)
    ok, _frame = capture.read()
    if ok:
        return end

    lo = 0.0
    hi = end
    best = 0.0
    for _ in range(40):
        if hi - lo <= 0.001:
            break
        mid = (lo + hi) / 2.0
        capture.set(cv2.CAP_PROP_POS_MSEC, mid * 1000.0)
        ok, _frame = capture.read()
        if ok:
            best = mid
            lo = mid
        else:
            hi = mid
    readable = round(math.floor(best * 1000.0) / 1000.0, 3)
    if readable < 0:
        raise RuntimeError("unable to determine readable video duration")
    return readable


def _video_duration(capture: Any) -> float:
    """Return a seekable duration, probing when container metadata overshoots."""

    meta = _meta_duration(capture)
    return _probe_readable_duration(capture, meta_duration=meta)


def _extract_frames(
    video: Path,
    times: list[float],
    output_dir: Path,
    *,
    duration: float | None = None,
) -> list[dict[str, Any]]:
    import cv2

    capture = cv2.VideoCapture(str(video))
    try:
        if not capture.isOpened():
            raise RuntimeError(f"unable to open video: {video}")
        output_dir = output_dir.resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        if duration is None:
            duration = _video_duration(capture)
        else:
            duration = float(duration)
        validate_sample_times(times, duration=duration)
        records: list[dict[str, Any]] = []
        seen: set[float] = set()
        for index, timestamp in enumerate(times, start=1):
            timestamp = round(float(timestamp), 3)
            if timestamp in seen:
                raise RuntimeError(f"duplicate timestamp in extraction queue: {timestamp:.3f}")
            seen.add(timestamp)
            if not capture.set(cv2.CAP_PROP_POS_MSEC, timestamp * 1000.0):
                raise RuntimeError(f"frame seek failed at timestamp {timestamp:.3f}")
            ok, frame = capture.read()
            if not ok or frame is None:
                raise RuntimeError(f"frame decode failed at timestamp {timestamp:.3f}")
            filename = f"frame_{index:06d}.jpg"
            destination = output_dir / filename
            if not cv2.imwrite(str(destination), frame) or not destination.is_file():
                raise RuntimeError(f"frame write failed at timestamp {timestamp:.3f}")
            records.append({
                "id": f"boundary_{index:06d}",
                "rel_path": filename,
                "abs_path": str(destination.resolve()),
                "timestamp_sec": timestamp,
            })
    finally:
        capture.release()
    return records


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a Valorant round-boundary frame queue")
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--analysis-json", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--radius-sec", type=float, default=1.5)
    parser.add_argument("--fps", type=float, default=2.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if not args.video.is_file():
            raise ValueError(f"video does not exist: {args.video}")
        if not args.analysis_json.is_file():
            raise ValueError(f"analysis JSON does not exist: {args.analysis_json}")
        try:
            import cv2
        except ImportError as exc:
            raise RuntimeError("OpenCV (cv2) is required for frame extraction") from exc

        capture = cv2.VideoCapture(str(args.video))
        try:
            if not capture.isOpened():
                raise RuntimeError(f"unable to open video: {args.video}")
            duration = _video_duration(capture)
        finally:
            capture.release()
        rows = _load_round_rows(args.analysis_json)
        validate_round_rows(rows, duration=duration)
        times = build_sample_times(
            rows, duration=duration, radius=args.radius_sec, fps=args.fps
        )
        validate_sample_times(times, duration=duration)
        out_dir = args.out_dir.resolve()
        frame_records = _extract_frames(args.video, times, out_dir)
        video_id = args.video.stem
        queue = [
            {
                **record,
                "video_id": video_id,
                "video_path": str(args.video),
                "source_type": "round_boundary",
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
                "ground_truth": rows,
                "queue_path": str(queue_path),
            }],
            "sample_times": times,
            "radius_sec": args.radius_sec,
            "fps": args.fps,
        }
        (out_dir / "round_manifest.draft.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return 0
    except (ImportError, OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
