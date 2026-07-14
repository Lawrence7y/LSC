from __future__ import annotations

from lsc.config import LscConfig
from lsc.core.models import RecordingStatus, RoomInfo
from lsc.core.services.ingest_registry import get_shared_ingest_registry
from lsc.core.services.shared_ingest import SharedIngestStartResult
from lsc.core.services.recording_service import RecordingService, _SharedCaptureAdapter


class _FakeCapture:
    instances: list["_FakeCapture"] = []

    def __init__(self, _config):
        self.starts: list[dict] = []
        self.last_error = ""
        self.duration = 0.0
        self.cleaned = False
        self.__class__.instances.append(self)

    def start(self, url, output_path, *, codec="copy", input_args=None, extra_args=None):
        self.starts.append(
            {
                "url": url,
                "output_path": output_path,
                "codec": codec,
                "input_args": input_args,
                "extra_args": extra_args,
            }
        )
        return True

    def force_cleanup(self):
        self.cleaned = True


class _UnexpectedCapture:
    def __init__(self, _config):
        raise AssertionError("legacy StreamCapture should not be created")


def _room() -> RoomInfo:
    return RoomInfo(
        platform="test",
        room_url="https://example/room",
        stream_url="http://example/live.flv",
        title="Live",
        streamer="Streamer",
        is_live=True,
        headers={"Referer": "https://example"},
    )


def _room_with_runtime_id(room_id: str) -> RoomInfo:
    room = _room()
    room.raw["room_id"] = room_id
    return room


def _config(shared_enabled: bool) -> LscConfig:
    return LscConfig(
        ffmpeg_path="ffmpeg",
        ffprobe_path="ffprobe",
        shared_ingest_enabled=shared_enabled,
    )


def test_shared_recording_falls_back_to_legacy_on_start_failure(tmp_path, monkeypatch):
    _FakeCapture.instances.clear()
    shared_attempted = {"value": False}

    def failing_get_or_create(self, room_id, url, headers=None):
        shared_attempted["value"] = True
        raise RuntimeError("shared unavailable")

    monkeypatch.setattr("lsc.core.services.recording_service.StreamCapture", _FakeCapture)
    monkeypatch.setattr(
        "lsc.core.services.ingest_registry.SharedIngestRegistry.get_or_create",
        failing_get_or_create,
    )

    service = RecordingService(config=_config(shared_enabled=True))
    session = service.start_recording(_room(), str(tmp_path))

    assert shared_attempted["value"] is True
    assert session.status == RecordingStatus.RECORDING
    assert session.output_path.endswith(".mp4")
    assert session.stream_url == "http://example/live.flv"
    assert _FakeCapture.instances[0].starts[0]["url"] == "http://example/live.flv"


def test_shared_recording_disabled_uses_legacy_without_registry(tmp_path, monkeypatch):
    _FakeCapture.instances.clear()

    def unexpected_get_or_create(self, room_id, url, headers=None):
        raise AssertionError("shared registry should not be used when flag is disabled")

    monkeypatch.setattr("lsc.core.services.recording_service.StreamCapture", _FakeCapture)
    monkeypatch.setattr(
        "lsc.core.services.ingest_registry.SharedIngestRegistry.get_or_create",
        unexpected_get_or_create,
    )

    service = RecordingService(config=_config(shared_enabled=False))
    session = service.start_recording(_room(), str(tmp_path))

    assert session.status == RecordingStatus.RECORDING
    assert len(_FakeCapture.instances) == 1


def test_shared_recording_falls_back_when_shared_start_requests_legacy(tmp_path, monkeypatch):
    _FakeCapture.instances.clear()
    shared_start_called = {"value": False}

    def fallback_start(self, recording_path, preview_pipe="pipe:1"):
        shared_start_called["value"] = True
        return SharedIngestStartResult(ok=False, use_legacy_fallback=True, error="probe failed")

    monkeypatch.setattr("lsc.core.services.recording_service.StreamCapture", _FakeCapture)
    monkeypatch.setattr(
        "lsc.core.services.shared_ingest.SharedRoomIngest.start_recording_and_preview",
        fallback_start,
    )

    service = RecordingService(config=_config(shared_enabled=True))
    session = service.start_recording(_room(), str(tmp_path))

    assert shared_start_called["value"] is True
    assert session.status == RecordingStatus.RECORDING
    assert _FakeCapture.instances[0].starts[0]["url"] == "http://example/live.flv"


def test_recording_service_uses_process_wide_shared_ingest_registry():
    service = RecordingService(config=_config(shared_enabled=True))

    assert service._shared_ingests is get_shared_ingest_registry()


