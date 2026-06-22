"""多直播间工作台的房间卡片组件。"""
from __future__ import annotations

from PySide6.QtCore import (
    QPropertyAnimation,
    QEvent,
    QEasingCurve,
    QParallelAnimationGroup,
    QPointF,
    QRectF,
    QSize,
    Qt,
    Signal,
    Property,
)
from PySide6.QtGui import QBrush, QColor, QMouseEvent, QPainter, QPen
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from lsc.gui.multi_room.session import RoomSession
from lsc.gui.theme import connect_theme_changed, get_theme, is_dark

_PLATFORM_COLORS: dict[str, str] = {
    "douyin": "#111111",
    "bilibili": "#00A1D6",
    "huya": "#FF9500",
    "custom": "#6b7280",
}

_STATUS_COLORS_ATTR: dict[str, str] = {
    "idle": "text_tertiary",
    "connected": "accent_success",
    "recording": "accent_warning",
    "error": "accent_error",
}


class _StatusDot(QWidget):
    """自绘状态指示圆点，支持脉冲动画。"""

    def __init__(self, size: int = 10, parent=None):
        super().__init__(parent)
        self._color = QColor(get_theme().text_tertiary)
        self._pulse = 0.0
        self.setFixedSize(size + 6, size + 6)
        self._dot_size = size

    def set_color(self, color: str) -> None:
        self._color = QColor(color)
        self.update()

    def set_pulse(self, v: float) -> None:
        self._pulse = v
        self.update()

    def get_pulse(self) -> float:
        return self._pulse

    pulse = Property(float, get_pulse, set_pulse)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        cx = self.width() / 2
        cy = self.height() / 2
        r = self._dot_size / 2
        if self._pulse > 0:
            glow = QColor(self._color)
            glow.setAlphaF(0.3 * self._pulse)
            p.setBrush(QBrush(glow))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(int(cx - r - 2), int(cy - r - 2), int(self._dot_size + 4), int(self._dot_size + 4))
        p.setBrush(QBrush(self._color))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(int(cx - r), int(cy - r), self._dot_size, self._dot_size)
        p.end()


