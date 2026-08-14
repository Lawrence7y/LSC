# tests/test_orchestrator.py
from __future__ import annotations

import threading
import time

import pytest

from lsc.config import LscConfig
from lsc.core.orchestrator import RoomOrchestrator
from lsc.platforms.base import StreamInfo


@pytest.fixture
def orch():
    o = RoomOrchestrator(controller_factory=lambda: object(), preview_factory=lambda: object())
    o.start()
    yield o
    o.shutdown(timeout_sec=5.0)


def test_call_executes_on_orchestrator_thread(orch):
    tid_holder: list[int] = []
    def fn():
        tid_holder.append(threading.get_ident())
        return 42
    assert orch.call(fn) == 42
    assert tid_holder[0] == orch.thread_ident


def test_call_from_orchestrator_thread_runs_inline(orch):
    def nested():
        return orch.call(lambda: "inline")
    assert orch.call(nested) == "inline"


def test_submit_fire_and_forget(orch):
    done = threading.Event()
    orch.submit(lambda: done.set())
    assert done.wait(2.0)


def test_worker_submission_is_noop_after_shutdown_starts():
    orchestrator = RoomOrchestrator()
    orchestrator._stop.set()
    assert orchestrator._submit_worker(lambda: None) is False
    orchestrator._worker_pool.shutdown(wait=False, cancel_futures=True)


def test_call_rejects_when_pending_full(orch):
    block = threading.Event()
    release = threading.Event()
    def blocker():
        block.set()
        release.wait(5)
    threads = []
    for _ in range(8):
        t = threading.Thread(target=lambda: orch.call(blocker, timeout=5.0))
        t.start()
        threads.append(t)
    assert block.wait(2.0)
    # Wait until pending is full (actor is serial; first call holds orch thread)
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        with orch._pending_lock:
            if orch._pending_count >= RoomOrchestrator._MAX_PENDING_REQUESTS:
                break
        time.sleep(0.01)
    with pytest.raises(TimeoutError, match="too busy"):
        orch.call(lambda: None, timeout=1.0)
    release.set()
    for t in threads:
        t.join(5)


def test_is_stream_offline_error():
    from lsc.core.orchestrator import _is_stream_offline_error
    assert _is_stream_offline_error("直播间已结束")
    assert not _is_stream_offline_error("connection reset")


def test_add_get_remove_room(orch):
    r = orch.add_room("https://live.example/1")
    assert r is not None
    assert orch.room_count() == 1
    assert orch.get_room(r.room_id) is r
    assert orch.remove_room(r.room_id) is True
    assert orch.room_count() == 0


def test_max_rooms_cap(orch):
    from lsc.core.orchestrator import MAX_ROOMS
    for i in range(MAX_ROOMS):
        assert orch.add_room(f"https://live.example/{i}") is not None
    assert orch.add_room("https://live.example/extra") is None


def test_tick_layers_emit(orch):
    global_n = medium_n = low_n = 0

    def g():
        nonlocal global_n
        global_n += 1

    def m():
        nonlocal medium_n
        medium_n += 1

    def low():
        nonlocal low_n
        low_n += 1

    orch.bus.subscribe("global_tick", lambda: g())
    orch.bus.subscribe("medium_tick", lambda: m())
    orch.bus.subscribe("low_tick", lambda: low())
    orch.add_room("https://live.example/tick")
    for _ in range(4):
        orch.call(orch._on_global_tick)
    assert global_n == 4
    assert medium_n == 4
    assert low_n == 1


def test_missing_controller_tick_does_not_kill_orchestrator(orch):
    room = orch.add_room("https://live.example/no-tick")
    assert room is not None
    orch.call(lambda: setattr(room, "is_recording", True))

    # controller_factory 返回 object()，不提供 tick/watchdog_check。
    orch.call(orch._on_global_tick)

    assert orch.call(lambda: "still-alive") == "still-alive"


def test_global_tick_uses_shared_health_when_legacy_controller_exists(monkeypatch):
    """Shared V2 rooms must not fall back to the desktop controller watchdog."""
    orchestrator = RoomOrchestrator(
        controller_factory=lambda: object(),
        preview_factory=lambda: object(),
    )
    room = orchestrator.add_room("https://live.example/shared-with-controller")
    assert room is not None
    room.is_recording = True
    room.record_output_path = ""
    ingest = type(
        "FakeIngest",
        (),
        {
            "recording_active": False,
            "recording_error": "AUTH_REQUIRED",
            "upstream_error": "",
        },
    )()

    class Registry:
        def get(self, room_id):
            return ingest if room_id == room.room_id else None

        def get_supervisor_if_exists(self, room_id):
            return None

    monkeypatch.setattr(
        "lsc.core.orchestrator.get_shared_ingest_registry",
        lambda: Registry(),
    )
    monkeypatch.setattr(
        orchestrator,
        "_shared_ingest_recovery_allowed",
        lambda *_args: False,
    )
    orchestrator._on_global_tick()

    assert room.is_recording is False
    assert room.is_reconnecting is False


