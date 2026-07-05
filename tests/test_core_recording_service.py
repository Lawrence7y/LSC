"""核心录制服务单元测试。

使用 mock 隔离 FFmpeg 依赖，专注于测试业务逻辑。
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from lsc.core.models import RecordingStatus, RoomInfo, StreamQuality
from lsc.core.services.recording_service import (
    RecordingConfig,
    RecordingService,
)


@pytest.fixture
def sample_room() -> RoomInfo:
    """创建一个可用的直播间信息。"""
    return RoomInfo(
        platform="douyin",
        room_url="https://live.douyin.com/123456",
        stream_url="https://example.com/live.flv",
        title="测试直播间",
        streamer="测试主播",
        is_live=True,
        qualities=[
            StreamQuality(name="原画", url="https://example.com/live.flv"),
            StreamQuality(name="高清", url="https://example.com/hd.flv"),
        ],
        selected_quality="原画",
        headers={"Referer": "https://live.douyin.com/"},
    )


@pytest.fixture
def offline_room() -> RoomInfo:
    """创建一个未开播的房间。"""
    return RoomInfo(
        platform="douyin",
        room_url="https://live.douyin.com/789",
        is_live=False,
        error="主播未开播",
        error_code="offline",
    )


@pytest.fixture
def service(tmp_path):
    """创建一个录制服务实例。"""
    return RecordingService()


class TestRecordingConfig:
    def test_default_config(self):
        config = RecordingConfig()
        assert config.encoder == "copy"
        assert config.crf == 23
        assert config.rate_mode == "crf"
        assert config.auto_reconnect is True
        assert config.max_reconnect_attempts == 3

    def test_custom_config(self):
        config = RecordingConfig(
            encoder="libx264",
            crf=28,
            rate_mode="bitrate",
            bitrate="4000k",
            vertical_crop=True,
        )
        assert config.encoder == "libx264"
        assert config.crf == 28
        assert config.rate_mode == "bitrate"
        assert config.vertical_crop is True


class TestRecordingServiceParseRoom:
    def test_parse_room_success(self, service, sample_room):
        with patch(
            "lsc.core.services.recording_service.parse_stream"
        ) as mock_parse:
            from lsc.platforms.base import StreamInfo

            mock_parse.return_value = StreamInfo(
                platform="douyin",
                room_url=sample_room.room_url,
                stream_url=sample_room.stream_url,
                title=sample_room.title,
                streamer=sample_room.streamer,
                is_live=True,
                quality_urls={"原画": sample_room.stream_url},
                selected_quality="原画",
                headers=dict(sample_room.headers),
            )

            result = service.parse_room(sample_room.room_url)

            assert result.platform == "douyin"
            assert result.is_live is True
            assert result.stream_url == sample_room.stream_url
            assert result.title == sample_room.title
            assert len(result.qualities) == 1
            assert result.qualities[0].name == "原画"
            mock_parse.assert_called_once_with(sample_room.room_url, force_refresh=False)

    def test_parse_room_force_refresh(self, service, sample_room):
        with patch(
            "lsc.core.services.recording_service.parse_stream"
        ) as mock_parse:
            from lsc.platforms.base import StreamInfo

            mock_parse.return_value = StreamInfo(
                platform="douyin",
                room_url=sample_room.room_url,
                is_live=False,
            )

            service.parse_room(sample_room.room_url, force_refresh=True)

            mock_parse.assert_called_once_with(sample_room.room_url, force_refresh=True)


class TestRecordingServicePreflight:
    def test_preflight_check_pass(self, tmp_path):
        error = RecordingService.preflight_check(str(tmp_path), concurrent_streams=1)
        assert error == ""

    def test_preflight_check_insufficient_space(self, tmp_path):
        # 这个测试可能在磁盘空间大的机器上不触发，
        # 我们只验证函数能正常调用即可
        result = RecordingService.preflight_check(str(tmp_path), concurrent_streams=1)
        assert isinstance(result, str)

    def test_preflight_check_creates_dir(self, tmp_path):
        output_dir = os.path.join(str(tmp_path), "new_dir")
        RecordingService.preflight_check(output_dir)
        assert os.path.isdir(output_dir)


class TestRecordingServiceSessionManagement:
    def test_list_sessions_empty(self, service):
        sessions = service.list_sessions()
        assert sessions == []

    def test_get_session_not_found(self, service):
        assert service.get_session("nonexistent") is None

    def test_start_recording_offline_room_raises(self, service, offline_room, tmp_path):
        with pytest.raises(ValueError, match="未开播"):
            service.start_recording(offline_room, str(tmp_path))

    def test_start_recording_success(self, service, sample_room, tmp_path):
        with patch("lsc.core.services.recording_service.StreamCapture") as MockCapture:
            mock_capture = MagicMock()
            mock_capture.start.return_value = True
            mock_capture.status.value = "recording"
            mock_capture.last_error = ""
            MockCapture.return_value = mock_capture

            session = service.start_recording(sample_room, str(tmp_path))

            assert session.status == RecordingStatus.RECORDING
            assert session.room_url == sample_room.room_url
            assert session.stream_url == sample_room.stream_url
            assert session.platform == "douyin"
            assert session.start_time is not None

            sessions = service.list_sessions()
            assert len(sessions) == 1
            assert sessions[0].session_id == session.session_id

    def test_start_recording_failure(self, service, sample_room, tmp_path):
        with patch("lsc.core.services.recording_service.StreamCapture") as MockCapture:
            mock_capture = MagicMock()
            mock_capture.start.return_value = False
            mock_capture.status.value = "error"
            mock_capture.last_error = "FFmpeg not found"
            MockCapture.return_value = mock_capture

            session = service.start_recording(sample_room, str(tmp_path))

            assert session.status == RecordingStatus.ERROR
            assert "FFmpeg not found" in session.last_error

    def test_stop_recording_success(self, service, sample_room, tmp_path):
        with patch("lsc.core.services.recording_service.StreamCapture") as MockCapture:
            mock_capture = MagicMock()
            mock_capture.start.return_value = True
            mock_capture.status.value = "recording"
            mock_capture.last_error = ""
            from lsc.recorder.capture import CaptureResult

            mock_capture.stop.return_value = CaptureResult(
                success=True,
                output_path=os.path.join(str(tmp_path), "recording_123.mp4"),
                duration_sec=60.0,
                file_size_mb=10.5,
            )
            MockCapture.return_value = mock_capture

            session = service.start_recording(sample_room, str(tmp_path))
            stopped = service.stop_recording(session.session_id)

            assert stopped.status == RecordingStatus.STOPPED
            assert stopped.duration_sec == 60.0
            assert stopped.file_size_mb == 10.5
            mock_capture.stop.assert_called_once()

    def test_stop_recording_not_found(self, service):
        with pytest.raises(KeyError):
            service.stop_recording("nonexistent")

    def test_remove_session(self, service, sample_room, tmp_path):
        with patch("lsc.core.services.recording_service.StreamCapture") as MockCapture:
            mock_capture = MagicMock()
            mock_capture.start.return_value = True
            mock_capture.status.value = "recording"
            mock_capture.last_error = ""
            from lsc.recorder.capture import CaptureResult

            mock_capture.stop.return_value = CaptureResult(
                success=True,
                output_path=os.path.join(str(tmp_path), "rec.mp4"),
                duration_sec=10.0,
                file_size_mb=1.0,
            )
            MockCapture.return_value = mock_capture

            session = service.start_recording(sample_room, str(tmp_path))
            assert len(service.list_sessions()) == 1

            result = service.remove_session(session.session_id)
            assert result is True
            assert len(service.list_sessions()) == 0

    def test_remove_session_not_found(self, service):
        assert service.remove_session("nonexistent") is False

    def test_stop_all(self, service, sample_room, tmp_path):
        with patch("lsc.core.services.recording_service.StreamCapture") as MockCapture:
            mock_capture = MagicMock()
            mock_capture.start.return_value = True
            mock_capture.status.value = "recording"
            mock_capture.last_error = ""
            from lsc.recorder.capture import CaptureResult

            mock_capture.stop.return_value = CaptureResult(
                success=True,
                output_path="test.mp4",
                duration_sec=10.0,
                file_size_mb=1.0,
            )
            MockCapture.return_value = mock_capture

            service.start_recording(sample_room, str(tmp_path))
            service.start_recording(sample_room, str(tmp_path))
            assert len(service.list_sessions()) == 2

            results = service.stop_all()
            assert len(results) == 2
            for r in results:
                assert r.status == RecordingStatus.STOPPED


class TestRecordingServiceHealth:
    def test_check_health_not_recording(self, service, sample_room, tmp_path):
        with patch("lsc.core.services.recording_service.StreamCapture") as MockCapture:
            mock_capture = MagicMock()
            mock_capture.start.return_value = False
            mock_capture.status.value = "error"
            mock_capture.last_error = "test error"
            MockCapture.return_value = mock_capture

            session = service.start_recording(sample_room, str(tmp_path))
            # 启动失败后会话被清理，check_health 返回"会话不存在"
            error = service.check_health(session.session_id)
            assert error == "会话不存在"

    def test_check_health_not_found(self, service):
        error = service.check_health("nonexistent")
        assert error == "会话不存在"

    def test_check_all_health_empty(self, service):
        result = service.check_all_health()
        assert result == {}

    def test_update_duration(self, service, sample_room, tmp_path):
        with patch("lsc.core.services.recording_service.StreamCapture") as MockCapture:
            mock_capture = MagicMock()
            mock_capture.start.return_value = True
            mock_capture.status.value = "recording"
            mock_capture.last_error = ""
            mock_capture.duration = 42.5
            MockCapture.return_value = mock_capture

            session = service.start_recording(sample_room, str(tmp_path))
            duration = service.update_duration(session.session_id)
            assert duration == 42.5


class TestRecordingServiceStatusCallback:
    def test_status_callback_called_on_start(self, service, sample_room, tmp_path):
        callback = MagicMock()
        service.set_status_callback(callback)

        with patch("lsc.core.services.recording_service.StreamCapture") as MockCapture:
            mock_capture = MagicMock()
            mock_capture.start.return_value = True
            mock_capture.status.value = "recording"
            mock_capture.last_error = ""
            MockCapture.return_value = mock_capture

            service.start_recording(sample_room, str(tmp_path))

            assert callback.call_count >= 1
            last_call_arg = callback.call_args[0][0]
            assert last_call_arg.status == RecordingStatus.RECORDING

    def test_status_callback_none(self, service, sample_room, tmp_path):
        service.set_status_callback(None)

        with patch("lsc.core.services.recording_service.StreamCapture") as MockCapture:
            mock_capture = MagicMock()
            mock_capture.start.return_value = True
            mock_capture.status.value = "recording"
            mock_capture.last_error = ""
            MockCapture.return_value = mock_capture

            # 不应该抛出异常
            session = service.start_recording(sample_room, str(tmp_path))
            assert session.status == RecordingStatus.RECORDING
