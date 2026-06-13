"""GUI tests for the multi-room workbench page and room cards."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication


def _qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_room_card_reflects_basic_room_state() -> None:
    _qapp()

    from lsc.gui.components.room_card import RoomCard
    from lsc.gui.multi_room.session import RoomSession

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
    from lsc.gui.multi_room.session import RoomSession

    room = RoomSession(room_id="room-1", room_url="https://live.douyin.com/123")
    card = RoomCard(room)
    hits: list[tuple[str, bool]] = []
    card.mute_toggled.connect(lambda room_id, muted: hits.append((room_id, muted)))

    card._mute_button.click()

    assert hits[-1] == ("room-1", False)
