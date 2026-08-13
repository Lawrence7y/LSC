from __future__ import annotations

from types import SimpleNamespace

import pytest

from lsc.core.services.ingest_supervisor import IngestState, IngestSupervisor
from lsc.platforms.failure import FailureKind


class FakeIngest:
    url = "https://old.example/live.flv"
    headers = {}
    network_context = {}
    process_id = 101
    recording_process_id = 202
    preview_process_id = 303
    preview_subscribers = 1
    recording_active = False
    is_stopped = False
    preview_error = ""
    upstream_error = ""

    def attach_preview_subscriber(self):
        return object()

    def start_recording(self, path, profile=None):
        self.recording_active = True
        return SimpleNamespace(ok=True, error="")

    def stop_recording_sink(self, reason=""):
        self.recording_active = False

    def stop_preview_sink(self, reason=""):
        pass

    def start_preview(self, **_kwargs):
        self.preview_started = True
        return SimpleNamespace(ok=True, error="")

    def stop(self, reason=""):
        self.is_stopped = True
        self.recording_active = False

    def handle_preview_error(self, error):
        self.preview_error = error

    def handle_upstream_error(self, error):
        self.upstream_error = error

    def replace_upstream(self, url, *, headers=None, network_context=None):
        self.url = url
        self.headers = dict(headers or {})
        self.network_context = dict(network_context or {})
        return ""


def test_recording_failure_does_not_clear_preview_request(monkeypatch):
    ingest = FakeIngest()
    events = []
    supervisor = IngestSupervisor(
        "room-1",
        ingest,
        event_callback=events.append,
    )

    monkeypatch.setattr(
        "lsc.core.services.ingest_supervisor.SharedPreviewHandle",
        lambda *args, **kwargs: object(),
    )
    supervisor.attach_preview(
        on_init_segment=lambda data: None,
        on_media_segment=lambda data: None,
    )
    assert supervisor.preview_requested
    assert supervisor.start_recording("recording.mkv")
    assert supervisor.state == IngestState.RUNNING
    event_payload = events[-1].to_dict()
    assert event_payload["schema_version"] == 1
    assert event_payload["room_id"] == "room-1"
    assert supervisor.health()["recording_active"] is True

    supervisor.stop_recording()
    assert supervisor.preview_requested
    assert supervisor.state == IngestState.RUNNING
    assert any(event.event_type == "SINK_DETACHED" for event in events)


def test_runtime_event_v2_fields_are_present_and_redacted():
    events = []
    supervisor = IngestSupervisor("room-events", FakeIngest(), event_callback=events.append)
    supervisor.set_lease_context(
        session_id="session-1",
        recording_session_id="recording-1",
        platform_id="bilibili",
        lease_id="lease-1",
        candidate_id="candidate-1",
        generation=3,
    )
    supervisor.handle_failure(
        "CONNECT_TIMEOUT",
        error="https://cdn.example/live?token=secret-token",
        retry_after=2.5,
    )

    payload = events[-1].to_dict()
    assert payload["event_id"].startswith("evt-")
    assert payload["room_session_id"] == "room-events"
    assert payload["recording_session_id"] == "recording-1"
    assert payload["platform_id"] == "bilibili"
    assert payload["component"] == "ingest"
    assert payload["state_from"] == "IDLE"
    assert payload["state_to"] == "BACKING_OFF"
    assert payload["lease_generation"] == 3
    assert payload["retry_after_seconds"] == 2.5
    assert "secret-token" not in str(payload)
    assert payload["safe_context"] == payload["context"]


def test_preview_first_attach_starts_preview_sink_before_recording(monkeypatch):
    ingest = FakeIngest()
    supervisor = IngestSupervisor("room-preview-first", ingest)
    monkeypatch.setattr(
        "lsc.core.services.ingest_supervisor.SharedPreviewHandle",
        lambda *args, **kwargs: object(),
    )
    supervisor.attach_preview(
        on_init_segment=lambda _data: None,
        on_media_segment=lambda _data: None,
    )
    assert ingest.preview_started is True
    assert supervisor.preview_requested is True


