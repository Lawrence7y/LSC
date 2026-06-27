"""Shared test fixtures."""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from lsc.config import LscConfig


@pytest.fixture(scope="session")
def qapp():
    """提供全局 QApplication 实例。

    所有 Qt 相关的测试都应该使用此 fixture，
    确保整个测试会话中只有一个 QApplication 实例。
    """
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app
    app.quit()


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
