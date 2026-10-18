"""后端停机链（`LSCWebSocketBackend.stop()`）回归测试。

2026-09-18 实测到的事故级缺陷（把 python-backend 纳入 mypy 范围后由
`func-returns-value` 暴露）：

    asyncio.run_coroutine_threadsafe(self.server._server.close(), self._loop)

`websockets` legacy 的 `WebSocketServer.close()` 是**同步**方法
（实测 `inspect.iscoroutinefunction(WebSocketServer.close) is False`），于是这一行
每次都抛 `TypeError: A coroutine object is required`，而旁边只
`except RuntimeError: pass` —— 异常冲出 stop()，后面的 `_main_stop.set()`
永远不执行，主线程卡在 `self._main_stop.wait()` 上。用户侧表现就是
"关掉应用以后 python 后端进程还在"，也正是打包前总要手工杀残留进程的原因。

这里守三条：
1. 停机不再抛异常；
2. 无论 ws 那两步成功与否，`_main_stop` 必须被 set（主线程必须能返回）；
3. close 走 `call_soon_threadsafe`，不是 `run_coroutine_threadsafe`。
"""
from __future__ import annotations

import asyncio
import inspect
import os
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
_backend_dir = os.path.join(ROOT, "python-backend")
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

import pytest
from main import LSCWebSocketBackend  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_real_shutdown(_noop_handler_shutdown, _noop_room_flush):
    """本文件的每个用例都必须与真实停机隔离。

    `stop()` 会调 `room_handler.shutdown_room_handlers()`，而它**关的是模块级
    线程池**（实测 `executors_shutdown: 5`）。在一个 pytest 进程里这就把后续
    所有跑 executor 的用例全毒化成 `RuntimeError: cannot schedule new futures
    after shutdown` —— 本文件最初就是这样让 9 个无关用例变红的。
    """


@pytest.fixture
def _noop_handler_shutdown(monkeypatch):
    from handlers import room_handler

    calls: list[float | None] = []
    monkeypatch.setattr(
        room_handler,
        "shutdown_room_handlers",
        lambda timeout_sec=None: calls.append(timeout_sec) or {"executors_shutdown": 0},
    )
    return calls


@pytest.fixture
def _noop_room_flush(monkeypatch):
    import main

    monkeypatch.setattr(main, "flush_pending_room_saves", lambda **_kw: True)


class _StubLoop:
    def __init__(self, *, raise_on_schedule: BaseException | None = None) -> None:
        self.scheduled: list[object] = []
        self._raise = raise_on_schedule

    def is_closed(self) -> bool:
        return False

    def call_soon_threadsafe(self, fn, *args):  # noqa: ANN001
        if self._raise is not None:
            raise self._raise
        self.scheduled.append(fn if not args else (fn, args))
        return SimpleNamespace(cancel=lambda: None)


class _StubServer:
    def __init__(self) -> None:
        self.closed = 0

    def close(self) -> None:  # 与 websockets legacy 一致：同步、返回 None
        self.closed += 1


def _bare_backend(loop: _StubLoop | None) -> LSCWebSocketBackend:
    """绕开 __init__（它会起真的 RoomOrchestrator 线程）拼一个只够 stop() 的后端。"""
    backend = object.__new__(LSCWebSocketBackend)
    backend.manager = SimpleNamespace(shutdown=lambda **_kw: None)
    backend.server = SimpleNamespace(_server=_StubServer())
    backend.bridge = SimpleNamespace()
    backend._ws_thread = None
    backend._loop = loop
    backend._stop_event = asyncio.Event()
    backend._shutdown = False
    backend._main_stop = threading.Event()
    backend._stop_lock = threading.Lock()
    backend._parent_watch_thread = None
    return backend


def test_websockets_close_is_sync_so_coroutine_wrapper_was_wrong():
    """事故根因的可执行说明：close() 不是协程，塞进 run_coroutine_threadsafe 必抛。"""
    from websockets.legacy.server import WebSocketServer

    assert inspect.iscoroutinefunction(WebSocketServer.close) is False
    try:
        asyncio.run_coroutine_threadsafe(None, asyncio.new_event_loop())  # type: ignore[arg-type]
    except TypeError as exc:
        assert "coroutine" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("run_coroutine_threadsafe(非协程) 应当抛 TypeError")


def test_stop_schedules_server_close_on_loop_thread():
    loop = _StubLoop()
    backend = _bare_backend(loop)
    backend.stop()
    srv_close = backend.server._server.close
    assert srv_close in loop.scheduled, "close 必须以 call_soon_threadsafe 投递"
    assert backend.server._server.closed == 0  # 只投递，不跨线程直接调


def test_stop_releases_main_wait_even_when_scheduling_explodes():
    """核心不变量：ws 那两步坏成什么样，主线程都必须被放行。"""
    loop = _StubLoop(raise_on_schedule=TypeError("A coroutine object is required"))
    backend = _bare_backend(loop)
    backend.stop()  # 不得抛出
    assert backend._main_stop.is_set()


def test_stop_never_raises_for_unexpected_loop_errors():
    for error in (RuntimeError("loop dead"), TypeError("bad"), OSError("socket gone")):
        loop = _StubLoop(raise_on_schedule=error)
        backend = _bare_backend(loop)
        backend.stop()
        assert backend._main_stop.is_set(), error


def test_stop_waits_for_ws_thread_and_flushes_rooms(tmp_path, monkeypatch):
    """第 4/5 步（join ws 线程、最终落盘）也必须跑到。"""
    joined: list[float] = []
    thread = SimpleNamespace(join=lambda timeout=None: joined.append(timeout or -1))
    loop = _StubLoop()
    backend = _bare_backend(loop)
    backend._ws_thread = thread
    flushed: list[bool] = []
    # main.py 用的是 `from persistence import ...`，绑定的名字在 main 模块里
    monkeypatch.setattr(
        "main.flush_pending_room_saves",
        lambda *, fsync=False: flushed.append(fsync) or True,
    )

    backend.stop()

    assert joined == [3.0]
    assert flushed == [True]


def test_stop_is_idempotent_after_first_call():
    backend = _bare_backend(_StubLoop())
    backend.stop()
    again = _bare_backend(None)
    again._shutdown = True
    again._loop = None
    again._stop_event = None
    again.stop()
    assert again._main_stop.is_set()
