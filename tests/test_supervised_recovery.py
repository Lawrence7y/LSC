from __future__ import annotations

from types import SimpleNamespace

from lsc.core.orchestrator import RoomOrchestrator
from lsc.core.session import RoomSession


class _Supervisor:
    def __init__(self):
        self.calls = []

    def run_recovery(self, callback, *, reason_code):
        self.calls.append(reason_code)
        return bool(callback("room-1-recovery-1"))


class _Registry:
    def __init__(self, supervisor):
        self.supervisor = supervisor

    def get_supervisor_if_exists(self, room_id):
        return self.supervisor if room_id == "room-1" else None


def test_v2_reconnect_is_serialized_by_supervisor(monkeypatch):
    supervisor = _Supervisor()
    monkeypatch.setattr(
        "lsc.core.orchestrator.get_shared_ingest_registry",
        lambda: _Registry(supervisor),
    )
    monkeypatch.setattr(
        "lsc.core.orchestrator.is_platform_pipeline_component_enabled",
        lambda component, platform: component == "ingest_supervisor_v2",
    )
    orchestrator = RoomOrchestrator()
    room = RoomSession("room-1", "https://live.example/1", platform="bilibili")
    room.is_recording = True
    monkeypatch.setattr(
        orchestrator,
        "_attempt_recording_reconnect",
        lambda target, _error: (setattr(target, "is_recording", True), setattr(target, "is_reconnecting", False)),
    )
    assert orchestrator._start_recording_reconnect_thread(room, "connection reset") is True
    assert supervisor.calls == ["CONNECTION_RESET"]
    assert room._reconnect_in_progress is False


def test_shared_ingest_recovery_prefers_typed_terminal_failure(monkeypatch):
    orchestrator = RoomOrchestrator()
    room = RoomSession("room-auth", "https://live.example/auth", platform="douyin")

    class _Supervisor:
        def health(self):
            return {"failure_kind": "AUTH_REQUIRED"}

    class _Registry:
        def get_supervisor_if_exists(self, room_id):
            return _Supervisor() if room_id == room.room_id else None

    monkeypatch.setattr(
        "lsc.core.orchestrator.get_shared_ingest_registry",
        lambda: _Registry(),
    )
    assert orchestrator._shared_ingest_recovery_allowed(
        room,
        object(),
        "shared ingest upstream ffmpeg exited: code=0",
    ) is False


def test_global_tick_does_not_retry_auth_required_shared_ingest(monkeypatch):
    orchestrator = RoomOrchestrator()
    room = RoomSession("room-auth-tick", "https://live.example/auth", platform="douyin")
    room.is_recording = True
    ingest = SimpleNamespace(
        recording_active=False,
        recording_error="需要登录凭证",
        upstream_error="",
    )

    class _Supervisor:
        def health(self):
            return {"failure_kind": "AUTH_REQUIRED"}

    class _Registry:
        def get(self, room_id):
            return ingest if room_id == room.room_id else None

        def get_supervisor_if_exists(self, room_id):
            return _Supervisor() if room_id == room.room_id else None

    monkeypatch.setattr(
        "lsc.core.orchestrator.get_shared_ingest_registry",
        lambda: _Registry(),
    )
    monkeypatch.setattr(
        orchestrator,
        "_start_recording_reconnect_thread",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("terminal auth failure must not retry")
        ),
    )
    orchestrator._rooms[room.room_id] = room
    orchestrator._on_global_tick()
    assert room.is_recording is False
    assert room.is_reconnecting is False


