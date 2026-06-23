"""Shared test fixtures."""
from __future__ import annotations

import pytest

from lsc.config import LscConfig


@pytest.fixture
def sample_config(tmp_path):
    """Provide a minimal LscConfig for testing.

    Uses a nonexistent ffmpeg/ffprobe path so that capture.start() fails
    deterministically without depending on the host environment.
    """
    return LscConfig(
        ffmpeg_path="nonexistent_ffmpeg_binary",
        ffprobe_path="nonexistent_ffprobe_binary",
        output_path=str(tmp_path / "output"),
        output_dir=str(tmp_path / "output"),
    )
