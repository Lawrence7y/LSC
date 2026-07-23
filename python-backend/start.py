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

from broadcast_hub import BroadcastHub
from handlers.room_handler import register_room_handlers
from server import server

from lsc.core.orchestrator import RoomOrchestrator


def main():
    _log.info("Starting LSC WebSocket server...")

    # 初始化顺序：RoomOrchestrator -> BroadcastHub
    manager = RoomOrchestrator()
    manager.start()
    bridge = BroadcastHub(manager)

    loop = asyncio.new_event_loop()
    stop_event = threading.Event()

    async def _drain_broadcasts():
        """从 bridge 队列消费广播消息并推送给 WebSocket 客户端（事件驱动）。"""
        from server import drain_merge_broadcasts
        wake = asyncio.Event()
        bridge.bind_async_wake(loop, wake)
        while True:
            merged = drain_merge_broadcasts(bridge)
            if not merged:
                wake.clear()
                try:
                    await asyncio.wait_for(wake.wait(), timeout=0.5)
                except asyncio.TimeoutError:
                    pass
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
        stop_event.wait()
    except KeyboardInterrupt:
        _log.info("Shutting down...")
    finally:
        try:
            manager.shutdown(timeout_sec=10.0)
        except Exception as exc:
            _log.warning("manager shutdown failed: %s", exc)
        try:
            loop.call_soon_threadsafe(loop.stop)
        except Exception as exc:
            _log.debug("loop stop failed: %s", exc)


if __name__ == '__main__':
    main()
