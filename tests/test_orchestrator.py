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
