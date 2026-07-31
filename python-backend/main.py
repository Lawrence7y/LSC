"""LSC Electron 后端入口。

同时启动：
- RoomOrchestrator（编排线程，无 Qt）
- WebSocket 服务器（运行在工作线程，与前端通信）

全局异常处理（项目记忆硬约束）：
- sys.excepthook 捕获未处理异常
- RotatingFileHandler 滚动文件日志
"""
from __future__ import annotations

import asyncio
import logging
import logging.handlers
import os
import shutil
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

# Windows embeddable Python 的 ._pth 会忽略 PYTHONPATH；显式加载安装器写入
# 当前用户目录的运行时依赖。
_RUNTIME_PACKAGES = os.environ.get('LSC_PYTHON_PACKAGES')
if _RUNTIME_PACKAGES and _RUNTIME_PACKAGES not in sys.path:
    sys.path.insert(0, _RUNTIME_PACKAGES)


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


class _CompressedRotatingFileHandler(logging.handlers.RotatingFileHandler):
    """带压缩的滚动文件日志处理器（P3-3: 日志压缩和清理）。

    轮转时将旧日志压缩为 .gz 格式，节省约 80% 磁盘空间。
    """

    def doRollover(self) -> None:
        """执行轮转并压缩旧日志"""
        import gzip
        super().doRollover()
        # 压缩轮转后的旧日志
        for i in range(self.backupCount, 0, -1):
            sfn = f"{self.baseFilename}.{i}"
            dfn = f"{self.baseFilename}.{i}.gz"
            if os.path.exists(sfn) and not os.path.exists(dfn):
                try:
                    with open(sfn, 'rb') as f_in, gzip.open(dfn, 'wb') as f_out:
                        shutil.copyfileobj(f_in, f_out)
                    os.remove(sfn)
                except OSError:
                    pass


def _setup_logging() -> logging.Logger:
    """配置根 logger：控制台 + 滚动文件日志（2MB × 5）。

    环境变量：
    - LSC_LOG_LEVEL: 设置根日志级别（DEBUG/INFO/WARNING/ERROR），默认 INFO
    - LSC_LOG_DEBUG_LOGGERS: 逗号分隔的 logger 名，强制设为 DEBUG（如 'lsc.analyzer'）
      例：LSC_LOG_DEBUG_LOGGERS=lsc.analyzer,lsc.handlers
    """
    log_dir = _get_log_dir()
    log_file = os.path.join(log_dir, 'backend.log')

    # 根级别：默认 INFO，可通过环境变量覆盖
    root_level_name = os.environ.get('LSC_LOG_LEVEL', 'INFO').upper()
    root_level = getattr(logging, root_level_name, logging.INFO)

    root = logging.getLogger()
    root.setLevel(root_level)
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

    # 滚动文件日志：单文件 2MB，保留 5 个备份，压缩旧日志（P3-3: 日志压缩和清理）
    try:
        file_handler = _CompressedRotatingFileHandler(
            log_file, maxBytes=2 * 1024 * 1024, backupCount=5, encoding='utf-8',
        )
        file_handler.setFormatter(fmt)
        root.addHandler(file_handler)
    except OSError as exc:
        # 日志目录不可写时仅用控制台
        print(f"[warn] failed to create file log handler at {log_file}: {exc}", file=sys.stderr)

    # P0/P1: 允许通过环境变量强制指定 logger 为 DEBUG 级别
    # 用法：LSC_LOG_DEBUG_LOGGERS=lsc.analyzer
    debug_loggers = os.environ.get('LSC_LOG_DEBUG_LOGGERS', '').strip()
    backend_log = logging.getLogger('lsc.backend')
    if debug_loggers:
        for name in debug_loggers.split(','):
            name = name.strip()
            if name:
                logging.getLogger(name).setLevel(logging.DEBUG)
                backend_log.info("强制 DEBUG 级别: %s (via LSC_LOG_DEBUG_LOGGERS)", name)

    return backend_log


