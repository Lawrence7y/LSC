"""仪表盘页面。"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget


class DashboardPage(QWidget):
    record_requested = Signal()
    multi_room_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build()

    def _build(self):
        main = QVBoxLayout(self)
        main.setContentsMargins(24, 24, 24, 24)
        main.setSpacing(24)

        stats = QHBoxLayout()
        stats.setContentsMargins(0, 0, 0, 0)
        stats.setSpacing(20)

        self._rec_card = _StatCard("0", "正在录制")
        stats.addWidget(self._rec_card, 1)

        self._dur_card = _StatCard("00:00:00", "今日录制时长")
        stats.addWidget(self._dur_card, 1)

        self._clip_card = _StatCard("0", "已导出片段")
        stats.addWidget(self._clip_card, 1)

        main.addLayout(stats)

        card = _SectionCard()
        hdr = QHBoxLayout()
        tl = QVBoxLayout()

        title = QLabel("录制会话")
        title.setStyleSheet("font-size:15px;font-weight:600;")
        tl.addWidget(title)

        subtitle = QLabel("当前和近期的录制任务")
        subtitle.setStyleSheet("font-size:12px;color:#6b7280;")
        tl.addWidget(subtitle)

        hdr.addLayout(tl)
        hdr.addStretch()

        self._multi_room_btn = QPushButton("进入多房间")
        self._multi_room_btn.setFixedSize(120, 36)
        self._multi_room_btn.clicked.connect(self.multi_room_requested.emit)
        hdr.addWidget(self._multi_room_btn)

        self._record_btn = QPushButton("进入直播录制")
        self._record_btn.setFixedSize(140, 36)
        self._record_btn.clicked.connect(self.record_requested.emit)
        hdr.addWidget(self._record_btn)

        tabs = QHBoxLayout()
        tabs.setSpacing(8)
        for txt in ["全部", "录制中", "已完成"]:
            tab = QLabel(txt)
            tab.setFixedSize(80, 36)
            tab.setAlignment(Qt.AlignCenter)
            tab.setStyleSheet(
                "padding:8px 14px;border:1px solid #d0d7de;border-radius:6px;font-size:13px;font-weight:500;"
                "background:#ffffff;"
            )
            tabs.addWidget(tab)
        hdr.addLayout(tabs)

        card.layout().addLayout(hdr)

        empty = QLabel("暂无录制任务\n前往“直播录制”页面，连接直播间并开始一次新的录制。")
        empty.setAlignment(Qt.AlignCenter)
        empty.setStyleSheet("color:#6b7280;font-size:13px;padding:32px 12px;")
        card.layout().addWidget(empty)

        main.addWidget(card)
        main.addStretch()

    def set_recording_count(self, count: int):
        self._rec_card.set_value(str(count))

    def set_total_duration(self, seconds: int):
        h = seconds // 3600
        m = (seconds % 3600) // 60
        s = seconds % 60
        self._dur_card.set_value(f"{h:02d}:{m:02d}:{s:02d}")

    def set_clip_count(self, count: int):
        self._clip_card.set_value(str(count))

    def set_stats(self, recording_count: int, total_duration: int, clip_count: int):
        self.set_recording_count(recording_count)
        self.set_total_duration(total_duration)
        self.set_clip_count(clip_count)

    def set_sessions(self, sessions: list[dict]) -> None:
        self._sessions = sessions
        self._session_count = len(sessions)
        self._render_sessions()

    def _render_sessions(self) -> None:
        """保留占位，后续接真实会话列表。"""

    def refresh(self):
        """刷新仪表盘数据。"""


class _StatCard(QFrame):
    def __init__(self, value: str, desc: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet("QFrame { border: 1px solid #d0d7de; border-radius: 6px; background: #ffffff; }")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(6)

        self._value_label = QLabel(value, self)
        self._value_label.setStyleSheet("font-size:24px;font-weight:700;")
        layout.addWidget(self._value_label)

        self._desc_label = QLabel(desc, self)
        self._desc_label.setStyleSheet("font-size:12px;color:#6b7280;")
        layout.addWidget(self._desc_label)

    def set_value(self, value: str) -> None:
        self._value_label.setText(value)


class _SectionCard(QFrame):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet("QFrame { border: 1px solid #d0d7de; border-radius: 6px; background: #ffffff; }")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)
