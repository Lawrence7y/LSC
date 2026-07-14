"""Tests for multi-room session state and manager behavior."""
from __future__ import annotations

from types import SimpleNamespace

from lsc.config import LscConfig
from lsc.core.services.ingest_registry import get_shared_ingest_registry
from lsc.core.services.shared_ingest import SharedIngestStartResult
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
    assert room.selected_quality == "origin"
    assert room.last_error == ""
    assert room.controller.stream_url == "https://example.com/live.m3u8"
    assert room.controller.input_args == ["-headers", "Referer: https://example.com/\r\n"]
    assert room.controller.selected_quality == "origin"

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


def test_manager_start_and_stop_recording_uses_room_controller(monkeypatch, tmp_path) -> None:
    from lsc.gui.multi_room.manager import MultiRoomManager

    monkeypatch.setattr(
        "lsc.gui.multi_room.manager.load_config",
        lambda: LscConfig(ffmpeg_path="ffmpeg", ffprobe_path="ffprobe", shared_ingest_enabled=False),
    )

    class FakeController:
        def __init__(self) -> None:
            self.stream_url = "https://example.com/live.m3u8"
            self.input_args = ["-headers", "Referer: https://example.com/\r\n"]
            self.calls: list[tuple] = []

        def start_recording_with_crf(self, stream_url, output_dir, encoder, crf, **kwargs):
            self.calls.append(("start", stream_url, output_dir, encoder, crf, kwargs))
            return True, str(tmp_path / "recording.mp4"), encoder, ""

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


def test_manager_start_recording_refreshes_stream_url_before_ffmpeg_start(monkeypatch, tmp_path) -> None:
    from lsc.gui.multi_room.manager import MultiRoomManager

    monkeypatch.setattr(
        "lsc.gui.multi_room.manager.load_config",
        lambda: LscConfig(ffmpeg_path="ffmpeg", ffprobe_path="ffprobe", shared_ingest_enabled=False),
    )

    class FakeController:
        def __init__(self) -> None:
            self.stream_url = "https://example.com/expired.flv"
            self.input_args = ["-headers", "Referer: https://old.example/\r\n"]
            self.selected_quality = "250"
            self.calls: list[tuple] = []

        def start_recording_with_crf(self, stream_url, output_dir, encoder, crf, **kwargs):
            self.calls.append(("start", stream_url, output_dir, encoder, crf, kwargs))
            return True, str(tmp_path / "recording.mp4"), encoder, ""

    refreshed = StreamInfo(
        platform="bilibili",
        room_url="https://live.bilibili.com/35",
        stream_url="https://example.com/fresh-250.flv",
        is_live=True,
        quality_urls={
            "250": "https://example.com/fresh-250.flv",
            "400": "https://example.com/fresh-400.flv",
        },
        selected_quality="250",
        headers={"Referer": "https://live.bilibili.com/"},
    )
    calls: list[tuple[str, bool]] = []

    def fake_parse_stream(url: str, *, force_refresh: bool = False):
        calls.append((url, force_refresh))
        return refreshed

    monkeypatch.setattr("lsc.gui.multi_room.manager.parse_stream", fake_parse_stream)

    manager = MultiRoomManager(controller_factory=FakeController)
    room = manager.add_room("https://live.bilibili.com/35")
    room.is_connected = True
    room.selected_quality = "250"

    assert manager.start_recording(room.room_id, str(tmp_path), "Copy", 23) is True

    assert calls  # at least one parse when no reusable cache
    assert calls[0] == ("https://live.bilibili.com/35", False)
    assert room.controller.stream_url == "https://example.com/fresh-250.flv"
    assert room.controller.input_args == ["-headers", "Referer: https://live.bilibili.com/\r\n"]
    assert room.controller.calls[-1][1] == "https://example.com/fresh-250.flv"
    assert room.controller.calls[-1][5]["input_args"] == [
        "-headers",
        "Referer: https://live.bilibili.com/\r\n",
    ]


