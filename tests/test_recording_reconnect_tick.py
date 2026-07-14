from __future__ import annotations

from lsc.config import LscConfig


def test_global_tick_runs_due_recording_reconnect(monkeypatch, tmp_path) -> None:
    from lsc.gui.multi_room import manager as manager_module
    from lsc.gui.multi_room.manager import MultiRoomManager

    monkeypatch.setattr(
        "lsc.gui.multi_room.manager.load_config",
        lambda: LscConfig(ffmpeg_path="ffmpeg", ffprobe_path="ffprobe", shared_ingest_enabled=False),
    )

    class ImmediateThread:
        def __init__(self, target, daemon=False):
            self.target = target
            self.daemon = daemon
            self.started = False

        def start(self):
            self.started = True
            self.target()

        def is_alive(self):
            return False

    class FakeController:
        def __init__(self) -> None:
            self.stream_url = "https://example.com/live.m3u8"
            self.input_args: list[str] = []
            self.tick_calls = 0
            self.watchdog_calls = 0
            self.stop_calls = 0
            self.start_calls = 0

        def tick(self) -> None:
            self.tick_calls += 1

        def watchdog_check(self):
            self.watchdog_calls += 1
            return None

        def stop_recording(self):
            self.stop_calls += 1
            return True, 1.0, str(tmp_path / "old.mp4")

        def start_recording_with_crf(self, stream_url, output_dir, encoder, crf, **kwargs):
            self.start_calls += 1
            return True, str(tmp_path / "new.mp4"), encoder, ""

    monkeypatch.setattr("threading.Thread", ImmediateThread)

    manager = MultiRoomManager(controller_factory=FakeController)
    room = manager.add_room("https://example.com/live.m3u8")
    room.is_connected = True
    room.is_recording = True
    room.is_reconnecting = True
    room.last_error = "输出文件长时间未增长，录制可能已卡住，2秒后尝试恢复..."
    room.reconnect_next_attempt_at = manager_module._time.monotonic() - 1.0
    room.reconnect_output_dir = str(tmp_path)
    room.reconnect_encoder = "Copy"
    room.reconnect_crf = 23
    room.controller.stream_url = "https://example.com/live.m3u8"

    monkeypatch.setattr(manager, "_refresh_room_stream_for_recording", lambda room: True)
    manager._tick_counter = manager_module._MEDIUM_FREQ_INTERVAL - 1

    manager._on_global_tick()

    assert room.controller.watchdog_calls == 0
    assert room.controller.stop_calls == 1
    assert room.controller.start_calls == 1
    assert room.is_recording is True
    assert room.is_reconnecting is False
    assert room.reconnect_next_attempt_at == 0.0


def test_recording_reconnect_stops_when_stream_is_offline(monkeypatch, tmp_path) -> None:
    from lsc.gui.multi_room import manager as manager_module
    from lsc.gui.multi_room.manager import MultiRoomManager
    from lsc.platforms.base import StreamInfo

    monkeypatch.setattr(
        "lsc.gui.multi_room.manager.load_config",
        lambda: LscConfig(ffmpeg_path="ffmpeg", ffprobe_path="ffprobe", shared_ingest_enabled=False),
    )

    class FakeController:
        def __init__(self) -> None:
            self.stream_url = "https://example.com/live.m3u8"
            self.input_args: list[str] = []
            self.stop_calls = 0
            self.start_calls = 0

        def stop_recording(self):
            self.stop_calls += 1
            return True, 1.0, str(tmp_path / "old.mp4")

        def start_recording_with_crf(self, stream_url, output_dir, encoder, crf, **kwargs):
            self.start_calls += 1
            return True, str(tmp_path / "new.mp4"), encoder, ""

    offline_info = StreamInfo(
        platform="douyin",
        room_url="https://live.douyin.com/offline",
        stream_url="",
        is_live=False,
        error="直播间已下播",
    )
    monkeypatch.setattr(
        "lsc.gui.multi_room.manager.parse_stream",
        lambda url, force_refresh=False: offline_info,
    )

    manager = MultiRoomManager(controller_factory=FakeController)
    room = manager.add_room("https://live.douyin.com/offline")
    room.is_connected = True
    room.is_recording = True
    room.is_reconnecting = True
    room.last_error = "输出文件长时间未增长，录制可能已卡住，2秒后尝试恢复..."
    room.reconnect_next_attempt_at = manager_module._time.monotonic() - 1.0
    room.reconnect_output_dir = str(tmp_path)
    room.reconnect_encoder = "Copy"
    room.reconnect_crf = 23

    manager._attempt_recording_reconnect(room, room.last_error)

    assert room.controller.stop_calls == 1
    assert room.controller.start_calls == 0
    assert room.is_recording is False
    assert room.is_reconnecting is False
    assert room.reconnect_next_attempt_at == 0.0
    assert "下播" in room.last_error
