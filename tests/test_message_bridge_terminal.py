"""Broadcast queue terminal-message preservation tests (H3a)."""
from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock

import pytest

_backend_dir = os.path.join(os.path.dirname(__file__), '..', 'python-backend')
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

from message_bridge import QtManagerBridge


def _make_bridge() -> QtManagerBridge:
    mock_manager = MagicMock()
    mock_manager.room_connect_finished = MagicMock()
    mock_manager.batch_record_progress = MagicMock()
    mock_manager.recording_stopped = MagicMock()
    mock_manager.room_connect_finished.connect = MagicMock()
    mock_manager.batch_record_progress.connect = MagicMock()
    mock_manager.recording_stopped.connect = MagicMock()
    return QtManagerBridge(mock_manager)


def _drain_queue(bridge: QtManagerBridge) -> list[dict]:
    items: list[dict] = []
    while True:
        item = bridge.get_broadcast(block=False)
        if item is None:
            break
        items.append(item)
    return items


class TestTerminalBroadcastPreservation:
    def test_clip_completed_survives_full_queue_of_mse_segments(self):
        bridge = _make_bridge()
        maxsize = bridge._broadcast_queue.maxsize

        for i in range(maxsize):
            bridge.queue_broadcast({"type": "mse_segment", "data": {"i": i}})

        assert bridge._broadcast_queue.full()

        terminal = {"type": "clip_completed", "data": {"clip_id": "clip-1"}}
        bridge.queue_broadcast(terminal)

        queued = _drain_queue(bridge)
        types = [m.get("type") for m in queued]
        assert "clip_completed" in types
        assert any(m == terminal for m in queued)

    def test_droppable_messages_evicted_before_terminal_enqueue(self):
        bridge = _make_bridge()
        maxsize = bridge._broadcast_queue.maxsize

        for i in range(maxsize):
            bridge.queue_broadcast({"type": "mse_segment", "data": {"i": i}})

        bridge.queue_broadcast({"type": "clip_failed", "data": {"clip_id": "clip-2"}})

        queued = _drain_queue(bridge)
        assert len(queued) == 1
        assert queued[0]["type"] == "clip_failed"

    def test_incoming_droppable_dropped_when_queue_full(self):
        bridge = _make_bridge()
        maxsize = bridge._broadcast_queue.maxsize

        for i in range(maxsize):
            bridge.queue_broadcast({"type": "rooms_updated", "data": {"i": i}})

        bridge.queue_broadcast({"type": "mse_segment", "data": {"overflow": True}})

        assert bridge._broadcast_queue.qsize() == maxsize
        queued = _drain_queue(bridge)
        assert all(m.get("type") == "rooms_updated" for m in queued)