def test_manager_start_and_stop_recording_all_is_failure_isolated(monkeypatch, tmp_path) -> None:
    from lsc.gui.multi_room.manager import MultiRoomManager

    monkeypatch.setattr(
        "lsc.gui.multi_room.manager.load_config",
        lambda: LscConfig(ffmpeg_path="ffmpeg", ffprobe_path="ffprobe", shared_ingest_enabled=False),
    )

    class FakeController:
        def __init__(self) -> None:
            self.stream_url = ""
            self.input_args: list[str] = []

        def start_recording_with_crf(self, stream_url, output_dir, encoder, crf, **kwargs):
            if stream_url:
                return True, str(tmp_path / "recording.mp4"), encoder, ""
            return False, "", encoder, "直播流地址已失效"

        def stop_recording(self):
            return bool(self.stream_url), 1.0, str(tmp_path / "recording.mp4")

    manager = MultiRoomManager(controller_factory=FakeController)
    first = manager.add_room("https://example.com/a.m3u8")
    second = manager.add_room("not-a-supported-live-url")
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
    # Failed room should carry the refresh/connect error detail.
    assert second.last_error


def test_manager_shutdown_cleans_rooms_workers_and_is_idempotent(tmp_path) -> None:
    from lsc.gui.multi_room.manager import MultiRoomManager

    class FakeController:
        def __init__(self) -> None:
            self.stop_async_calls = 0
            self.cleanup_calls = 0

        def stop_recording_async(self) -> None:
            self.stop_async_calls += 1

        def cleanup(self) -> None:
            self.cleanup_calls += 1

    class FakeWorker:
        def __init__(self) -> None:
            self.interrupted = False
            self.wait_calls: list[int] = []

        def isRunning(self) -> bool:
            return True

        def requestInterruption(self) -> None:
            self.interrupted = True

        def wait(self, timeout_ms: int) -> bool:
            self.wait_calls.append(timeout_ms)
            return True

    manager = MultiRoomManager(controller_factory=FakeController)
    room = manager.add_room("https://example.com/live.m3u8")
    room.is_connected = True
    room.is_recording = True
    room.record_output_path = str(tmp_path / "recording.mp4")
    connect_worker = FakeWorker()
    probe_worker = FakeWorker()
    manager._connect_workers[room.room_id] = connect_worker
    manager._metadata_probe_workers[room.room_id] = probe_worker

    result = manager.shutdown(timeout_sec=0.2)
    second = manager.shutdown(timeout_sec=0.2)

    assert result["rooms"] == 1
    assert result["recordings_stopped"] == 1
    assert connect_worker.interrupted is True
    assert probe_worker.interrupted is True
    assert room.controller.stop_async_calls == 1
    assert room.controller.cleanup_calls == 1
    assert manager.list_rooms() == []
    assert second["rooms"] == 0


def test_manager_start_recording_propagates_error_detail(monkeypatch, tmp_path) -> None:
    """When start_recording fails, room.last_error must carry the controller's message."""
    from lsc.gui.multi_room.manager import MultiRoomManager

    monkeypatch.setattr(
        "lsc.gui.multi_room.manager.load_config",
        lambda: LscConfig(ffmpeg_path="ffmpeg", ffprobe_path="ffprobe", shared_ingest_enabled=False),
    )

    class FakeController:
        def __init__(self) -> None:
            self.stream_url = "https://example.com/live.m3u8"
            self.input_args: list[str] = []

        def start_recording_with_crf(self, stream_url, output_dir, encoder, crf, **kwargs):
            return False, "", encoder, "连接直播流超时"

    manager = MultiRoomManager(controller_factory=FakeController)
    room = manager.add_room("https://example.com/live.m3u8")
    room.is_connected = True

    assert manager.start_recording(room.room_id, str(tmp_path), "Copy", 23) is False
    assert room.last_error == "连接直播流超时"
