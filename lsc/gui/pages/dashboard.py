"""Dashboard page with room status overview and recording history."""
from __future__ import annotations

import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from lsc.gui.components.widgets import Card, EmptyState, PageHeader
from lsc.gui.theme import connect_theme_changed, get_theme


class _StatusDot(QWidget):
    """小型圆形状态指示点。"""

    _COLORS = {
        "recording": "accent_success",
        "connected": "accent_primary",
        "idle": "accent_warning",
        "error": "accent_error",
        "default": "text_tertiary",
    }

    def __init__(self, status: str = "default", parent=None):
        super().__init__(parent)
        self._status = status
        self.setFixedSize(10, 10)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def set_status(self, status: str) -> None:
        self._status = status
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = get_theme()
        color_key = self._COLORS.get(self._status, self._COLORS["default"])
        painter.setBrush(QColor(getattr(c, color_key)))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(self.rect())
        painter.end()


class _PlatformBadge(QLabel):
    """平台标签徽标。"""

    def __init__(self, platform: str = "", parent=None):
        super().__init__(platform, parent)
        self.setObjectName("dashboardPlatformBadge")
        self.setAlignment(Qt.AlignCenter)
        self.setFixedHeight(20)


class _StatusBadge(QLabel):
    """状态文字徽标。"""

    _STATUS_MAP = {
        "recording": ("录制中", "accent_success"),
        "connected": ("已连接", "accent_primary"),
        "idle": ("空闲", "accent_warning"),
        "error": ("错误", "accent_error"),
        "disconnected": ("未连接", "text_tertiary"),
    }

    def __init__(self, status: str = "idle", parent=None):
        super().__init__(parent)
        self.setObjectName("dashboardStatusBadge")
        self.setAlignment(Qt.AlignCenter)
        self.setFixedHeight(20)
        self.set_status(status)

    def set_status(self, status: str) -> None:
        text, prop = self._STATUS_MAP.get(status, (status, "text_tertiary"))
        self.setText(text)
        self.setProperty("status", prop)
        self.style().unpolish(self)
        self.style().polish(self)


class _RoomStatusRow(QFrame):
    """房间状态概览中的一行。"""

    def __init__(
        self,
        room_name: str,
        platform: str,
        status: str,
        duration_text: str,
        parent=None,
    ):
        super().__init__(parent)
        self.setObjectName("dashboardRoomStatusRow")
        self.setMinimumHeight(44)

        root = QHBoxLayout(self)
        root.setContentsMargins(14, 8, 14, 8)
        root.setSpacing(12)

        self._dot = _StatusDot(status)
        root.addWidget(self._dot)

        self._name_lbl = QLabel(room_name)
        self._name_lbl.setObjectName("dashboardRoomName")
        root.addWidget(self._name_lbl)

        self._platform_badge = _PlatformBadge(platform)
        root.addWidget(self._platform_badge)

        root.addStretch()

        self._status_badge = _StatusBadge(status)
        root.addWidget(self._status_badge)

        self._dur_lbl = QLabel(duration_text)
        self._dur_lbl.setObjectName("label_mono")
        self._dur_lbl.setMinimumWidth(60)
        root.addWidget(self._dur_lbl)

    def update_data(
        self,
        room_name: str,
        platform: str,
        status: str,
        duration_text: str,
    ) -> None:
        self._name_lbl.setText(room_name)
        self._platform_badge.setText(platform)
        self._dot.set_status(status)
        self._status_badge.set_status(status)
        self._dur_lbl.setText(duration_text)


