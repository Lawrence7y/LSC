from __future__ import annotations

from handlers.room_handler import _compute_recording_to_preview_delta


def test_preview_clock_delta_when_preview_starts_late() -> None:
    # 录制已运行 135 秒，而同一时刻 MSE currentTime 为 120 秒。
    assert _compute_recording_to_preview_delta(120.0, 1135.0, 1000.0) == -15.0


def test_preview_clock_delta_supports_arbitrary_mse_pts_base() -> None:
    # MSE currentTime 可能有独立 PTS 基座，映射不能假设从 0 开始。
    assert _compute_recording_to_preview_delta(5120.0, 1135.0, 1000.0) == 4985.0