def test_manager_start_recording_uses_shared_ingest_when_enabled(monkeypatch, tmp_path) -> None:
    from lsc.gui.multi_room.manager import MultiRoomManager

    registry = get_shared_ingest_registry()
    registry.stop_room("https://example.com/live.m3u8", reason="test cleanup before")

    class FakeController:
        def __init__(self) -> None:
            self.stream_url = "https://example.com/live.m3u8"
            self.input_args: list[str] = []
            self.start_calls = 0

        def start_recording_with_crf(self, stream_url, output_dir, encoder, crf, **kwargs):
            self.start_calls += 1
            raise AssertionError("legacy controller recording should not start")

    def successful_start(self, recording_path, profile=None):
        self.recording_active = True
        self.recording_media_start_mono = 123.0
        self._recording_path = recording_path
        return SharedIngestStartResult(ok=True)

    monkeypatch.setattr(
        "lsc.gui.multi_room.manager.load_config",
        lambda: LscConfig(ffmpeg_path="ffmpeg", ffprobe_path="ffprobe", shared_ingest_enabled=True),
    )
    monkeypatch.setattr(
        "lsc.core.services.shared_ingest.SharedRoomIngest.start_recording",
        successful_start,
    )

    try:
        manager = MultiRoomManager(controller_factory=FakeController)
        room = manager.add_room("https://example.com/live.m3u8")
        room.is_connected = True
        room.stream_info = StreamInfo(
            platform="test",
            room_url="https://example.com/live.m3u8",
            stream_url="https://example.com/live.m3u8",
            is_live=True,
        )

        assert manager.start_recording(room.room_id, str(tmp_path), "Copy", 23) is True

        assert room.is_recording is True
        assert room.record_output_path.endswith(".mp4")
        assert room.record_started_at is not None
        assert room.recording_media_start_mono == 123.0
        assert room.controller.start_calls == 0
        assert registry.get(room.room_id) is not None
    finally:
        if "room" in locals():
            registry.stop_room(room.room_id, reason="test cleanup after")
        registry.stop_room("https://example.com/live.m3u8", reason="test cleanup after")


def test_manager_start_recording_updates_existing_preview_only_ingest(monkeypatch, tmp_path) -> None:
    from lsc.gui.multi_room.manager import MultiRoomManager

    registry = get_shared_ingest_registry()
    start_context: dict[str, object] = {}

    class FakeController:
        def __init__(self) -> None:
            self.stream_url = "https://example.com/expired.flv"
            self.input_args: list[str] = []

        def start_recording_with_crf(self, stream_url, output_dir, encoder, crf, **kwargs):
            raise AssertionError("legacy controller recording should not start")

    refreshed = StreamInfo(
        platform="test",
        room_url="https://example.com/room",
        stream_url="https://example.com/fresh.flv",
        is_live=True,
        headers={"Referer": "https://fresh.example/"},
    )

    def fake_parse_stream(url: str, *, force_refresh: bool = False):
        # 无房间级缓存时会先 force_refresh=False；若 URL 即将过期再 force=True
        return refreshed

    def successful_start(self, recording_path, profile=None):
        start_context["url"] = self.url
        start_context["headers"] = dict(self.headers)
        self.recording_active = True
        self.recording_media_start_mono = 456.0
        self._recording_path = recording_path
        return SharedIngestStartResult(ok=True)

    monkeypatch.setattr(
        "lsc.gui.multi_room.manager.load_config",
        lambda: LscConfig(ffmpeg_path="ffmpeg", ffprobe_path="ffprobe", shared_ingest_enabled=True),
    )
    monkeypatch.setattr("lsc.gui.multi_room.manager.parse_stream", fake_parse_stream)
    monkeypatch.setattr(
        "lsc.core.services.shared_ingest.SharedRoomIngest.start_recording",
        successful_start,
    )

    try:
        manager = MultiRoomManager(controller_factory=FakeController)
        room = manager.add_room("https://example.com/room")
        room.is_connected = True
        room.stream_info = StreamInfo(
            platform="test",
            room_url="https://example.com/room",
            stream_url="https://example.com/expired.flv",
            is_live=True,
            headers={"Referer": "https://old.example/"},
        )
        registry.get_or_create(
            room.room_id,
            url="https://example.com/expired.flv",
            headers={"Referer": "https://old.example/"},
        )

        assert manager.start_recording(room.room_id, str(tmp_path), "Copy", 23) is True

        assert start_context == {
            "url": "https://example.com/fresh.flv",
            "headers": {"Referer": "https://fresh.example/"},
        }
        assert room.recording_media_start_mono == 456.0
    finally:
        if "room" in locals():
            registry.stop_room(room.room_id, reason="test cleanup after")