def test_preview_encoder_failure_does_not_switch_upstream(monkeypatch):
    from lsc.core.services.ingest_supervisor import IngestSupervisor
    from lsc.core.session import RoomSession
    from lsc.platforms.base import StreamInfo

    ingest = SimpleNamespace(
        preview_starts=0,
        switches=0,
        process_id=11,
        recording_active=True,
        preview_subscribers=1,
        is_stopped=False,
        preview_error="",
        upstream_error="",
        recording_error="",
    )

    def start_preview(**_kwargs):
        ingest.preview_starts += 1
        return SimpleNamespace(ok=True, accepted=True, error="")

    def replace_upstream(*_args, **_kwargs):
        ingest.switches += 1
        return ""

    ingest.start_preview = start_preview
    ingest.replace_upstream = replace_upstream
    ingest.stop_preview_sink = lambda reason="": None
    ingest.add_error_callback = lambda _cb: None
    ingest.attach_preview_subscriber = lambda: object()
    ingest.start_recording = lambda *_a, **_k: SimpleNamespace(ok=True, error="")
    ingest.stop_recording_sink = lambda reason="": None
    ingest.stop = lambda reason="": None

    supervisor = IngestSupervisor("room-1", ingest)
    supervisor.handle_failure(
        "PREVIEW_ENCODER_FAILURE",
        error="preview encoder failed",
    )

    reconnects = []
    monkeypatch.setattr(
        "lsc.core.orchestrator.get_shared_ingest_registry",
        lambda: SimpleNamespace(
            get_supervisor_if_exists=lambda room_id: supervisor if room_id == "room-1" else None
        ),
    )
    monkeypatch.setattr(
        "lsc.core.orchestrator.is_platform_pipeline_component_enabled",
        lambda component, platform: component == "ingest_supervisor_v2",
    )
    orchestrator = RoomOrchestrator()
    room = RoomSession("room-1", "https://www.huya.com/1", platform="huya")
    room.stream_info = StreamInfo(
        platform="huya",
        room_url="https://www.huya.com/1",
        stream_url="https://tx.flv.huya.com/src/live.flv?wsSecret=abc",
    )
    room.is_recording = True
    monkeypatch.setattr(
        orchestrator,
        "_attempt_recording_reconnect",
        lambda *_args, **_kwargs: reconnects.append("reconnect"),
    )
    assert orchestrator._start_supervised_recovery(room, "preview encoder failed") is True
    assert ingest.preview_starts == 1
    assert ingest.switches == 0
    assert reconnects == []


def test_upstream_eof_recovery_keeps_recording_clock(monkeypatch):
    from datetime import datetime, timedelta

    from lsc.platforms.base import StreamInfo

    started = datetime.now() - timedelta(seconds=125)
    switches: list[str] = []
    start_recording_calls: list[str] = []
    ingest = SimpleNamespace(
        recording_active=True,
        is_stopped=False,
        preview_subscribers=1,
        upstream_error="shared ingest upstream ffmpeg exited: code=0",
        recording_error="",
        url="https://hs.flv.huya.com/src/old.flv?wsSecret=old",
        headers={},
        network_context={},
        recording_sink_is_live=lambda: True,
        upstream_is_live=lambda: False,
        bind_lease=lambda *_args, **_kwargs: None,
        replace_upstream=lambda url, **_kwargs: switches.append(url) or "",
    )

    class _Supervisor:
        def stop_recording(self, _reason):
            raise AssertionError("healthy recording sink must not be stopped")

        def switch_upstream(self, url, **_kwargs):
            switches.append(url)
            ingest.upstream_is_live = lambda: True
            ingest.upstream_error = ""
            return True

    class _Registry:
        def get(self, room_id):
            return ingest if room_id == "room-clock" else None

        def get_supervisor_if_exists(self, room_id):
            return _Supervisor() if room_id == "room-clock" else None

        def stop_room(self, *_args, **_kwargs):
            raise AssertionError("in-place upstream recovery must not stop the room")

    monkeypatch.setattr(
        "lsc.core.orchestrator.get_shared_ingest_registry",
        lambda: _Registry(),
    )
    orchestrator = RoomOrchestrator()
    room = RoomSession("room-clock", "https://www.huya.com/lpl", platform="huya")
    room.is_connected = True
    room.is_recording = True
    room.is_reconnecting = True
    room.record_started_at = started
    room.recording_id = "epoch-keep"
    room.recording_start_mono = 100.0
    room.reconnect_output_dir = "/tmp/keep"
    room.reconnect_encoder = "copy"
    room.reconnect_crf = 23
    room.reconnect_attempts = 0
    room.reconnect_next_attempt_at = 0.0
    room.stream_info = StreamInfo(
        platform="huya",
        room_url=room.room_url,
        stream_url="https://hs.flv.huya.com/src/new.flv?wsSecret=new",
        is_live=True,
        headers={"Referer": "https://www.huya.com/"},
    )
    monkeypatch.setattr(orchestrator, "refresh_stream_url", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        orchestrator,
        "start_recording",
        lambda *_args, **_kwargs: start_recording_calls.append("start") or True,
    )

    room.reconnect_next_attempt_at = 1.0
    orchestrator._attempt_recording_reconnect(
        room,
        "shared ingest upstream ffmpeg exited: code=0",
    )

    assert switches
    assert start_recording_calls == []
    assert room.record_started_at == started
    assert room.recording_id == "epoch-keep"
    assert room.is_recording is True
    assert room.is_reconnecting is False


