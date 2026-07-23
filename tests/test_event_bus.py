# tests/test_event_bus.py
from __future__ import annotations

import threading

import pytest

from lsc.core.events import EventBus


def test_subscribe_emit_delivers_args():
    bus = EventBus()
    seen: list[tuple] = []
    bus.subscribe("room_connect_finished", lambda *a: seen.append(a))
    bus.emit("room_connect_finished", "r1", True, "")
    assert seen == [("r1", True, "")]


def test_unsubscribe_stops_delivery():
    bus = EventBus()
    seen: list[int] = []
    def cb(*_a):
        seen.append(1)
    bus.subscribe("global_tick", cb)
    bus.unsubscribe("global_tick", cb)
    bus.emit("global_tick")
    assert seen == []


def test_callback_exception_does_not_stop_others(caplog):
    bus = EventBus()
    order: list[str] = []
    def bad(*_a):
        order.append("bad")
        raise RuntimeError("boom")
    def good(*_a):
        order.append("good")
    bus.subscribe("low_tick", bad)
    bus.subscribe("low_tick", good)
    bus.emit("low_tick")
    assert order == ["bad", "good"]


def test_emit_from_wrong_thread_raises_when_bound():
    bus = EventBus()
    bus.bind_emitter_thread(threading.current_thread())
    err: list[BaseException] = []
    def other():
        try:
            bus.emit("global_tick")
        except Exception as e:
            err.append(e)
    t = threading.Thread(target=other)
    t.start()
    t.join(2)
    assert err and isinstance(err[0], RuntimeError)
