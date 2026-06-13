"""
Dashboard page - 1:1 replica
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ..components.widgets import Card, EmptyState, FadeInWidget, StatCard
from ..theme import get_theme


class DashboardPage(QWidget):
    record_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build()

    def _build(self):
        c = get_theme()
        main = QVBoxLayout(self)
        main.setContentsMargins(24, 24, 24, 24)
        main.setSpacing(24)

        # Stats grid - stretch to fill width
        stats = QHBoxLayout()
        stats.setContentsMargins(0, 0, 0, 0)
        stats.setSpacing(20)

        self._rec_card = StatCard(c.accent_primary, "record")
        self._rec_card.set_value("0")
        self._rec_card.set_desc("正在录制")
        fade1 = FadeInWidget(delay_ms=50)
        fade1_layout = QHBoxLayout(fade1)
        fade1_layout.setContentsMargins(0, 0, 0, 0)
        fade1_layout.addWidget(self._rec_card)
        stats.addWidget(fade1, 1)

        self._dur_card = StatCard(c.accent_secondary, "clock")
        self._dur_card.set_value("00:00:00")
        self._dur_card.set_desc("今日录制时长")
        fade2 = FadeInWidget(delay_ms=100)
        fade2_layout = QHBoxLayout(fade2)
        fade2_layout.setContentsMargins(0, 0, 0, 0)
        fade2_layout.addWidget(self._dur_card)
        stats.addWidget(fade2, 1)

        self._clip_card = StatCard(c.accent_success, "download")
        self._clip_card.set_value("0")
        self._clip_card.set_desc("已导出片段")
        fade3 = FadeInWidget(delay_ms=150)
        fade3_layout = QHBoxLayout(fade3)
        fade3_layout.setContentsMargins(0, 0, 0, 0)
        fade3_layout.addWidget(self._clip_card)
        stats.addWidget(fade3, 1)

        main.addLayout(stats)

        # Sessions
        card = Card()

        # Header
        hdr = QHBoxLayout()
        tl = QVBoxLayout()
        t = QLabel("录制会话")
        t.setStyleSheet("font-size:15px;font-weight:600;letter-spacing:-0.01em;")
        tl.addWidget(t)
        st = QLabel("当前和近期的录制任务")
        st.setObjectName("label_tertiary")
        st.setStyleSheet("font-size:12px;")
        tl.addWidget(st)
        hdr.addLayout(tl)
        hdr.addStretch()

        self._record_btn = QPushButton("进入直播录制")
        self._record_btn.setObjectName("btnPrimary")
        self._record_btn.setFixedSize(140, 36)
        self._record_btn.clicked.connect(self.record_requested.emit)
        hdr.addWidget(self._record_btn)

        tabs = QHBoxLayout()
        tabs.setSpacing(8)
        for txt in ["全部", "录制中", "已完成"]:
            tab = QLabel(txt)
            tab.setObjectName("tab_active" if txt == "全部" else "tab_inactive")
            tab.setFixedSize(80, 36)
            tab.setAlignment(Qt.AlignCenter)
            tab.setStyleSheet("padding:8px 14px;border-radius:6px;font-size:13px;font-weight:500;")
            tabs.addWidget(tab)
        hdr.addLayout(tabs)
        card.add_layout(hdr)

        # Empty state
        empty = EmptyState("暂无录制任务", "前往「直播录制」页面，连接直播间并开始一次新的录制", icon_type="record")
        card.add_widget(empty)

        main.addWidget(card)
        main.addStretch()

    def set_recording_count(self, count: int):
        """Update the number of active recordings."""
        self._rec_card.set_value(str(count))

    def set_total_duration(self, seconds: int):
        """Update today's total recording duration."""
        h = seconds // 3600
        m = (seconds % 3600) // 60
        s = seconds % 60
        self._dur_card.set_value(f"{h:02d}:{m:02d}:{s:02d}")

    def set_clip_count(self, count: int):
        """Update the number of exported clips."""
        self._clip_card.set_value(str(count))

    def set_stats(self, recording_count: int, total_duration: int, clip_count: int):
        """Update all dashboard counters from the active recording page."""
        self.set_recording_count(recording_count)
        self.set_total_duration(total_duration)
        self.set_clip_count(clip_count)

    def set_sessions(self, sessions: list[dict]) -> None:
        """Update the session list display."""
        self._sessions = sessions
        self._session_count = len(sessions)
        self._render_sessions()

    def _render_sessions(self) -> None:
        """Render the session list (placeholder for future implementation)."""

    def refresh(self):
        """Refresh dashboard data. Override or connect to data source."""
        # TODO: Connect to RecordingSession/LscDatabase