def test_shutdown_releases_shared_ingest_without_legacy_controller(monkeypatch):
    """V2-only rooms must not leak their upstream on application shutdown."""

    class FakeIngest:
        pass

    class FakeRegistry:
        def __init__(self):
            self.ingest = FakeIngest()
            self.stopped: list[tuple[str, str]] = []

        def get(self, room_id: str):
            return self.ingest

        def stop_room(self, room_id: str, reason: str = "") -> None:
            self.stopped.append((room_id, reason))
            self.ingest = None

    registry = FakeRegistry()
    monkeypatch.setattr("lsc.core.orchestrator.get_shared_ingest_registry", lambda: registry)

    orchestrator = RoomOrchestrator()
    room = orchestrator.add_room("https://example.com/live.m3u8")
    assert room is not None
    room.is_recording = True
    room.controller = None

    stats = orchestrator._shutdown_resources()

    assert stats["shared_ingests_stopped"] == 1
    assert registry.stopped == [(room.room_id, "orchestrator shutdown")]


def test_refresh_stream_url_uses_v2_resolver_when_platform_is_allowlisted(monkeypatch):
    orchestrator = RoomOrchestrator()
    room = orchestrator.add_room("https://live.example/v2-refresh")
    assert room is not None
    room.platform = "direct"
    room.platform_name = "direct"
    room.selected_quality = "origin"
    room.is_connected = True

    cfg = LscConfig(
        platform_pipeline_v2_enabled=True,
        platform_pipeline_v2_allowlist=["direct"],
        ffmpeg_path="ffmpeg",
        ffprobe_path="ffprobe",
    )
    monkeypatch.setattr("lsc.core.orchestrator.load_config", lambda: cfg)
    monkeypatch.setattr(
        orchestrator,
        "_resolve_v2_stream_info",
        lambda *_args, **_kwargs: StreamInfo(
            platform="direct",
            room_url=room.room_url,
            stream_url="https://cdn.example/refreshed.flv",
            selected_quality="origin",
            is_live=True,
        ),
    )
    monkeypatch.setattr("lsc.core.orchestrator._sync_controller_stream", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "lsc.core.orchestrator.parse_stream",
        lambda *_args, **_kwargs: pytest.fail("V2 refresh must not call legacy parse_stream"),
    )

    assert orchestrator.refresh_stream_url(room.room_id, force=True) is True
    assert room.stream_info is not None
    assert room.stream_info.stream_url.endswith("refreshed.flv")


def test_v2_stream_info_exposes_only_the_probed_lease_candidate(monkeypatch):
    from lsc.platforms.models import (
        PlatformCapabilities,
        ProbeResult,
        ResolveResult,
        StreamCandidate,
        StreamLease,
    )

    orchestrator = RoomOrchestrator()
    room = orchestrator.add_room("https://live.bilibili.com/1")
    assert room is not None
    room.platform = "bilibili"
    selected = StreamCandidate(
        candidate_id="bilibili|10000|1",
        url="https://good.example/live.flv",
        quality_id="10000",
        protocol="flv",
        cdn_id="good",
    )
    unselected = StreamCandidate(
        candidate_id="bilibili|10000|2",
        url="https://bad.example/live.flv",
        quality_id="10000",
        protocol="flv",
        cdn_id="bad",
    )
    result = ResolveResult(
        platform="bilibili",
        room_url=room.room_url,
        live_status="LIVE",
        capabilities=PlatformCapabilities(platform="bilibili"),
        candidates=(selected, unselected),
    )
    probe = ProbeResult(
        candidate_id=selected.candidate_id,
        reachable=True,
        has_video=True,
        timestamp_ok=True,
    )
    lease = StreamLease(
        lease_id="lease-1",
        room_id=room.room_id,
        candidate=selected,
        issued_at=time.time(),
        generation=2,
    )
    monkeypatch.setattr("lsc.platforms.resolver.resolve_stream_v2", lambda *_args, **_kwargs: result)
    monkeypatch.setattr(
        "lsc.platforms.resolver.probe_candidates",
        lambda *_args, **_kwargs: {selected.candidate_id: probe},
    )
    monkeypatch.setattr("lsc.platforms.resolver.select_stream_lease", lambda *_args, **_kwargs: lease)

    info = orchestrator._resolve_v2_stream_info(
        room,
        LscConfig(ffprobe_path="ffprobe"),
    )

    assert info is not None
    assert info.stream_url == selected.url
    assert info.quality_urls == {"10000": selected.url}
    assert unselected.url not in info.quality_urls.values()
    assert info.raw["candidate_id"] == selected.candidate_id
    assert info.raw["candidate_cdn_id"] == "good"


def test_room_stream_not_reusable_after_huya_eof():
    from types import SimpleNamespace

    from lsc.core.orchestrator import _room_stream_is_reusable

    room = SimpleNamespace(
        stream_parsed_at=time.time(),
        stream_url_cached="https://hs.flv.huya.com/src/live.flv",
        stream_info=SimpleNamespace(stream_url="https://hs.flv.huya.com/src/live.flv"),
        controller=None,
        last_error=(
            "shared ingest upstream ffmpeg exited: code=0 | "
            "stderr: error=End of file. | Error during demuxing: I/O error"
        ),
    )
    assert _room_stream_is_reusable(room) is False
