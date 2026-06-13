"""GUI tests for the multi-room workbench page and room cards."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from lsc.gui.multi_room.session import RoomSession


def _qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_room_card_reflects_basic_room_state() -> None:
    _qapp()

    from lsc.gui.components.room_card import RoomCard

    room = RoomSession(
        room_id="room-1",
        room_url="https://live.douyin.com/123",
        platform="douyin",
    )
    room.preview_muted = True
    room.is_connected = True

    card = RoomCard(room)

    assert "douyin" in card._platform_label.text().lower()
    assert card._mute_button.isChecked() is True
    assert "已连接" in card._status_label.text()


def test_room_card_emits_mute_toggle_for_own_room() -> None:
    _qapp()

    from lsc.gui.components.room_card import RoomCard

    room = RoomSession(room_id="room-1", room_url="https://live.douyin.com/123")
    card = RoomCard(room)
    hits: list[tuple[str, bool]] = []
    card.mute_toggled.connect(lambda room_id, muted: hits.append((room_id, muted)))

    card._mute_button.click()

    assert hits[-1] == ("room-1", False)


class _FakeManager:
    def __init__(self, rooms: list[RoomSession] | None = None) -> None:
        self.rooms = list(rooms or [])
        self.added_urls: list[str] = []
        self.muted_calls: list[tuple[str, bool]] = []
        self.start_all_calls: list[tuple[str, str, int]] = []
        self.stop_all_calls = 0

    def list_rooms(self) -> list[RoomSession]:
        return list(self.rooms)

    def add_room(self, url: str) -> RoomSession:
        room = RoomSession(room_id=f"room-{len(self.rooms) + 1}", room_url=url, platform="douyin")
        self.rooms.append(room)
        self.added_urls.append(url)
        return room

    def mute_room(self, room_id: str, muted: bool) -> None:
        self.muted_calls.append((room_id, muted))

    def start_recording_all(self, output_dir: str, encoder: str, crf: int) -> dict[str, bool]:
        self.start_all_calls.append((output_dir, encoder, crf))
        return {room.room_id: True for room in self.rooms}

    def stop_recording_all(self) -> dict[str, bool]:
        self.stop_all_calls += 1
        return {room.room_id: False for room in self.rooms}


def test_multi_room_page_can_be_instantiated_with_manager() -> None:
    _qapp()

    from lsc.gui.pages.multi_room import MultiRoomPage

    manager = _FakeManager()

    page = MultiRoomPage(manager=manager)

    assert page._manager is manager
    assert page._summary_label.text() == "房间数：0"


def test_multi_room_page_load_rooms_renders_cards() -> None:
    _qapp()

    from lsc.gui.pages.multi_room import MultiRoomPage

    manager = _FakeManager(
        [
            RoomSession(room_id="room-1", room_url="https://live.douyin.com/1", platform="douyin"),
            RoomSession(room_id="room-2", room_url="https://live.bilibili.com/2", platform="bilibili"),
        ]
    )
    page = MultiRoomPage(manager=manager)

    page.load_rooms()

    assert list(page._cards_by_room_id) == ["room-1", "room-2"]
    assert page._card_layout.count() == 2
    assert page._summary_label.text() == "房间数：2"


def test_multi_room_page_add_room_from_explicit_url_adds_card_and_refreshes_summary() -> None:
    _qapp()

    from lsc.gui.pages.multi_room import MultiRoomPage

    manager = _FakeManager()
    page = MultiRoomPage(manager=manager)

    page.add_room_from_url("https://live.douyin.com/3")

    assert manager.added_urls == ["https://live.douyin.com/3"]
    assert list(page._cards_by_room_id) == ["room-1"]
    assert page._summary_label.text() == "房间数：1"


def test_multi_room_page_add_room_from_input_uses_textbox_value() -> None:
    _qapp()

    from lsc.gui.pages.multi_room import MultiRoomPage

    manager = _FakeManager()
    page = MultiRoomPage(manager=manager)
    page._url_input.setText("https://live.douyin.com/5")

    page.add_room_from_url()

    assert manager.added_urls == ["https://live.douyin.com/5"]
    assert page._url_input.text() == ""


def test_multi_room_page_mute_toggle_forwards_to_manager() -> None:
    _qapp()

    from lsc.gui.pages.multi_room import MultiRoomPage

    manager = _FakeManager([RoomSession(room_id="room-1", room_url="https://live.douyin.com/1")])
    page = MultiRoomPage(manager=manager)
    page.load_rooms()

    page._cards_by_room_id["room-1"]._mute_button.click()

    assert manager.muted_calls == [("room-1", False)]


def test_multi_room_page_start_recording_all_forwards_to_manager() -> None:
    _qapp()

    from lsc.gui.pages.multi_room import MultiRoomPage

    manager = _FakeManager([RoomSession(room_id="room-1", room_url="https://live.douyin.com/1")])
    page = MultiRoomPage(manager=manager)

    result = page.start_recording_all("D:/records", "Copy", 23)

    assert result == {"room-1": True}
    assert manager.start_all_calls == [("D:/records", "Copy", 23)]


def test_multi_room_page_stop_recording_all_forwards_to_manager() -> None:
    _qapp()

    from lsc.gui.pages.multi_room import MultiRoomPage

    manager = _FakeManager([RoomSession(room_id="room-1", room_url="https://live.douyin.com/1")])
    page = MultiRoomPage(manager=manager)

    result = page.stop_recording_all()

    assert result == {"room-1": False}
    assert manager.stop_all_calls == 1


def test_dashboard_page_emits_multi_room_navigation_request() -> None:
    _qapp()

    from lsc.gui.pages.dashboard import DashboardPage

    page = DashboardPage()
    hits: list[bool] = []
    page.multi_room_requested.connect(lambda: hits.append(True))

    page._multi_room_btn.click()

    assert hits == [True]
