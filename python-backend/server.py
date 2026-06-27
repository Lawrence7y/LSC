import asyncio
import json
import traceback
import websockets
from typing import Dict, Any, Callable

class LSCWebSocketServer:
    def __init__(self, host: str = 'localhost', port: int = 9876, fallback_ports: list[int] | None = None):
        self.host = host
        self.port = port
        self.fallback_ports = fallback_ports or [9877, 9878, 9879, 9880]
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
        print(f"Client connected. Total: {len(self.clients)}")

        for handler in self.connect_handlers:
            try:
                await handler(websocket)
            except Exception as exc:
                print(f"Connect handler error: {exc}")

        try:
            async for message in websocket:
                msg_type = None
                try:
                    data = json.loads(message)
                    msg_type = data.get('type')
                    msg_data = data.get('data', {})

                    # 调用对应的处理器
                    if msg_type in self.handlers:
                        result = await self.handlers[msg_type](msg_data)
                        if result is not None:
                            await websocket.send(json.dumps({
                                'type': f'{msg_type}_response',
                                'data': result
                            }))
                    else:
                        print(f"Unknown message type: {msg_type}")

                except json.JSONDecodeError:
                    print(f"Invalid JSON: {message}")
                except Exception as e:
                    # 处理器抛异常时打印完整 traceback，并向客户端发送错误响应，避免前端永久等待
                    print(f"Error handling message: {e}")
                    print(traceback.format_exc())
                    if msg_type is not None:
                        try:
                            await websocket.send(json.dumps({
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
            print(f"Client disconnected. Total: {len(self.clients)}")
    
    async def broadcast(self, message_type: str, data: Any):
        """广播消息给所有客户端"""
        if not self.clients:
            return
            
        message = json.dumps({
            'type': message_type,
            'data': data
        })
        
        await asyncio.gather(
            *[client.send(message) for client in self.clients],
            return_exceptions=True,
        )
    
    async def start(self):
        """启动服务器，支持端口回退（主端口被占用时尝试备用端口）。"""
        ports_to_try = [self.port] + [p for p in self.fallback_ports if p != self.port]
        last_error = None

        for port in ports_to_try:
            try:
                if port != self.port:
                    print(f"Port {self.port} unavailable, trying fallback port {port}...")
                async with websockets.serve(self.handle_client, self.host, port, max_size=16 * 1024 * 1024) as srv:
                    self._server = srv
                    self._bound_port = port
                    print(f"WebSocket server listening on ws://{self.host}:{port}")
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
    from PySide6.QtWidgets import QApplication
    from message_bridge import QtManagerBridge
    from lsc.gui.multi_room.manager import MultiRoomManager
    from handlers.room_handler import register_room_handlers

    # 初始化顺序：QApplication -> manager -> bridge
    # 注意：MultiRoomManager 没有 set_bridge 方法，bridge 通过构造函数接收 manager 引用
    app = QApplication.instance() or QApplication([])
    manager = MultiRoomManager()
    bridge = QtManagerBridge(manager)

    loop = asyncio.new_event_loop()

    async def _drain_broadcasts():
        """从 bridge 队列消费广播消息并推送给 WebSocket 客户端。"""
        while True:
            msg = bridge.get_broadcast(block=False)
            if msg is None:
                await asyncio.sleep(0.1)
                continue
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
