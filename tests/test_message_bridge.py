"""BroadcastHub unit tests.

Tests broadcast queue, droppable overflow, and EventBus subscription payloads.
Cross-thread call/timeout lives on RoomOrchestrator (see test_orchestrator.py).
"""
from __future__ import annotations

import os
import sys
import threading

# Add python-backend to path
_backend_dir = os.path.join(os.path.dirname(__file__), '..', 'python-backend')
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

from broadcast_hub import BroadcastHub

from lsc.core.events import EventBus


class _FakeOrch:
    def __init__(self):
        self.bus = EventBus()


class TestBroadcastHub:
    """Test BroadcastHub with a fake orchestrator EventBus."""

    def _make_hub(self):
        orch = _FakeOrch()
        return BroadcastHub(orch), orch

    def test_manager_property_returns_orchestrator(self):
        hub, orch = self._make_hub()
        assert hub.manager is orch

    def test_queue_broadcast_and_get(self):
        hub, _ = self._make_hub()
        msg = {"type": "test", "data": {"key": "value"}}
        hub.queue_broadcast(msg)
        result = hub.get_broadcast(block=False)
        assert result == msg

    def test_get_broadcast_empty_returns_none(self):
        hub, _ = self._make_hub()
        result = hub.get_broadcast(block=False)
        assert result is None

    def test_queue_broadcast_multiple_messages(self):
        hub, _ = self._make_hub()
        msgs = [{"type": f"msg_{i}"} for i in range(5)]
        for m in msgs:
            hub.queue_broadcast(m)
        for expected in msgs:
            result = hub.get_broadcast(block=False)
            assert result == expected

    def test_on_connect_finished_queues_broadcast(self):
        hub, _ = self._make_hub()
        hub._on_connect_finished("room1", True, "")
        result = hub.get_broadcast(block=False)
        assert result["type"] == "room_connect_finished"
        assert result["data"]["room_id"] == "room1"
        assert result["data"]["success"] is True

    def test_connect_and_recording_events_redact_signed_urls(self):
        hub, _ = self._make_hub()
        hub._on_connect_finished(
            "room1",
            False,
            "ffmpeg failed at https://cdn.example/live.flv?token=bridge-secret",
        )
        result = hub.get_broadcast(block=False)
        assert "bridge-secret" not in str(result)
        hub._on_recording_stopped(
            "room1",
            "upstream",
            "https://cdn.example/live.flv?signature=stop-secret",
        )
        result = hub.get_broadcast(block=False)
        assert "stop-secret" not in str(result)

    def test_runtime_event_mapping_has_final_public_redaction_boundary(self):
        hub, _ = self._make_hub()
        hub._on_runtime_event({
            "event_type": "UPSTREAM_FAILED",
            "room_id": "room1",
            "context": {
                "url": "https://cdn.example/live.flv?token=runtime-secret",
                "Cookie": "session=runtime-cookie",
            },
        })
        result = hub.get_broadcast(block=False)
        assert result["type"] == "runtime_event"
        assert "runtime-secret" not in str(result)
        assert "runtime-cookie" not in str(result)

    def test_runtime_event_is_logged_without_secrets(self, caplog):
        hub, _ = self._make_hub()
        with caplog.at_level("INFO", logger="broadcast_hub"):
            hub._on_runtime_event({
                "event_type": "UPSTREAM_FAILED",
                "room_id": "room1",
                "component": "ingest",
                "state_from": "RUNNING",
                "state_to": "FAILED",
                "failure_kind": "CONNECTION_RESET",
                "context": {
                    "url": "https://cdn.example/live.flv?token=runtime-secret",
                },
            })
        joined = "\n".join(record.getMessage() for record in caplog.records)
        assert "runtime_event room=room1" in joined
        assert "UPSTREAM_FAILED" in joined
        assert "runtime-secret" not in joined

    def test_bus_emit_connect_finished_queues_broadcast(self):
        hub, orch = self._make_hub()
        orch.bus.emit("room_connect_finished", "room1", True, "")
        result = hub.get_broadcast(block=False)
        assert result["type"] == "room_connect_finished"
        assert result["data"]["room_id"] == "room1"
        assert result["data"]["success"] is True

    def test_on_batch_record_progress_queues_broadcast(self):
        hub, _ = self._make_hub()
        hub._on_batch_record_progress("room2", False)
        result = hub.get_broadcast(block=False)
        assert result["type"] == "recording_started"
        assert result["data"]["room_id"] == "room2"
        assert result["data"]["success"] is False

    def test_bus_emit_batch_record_progress_queues_broadcast(self):
        hub, orch = self._make_hub()
        orch.bus.emit("batch_record_progress", "room2", False)
        result = hub.get_broadcast(block=False)
        assert result["type"] == "recording_started"
        assert result["data"]["success"] is False

    def test_bus_emit_recording_stopped_queues_broadcast(self):
        hub, orch = self._make_hub()
        orch.bus.emit("recording_stopped", "room3", "offline", "下播")
        result = hub.get_broadcast(block=False)
        assert result["type"] == "recording_stopped"
        assert result["data"]["room_id"] == "room3"
        assert result["data"]["reason"] == "offline"
        assert result["data"]["message"] == "下播"

    def test_broadcast_queue_max_size(self):
        hub, _ = self._make_hub()
        maxsize = hub._broadcast_queue.maxsize
        # Droppable overflow is discarded instead of expanding the queue.
        for i in range(maxsize + 5):
            hub.queue_broadcast({"type": "mse_segment", "data": {"i": i}})
        count = 0
        while hub.get_broadcast(block=False) is not None:
            count += 1
        assert count == maxsize

    def test_signal_callbacks_do_not_block_when_broadcast_queue_is_full(self):
        hub, _ = self._make_hub()
        for i in range(1000):
            hub.queue_broadcast({"type": "rooms_updated", "data": {"i": i}})

        finished = threading.Event()

        def invoke_callback():
            hub._on_connect_finished("room-full", True, "")
            finished.set()

        t = threading.Thread(target=invoke_callback, daemon=True)
        t.start()

        assert finished.wait(timeout=0.5) is True
        assert hub._broadcast_queue.qsize() <= 1000