def test_manager_stop_recording_stops_shared_ingest_when_used(monkeypatch, tmp_path) -> None:
    from lsc.gui.multi_room.manager import MultiRoomManager

    registry = get_shared_ingest_registry()

    class FakeController:
        def __init__(self) -> None:
            self.stream_url = "https://example.com/live.m3u8"
            self.input_args: list[str] = []
            self.stop_calls = 0

        def start_recording_with_crf(self, stream_url, output_dir, encoder, crf, **kwargs):
            raise AssertionError("legacy controller recording should not start")

        def stop_recording(self):
            self.stop_calls += 1
            raise AssertionError("legacy controller recording should not stop")

    def successful_start(self, recording_path, profile=None):
        self.recording_active = True
        self._recording_path = recording_path
        return SharedIngestStartResult(ok=True)

    monkeypatch.setattr(
        "lsc.gui.multi_room.manager.load_config",
        lambda: LscConfig(ffmpeg_path="ffmpeg", ffprobe_path="ffprobe", shared_ingest_enabled=True),
    )
    monkeypatch.setattr(
        "lsc.core.services.shared_ingest.SharedRoomIngest.start_recording",
        successful_start,
    )

    try:
        manager = MultiRoomManager(controller_factory=FakeController)
        room = manager.add_room("https://example.com/live.m3u8")
        room.is_connected = True
        room.stream_info = StreamInfo(
            platform="test",
            room_url="https://example.com/live.m3u8",
            stream_url="https://example.com/live.m3u8",
            is_live=True,
        )

        assert manager.start_recording(room.room_id, str(tmp_path), "Copy", 23) is True
        assert manager.stop_recording(room.room_id) is True

        assert room.is_recording is False
        assert registry.get(room.room_id) is None
        assert room.controller.stop_calls == 0
    finally:
        if "room" in locals():
            registry.stop_room(room.room_id, reason="test cleanup after")
def test_manager_stop_recording_async_stops_shared_ingest_when_used(monkeypatch, tmp_path) -> None:
    from lsc.gui.multi_room.manager import MultiRoomManager

    registry = get_shared_ingest_registry()

    class FakeController:
        def __init__(self) -> None:
            self.stream_url = "https://example.com/live.m3u8"
            self.input_args: list[str] = []
            self.stop_async_calls = 0

        def start_recording_with_crf(self, stream_url, output_dir, encoder, crf, **kwargs):
            raise AssertionError("legacy controller recording should not start")

        def stop_recording_async(self):
            self.stop_async_calls += 1
            raise AssertionError("legacy controller async stop should not run")

    def successful_start(self, recording_path, profile=None):
        self.recording_active = True
        self._recording_path = recording_path
        return SharedIngestStartResult(ok=True)

    monkeypatch.setattr(
        "lsc.gui.multi_room.manager.load_config",
        lambda: LscConfig(ffmpeg_path="ffmpeg", ffprobe_path="ffprobe", shared_ingest_enabled=True),
    )
    monkeypatch.setattr(
        "lsc.core.services.shared_ingest.SharedRoomIngest.start_recording",
        successful_start,
    )

    try:
        manager = MultiRoomManager(controller_factory=FakeController)
        room = manager.add_room("https://example.com/live.m3u8")
        room.is_connected = True
        room.stream_info = StreamInfo(
            platform="test",
            room_url="https://example.com/live.m3u8",
            stream_url="https://example.com/live.m3u8",
            is_live=True,
        )

        assert manager.start_recording(room.room_id, str(tmp_path), "Copy", 23) is True
        assert manager.stop_recording_async(room.room_id) is True

        assert room.is_recording is False
        assert registry.get(room.room_id) is None
        assert room.controller.stop_async_calls == 0
    finally:
        if "room" in locals():
            registry.stop_room(room.room_id, reason="test cleanup after")


