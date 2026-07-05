"""Integration tests that exercise real FFmpeg recording and export.

These tests are skipped automatically when FFmpeg is not available on the
host PATH, so they don't break CI on environments without FFmpeg. When
FFmpeg IS available, they validate the full capture → export pipeline
end-to-end with a synthetic test stream.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time

import pytest

from lsc.config import LscConfig
from lsc.exporter.clip import ClipExporter
from lsc.recorder.capture import StreamCapture


def _ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def _ffprobe_available() -> bool:
    return shutil.which("ffprobe") is not None


pytestmark = pytest.mark.skipif(
    not _ffmpeg_available(),
    reason="FFmpeg not available on PATH — skipping integration tests",
)


@pytest.fixture
def real_config(tmp_path):
    """Config pointing at the real FFmpeg binaries on PATH."""
    return LscConfig(
        ffmpeg_path=shutil.which("ffmpeg") or "ffmpeg",
        ffprobe_path=shutil.which("ffprobe") or "ffprobe",
        output_path=str(tmp_path / "output"),
        output_dir=str(tmp_path / "output"),
    )


def _generate_test_video(path: str, duration: int = 3) -> None:
    """Generate a short test video using FFmpeg's lavfi source.

    Uses testsrc + sine tone so we have a real audio/video stream without
    depending on external files.
    """
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", f"testsrc=duration={duration}:size=320x240:rate=25",
        "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
        "-c:a", "aac", "-b:a", "64k",
        "-f", "mp4", "-movflags", "+faststart",
        path,
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def test_stream_capture_records_local_file(real_config, tmp_path):
    """StreamCapture should record a local file source to an mp4 output."""
    source = str(tmp_path / "source.mp4")
    output = str(tmp_path / "output" / "recorded.mp4")

    _generate_test_video(source, duration=2)

    capture = StreamCapture(real_config)
    # Use the local file as the input URL.
    ok = capture.start(source, output, codec="copy")
    assert ok is True
    assert capture.is_recording is True

    # Let it capture briefly, then stop gracefully.
    time.sleep(1.0)
    result = capture.stop()

    assert result.success is True
    assert os.path.isfile(result.output_path)
    assert result.file_size_mb > 0


def test_stream_capture_returns_error_when_ffmpeg_missing(tmp_path):
    """When ffmpeg_path points to a nonexistent binary, start() must fail
    and expose a human-readable last_error message."""
    cfg = LscConfig(
        ffmpeg_path="/nonexistent/ffmpeg",
        ffprobe_path="/nonexistent/ffprobe",
        output_path=str(tmp_path / "output"),
        output_dir=str(tmp_path / "output"),
    )
    capture = StreamCapture(cfg)
    ok = capture.start("https://example.com/live.m3u8",
                       str(tmp_path / "out.mp4"))
    assert ok is False
    assert capture.last_error != ""
    assert "ffmpeg" in capture.last_error.lower() or "未找到" in capture.last_error


def test_clip_exporter_produces_clip_from_real_video(real_config, tmp_path):
    """ClipExporter.export_clip should produce a valid mp4 from a real source."""
    if not _ffprobe_available():
        pytest.skip("ffprobe not available")

    source = str(tmp_path / "source.mp4")
    output_dir = str(tmp_path / "clips")

    _generate_test_video(source, duration=4)

    exporter = ClipExporter(real_config)
    result = exporter.export_clip(source, 1.0, 3.0, output_dir, title="clip_test")

    assert result.success is True
    assert os.path.isfile(result.output_path)
    assert result.file_size_mb > 0
    # Duration should be approximately 2 seconds (3.0 - 1.0).
    assert 1.5 <= result.duration <= 2.5


def test_clip_exporter_export_all_produces_manifest(real_config, tmp_path):
    """export_all should produce multiple clips and a manifest JSON."""
    if not _ffprobe_available():
        pytest.skip("ffprobe not available")

    source = str(tmp_path / "source.mp4")
    output_dir = str(tmp_path / "clips_all")

    _generate_test_video(source, duration=6)

    highlights = [
        {"start_sec": 0.0, "end_sec": 2.0, "score": 0.9, "description": "first"},
        {"start_sec": 2.0, "end_sec": 4.0, "score": 0.8, "description": "second"},
        {"start_sec": 4.0, "end_sec": 6.0, "score": 0.7, "description": "third"},
    ]

    exporter = ClipExporter(real_config)
    results = list(exporter.export_all(source, highlights, output_dir))

    assert len(results) == 3
    assert all(r.success for r in results)
    manifest_path = ClipExporter.save_export_manifest(source, output_dir, results)
    assert os.path.isfile(manifest_path)
