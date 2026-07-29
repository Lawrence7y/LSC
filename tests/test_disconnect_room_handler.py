"""Regression test for the disconnect_room WS handler (A3 去 Qt 化迁移).

守护点：后端拆桥后 `bridge` 是 `BroadcastHub`，它**没有** `submit` 方法。
断房指令必须经 `manager.submit(...)`（RoomOrchestrator）派发；若有人回退成
`bridge.submit(...)`，本用例会在真实 BroadcastHub 上抛 AttributeError，
真正的 disconnect_room 永不执行（房间无法断开）。
"""
from __future__ import annotations

import asyncio
import os
import sys

_backend_dir = os.path.join(os.path.dirname(__file__), '..', 'python-backend')
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

from broadcast_hub import BroadcastHub  # 真实生产 bridge 对象
from handlers import room_handler


class _FakeBus:
    """EventBus 替身：BroadcastHub 初始化时只调用 subscribe。"""

    def subscribe(self, *_args, **_kwargs) -> None:
        return None


class _FakeServer:
    def __init__(self) -> None:
        self.handlers: dict = {}

    def on(self, name):
        def decorator(handler):
            self.handlers[name] = handler
            return handler

        return decorator

    def on_connect(self, handler):
        return handler

    async def broadcast(self, name, data):
        return None

    async def broadcast_mse(self, kind, room_id, payload):
        return None


class _FakeOrchestrator:
    """最小 RoomOrchestrator 替身，覆盖 BroadcastHub + disconnect 处理所需接口。"""

    def __init__(self) -> None:
        self.bus = _FakeBus()
        self.submitted: list[tuple] = []
        self.disconnected: list[str] = []

    # 跨线程原语
    def call(self, fn, *args, **kwargs):
        kwargs.pop("timeout", None)
        return fn(*args, **kwargs)

    def submit(self, fn, *args, **kwargs) -> None:
        self.submitted.append((fn, args, kwargs))
        fn(*args, **kwargs)

    # handler 触碰的公开 API
    def disconnect_room(self, room_id: str) -> bool:
        self.disconnected.append(room_id)
        return True

    def get_room(self, room_id: str):
        return None

    def list_rooms(self):
        return []


def test_disconnect_room_dispatches_via_manager_submit(monkeypatch) -> None:
    """disconnect_room handler 必须经 manager.submit 命中 disconnect_room。"""
    manager = _FakeOrchestrator()
    bridge = BroadcastHub(manager)  # 真实生产对象（无 submit）
    server = _FakeServer()

    # 无公共时间轴 → _soft_disconnect 走 no_timeline 早退，不触碰 timeline 分支
    fake_svc = type(
        "_Svc", (), {"get_active_timeline_for_room": lambda self, rid: None}
    )()
    monkeypatch.setattr(room_handler, "get_timeline_service", lambda: fake_svc)
    monkeypatch.setattr(room_handler, "load_settings", lambda: {})

    async def scenario():
        room_handler.register_room_handlers(server, bridge)
        return await server.handlers["disconnect_room"]({"room_id": "room-9"})

    result = asyncio.run(scenario())

    assert result == {"success": True}
    assert manager.disconnected == ["room-9"]
    # fire-and-forget 语义：经 manager.submit 提交 disconnect_room
    assert manager.submitted
    assert manager.submitted[0][0] == manager.disconnect_room


def test_broadcast_hub_has_no_submit_method() -> None:
    """契约锚点：生产 bridge 是 BroadcastHub，没有 submit；跨线程写必须走 orchestrator。"""
    assert not hasattr(BroadcastHub, "submit")