def test_preview_attach_failure_is_not_reported_as_running(monkeypatch):
    class FailedPreviewIngest(FakeIngest):
        process_id = None

        def start_preview(self, **_kwargs):
            return SimpleNamespace(ok=False, error="HTTP 403 forbidden")

    class FakeHandle:
        stopped = False

        def stop(self):
            self.stopped = True

    handle = FakeHandle()
    ingest = FailedPreviewIngest()
    supervisor = IngestSupervisor("room-preview-failed", ingest)
    monkeypatch.setattr(
        "lsc.core.services.ingest_supervisor.SharedPreviewHandle",
        lambda *args, **kwargs: handle,
    )

    with pytest.raises(RuntimeError, match="403"):
        supervisor.attach_preview(
            on_init_segment=lambda _data: None,
            on_media_segment=lambda _data: None,
        )

    assert handle.stopped is True
    assert supervisor.state == IngestState.FAILED


def test_recovery_is_serialized_and_increments_generation():
    supervisor = IngestSupervisor("room-1", FakeIngest())
    called = []
    assert supervisor.run_recovery(
        lambda recovery_id: called.append(recovery_id) or True
    )
    assert supervisor.generation == 1
    assert called == ["room-1-recovery-1"]
    assert supervisor.state == IngestState.RUNNING


def test_stop_blocks_recovery_and_stale_upstream_switch():
    supervisor = IngestSupervisor("room-stop-race", FakeIngest())
    supervisor.stop()
    called = []
    assert supervisor.run_recovery(
        lambda _recovery_id: called.append(True) or True
    ) is False
    assert called == []
    assert supervisor.switch_upstream("https://new.example/live.flv") is False
    assert supervisor.state == IngestState.STOPPED


def test_inflight_recovery_cannot_restore_running_after_stop():
    supervisor = IngestSupervisor("room-stop-inflight", FakeIngest())

    def recover(_recovery_id):
        supervisor.stop("application shutdown")
        return True

    assert supervisor.run_recovery(recover) is False
    assert supervisor.state == IngestState.STOPPED


def test_stop_closes_both_sinks():
    ingest = FakeIngest()
    supervisor = IngestSupervisor("room-1", ingest)
    supervisor.start_recording("recording.mkv")
    supervisor.stop()
    assert ingest.is_stopped
    assert supervisor.state == IngestState.STOPPED


def test_stop_propagates_total_cleanup_deadline():
    class DeadlineIngest(FakeIngest):
        def stop(self, reason="", *, deadline_monotonic=None):
            self.deadline_monotonic = deadline_monotonic
            super().stop(reason)

    ingest = DeadlineIngest()
    supervisor = IngestSupervisor("room-deadline", ingest)
    supervisor.stop(timeout_sec=0.5)
    assert ingest.deadline_monotonic is not None
    assert ingest.deadline_monotonic >= __import__("time").monotonic() - 0.1


def test_add_event_callback_keeps_existing_observers():
    first = []
    second = []
    supervisor = IngestSupervisor("room-1", FakeIngest(), event_callback=first.append)
    supervisor.add_event_callback(second.append)
    supervisor.stop()
    assert first and second
    assert first[-1].event_type == "INGEST_STATE_CHANGED"
    assert second[-1].to_dict()["room_id"] == "room-1"


def test_refresh_state_does_not_invalidate_current_generation():
    supervisor = IngestSupervisor("room-1", FakeIngest())
    generation = supervisor.begin_refresh()
    assert supervisor.state == IngestState.REFRESHING
    assert supervisor.is_generation_current(generation)
    supervisor.finish_refresh(True)
    assert supervisor.state == IngestState.IDLE


def test_typed_failure_preserves_recording_on_preview_failure():
    ingest = FakeIngest()
    events = []
    supervisor = IngestSupervisor("room-1", ingest, event_callback=events.append)
    supervisor.start_recording("recording.mkv")
    state = supervisor.handle_failure(
        "PREVIEW_ENCODER_FAILURE",
        error="preview encoder failed",
    )
    assert state == IngestState.DEGRADED
    assert supervisor.recording_requested
    assert events[-1].to_dict()["failure_kind"] == "PREVIEW_ENCODER_FAILURE"
    assert supervisor.health()["failure_kind"] == "PREVIEW_ENCODER_FAILURE"


def test_typed_failure_accepts_failure_kind_enum_objects():
    supervisor = IngestSupervisor("room-1", FakeIngest())
    state = supervisor.handle_failure(
        FailureKind.AUTH_REQUIRED,
        error="login required",
    )
    assert state == IngestState.AUTH_REQUIRED
    assert supervisor.health()["failure_kind"] == "AUTH_REQUIRED"


