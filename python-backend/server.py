import asyncio
import json
import logging
import math
from collections.abc import Callable
from typing import Any, Dict

import websockets

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
        self.handlers: Dict[str, Callable] = {}
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
        self.clients.add(websocket)
        _log.info(f"Client connected. Total: {len(self.clients)}")

        for handler in self.connect_handlers:
            try:
                await handler(websocket)
            except Exception as exc:
                _log.error(f"Connect handler error: {exc}", exc_info=True)

        try:
            async for message in websocket:
                msg_type = None
                try:
                    data = json.loads(message)
                    msg_type = data.get('type')
                    msg_data = data.get('data', {})

                    log_data = _truncate_for_log(msg_data)

                    # 高频消息降为 debug，避免日志文件被淹没
                    _HIGH_FREQ_TYPES = frozenset({
                        'mse_segment', 'mse_init', 'rooms_updated',
                        'export_progress', 'medium_tick',
                    })
                    if msg_type in _HIGH_FREQ_TYPES:
                        _log.debug(f"Received WS message: type={msg_type}")
                    else:
                        _log.info(f"Received WS message: type={msg_type}, data={log_data}")

                    # 调用对应的处理器
                    if msg_type in self.handlers:
                        result = await self.handlers[msg_type](msg_data)
                        if result is not None:
                            # 同样截断响应数据
                            log_res = _truncate_for_log(result)

                            if msg_type in _HIGH_FREQ_TYPES:
                                _log.debug(f"Sending WS response: type={msg_type}_response")
                            else:
                                _log.info(f"Sending WS response: type={msg_type}_response, data={log_res}")
                            await websocket.send(_json_dumps({
                                'type': f'{msg_type}_response',
                                'data': result
                            }))
                    else:
                        _log.warning(f"Unknown message type: {msg_type}")

                except json.JSONDecodeError:
                    _log.warning(f"Invalid JSON format received (truncated): {message[:500]}")
                except Exception as e:
                    # 处理器抛异常时打印完整 traceback，并向客户端发送错误响应，避免前端永久等待
                    _log.error(f"Error handling message: {e}", exc_info=True)
                    if msg_type is not None:
                        try:
                            await websocket.send(_json_dumps({
                                'type': f'{msg_type}_response',
                                'data': {'success': False, 'error': str(e)}
                            }))
                        except Exception:
                            # 客户端可能已断连，忽略发送失败
                            pass

        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            self.clients.remove(websocket)
            _log.info(f"Client disconnected. Total: {len(self.clients)}")

    async def broadcast(self, message_type: str, data: Any):
        """广播消息给所有客户端"""
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

        log_data = _truncate_for_log(data)
        _log.info(f"Broadcasted WS message: type={message_type}, data={log_data}")

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
        """从 bridge 队列消费广播消息并推送给 WebSocket 客户端。

        合并连续的 rooms_updated 消息：多房间同时变更状态时，
        Qt 信号会快速触发多次 _queue_rooms_update，每次都序列化全部房间。
        合并为只发送最新的一条，减少前端 JSON.parse 负载。
        """
        while True:
            msg = bridge.get_broadcast(block=False)
            if msg is None:
                await asyncio.sleep(0.1)
                continue
            if msg.get('type') == 'rooms_updated':
                while True:
                    next_msg = bridge.get_broadcast(block=False)
                    if next_msg is None:
                        break
                    if next_msg.get('type') != 'rooms_updated':
                        await server.broadcast(msg.get('type'), msg.get('data', {}))
                        msg = next_msg
                        break
                    msg = next_msg
            await server.broadcast(msg.get('type'), msg.get('data', {}))

    def _run_ws():
        asyncio.set_event_loop(loop)
        register_room_handlers(server, bridge)
        loop.create_task(_drain_broadcasts())
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