class _HistoryRow(QFrame):
    """录制历史中的一行记录。"""

    clicked = Signal()

    def __init__(
        self,
        room_name: str,
        platform: str,
        duration_text: str,
        file_size_text: str,
        recorded_at: str,
        parent=None,
    ):
        super().__init__(parent)
        self.setObjectName("dashboardHistoryRow")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(48)

        root = QHBoxLayout(self)
        root.setContentsMargins(14, 10, 14, 10)
        root.setSpacing(12)

        icon_lbl = QLabel("\U0001f3a5")
        icon_lbl.setFixedWidth(20)
        icon_lbl.setAlignment(Qt.AlignCenter)
        root.addWidget(icon_lbl)

        info_box = QVBoxLayout()
        info_box.setSpacing(2)
        self._title_lbl = QLabel()
        self._title_lbl.setObjectName("dashboardHistoryTitle")
        info_box.addWidget(self._title_lbl)

        self._meta_lbl = QLabel()
        self._meta_lbl.setObjectName("label_secondary")
        info_box.addWidget(self._meta_lbl)

        root.addLayout(info_box, 1)

        self._time_lbl = QLabel()
        self._time_lbl.setObjectName("label_tertiary")
        self._time_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        root.addWidget(self._time_lbl)

        self._session: dict = {}
        self.set_data(room_name, platform, duration_text, file_size_text, recorded_at)

    def set_data(
        self,
        room_name: str,
        platform: str,
        duration_text: str,
        file_size_text: str,
        recorded_at: str,
    ) -> None:
        self._title_lbl.setText(room_name)
        parts = [p for p in [platform, duration_text, file_size_text] if p]
        self._meta_lbl.setText(" · ".join(parts))
        self._time_lbl.setText(recorded_at)

    def set_session(self, session: dict) -> None:
        self._session = session

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._session:
            self.clicked.emit()
        super().mousePressEvent(event)


