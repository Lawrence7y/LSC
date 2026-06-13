"""Tests for ClipExporter, ExportResult, and I/O paths."""
from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from lsc.exporter.clip import ClipExporter, ExportResult


@pytest.fixture
def exporter(sample_config):
    return ClipExporter(sample_config)


class TestExportResult:
    def test_success_result(self):
        r = ExportResult(True, "/path/to/clip.mp4", 1, "test_clip", 10.0, 5.5)
        assert r.success is True
        assert r.output_path == "/path/to/clip.mp4"
        assert r.clip_index == 1
        assert r.title == "test_clip"
        assert r.duration == 10.0
        assert r.file_size_mb == 5.5
        assert r.error == ""
        assert r.thumbnail_path == ""

    def test_failure_result(self):
        r = ExportResult(False, "", 1, "test_clip", error="Video not found")
        assert r.success is False
        assert r.error == "Video not found"

    def test_defaults(self):
        r = ExportResult(True, "/path", 0, "")
        assert r.duration == 0.0
        assert r.file_size_mb == 0.0
        assert r.thumbnail_path == ""
        assert r.error == ""


class TestClipExporter:
    def test_export_clip_nonexistent_video(self, exporter, tmp_path):
        result = exporter.export_clip(
            "/nonexistent/video.mp4", 0.0, 10.0, str(tmp_path)
        )
        assert result.success is False
        assert "not found" in result.error.lower()

    def test_export_clip_short_duration(self, exporter, tmp_path):
        dummy = tmp_path / "video.mp4"
        dummy.write_bytes(b"fake")
        result = exporter.export_clip(
            str(dummy), 5.0, 5.5, str(tmp_path / "output")
        )
        assert result.success is False
        assert "short" in result.error.lower()


class TestExporterIO:
    def test_export_nonexistent_video(self, exporter, tmp_path):
        result = exporter.export_clip("/nonexistent/video.mp4", 0, 10, str(tmp_path))
        assert not result.success
        assert "not found" in result.error.lower()

    def test_export_short_duration(self, exporter, tmp_path):
        dummy = tmp_path / "video.mp4"
        dummy.write_bytes(b"fake")
        result = exporter.export_clip(str(dummy), 5.0, 5.5, str(tmp_path / "out"))
        assert not result.success
        assert "short" in result.error.lower()

    def test_export_ffmpeg_timeout(self, exporter, tmp_path):
        dummy = tmp_path / "video.mp4"
        dummy.write_bytes(b"fake")
        with patch('subprocess.run') as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(cmd="ffmpeg", timeout=300)
            result = exporter.export_clip(str(dummy), 0, 10, str(tmp_path / "out"))
            assert not result.success
            assert "timed out" in result.error.lower()

    def test_export_ffmpeg_error(self, exporter, tmp_path):
        dummy = tmp_path / "video.mp4"
        dummy.write_bytes(b"fake")
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stderr="ffmpeg error")
            result = exporter.export_clip(str(dummy), 0, 10, str(tmp_path / "out"))
            assert not result.success
            assert "ffmpeg error" in result.error


def test_parse_ffmpeg_progress_line_extracts_out_time_ms() -> None:
    from lsc.exporter.clip import parse_ffmpeg_progress_line

    state = {}
    parse_ffmpeg_progress_line("out_time_ms=15000000", state)
    assert state["out_time_ms"] == 15000000