def _install_exception_hook(log: logging.Logger) -> None:
    """安装 sys.excepthook + threading.excepthook，将未捕获异常桥接到 logging。"""
    def _hook(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        log.error("Unhandled exception: %s", ''.join(traceback.format_exception(exc_type, exc_value, exc_tb)))
        # 同步打到 stderr 供 Electron 捕获
        traceback.print_exception(exc_type, exc_value, exc_tb, file=sys.stderr)

    def _thread_hook(args):
        # threading.ExceptHookArgs 字段名为 exc_traceback（非 exc_tb）
        tb = getattr(args, 'exc_traceback', None) or getattr(args, 'exc_tb', None)
        log.error(
            "Unhandled exception in thread %s: %s",
            args.thread.name,
            ''.join(traceback.format_exception(args.exc_type, args.exc_value, tb)),
        )

    sys.excepthook = _hook
    threading.excepthook = _thread_hook


_log = _setup_logging()
_install_exception_hook(_log)


from broadcast_hub import BroadcastHub
from server import LSCWebSocketServer

from lsc.core.orchestrator import RoomOrchestrator


class LSCWebSocketBackend:
    def __init__(self):
        self.manager = RoomOrchestrator()
        self.manager.start()
        self.bridge = BroadcastHub(self.manager)
        self.server = LSCWebSocketServer(host="127.0.0.1", port=9876)
        self._ws_thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._shutdown = False
        # 用于解除 server.start() 中 `await asyncio.Future()` 的阻塞。
        # stop() 时 set 此 event，run_until_complete 会正常返回。
        self._stop_event: asyncio.Event | None = None
        self._main_stop = threading.Event()
        self._stop_lock = threading.Lock()
        self._parent_watch_thread: threading.Thread | None = None

    def _start_parent_watchdog(self) -> None:
        """Electron 异常退出时主动清理录制/预览/分析及 FFmpeg 子进程。"""
        try:
            parent_pid = int(os.environ.get("LSC_PARENT_PID", "0") or 0)
        except ValueError:
            parent_pid = 0
        if parent_pid <= 0:
            return

        def _watch() -> None:
            try:
                import psutil
            except ImportError:
                return
            while not self._main_stop.wait(2.0):
                if psutil.pid_exists(parent_pid):
                    continue
                _log.warning("Electron parent process exited; stopping backend (parent_pid=%s)", parent_pid)
                self.stop()
                return

        self._parent_watch_thread = threading.Thread(
            target=_watch,
            name="electron-parent-watchdog",
            daemon=True,
        )
        self._parent_watch_thread.start()

    def _run_ws_server(self):
        """在工作线程中运行 WebSocket 服务器。"""
        from handlers.room_handler import register_room_handlers, restore_persisted_rooms

        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._stop_event = asyncio.Event()

        try:
            restored = restore_persisted_rooms(self.manager)
            _log.info("Restored %d persisted room(s) before WebSocket startup", restored)
        except Exception:
            # 房间恢复失败不应阻止后端启动；连接后仍可手动重新添加。
            _log.exception("Failed to restore persisted rooms")

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
            except Exception as exc:
                _log.debug("操作异常（已忽略）: %s", exc)
            self._loop.close()
            _log.info("WebSocket server thread exited")

    async def _broadcast_coroutine(self):
        """协程版广播循环：从 bridge 队列取消息并发送（事件驱动唤醒）。"""
        from server import _json_dumps, drain_merge_broadcasts
        wake = asyncio.Event()
        self.bridge.bind_async_wake(asyncio.get_running_loop(), wake)
        while not self._shutdown:
            try:
                merged = drain_merge_broadcasts(self.bridge)
                if not merged:
                    wake.clear()
                    try:
                        await asyncio.wait_for(wake.wait(), timeout=0.5)
                    except asyncio.TimeoutError:
                        pass
                    continue
                for msg in merged:
                    # Use the numpy-aware serializer (consistent with
                    # server.py dispatch/broadcast). Plain json.dumps
                    # raises TypeError on numpy int64/float64 values
                    # produced by the audio-analysis code; the broad
                    # except below silently dropped those broadcasts.
                    data = _json_dumps(msg)
                    clients = list(self.server.clients)
                    if not clients:
                        continue
                    await asyncio.gather(
                        *[client.send(data) for client in clients],
                        return_exceptions=True,
                    )
            except Exception:
                _log.exception("broadcast error, retrying in 1s")
                await asyncio.sleep(1)

    def start(self):
        _log.info("Starting LSC Electron backend...")
        self._start_parent_watchdog()

        self._ws_thread = threading.Thread(target=self._run_ws_server, daemon=True)
        self._ws_thread.start()

        # 等待 WebSocket 线程完成端口绑定（最多 5 秒）
        for _ in range(50):
            if self.server._server is not None:
                port = self.server._bound_port or self.server.port
                _log.info("WebSocket server ready at ws://127.0.0.1:%s", port)
                # 同时打到 stdout 供 Electron 主进程正则匹配
                print(f"WebSocket server ready at ws://127.0.0.1:{port}", flush=True)
                break
            time.sleep(0.1)

        # 主线程阻塞等待停止信号（不再使用 QApplication.exec）
        self._main_stop.wait()

    def stop(self):
        """优雅停止后端。

        通过 set _stop_event 解除 server.start() 中 `await asyncio.Future()`
        的阻塞（旧实现仅设 _shutdown 标志但未调度 loop.stop，导致 ws 线程
        超时被强杀）。
        """
        with self._stop_lock:
            if self._shutdown:
                self._main_stop.set()
                return
            self._shutdown = True
        try:
            from handlers.room_handler import shutdown_room_handlers
            shutdown_room_handlers(timeout_sec=10.0)
        except Exception as exc:
            _log.warning("room handler shutdown failed: %s", exc, exc_info=True)
        try:
            self.manager.shutdown(timeout_sec=10.0)
        except Exception as exc:
            _log.warning("manager shutdown failed: %s", exc, exc_info=True)
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
        # 3) 解除主线程 wait
        self._main_stop.set()
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