class _StorageBar(QWidget):
    """底部存储使用量条。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 4, 0, 0)
        root.setSpacing(10)

        title = QLabel("存储使用")
        title.setObjectName("label_secondary")
        root.addWidget(title)

        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setTextVisible(False)
        self._progress.setFixedHeight(6)
        root.addWidget(self._progress, 1)

        self._value_lbl = QLabel("0 GB / —")
        self._value_lbl.setObjectName("label_mono")
        root.addWidget(self._value_lbl)

    def set_value(self, used_bytes: int, total_bytes: int = 0) -> None:
        used_text = self._format_bytes(used_bytes)
        if total_bytes > 0:
            total_text = self._format_bytes(total_bytes)
            pct = min(int(used_bytes / total_bytes * 100), 100)
            self._progress.setValue(pct)
            self._value_lbl.setText(f"{used_text} / {total_text}")
        else:
            self._progress.setValue(0)
            self._value_lbl.setText(f"{used_text} / —")

    @staticmethod
    def _format_bytes(n: int) -> str:
        if n >= 1024 ** 3:
            return f"{n / (1024 ** 3):.1f} GB"
        if n >= 1024 ** 2:
            return f"{n / (1024 ** 2):.0f} MB"
        if n > 0:
            return f"{n / 1024:.0f} KB"
        return "0 GB"


class DashboardPage(QWidget):
    """仪表盘页面。"""

    navigate_to = Signal(str)
    history_item_clicked = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._room_rows: list[_RoomStatusRow] = []
        self._history_rows: list[_HistoryRow] = []
        self._history_items: list[dict] = []
        self._build()
        connect_theme_changed(self._refresh_theme)

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.setAlignment(Qt.AlignTop)

        self._page_header = PageHeader("仪表盘", "概览直播间状态与录制历史")
        self._title = self._page_header._title_label
        layout.addWidget(self._page_header)

        content = QWidget()
        content.setStyleSheet("background:transparent;")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(20, 16, 20, 20)
        content_layout.setSpacing(20)
        content_layout.setAlignment(Qt.AlignTop)
        layout.addWidget(content, 1)

        # ── 房间状态概览 ──
        room_card = Card()
        room_title = QLabel("房间状态概览")
        room_title.setObjectName("section_title")
        room_card.add_widget(room_title)

        self._room_container = QWidget()
        self._room_layout = QVBoxLayout(self._room_container)
        self._room_layout.setContentsMargins(0, 0, 0, 0)
        self._room_layout.setSpacing(2)

        self._room_empty = EmptyState(
            title="暂无房间", subtitle="添加直播间后将在这里显示状态概览"
        )
        self._room_layout.addWidget(self._room_empty)
        self._room_layout.addStretch()

        room_card.add_widget(self._room_container)
        content_layout.addWidget(room_card)

        # ── 最近录制历史 ──
        history_card = Card()
        history_title = QLabel("最近录制历史")
        history_title.setObjectName("section_title")
        history_card.add_widget(history_title)

        self._history_container = QWidget()
        self._history_layout = QVBoxLayout(self._history_container)
        self._history_layout.setContentsMargins(0, 0, 0, 0)
        self._history_layout.setSpacing(2)

        self._history_empty = EmptyState(
            title="暂无录制记录", subtitle="录制完成后将在这里显示历史记录"
        )
        self._history_layout.addWidget(self._history_empty)
        self._history_layout.addStretch()

        history_card.add_widget(self._history_container)
        content_layout.addWidget(history_card)

        # ── 存储使用 ──
        self._storage_bar = _StorageBar()
        content_layout.addWidget(self._storage_bar)

        content_layout.addStretch()

    # ── 公开 API ──

    def set_sessions(self, sessions: list[dict]) -> None:
        """更新房间状态概览列表。"""
        self._clear_layout_rows(self._room_layout, self._room_rows, self._room_empty)
        self._room_rows.clear()

        self._room_empty.setVisible(not sessions)

        for s in sessions:
            row = _RoomStatusRow(
                room_name=s.get("title", s.get("room_name", "未知")),
                platform=s.get("platform", ""),
                status=s.get("status", "idle"),
                duration_text=s.get("duration_text", ""),
            )
            self._room_rows.append(row)
            self._room_layout.insertWidget(self._room_layout.count() - 1, row)

    def set_history(self, items: list[dict]) -> None:
        """设置最近录制历史列表。"""
        self._history_items = list(items)
        self._clear_layout_rows(
            self._history_layout, self._history_rows, self._history_empty
        )
        self._history_rows.clear()

        self._history_empty.setVisible(not items)

        for item in items:
            row = _HistoryRow(
                room_name=item.get("room_name", item.get("title", "未知")),
                platform=item.get("platform", ""),
                duration_text=item.get("duration_text", ""),
                file_size_text=item.get("file_size_text", ""),
                recorded_at=item.get("recorded_at", ""),
            )
            row.set_session(item)
            row.clicked.connect(lambda it=item: self.history_item_clicked.emit(it))
            self._history_rows.append(row)
            self._history_layout.insertWidget(self._history_layout.count() - 1, row)

    def update_stats(self, recording: int = 0, connected: int = 0, clips: int = 0) -> None:
        """从多房间管理器刷新房间状态概览。"""
        pass

    # ── 内部方法 ──

    @staticmethod
    def _clear_layout_rows(
        layout: QVBoxLayout, rows: list, empty_widget: QWidget
    ) -> None:
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w is not None and w is not empty_widget:
                w.deleteLater()

    def _refresh_theme(self) -> None:
        for row in self._room_rows:
            row.update()
        for row in self._history_rows:
            row.update()
        self._storage_bar.update()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._update_storage()

    def _update_storage(self) -> None:
        """扫描输出目录，更新存储使用条。"""
        from lsc.config import load_config

        output_dir = load_config().output_dir
        total_bytes = 0
        if output_dir and os.path.isdir(output_dir):
            try:
                for dirpath, _dirnames, filenames in os.walk(output_dir):
                    for f in filenames:
                        fp = os.path.join(dirpath, f)
                        try:
                            total_bytes += os.path.getsize(fp)
                        except OSError:
                            pass
            except OSError:
                pass
        self._storage_bar.set_value(total_bytes)
