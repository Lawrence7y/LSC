import asyncio
import os
import sys
import threading
import time

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
    # 第三引导入口原先零测试覆盖且缺五项基建：无 logging 配置（INFO 全部
    # 不可见）、无 excepthook、无端口 stdout 打印（前端靠正则匹配拿端口，
    # 该入口实际监听 server.py 默认 19876，前端只能拿 9876 兜底 ⇒ 直连失败）、
    # 广播协程无异常恢复（一条异常即永久停播）。2026-09-24 向 main 入口收敛。
    from main import _install_exception_hook, _setup_logging

    log = _setup_logging()
    _install_exception_hook(log)
    log.info("Starting LSC WebSocket server (start.py entry)...")

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
            try:
                merged = drain_merge_broadcasts(bridge)
                if not merged:
                    wake.clear()
                    try:
                        await asyncio.wait_for(wake.wait(), timeout=0.5)
                    except asyncio.TimeoutError:
                        pass
                    continue
                for msg in merged:
                    # _seq/_coalesced 透传：§2.1 缺号告警此前因只转发 type/data
                    # 而整体空转（顶层序列号在广播协程被丢弃）。
                    await server.broadcast(
                        msg.get('type'),
                        msg.get('data', {}),
                        seq=msg.get('_seq'),
                        coalesced=msg.get('_coalesced'),
                    )
            except Exception:
                # 广播循环不得因单条意外异常永久停播（与 main.py 主入口同款兜底）
                log.exception("broadcast error, retrying in 1s")
                await asyncio.sleep(1)

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
        except Exception:
            # loop.stop() 会在 run_until_complete 上抛 RuntimeError：留痕而非
            # 让线程带 traceback 静默死亡
            log.exception("ws thread event loop stopped unexpectedly")

    ws_thread = threading.Thread(target=_run_ws, daemon=True)
    ws_thread.start()

    # 等待端口绑定后把端口打到 stdout：Electron 主进程靠正则匹配这行取端口
    # （缺它时前端只能拿 9876 兜底，而本入口默认监听 19876）
    for _ in range(50):
        if server._server is not None:
            port = server._bound_port or server.port
            log.info("WebSocket server ready at ws://127.0.0.1:%s", port)
            print(f"WebSocket server ready at ws://127.0.0.1:{port}", flush=True)
            break
        time.sleep(0.1)

    try:
        stop_event.wait()
    except KeyboardInterrupt:
        log.info("Shutting down...")
    finally:
        try:
            manager.shutdown(timeout_sec=10.0)
        except Exception as exc:
            log.warning("manager shutdown failed: %s", exc)
        # 与 main.py.stop 同口径：1s 去抖窗口里未落盘的 rooms.json 必须冲刷
        try:
            from persistence import flush_pending_room_saves

            if not flush_pending_room_saves(fsync=True):
                log.error("房间配置最终落盘失败")
        except Exception as exc:
            log.error("房间配置最终落盘异常: %s", exc, exc_info=True)
        try:
            loop.call_soon_threadsafe(loop.stop)
        except Exception as exc:
            log.debug("loop stop failed: %s", exc)


if __name__ == '__main__':
    main()
