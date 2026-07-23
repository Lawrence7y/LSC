"""Broadcast queue terminal-message preservation tests (H3a)."""
from __future__ import annotations

import os
import sys

import pytest

_backend_dir = os.path.join(os.path.dirname(__file__), '..', 'python-backend')
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

from broadcast_hub import BroadcastHub
from lsc.core.events import EventBus


class _FakeOrch:
    def __init__(self):
        self.bus = EventBus()


def _make_hub() -> BroadcastHub:
    return BroadcastHub(_FakeOrch())


def _drain_queue(hub: BroadcastHub) -> list[dict]:
    items: list[dict] = []
    while True:
        item = hub.get_broadcast(block=False)
        if item is None:
            break
        items.append(item)
    return items


class TestTerminalBroadcastPreservation:
    def test_clip_completed_survives_full_queue_of_mse_segments(self):
        hub = _make_hub()
        maxsize = hub._broadcast_queue.maxsize

        for i in range(maxsize):
            hub.queue_broadcast({"type": "mse_segment", "data": {"i": i}})

        assert hub._broadcast_queue.full()

        terminal = {"type": "clip_completed", "data": {"clip_id": "clip-1"}}
        hub.queue_broadcast(terminal)

        queued = _drain_queue(hub)
        types = [m.get("type") for m in queued]
        assert "clip_completed" in types
        assert any(m == terminal for m in queued)

    def test_droppable_messages_evicted_before_terminal_enqueue(self):
        hub = _make_hub()
        maxsize = hub._broadcast_queue.maxsize

        for i in range(maxsize):
            hub.queue_broadcast({"type": "mse_segment", "data": {"i": i}})

        hub.queue_broadcast({"type": "clip_failed", "data": {"clip_id": "clip-2"}})

        queued = _drain_queue(hub)
        assert len(queued) == 1
        assert queued[0]["type"] == "clip_failed"

    def test_incoming_droppable_dropped_when_queue_full(self):
        hub = _make_hub()
        maxsize = hub._broadcast_queue.maxsize

        for i in range(maxsize):
            hub.queue_broadcast({"type": "rooms_updated", "data": {"i": i}})

        hub.queue_broadcast({"type": "mse_segment", "data": {"overflow": True}})

        assert hub._broadcast_queue.qsize() == maxsize
        queued = _drain_queue(hub)
        assert all(m.get("type") == "rooms_updated" for m in queued)