def test_shared_recording_fallback_removes_failed_ingest_from_registry(tmp_path, monkeypatch):
    _FakeCapture.instances.clear()
    registry = get_shared_ingest_registry()
    registry.stop_room("https://example/room", reason="test cleanup before")

    def fallback_start(self, recording_path, preview_pipe="pipe:1"):
        return SharedIngestStartResult(ok=False, use_legacy_fallback=True, error="probe failed")

    monkeypatch.setattr("lsc.core.services.recording_service.StreamCapture", _FakeCapture)
    monkeypatch.setattr(
        "lsc.core.services.shared_ingest.SharedRoomIngest.start_recording_and_preview",
        fallback_start,
    )

    try:
        service = RecordingService(config=_config(shared_enabled=True))
        session = service.start_recording(_room(), str(tmp_path))

        assert session.status == RecordingStatus.RECORDING
        assert registry.get("https://example/room") is None
    finally:
        registry.stop_room("https://example/room", reason="test cleanup after")


def test_shared_recording_success_uses_shared_session_without_legacy_capture(tmp_path, monkeypatch):
    registry = get_shared_ingest_registry()
    registry.stop_room("https://example/room", reason="test cleanup before")
    shared_start_called = {"value": False}

    def successful_start(self, recording_path, preview_pipe="pipe:1"):
        shared_start_called["value"] = True
        self.recording_active = True
        self._recording_path = recording_path
        return SharedIngestStartResult(ok=True)

    monkeypatch.setattr("lsc.core.services.recording_service.StreamCapture", _UnexpectedCapture)
    monkeypatch.setattr(
        "lsc.core.services.shared_ingest.SharedRoomIngest.start_recording_and_preview",
        successful_start,
    )

    try:
        service = RecordingService(config=_config(shared_enabled=True))
        session = service.start_recording(_room(), str(tmp_path))

        assert shared_start_called["value"] is True
        assert session.status == RecordingStatus.RECORDING
        assert session.output_path.endswith(".mp4")
        assert registry.get("https://example/room") is not None
    finally:
        registry.stop_room("https://example/room", reason="test cleanup after")


def test_shared_recording_prefers_runtime_room_id_for_registry_key(tmp_path, monkeypatch):
    registry = get_shared_ingest_registry()
    registry.stop_room("room-1", reason="test cleanup before")
    registry.stop_room("https://example/room", reason="test cleanup before")

    def successful_start(self, recording_path, preview_pipe="pipe:1"):
        self.recording_active = True
        self._recording_path = recording_path
        return SharedIngestStartResult(ok=True)

    monkeypatch.setattr("lsc.core.services.recording_service.StreamCapture", _UnexpectedCapture)
    monkeypatch.setattr(
        "lsc.core.services.shared_ingest.SharedRoomIngest.start_recording_and_preview",
        successful_start,
    )

    try:
        service = RecordingService(config=_config(shared_enabled=True))
        session = service.start_recording(_room_with_runtime_id("room-1"), str(tmp_path))

        assert session.status == RecordingStatus.RECORDING
        assert registry.get("room-1") is not None
        assert registry.get("https://example/room") is None
    finally:
        registry.stop_room("room-1", reason="test cleanup after")
        registry.stop_room("https://example/room", reason="test cleanup after")


def test_shared_recording_stop_cleans_registry_without_legacy_capture(tmp_path, monkeypatch):
    registry = get_shared_ingest_registry()
    registry.stop_room("https://example/room", reason="test cleanup before")

    def successful_start(self, recording_path, preview_pipe="pipe:1"):
        self.recording_active = True
        self._recording_path = recording_path
        return SharedIngestStartResult(ok=True)

    monkeypatch.setattr("lsc.core.services.recording_service.StreamCapture", _UnexpectedCapture)
    monkeypatch.setattr(
        "lsc.core.services.shared_ingest.SharedRoomIngest.start_recording_and_preview",
        successful_start,
    )

    service = RecordingService(config=_config(shared_enabled=True))
    session = service.start_recording(_room(), str(tmp_path))
    stopped = service.stop_recording(session.session_id)

    assert stopped.status == RecordingStatus.STOPPED
    assert registry.get("https://example/room") is None


def test_shared_capture_stop_reports_file_size_after_ingest_stops(tmp_path):
    output = tmp_path / "recording.mp4"

    class FakeIngest:
        is_stopped = False
        preview_subscribers = 0
        upstream_error = ""

        def stop_recording_sink(self, reason: str = "") -> None:
            output.write_bytes(b"x" * 1024 * 1024)

    adapter = _SharedCaptureAdapter(
        ingest=FakeIngest(),
        room_id="room-1",
        output_path=str(output),
    )

    result = adapter.stop()

    assert result.success is True
    assert result.file_size_mb == 1.0
