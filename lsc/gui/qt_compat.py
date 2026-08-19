"""PySide6.QtCore 最小兼容层（无 Qt 打包环境）。

Electron 后端是纯线程架构，不依赖 Qt 事件循环。历史遗留组件
（``lsc.gui.pages.recording_controller`` / ``lsc.gui.common_workers``）
仅使用 QThread / QTimer / Signal 的最小接口；在未安装 PySide6 的环境
（Store / 内置依赖安装包）中，用标准库 threading 提供等价的线程与回调语义，
使录制控制器等组件可正常工作，从而彻底移除 PySide6 依赖。

用法（与 Qt 路径互斥切换）::

    try:
        from PySide6.QtCore import QThread, QTimer, Signal
    except ImportError:
        from lsc.gui.qt_compat import QThread, QTimer, Signal

注意：兼容层 Signal.emit 为同步直调（无 Qt 事件循环时 queued signal 本就
不可用）；Electron 后端相关代码已按直接回调设计，语义一致。
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

_log = logging.getLogger(__name__)


class _SignalState:
    """单个 Signal 实例的连接状态（描述符按实例隔离）。"""

    __slots__ = ("_handlers",)

    def __init__(self) -> None:
        self._handlers: list[Callable[..., Any]] = []

    def connect(self, handler: Callable[..., Any]) -> None:
        if handler not in self._handlers:
            self._handlers.append(handler)

    def disconnect(self, handler: Callable[..., Any] | None = None) -> None:
        if handler is None:
            self._handlers.clear()
        elif handler in self._handlers:
            self._handlers.remove(handler)

    def emit(self, *args: Any) -> None:
        # Electron 路径无 Qt 事件循环，queued signal 不可用；
        # 直接同步调用 handler，与调用方线程上下文一致。
        for handler in list(self._handlers):
            try:
                handler(*args)
            except Exception:
                # 与 Qt 槽异常隔离一致：单个槽抛错不得中断其它槽或 worker 线程
                _log.exception("qt_compat signal handler raised")


class Signal:
    """Qt Signal 的最小兼容：类属性声明 + 实例级连接（描述符隔离）。

    用法与 PySide6 相同：``finished = Signal(dict)`` 定义类属性，
    ``self.finished.connect(fn)`` / ``self.finished.emit(**kwargs)``。
    """

    def __init__(self, *types: Any) -> None:
        self._name = ""

    def __set_name__(self, owner: type, name: str) -> None:
        self._name = name

    def __get__(self, instance: Any, owner: type | None = None) -> Any:
        if instance is None:
            return self
        state = instance.__dict__.get(self._name)
        if state is None:
            state = _SignalState()
            instance.__dict__[self._name] = state
        return state


class QThread:
    """QThread 的最小兼容：子类覆写 run()，start/isRunning/wait 语义等价。"""

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self.run,
            daemon=True,
            name=f"{type(self).__name__}-qt-compat",
        )
        self._thread.start()

    def run(self) -> None:  # pragma: no cover - 由子类覆写
        pass

    def isRunning(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def wait(self, msecs: int | None = None) -> bool:
        if self._thread is None:
            return True
        self._thread.join(timeout=None if msecs is None else msecs / 1000.0)
        return not self._thread.is_alive()

    def quit(self) -> None:
        # run() 无事件循环；退出由 wait() 等待自然结束。
        pass


class QTimer:
    """QTimer 的最小兼容。

    Electron 后端仅调用 stop()（cleanup 时）；start 提供简单的循环定时
    线程，供历史 GUI 路径使用。
    """

    def __init__(self) -> None:
        self.timeout = _SignalState()
        self._interval_ms = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self, msecs: int | None = None) -> None:
        if msecs is not None:
            self._interval_ms = int(msecs)
        self.stop()
        if self._interval_ms <= 0:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            daemon=True,
            name="QTimer-compat",
        )
        self._thread.start()

    def _run(self) -> None:
        interval = self._interval_ms / 1000.0
        while not self._stop.wait(interval):
            self.timeout.emit()

    def stop(self) -> None:
        self._stop.set()

    def isActive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()
