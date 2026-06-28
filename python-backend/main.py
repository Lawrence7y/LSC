"""LSC Electron 后端入口。

同时启动：
- Qt 事件循环（运行 MultiRoomManager）
- WebSocket 服务器（运行在工作线程，与前端通信）

全局异常处理（项目记忆硬约束）：
- sys.excepthook 捕获未处理异常
- RotatingFileHandler 滚动文件日志
- qInstallMessageHandler 捕获 Qt 警告/错误
"""
from __future__ import annotations

import asyncio
import json
import logging
import logging.handlers
import os
import sys
import threading
import time
import traceback

# 路径设置
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_HERE = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)


def _get_log_dir() -> str:
    """日志目录：优先 userData/logs（Electron 提供），回退到 ~/.lsc/LiveStreamClipper/logs。"""
    candidates = [
        os.environ.get('LSC_LOG_DIR'),
        os.path.join(os.path.expanduser('~'), '.lsc', 'LiveStreamClipper', 'logs'),
        os.path.join(_HERE, 'logs'),
    ]
    for d in candidates:
        if d:
            try:
                os.makedirs(d, exist_ok=True)
                if os.access(d, os.W_OK):
                    return d
            except OSError:
                continue
    return os.path.join(_HERE, 'logs')


def _setup_logging() -> logging.Logger:
    """配置根 logger：控制台 + 滚动文件日志（2MB × 5）。"""
    log_dir = _get_log_dir()
    log_file = os.path.join(log_dir, 'backend.log')

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    # 清理可能存在的 handlers（避免重复添加）
    root.handlers.clear()

    fmt = logging.Formatter(
        '%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    )

    # 控制台输出（Electron 会捕获 stdout/stderr 写入 userData/logs/backend.log）
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root.addHandler(console)

    # 滚动文件日志：单文件 2MB，保留 5 个备份
    try:
        file_handler = logging.handlers.RotatingFileHandler(
            log_file, maxBytes=2 * 1024 * 1024, backupCount=5, encoding='utf-8',
        )
        file_handler.setFormatter(fmt)
        root.addHandler(file_handler)
    except OSError as exc:
        # 日志目录不可写时仅用控制台
        print(f"[warn] failed to create file log handler at {log_file}: {exc}", file=sys.stderr)

    return logging.getLogger('lsc.backend')


def _install_exception_hook(log: logging.Logger) -> None:
    """安装 sys.excepthook，将未捕获异常写入日志文件而非仅控制台。"""
    def _hook(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        log.error("Unhandled exception: %s", ''.join(traceback.format_exception(exc_type, exc_value, exc_tb)))
        # 同步打到 stderr 供 Electron 捕获
        traceback.print_exception(exc_type, exc_value, exc_tb, file=sys.stderr)
    sys.excepthook = _hook


def _install_qt_message_handler(log: logging.Logger) -> None:
    """安装 qInstallMessageHandler，将 Qt 警告/错误转入 Python logging。"""
    try:
        from PySide6.QtCore import qInstallMessageHandler, QtMsgType, QtMessageHandler
    except ImportError:
        log.warning("PySide6.QtCore not available, skipping Qt message handler")
        return

    def _handler(msg_type, context, message):
        level_map = {
            QtMsgType.QtDebugMsg: logging.DEBUG,
            QtMsgType.QtInfoMsg: logging.INFO,
            QtMsgType.QtWarningMsg: logging.WARNING,
            QtMsgType.QtCriticalMsg: logging.ERROR,
            QtMsgType.QtFatalMsg: logging.CRITICAL,
        }
        level = level_map.get(msg_type, logging.INFO)
        # Qt fatal 默认会让进程 abort，这里仅记录日志，不调用 abort
        log.log(level, "[Qt] %s", message)

    try:
        qInstallMessageHandler(_handler)
    except Exception as exc:
        log.warning("Failed to install Qt message handler: %s", exc)


_log = _setup_logging()
_install_exception_hook(_log)
# Qt message handler 需要 QCoreApplication 存在才能完整生效，但安装本身可以提前。
_install_qt_message_handler(_log)


from PySide6.QtWidgets import QApplication

from server import LSCWebSocketServer
from message_bridge import QtManagerBridge
from lsc.gui.multi_room.manager import MultiRoomManager


class LSCWebSocketBackend:
    def __init__(self):
        self.app = QApplication(sys.argv)
        self.manager = MultiRoomManager()
        self.bridge = QtManagerBridge(self.manager)
        # 注意：MultiRoomManager 没有 set_bridge 方法。
        # bridge 通过构造函数接收 manager 引用，反向注册非必需。
        self.server = LSCWebSocketServer(port=9876)
        self._ws_thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._shutdown = False
        # 用于解除 server.start() 中 `await asyncio.Future()` 的阻塞。
        # stop() 时 set 此 event，run_until_complete 会正常返回。
        self._stop_event: asyncio.Event | None = None

    def _run_ws_server(self):
        """在工作线程中运行 WebSocket 服务器。"""
        from handlers.room_handler import register_room_handlers

        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._stop_event = asyncio.Event()

        register_room_handlers(self.server, self.bridge)

        # 启动广播推送任务
        broadcaster = self._loop.create_task(self._broadcast_coroutine())

        async def _serve():
            """包装 server.start()，附加 stop_event 等待，使其可被停止。"""
            serve_task = self._loop.create_task(self.server.start())
            stop_task = self._loop.create_task(self._stop_event.wait())
            try:
                # 哪个先完成都触发停止：server.start 异常退出 或 stop_event 被 set
                await asyncio.wait({serve_task, stop_task}, return_when=asyncio.FIRST_COMPLETED)
            finally:
                # 取消未完成的任务
                for t in (serve_task, stop_task):
                    if not t.done():
                        t.cancel()
                        try:
                            await t
                        except (asyncio.CancelledError, Exception):
                            pass

        try:
            self._loop.run_until_complete(_serve())
        except asyncio.CancelledError:
            pass
        except Exception:
            _log.exception("WebSocket server thread crashed")
        finally:
            broadcaster.cancel()
            try:
                # 给 broadcaster 一点时间清理
                self._loop.run_until_complete(asyncio.gather(broadcaster, return_exceptions=True))
            except Exception:
                pass
            self._loop.close()
            _log.info("WebSocket server thread exited")

    async def _broadcast_coroutine(self):
        """协程版广播循环：从 bridge 队列取消息并发送。

        合并连续的 rooms_updated 消息：多房间同时变更状态时，
        Qt 信号会快速触发多次 _queue_rooms_update，每次都序列化全部房间。
        合并为只发送最新的一条，减少前端 JSON.parse 负载。
        """
        while not self._shutdown:
            msg = self.bridge.get_broadcast(block=False)
            if msg is None:
                await asyncio.sleep(0.1)
                continue
            msg_type = msg.get('type')
            if msg_type == 'rooms_updated':
                while True:
                    next_msg = self.bridge.get_broadcast(block=False)
                    if next_msg is None:
                        break
                    if next_msg.get('type') != 'rooms_updated':
                        data = json.dumps(msg)
                        clients = list(self.server.clients)
                        if clients:
                            await asyncio.gather(
                                *[client.send(data) for client in clients],
                                return_exceptions=True,
                            )
                        msg = next_msg
                        break
                    msg = next_msg
            data = json.dumps(msg)
            clients = list(self.server.clients)
            if not clients:
                await asyncio.sleep(0.1)
                continue
            await asyncio.gather(
                *[client.send(data) for client in clients],
                return_exceptions=True,
            )

    def start(self):
        _log.info("Starting LSC Electron backend...")

        self._ws_thread = threading.Thread(target=self._run_ws_server, daemon=True)
        self._ws_thread.start()

        # 等待 WebSocket 线程完成端口绑定（最多 5 秒）
        for _ in range(50):
            if self.server._server is not None:
                port = self.server._bound_port or self.server.port
                _log.info("WebSocket server ready at ws://localhost:%s", port)
                # 同时打到 stdout 供 Electron 主进程正则匹配
                print(f"WebSocket server ready at ws://localhost:{port}", flush=True)
                break
            time.sleep(0.1)

        self.app.exec()

    def stop(self):
        """优雅停止后端。

        通过 set _stop_event 解除 server.start() 中 `await asyncio.Future()`
        的阻塞（旧实现仅设 _shutdown 标志但未调度 loop.stop，导致 ws 线程
        超时被强杀）。
        """
        self._shutdown = True
        # 1) 通知 WebSocket 服务器停止接受新连接
        if self.server._server is not None and self._loop is not None and not self._loop.is_closed():
            try:
                asyncio.run_coroutine_threadsafe(self.server._server.close(), self._loop)
            except RuntimeError:
                pass
        # 2) set stop_event 让 _serve() 的 await 返回，run_until_complete 正常退出
        if self._stop_event is not None and self._loop is not None and not self._loop.is_closed():
            try:
                self._loop.call_soon_threadsafe(self._stop_event.set)
            except RuntimeError:
                pass
        # 3) 退出 Qt 事件循环
        try:
            self.app.quit()
        except Exception:
            pass
        # 4) 等待 ws 线程结束（最多 3 秒）
        if self._ws_thread is not None:
            self._ws_thread.join(timeout=3.0)
        _log.info("LSC Electron backend stopped")


def main():
    backend = LSCWebSocketBackend()
    try:
        backend.start()
    except KeyboardInterrupt:
        _log.info("Shutting down (KeyboardInterrupt)...")
    except Exception:
        _log.exception("Backend crashed")
        raise
    finally:
        backend.stop()


if __name__ == '__main__':
    main()
