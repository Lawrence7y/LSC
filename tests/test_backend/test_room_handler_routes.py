"""python-backend room_handler 核心路由单元测试。

覆盖 get_rooms / save_rooms / validate_room_urls / add_room / remove_room /
refresh_room_status 等核心 WebSocket 消息处理逻辑。
"""
from __future__ import annotations

import asyncio
import os
import sys
from types import SimpleNamespace

_backend_dir = os.path.join(os.path.dirname(__file__), '..', '..', 'python-backend')
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

from handlers import room_handler

# ─── 测试替身 ───────────────────────────────────────────────────────────────────


class _FakeBus:
    def subscribe(self, *_args, **_kwargs) -> None:
        return None


class _FakeSignal:
    def __init__(self) -> None:
        self.callbacks: list = []

    def connect(self, callback) -> None:
        self.callbacks.append(callback)


class _FakeServer:
    def __init__(self) -> None:
        self.handlers: dict = {}
        self.broadcasts: list = []
        self.connect_handler = None

    def on(self, name):
        def decorator(handler):
            self.handlers[name] = handler
            return handler
        return decorator

    def on_connect(self, handler):
        self.connect_handler = handler
        return handler

    async def broadcast(self, name, data):
        self.broadcasts.append((name, data))

    async def broadcast_mse(self, kind, room_id, payload):
        self.broadcasts.append((f"mse_{kind}", {"room_id": room_id}))


class _FakeBridge:
    def __init__(self, manager) -> None:
        self.manager = manager
        self.broadcasts: list = []

    def queue_broadcast(self, message) -> None:
        self.broadcasts.append(message)


class _FakeManager:
    def __init__(self, rooms=None) -> None:
        self._rooms = {r.room_id: r for r in (rooms or [])}
        self.bus = _FakeBus()
        self.room_connect_finished = _FakeSignal()
        self.batch_record_progress = _FakeSignal()
        self.batch_record_finished = _FakeSignal()
        self.medium_tick = _FakeSignal()
        self.low_tick = _FakeSignal()
        self.recording_stopped = _FakeSignal()
        self.removed: list = []

    def get_room(self, room_id: str):
        return self._rooms.get(room_id)

    def list_rooms(self):
        return list(self._rooms.values())

    def call(self, func, *args, **kwargs):
        kwargs.pop("timeout", None)
        return func(*args, **kwargs)

    def add_room(self, url: str):
        room_id = f"room-{len(self._rooms) + 1}"
        room = _make_room(room_id, url=url)
        self._rooms[room_id] = room
        return room

    def remove_room(self, room_id: str) -> None:
        self._rooms.pop(room_id, None)
        self.removed.append(room_id)

    def connect_room(self, room_id: str, *, async_mode: bool = False, quality_preset: str = "原画") -> bool:
        room = self.get_room(room_id)
        if room is None:
            return False
        if async_mode:
            room.is_connecting = True
        return True


def _make_room(room_id: str = "room-1", url: str = "https://live.example.com/123") -> SimpleNamespace:
    return SimpleNamespace(
        room_id=room_id,
        streamer_name="TestStreamer",
        platform="test",
        platform_name="Test",
        room_url=url,
        stream_title="Title",
        is_connecting=False,
        is_connected=False,
        is_recording=False,
        is_reconnecting=False,
        is_muted=False,
        record_output_path="",
        record_started_at=None,
        record_size_mb=0.0,
        last_error="",
        preview_enabled=False,
        preview_paused=False,
        preview_muted=False,
        preview_mode="live_mse",
        mark_in=None,
        mark_out=None,
        mark_in_wallclock=None,
        mark_out_wallclock=None,
        recording_start_mono=0.0,
        recording_media_start_mono=0.0,
        preview_latency=0.0,
        content_offset=0.0,
        align_group_id="",
        category="",
        stream_info=None,
        stream_url_cached="",
        controller=None,
    )


def _setup(monkeypatch, rooms=None):
    """注册 handlers 并返回 (server, manager, bridge)。"""
    manager = _FakeManager(rooms)
    server = _FakeServer()
    bridge = _FakeBridge(manager)
    monkeypatch.setattr(room_handler, "load_settings", lambda: {"quality": "原画"})
    room_handler.register_room_handlers(server, bridge)
    return server, manager, bridge


# ─── 测试用例 ───────────────────────────────────────────────────────────────────


