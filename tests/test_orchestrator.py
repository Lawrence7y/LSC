# tests/test_orchestrator.py
from __future__ import annotations

import threading
import time

import pytest

from lsc.core.orchestrator import RoomOrchestrator


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
