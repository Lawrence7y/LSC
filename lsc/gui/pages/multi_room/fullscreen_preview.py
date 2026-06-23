"""Fullscreen preview for the multi-room workbench."""
from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QSlider, QCheckBox, QPushButton,
)
from PySide6.QtGui import QPainter, QColor, QPen, QPolygonF, QShortcut, QKeySequence
from PySide6.QtCore import Qt, QObject, QTimer, QEvent, QPointF, Signal

from lsc.utils.helpers import fmt_time

if TYPE_CHECKING:
    from lsc.gui.multi_room.manager import MultiRoomManager
    from lsc.gui.pages.multi_room.page import MultiRoomPage


class _FullscreenIconButton(QPushButton):
    """字体无关的全屏播放器图标按钮，避免系统缺字导致图标乱码。"""

    def __init__(self, icon_kind: str, parent=None) -> None:
        super().__init__("", parent)
        self._icon_kind = icon_kind
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def icon_kind(self) -> str:
        return self._icon_kind

    def set_icon_kind(self, icon_kind: str) -> None:
        if self._icon_kind == icon_kind:
            return
        self._icon_kind = icon_kind
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        bg = QColor(0, 0, 0, 165 if self.underMouse() else 120)
        p.setBrush(bg)
        p.setPen(QColor(255, 255, 255, 42))
        p.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 8, 8)

        pen = QPen(QColor(255, 255, 255, 238), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.setBrush(QColor(255, 255, 255, 238))
        cx = self.width() / 2
        cy = self.height() / 2

        if self._icon_kind == "play":
            p.drawPolygon(
                QPolygonF(
                    [
                        QPointF(cx - 5, cy - 8),
                        QPointF(cx - 5, cy + 8),
                        QPointF(cx + 8, cy),
                    ]
                )
            )
        elif self._icon_kind == "pause":
            p.drawRoundedRect(int(cx - 7), int(cy - 8), 4, 16, 1, 1)
            p.drawRoundedRect(int(cx + 3), int(cy - 8), 4, 16, 1, 1)
        elif self._icon_kind == "minimize":
            p.drawLine(int(cx - 8), int(cy + 6), int(cx + 8), int(cy + 6))
        elif self._icon_kind == "exit_fullscreen":
            left, right = int(cx - 10), int(cx + 10)
            top, bottom = int(cy - 8), int(cy + 8)
            mid_left, mid_right = int(cx - 3), int(cx + 3)
            mid_top, mid_bottom = int(cy - 2), int(cy + 2)
            p.drawLine(left, top, mid_left, top)
            p.drawLine(left, top, left, mid_top)
            p.drawLine(right, top, mid_right, top)
            p.drawLine(right, top, right, mid_top)
            p.drawLine(left, bottom, mid_left, bottom)
            p.drawLine(left, bottom, left, mid_bottom)
            p.drawLine(right, bottom, mid_right, bottom)
            p.drawLine(right, bottom, right, mid_bottom)
        p.end()


class _FullscreenActivityFilter(QObject):
    """捕获全屏窗口和控件上的鼠标活动，用于唤醒自动隐藏的底栏。"""

    def __init__(self, on_activity, parent=None) -> None:
        super().__init__(parent)
        self._on_activity = on_activity

    def eventFilter(self, obj, event) -> bool:
        if event.type() in (
            QEvent.Type.MouseMove,
            QEvent.Type.Enter,
            QEvent.Type.HoverMove,
            QEvent.Type.MouseButtonPress,
        ):
            self._on_activity()
        return super().eventFilter(obj, event)


class FullscreenPreview(QObject):
    """全屏预览封装类，管理全屏窗口的生命周期和控制逻辑。"""

    closed = Signal()  # 全屏关闭信号

    def __init__(self, room_id: str, widget: QWidget, card: QWidget,
                 manager: "MultiRoomManager", parent_page: "MultiRoomPage"):
        super().__init__(parent_page)
        self._room_id = room_id
        self._widget = widget
        self._card = card
        self._manager = manager
        self._page = parent_page
        self._active = False
        self._win: QWidget | None = None
        self._auto_hide_timer: QTimer | None = None
        self._fullscreen_timer: QTimer | None = None
        self._controls: QWidget | None = None
        self._activity_filter: _FullscreenActivityFilter | None = None
        self._syncing_progress = {"active": False}

        self._build_window()

    def _build_window(self) -> None:
        """构建全屏窗口和控件。"""
        room = self._manager.get_room(self._room_id)
        if room is None:
            return

        self._win = QWidget(self._page, Qt.Window)
        self._win.setWindowTitle(f"全屏预览 - {room.streamer_name or room.platform_name or room.room_url}")
        self._win.setObjectName("fullscreenPreviewWindow")
        self._win.setWindowFlags(Qt.Window | Qt.WindowMinimizeButtonHint | Qt.WindowCloseButtonHint)

        lay = QVBoxLayout(self._win)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self._win.setMouseTracking(True)

        surface = QWidget(self._win)
        surface.setObjectName("fullscreenPreviewSurface")
        surface.setMouseTracking(True)
        surface_layout = QVBoxLayout(surface)
        surface_layout.setContentsMargins(0, 0, 0, 0)
        surface_layout.setSpacing(0)

        self._widget.setMouseTracking(True)
        self._widget.setParent(surface)
        self._widget.setMinimumSize(0, 0)
        self._widget.show()
        surface_layout.addWidget(self._widget, 1)
        rebind_fn = getattr(self._widget, "rebind_video_output", None)
        if callable(rebind_fn):
            rebind_fn()
        lay.addWidget(surface, 1)

        controls_height = 74
        self._controls = QWidget(surface)
        self._controls.setObjectName("fullscreenPlayerControls")
        self._controls.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._controls.setMouseTracking(True)
        self._controls.setFixedHeight(controls_height)
        controls_layout = QVBoxLayout(self._controls)
        controls_layout.setContentsMargins(14, 0, 14, 9)
        controls_layout.setSpacing(4)

        self._progress = QSlider(Qt.Orientation.Horizontal, self._controls)
        self._progress.setObjectName("fullscreenProgressSlider")
        self._progress.setMouseTracking(True)
        self._progress.setMaximumHeight(12)
        self._progress.setRange(0, 0)
        controls_layout.addWidget(self._progress)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)

        self._play_btn = _FullscreenIconButton("pause", self._controls)
        self._play_btn.setObjectName("fullscreenPlayButton")
        self._play_btn.setFixedSize(40, 34)
        self._play_btn.setToolTip("播放/暂停")
        row.addWidget(self._play_btn)

        self._time_label = QLabel("00:00 / 00:00", self._controls)
        self._time_label.setObjectName("fullscreenTimeLabel")
        row.addWidget(self._time_label)
        row.addStretch(1)

        self._mute_btn = QCheckBox("静音", self._controls)
        self._mute_btn.setObjectName("fullscreenMuteButton")
        self._mute_btn.setChecked(bool(room.preview_muted))
        row.addWidget(self._mute_btn)

        min_btn = _FullscreenIconButton("minimize", self._controls)
        min_btn.setObjectName("fullscreenMinimizeButton")
        min_btn.setFixedSize(40, 34)
        min_btn.setToolTip("最小化")
        min_btn.clicked.connect(self._win.showMinimized)
        row.addWidget(min_btn)

        exit_btn = _FullscreenIconButton("exit_fullscreen", self._controls)
        exit_btn.setObjectName("fullscreenExitButton")
        exit_btn.setFixedSize(40, 34)
        exit_btn.setToolTip("退出全屏")
        exit_btn.clicked.connect(self._win.close)
        row.addWidget(exit_btn)

        controls_layout.addLayout(row)

        # Connect signals
        self._play_btn.clicked.connect(self._toggle_play)
        self._mute_btn.toggled.connect(self._set_muted)
        self._progress.valueChanged.connect(self._seek)

        # Timers
        self._fullscreen_timer = QTimer(self._win)
        self._fullscreen_timer.setInterval(500)
        self._fullscreen_timer.timeout.connect(self._sync_controls)
        self._fullscreen_timer.start()

        self._auto_hide_timer = QTimer(self._win)
        self._auto_hide_timer.setSingleShot(True)
        self._auto_hide_timer.setInterval(2400)
        self._auto_hide_timer.timeout.connect(self._hide_controls)

        # Activity filter
        self._activity_filter = _FullscreenActivityFilter(self._show_controls, self._win)
        for watched in (self._win, surface, self._widget, self._controls,
                       self._progress, self._play_btn, self._mute_btn, min_btn, exit_btn):
            watched.installEventFilter(self._activity_filter)

        # ESC shortcut
        esc_shortcut = QShortcut(QKeySequence(Qt.Key_Escape), self._win)
        esc_shortcut.activated.connect(self._win.close)

        # Key press override
        def _key_press_event(event):
            if event.key() == Qt.Key_Escape:
                event.accept()
                self._win.close()
                return
            QWidget.keyPressEvent(self._win, event)

        self._win.keyPressEvent = _key_press_event

        # Resize event for controls placement
        def _place_controls():
            self._controls.setGeometry(
                0,
                max(0, surface.height() - controls_height),
                max(1, surface.width()),
                controls_height,
            )
            self._controls.raise_()

        def _surface_resize_event(event):
            QWidget.resizeEvent(surface, event)
            _place_controls()

        surface.resizeEvent = _surface_resize_event

        # Close event
        def _close_event(event):
            self._cleanup()
            QWidget.closeEvent(self._win, event)

        self._win.closeEvent = _close_event

        # Initial sync
        self._sync_controls()
        _place_controls()
        self._auto_hide_timer.start()

        self._active = True

    def is_active(self) -> bool:
        """返回全屏是否处于活动状态。"""
        return self._active

    def window(self) -> QWidget | None:
        """返回底层的 QWidget 窗口。"""
        return self._win

    def _show_controls(self, controls=None) -> None:
        """显示控制条。"""
        if self._controls:
            self._controls.setVisible(True)
            self._controls.setFixedHeight(74)
            # Re-place controls
            surface = self._win.findChild(QWidget, "fullscreenPreviewSurface")
            if surface:
                self._controls.setGeometry(
                    0,
                    max(0, surface.height() - 74),
                    max(1, surface.width()),
                    74,
                )
                self._controls.raise_()
            if self._auto_hide_timer:
                self._auto_hide_timer.start()

    def _hide_controls(self) -> None:
        """隐藏控制条。"""
        if self._controls:
            self._controls.setVisible(False)

    def _sync_controls(self) -> None:
        """同步控制条状态。"""
        room = self._manager.get_room(self._room_id)
        if room is None:
            return

        pos_fn = getattr(self._widget, "position_sec", None)
        dur_fn = getattr(self._widget, "duration_sec", None)

        position = 0.0
        if callable(pos_fn):
            try:
                position = float(pos_fn() or 0.0)
            except Exception:
                pass

        duration = 0.0
        if callable(dur_fn):
            try:
                duration = float(dur_fn() or 0.0)
            except Exception:
                pass

        if duration <= 0:
            duration = self._manager.get_preview_duration(self._room_id)
        if position <= 0:
            position = self._manager.get_preview_position(self._room_id)

        self._syncing_progress["active"] = True
        self._progress.setRange(0, max(0, int(duration)))
        self._progress.setValue(max(0, int(position)))
        self._syncing_progress["active"] = False

        self._time_label.setText(f"{fmt_time(position)} / {fmt_time(duration)}")
        self._play_btn.set_icon_kind("play" if room.preview_paused else "pause")
        self._mute_btn.blockSignals(True)
        self._mute_btn.setChecked(bool(room.preview_muted))
        self._mute_btn.blockSignals(False)

    def _toggle_play(self) -> None:
        """切换播放/暂停。"""
        room = self._manager.get_room(self._room_id)
        if room is None:
            return

        if room.preview_paused:
            self._manager.resume_preview(self._room_id)
        else:
            self._manager.pause_preview(self._room_id)
        self._sync_controls()
        self._page._refresh_card(self._room_id)

    def _set_muted(self, muted: bool) -> None:
        """设置静音状态。"""
        self._manager.set_preview_muted(self._room_id, muted)
        self._sync_controls()
        self._page._refresh_card(self._room_id)

    def _seek(self, value: int) -> None:
        """Seek 到指定位置。"""
        if self._syncing_progress["active"]:
            return

        seek_to_fn = getattr(self._widget, "seek_to", None)
        if callable(seek_to_fn):
            seek_to_fn(float(value))
        else:
            seek_fn = getattr(self._widget, "seek", None)
            if callable(seek_fn):
                seek_fn(float(value))

        room = self._manager.get_room(self._room_id)
        if room:
            controller = getattr(room, "controller", None)
            if controller is not None:
                controller.current_sec = float(value)

        self._sync_controls()
        self._page._update_card_timeline(self._room_id)

    def _cleanup(self) -> None:
        """清理资源并恢复预览 widget。"""
        if not self._active:
            return

        self._active = False

        if self._auto_hide_timer:
            self._auto_hide_timer.stop()
        if self._fullscreen_timer:
            self._fullscreen_timer.stop()

        # Restore widget to card
        self._widget.setParent(self._card)
        self._card.set_preview_widget(self._widget)

        # Clear page references
        self._page._fullscreen_window = None
        self._page._controls.set_fullscreen(False)

        self.closed.emit()

    def close(self) -> None:
        """关闭全屏窗口。"""
        if self._win:
            self._win.close()
