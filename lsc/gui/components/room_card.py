"""多直播间工作台的房间卡片组件。"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QCheckBox, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from lsc.gui.multi_room.session import RoomSession


class RoomCard(QFrame):
    selected = Signal(str)
    connect = Signal(str)
    record = Signal(str)
    stop = Signal(str)
    remove = Signal(str)
    mute_toggled = Signal(str, bool)

    def __init__(self, room: RoomSession, parent=None) -> None:
        super().__init__(parent)
        self.room = room
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setObjectName("roomCard")
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        self._platform_label = QLabel(self)
        self._title_label = QLabel(self)
        self._title_label.setWordWrap(True)
        self._status_label = QLabel(self)
        self._status_label.setWordWrap(True)
        self._mute_button = QCheckBox("静音", self)

        controls = QHBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(6)

        self._connect_button = QPushButton("连接", self)
        self._record_button = QPushButton("录制", self)
        self._stop_button = QPushButton("停止", self)
        self._remove_button = QPushButton("移除", self)

        controls.addWidget(self._connect_button)
        controls.addWidget(self._record_button)
        controls.addWidget(self._stop_button)
        controls.addWidget(self._mute_button)
        controls.addWidget(self._remove_button)

        root.addWidget(self._platform_label)
        root.addWidget(self._title_label)
        root.addWidget(self._status_label)
        root.addLayout(controls)

        self._mute_button.toggled.connect(self._on_mute_toggled)
        self._connect_button.clicked.connect(lambda: self.connect.emit(self.room.room_id))
        self._record_button.clicked.connect(lambda: self.record.emit(self.room.room_id))
        self._stop_button.clicked.connect(lambda: self.stop.emit(self.room.room_id))
        self._remove_button.clicked.connect(lambda: self.remove.emit(self.room.room_id))

    def refresh(self) -> None:
        title = self.room.room_url
        if self.room.stream_info is not None and self.room.stream_info.title:
            title = self.room.stream_info.title

        self._platform_label.setText(self.room.platform or "未知平台")
        self._title_label.setText(title)
        self._status_label.setText(self._status_text())
        self._mute_button.setChecked(self.room.preview_muted)

    def _status_text(self) -> str:
        if self.room.is_recording:
            return "录制中"
        if self.room.is_connected:
            return "已连接"
        if self.room.last_error:
            return self.room.last_error
        return "未连接"

    def _on_mute_toggled(self, muted: bool) -> None:
        self.room.preview_muted = muted
        self.mute_toggled.emit(self.room.room_id, muted)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.selected.emit(self.room.room_id)
        super().mousePressEvent(event)