def test_recover_uses_pending_lease_without_refreshing(monkeypatch):
    from datetime import datetime, timedelta

    from lsc.platforms.base import StreamInfo

    started = datetime.now() - timedelta(seconds=125)
    switches: list[str] = []
    refresh_calls: list[str] = []
    ingest = SimpleNamespace(
        recording_active=True,
        is_stopped=False,
        preview_subscribers=1,
        upstream_error="signed http remote EOF after media",
        recording_error="",
        url="https://hs.flv.huya.com/src/old.flv?wsSecret=old",
        headers={},
        network_context={},
        recording_sink_is_live=lambda: True,
        upstream_is_live=lambda: False,
        bind_lease=lambda *_args, **_kwargs: None,
        take_pending_lease=lambda: {
            "url": "https://hs.flv.huya.com/src/new.flv?wsSecret=new",
            "headers": {"Referer": "https://www.huya.com/"},
            "network_context": {},
            "lease_id": "lease-pending",
            "generation": 3,
        },
    )

    class _Supervisor:
        def stop_recording(self, _reason):
            raise AssertionError("lease rotation must not stop recording")

        def switch_upstream(self, url, **_kwargs):
            switches.append(url)
            ingest.upstream_is_live = lambda: True
            ingest.upstream_error = ""
            return True

    class _Registry:
        def get(self, room_id):
            return ingest if room_id == "room-pending" else None

        def get_supervisor_if_exists(self, room_id):
            return _Supervisor() if room_id == "room-pending" else None

        def stop_room(self, *_args, **_kwargs):
            raise AssertionError("lease rotation must not stop the room")

    monkeypatch.setattr(
        "lsc.core.orchestrator.get_shared_ingest_registry",
        lambda: _Registry(),
    )
    orchestrator = RoomOrchestrator()
    room = RoomSession("room-pending", "https://www.huya.com/lpl", platform="huya")
    room.is_connected = True
    room.is_recording = True
    room.record_started_at = started
    room.recording_id = "epoch-keep"
    room.stream_info = StreamInfo(
        platform="huya",
        room_url=room.room_url,
        stream_url="https://hs.flv.huya.com/src/old.flv?wsSecret=old",
        is_live=True,
    )
    monkeypatch.setattr(
        orchestrator,
        "refresh_stream_url",
        lambda *_args, **_kwargs: refresh_calls.append("refresh") or True,
    )

    assert orchestrator._recover_shared_upstream_in_place(room) is True
    assert switches == ["https://hs.flv.huya.com/src/new.flv?wsSecret=new"]
    assert refresh_calls == []
    assert room.record_started_at == started
    assert room.recording_id == "epoch-keep"


def test_supervised_recovery_rotate_lease_uses_in_place_path(monkeypatch):
    from lsc.platforms.base import StreamInfo

    rotates: list[str] = []
    ingest = SimpleNamespace(_upstream_has_produced_data=True)

    class _Supervisor:
        def run_recovery(self, callback, *, reason_code):
            rotates.append(reason_code)
            return bool(callback("rot-1"))

        def restart_preview_sink(self):
            raise AssertionError("lease rotation is not a preview encoder failure")

    class _Registry:
        def get(self, room_id):
            return ingest if room_id == "room-rot" else None

        def get_supervisor_if_exists(self, room_id):
            return _Supervisor() if room_id == "room-rot" else None

    monkeypatch.setattr(
        "lsc.core.orchestrator.get_shared_ingest_registry",
        lambda: _Registry(),
    )
    monkeypatch.setattr(
        "lsc.core.orchestrator.is_platform_pipeline_component_enabled",
        lambda component, platform: component == "ingest_supervisor_v2",
    )
    orchestrator = RoomOrchestrator()
    room = RoomSession("room-rot", "https://www.huya.com/1", platform="huya")
    room.stream_info = StreamInfo(
        platform="huya",
        room_url=room.room_url,
        stream_url="https://hs.flv.huya.com/src/live.flv?wsSecret=abc",
    )
    room.is_recording = True
    monkeypatch.setattr(
        orchestrator,
        "_recover_shared_upstream_in_place",
        lambda target: rotates.append("in-place") or True,
    )
    monkeypatch.setattr(
        orchestrator,
        "_attempt_recording_reconnect",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("rotate_lease must not restart the recording session")
        ),
    )
    assert orchestrator._start_supervised_recovery(
        room,
        "signed http remote EOF after media",
    ) is True
    assert "in-place" in rotates
