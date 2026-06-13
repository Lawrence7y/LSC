"""Timeline math utilities for coordinate conversion and tick generation."""
from __future__ import annotations


def time_to_x(time_sec: float, *, offset_sec: float, pixels_per_sec: float, left_px: int) -> int:
    """Convert time in seconds to x pixel coordinate."""
    return int(left_px + max(0.0, time_sec - offset_sec) * pixels_per_sec)


def x_to_time(x_px: int, *, offset_sec: float, pixels_per_sec: float, left_px: int) -> float:
    """Convert x pixel coordinate to time in seconds."""
    return max(0.0, offset_sec + (x_px - left_px) / max(1.0, pixels_per_sec))


def build_time_ticks(*, visible_start: float, visible_end: float) -> list[float]:
    """Generate time tick marks for the visible range.

    Tick spacing adapts to zoom level:
    - <=15s visible: 1s ticks
    - <=120s visible: 5s ticks
    - >120s visible: 30s ticks
    """
    span = max(1.0, visible_end - visible_start)
    step = 1.0 if span <= 15 else 5.0 if span <= 120 else 30.0
    first = visible_start - (visible_start % step)
    ticks: list[float] = []
    current = first
    while current <= visible_end:
        ticks.append(round(current, 3))
        current += step
    return ticks
