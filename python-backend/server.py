import asyncio
import json
import logging
import math
from collections.abc import Callable
from typing import Any

import websockets
from websockets.exceptions import ConnectionClosed

_log = logging.getLogger('lsc.server')


def _truncate_for_log(data: Any, str_limit: int = 200, list_limit: int = 10) -> Any:
    """截断超大日志字段，避免日志文件暴增。"""
    if isinstance(data, dict):
        result = {}
        for k, v in data.items():
            if isinstance(v, str) and len(v) > str_limit:
                result[k] = f"<str of length {len(v)}>"
            elif isinstance(v, list) and len(v) > list_limit:
                result[k] = f"<list of length {len(v)}>"
            else:
                result[k] = v
        return result
    if isinstance(data, str) and len(data) > str_limit:
        return f"<str of length {len(data)}>"
    return data


class _NumpyJSONEncoder(json.JSONEncoder):
    """自定义 JSON encoder，处理 numpy 数值类型。"""

    def default(self, obj):
        try:
            import numpy as np
            if isinstance(obj, np.integer):
                return int(obj)
            if isinstance(obj, np.floating):
                if math.isnan(obj) or math.isinf(obj):
                    return None
                return float(obj)
            if isinstance(obj, np.ndarray):
                return obj.tolist()
        except ImportError:
            pass
        return super().default(obj)


def _json_dumps(obj) -> str:
    """带 numpy 兼容的 JSON 序列化。"""
    return json.dumps(obj, cls=_NumpyJSONEncoder)


class LSCWebSocketServer:
    def __init__(self, host: str = 'localhost', port: int = 19876, fallback_ports: list[int] | None = None):
        self.host = host
        self.port = port
        self.fallback_ports = fallback_ports or [19877, 19878, 19879, 19880]
        self.clients: set = set()
        self.handlers: dict[str, Callable] = {}
        self.connect_handlers: list[Callable] = []
        self._server = None
        self._bound_port: int | None = None

    @property
    def bound_port(self) -> int | None:
        """返回实际绑定的端口（可能与配置的 port 不同，因端口回退）。"""
        return self._bound_port

    def on(self, message_type: str, handler: Callable | None = None):
        """注册消息处理器，支持装饰器用法：@server.on('type')"""
        def decorator(fn: Callable) -> Callable:
            self.handlers[message_type] = fn
            _log.debug("registered handler: %s -> %s", message_type, getattr(fn, '__name__', '?'))
            return fn

        if handler is None:
            return decorator
        return decorator(handler)

    def on_connect(self, handler: Callable | None = None):
        """注册客户端连接成功后的回调，支持装饰器用法。"""
        def decorator(fn: Callable) -> Callable:
            self.connect_handlers.append(fn)
            return fn

        if handler is None:
            return decorator
        return decorator(handler)

    async def handle_client(self, websocket):
        """处理客户端连接"""
        # S-4: Origin 校验 — 仅允许 Electron (file://) 和本地开发服务器
        origin = ''
        if hasattr(websocket, 'request_headers'):
            origin = websocket.request_headers.get('origin', '')
        if origin and origin != 'null' and not origin.startswith(('http://localhost', 'http://127.0.0.1')):
            _log.warning("Rejected WebSocket connection from origin: %s", origin)
            await websocket.close(code=1008, reason='Origin not allowed')
            return

        self.clients.add(websocket)
        _log.info(f"Client connected. Total: {len(self.clients)}")

        for handler in self.connect_handlers:
            try:
                await handler(websocket)
            except websockets.ConnectionClosed:
                _log.info("Client disconnected during connect handler")
                return
            except Exception as exc:
                _log.error(f"Connect handler error: {exc}", exc_info=True)

        pending: set[asyncio.Task] = set()

        async def dispatch(message: str):
            msg_type = None
            request_id = None
            try:
                data = json.loads(message)
                msg_type = data.get('type')
                msg_data = data.get('data', {})
                log_data = _truncate_for_log(msg_data)
                high_freq_types = frozenset({
                    'mse_segment', 'mse_init', 'rooms_updated',
                    'export_progress', 'medium_tick',
                })
                if msg_type in high_freq_types:
                    _log.debug("Received WS message: type=%s", msg_type)
                else:
                    _log.info("Received WS message: type=%s, data=%s", msg_type, log_data)

                handler = self.handlers.get(msg_type)
                if handler is None:
                    _log.warning("Unknown message type: %s", msg_type)
                    return
                request_id = msg_data.pop('request_id', None) if isinstance(msg_data, dict) else None
                result = await handler(msg_data)
                if result is None:
                    return
                if request_id is not None and isinstance(result, dict):
                    result['request_id'] = request_id
                if msg_type not in high_freq_types:
                    _log.info("Sending WS response: type=%s_response, data=%s", msg_type, _truncate_for_log(result))
                await websocket.send(_json_dumps({'type': f'{msg_type}_response', 'data': result}))
            except json.JSONDecodeError:
                _log.warning("Invalid JSON format received (truncated): %s", message[:500])
            except Exception as exc:
                _log.error("Error handling message: %s", exc, exc_info=True)
                if msg_type is not None:
                    try:
                        error_data: dict[str, Any] = {'success': False, 'error': str(exc)}
                        if request_id is not None:
                            error_data['request_id'] = request_id
                        await websocket.send(_json_dumps({
                            'type': f'{msg_type}_response',
                            'data': error_data,
                        }))
                    except Exception:
                        pass

        # Process messages sequentially per connection to guarantee in-order
        # handler execution. Previously each message spawned an independent
        # asyncio.create_task, so a later message (e.g. export_clip) could
        # complete before an earlier one (e.g. set_mark_in) whose state it
        # depends on. Handlers are non-blocking (long work is queued), so
        # serialization does not stall the connection.
        try:
            async for message in websocket:
                await dispatch(message)
        except ConnectionClosed:
            pass
        finally:
            # `pending` is kept for backward compatibility but is no longer
            # populated under sequential processing. If any stray tasks exist
            # (e.g. from connect handlers), bound the wait so a slow/stuck
            # task cannot block disconnect cleanup indefinitely.
            if pending:
                try:
                    await asyncio.wait_for(
                        asyncio.gather(*pending, return_exceptions=True),
                        timeout=3.0,
                    )
                except asyncio.TimeoutError:
                    for t in pending:
                        t.cancel()
            self.clients.discard(websocket)
            _log.info(f"Client disconnected. Total: {len(self.clients)}")

    async def broadcast(self, message_type: str, data: Any):
        """广播消息给所有客户端。

        高频消息（mse_segment/mse_init/rooms_updated/export_progress）不记录 INFO，
        避免日志文件被淹没。确保 INFO 中 MSE 分片记录为 0。
        """
        if not self.clients:
            return

        message = _json_dumps({
            'type': message_type,
            'data': data
        })

        await asyncio.gather(
            *[client.send(message) for client in self.clients],
            return_exceptions=True,
        )

        # 高频消息不记录 INFO，确保 INFO 中 MSE 记录为 0
        _HIGH_FREQ_BROADCASTS = frozenset({
            'mse_segment', 'mse_init', 'rooms_updated',
            'export_progress', 'medium_tick',
        })
        if message_type not in _HIGH_FREQ_BROADCASTS:
            log_data = _truncate_for_log(data)
            _log.debug(f"Broadcasted WS message: type={message_type}, data={log_data}")

    async def start(self):
        """启动服务器，支持端口回退（主端口被占用时尝试备用端口）。"""
        ports_to_try = [self.port] + [p for p in self.fallback_ports if p != self.port]
        last_error = None

        for port in ports_to_try:
            try:
                if port != self.port:
                    _log.warning(f"Port {self.port} unavailable, trying fallback port {port}...")
                async with websockets.serve(self.handle_client, self.host, port, max_size=16 * 1024 * 1024) as srv:
                    self._server = srv
                    self._bound_port = port
                    _log.info(f"WebSocket server listening on ws://{self.host}:{port}")
                    await asyncio.Future()  # 永远运行
                    return  # 正常退出时返回
            except OSError as e:
                last_error = e
                continue

        # 所有端口都失败
        raise RuntimeError(f"Failed to bind WebSocket server on any port ({ports_to_try}): {last_error}")


