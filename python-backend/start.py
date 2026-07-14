import asyncio
import logging
import os
import sys
import threading

_log = logging.getLogger('lsc.backend')

# 添加项目根目录和本目录到 Python 路径
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_HERE = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from handlers.room_handler import register_room_handlers
from message_bridge import QtManagerBridge
from PySide6.QtWidgets import QApplication
from server import server

from lsc.gui.multi_room.manager import MultiRoomManager


def main():
    _log.info("Starting LSC WebSocket server...")

    # MultiRoomManager 是 Qt 对象，需要 QApplication 与 Qt 事件循环。
    # 初始化顺序：QApplication -> manager -> bridge
    # 注意：MultiRoomManager 没有 set_bridge 方法，bridge 通过构造函数接收 manager 引用
    app = QApplication.instance() or QApplication(sys.argv)
    manager = MultiRoomManager()
    bridge = QtManagerBridge(manager)

    loop = asyncio.new_event_loop()

    async def _drain_broadcasts():
        """从 bridge 队列消费广播消息并推送给 WebSocket 客户端。"""
        from server import drain_merge_broadcasts
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
        _log.info("Shutting down...")
    finally:
        loop.call_soon_threadsafe(loop.stop)


if __name__ == '__main__':
    main()
