"""Qt 桥接层单元测试。

测试 QtRecordingService 和 QtExportService 适配器，
验证 Qt 信号是否正确发出。
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import QObject, Signal

from lsc.core.models import Clip, RecordingSession, RecordingStatus, RoomInfo, StreamQuality
from lsc.core.qt import QtExportService, QtRecordingService, RecordingConfig


# ── QtRecordingService 测试 ───────────────────────────────


class TestQtRecordingService:
    def test_create_service(self, qapp):
        svc = QtRecordingService()
        assert isinstance(svc, QObject)
        assert svc.list_sessions() == []

    def test_preflight_check(self, qapp, tmp_path):
        result = QtRecordingService.preflight_check(str(tmp_path), concurrent_streams=1)
        assert isinstance(result, str)

    def test_parse_room_emits_signal(self, qapp):
        svc = QtRecordingService()
        received_rooms: list[RoomInfo] = []
        svc.room_parsed.connect(lambda room: received_rooms.append(room))

        with patch("lsc.core.services.recording_service.parse_stream") as mock_parse:
            from lsc.platforms.base import StreamInfo

            mock_parse.return_value = StreamInfo(
                platform="douyin",
                room_url="https://live.douyin.com/123",
                stream_url="https://example.com/live.flv",
                title="Test Room",
                streamer="Test Streamer",
                is_live=True,
                quality_urls={"原画": "https://example.com/live.flv"},
                selected_quality="原画",
            )

            room = svc.parse_room("https://live.douyin.com/123")

            assert room.platform == "douyin"
            assert room.is_live is True
            assert len(received_rooms) == 1
            assert received_rooms[0].platform == "douyin"

    def test_start_recording_emits_signal(self, qapp, tmp_path):
        svc = QtRecordingService()
        started_sessions: list[RecordingSession] = []
        svc.session_started.connect(lambda s: started_sessions.append(s))

        room = RoomInfo(
            platform="douyin",
            room_url="https://live.douyin.com/123",
            stream_url="https://example.com/live.flv",
            title="Test",
            streamer="Test",
            is_live=True,
            qualities=[StreamQuality(name="原画", url="https://example.com/live.flv")],
            selected_quality="原画",
        )

        with patch("lsc.core.services.recording_service.StreamCapture") as MockCapture:
            mock_capture = MagicMock()
            mock_capture.start.return_value = True
            mock_capture.status.value = "recording"
            mock_capture.last_error = ""
            MockCapture.return_value = mock_capture

            session = svc.start_recording(room, str(tmp_path))

            assert session.status == RecordingStatus.RECORDING
            assert len(started_sessions) == 1
            assert started_sessions[0].session_id == session.session_id

    def test_stop_recording_emits_signal(self, qapp, tmp_path):
        svc = QtRecordingService()
        stopped_sessions: list[RecordingSession] = []
        svc.session_stopped.connect(lambda s: stopped_sessions.append(s))

        room = RoomInfo(
            platform="douyin",
            room_url="https://live.douyin.com/123",
            stream_url="https://example.com/live.flv",
            is_live=True,
        )

        with patch("lsc.core.services.recording_service.StreamCapture") as MockCapture:
            mock_capture = MagicMock()
            mock_capture.start.return_value = True
            mock_capture.status.value = "recording"
            mock_capture.last_error = ""
            from lsc.recorder.capture import CaptureResult

            mock_capture.stop.return_value = CaptureResult(
                success=True,
                output_path=os.path.join(str(tmp_path), "test.mp4"),
                duration_sec=10.0,
                file_size_mb=1.0,
            )
            MockCapture.return_value = mock_capture

            session = svc.start_recording(room, str(tmp_path))
            assert session.status == RecordingStatus.RECORDING

            stopped = svc.stop_recording(session.session_id)
            assert stopped.status == RecordingStatus.STOPPED
            assert len(stopped_sessions) == 1
            assert stopped_sessions[0].session_id == session.session_id

    def test_has_active_recordings(self, qapp, tmp_path):
        svc = QtRecordingService()
        assert svc.has_active_recordings() is False

        room = RoomInfo(
            platform="douyin",
            room_url="https://live.douyin.com/123",
            stream_url="https://example.com/live.flv",
            is_live=True,
        )

        with patch("lsc.core.services.recording_service.StreamCapture") as MockCapture:
            mock_capture = MagicMock()
            mock_capture.start.return_value = True
            mock_capture.status.value = "recording"
            mock_capture.last_error = ""
            MockCapture.return_value = mock_capture

            session = svc.start_recording(room, str(tmp_path))
            assert svc.has_active_recordings() is True
            assert svc.is_recording(session.session_id) is True
            assert svc.is_recording("nonexistent") is False

    def test_status_changed_signal(self, qapp, tmp_path):
        svc = QtRecordingService()
        status_changes: list[RecordingSession] = []
        svc.session_status_changed.connect(lambda s: status_changes.append(s))

        room = RoomInfo(
            platform="douyin",
            room_url="https://live.douyin.com/123",
            stream_url="https://example.com/live.flv",
            is_live=True,
        )

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

            # 开始录制
            session = svc.start_recording(room, str(tmp_path))
            assert len(status_changes) >= 1

            # 停止录制
            prev_count = len(status_changes)
            svc.stop_recording(session.session_id)
            assert len(status_changes) > prev_count

    def test_core_service_access(self, qapp):
        svc = QtRecordingService()
        from lsc.core.services.recording_service import RecordingService

        assert isinstance(svc.core_service, RecordingService)

    def test_recording_config_defaults(self, qapp):
        config = RecordingConfig()
        assert config.encoder == "copy"
        assert config.crf == 23
        assert config.auto_reconnect is True
        assert config.max_reconnect_attempts == 3


# ── QtExportService 测试 ─────────────────────────────────


class TestQtExportService:
    def test_create_service(self, qapp):
        svc = QtExportService(max_concurrent=2)
        assert isinstance(svc, QObject)
        assert svc.get_active_count() == 0
        assert svc.has_active_exports() is False

    def test_export_clip_sync_emits_done_signal(self, qapp, tmp_path):
        svc = QtExportService()
        done_results: list = []
        svc.export_done.connect(lambda r: done_results.append(r))

        clip = Clip(clip_id="test-1", title="Test Clip", start_sec=0.0, end_sec=10.0)

        # 直接调用 _on_done 来测试信号转发
        from lsc.core.models import ExportResult

        test_result = ExportResult(
            success=True,
            clip_id="test-1",
            output_path=str(tmp_path / "out.mp4"),
            duration_sec=10.0,
            file_size_mb=2.0,
        )
        svc._on_done(test_result)

        assert len(done_results) == 1
        assert done_results[0].clip_id == "test-1"

    def test_export_clip_async_emits_started_signal(self, qapp, tmp_path):
        svc = QtExportService()
        started_ids: list[str] = []
        svc.export_started.connect(lambda cid: started_ids.append(cid))

        clip = Clip(clip_id="async-1", title="Async Clip", start_sec=0.0, end_sec=5.0)

        with patch.object(svc.core_service, "export_clip") as mock_export:
            mock_export.return_value = None

            result = svc.export_clip(
                "/tmp/source.mp4",
                clip,
                str(tmp_path),
                async_mode=True,
            )

            assert result is None
            assert len(started_ids) == 1
            assert started_ids[0] == "async-1"

        svc.cleanup()

    def test_safe_filename(self, qapp):
        result = QtExportService.safe_filename('test/:*?"<>|')
        assert "/" not in result
        assert ":" not in result
        assert "*" not in result

    def test_generate_thumbnail_emits_signal(self, qapp, tmp_path):
        svc = QtExportService()
        thumb_results: list = []
        svc.thumbnail_done.connect(lambda name, path: thumb_results.append((name, path)))

        # name 参数会加上 _thumb.jpg 后缀
        thumb_file = tmp_path / "myclip_thumb.jpg"
        thumb_file.touch()

        with patch("subprocess.run") as mock_run:
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_run.return_value = mock_result

            result = svc.generate_thumbnail(
                str(tmp_path / "video.mp4"),
                5.0,
                str(tmp_path),
                "myclip",
            )

            assert result.endswith("myclip_thumb.jpg")
            assert len(thumb_results) == 1
            assert thumb_results[0][0] == "myclip"

    def test_cancel_export_not_found(self, qapp):
        svc = QtExportService()
        assert svc.cancel_export("nonexistent") is False

    def test_core_service_access(self, qapp):
        svc = QtExportService()
        from lsc.core.services.export_service import ExportService

        assert isinstance(svc.core_service, ExportService)
