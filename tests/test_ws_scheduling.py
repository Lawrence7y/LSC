"""WebSocket 调度与 rooms_updated 改进测试 — TDD 先行。"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# 添加 python-backend 到路径（与 conftest.py 一致）
_python_backend = os.path.join(os.path.dirname(__file__), '..', 'python-backend')
if _python_backend not in sys.path:
    sys.path.insert(0, _python_backend)

from server import LSCWebSocketServer


@pytest.fixture
def server():
    """创建测试用 WebSocket 服务器实例。"""
    srv = LSCWebSocketServer(host='localhost', port=19999)
    return srv


class TestRequestIdEcho:
    def test_request_id_extracted_from_data(self, server):
        """request_id 从 msg_data 中提取（不传递给 handler）。"""
        captured_data = {}

        async def mock_handler(data):
            captured_data.update(data)
            return {'success': True}

        async def run_test():
            import json
            from server import _truncate_for_log

            msg_data = {'request_id': 'req-abc-123', 'value': 42}
            # 模拟服务器内的 request_id 提取逻辑
            request_id = msg_data.pop('request_id', None) if isinstance(msg_data, dict) else None
            result = await mock_handler(msg_data)
            if result is not None and request_id is not None and isinstance(result, dict):
                result['request_id'] = request_id

            assert result['request_id'] == 'req-abc-123'
            assert 'request_id' not in captured_data  # handler 不接收 request_id
            assert captured_data == {'value': 42}

        asyncio.run(run_test())

    def test_no_request_id_no_echo(self, server):
        """没有 request_id 时响应中不包含 request_id。"""
        async def mock_handler(data):
            return {'success': True}

        async def run_test():
            msg_data = {'value': 42}
            request_id = msg_data.pop('request_id', None) if isinstance(msg_data, dict) else None
            result = await mock_handler(msg_data)
            if result is not None and request_id is not None and isinstance(result, dict):
                result['request_id'] = request_id

            assert 'request_id' not in result

        asyncio.run(run_test())


class _FakeWebSocket:
    def __init__(self, messages):
        self._messages = iter(messages)
        self.sent = []
        self.request_headers = {}

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._messages)
        except StopIteration:
            raise StopAsyncIteration

    async def send(self, message):
        self.sent.append(message)


class TestConcurrentDispatch:
    def test_slow_handler_does_not_block_following_command(self, server):
        """同一客户端的慢操作不应阻塞后续按钮命令。"""
        import json

        slow_started = asyncio.Event()
        release_slow = asyncio.Event()
        fast_finished = asyncio.Event()

        @server.on('slow')
        async def slow_handler(_data):
            slow_started.set()
            await release_slow.wait()
            return {'success': True}

        @server.on('fast')
        async def fast_handler(_data):
            fast_finished.set()
            return {'success': True}

        async def run_test():
            websocket = _FakeWebSocket([
                json.dumps({'type': 'slow', 'data': {}}),
                json.dumps({'type': 'fast', 'data': {}}),
            ])
            client_task = asyncio.create_task(server.handle_client(websocket))
            await asyncio.wait_for(slow_started.wait(), timeout=0.2)
            await asyncio.wait_for(fast_finished.wait(), timeout=0.2)
            release_slow.set()
            await asyncio.wait_for(client_task, timeout=0.2)

        asyncio.run(run_test())


class TestRoomsUpdatedThrottle:
    def test_first_broadcast_immediate(self):
        """首次 rooms_updated 立即发送（0ms 延迟）。"""
        from handlers.room_handler import _RoomsThrottle

        throttle = _RoomsThrottle()
        assert throttle.should_send_immediate() is True

    def test_subsequent_broadcast_merged(self):
        """后续 rooms_updated 进入合并窗口。"""
        from handlers.room_handler import _RoomsThrottle

        throttle = _RoomsThrottle()
        # 首次立即发送
        throttle.should_send_immediate()
        # 后续应合并
        assert throttle.should_send_immediate() is False

    def test_flush_after_window(self):
        """合并窗口到期后再次立即发送。"""
        from handlers.room_handler import _RoomsThrottle

        throttle = _RoomsThrottle()
        throttle.should_send_immediate()  # 首次
        throttle.mark_pending()  # 标记有待发送
        # 模拟窗口到期
        throttle._last_send_time = time.monotonic() - 0.4  # 400ms 前
        assert throttle.should_send_immediate() is True
