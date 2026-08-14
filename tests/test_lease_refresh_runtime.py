from __future__ import annotations

from types import SimpleNamespace

from lsc.core.orchestrator import RoomOrchestrator
from lsc.platforms.base import StreamInfo


class _Lease:
    def __init__(self, lease_id: str):
        self.lease_id = lease_id


class _LeaseManager:
    def needs_refresh(self, lease, *, now):
        return True

    def is_expired(self, lease, *, now):
        return False


class _Supervisor:
    def __init__(self):
        self.started = 0
        self.finished: list[bool] = []

    def begin_refresh(self, reason):
        self.started += 1
        return 1

    def finish_refresh(self, success, *, reason_code):
        self.finished.append(success)


class _Registry:
    def __init__(self, supervisor):
        self.supervisor = supervisor

    def get_supervisor_if_exists(self, room_id):
        return self.supervisor


class _Pool:
    def submit(self, callback):
        callback()


def test_proactive_lease_refresh_keeps_current_ingest_running(monkeypatch):
    orch = RoomOrchestrator()
    room = SimpleNamespace(
        room_id="room-refresh",
        platform="bilibili",
        platform_name="B站",
        selected_quality="原画",
    )
    old = _Lease("lease-old")
    refreshed = _Lease("lease-new")
    supervisor = _Supervisor()
    orch._rooms[room.room_id] = room
    orch._stream_leases[room.room_id] = old
    orch._lease_managers[room.room_id] = _LeaseManager()
    orch._worker_pool = _Pool()

    monkeypatch.setattr(
        "lsc.core.orchestrator.get_shared_ingest_registry",
        lambda: _Registry(supervisor),
    )
    monkeypatch.setattr(
        "lsc.core.orchestrator.is_platform_pipeline_component_enabled",
        lambda *args, **kwargs: True,
    )

    def resolve(room_arg, cfg, *, quality_preset=None):
        assert room_arg is room
        orch._stream_leases[room.room_id] = refreshed
        return StreamInfo(
            platform="bilibili",
            room_url="https://live.example/room",
            stream_url="https://cdn.example/new.flv",
            is_live=True,
        )

    monkeypatch.setattr(orch, "_resolve_v2_stream_info", resolve)
    orch.submit = lambda fn, *args, **kwargs: fn(*args, **kwargs)

    orch._schedule_v2_lease_refresh(room)

    assert supervisor.started == 1
    assert supervisor.finished == [True]
    assert orch._pending_stream_infos[room.room_id].stream_url.endswith("new.flv")
    assert orch._stream_leases[room.room_id] is refreshed
    assert room.room_id not in orch._lease_refresh_inflight
