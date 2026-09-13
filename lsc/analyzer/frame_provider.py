"""Low-risk frame cache shared by broadcast audit / boundary refine.

The audit path currently issues many FFmpeg extractions for the same recording:
start gate, tail lookahead, fallback full scan, and 2fps refine.  This provider
keeps a bounded in-memory frame cache per video path and per sampling fps, so
overlapping candidates and repeated audit attempts reuse already decoded frames
instead of re-running FFmpeg for the same intervals.

This is intentionally standalone: it does not touch the recorder or shared-ingest
path.  It only replaces ``extract_frames_cancellable`` when callers explicitly
provide a ``frame_provider``.
"""
from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterable
from typing import Any

_log = logging.getLogger(__name__)

DEFAULT_MAX_FRAMES = 256
# A gap smaller than this is considered covered by an existing extraction;
# the value is roughly one frame at 1fps plus seek tolerance.
COVERAGE_EPSILON_SEC = 0.6


def _merge_ranges(
    ranges: list[tuple[float, float]],
    *,
    epsilon: float = 0.001,
) -> list[tuple[float, float]]:
    normalized: list[tuple[float, float]] = []
    for start, end in ranges:
        try:
            start = float(start)
            end = float(end)
        except (TypeError, ValueError):
            continue
        if start < 0.0 or end <= start:
            continue
        normalized.append((start, end))
    normalized.sort(key=lambda item: (item[0], item[1]))
    merged: list[tuple[float, float]] = []
    for start, end in normalized:
        if not merged or start > merged[-1][1] + max(0.0, epsilon):
            merged.append((start, end))
            continue
        merged[-1] = (merged[-1][0], max(merged[-1][1], end))
    return merged


def _uncovered_ranges(
    ranges: list[tuple[float, float]],
    start: float,
    end: float,
    *,
    epsilon: float = 0.001,
) -> list[tuple[float, float]]:
    target_start = max(0.0, float(start))
    target_end = max(target_start, float(end))
    if target_end <= target_start:
        return []
    clipped: list[tuple[float, float]] = []
    for item_start, item_end in _merge_ranges(ranges):
        clipped_start = max(target_start, item_start)
        clipped_end = min(target_end, item_end)
        if clipped_end > clipped_start:
            clipped.append((clipped_start, clipped_end))

    gaps: list[tuple[float, float]] = []
    cursor = target_start
    for covered_start, covered_end in clipped:
        if covered_start > cursor + max(0.0, epsilon):
            gaps.append((cursor, covered_start))
        cursor = max(cursor, covered_end)
    if target_end > cursor + max(0.0, epsilon):
        gaps.append((cursor, target_end))
    return gaps


