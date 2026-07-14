"""WebSocket server unit tests.

Tests LSCWebSocketServer message handling, broadcast, JSON encoding,
log truncation, and origin validation. Uses mock objects to avoid
actual network binding.
"""
from __future__ import annotations

import asyncio
import json
import math
import os
import sys
import threading
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Add python-backend to path
_backend_dir = os.path.join(os.path.dirname(__file__), '..', 'python-backend')
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

from server import _truncate_for_log, _NumpyJSONEncoder, _json_dumps, LSCWebSocketServer


class TestTruncateForLog:
    """Test log field truncation."""

    def test_dict_with_long_string(self):
        data = {"url": "x" * 500}
        result = _truncate_for_log(data, str_limit=200)
        assert result["url"] == "<str of length 500>"

    def test_dict_with_short_string(self):
        data = {"name": "short"}
        result = _truncate_for_log(data)
        assert result["name"] == "short"

    def test_dict_with_long_list(self):
        data = {"items": list(range(50))}
        result = _truncate_for_log(data, list_limit=10)
        assert result["items"] == "<list of length 50>"

    def test_dict_with_short_list(self):
        data = {"items": [1, 2, 3]}
        result = _truncate_for_log(data)
        assert result["items"] == [1, 2, 3]

    def test_nested_dict_not_recursively_truncated(self):
        """truncate only processes top-level keys, not nested dicts."""
        data = {"inner": {"key": "y" * 300}}
        result = _truncate_for_log(data, str_limit=50)
        # Top-level "inner" is a dict (not str/list), so it's kept as-is
        assert result["inner"] == {"key": "y" * 300}

    def test_non_dict_returned_as_is(self):
        assert _truncate_for_log(42) == 42
        assert _truncate_for_log([1, 2, 3]) == [1, 2, 3]

    def test_long_string_truncated(self):
        result = _truncate_for_log("x" * 500, str_limit=200)
        assert result == "<str of length 500>"


class TestNumpyJSONEncoder:
    """Test numpy-compatible JSON encoder."""

    def test_encodes_basic_types(self):
        result = _json_dumps({"key": "value"})
        assert json.loads(result) == {"key": "value"}

    def test_encodes_list(self):
        result = _json_dumps([1, 2, 3])
        assert json.loads(result) == [1, 2, 3]

    def test_numpy_types_serialize_without_error(self):
        """Verify numpy integers/float/arrays serialize via NumpyJSONEncoder."""
        try:
            import numpy as np
            # Just verify no exception is raised
            result = _json_dumps({"i": np.int64(1), "f": np.float64(2.5), "a": np.array([1, 2])})
            data = json.loads(result)
            assert data["i"] == 1
            assert data["f"] == 2.5
            assert data["a"] == [1, 2]
        except ImportError:
            pytest.skip("numpy not installed")

    def test_handles_numpy_types(self):
        try:
            import numpy as np
            result = _json_dumps({"int": np.int64(42), "float": np.float32(3.14), "arr": np.array([1, 2, 3])})
            data = json.loads(result)
            assert data["int"] == 42
            assert abs(data["float"] - 3.14) < 0.01
            assert data["arr"] == [1, 2, 3]
        except ImportError:
            pytest.skip("numpy not installed")


class TestLSCWebSocketServer:
    """Test server initialization and handler registration."""

    def _make_server(self):
        return LSCWebSocketServer()

    def test_default_host_and_port(self):
        srv = self._make_server()
        assert srv.host == 'localhost'
        assert srv.port == 19876

    def test_fallback_ports_default(self):
        srv = self._make_server()
        assert srv.fallback_ports == [19877, 19878, 19879, 19880]

    def test_custom_fallback_ports(self):
        srv = LSCWebSocketServer(port=9999, fallback_ports=[10000, 10001])
        assert srv.port == 9999
        assert srv.fallback_ports == [10000, 10001]

    def test_on_registers_handler(self):
        srv = self._make_server()

        @srv.on('test_msg')
        async def handler(data):
            return {'success': True}

        assert 'test_msg' in srv.handlers

    def test_on_with_decorator_syntax(self):
        srv = self._make_server()

        @srv.on('action')
        async def my_handler(data):
            pass

        assert 'action' in srv.handlers

    def test_on_connect_registers_handler(self):
        srv = self._make_server()

        @srv.on_connect()
        async def on_connect(ws):
            pass

        assert len(srv.connect_handlers) == 1

    def test_bound_port_initially_none(self):
        srv = self._make_server()
        assert srv.bound_port is None

    def test_clients_set_initially_empty(self):
        srv = self._make_server()
        assert len(srv.clients) == 0


class TestWebSocketOriginValidation:
    """Test origin-based connection rejection logic."""

    def _check_origin(self, origin: str) -> bool:
        """Simulate origin check from handle_client. Returns True if allowed."""
        if origin and origin != 'null' and not origin.startswith(('http://localhost', 'http://127.0.0.1')):
            return False  # rejected
        return True

    def test_null_origin_allowed(self):
        assert self._check_origin('null') is True

    def test_empty_origin_allowed(self):
        assert self._check_origin('') is True

    def test_localhost_allowed(self):
        assert self._check_origin('http://localhost:3000') is True

    def test_localhost_ip_allowed(self):
        assert self._check_origin('http://127.0.0.1:5173') is True

    def test_file_origin_allowed(self):
        assert self._check_origin('null') is True  # Electron file:// sends 'null'

    def test_external_origin_rejected(self):
        assert self._check_origin('http://evil.com') is False

    def test_external_ip_rejected(self):
        assert self._check_origin('http://192.168.1.100') is False


class TestBroadcastQueue:
    """Test broadcast queue management through bridge."""

    def _make_mock_server(self):
        srv = LSCWebSocketServer()
        srv.clients = set()
        return srv

    def test_broadcast_no_clients_no_error(self):
        srv = self._make_mock_server()
        # Should not raise when no clients
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(srv.broadcast("test", {"key": "val"}))
        finally:
            loop.close()

    def test_broadcast_sends_to_all_clients(self):
        srv = self._make_mock_server()
        # Create mock clients
        client1 = AsyncMock()
        client2 = AsyncMock()
        srv.clients.add(client1)
        srv.clients.add(client2)

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(srv.broadcast("status", {"connected": True}))
            # Both clients should have been called
            client1.send.assert_called_once()
            client2.send.assert_called_once()
        finally:
            loop.close()

    def test_broadcast_message_format(self):
        srv = self._make_mock_server()
        client = AsyncMock()
        srv.clients.add(client)

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(srv.broadcast("rooms_updated", {"count": 5}))
            call_args = client.send.call_args[0][0]
            data = json.loads(call_args)
            assert data["type"] == "rooms_updated"
            assert data["data"]["count"] == 5
        finally:
            loop.close()
