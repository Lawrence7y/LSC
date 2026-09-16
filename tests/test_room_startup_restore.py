from __future__ import annotations

from types import SimpleNamespace

from handlers import room_handler


class _FakeManager:
    def __init__(self) -> None:
        self.rooms = []

    def call(self, callback):
        return callback()

    def list_rooms(self):
        return list(self.rooms)

    def add_room(self, url: str):
        room = SimpleNamespace(
            room_url=url,
            mark_in=None,
            mark_out=None,
            content_offset=0.0,
            align_group_id="",
            category="",
            preview_muted=True,
            include_in_cut=True,
        )
        self.rooms.append(room)
        return room


def test_restore_persisted_rooms_once_before_websocket_connect(monkeypatch) -> None:
    manager = _FakeManager()
    monkeypatch.setattr(
        "persistence.load_rooms",
        lambda: [
            {
                "room_url": "https://live.example/1",
                "mark_in": 12,
                "content_offset": 1.5,
                "align_group_id": "group-a",
                "category": "valorant",
                "preview_muted": False,
            }
        ],
    )

    assert room_handler.restore_persisted_rooms(manager) == 1
    assert room_handler.restore_persisted_rooms(manager) == 0
    assert len(manager.rooms) == 1
    room = manager.rooms[0]
    assert room.room_url == "https://live.example/1"
    # 选区 mark_in 为会话级瞬时状态，跨重启不恢复，确保初态为 None
    assert room.mark_in is None
    assert getattr(room, "mark_out", None) is None
    assert room.content_offset == 1.5
    assert room.align_group_id == "group-a"
    assert room.category == "valorant"
    assert room.preview_muted is False


def test_restore_persisted_rooms_accepts_legacy_url_field(monkeypatch) -> None:
    manager = _FakeManager()
    monkeypatch.setattr(
        "persistence.load_rooms",
        lambda: [{"url": "https://live.example/legacy"}],
    )

    assert room_handler.restore_persisted_rooms(manager) == 1
    assert manager.rooms[0].room_url == "https://live.example/legacy"


def test_connect_and_disconnect_resets_markers(monkeypatch) -> None:
    """连接和断开房间时，旧选区与墙钟时间戳必须被清空。"""
    from lsc.core.orchestrator import RoomOrchestrator

    orch = RoomOrchestrator()
    room = orch.add_room("https://live.example/test-room")
    assert room is not None

    # 模拟用户打标
    room.mark_in = 10.0
    room.mark_out = 30.0
    room.mark_in_wallclock = 1000.0
    room.mark_out_wallclock = 1020.0

    # 断开连接时应重置选区
    orch.disconnect_room(room.room_id)
    assert room.mark_in is None
    assert room.mark_out is None
    assert room.mark_in_wallclock is None
    assert room.mark_out_wallclock is None

    # 再次打标并模拟重新连接
    room.mark_in = 15.0
    room.mark_out = 45.0
    room.mark_in_wallclock = 2000.0
    room.mark_out_wallclock = 2030.0

    monkeypatch.setattr(orch, "_connect_room_async", lambda r, quality_preset="原画": True)
    orch.connect_room(room.room_id, async_mode=True)
    assert room.mark_in is None
    assert room.mark_out is None
    assert room.mark_in_wallclock is None
    assert room.mark_out_wallclock is None


def test_orchestrator_serialize_and_load_ignores_markers() -> None:
    """RoomOrchestrator 持久化序列化不包含选区，加载时忽略选区。"""
    from lsc.core.orchestrator import RoomOrchestrator
    from lsc.core.session import RoomSession

    orch = RoomOrchestrator()
    room = RoomSession(room_id="r1", room_url="https://live.example/orch")
    room.mark_in = 25.0
    room.mark_out = 50.0

    serialized = orch._serialize_room(room)
    assert "mark_in" not in serialized
    assert "mark_out" not in serialized

    # load_rooms 针对带 mark_in 的字典不恢复该字段
    monkeypatch_data = {"rooms": [{"url": "https://live.example/orch", "mark_in": 25.0, "mark_out": 50.0}]}
    orch._rooms.clear()
    orch._load_json_file = lambda path: monkeypatch_data
    loaded_count = orch.load_rooms()
    assert loaded_count == 1
    loaded_room = orch.list_rooms()[0]
    assert loaded_room.mark_in is None
    assert loaded_room.mark_out is None