def test_get_rooms_returns_all_rooms(monkeypatch) -> None:
    """get_rooms 应返回当前所有房间的列表。"""
    rooms = [_make_room("r1"), _make_room("r2")]
    server, manager, _ = _setup(monkeypatch, rooms)

    result = asyncio.run(server.handlers["get_rooms"]({}))

    assert "rooms" in result
    assert len(result["rooms"]) == 2


def test_save_rooms_rejects_non_list(monkeypatch) -> None:
    """save_rooms 传入非列表时应返回错误。"""
    server, _, _ = _setup(monkeypatch)

    result = asyncio.run(server.handlers["save_rooms"]({"rooms": "not_a_list"}))

    assert result["success"] is False
    assert "列表" in result["error"]


def test_save_rooms_rejects_missing_room_id(monkeypatch) -> None:
    """save_rooms 房间缺少 room_id 时应返回错误。"""
    server, _, _ = _setup(monkeypatch)

    result = asyncio.run(server.handlers["save_rooms"]({
        "rooms": [{"room_url": "https://example.com"}]
    }))

    assert result["success"] is False
    assert "room_id" in result["error"]


def test_validate_room_urls_rejects_empty_list(monkeypatch) -> None:
    """validate_room_urls 空列表应返回错误。"""
    server, _, _ = _setup(monkeypatch)

    result = asyncio.run(server.handlers["validate_room_urls"]({"urls": []}))

    assert result["success"] is False
    assert result["valid"] is False
    assert "链接" in result["error"]


def test_validate_room_urls_rejects_exceeding_limit(monkeypatch) -> None:
    """validate_room_urls 超过上限应返回错误。"""
    server, _, _ = _setup(monkeypatch)
    urls = [f"https://live.example.com/{i}" for i in range(20)]

    result = asyncio.run(server.handlers["validate_room_urls"]({"urls": urls}))

    assert result["success"] is False
    assert "最多" in result["error"]


def test_add_room_empty_url_returns_error(monkeypatch) -> None:
    """add_room 空 URL 应返回错误。"""
    server, _, _ = _setup(monkeypatch)

    result = asyncio.run(server.handlers["add_room"]({"url": ""}))

    assert result["success"] is False
    assert "链接" in result["error"]


def test_add_room_success(monkeypatch) -> None:
    """add_room 有效 URL 应成功添加房间并返回 room_id。"""
    server, manager, bridge = _setup(monkeypatch)
    monkeypatch.setattr(room_handler, "_persist_current_rooms", lambda m: None)

    result = asyncio.run(server.handlers["add_room"]({"url": "https://live.example.com/999"}))

    assert result["success"] is True
    assert "room_id" in result
    assert len(manager.list_rooms()) == 1


def test_remove_room_missing_room_id(monkeypatch) -> None:
    """remove_room 缺少 room_id 应返回错误。"""
    server, _, _ = _setup(monkeypatch)

    result = asyncio.run(server.handlers["remove_room"]({}))

    assert "error" in result
    assert "room_id" in result["error"]


def test_remove_room_success(monkeypatch) -> None:
    """remove_room 应成功移除房间。"""
    room = _make_room("r1")
    server, manager, bridge = _setup(monkeypatch, [room])
    monkeypatch.setattr(room_handler, "_persist_current_rooms", lambda m: None)
    monkeypatch.setattr(
        room_handler, "get_timeline_service",
        lambda: SimpleNamespace(
            get_active_timeline_for_room=lambda rid: None,
            invalidate_timeline=lambda tid, reason: None,
        ),
    )

    result = asyncio.run(server.handlers["remove_room"]({"room_id": "r1"}))

    assert result["success"] is True
    assert "r1" in manager.removed


def test_refresh_room_status_clears_errors(monkeypatch) -> None:
    """refresh_room_status 应清除房间的 last_error 字段。"""
    room = _make_room("r1")
    room.last_error = "连接超时"
    room.preview_error = "预览失败"
    server, manager, _ = _setup(monkeypatch, [room])

    result = asyncio.run(server.handlers["refresh_room_status"]({"room_id": "r1"}))

    assert result["success"] is True
    assert result["refreshed"] == 2
    assert room.last_error is None
    assert room.preview_error is None


def test_connect_room_requires_room_id(monkeypatch) -> None:
    """connect_room 缺少 room_id 应返回错误。"""
    server, _, _ = _setup(monkeypatch)

    result = asyncio.run(server.handlers["connect_room"]({}))

    assert result["success"] is False
    assert result["accepted"] is False
    assert "room_id" in result["error"]
