import asyncio
import os
import sys
import threading

# 添加项目根目录和本目录到 Python 路径
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_HERE = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from PySide6.QtWidgets import QApplication

from server import server
from message_bridge import QtManagerBridge
from lsc.gui.multi_room.manager import MultiRoomManager
from handlers.room_handler import register_room_handlers


def main():
    print("Starting LSC WebSocket server...")

    # MultiRoomManager 是 Qt 对象，需要 QApplication 与 Qt 事件循环。
    # 初始化顺序：QApplication -> manager -> bridge
    # 注意：MultiRoomManager 没有 set_bridge 方法，bridge 通过构造函数接收 manager 引用
    app = QApplication.instance() or QApplication(sys.argv)
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
