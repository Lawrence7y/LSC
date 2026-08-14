"""Monotonic media/content timeline mapping for segmented recordings."""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from .manifest import RecordingManifest, SegmentEntry


@dataclass(frozen=True, slots=True)
class TimelineInterval:
    """One validated segment's media and logical content interval."""

    sequence: int
    media_start_ms: int
    media_end_ms: int
    content_start_ms: int
    content_end_ms: int
    generation: int = 0


class TimelineMapper:
    """Map media timestamps to a non-decreasing logical content timeline.

    Segment-local timestamps may reset after a reconnect.  The mapper therefore
    uses the manifest's ordered segment durations as the logical axis and
    treats missing/unknown media timestamps as contiguous offsets.  Explicit
    gaps are preserved as forward-only intervals; no method can return a time
    earlier than the previous mapped point.
    """

    def __init__(
        self,
        manifest: RecordingManifest | None = None,
        *,
        segments: Iterable[SegmentEntry] | None = None,
        content_offset: float = 0.0,
    ) -> None:
        self.content_offset = float(
            manifest.content_offset if manifest is not None else content_offset
        )
        source = list(segments or (manifest.segments if manifest is not None else ()))
        self.intervals = tuple(self._build_intervals(source, manifest))
        self._cursor_index = 0
        self._last_media_ms: float | None = None
        self._last_content_ms = 0.0

    @staticmethod
    def _build_intervals(
        segments: list[SegmentEntry],
        manifest: RecordingManifest | None,
    ) -> list[TimelineInterval]:
        intervals: list[TimelineInterval] = []
        media_cursor = 0
        content_cursor = 0
        completed = sorted(
            (
                item
                for item in segments
                if item.state in {"COMPLETE", "RECOVERED"}
                and item.duration_ms >= 0
            ),
            key=lambda item: item.sequence,
        )
        gaps_by_sequence: dict[int, int] = {}
        if manifest is not None:
            for gap in manifest.gaps:
                if not isinstance(gap, dict):
                    continue
                try:
                    sequence = int(gap.get("before_sequence", gap.get("sequence", 0)))
                    duration = max(0, int(gap.get("duration_ms", 0) or 0))
                except (TypeError, ValueError):
                    continue
                if sequence > 0 and duration:
                    gaps_by_sequence[sequence] = duration

        for entry in completed:
            duration = max(0, int(entry.duration_ms))
            media_start = (
                int(entry.media_start_ms)
                if entry.media_start_ms is not None
                else media_cursor
            )
            media_end = (
                int(entry.media_end_ms)
                if entry.media_end_ms is not None
                else media_start + duration
            )
            media_end = max(media_start, media_end)
            content_start = content_cursor
            content_end = content_start + max(duration, media_end - media_start)
            intervals.append(
                TimelineInterval(
                    sequence=entry.sequence,
                    media_start_ms=media_start,
                    media_end_ms=media_end,
                    content_start_ms=content_start,
                    content_end_ms=content_end,
                    generation=entry.generation,
                )
            )
            media_cursor = media_end
            content_cursor = content_end + gaps_by_sequence.get(entry.sequence + 1, 0)
        return intervals

    @property
    def legacy_offset_seconds(self) -> float:
        return self.content_offset

    def media_to_content(self, media_seconds: float) -> float:
        value_ms = max(0.0, float(media_seconds) * 1000.0)
        if not self.intervals:
            return max(0.0, float(media_seconds) + self.content_offset)
        if (
            self._last_media_ms is not None
            and value_ms < self._last_media_ms
            and self._cursor_index + 1 < len(self.intervals)
        ):
            # A timestamp reset is a generation/segment boundary.  Advance
            # once and keep subsequent calls in the new segment.
            self._cursor_index += 1
        while (
            self._cursor_index + 1 < len(self.intervals)
            and value_ms > self.intervals[self._cursor_index].media_end_ms
        ):
            self._cursor_index += 1
        interval = self.intervals[self._cursor_index]
        if value_ms < interval.media_start_ms:
            mapped_ms = interval.content_start_ms
        elif value_ms <= interval.media_end_ms:
            mapped_ms = interval.content_start_ms + value_ms - interval.media_start_ms
        else:
            mapped_ms = interval.content_end_ms + value_ms - interval.media_end_ms
        mapped = max(
            self._last_content_ms / 1000.0,
            mapped_ms / 1000.0 + self.content_offset,
        )
        self._last_media_ms = value_ms
        self._last_content_ms = mapped * 1000.0
        return mapped

    def content_to_media(self, content_seconds: float) -> float:
        value_ms = max(0.0, float(content_seconds) * 1000.0)
        if not self.intervals:
            return max(0.0, float(content_seconds) - self.content_offset)
        value_ms = max(0.0, value_ms - self.content_offset * 1000.0)
        previous: TimelineInterval | None = None
        for interval in self.intervals:
            if value_ms < interval.content_start_ms:
                # The logical timeline can contain an explicit outage gap.
                # Seeking into that gap resolves to the last playable media
                # timestamp instead of producing a negative offset in the
                # following segment.
                if previous is not None:
                    return max(0.0, previous.media_end_ms / 1000.0)
                return max(0.0, interval.media_start_ms / 1000.0)
            if value_ms <= interval.content_end_ms:
                return max(
                    0.0,
                    (interval.media_start_ms + value_ms - interval.content_start_ms)
                    / 1000.0,
                )
            previous = interval
        tail = self.intervals[-1]
        return max(
            0.0,
            (tail.media_end_ms + value_ms - tail.content_end_ms) / 1000.0,
        )

    def map_range(self, start_seconds: float, end_seconds: float) -> tuple[float, float]:
        start = self.media_to_content(start_seconds)
        end = max(start, self.media_to_content(end_seconds))
        return start, end


__all__ = ["TimelineInterval", "TimelineMapper"]
