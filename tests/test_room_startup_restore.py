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
    assert room.mark_in == 12.0
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
