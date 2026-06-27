"""核心领域模型单元测试。"""

from __future__ import annotations

import pytest

from lsc.core.models import (
    Clip,
    ExportOptions,
    ExportResult,
    RecordingSession,
    RecordingStatus,
    RoomInfo,
    StreamQuality,
)


class TestRecordingStatus:
    def test_status_values_are_strings(self):
        assert RecordingStatus.IDLE == "idle"
        assert RecordingStatus.RECORDING == "recording"
        assert RecordingStatus.STOPPED == "stopped"
        assert RecordingStatus.ERROR == "error"

    def test_status_is_enum(self):
        assert isinstance(RecordingStatus.IDLE, RecordingStatus)
        assert RecordingStatus("recording") == RecordingStatus.RECORDING


class TestStreamQuality:
    def test_create_quality(self):
        q = StreamQuality(name="原画", url="https://example.com/stream.flv")
        assert q.name == "原画"
        assert q.url == "https://example.com/stream.flv"

    def test_quality_is_mutable(self):
        q = StreamQuality(name="高清", url="https://example.com/hd.flv")
        q.name = "720p"
        assert q.name == "720p"


class TestRoomInfo:
    def test_create_room_info_defaults(self):
        room = RoomInfo(platform="douyin", room_url="https://live.douyin.com/123")
        assert room.platform == "douyin"
        assert room.room_url == "https://live.douyin.com/123"
        assert room.stream_url == ""
        assert room.title == ""
        assert room.streamer == ""
        assert room.is_live is False
        assert room.qualities == []
        assert room.selected_quality == ""
        assert room.headers == {}
        assert room.error == ""
        assert room.error_code == ""
        assert room.raw == {}

    def test_create_room_info_with_values(self):
        qualities = [
            StreamQuality(name="原画", url="https://example.com/original.flv"),
            StreamQuality(name="高清", url="https://example.com/hd.flv"),
        ]
        room = RoomInfo(
            platform="bilibili",
            room_url="https://live.bilibili.com/123",
            stream_url="https://example.com/original.flv",
            title="测试直播间",
            streamer="测试主播",
            is_live=True,
            qualities=qualities,
            selected_quality="原画",
            headers={"Referer": "https://example.com/"},
            error="",
            error_code="",
            raw={"key": "value"},
        )
        assert room.is_live is True
        assert len(room.qualities) == 2
        assert room.qualities[0].name == "原画"
        assert room.headers["Referer"] == "https://example.com/"


class TestRecordingSession:
    def test_create_session_defaults(self):
        session = RecordingSession(
            session_id="test-123",
            room_url="https://example.com/live",
            output_dir="/tmp/recordings",
        )
        assert session.session_id == "test-123"
        assert session.room_url == "https://example.com/live"
        assert session.output_dir == "/tmp/recordings"
        assert session.status == RecordingStatus.IDLE
        assert session.output_path == ""
        assert session.stream_url == ""
        assert session.duration_sec == 0.0
        assert session.file_size_mb == 0.0
        assert session.reconnect_attempts == 0
        assert session.max_reconnect_attempts == 3

    def test_session_status_transition(self):
        session = RecordingSession(
            session_id="s1", room_url="https://example.com", output_dir="/tmp"
        )
        assert session.status == RecordingStatus.IDLE
        session.status = RecordingStatus.RECORDING
        assert session.status == RecordingStatus.RECORDING
        session.status = RecordingStatus.STOPPED
        assert session.status == RecordingStatus.STOPPED


class TestClip:
    def test_create_clip_defaults(self):
        clip = Clip(
            clip_id="clip-1",
            title="精彩片段",
            start_sec=10.5,
            end_sec=30.0,
        )
        assert clip.clip_id == "clip-1"
        assert clip.title == "精彩片段"
        assert clip.start_sec == 10.5
        assert clip.end_sec == 30.0
        assert clip.duration_sec == 0.0
        assert clip.exported is False
        assert clip.error == ""

    def test_clip_duration_property_like(self):
        clip = Clip(clip_id="c1", title="test", start_sec=0.0, end_sec=15.5)
        # 计算的时长应该约等于 end - start
        assert clip.end_sec - clip.start_sec == pytest.approx(15.5)


class TestExportOptions:
    def test_default_options(self):
        opts = ExportOptions()
        assert opts.codec == "libx264"
        assert opts.crf == 23
        assert opts.preset == "medium"
        assert opts.audio_bitrate == "128k"
        assert opts.rate_mode == "crf"
        assert opts.video_bitrate == "8000k"
        assert opts.resolution == ""
        assert opts.fps == 0.0
        assert opts.vertical_crop is False
        assert opts.generate_thumbnail is True

    def test_custom_options(self):
        opts = ExportOptions(
            codec="h264_nvenc",
            crf=28,
            rate_mode="bitrate",
            video_bitrate="6000k",
            vertical_crop=True,
        )
        assert opts.codec == "h264_nvenc"
        assert opts.crf == 28
        assert opts.rate_mode == "bitrate"
        assert opts.vertical_crop is True


class TestExportResult:
    def test_success_result(self):
        result = ExportResult(
            success=True,
            clip_id="clip-1",
            output_path="/tmp/output.mp4",
            duration_sec=19.5,
            file_size_mb=4.2,
        )
        assert result.success is True
        assert result.clip_id == "clip-1"
        assert result.output_path == "/tmp/output.mp4"
        assert result.duration_sec == 19.5
        assert result.file_size_mb == 4.2
        assert result.error == ""

    def test_failure_result(self):
        result = ExportResult(
            success=False,
            clip_id="clip-2",
            error="FFmpeg error",
        )
        assert result.success is False
        assert result.error == "FFmpeg error"
