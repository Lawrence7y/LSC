"""Status bar and bottom bar for the multi-room workbench."""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from lsc.gui.components.control_bar import ControlBar
from lsc.gui.theme import connect_theme_changed, get_theme, is_dark


class StatusBar(QWidget):
    """底部状态栏，内容溢出时显示水平滚动条。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(36)
        self._message_timer = QTimer(self)
        self._message_timer.setSingleShot(True)
        self._message_timer.timeout.connect(lambda: self._message_label.setText(""))
        self._build()

    def _build(self):
        self.setObjectName("multiRoomStatusBar")
        self._stat_dots: list[QLabel] = []

        # 外层布局：QScrollArea 包裹内容，溢出时显示水平滚动条
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }"
                             "QScrollBar:horizontal { height: 4px; background: transparent; }"
                             "QScrollBar::handle:horizontal { background: rgba(128,128,128,0.4); border-radius: 2px; min-width: 30px; }"
                             "QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { height: 0; }")

        content = QWidget()
        content.setStyleSheet("background: transparent;")
        layout = QHBoxLayout(content)
        layout.setContentsMargins(20, 0, 20, 0)
        layout.setSpacing(16)

        self._total = self._make_stat("房间", "0")
        self._connected = self._make_stat("已连接", "0", "statusBarDotSuccess")
        self._recording = self._make_stat("录制中", "0", "statusBarDotError")
        self._preview = self._make_stat("预览中", "0", "statusBarDotPrimary")

        for s in (self._total, self._connected, self._recording, self._preview):
            layout.addWidget(s)

        layout.addStretch()

        # 导出进度条（默认隐藏）
        self._progress_bar = QProgressBar()
        self._progress_bar.setFixedWidth(160)
        self._progress_bar.setFixedHeight(14)
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setTextVisible(True)
        self._progress_bar.setFormat("导出中 %p%")
        self._progress_bar.setObjectName("progressBar")
        self._progress_bar.setVisible(False)
        layout.addWidget(self._progress_bar)

        self._message_label = QLabel("")
        self._message_label.setObjectName("statusBarMessage")
        layout.addWidget(self._message_label)

        self._error_label = QLabel("")
        self._error_label.setObjectName("statusBarError")
        layout.addWidget(self._error_label)

        scroll.setWidget(content)
        outer.addWidget(scroll)

    def _make_stat(self, label: str, value: str, dot_name: str = "") -> QWidget:
        w = QWidget()
        w.setObjectName("statusBarStat")
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(5)
        if dot_name:
            dot = QLabel("●")
            dot.setObjectName(dot_name)
            self._stat_dots.append(dot)
            lay.addWidget(dot)
        lbl = QLabel(label)
        lbl.setObjectName("statusBarLabel")
        val = QLabel(value)
        val.setObjectName("statusBarValue")
        lay.addWidget(lbl)
        lay.addWidget(val)
        return w

    def update_stats(self, total: int, connected: int, recording: int, previewing: int, errors: int):
        for w, v in zip(
            [self._total, self._connected, self._recording, self._preview],
            [total, connected, recording, previewing],
        ):
            val = w.findChild(QLabel, "statusBarValue")
            if val:
                val.setText(str(v))
        self._error_label.setText(f"{errors} 个房间有错误" if errors > 0 else "")

    def refresh_theme(self) -> None:
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def show_message(self, text: str, timeout_ms: int = 5000) -> None:
        """Show a temporary status message (toast-like)."""
        self._message_timer.stop()
        self._message_label.setText(text)
        if timeout_ms > 0:
            self._message_timer.start(timeout_ms)

    def show_progress(self, percent: float) -> None:
        """显示导出进度条。"""
        self._progress_bar.setValue(int(percent))
        self._progress_bar.setVisible(True)

    def hide_progress(self) -> None:
        """隐藏导出进度条。"""
        self._progress_bar.setVisible(False)
        self._progress_bar.setValue(0)


class _BottomBar(QWidget):
    """底部控制栏与状态栏合并后的圆角容器（风格同录制页 ControlBar）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build()
        connect_theme_changed(self.refresh_theme)

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 6, 12, 6)
        root.setSpacing(6)

        self._controls = ControlBar()
        self._controls.set_draw_background(False)
        root.addWidget(self._controls)

        self._status = StatusBar()
        root.addWidget(self._status)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = get_theme()
        p.setBrush(QColor(c.bg_secondary))
        # 亮色模式下白底容器在浅灰页面上几乎无边，使用更强的描边保证可见
        pen_color = c.border_subtle if is_dark() else c.border_strong
        p.setPen(QColor(pen_color))
        p.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 12, 12)
        p.end()

    def refresh_theme(self) -> None:
        self._controls.refresh_theme()
        self._status.refresh_theme()
        self.update()

    @property
    def controls(self) -> ControlBar:
        return self._controls

    @property
    def status(self) -> StatusBar:
        return self._status
