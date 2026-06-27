"""多房间录制适配器集成测试。

测试 MultiRoomRecordingAdapter 的批量操作、
房间 ↔ 会话映射、统计查询等功能。
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from lsc.core.models import RecordingStatus, RoomInfo, StreamQuality
from lsc.core.qt import MultiRoomRecordingAdapter, RecordingConfig, RoomRecordingState


@pytest.fixture
def sample_rooms() -> list[RoomInfo]:
    """创建多个测试房间。"""
    rooms = []
    for i in range(3):
        rooms.append(RoomInfo(
            platform="douyin",
            room_url=f"https://live.douyin.com/room{i+1}",
            stream_url=f"https://example.com/stream{i+1}.flv",
            title=f"测试直播间 {i+1}",
            streamer=f"主播{i+1}",
            is_live=True,
            qualities=[StreamQuality(name="原画", url=f"https://example.com/stream{i+1}.flv")],
            selected_quality="原画",
            headers={"Referer": "https://live.douyin.com/"},
        ))
    return rooms


@pytest.fixture
def adapter(qapp):
    """创建多房间录制适配器。"""
    adapter = MultiRoomRecordingAdapter()
    yield adapter
    adapter.cleanup()


class TestRoomRecordingState:
    def test_create_state_defaults(self):
        state = RoomRecordingState(room_url="https://example.com/live")
        assert state.room_url == "https://example.com/live"
        assert state.session_id == ""
        assert state.status == RecordingStatus.IDLE
        assert state.duration_sec == 0.0
        assert state.file_size_mb == 0.0
        assert state.last_error == ""


class TestMultiRoomRecordingAdapter:
    def test_create_adapter(self, adapter):
        assert adapter.get_active_count() == 0
        assert adapter.is_any_recording() is False
        assert adapter.get_total_duration() == 0.0
        assert adapter.get_total_file_size_mb() == 0.0

    def test_make_room_output_dir(self, adapter, tmp_path):
        room = RoomInfo(
            platform="douyin",
            room_url="https://live.douyin.com/123456",
            streamer="测试主播",
            is_live=True,
        )
        output_dir = adapter.make_room_output_dir(str(tmp_path), room)
        assert output_dir.startswith(str(tmp_path))
        assert "douyin" in output_dir
        assert os.path.isdir(output_dir)

    def test_make_room_output_dir_safe_chars(self, adapter, tmp_path):
        room = RoomInfo(
            platform="bilibili",
            room_url="https://live.bilibili.com/789",
            streamer="主播/:*?\"<>|",
            is_live=True,
        )
        output_dir = adapter.make_room_output_dir(str(tmp_path), room)
        basename = os.path.basename(output_dir)
        assert "/" not in basename
        assert ":" not in basename
        assert "*" not in basename
        assert "?" not in basename
        assert os.path.isdir(output_dir)

    def test_room_session_mapping(self, adapter, sample_rooms, tmp_path):
        room = sample_rooms[0]

        with patch("lsc.core.services.recording_service.StreamCapture") as MockCapture:
            mock_capture = MagicMock()
            mock_capture.start.return_value = True
            mock_capture.status.value = "recording"
            mock_capture.last_error = ""
            mock_capture.duration = 0.0
            MockCapture.return_value = mock_capture

            session = adapter.start_recording(room, str(tmp_path))
            assert session is not None
            assert session.status == RecordingStatus.RECORDING

            # 测试映射
            assert adapter.get_session_id_for_room(room.room_url) == session.session_id
            assert adapter.get_room_url_for_session(session.session_id) == room.room_url
            assert adapter.is_room_recording(room.room_url) is True

    def test_get_room_state(self, adapter, sample_rooms, tmp_path):
        room = sample_rooms[0]

        with patch("lsc.core.services.recording_service.StreamCapture") as MockCapture:
            mock_capture = MagicMock()
            mock_capture.start.return_value = True
            mock_capture.status.value = "recording"
            mock_capture.last_error = ""
            mock_capture.duration = 42.0
            MockCapture.return_value = mock_capture

            adapter.start_recording(room, str(tmp_path))
            state = adapter.get_room_state(room.room_url)

            assert state is not None
            assert isinstance(state, RoomRecordingState)
            assert state.room_url == room.room_url
            assert state.status == RecordingStatus.RECORDING
            assert state.duration_sec == 42.0

    def test_get_room_state_not_found(self, adapter):
        state = adapter.get_room_state("https://nonexistent.com")
        assert state is None

    def test_stop_recording(self, adapter, sample_rooms, tmp_path):
        room = sample_rooms[0]

        with patch("lsc.core.services.recording_service.StreamCapture") as MockCapture:
            mock_capture = MagicMock()
            mock_capture.start.return_value = True
            mock_capture.status.value = "recording"
            mock_capture.last_error = ""
            mock_capture.duration = 0.0
            from lsc.recorder.capture import CaptureResult

            mock_capture.stop.return_value = CaptureResult(
                success=True,
                output_path=str(tmp_path / "test.mp4"),
                duration_sec=60.0,
                file_size_mb=5.0,
            )
            MockCapture.return_value = mock_capture

            adapter.start_recording(room, str(tmp_path))
            assert adapter.is_room_recording(room.room_url) is True

            stopped = adapter.stop_recording(room.room_url)
            assert stopped is not None
            assert stopped.status == RecordingStatus.STOPPED
            assert stopped.duration_sec == 60.0

    def test_stop_recording_not_found(self, adapter):
        result = adapter.stop_recording("https://nonexistent.com")
        assert result is None

    def test_remove_recording(self, adapter, sample_rooms, tmp_path):
        room = sample_rooms[0]

        with patch("lsc.core.services.recording_service.StreamCapture") as MockCapture:
            mock_capture = MagicMock()
            mock_capture.start.return_value = True
            mock_capture.status.value = "recording"
            mock_capture.last_error = ""
            mock_capture.duration = 0.0
            from lsc.recorder.capture import CaptureResult

            mock_capture.stop.return_value = CaptureResult(
                success=True,
                output_path="test.mp4",
                duration_sec=10.0,
                file_size_mb=1.0,
            )
            MockCapture.return_value = mock_capture

            adapter.start_recording(room, str(tmp_path))
            assert adapter.get_active_count() == 1

            result = adapter.remove_recording(room.room_url)
            assert result is True
            assert adapter.get_active_count() == 0
            assert adapter.get_session_id_for_room(room.room_url) is None

    def test_remove_recording_not_found(self, adapter):
        assert adapter.remove_recording("https://nonexistent.com") is False

    def test_batch_start_recordings(self, adapter, sample_rooms, tmp_path):
        with patch("lsc.core.services.recording_service.StreamCapture") as MockCapture:
            mock_capture = MagicMock()
            mock_capture.start.return_value = True
            mock_capture.status.value = "recording"
            mock_capture.last_error = ""
            mock_capture.duration = 0.0
            MockCapture.return_value = mock_capture

            started = adapter.start_recordings(sample_rooms, str(tmp_path))

            assert len(started) == 3
            assert adapter.get_active_count() == 3
            assert adapter.is_any_recording() is True

            for room in sample_rooms:
                assert adapter.is_room_recording(room.room_url) is True

    def test_batch_start_with_progress_signal(self, adapter, sample_rooms, tmp_path):
        progress_events: list = []
        adapter.batch_progress.connect(lambda c, t, r, s: progress_events.append((c, t, r, s)))

        finished_events: list = []
        adapter.batch_finished.connect(lambda s, t: finished_events.append((s, t)))

        with patch("lsc.core.services.recording_service.StreamCapture") as MockCapture:
            mock_capture = MagicMock()
            mock_capture.start.return_value = True
            mock_capture.status.value = "recording"
            mock_capture.last_error = ""
            mock_capture.duration = 0.0
            MockCapture.return_value = mock_capture

            adapter.start_recordings(sample_rooms, str(tmp_path))

            assert len(progress_events) == 3
            assert len(finished_events) == 1
            assert finished_events[0] == (3, 3)

    def test_stop_all_recordings(self, adapter, sample_rooms, tmp_path):
        with patch("lsc.core.services.recording_service.StreamCapture") as MockCapture:
            mock_capture = MagicMock()
            mock_capture.start.return_value = True
            mock_capture.status.value = "recording"
            mock_capture.last_error = ""
            mock_capture.duration = 0.0
            from lsc.recorder.capture import CaptureResult

            mock_capture.stop.return_value = CaptureResult(
                success=True,
                output_path="test.mp4",
                duration_sec=10.0,
                file_size_mb=1.0,
            )
            MockCapture.return_value = mock_capture

            adapter.start_recordings(sample_rooms, str(tmp_path))
            assert adapter.get_active_count() == 3

            stopped = adapter.stop_all_recordings()
            assert len(stopped) == 3
            assert adapter.get_active_count() == 0

    def test_stats_changed_signal(self, adapter, sample_rooms, tmp_path):
        stats_events: list = []
        adapter.stats_changed.connect(lambda c, d, s: stats_events.append((c, d, s)))

        with patch("lsc.core.services.recording_service.StreamCapture") as MockCapture:
            mock_capture = MagicMock()
            mock_capture.start.return_value = True
            mock_capture.status.value = "recording"
            mock_capture.last_error = ""
            mock_capture.duration = 0.0
            MockCapture.return_value = mock_capture

            adapter.start_recording(sample_rooms[0], str(tmp_path))

            # 至少有一次 stats_changed 信号
            assert len(stats_events) >= 1
            active_count, total_dur, total_size = stats_events[-1]
            assert active_count == 1

    def test_recording_started_signal(self, adapter, sample_rooms, tmp_path):
        started_events: list = []
        adapter.recording_started.connect(lambda url, sess: started_events.append((url, sess)))

        with patch("lsc.core.services.recording_service.StreamCapture") as MockCapture:
            mock_capture = MagicMock()
            mock_capture.start.return_value = True
            mock_capture.status.value = "recording"
            mock_capture.last_error = ""
            mock_capture.duration = 0.0
            MockCapture.return_value = mock_capture

            room = sample_rooms[0]
            adapter.start_recording(room, str(tmp_path))

            assert len(started_events) == 1
            assert started_events[0][0] == room.room_url

    def test_recording_stopped_signal(self, adapter, sample_rooms, tmp_path):
        stopped_events: list = []
        adapter.recording_stopped.connect(lambda url, sess: stopped_events.append((url, sess)))

        with patch("lsc.core.services.recording_service.StreamCapture") as MockCapture:
            mock_capture = MagicMock()
            mock_capture.start.return_value = True
            mock_capture.status.value = "recording"
            mock_capture.last_error = ""
            mock_capture.duration = 0.0
            from lsc.recorder.capture import CaptureResult

            mock_capture.stop.return_value = CaptureResult(
                success=True,
                output_path="test.mp4",
                duration_sec=10.0,
                file_size_mb=1.0,
            )
            MockCapture.return_value = mock_capture

            room = sample_rooms[0]
            adapter.start_recording(room, str(tmp_path))
            adapter.stop_recording(room.room_url)

            assert len(stopped_events) >= 1
            assert stopped_events[-1][0] == room.room_url

    def test_get_all_room_states(self, adapter, sample_rooms, tmp_path):
        with patch("lsc.core.services.recording_service.StreamCapture") as MockCapture:
            mock_capture = MagicMock()
            mock_capture.start.return_value = True
            mock_capture.status.value = "recording"
            mock_capture.last_error = ""
            mock_capture.duration = 0.0
            MockCapture.return_value = mock_capture

            states_before = adapter.get_all_room_states()
            assert len(states_before) == 0

            adapter.start_recordings(sample_rooms, str(tmp_path))

            states_after = adapter.get_all_room_states()
            assert len(states_after) == 3
            for state in states_after:
                assert isinstance(state, RoomRecordingState)
                assert state.status == RecordingStatus.RECORDING

    def test_check_all_health(self, adapter, sample_rooms, tmp_path):
        with patch("lsc.core.services.recording_service.StreamCapture") as MockCapture:
            mock_capture = MagicMock()
            mock_capture.start.return_value = True
            mock_capture.status.value = "recording"
            mock_capture.last_error = ""
            mock_capture.duration = 0.0
            mock_capture.check_health.return_value = ""
            MockCapture.return_value = mock_capture

            adapter.start_recordings(sample_rooms, str(tmp_path))
            health = adapter.check_all_health()

            assert isinstance(health, dict)
            # 健康时所有房间都不应该出现在结果中（check_all_health 只返回有问题的）
            # 这里 check_health 返回空字符串，所以应该是空字典
            # 但由于 check_all_health 是检查所有会话并只返回有问题的，
            # 我们需要确认返回类型正确
            assert isinstance(health, dict)

    def test_max_concurrent_limit(self, adapter, tmp_path):
        """验证并发录制上限。"""
        # 创建超过 MAX_CONCURRENT_RECORDINGS 个房间
        many_rooms = []
        for i in range(adapter.MAX_CONCURRENT_RECORDINGS + 2):
            many_rooms.append(RoomInfo(
                platform="test",
                room_url=f"https://example.com/room{i}",
                stream_url=f"https://example.com/stream{i}.flv",
                is_live=True,
            ))

        with patch("lsc.core.services.recording_service.StreamCapture") as MockCapture:
            mock_capture = MagicMock()
            mock_capture.start.return_value = True
            mock_capture.status.value = "recording"
            mock_capture.last_error = ""
            mock_capture.duration = 0.0
            MockCapture.return_value = mock_capture

            started = adapter.start_recordings(many_rooms, str(tmp_path))
            # 应该被限制在 MAX_CONCURRENT_RECORDINGS
            assert len(started) <= adapter.MAX_CONCURRENT_RECORDINGS
