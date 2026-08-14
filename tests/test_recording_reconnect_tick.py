from __future__ import annotations

from lsc.config import LscConfig


def test_global_tick_runs_due_recording_reconnect(monkeypatch, tmp_path) -> None:
    from lsc.gui.multi_room import manager as manager_module
    from lsc.gui.multi_room.manager import MultiRoomManager

    monkeypatch.setattr(
        "lsc.gui.multi_room.manager.load_config",
        lambda: LscConfig(ffmpeg_path="ffmpeg", ffprobe_path="ffprobe", shared_ingest_enabled=False),
    )
    monkeypatch.setattr(
        "lsc.core.orchestrator.load_config",
        lambda: LscConfig(ffmpeg_path="ffmpeg", ffprobe_path="ffprobe", shared_ingest_enabled=False),
    )

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

    # Reconnect landing (stop → refresh → restart) now runs in the worker
    # pool so the orchestrator thread is never blocked by 10-20s URL refresh.
    monkeypatch.setattr("lsc.gui.multi_room.manager.MultiRoomManager.save_rooms", lambda self: 0)
    monkeypatch.setattr("lsc.core.orchestrator.RoomOrchestrator.save_rooms", lambda self: 0)

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
    monkeypatch.setattr(manager._orch, "_refresh_room_stream_for_recording", lambda room: True)
    manager._tick_counter = manager_module._MEDIUM_FREQ_INTERVAL - 1

    manager._on_global_tick()

    # 落地段异步执行：轮询等待 worker 完成（最长 5s）
    deadline = manager_module._time.monotonic() + 5.0
    while manager_module._time.monotonic() < deadline:
        if room.controller.stop_calls >= 1 and room.controller.start_calls >= 1:
            break
        manager_module._time.sleep(0.02)

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
    monkeypatch.setattr(
        "lsc.core.orchestrator.load_config",
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
    monkeypatch.setattr(
        "lsc.core.orchestrator.parse_stream",
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


def test_huya_ffmpeg_crash_code0_forces_reconnect(monkeypatch, tmp_path) -> None:
    """虎牙 FFmpeg code=0 异常退出即使无 403 特征也强制换线重连（回归 #3b）。"""
    from lsc.gui.multi_room.manager import MultiRoomManager
    from lsc.platforms.base import StreamInfo

    monkeypatch.setattr(
        "lsc.gui.multi_room.manager.load_config",
        lambda: LscConfig(ffmpeg_path="ffmpeg", ffprobe_path="ffprobe", shared_ingest_enabled=False),
    )
    monkeypatch.setattr(
        "lsc.core.orchestrator.load_config",
        lambda: LscConfig(ffmpeg_path="ffmpeg", ffprobe_path="ffprobe", shared_ingest_enabled=False),
    )

    class FakeController:
        def __init__(self) -> None:
            self.stop_calls = 0
            self.start_calls = 0

        def stop_recording(self):
            self.stop_calls += 1
            return True, 1.0, str(tmp_path / "old.mp4")

        def start_recording_with_crf(self, stream_url, output_dir, encoder, crf, **kwargs):
            self.start_calls += 1
            return True, str(tmp_path / "new.mp4"), encoder, ""

    manager = MultiRoomManager(controller_factory=FakeController)
    room = manager.add_room("https://www.huya.com/example")
    room.is_connected = True
    room.is_recording = True
    room.stream_info = StreamInfo(
        platform="huya",
        room_url="https://www.huya.com/example",
        stream_url="https://tx.flv.huya.com/live.flv",
        is_live=True,
    )

    # code=0 异常退出文案无 403 特征 → is_recoverable_error 判 False，
    # 但虎牙 "异常退出" 应放行走重连，而非置不可恢复停止
    manager._attempt_recording_reconnect(room, "FFmpeg 异常退出 (code 0)")

    assert room.controller.stop_calls == 0
    assert room.is_recording is True
    assert room.is_reconnecting is True


def test_recording_start_auth_failure_does_not_quarantine_huya_cdn(monkeypatch, tmp_path) -> None:
    """虎牙录制启动鉴权失败走签名族失效，不得当成 CDN 换线。"""
    from lsc.gui.multi_room.manager import MultiRoomManager
    from lsc.platforms.base import StreamInfo

    monkeypatch.setattr(
        "lsc.gui.multi_room.manager.load_config",
        lambda: LscConfig(ffmpeg_path="ffmpeg", ffprobe_path="ffprobe", shared_ingest_enabled=False),
    )
    monkeypatch.setattr(
        "lsc.core.orchestrator.load_config",
        lambda: LscConfig(ffmpeg_path="ffmpeg", ffprobe_path="ffprobe", shared_ingest_enabled=False),
    )

    marked: list[str] = []
    monkeypatch.setattr(
        "lsc.platforms.huya.mark_cdn_bad",
        lambda cdn: marked.append(cdn),
    )

    class FakeController:
        def __init__(self) -> None:
            self.stream_url = "https://tx.flv.huya.com/live.flv"
            self.input_args: list[str] = []

        def start_recording_with_crf(self, stream_url, output_dir, encoder, crf, **kwargs):
            return False, "", encoder, "直播流鉴权失败或链接已过期 (code 3436169992)"

    manager = MultiRoomManager(controller_factory=FakeController)
    room = manager.add_room("https://www.huya.com/example")
    room.is_connected = True
    room.stream_info = StreamInfo(
        platform="huya",
        room_url="https://www.huya.com/example",
        stream_url="https://tx.flv.huya.com/live.flv",
        is_live=True,
    )
    monkeypatch.setattr(manager, "_refresh_room_stream_for_recording", lambda room: True)
    monkeypatch.setattr(manager._orch, "_refresh_room_stream_for_recording", lambda room: True)

    ok = manager.start_recording(room.room_id, str(tmp_path), "Copy", 23)

    assert ok is False
    assert marked == []
    assert "鉴权" in room.last_error