def test_wrapped_upstream_403_is_published_as_cdn_forbidden():
    events = []
    supervisor = IngestSupervisor("room-1", FakeIngest(), event_callback=events.append)
    supervisor._on_ingest_error(
        "upstream",
        "录制启动失败：直播流连接异常（流地址可能已过期，请重新连接房间）| "
        "upstream: HTTP error 403 Forbidden",
    )
    assert any(
        event.event_type == "UPSTREAM_FAILED"
        and event.failure_kind == "CDN_FORBIDDEN"
        for event in events
    )
    assert supervisor.health()["failure_kind"] == "CDN_FORBIDDEN"


def test_shared_ingest_error_callback_publishes_typed_event():
    events = []
    supervisor = IngestSupervisor("room-1", FakeIngest(), event_callback=events.append)
    supervisor._on_ingest_error("preview", "nvenc encoder failed")
    assert any(
        event.event_type == "SINK_FAILED"
        and event.failure_kind == "PREVIEW_ENCODER_FAILURE"
        for event in events
    )


def test_recording_sink_failure_does_not_stop_preview_request():
    supervisor = IngestSupervisor("room-1", FakeIngest())
    supervisor._preview_requested = True
    state = supervisor.handle_failure(
        "RECORDING_SINK_FAILURE",
        error="recording sink write failed",
    )
    assert state == IngestState.DEGRADED
    assert supervisor.preview_requested


def test_refresh_events_have_unique_recovery_ids_without_generation_bump():
    events = []
    supervisor = IngestSupervisor("room-1", FakeIngest(), event_callback=events.append)
    generation = supervisor.generation
    supervisor.begin_refresh()
    supervisor.finish_refresh(True)
    supervisor.begin_refresh()
    supervisor.finish_refresh(False)
    refresh_events = [
        event for event in events
        if event.event_type in {"LEASE_REFRESH_STARTED", "INGEST_STATE_CHANGED"}
        and event.recovery_id
    ]
    assert len({event.recovery_id for event in refresh_events}) >= 2
    assert supervisor.generation == generation


def test_upstream_switch_keeps_sink_requests_and_updates_generation():
    ingest = FakeIngest()
    supervisor = IngestSupervisor("room-1", ingest)
    supervisor._recording_requested = True
    supervisor._preview_requested = True
    old_generation = supervisor.generation

    assert supervisor.switch_upstream(
        "https://new.example/live.flv",
        headers={"Referer": "https://new.example/"},
    )

    assert ingest.url == "https://new.example/live.flv"
    assert supervisor.generation == old_generation + 1
    assert supervisor.recording_requested is True
    assert supervisor.preview_requested is True


def test_recovery_failure_enters_backoff_and_has_finite_budget(monkeypatch):
    supervisor = IngestSupervisor("room-1", FakeIngest())
    supervisor._max_recovery_attempts = 1
    monkeypatch.setattr(
        "lsc.core.services.ingest_supervisor.time.monotonic",
        lambda: 100.0,
    )
    assert supervisor.run_recovery(lambda recovery_id: False) is False
    assert supervisor.state == IngestState.BACKING_OFF
    assert supervisor.health()["recovery_attempt"] == 1
    assert supervisor.run_recovery(lambda recovery_id: True) is False
    assert supervisor.state == IngestState.FAILED


def test_retry_after_delays_recovery(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(
        "lsc.core.services.ingest_supervisor.time.monotonic",
        lambda: clock[0],
    )
    supervisor = IngestSupervisor("room-1", FakeIngest())
    supervisor._on_ingest_error("upstream", "HTTP 429 Too Many Requests; Retry-After: 12")
    assert supervisor.health()["next_recovery_at"] == 112.0
    assert supervisor.run_recovery(lambda _recovery_id: True) is False
    assert supervisor.state == IngestState.BACKING_OFF


def test_preview_encoder_failure_restarts_sink_without_switching_upstream():
    ingest = FakeIngest()
    ingest.preview_started = False
    ingest.switched = False

    def start_preview(**_kwargs):
        ingest.preview_started = True
        return SimpleNamespace(ok=True, accepted=True, error="")

    def replace_upstream(*_args, **_kwargs):
        ingest.switched = True
        return ""

    ingest.start_preview = start_preview
    ingest.replace_upstream = replace_upstream
    supervisor = IngestSupervisor("room-preview-encoder", ingest)
    supervisor.handle_failure(
        "PREVIEW_ENCODER_FAILURE",
        error="preview encoder failed",
    )
    assert supervisor.restart_preview_sink() is True
    assert ingest.preview_started is True
    assert ingest.switched is False