def test_start_recording_heals_stale_is_connected_when_stream_cache_exists(monkeypatch, tmp_path) -> None:
    """预览刷新失败误清 is_connected 后，录制应能凭流缓存恢复连接态。"""
    import time

    from lsc.gui.multi_room.manager import MultiRoomManager

    monkeypatch.setattr(
        "lsc.gui.multi_room.manager.load_config",
        lambda: LscConfig(ffmpeg_path="ffmpeg", ffprobe_path="ffprobe", shared_ingest_enabled=False),
    )

    class FakeController:
        def __init__(self) -> None:
            self.stream_url = "https://example.com/live.flv"
            self.input_args = ["-headers", "Referer: https://live.example/\r\n"]
            self.selected_quality = "origin"

        def start_recording_with_crf(self, stream_url, output_dir, encoder, crf, **kwargs):
            return True, str(tmp_path / "recording.mp4"), encoder, ""

    parse_calls: list[tuple[str, bool]] = []

    def fake_parse_stream(url: str, *, force_refresh: bool = False):
        parse_calls.append((url, force_refresh))
        raise AssertionError("should reuse room stream cache, not parse")

    monkeypatch.setattr("lsc.gui.multi_room.manager.parse_stream", fake_parse_stream)

    manager = MultiRoomManager(controller_factory=FakeController)
    room = manager.add_room("https://live.douyin.com/123")
    room.is_connected = False  # 模拟被预览刷新误清
    room.stream_url_cached = "https://example.com/live.flv"
    room.stream_parsed_at = time.time()
    room.stream_info = StreamInfo(
        platform="douyin",
        room_url=room.room_url,
        stream_url="https://example.com/live.flv",
        is_live=True,
        headers={"Referer": "https://live.douyin.com/"},
    )

    assert manager.start_recording(room.room_id, str(tmp_path), "Copy", 23) is True
    assert room.is_connected is True
    assert parse_calls == []


def test_refresh_stream_url_reuses_fresh_room_cache(monkeypatch) -> None:
    import time

    from lsc.gui.multi_room.manager import MultiRoomManager

    monkeypatch.setattr(
        "lsc.gui.multi_room.manager.load_config",
        lambda: LscConfig(ffmpeg_path="ffmpeg", ffprobe_path="ffprobe", shared_ingest_enabled=False),
    )

    def boom(url: str, *, force_refresh: bool = False):
        raise AssertionError(f"unexpected parse_stream force_refresh={force_refresh}")

    monkeypatch.setattr("lsc.gui.multi_room.manager.parse_stream", boom)

    manager = MultiRoomManager(controller_factory=lambda: SimpleNamespace(stream_url="", input_args=[], selected_quality=""))
    room = manager.add_room("https://live.bilibili.com/1")
    room.stream_url_cached = "https://example.com/cached.flv"
    room.stream_parsed_at = time.time()
    room.stream_info = StreamInfo(
        platform="bilibili",
        room_url=room.room_url,
        stream_url="https://example.com/cached.flv",
        is_live=True,
        headers={"Referer": "https://live.bilibili.com/"},
    )
    room.is_connected = True

    assert manager.refresh_stream_url(room.room_id, force=False) is True
    assert room.controller.stream_url == "https://example.com/cached.flv"
