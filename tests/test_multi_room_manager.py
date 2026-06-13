"""Tests for multi-room session state and manager behavior."""
from __future__ import annotations

from types import SimpleNamespace

from lsc.gui.multi_room import RoomSession
from lsc.platforms.base import StreamInfo


def test_room_session_defaults_to_muted_and_disconnected() -> None:
    session = RoomSession(room_id="room-1", room_url="https://live.douyin.com/123")

    assert session.room_id == "room-1"
    assert session.room_url == "https://live.douyin.com/123"
    assert session.platform == ""
    assert session.stream_info is None
    assert session.selected_quality == ""
    assert session.preview_muted is True
    assert session.is_connected is False
    assert session.is_recording is False
    assert session.record_output_path == ""
    assert session.record_started_at is None
    assert session.last_error == ""
    assert session.controller is None


def test_room_session_can_apply_stream_info_fields() -> None:
    session = RoomSession(room_id="room-1", room_url="https://live.bilibili.com/123")
    session.set_error("连接失败")
    info = StreamInfo(
        platform="bilibili",
        room_url=session.room_url,
        stream_url="https://example.com/live.m3u8",
        title="night stream",
        streamer="tester",
        is_live=True,
        quality_urls={"origin": "https://example.com/live.m3u8"},
        selected_quality="origin",
    )

    session.apply_stream_info(info)

    assert session.platform == "bilibili"
    assert session.stream_info is info
    assert session.selected_quality == "origin"
    assert session.is_connected is True
    assert session.last_error == ""


def test_room_session_can_capture_error_without_marking_connected() -> None:
    session = RoomSession(room_id="room-1", room_url="https://www.huya.com/123")

    session.set_error("未开播")

    assert session.is_connected is False
    assert session.last_error == "未开播"


def test_manager_add_get_list_and_remove_room() -> None:
    from lsc.gui.multi_room.manager import MultiRoomManager

    class FakeController:
        def __init__(self) -> None:
            self.cleaned = False

        def cleanup(self) -> None:
            self.cleaned = True

    manager = MultiRoomManager(controller_factory=FakeController)

    first = manager.add_room("https://live.douyin.com/123")
    second = manager.add_room("https://live.bilibili.com/456")

    assert first.room_id != second.room_id
    assert manager.get_room(first.room_id) is first
    assert [room.room_id for room in manager.list_rooms()] == [first.room_id, second.room_id]

    assert manager.remove_room(first.room_id) is True
    assert manager.get_room(first.room_id) is None
    assert first.controller.cleaned is True
    assert manager.remove_room(first.room_id) is False


def test_manager_connect_and_disconnect_room_updates_session(monkeypatch) -> None:
    from lsc.gui.multi_room.manager import MultiRoomManager

    manager = MultiRoomManager(controller_factory=lambda: SimpleNamespace())
    room = manager.add_room("https://live.bilibili.com/123")

    def fake_parse_stream(url: str) -> StreamInfo:
        assert url == "https://live.bilibili.com/123"
        return StreamInfo(
            platform="bilibili",
            room_url=url,
            stream_url="https://example.com/live.m3u8",
            title="test-title",
            streamer="tester",
            is_live=True,
            quality_urls={"origin": "https://example.com/live.m3u8"},
            selected_quality="origin",
            headers={"Referer": "https://example.com/"},
        )

    monkeypatch.setattr("lsc.gui.multi_room.manager.parse_stream", fake_parse_stream)

    assert manager.connect_room(room.room_id) is True
    assert room.platform == "bilibili"
    assert room.is_connected is True
    assert room.last_error == ""
    assert room.controller.stream_url == "https://example.com/live.m3u8"
    assert room.controller.input_args == ["-headers", "Referer: https://example.com/\r\n"]

    assert manager.disconnect_room(room.room_id) is True
    assert room.is_connected is False
    assert manager.disconnect_room("missing-room") is False


def test_manager_mute_room_only_updates_session_flag() -> None:
    from lsc.gui.multi_room.manager import MultiRoomManager

    manager = MultiRoomManager(controller_factory=lambda: SimpleNamespace())
    room = manager.add_room("https://www.huya.com/123")

    manager.mute_room(room.room_id, False)
    assert room.preview_muted is False

    manager.mute_room(room.room_id, True)
    assert room.preview_muted is True


def test_manager_start_and_stop_recording_uses_room_controller(tmp_path) -> None:
    from lsc.gui.multi_room.manager import MultiRoomManager

    class FakeController:
        def __init__(self) -> None:
            self.stream_url = "https://example.com/live.m3u8"
            self.input_args = ["-headers", "Referer: https://example.com/\r\n"]
            self.calls: list[tuple] = []

        def start_recording_with_crf(self, stream_url, output_dir, encoder, crf, **kwargs):
            self.calls.append(("start", stream_url, output_dir, encoder, crf, kwargs))
            return True, str(tmp_path / "recording.mp4"), encoder

        def stop_recording(self):
            self.calls.append(("stop",))
            return True, 12.3, str(tmp_path / "recording.mp4")

    manager = MultiRoomManager(controller_factory=FakeController)
    room = manager.add_room("https://example.com/live.m3u8")
    room.is_connected = True

    assert manager.start_recording(room.room_id, str(tmp_path), "Copy", 23) is True
    assert room.is_recording is True
    assert room.record_output_path.endswith("recording.mp4")
    assert room.record_started_at is not None

    assert manager.stop_recording(room.room_id) is True
    assert room.is_recording is False
    assert room.record_output_path.endswith("recording.mp4")


def test_manager_start_and_stop_recording_all_is_failure_isolated(tmp_path) -> None:
    from lsc.gui.multi_room.manager import MultiRoomManager

    class FakeController:
        def __init__(self) -> None:
            self.stream_url = ""
            self.input_args: list[str] = []

        def start_recording_with_crf(self, stream_url, output_dir, encoder, crf, **kwargs):
            return bool(stream_url), str(tmp_path / "recording.mp4"), encoder

        def stop_recording(self):
            return bool(self.stream_url), 1.0, str(tmp_path / "recording.mp4")

    manager = MultiRoomManager(controller_factory=FakeController)
    first = manager.add_room("https://example.com/a.m3u8")
    second = manager.add_room("https://example.com/b.m3u8")
    first.is_connected = True
    second.is_connected = True
    first.controller.stream_url = "https://example.com/a.m3u8"
    second.controller.stream_url = ""

    started = manager.start_recording_all(str(tmp_path), "Copy", 23)
    stopped = manager.stop_recording_all()

    assert started[first.room_id] is True
    assert started[second.room_id] is False
    assert stopped[first.room_id] is True
    assert stopped[second.room_id] is False