class FrameProvider:
    """Bounded cross-call frame cache for one continuous-analysis task.

    Not thread-safe by itself; all mutating operations are guarded by an
    internal lock so background refine and coarse scan helpers may share it.
    """

    def __init__(self, max_frames: int = DEFAULT_MAX_FRAMES) -> None:
        self._lock = threading.RLock()
        self._max_frames = int(max_frames)
        # (video_path, fps_key) -> {timestamp -> image}.  Coverage was already
        # isolated by fps; the image bucket must use the same key or a later
        # 1fps request can accidentally receive cached 10fps refine frames.
        self._frames: dict[tuple[str, float], dict[float, Any]] = {}
        # (video_path, fps_key) -> list of successfully extracted ranges
        self._coverage: dict[tuple[str, float], list[tuple[float, float]]] = {}

    def _evict_if_needed(self) -> None:
        total = sum(len(items) for items in self._frames.values())
        max_frames = max(1, self._max_frames)
        while total > max_frames and self._frames:
            # Continuous analysis consumes frames chronologically.  Evict the
            # globally oldest sample while preserving newer coverage instead
            # of dropping the whole recording cache at the limit.
            candidates = [
                (min(bucket), key)
                for key, bucket in self._frames.items()
                if bucket
            ]
            if not candidates:
                self._frames.clear()
                self._coverage.clear()
                return
            oldest_ts, oldest_key = min(candidates, key=lambda item: item[0])
            bucket = self._frames[oldest_key]
            bucket.pop(oldest_ts, None)
            total -= 1
            if not bucket:
                self._frames.pop(oldest_key, None)
                self._coverage.pop(oldest_key, None)
                continue
            earliest_retained = min(bucket)
            clipped = []
            for start, end in self._coverage.get(oldest_key, []):
                clipped_start = max(float(start), float(earliest_retained))
                if float(end) > clipped_start:
                    clipped.append((clipped_start, float(end)))
            self._coverage[oldest_key] = _merge_ranges(clipped)

    def get_frames(
        self,
        video_path: str,
        *,
        start_sec: float,
        end_sec: float,
        fps: float,
        ffmpeg_path: str = "ffmpeg",
        cancel_check: Callable[[], bool] | None = None,
        overlap_sec: float = 0.0,
        extractor: Callable[..., list[tuple[float, Any]]] | None = None,
    ) -> list[tuple[float, Any]]:
        """Return frames for ``[start_sec - overlap_sec, end_sec + overlap_sec]``.

        Any missing subrange is decoded once via ``extractor`` (defaults to
        ``extract_frames_cancellable``) and cached for later calls with the same
        video/fps.
        """
        from lsc.utils.helpers import resolve_real_video_path

        path = resolve_real_video_path(video_path)
        if not path:
            return []
        request_start = max(0.0, float(start_sec) - float(overlap_sec))
        request_end = max(request_start, float(end_sec) + float(overlap_sec))
        if request_end <= request_start:
            return []
        fps_key = round(float(fps), 3)

        cache_key = (path, fps_key)
        with self._lock:
            covered = list(self._coverage.get(cache_key, []))
        gaps = _uncovered_ranges(
            covered,
            request_start,
            request_end,
            epsilon=COVERAGE_EPSILON_SEC,
        )

        if gaps:
            if extractor is None:
                from lsc.analyzer.valorant_ocr_rounds import extract_frames_cancellable

                extractor = extract_frames_cancellable
            for gap_start, gap_end in gaps:
                if cancel_check and cancel_check():
                    from lsc.utils.cancellable_ffmpeg import FFmpegCancelled

                    raise FFmpegCancelled("frame provider cancelled")
                try:
                    frames = extractor(
                        path,
                        start_sec=gap_start,
                        end_sec=gap_end,
                        fps=fps,
                        ffmpeg_path=ffmpeg_path,
                        cancel_check=cancel_check,
                        overlap_sec=0.0,
                    )
                except Exception as exc:  # noqa: BLE001 - classify cancellation below
                    # Cancellation is control flow, not a recoverable cache
                    # miss.  Swallowing it lets a timed-out audit continue with
                    # incomplete evidence and may produce a false terminal result.
                    from lsc.utils.cancellable_ffmpeg import FFmpegCancelled

                    if isinstance(exc, FFmpegCancelled):
                        raise
                    _log.warning("FrameProvider extraction failed for %.1f-%.1f: %s", gap_start, gap_end, exc)
                    continue
                with self._lock:
                    bucket = self._frames.setdefault(cache_key, {})
                    decoded: list[float] = []
                    for ts, image in frames:
                        try:
                            ts_key = round(float(ts), 3)
                        except (TypeError, ValueError):
                            continue
                        bucket[ts_key] = image
                        decoded.append(ts_key)
                    # 只登记**真正解出帧**的范围。录制文件仍在增长时，请求的
                    # [gap_start, gap_end] 可能超出已写入的媒体：旧实现无条件把整段
                    # 记为已覆盖，后续请求命中"已覆盖但无帧"的缓存 → 该区间永远
                    # 不再解码，审计 evidence 出现永久空洞（实测 2026-09-12：
                    # 448-486s 被跳过，351.312 回合的真出点 449.875 因此看不见）。
                    if decoded:
                        self._coverage.setdefault(cache_key, []).append(
                            (float(gap_start), max(decoded))
                        )
                        self._coverage[cache_key] = _merge_ranges(
                            self._coverage[cache_key]
                        )
                    self._evict_if_needed()

        with self._lock:
            bucket = self._frames.get(cache_key, {})
            result = sorted(
                (
                    (ts, image)
                    for ts, image in bucket.items()
                    if request_start - 0.001 <= ts <= request_end + 0.001
                ),
                key=lambda item: item[0],
            )
        return result

    def prefetch_ranges(
        self,
        video_path: str,
        ranges: Iterable[tuple[float, float]],
        *,
        fps: float,
        ffmpeg_path: str = "ffmpeg",
        cancel_check: Callable[[], bool] | None = None,
        merge_gap_sec: float = 1.0,
        extractor: Callable[..., list[tuple[float, Any]]] | None = None,
    ) -> int:
        """Decode merged ranges up front and return the number of groups.

        Overlapping candidates commonly request the same start gate and tail
        windows.  Merging them before auditing turns those requests into one
        FFmpeg extraction per continuous group; later ``get_frames`` calls are
        cache hits and only decode genuinely missing tails.
        """
        merged = _merge_ranges(
            list(ranges),
            epsilon=max(0.0, float(merge_gap_sec)),
        )
        for start, end in merged:
            self.get_frames(
                video_path,
                start_sec=start,
                end_sec=end,
                fps=fps,
                ffmpeg_path=ffmpeg_path,
                cancel_check=cancel_check,
                overlap_sec=0.0,
                extractor=extractor,
            )
        return len(merged)


__all__ = [
    "COVERAGE_EPSILON_SEC",
    "DEFAULT_MAX_FRAMES",
    "FrameProvider",
    "_merge_ranges",
    "_uncovered_ranges",
]