# 全局服务器实例
server = LSCWebSocketServer()


def drain_merge_broadcasts(bridge):
    """从 bridge 队列消费所有待发消息，按 type 做 last-value coalesce。

    同 type 的消息只保留最后一条（覆盖式合并），减少前端 JSON 序列化与
    React 重渲染负载。返回保序的消息列表，供 _broadcast_coroutine 发送。
    """
    messages: list[dict[str, Any]] = []
    while True:
        msg = bridge.get_broadcast(block=False)
        if msg is None:
            break
        messages.append(msg)
    coalesced: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for msg in messages:
        msg_type = msg.get('type', '')
        if msg_type not in coalesced:
            order.append(msg_type)
        coalesced[msg_type] = msg
    return [coalesced[t] for t in order]


def main():
    """独立入口：参考 main.py 的两线程模型启动后端。

    Qt 事件循环运行于主线程（MultiRoomManager 是 Qt 对象），
    WebSocket 服务器运行于工作线程，通过 QtManagerBridge 跨线程调用。
    """
    import threading

    from handlers.room_handler import register_room_handlers
    from message_bridge import QtManagerBridge
    from PySide6.QtWidgets import QApplication

    from lsc.gui.multi_room.manager import MultiRoomManager

    # 初始化顺序：QApplication -> manager -> bridge
    # 注意：MultiRoomManager 没有 set_bridge 方法，bridge 通过构造函数接收 manager 引用
    app = QApplication.instance() or QApplication([])
    manager = MultiRoomManager()
    bridge = QtManagerBridge(manager)

    loop = asyncio.new_event_loop()

    async def _drain_broadcasts():
        """从 bridge 队列消费广播消息并推送给 WebSocket 客户端。"""
        while True:
            merged = drain_merge_broadcasts(bridge)
            if not merged:
                await asyncio.sleep(0.1)
                continue
            for msg in merged:
                await server.broadcast(msg.get('type'), msg.get('data', {}))

    async def _start_export_queue():
        """启动全局导出队列 worker。"""
        from handlers.room_handler import _ensure_export_queue
        await _ensure_export_queue()

    def _run_ws():
        asyncio.set_event_loop(loop)
        register_room_handlers(server, bridge)
        loop.create_task(_drain_broadcasts())
        loop.create_task(_start_export_queue())
        try:
            loop.run_until_complete(server.start())
        except asyncio.CancelledError:
            pass

    ws_thread = threading.Thread(target=_run_ws, daemon=True)
    ws_thread.start()

    try:
        app.exec()
    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        loop.call_soon_threadsafe(loop.stop)


if __name__ == '__main__':
    main()
