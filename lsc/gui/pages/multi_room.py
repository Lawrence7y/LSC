"""多房间工作台页面。"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from lsc.gui.components.room_card import RoomCard
from lsc.gui.multi_room.manager import MultiRoomManager
from lsc.gui.multi_room.session import RoomSession


class MultiRoomPage(QWidget):
    """一个可独立实例化的多房间工作台页面。"""

    room_selected = Signal(str)
    room_record_requested = Signal(str)
    room_stop_requested = Signal(str)

    def __init__(self, manager: MultiRoomManager | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._manager = manager if manager is not None else MultiRoomManager()
        self._cards_by_room_id: dict[str, RoomCard] = {}
        self._build_ui()
        self.load_rooms()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(8)

        self._summary_label = QLabel("房间数：0", self)
        self._summary_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        self._url_input = QLineEdit(self)
        self._url_input.setPlaceholderText("输入直播间链接")
        self._url_input.returnPressed.connect(self.add_room_from_url)

        self._add_button = QPushButton("添加房间", self)
        self._add_button.clicked.connect(self.add_room_from_url)

        header.addWidget(self._summary_label)
        header.addStretch()
        header.addWidget(self._url_input, 1)
        header.addWidget(self._add_button)
        root.addLayout(header)

        self._empty_label = QLabel("暂无房间", self)
        self._empty_label.setAlignment(Qt.AlignCenter)
        root.addWidget(self._empty_label)

        self._scroll_area = QScrollArea(self)
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self._card_container = QWidget(self._scroll_area)
        self._card_layout = QVBoxLayout(self._card_container)
        self._card_layout.setContentsMargins(0, 0, 0, 0)
        self._card_layout.setSpacing(8)
        self._card_layout.setAlignment(Qt.AlignTop)

        self._scroll_area.setWidget(self._card_container)
        root.addWidget(self._scroll_area, 1)

    def load_rooms(self) -> None:
        rooms = list(self._manager.list_rooms())
        self._clear_cards()

        for room in rooms:
            self._add_card(room)

        self._refresh_summary(len(rooms))

    def add_room_from_url(self, url: str | None = None) -> RoomSession | None:
        room_url = (url or self._url_input.text()).strip()
        if not room_url:
            return None

        room = self._manager.add_room(room_url)
        self._url_input.clear()
        self.load_rooms()
        return room

    def connect_room(self, room_id: str) -> bool:
        ok = self._manager.connect_room(room_id)
        self._refresh_room_card(room_id)
        return ok

    def remove_room(self, room_id: str) -> bool:
        removed = self._manager.remove_room(room_id)
        if removed:
            self.load_rooms()
        return removed

    def start_recording_all(self, output_dir: str, encoder: str, crf: int) -> dict[str, bool]:
        return self._manager.start_recording_all(output_dir, encoder, crf)

    def stop_recording_all(self) -> dict[str, bool]:
        return self._manager.stop_recording_all()

    def _add_card(self, room: RoomSession) -> None:
        card = RoomCard(room, self._card_container)
        card.selected.connect(self.room_selected.emit)
        card.connect.connect(self.connect_room)
        card.record.connect(self.room_record_requested.emit)
        card.stop.connect(self.room_stop_requested.emit)
        card.mute_toggled.connect(self._manager.mute_room)
        card.remove.connect(self.remove_room)
        self._cards_by_room_id[room.room_id] = card
        self._card_layout.addWidget(card)

    def _clear_cards(self) -> None:
        self._cards_by_room_id.clear()
        while self._card_layout.count():
            item = self._card_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _refresh_summary(self, room_count: int) -> None:
        self._summary_label.setText(f"房间数：{room_count}")
        self._empty_label.setVisible(room_count == 0)
        self._scroll_area.setVisible(room_count > 0)

    def _refresh_room_card(self, room_id: str) -> None:
        card = self._cards_by_room_id.get(room_id)
        if card is not None:
            card.refresh()


__all__ = ["MultiRoomPage"]
