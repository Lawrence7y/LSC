"""多直播间工作台的房间卡片组件。"""
from __future__ import annotations

import hashlib

from PySide6.QtCore import (
    QPropertyAnimation,
    QEvent,
    QEasingCurve,
    QParallelAnimationGroup,
    QPoint,
    QPointF,
    QRect,
    QRectF,
    QSettings,
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

from lsc.gui.components.preview_surface import PreviewSurface
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

# ── Card size presets ─────────────────────────────────────────
# (name, width, preview_height) — 小号/中号/大号三档预设
_CARD_PRESETS: list[tuple[str, int, int]] = [
    ("小", 340, 130),
    ("中", 440, 200),
    ("大", 560, 300),
]
_DEFAULT_PRESET_INDEX = 1  # 中号
_CARD_WIDTH_FALLBACK = 440
_PREVIEW_HEIGHT_FALLBACK = 200


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
        # 在深色模式下，黑色/深色品牌标签会融入背景，提亮到可辨的中灰
        if key == "douyin" and is_dark():
            brand = "#3a3f4a"
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
        # 半透明白边框,让深色品牌色标签在深色背景下也有清晰边界
        edge = QColor(255, 255, 255, 38 if is_dark() else 0)
        p.setPen(QPen(edge, 1) if edge.alpha() else Qt.PenStyle.NoPen)
        p.drawRoundedRect(0, 0, self.width(), self.height(), 4, 4)
        p.end()

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


class _SizeToggleButton(QWidget):
    """卡片尺寸切换按钮：hover 时显示，点击循环切换小→中→大，右键弹出菜单。"""

    size_changed = Signal()

    def __init__(self, card: "RoomCard", parent=None):
        super().__init__(parent)
        self._card = card
        self._visible = False
        self._hover = False
        self.setFixedSize(28, 28)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMouseTracking(True)
        self.setToolTip("切换卡片大小（小/中/大）")

    def set_overlay_visible(self, visible: bool) -> None:
        if self._visible != visible:
            self._visible = visible
            self.setVisible(visible)

    def enterEvent(self, event) -> None:
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._card.cycle_preset()
            self.size_changed.emit()
            event.accept()
            return
        if event.button() == Qt.MouseButton.RightButton:
            self._show_preset_menu(event.globalPosition().toPoint())
            event.accept()
            return
        super().mousePressEvent(event)

    def _show_preset_menu(self, pos: QPoint) -> None:
        menu = QMenu(self)
        current = self._card._preset_index
        for i, (name, w, h) in enumerate(_CARD_PRESETS):
            action = menu.addAction(f"{name}号 ({w}×{h})")
            action.setCheckable(True)
            action.setChecked(i == current)
            action.triggered.connect(lambda checked=False, idx=i: self._apply_preset(idx))
        menu.exec(pos)

    def _apply_preset(self, index: int) -> None:
        self._card.set_preset(index)
        self.size_changed.emit()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        bg = QColor(0, 0, 0, 140 if self._hover else 100)
        p.setBrush(bg)
        p.setPen(QColor(255, 255, 255, 50))
        p.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 6, 6)
        # Draw resize grip icon (3 diagonal dots)
        pen = QPen(QColor(255, 255, 255, 200 if self._hover else 140), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        for dx, dy in [(4, -4), (0, 0), (-4, 4)]:
            cx = self.width() / 2 + dx
            cy = self.height() / 2 + dy
            p.drawPoint(QPointF(cx, cy))
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
        self._multi_selected = False
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setObjectName("roomCard")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        # 高度按内容自适应,宽度由预设规格控制。
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        _w = _CARD_PRESETS[_DEFAULT_PRESET_INDEX][1]
        self.setMinimumWidth(_w)
        self.setMaximumWidth(_w)
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
        self._preview_area = PreviewSurface(self)
        self._preview_area.setObjectName("roomCardPreviewArea")
        self._preview_area.setFixedHeight(_CARD_PRESETS[_DEFAULT_PRESET_INDEX][2])
        self._preview_layout = self._preview_area.content_layout

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
        self._preview_layout.addWidget(self._preview_placeholder)

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
        self._include_cb = _RoomCheckBox("导出", self)
        self._include_cb.setToolTip("勾选后,导出选区时会一并导出此房间的录制")

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
        inner.addLayout(action_row)

        root.addLayout(inner)

        # ── Size toggle button (overlay, appears on hover) ──
        self._size_btn = _SizeToggleButton(self, self)
        self._size_btn.setVisible(False)
        self._size_btn.size_changed.connect(self._on_size_changed)
        self._preset_index = _DEFAULT_PRESET_INDEX

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
            # 多选时不加阴影(QGraphicsDropShadowEffect 在多卡片下绘制开销大),
            # 仅靠 QSS 的 accent_primary 边框表达选中态。
            if not self._multi_selected:
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

    def set_card_width(self, width: int) -> None:
        """设置卡片固定宽度(min==max),FlowLayout 会让它保持该宽度不参与行内扩展。"""
        self.setMinimumWidth(width)
        self.setMaximumWidth(width)
        self.updateGeometry()
        self._invalidate_parent_layout()

    def set_preview_height(self, height: int) -> None:
        """设置预览区高度。"""
        self._preview_area.setFixedHeight(height)
        self.updateGeometry()
        self._invalidate_parent_layout()

    def cycle_preset(self) -> None:
        """循环切换预设: 小→中→大→小。"""
        self.set_preset((self._preset_index + 1) % len(_CARD_PRESETS))

    def set_preset(self, index: int) -> None:
        """应用指定预设规格。"""
        index = max(0, min(len(_CARD_PRESETS) - 1, index))
        self._preset_index = index
        _name, w, h = _CARD_PRESETS[index]
        self.set_card_width(w)
        self.set_preview_height(h)
        self._save_preset()

    def _on_size_changed(self) -> None:
        """尺寸切换后通知父布局重排。"""
        self._invalidate_parent_layout()

    def reset_card_width(self) -> None:
        """恢复为默认预设。"""
        self.set_preset(_DEFAULT_PRESET_INDEX)

    def _invalidate_parent_layout(self) -> None:
        """宽度/高度变化后通知父布局(FlowLayout)重新换行,并向上传播。"""
        parent = self.parentWidget()
        if parent is None:
            return
        lay = parent.layout()
        if lay is not None:
            lay.invalidate()
            lay.update()
        parent.updateGeometry()

    # ── Per-card size persistence ───────────────────────────────

    def _size_settings_key(self) -> str:
        digest = hashlib.md5(self.room.room_url.encode("utf-8")).hexdigest()[:12]
        return f"card_preset/{digest}"

    def restore_saved_size(self) -> None:
        """从 QSettings 恢复该房间上次保存的卡片预设。"""
        settings = QSettings("LSC", "LiveStreamClipper")
        val = settings.value(self._size_settings_key())
        if val is None:
            self.set_preset(_DEFAULT_PRESET_INDEX)
            return
        try:
            idx = int(val)
        except (TypeError, ValueError):
            idx = _DEFAULT_PRESET_INDEX
        self.set_preset(idx)

    def _save_preset(self) -> None:
        """持久化当前预设索引。"""
        settings = QSettings("LSC", "LiveStreamClipper")
        settings.setValue(self._size_settings_key(), self._preset_index)

    def _notify_size_changed(self) -> None:
        """兼容旧接口（拖拽手柄已移除，此方法保留供其他调用方使用）。"""
        self._save_preset()

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

    def set_selected(self, selected: bool, *, multi: bool = False) -> None:
        self._selected_state = selected
        self._multi_selected = bool(multi and selected)
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
            # 区分语义:未连接引导「点击连接」,已连接引导「点击预览」
            if not s.is_connected and not s.is_connecting:
                self._set_placeholder("📡", "点击连接")
            else:
                self._set_placeholder("▶", "点击预览")
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
        status_text = s.status_text()
        # 录制中时把时长直接显示在卡片状态行,用户一眼可见已录多久
        if s.is_recording and s.record_started_at is not None:
            from datetime import datetime
            elapsed = (datetime.now() - s.record_started_at).total_seconds()
            status_text = status_text.replace("录制中", f"录制中 {fmt_time(elapsed)}", 1)
        self._status_text.setText(status_text)

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

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._place_size_button()

    def _place_size_button(self) -> None:
        """将尺寸切换按钮定位到卡片右上角。"""
        btn = self._size_btn
        margin = 4
        btn.setGeometry(self.width() - btn.width() - margin, margin, btn.width(), btn.height())
        btn.raise_()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.selected.emit(self.room.room_id)
        super().mousePressEvent(event)

    def enterEvent(self, event) -> None:
        super().enterEvent(event)
        self._size_btn.set_overlay_visible(True)

    def leaveEvent(self, event) -> None:
        super().leaveEvent(event)
        self._size_btn.set_overlay_visible(False)

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
        size_menu = menu.addMenu("卡片大小")
        for i, (name, w, h) in enumerate(_CARD_PRESETS):
            action = size_menu.addAction(f"{name}号 ({w}×{h})")
            action.setCheckable(True)
            action.setChecked(i == self._preset_index)
            action.triggered.connect(lambda checked=False, idx=i: self.set_preset(idx))
        menu.addSeparator()
        menu.addAction("删除房间", lambda: self.remove_clicked.emit(self.room.room_id))
        menu.exec(event.globalPos())