class _PlatformTag(QWidget):
    """平台标签：SVG 图标 + 文字，使用 QLabel 布局。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._bg = QColor("#555")
        self._icon_key = "custom"
        self._text = ""
        self.setFixedHeight(22)
        # 使用 QHBoxLayout 放置图标 + 文字
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(5, 0, 7, 0)
        self._layout.setSpacing(3)
        self._icon_lbl = QLabel(self)
        self._icon_lbl.setFixedSize(12, 12)
        self._text_lbl = QLabel(self)
        self._text_lbl.setStyleSheet("color: #ffffff; font-size: 9pt; background: transparent;")
        self._layout.addWidget(self._icon_lbl)
        self._layout.addWidget(self._text_lbl)

    def set_platform(self, key: str, display_name: str) -> None:
        from lsc.gui.components.platform_icons import render_platform_icon

        c = get_theme()
        brand = _PLATFORM_COLORS.get(key, "#6b7280")
        # 在深色模式下，黑色/深色品牌标签会融入背景，使用深色但可辨的灰
        if key == "douyin" and is_dark():
            brand = c.bg_elevated
        self._bg = QColor(brand)
        self._icon_key = key
        self._text = display_name or key
        self._icon_lbl.setPixmap(render_platform_icon(key, "#ffffff", 12))
        self._text_lbl.setText(self._text)
        # 宽度自适应
        fm = self._text_lbl.fontMetrics()
        w = fm.horizontalAdvance(self._text) + 12 + 12 + 3  # 文字 + 图标 + 间距 + padding
        self.setFixedWidth(max(w, 40))
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(QBrush(self._bg))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(0, 0, self.width(), self.height(), 4, 4)
        p.end()


class _PreviewArea(QFrame):
    """预览区域：承载占位图/嵌入预览，并在右上角叠加徽章。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._badge_widget: QWidget | None = None
        self._corner_widget: QWidget | None = None
        self._controls_widget: QWidget | None = None
        self._overlay_expanded = False
        self.setObjectName("previewArea")
        self.setMouseTracking(True)

    def set_badge_widget(self, widget: QWidget) -> None:
        self._badge_widget = widget
        widget.setParent(self)
        widget.show()
        self._update_badge_geometry()

    def set_corner_widget(self, widget: QWidget) -> None:
        self._corner_widget = widget
        widget.setParent(self)
        widget.show()
        self._update_overlay_geometry()

    def set_controls_widget(self, widget: QWidget) -> None:
        self._controls_widget = widget
        widget.setParent(self)
        widget.show()
        widget.installEventFilter(self)
        self._update_overlay_geometry()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._update_badge_geometry()
        self._update_overlay_geometry()

    def enterEvent(self, event) -> None:
        super().enterEvent(event)
        self._set_overlay_expanded(True)

    def leaveEvent(self, event) -> None:
        super().leaveEvent(event)
        self._set_overlay_expanded(False)

    def eventFilter(self, obj, event) -> bool:
        if obj is self._controls_widget and event.type() in (QEvent.Type.Enter, QEvent.Type.MouseMove):
            self._set_overlay_expanded(True)
        return super().eventFilter(obj, event)

    def _update_badge_geometry(self) -> None:
        if self._badge_widget is None:
            return
        self._badge_widget.adjustSize()
        w = self._badge_widget.width()
        h = self._badge_widget.height()
        margin = 6
        self._badge_widget.setGeometry(
            self.width() - w - margin, margin, w, h
        )

    def _set_overlay_expanded(self, expanded: bool) -> None:
        if self._overlay_expanded == expanded:
            return
        self._overlay_expanded = expanded
        self._update_overlay_geometry()

    def _update_overlay_geometry(self) -> None:
        margin = 8
        expanded_h = 34
        y = (
            max(margin, self.height() - expanded_h - margin)
            if self._overlay_expanded
            else self.height() + margin
        )

        corner_w = self._corner_widget.width() if self._corner_widget is not None else 0
        if self._controls_widget is not None:
            controls_w = max(120, self.width() - corner_w - margin * 3)
            self._controls_widget.setGeometry(margin, y, controls_w, expanded_h)
            self._controls_widget.raise_()

        if self._corner_widget is not None:
            self._corner_widget.setGeometry(
                self.width() - self._corner_widget.width() - margin,
                y,
                self._corner_widget.width(),
                self._corner_widget.height(),
            )
            self._corner_widget.raise_()


