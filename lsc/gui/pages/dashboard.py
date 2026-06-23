"""Dashboard page with project overview and quick actions."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from lsc.gui.components.widgets import Card, EmptyState
from lsc.gui.theme import connect_theme_changed, get_theme


class _IconBadge(QFrame):
    """圆形图标徽标，用于卡片左侧。"""

    def __init__(self, bg_color: str, parent=None):
        super().__init__(parent)
        self._bg = QColor(bg_color)
        self.setFixedSize(40, 40)
        self.setFrameShape(QFrame.NoFrame)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(self._bg)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(self.rect())
        painter.end()


class _StatCard(Card):
    """仪表盘统计卡片。"""

    def __init__(self, label: str, value: str, accent_key: str, parent=None):
        super().__init__(parent)
        self._accent_key = accent_key
        self.setObjectName("dashboardStatCard")
        self.setMinimumHeight(96)

        self.layout.setSpacing(12)
        self.layout.setAlignment(Qt.AlignVCenter)

        # 左侧强调色竖条
        self._left_accent = QFrame(self)
        self._left_accent.setObjectName("dashboardStatAccentBar")
        self._left_accent.setFixedWidth(4)

        text = QVBoxLayout()
        text.setSpacing(4)
        text.setAlignment(Qt.AlignVCenter)
        self._value_lbl = QLabel(value)
        self._value_lbl.setObjectName("dashboardStatValue")
        self._label_lbl = QLabel(label)
        self._label_lbl.setObjectName("label_secondary")
        text.addWidget(self._value_lbl)
        text.addWidget(self._label_lbl)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(14)
        row.addWidget(self._left_accent)
        row.addLayout(text, 1)
        self.layout.addLayout(row)
        self._apply_style()

    def _apply_style(self) -> None:
        self._left_accent.setProperty("accent", self._accent_key)
        self._value_lbl.setProperty("accent", self._accent_key)
        self._repolish(self._left_accent)
        self._repolish(self._value_lbl)
        self._repolish(self)

    @staticmethod
    def _repolish(widget: QWidget) -> None:
        widget.style().unpolish(widget)
        widget.style().polish(widget)

    def set_value(self, value: str) -> None:
        """更新统计数值显示。"""
        self._value_lbl.setText(value)


class _ActionCard(Card):
    """快捷操作卡片。"""

    clicked = Signal()

    def __init__(self, title: str, desc: str, accent_key: str, is_primary: bool = False, parent=None):
        super().__init__(parent)
        self._enabled = True
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setObjectName(
            "dashboardActionCardPrimary" if is_primary else "dashboardActionCard"
        )
        self.setMinimumHeight(96)

        self.layout.setSpacing(6)

        self._title = QLabel(title)
        self._title.setObjectName(
            "dashboardActionCardPrimaryTitle" if is_primary else "dashboardActionCardTitle"
        )
        self._desc = QLabel(desc)
        self._desc.setObjectName(
            "dashboardActionCardPrimaryDesc" if is_primary else "dashboardActionCardDesc"
        )
        self._desc.setWordWrap(True)
        self.layout.addWidget(self._title)
        self.layout.addWidget(self._desc)
        self.layout.addStretch()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._enabled:
            self.clicked.emit()
        super().mousePressEvent(event)

    def click(self) -> None:
        """模拟点击，供测试和外部调用。"""
        if self._enabled:
            self.clicked.emit()

    def isEnabled(self) -> bool:
        return self._enabled

    def setEnabled(self, enabled: bool) -> None:
        self._enabled = enabled
        super().setEnabled(enabled)
        self.setCursor(
            Qt.CursorShape.PointingHandCursor if enabled else Qt.CursorShape.ArrowCursor
        )

    def _apply_style(self) -> None:
        """主题切换时刷新（样式全部由全局 QSS 处理）。"""


class _SessionCard(QFrame):
    """最近动态中的一条会话记录。"""

    def __init__(self, title: str, status: str, duration_text: str, path: str, parent=None):
        super().__init__(parent)
        self._status = status
        self.setObjectName("dashboardSessionCard")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(76)

        root = QHBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(14)

        # 左侧状态色竖条
        self._left_accent = QFrame()
        self._left_accent.setObjectName("dashboardSessionAccentBar")
        self._left_accent.setFixedWidth(3)
        root.addWidget(self._left_accent)

        self._badge = _IconBadge("#000000")
        root.addWidget(self._badge)

        text = QVBoxLayout()
        text.setSpacing(3)

        header = QHBoxLayout()
        header.setSpacing(8)
        self._title_lbl = QLabel(title)
        self._title_lbl.setObjectName("dashboardSessionTitle")
        header.addWidget(self._title_lbl)

        self._status_lbl = QLabel()
        self._status_lbl.setObjectName("dashboardSessionStatus")
        header.addWidget(self._status_lbl)
        header.addStretch()
        text.addLayout(header)

        self._dur_lbl = QLabel(duration_text)
        self._dur_lbl.setObjectName("label_secondary")
        text.addWidget(self._dur_lbl)

        self._path_lbl = QLabel(path)
        self._path_lbl.setObjectName("label_mono")
        self._path_lbl.setWordWrap(True)
        text.addWidget(self._path_lbl)

        root.addLayout(text, 1)
        connect_theme_changed(self._refresh_theme)
        self._apply_style()

    def _status_pair(self):
        if self._status == "recording":
            return ("accent_success", "accent_success_dim", "recording", "录制中")
        return ("accent_secondary", "accent_secondary_dim", "other", self._status)

    def _apply_style(self) -> None:
        c = get_theme()
        _, dim_key, status_prop, status_text = self._status_pair()
        self._badge._bg = QColor(getattr(c, dim_key))
        self._badge.update()
        self._status_lbl.setText(status_text)
        self._status_lbl.setProperty("status", status_prop)
        self._left_accent.setProperty("status", status_prop)
        self._repolish(self._status_lbl)
        self._repolish(self._left_accent)
        self._repolish(self)

    def _refresh_theme(self) -> None:
        self._apply_style()

    @staticmethod
    def _repolish(widget: QWidget) -> None:
        widget.style().unpolish(widget)
        widget.style().polish(widget)


class DashboardPage(QWidget):
    """仪表盘页面。"""

    record_requested = Signal()
    multi_room_requested = Signal()
    clips_requested = Signal()
    settings_requested = Signal()
    # 保留通用导航信号，供主窗口统一处理
    navigate_to = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._session_count = 0
        self._build()
        connect_theme_changed(self._refresh_theme)

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(24)
        layout.setAlignment(Qt.AlignTop)

        # Header
        header = QVBoxLayout()
        header.setSpacing(4)
        self._title = QLabel("仪表盘")
        self._title.setObjectName("page_title")
        header.addWidget(self._title)
        subtitle = QLabel("概览直播间状态、快捷操作与最近动态")
        subtitle.setObjectName("label_secondary")
        header.addWidget(subtitle)
        layout.addLayout(header)

        # Stats
        stats = QGridLayout()
        stats.setSpacing(12)
        self._stat_cards = [
            _StatCard("录制任务", "0", "accent_primary"),
            _StatCard("在线直播间", "0", "accent_success"),
            _StatCard("待导出切片", "0", "accent_warning"),
            _StatCard("存储使用", "0 GB", "accent_secondary"),
        ]
        for i, card in enumerate(self._stat_cards):
            stats.addWidget(card, 0, i)
        layout.addLayout(stats)

        # Quick actions
        actions_title = QLabel("快捷操作")
        actions_title.setObjectName("section_title")
        layout.addWidget(actions_title)

        actions = QGridLayout()
        actions.setSpacing(12)
        self._record_btn = _ActionCard(
            "开始录制", "添加直播间并启动录制任务", "accent_primary", is_primary=True
        )
        self._clips_btn = _ActionCard(
            "查看切片", "浏览已生成的精彩片段", "accent_success"
        )
        self._multi_room_btn = _ActionCard(
            "管理直播间", "查看与管理所有直播间", "accent_secondary"
        )
        self._settings_btn = _ActionCard(
            "应用设置", "调整输出目录、编码与主题", "accent_warning"
        )

        self._record_btn.clicked.connect(self._on_record_click)
        self._clips_btn.clicked.connect(self._on_clips_click)
        self._multi_room_btn.clicked.connect(self._on_multi_room_click)
        self._settings_btn.clicked.connect(self._on_settings_click)

        actions.addWidget(self._record_btn, 0, 0)
        actions.addWidget(self._clips_btn, 0, 1)
        actions.addWidget(self._multi_room_btn, 1, 0)
        actions.addWidget(self._settings_btn, 1, 1)
        layout.addLayout(actions)

        # Recent activity
        recent = Card()
        recent_title = QLabel("最近动态")
        recent_title.setObjectName("section_title")
        recent.add_widget(recent_title)

        self._session_container = QWidget()
        self._session_layout = QVBoxLayout(self._session_container)
        self._session_layout.setContentsMargins(0, 0, 0, 0)
        self._session_layout.setSpacing(8)

        self._empty = EmptyState(title="暂无动态", subtitle="开始录制后将在这里显示最近活动")
        self._session_layout.addWidget(self._empty)
        self._session_layout.addStretch()

        recent.add_widget(self._session_container)
        layout.addWidget(recent)

        layout.addStretch()

    def _on_record_click(self) -> None:
        self.record_requested.emit()
        self.navigate_to.emit("record")

    def _on_multi_room_click(self) -> None:
        self.multi_room_requested.emit()
        self.navigate_to.emit("workbench")

    def _on_clips_click(self) -> None:
        self.clips_requested.emit()
        self.navigate_to.emit("workbench")

    def _on_settings_click(self) -> None:
        self.settings_requested.emit()
        self.navigate_to.emit("settings")

    def set_sessions(self, sessions: list[dict]) -> None:
        """刷新最近动态列表。"""
        # 清空现有条目（保留 empty state 用于无数据时）
        while self._session_layout.count():
            item = self._session_layout.takeAt(0)
            w = item.widget()
            if w is not None and w is not self._empty:
                w.deleteLater()

        self._session_count = len(sessions)
        self._empty.setVisible(not sessions)

        for session in sessions:
            card = _SessionCard(
                title=session.get("title", "未知"),
                status=session.get("status", ""),
                duration_text=session.get("duration_text", ""),
                path=session.get("path", ""),
            )
            # 插入到 empty state 之后、stretch 之前
            self._session_layout.insertWidget(self._session_layout.count() - 1, card)

    def _refresh_theme(self) -> None:
        for card in self._stat_cards:
            card._apply_style()
        self._record_btn._apply_style()
        self._clips_btn._apply_style()
        self._multi_room_btn._apply_style()
        self._settings_btn._apply_style()
        # 刷新最近动态卡片
        for i in range(self._session_layout.count()):
            w = self._session_layout.itemAt(i).widget()
            if isinstance(w, _SessionCard):
                w._refresh_theme()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._update_storage_stat()

    def update_stats(self, recording: int = 0, connected: int = 0, clips: int = 0) -> None:
        """从多房间管理器刷新前三个统计卡(存储卡由 showEvent 扫描)。"""
        if len(self._stat_cards) >= 3:
            self._stat_cards[0].set_value(str(recording))
            self._stat_cards[1].set_value(str(connected))
            self._stat_cards[2].set_value(str(clips))

    def _update_storage_stat(self) -> None:
        """扫描输出目录，更新存储使用统计卡片。"""
        import os
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
        # 格式化显示
        if total_bytes >= 1024 * 1024 * 1024:
            text = f"{total_bytes / (1024**3):.1f} GB"
        elif total_bytes >= 1024 * 1024:
            text = f"{total_bytes / (1024**2):.0f} MB"
        elif total_bytes > 0:
            text = f"{total_bytes / 1024:.0f} KB"
        else:
            text = "0 GB"
        # 存储使用卡片是第 4 个 (index 3)
        if len(self._stat_cards) > 3:
            self._stat_cards[3].set_value(text)
