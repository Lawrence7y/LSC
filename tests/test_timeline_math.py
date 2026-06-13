"""Tests for timeline math utilities."""
from __future__ import annotations

from lsc.gui.pages.timeline_math import build_time_ticks, time_to_x, x_to_time


def test_time_to_x_and_back_respects_zoom_and_offset() -> None:
    x = time_to_x(30.0, offset_sec=20.0, pixels_per_sec=10.0, left_px=8)
    t = x_to_time(x, offset_sec=20.0, pixels_per_sec=10.0, left_px=8)

    assert x == 108
    assert t == 30.0


def test_build_time_ticks_returns_dense_ticks_for_zoomed_view() -> None:
    ticks = build_time_ticks(visible_start=0.0, visible_end=20.0)
    assert ticks[:3] == [0.0, 5.0, 10.0]
