"""Tests for recorder module."""
from __future__ import annotations

from collections import deque
from unittest.mock import MagicMock

import pytest

from lsc.recorder.capture import CaptureStatus, StreamCapture
from lsc.recorder.session import RecordingSession


@pytest.fixture
def capture(sample_config):
    return StreamCapture(sample_config)


@pytest.fixture
def session(sample_config):
    return RecordingSession(sample_config)


class TestStreamCapture:
    def test_init(self, capture):
        assert capture.status == CaptureStatus.IDLE
        assert not capture.is_recording
        assert capture.duration == 0.0

    def test_stop_when_not_recording(self, capture):
        result = capture.stop()
        assert not result.success
        assert "not recording" in result.error.lower()


class TestRecordingSession:
    def test_init(self, session):
        assert not session.is_recording
        assert session.session is None
        assert session.duration == 0.0

    def test_stop_when_no_session(self, session):
        result = session.stop()
        assert not result.success


class TestStreamCaptureExtended:
    def test_status_callback(self, capture):
        statuses = []
        capture.set_status_callback(lambda s: statuses.append(s))
        assert capture.status == CaptureStatus.IDLE

    def test_start_nonexistent_ffmpeg(self, capture, tmp_path):
        result = capture.start("http://example.com/stream", str(tmp_path / "output.mp4"))
        assert not result
        assert capture.status == CaptureStatus.ERROR

    def test_duration_when_idle(self, capture):
        assert capture.duration == 0.0

    def test_start_already_recording(self, capture, tmp_path):
        capture._status = CaptureStatus.RECORDING
        result = capture.start("http://example.com/stream", str(tmp_path / "out.mp4"))
        assert not result

    def test_stop_when_error_status(self, capture):
        capture._status = CaptureStatus.ERROR
        result = capture.stop()
        assert not result.success

    def test_pause_when_not_recording(self, capture):
        capture.pause()
        assert capture.status == CaptureStatus.IDLE

    def test_resume_when_not_paused(self, capture):
        capture.resume()
        assert capture.status == CaptureStatus.IDLE

    def test_check_health_reports_stall_after_three_static_size_checks(self, capture, tmp_path):
        output = tmp_path / "stalled.mp4"
        output.write_bytes(b"x" * 16)

        capture._status = CaptureStatus.RECORDING
        capture._output_path = str(output)
        capture._process = type("Proc", (), {"poll": lambda self: None})()

        assert capture.check_health() == ""
        assert capture.check_health() == ""
        assert capture.check_health() == ""
        assert capture.check_health() == "输出文件长时间未增长，录制可能已卡住"


class TestRecordingSessionExtended:
    def test_start_with_custom_output_dir(self, session, tmp_path):
        success = session.start("http://example.com/stream", str(tmp_path))
        assert not success
        assert session.session is not None
        assert session.session.status == "error"

    def test_stop_when_no_active_session(self, session):
        result = session.stop()
        assert not result.success

    def test_start_already_recording(self, session, tmp_path):
        session._capture._status = CaptureStatus.RECORDING
        success = session.start("http://example.com/stream", str(tmp_path))
        assert not success


class TestStderrDiagnostics:
    def test_check_health_uses_friendly_ffmpeg_message(self, sample_config):
        capture = StreamCapture(sample_config)
        capture._status = CaptureStatus.RECORDING
        capture._process = MagicMock()
        capture._process.poll.return_value = 403
        capture._process.returncode = 403
        capture._stderr_tail = deque(
            ["Server returned 403 Forbidden for stream request"],
            maxlen=80,
        )

        message = capture.check_health()

        assert "直播流鉴权失败" in message
        assert "403" in message
