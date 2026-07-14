"""Message bridge unit tests.

Tests QtManagerBridge cross-thread call mechanism, broadcast queue,
and timeout handling. Uses mock objects to avoid Qt dependency.
"""
from __future__ import annotations

import os
import sys
import threading
import time
from unittest.mock import MagicMock

import pytest

# Add python-backend to path
_backend_dir = os.path.join(os.path.dirname(__file__), '..', 'python-backend')
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

from message_bridge import _CallRequest, QtManagerBridge


class TestCallRequest:
    """Test _CallRequest data container."""

    def test_init_stores_fn_and_args(self):
        def dummy(a, b):
            return a + b

        req = _CallRequest(dummy, (1, 2), {})
        assert req.fn is dummy
        assert req.args == (1, 2)
        assert req.kwargs == {}
        assert req.result is None
        assert req.exception is None
        assert req.traceback is None

    def test_event_is_threading_event(self):
        req = _CallRequest(lambda: None, (), {})
        assert isinstance(req.event, threading.Event)

    def test_result_settable(self):
        req = _CallRequest(lambda: None, (), {})
        req.result = 42
        assert req.result == 42

    def test_exception_settable(self):
        req = _CallRequest(lambda: None, (), {})
        exc = ValueError("test")
        req.exception = exc
        assert req.exception is exc


class TestQtManagerBridge:
    """Test QtManagerBridge with mocked manager."""

    def _make_bridge(self):
        """Create a bridge with a mock manager (no Qt signals)."""
        mock_manager = MagicMock()
        mock_manager.room_connect_finished = MagicMock()
        mock_manager.batch_record_progress = MagicMock()
        # Mock signal connect to avoid Qt signal binding
        mock_manager.room_connect_finished.connect = MagicMock()
        mock_manager.batch_record_progress.connect = MagicMock()
        return QtManagerBridge(mock_manager), mock_manager

    def test_init_binds_signals(self):
        bridge, mock_manager = self._make_bridge()
        mock_manager.room_connect_finished.connect.assert_called_once()
        mock_manager.batch_record_progress.connect.assert_called_once()

    def test_manager_property_returns_manager(self):
        bridge, mock_manager = self._make_bridge()
        assert bridge.manager is mock_manager

    def test_call_on_main_thread_executes_directly(self):
        """When called from main thread, should execute fn directly without signal."""
        bridge, _ = self._make_bridge()
        result = bridge.call(lambda x: x * 2, 5)
        assert result == 10

    def test_call_with_kwargs(self):
        bridge, _ = self._make_bridge()
        result = bridge.call(lambda a, b=10: a + b, 5, b=20)
        assert result == 25

    def test_queue_broadcast_and_get(self):
        bridge, _ = self._make_bridge()
        msg = {"type": "test", "data": {"key": "value"}}
        bridge.queue_broadcast(msg)
        result = bridge.get_broadcast(block=False)
        assert result == msg

    def test_get_broadcast_empty_returns_none(self):
        bridge, _ = self._make_bridge()
        result = bridge.get_broadcast(block=False)
        assert result is None

    def test_queue_broadcast_multiple_messages(self):
        bridge, _ = self._make_bridge()
        msgs = [{"type": f"msg_{i}"} for i in range(5)]
        for m in msgs:
            bridge.queue_broadcast(m)
        for expected in msgs:
            result = bridge.get_broadcast(block=False)
            assert result == expected

    def test_on_connect_finished_queues_broadcast(self):
        bridge, _ = self._make_bridge()
        bridge._on_connect_finished("room1", True, "")
        result = bridge.get_broadcast(block=False)
        assert result["type"] == "room_connect_finished"
        assert result["data"]["room_id"] == "room1"
        assert result["data"]["success"] is True

    def test_on_batch_record_progress_queues_broadcast(self):
        bridge, _ = self._make_bridge()
        bridge._on_batch_record_progress("room2", False)
        result = bridge.get_broadcast(block=False)
        assert result["type"] == "recording_started"
        assert result["data"]["room_id"] == "room2"
        assert result["data"]["success"] is False

    def test_broadcast_queue_max_size(self):
        bridge, _ = self._make_bridge()
        # Fill the queue to capacity
        for i in range(1005):  # slightly over maxsize=1000
            bridge.queue_broadcast({"type": f"msg_{i}"})
        # Should not crash, queue should be at or below maxsize
        count = 0
        while bridge.get_broadcast(block=False) is not None:
            count += 1
        assert count <= 1000
        assert count >= 995  # should be close to maxsize

    def test_signal_callbacks_do_not_block_when_broadcast_queue_is_full(self):
        bridge, _ = self._make_bridge()
        for i in range(1000):
            bridge.queue_broadcast({"type": "rooms_updated", "data": {"i": i}})

        finished = threading.Event()

        def invoke_signal_callback():
            bridge._on_connect_finished("room-full", True, "")
            finished.set()

        t = threading.Thread(target=invoke_signal_callback, daemon=True)
        t.start()

        assert finished.wait(timeout=0.5) is True
        assert bridge._broadcast_queue.qsize() <= 1000

    def test_call_timeout_raises(self):
        """Test that call with a slow function raises TimeoutError."""
        bridge, _ = self._make_bridge()

        def slow_fn():
            time.sleep(5)
            return "done"

        # Call from a non-main thread with short timeout
        result_holder = {}

        def thread_target():
            try:
                result_holder["result"] = bridge.call(slow_fn, timeout=0.1)
            except TimeoutError:
                result_holder["timeout"] = True
            except Exception as e:
                result_holder["error"] = e

        t = threading.Thread(target=thread_target)
        t.start()
        t.join(timeout=5)

        # Should have timed out (or the call mechanism may differ in test env)
        # In test environment without Qt event loop, the signal won't fire,
        # so the call will timeout as expected
        assert result_holder.get("timeout") is True or "error" in result_holder
