"""Shared test fixtures."""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Add python-backend directory to path so tests can import from it
import sys
_python_backend = os.path.join(os.path.dirname(__file__), '..', 'python-backend')
if _python_backend not in sys.path:
    sys.path.insert(0, _python_backend)

from lsc.config import LscConfig  # noqa: E402




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