class _PreviewCornerButton(QPushButton):
    """视频预览右下角的全屏图标按钮。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("previewFullscreenButton")
        self.setFixedSize(32, 32)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("全屏预览")

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(QColor(0, 0, 0, 130 if not self.underMouse() else 180))
        p.setPen(QColor(255, 255, 255, 40))
        p.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 7, 7)
        p.setPen(QPen(QColor(255, 255, 255, 235), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(9, 9, 15, 9)
        p.drawLine(9, 9, 9, 15)
        p.drawLine(23, 9, 17, 9)
        p.drawLine(23, 9, 23, 15)
        p.drawLine(9, 23, 15, 23)
        p.drawLine(9, 23, 9, 17)
        p.drawLine(23, 23, 17, 23)
        p.drawLine(23, 23, 23, 17)
        p.end()


class _MiniTimeline(QWidget):
    """房间卡片内的小时间线，支持点击跳转预览。"""

    seek_requested = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._position = 0.0
        self._duration = 0.0
        self.setFixedHeight(18)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_data(self, position: float, duration: float) -> None:
        self._position = max(0.0, float(position or 0.0))
        self._duration = max(0.0, float(duration or 0.0))
        self.update()

    def _x_to_time(self, x: float) -> float:
        if self._duration <= 0:
            return 0.0
        left, right = 8, max(9, self.width() - 8)
        ratio = max(0.0, min(1.0, (x - left) / max(1, right - left)))
        return ratio * self._duration

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._duration > 0:
            self.seek_requested.emit(self._x_to_time(event.position().x()))
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if event.buttons() & Qt.MouseButton.LeftButton and self._duration > 0:
            self.seek_requested.emit(self._x_to_time(event.position().x()))
        super().mouseMoveEvent(event)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = get_theme()
        left, right = 8, self.width() - 8
        y = self.height() // 2
        track = QRectF(left, y - 2, max(1, right - left), 4)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(c.border_default))
        p.drawRoundedRect(track, 2, 2)
        if self._duration > 0:
            ratio = max(0.0, min(1.0, self._position / self._duration))
            progress = QRectF(left, y - 2, max(2, (right - left) * ratio), 4)
            p.setBrush(QColor(c.accent_primary))
            p.drawRoundedRect(progress, 2, 2)
            knob_x = left + (right - left) * ratio
            p.setBrush(QColor(c.accent_primary))
            p.drawEllipse(QRectF(knob_x - 4, y - 4, 8, 8))
        p.end()


class _CardResizeHandle(QWidget):
    """房间卡片右下角拖拽手柄，用于调节卡片宽度和预览高度。"""

    def __init__(self, card: "RoomCard", parent=None):
        super().__init__(parent)
        self._card = card
        self._drag_start = None
        self._start_width = 0
        self._start_preview_height = 0
        self.setObjectName("roomCardResizeHandle")
        self.setFixedSize(18, 18)
        self.setCursor(Qt.CursorShape.SizeFDiagCursor)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start = event.globalPosition()
            self._start_width = self._card.maximumWidth()
            self._start_preview_height = self._card._preview_area.height()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_start is not None and event.buttons() & Qt.MouseButton.LeftButton:
            delta = event.globalPosition() - self._drag_start
            self._card.set_card_size(
                self._start_width + int(delta.x()),
                self._start_preview_height + int(delta.y()),
            )
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._drag_start = None
        super().mouseReleaseEvent(event)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = QColor(get_theme().text_tertiary)
        c.setAlpha(150)
        p.setPen(QPen(c, 1.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        for offset in (4, 8, 12):
            p.drawLine(self.width() - offset, self.height() - 2, self.width() - 2, self.height() - offset)
        p.end()


class _RoomCheckBox(QCheckBox):
    """自绘房间卡片复选框，选中态有明确勾选标记。"""

    uses_custom_indicator = True

    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self.setObjectName("roomCardCheckBox")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(24)

    def sizeHint(self) -> QSize:
        fm = self.fontMetrics()
        return QSize(fm.horizontalAdvance(self.text()) + 28, max(24, fm.height() + 6))

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = get_theme()
        box_size = 15
        y = (self.height() - box_size) / 2
        box = QRectF(0.5, y, box_size, box_size)
        checked = self.isChecked()
        hover = self.underMouse()

        if checked:
            p.setBrush(QColor(c.accent_primary))
            p.setPen(QPen(QColor(c.accent_primary), 1.4))
        else:
            p.setBrush(QColor(c.bg_secondary if not is_dark() else c.bg_tertiary))
            p.setPen(QPen(QColor(c.accent_primary if hover else c.border_default), 1.4))
        p.drawRoundedRect(box, 4, 4)

        if checked:
            p.setPen(QPen(QColor("#ffffff"), 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            p.drawLine(QPointF(4.2, y + 8.0), QPointF(7.0, y + 10.8))
            p.drawLine(QPointF(7.0, y + 10.8), QPointF(12.0, y + 4.8))

        text_color = c.accent_primary if checked else (c.text_primary if hover else c.text_secondary)
        p.setPen(QColor(text_color))
        p.drawText(QRectF(22, 0, max(1, self.width() - 22), self.height()), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self.text())
        p.end()


class RoomCard(QFrame):
    """单个直播间房间卡片。"""

    selected = Signal(str)
    connect_clicked = Signal(str)
    disconnect_clicked = Signal(str)
    record_clicked = Signal(str)
    stop_clicked = Signal(str)
    remove_clicked = Signal(str)
    mute_toggled = Signal(str, bool)
    preview_clicked = Signal(str)
    pause_clicked = Signal(str)
    resume_clicked = Signal(str)
    fullscreen_clicked = Signal(str)
    timeline_seek_requested = Signal(str, float)
    include_toggled = Signal(str, bool)

    def __init__(self, room: RoomSession, parent=None) -> None:
        super().__init__(parent)
        self.room = room
        self._selected_state = False
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setObjectName("roomCard")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        # 高度按内容自适应，宽度最大 440，在常见分辨率下可并排放置两张卡片
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self.setMaximumWidth(440)
        self.setMinimumWidth(320)
        self._build_ui()
        self._apply_style()
        self.refresh()
        # Accessibility：为屏幕阅读器提供语义化描述
        self.setAccessibleName(f"房间卡片: {room.streamer_name or room.room_url}")
        self.setAccessibleDescription(f"平台: {room.platform_name or room.platform}, 状态: {room.status_text()}")
        connect_theme_changed(self.refresh_theme)

    # ── UI construction ──────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Top accent bar (hidden by default)
        self._top_bar = QFrame(self)
        self._top_bar.setObjectName("roomCardTopBar")
        self._top_bar.setFixedHeight(3)
        root.addWidget(self._top_bar)

        inner = QVBoxLayout()
        inner.setContentsMargins(12, 8, 12, 10)
        inner.setSpacing(6)

        # ── Header row ──
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(6)

        self._platform_tag = _PlatformTag(self)
        self._title_label = QLabel(self)
        self._title_label.setObjectName("roomCardTitle")
        self._title_label.setWordWrap(True)
        # 最多显示两行标题，避免超长 URL 把卡片撑得过高
        self._title_label.setMaximumHeight(42)
        self._title_label.setMinimumHeight(18)
        self._title_label.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)

        self._remove_btn = QPushButton("✕", self)
        self._remove_btn.setObjectName("roomCardRemoveBtn")
        self._remove_btn.setFixedSize(22, 22)
        self._remove_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._remove_btn.clicked.connect(lambda: self.remove_clicked.emit(self.room.room_id))

        header.addWidget(self._platform_tag)
        header.addWidget(self._title_label, 1)
        header.addWidget(self._remove_btn)
        inner.addLayout(header)

        # ── Preview area ──
        self._preview_area = _PreviewArea(self)
        self._preview_area.setObjectName("roomCardPreviewArea")
        self._preview_area.setFixedHeight(130)
        preview_layout = QVBoxLayout(self._preview_area)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        self._preview_layout = preview_layout

        self._preview_placeholder = QWidget(self._preview_area)
        self._preview_placeholder.setMinimumSize(120, 60)
        self._preview_placeholder.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        ph_layout = QVBoxLayout(self._preview_placeholder)
        ph_layout.setContentsMargins(8, 8, 8, 8)
        ph_layout.setSpacing(4)
        ph_layout.setAlignment(Qt.AlignCenter)
        self._ph_icon = QLabel("▶", self._preview_placeholder)
        self._ph_icon.setObjectName("roomCardPlaceholderIcon")
        self._ph_icon.setAlignment(Qt.AlignCenter)
        self._ph_text = QLabel("预览未开启", self._preview_placeholder)
        self._ph_text.setObjectName("roomCardPlaceholderText")
        self._ph_text.setAlignment(Qt.AlignCenter)
        self._ph_text.setWordWrap(True)
        ph_layout.addWidget(self._ph_icon)
        ph_layout.addWidget(self._ph_text)
        preview_layout.addWidget(self._preview_placeholder)

        self._embedded_preview: QWidget | None = None

        # Badges overlay container
        self._badge_container = QWidget()
        badge_layout = QHBoxLayout(self._badge_container)
        badge_layout.setContentsMargins(6, 6, 6, 6)
        badge_layout.setSpacing(4)
        badge_layout.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)

        self._rec_badge = QLabel("● REC", self._badge_container)
        self._rec_badge.setObjectName("roomCardBadgeRec")
        self._rec_badge.setVisible(False)
        badge_layout.addWidget(self._rec_badge)

        self._mute_badge = QLabel("静音", self._badge_container)
        self._mute_badge.setObjectName("roomCardBadgeMute")
        self._mute_badge.setVisible(False)
        badge_layout.addWidget(self._mute_badge)

        # 同步时间戳徽章（多选时显示）
        self._sync_badge = QLabel("", self._badge_container)
        self._sync_badge.setObjectName("syncIndicator")
        self._sync_badge.setVisible(False)
        badge_layout.addWidget(self._sync_badge)

        self._preview_area.set_badge_widget(self._badge_container)

        self._fullscreen_btn = _PreviewCornerButton(self._preview_area)
        self._fullscreen_btn.clicked.connect(lambda: self.fullscreen_clicked.emit(self.room.room_id))
        self._preview_area.set_corner_widget(self._fullscreen_btn)

        inner.addWidget(self._preview_area)

        # Preview controls live below the video so they are never hidden by
        # native video surfaces.
        self._preview_controls = QWidget(self)
        self._preview_controls.setObjectName("roomCardPreviewControls")
        self._preview_controls.setMouseTracking(True)
        preview_ctrl = QHBoxLayout(self._preview_controls)
        preview_ctrl.setContentsMargins(0, 0, 0, 0)
        preview_ctrl.setSpacing(6)

        self._preview_btn = QPushButton("预览", self._preview_controls)
        self._pause_btn = QPushButton("暂停", self._preview_controls)
        self._resume_btn = QPushButton("继续", self._preview_controls)
        self._mute_btn = _RoomCheckBox("静音", self._preview_controls)

        for btn in (self._preview_btn, self._pause_btn, self._resume_btn):
            btn.setObjectName("roomCardSmallBtn")
            btn.setFixedHeight(28)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)

        self._pause_btn.setVisible(False)
        self._resume_btn.setVisible(False)

        self._preview_btn.clicked.connect(lambda: self.preview_clicked.emit(self.room.room_id))
        self._pause_btn.clicked.connect(lambda: self.pause_clicked.emit(self.room.room_id))
        self._resume_btn.clicked.connect(lambda: self.resume_clicked.emit(self.room.room_id))
        self._mute_btn.toggled.connect(self._on_mute_toggled)

        preview_ctrl.addWidget(self._preview_btn)
        preview_ctrl.addWidget(self._pause_btn)
        preview_ctrl.addWidget(self._resume_btn)
        preview_ctrl.addStretch(1)
        preview_ctrl.addWidget(self._mute_btn)
        inner.addWidget(self._preview_controls)

        self._timeline = _MiniTimeline(self)
        self._timeline.setObjectName("roomCardTimeline")
        self._timeline.seek_requested.connect(lambda sec: self.timeline_seek_requested.emit(self.room.room_id, sec))
        inner.addWidget(self._timeline)

        # ── Status row ──
        status_row = QHBoxLayout()
        status_row.setContentsMargins(0, 0, 0, 0)
        status_row.setSpacing(6)

        self._status_dot = _StatusDot(10, self)
        self._status_text = QLabel(self)
        self._status_text.setObjectName("roomCardStatus")

        status_row.addWidget(self._status_dot)
        status_row.addWidget(self._status_text, 1)
        inner.addLayout(status_row)

        # ── Action row ──
        action_row = QHBoxLayout()
        action_row.setContentsMargins(0, 0, 0, 0)
        action_row.setSpacing(6)

        self._connect_btn = QPushButton("连接", self)
        self._record_btn = QPushButton("录制", self)
        self._include_cb = _RoomCheckBox("参与批量导出", self)
        self._include_cb.setToolTip("勾选后，导出选区时会一并导出此房间的录制")

        for btn in (self._connect_btn, self._record_btn):
            btn.setObjectName("roomCardActionBtn")
            btn.setFixedHeight(30)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)

        self._connect_btn.clicked.connect(self._on_connect_clicked)
        self._record_btn.clicked.connect(self._on_record_clicked)
        self._include_cb.toggled.connect(lambda v: self.include_toggled.emit(self.room.room_id, v))

        action_row.addWidget(self._connect_btn, 1)
        action_row.addWidget(self._record_btn, 1)
        action_row.addWidget(self._include_cb)
        self._resize_handle = _CardResizeHandle(self, self)
        action_row.addWidget(self._resize_handle, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom)
        inner.addLayout(action_row)

        root.addLayout(inner)

        # ── Pulse animation for recording dot ──
        self._pulse_anim = QPropertyAnimation(self._status_dot, b"pulse", self)
        self._pulse_anim.setDuration(1200)
        self._pulse_anim.setStartValue(0.0)
        self._pulse_anim.setEndValue(1.0)
        self._pulse_anim.setEasingCurve(QEasingCurve.Type.InOutSine)
        self._pulse_anim.setLoopCount(-1)
        self._pulse_group = QParallelAnimationGroup(self)
        self._pulse_group.addAnimation(self._pulse_anim)

    # ── Style helpers ────────────────────────────────────────────────

    def _set_button_style_name(self, btn: QPushButton, name: str) -> None:
        """Switch a button's objectName and re-apply the global stylesheet.

        This is used for action buttons whose color scheme changes with state
        (connected/disconnected, recording/not-recording).
        """
        if btn.objectName() != name:
            btn.setObjectName(name)
            btn.style().unpolish(btn)
            btn.style().polish(btn)
            btn.update()

    def _apply_style(self) -> None:
        # Global stylesheet drives the base roomCard appearance.
        self.setObjectName("roomCard")

    def _apply_selected_border(self) -> None:
        if self._selected_state:
            self.setObjectName("roomCardSelected")
            self.setGraphicsEffect(None)
            from PySide6.QtWidgets import QGraphicsDropShadowEffect
            c = get_theme()
            shadow = QGraphicsDropShadowEffect(self)
            shadow.setBlurRadius(20)
            shadow.setColor(QColor(c.accent_primary_glow))
            shadow.setOffset(0, 0)
            self.setGraphicsEffect(shadow)
        else:
            self.setGraphicsEffect(None)
            self._apply_style()
        # Re-polish so the new objectName takes effect.
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    # ── Signal handlers ──────────────────────────────────────────────

    def _on_connect_clicked(self) -> None:
        if self.room.is_connected:
            self.disconnect_clicked.emit(self.room.room_id)
        else:
            self.connect_clicked.emit(self.room.room_id)

    def _on_record_clicked(self) -> None:
        if self.room.is_recording:
            self.stop_clicked.emit(self.room.room_id)
        else:
            self.record_clicked.emit(self.room.room_id)

    def _on_mute_toggled(self, muted: bool) -> None:
        self.room.preview_muted = muted
        self.mute_toggled.emit(self.room.room_id, muted)

    # ── Public API ───────────────────────────────────────────────────

    def set_preview_widget(self, widget: QWidget) -> None:
        """Embed a preview widget (e.g. MpvWidget) into the preview area."""
        if self._embedded_preview is not None:
            self._preview_layout.removeWidget(self._embedded_preview)
            self._embedded_preview.setParent(None)

        self._embedded_preview = widget
        widget.setParent(self._preview_area)
        widget.setObjectName("roomCardPreviewEmbed")
        widget.setMinimumSize(0, 0)
        widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._preview_layout.addWidget(widget, 1)
        widget.show()
        rebind_fn = getattr(widget, "rebind_video_output", None)
        if callable(rebind_fn):
            rebind_fn()

        # If the preview backend failed to initialize, surface the reason and
        # keep the placeholder visible so the user sees the error message.
        is_available_fn = getattr(widget, "is_available", None)
        if callable(is_available_fn) and not is_available_fn():
            error_msg = getattr(widget, "init_error", lambda: "预览初始化失败")() or "预览初始化失败"
            self._set_placeholder("⚠", error_msg)
            self._embedded_preview.hide()
            self._preview_placeholder.show()
            self._preview_btn.setEnabled(False)
            self._preview_btn.setToolTip(error_msg)
        else:
            self._preview_placeholder.hide()
            self._preview_btn.setEnabled(True)
            self._preview_btn.setToolTip("")

    def set_card_size(self, width: int, preview_height: int) -> None:
        """调节单个房间卡片尺寸，宽度和预览高度都限制在合理范围内。"""
        width = max(320, min(760, int(width)))
        preview_height = max(110, min(340, int(preview_height)))
        self.setMaximumWidth(width)
        self.setMinimumWidth(min(width, 320))
        self._preview_area.setFixedHeight(preview_height)
        self.updateGeometry()

    def set_timeline_data(self, position: float, duration: float) -> None:
        self._timeline.set_data(position, duration)

    def remove_preview_widget(self) -> None:
        """Remove the embedded preview widget and show placeholder."""
        if self._embedded_preview is not None:
            self._preview_layout.removeWidget(self._embedded_preview)
            self._embedded_preview.setParent(None)
            self._embedded_preview.hide()
            self._embedded_preview = None
        self._preview_placeholder.show()

    def _set_placeholder(self, icon: str, text: str) -> None:
        self._ph_icon.setText(icon)
        self._ph_text.setText(text)

    def set_selected(self, selected: bool) -> None:
        self._selected_state = selected
        self._apply_selected_border()

    def show_sync_badge(self, time_text: str) -> None:
        """显示同步时间戳徽章（多选模式下显示统一时间）。"""
        self._sync_badge.setText(f"⇔ {time_text}")
        self._sync_badge.setVisible(True)

    def hide_sync_badge(self) -> None:
        """隐藏同步时间戳徽章。"""
        self._sync_badge.setVisible(False)

    def refresh_theme(self) -> None:
        """主题切换时刷新卡片样式。"""
        self._apply_style()
        self._apply_selected_border()
        # Global stylesheet drives most widget colors; refresh() ensures
        # action button objectNames reflect the current room state.
        self.refresh()

    def refresh(self) -> None:
        c = get_theme()
        s = self.room

        # Platform tag
        display_name = s.platform_name or s.platform or "直链"
        self._platform_tag.set_platform(s.platform or "custom", display_name)

        # Title: 优先使用直播间标题/主播名，兜底再显示 URL（超长则省略）
        title = ""
        if s.stream_info and s.stream_info.title:
            title = s.stream_info.title
        if not title and s.stream_title:
            title = s.stream_title
        if not title and s.streamer_name:
            title = s.streamer_name
        if not title:
            url = s.room_url
            # 超长 URL 一行显示，用省略号截断，避免把卡片撑得很高
            self._title_label.setWordWrap(False)
            self._title_label.setMaximumHeight(22)
            fm = self._title_label.fontMetrics()
            # 预留平台标签、关闭按钮和边距后的可用宽度
            avail_width = max(180, self.width() - 90)
            title = fm.elidedText(url, Qt.ElideRight, avail_width)
        else:
            self._title_label.setWordWrap(True)
            self._title_label.setMaximumHeight(42)
        self._title_label.setText(title)

        # Connecting loading state / preview placeholder
        preview_available = (
            self._embedded_preview is not None
            and getattr(self._embedded_preview, "is_available", lambda: True)()
        )
        if s.preview_error:
            # 预览初始化失败时把错误直接显示在卡片占位区，并禁用预览按钮
            self._set_placeholder("⚠", s.preview_error)
            if self._embedded_preview is not None:
                self._embedded_preview.hide()
            self._preview_placeholder.show()
            self._preview_btn.setEnabled(False)
            self._preview_btn.setToolTip(s.preview_error)
        elif s.is_connecting:
            self._set_placeholder("⟳", "连接中...")
            if self._embedded_preview is not None:
                self._embedded_preview.hide()
            self._preview_placeholder.show()
            self._preview_btn.setEnabled(True)
            self._preview_btn.setToolTip("")
        elif s.preview_enabled and preview_available:
            self._embedded_preview.show()
            self._preview_placeholder.hide()
            self._preview_btn.setEnabled(True)
            self._preview_btn.setToolTip("")
        elif s.preview_enabled and self._embedded_preview is not None:
            # 预览组件已创建但后端不可用，把错误显示在卡片占位区里更醒目
            error_msg = getattr(self._embedded_preview, "init_error", lambda: "预览初始化失败")()
            self._set_placeholder("⚠", error_msg or "预览不可用")
            self._embedded_preview.hide()
            self._preview_placeholder.show()
            self._preview_btn.setEnabled(False)
            self._preview_btn.setToolTip(error_msg or "预览不可用")
        else:
            if self._embedded_preview is not None:
                self._embedded_preview.hide()
            self._set_placeholder("▶", "预览未开启")
            self._preview_placeholder.show()
            self._preview_btn.setEnabled(True)
            self._preview_btn.setToolTip("")

        # Status
        status_key = "idle"
        if s.last_error:
            status_key = "error"
        elif s.is_connecting:
            status_key = "idle"
        elif s.is_recording:
            status_key = "recording"
        elif s.is_connected:
            status_key = "connected"
        dot_color = getattr(c, _STATUS_COLORS_ATTR[status_key], c.text_tertiary)
        self._status_dot.set_color(dot_color)
        self._status_text.setText(s.status_text())

        # Pulse animation
        if status_key == "recording":
            if self._pulse_group.state() != QParallelAnimationGroup.State.Running:
                self._pulse_group.start()
        else:
            self._pulse_group.stop()
            self._status_dot.set_pulse(0.0)

        # Badges
        self._rec_badge.setVisible(s.is_recording)
        self._mute_badge.setVisible(s.preview_muted)

        # Mute checkbox
        self._mute_btn.blockSignals(True)
        self._mute_btn.setChecked(s.preview_muted)
        self._mute_btn.blockSignals(False)

        # Include checkbox
        self._include_cb.blockSignals(True)
        self._include_cb.setChecked(s.include_in_cut)
        self._include_cb.blockSignals(False)

        # Preview controls
        if s.preview_enabled:
            self._preview_btn.setVisible(False)
            self._pause_btn.setVisible(not s.preview_paused)
            self._resume_btn.setVisible(s.preview_paused)
        else:
            self._preview_btn.setVisible(True)
            self._pause_btn.setVisible(False)
            self._resume_btn.setVisible(False)

        # Connect button
        if s.is_connecting:
            self._connect_btn.setText("连接中...")
            self._connect_btn.setEnabled(False)
            self._set_button_style_name(self._connect_btn, "roomCardActionBtn")
        elif s.is_connected:
            self._connect_btn.setEnabled(True)
            self._connect_btn.setText("断开")
            self._set_button_style_name(self._connect_btn, "roomCardActionBtnDanger")
        else:
            self._connect_btn.setEnabled(True)
            self._connect_btn.setText("连接")
            self._set_button_style_name(self._connect_btn, "roomCardActionBtn")

        # Record button
        if s.is_recording:
            self._record_btn.setText("停止")
            self._set_button_style_name(self._record_btn, "roomCardActionBtnDanger")
        elif s.last_error and not s.is_connected:
            # Recording failed — offer retry with a visually distinct button.
            self._record_btn.setText("重试")
            self._set_button_style_name(self._record_btn, "roomCardActionBtnWarning")
        else:
            self._record_btn.setText("录制")
            self._set_button_style_name(self._record_btn, "roomCardActionBtn")

    # ── Event overrides ──────────────────────────────────────────────

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.selected.emit(self.room.room_id)
        super().mousePressEvent(event)

    def contextMenuEvent(self, event) -> None:
        menu = QMenu(self)
        if self.room.is_connected:
            menu.addAction("断开连接", lambda: self.disconnect_clicked.emit(self.room.room_id))
        else:
            menu.addAction("连接", lambda: self.connect_clicked.emit(self.room.room_id))
        menu.addSeparator()
        if self.room.is_recording:
            menu.addAction("停止录制", lambda: self.stop_clicked.emit(self.room.room_id))
        else:
            menu.addAction("开始录制", lambda: self.record_clicked.emit(self.room.room_id))
        menu.addSeparator()
        menu.addAction("删除房间", lambda: self.remove_clicked.emit(self.room.room_id))
        menu.exec(event.globalPos())
