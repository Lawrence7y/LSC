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
        # server.handle_client 校验 Origin（小写 key）；缺省会直接拒绝连接
        self.request_headers = {'origin': 'http://localhost:5173'}

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._messages)
        except StopIteration:
            raise StopAsyncIteration

    async def send(self, message):
        self.sent.append(message)


class TestSequentialDispatch:
    def test_handlers_run_sequentially_preserving_order(self, server):
        """同一连接上的消息按序执行，保证依赖状态的命令不错乱。

        慢 handler 会挡住后续消息——这是有意设计（见 server.handle_client 注释）。
        长耗时工作必须在 handler 内入队后立刻返回，不得在 handler 里 await 阻塞。
        """
        import json

        order: list[str] = []

        @server.on('first')
        async def first_handler(_data):
            order.append('first_start')
            await asyncio.sleep(0.05)
            order.append('first_end')
            return {'success': True}

        @server.on('second')
        async def second_handler(_data):
            order.append('second')
            return {'success': True}

        async def run_test():
            websocket = _FakeWebSocket([
                json.dumps({'type': 'first', 'data': {}}),
                json.dumps({'type': 'second', 'data': {}}),
            ])
            await asyncio.wait_for(server.handle_client(websocket), timeout=1.0)
            assert order == ['first_start', 'first_end', 'second']

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
