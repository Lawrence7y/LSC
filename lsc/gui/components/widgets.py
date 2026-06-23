"""LSC 通用 GUI 组件。"""
from __future__ import annotations

from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QObject,
    QPropertyAnimation,
    QByteArray,
    Qt,
    Signal,
    QTimer,
)
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from lsc.gui.theme import get_theme


class Card(QFrame):
    """带边框和圆角的卡片容器。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self.setFrameShape(QFrame.StyledPanel)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(20, 20, 20, 20)
        self._layout.setSpacing(14)

    @property
    def layout(self):
        return self._layout

    def add_widget(self, widget):
        """添加子组件到卡片布局。"""
        self._layout.addWidget(widget)

    def add_layout(self, layout):
        """添加子布局到卡片布局。"""
        self._layout.addLayout(layout)


class ChipGroup(QWidget):
    """一组可选的标签按钮（Chip/Tag 风格）。"""

    selection_changed = Signal(str)

    def __init__(self, options: list[str], parent=None):
        super().__init__(parent)
        self._options = options
        self._items = options  # 兼容 record.py 中的 _items 访问
        self._selected = options[0] if options else ""
        self._buttons: dict[str, QPushButton] = {}
        self._build()

    def _build(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.setObjectName("chipGroup")
        for opt in self._options:
            btn = QPushButton(opt)
            btn.setCheckable(True)
            btn.setChecked(opt == self._selected)
            btn.clicked.connect(lambda checked, o=opt: self._on_click(o))
            self._buttons[opt] = btn
            layout.addWidget(btn)

    def _on_click(self, option: str):
        self._selected = option
        for opt, btn in self._buttons.items():
            btn.setChecked(opt == option)
        self.selection_changed.emit(option)

    def _click(self, option: str) -> None:
        """外部触发选中（兼容 record.py）。"""
        self._on_click(option)

    @property
    def selected(self) -> str:
        """当前选中的选项。"""
        return self._selected

    def set_selected(self, value: str) -> None:
        if value in self._buttons:
            self._on_click(value)


class EmptyState(QWidget):
    """空状态占位组件。"""

    def __init__(self, title: str = "", subtitle: str = "", icon_type: str = "", parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setSpacing(6)
        layout.setAlignment(Qt.AlignCenter)
        if title:
            lbl = QLabel(title)
            lbl.setAlignment(Qt.AlignCenter)
            lbl.setObjectName("card_title")
            layout.addWidget(lbl)
        if subtitle:
            lbl = QLabel(subtitle)
            lbl.setAlignment(Qt.AlignCenter)
            lbl.setObjectName("label_tertiary")
            layout.addWidget(lbl)


class FadeInWidget(QWidget):
    """带淡入动画的容器。

    Uses QGraphicsOpacityEffect + QPropertyAnimation so child widgets really
    fade in when the container is first shown. Previously this class was a
    plain QWidget container with no animation despite its name.
    """

    def __init__(self, delay_ms: int = 100, parent=None):
        super().__init__(parent)
        self._delay = delay_ms
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        # Opacity effect driven by the fade-in animation.
        self._opacity_effect = QGraphicsOpacityEffect(self)
        self._opacity_effect.setOpacity(0.0)
        self.setGraphicsEffect(self._opacity_effect)
        self._anim: QPropertyAnimation | None = None

    @property
    def layout(self):
        return self._layout

    def addWidget(self, widget):
        self._layout.addWidget(widget)

    def showEvent(self, event):
        super().showEvent(event)
        # Start the fade-in animation once on first show.
        if self._anim is None and self._opacity_effect.opacity() < 1.0:
            QTimer.singleShot(self._delay, self._start_fade_in)

    def _start_fade_in(self):
        self._anim = QPropertyAnimation(self._opacity_effect, b"opacity", self)
        self._anim.setDuration(300)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.start()


class InputField(QWidget):
    """带标签的输入框。"""

    text_changed = Signal(str)
    returnPressed = Signal()

    def __init__(self, placeholder: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("inputField")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._input = QLineEdit()
        self._input.setPlaceholderText(placeholder)
        self._input.textChanged.connect(self.text_changed.emit)
        self._input.returnPressed.connect(self.returnPressed.emit)
        layout.addWidget(self._input)

    def text(self) -> str:
        return self._input.text()

    def setText(self, text: str) -> None:
        self._input.setText(text)

    def set_text(self, text: str) -> None:
        """设置输入框文本（别名）。"""
        self._input.setText(text)

    def clear(self) -> None:
        """清空输入框文本。"""
        self._input.clear()

    def setPlaceholderText(self, text: str) -> None:
        self._input.setPlaceholderText(text)


class ParamPanel(QWidget):
    """参数面板（CRF / 码率等）。

    mode 0 = CRF, mode 1 = 码率限制, mode 2 = 不限制
    """

    value_changed = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._mode = 0  # 0=CRF, 1=码率, 2=不限制
        self._crf = 23
        self._bitrate_value = "8000"
        self._bitrate_unit = "kbps"

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        # CRF 控件
        self._crf_label = QLabel("CRF: 23")
        layout.addWidget(self._crf_label)
        self._crf_slider = QSlider(Qt.Horizontal)
        self._crf_slider.setRange(0, 51)
        self._crf_slider.setValue(23)
        self._crf_slider.valueChanged.connect(self._on_crf_change)
        layout.addWidget(self._crf_slider)

        # 码率输入
        self._bitrate_label = QLabel("码率:")
        layout.addWidget(self._bitrate_label)
        self._bitrate_input = QLineEdit("8000")
        layout.addWidget(self._bitrate_input)

        self._update_visibility()

    def _on_crf_change(self, value: int):
        self._crf = value
        self._crf_label.setText(f"CRF: {value}")
        self.value_changed.emit(float(value))

    def _update_visibility(self):
        crf_visible = self._mode == 0
        bitrate_visible = self._mode == 1
        self._crf_label.setVisible(crf_visible)
        self._crf_slider.setVisible(crf_visible)
        self._bitrate_label.setVisible(bitrate_visible)
        self._bitrate_input.setVisible(bitrate_visible)

    def set_mode(self, mode: int) -> None:
        """设置参数模式: 0=CRF, 1=码率, 2=不限制。"""
        self._mode = mode
        self._update_visibility()

    def set_crf_value(self, value) -> None:
        """设置 CRF 值。"""
        try:
            self._crf = int(value)
            self._crf_slider.setValue(self._crf)
        except (ValueError, TypeError):
            pass

    def set_bitrate_value(self, value: str) -> None:
        """设置码率值。"""
        self._bitrate_value = str(value)
        self._bitrate_input.setText(self._bitrate_value)

    def set_bitrate_unit(self, unit: str) -> None:
        """设置码率单位。"""
        self._bitrate_unit = unit

    def crf_value(self) -> int:
        """获取 CRF 值。"""
        return self._crf

    def bitrate_value(self) -> str:
        """获取码率值。"""
        return self._bitrate_input.text()

    def bitrate_unit(self) -> str:
        """获取码率单位。"""
        return self._bitrate_unit

    def value(self) -> float:
        return float(self._crf)

    def setValue(self, v: float) -> None:
        self._crf_slider.setValue(int(v))


# ---------------------------------------------------------------------------
# Toast 通知组件
# ---------------------------------------------------------------------------

# Toast 类型 → (SVG path, 主题色字段, 暗色背景字段)
_TOAST_META: dict[str, tuple[str, str, str]] = {
    "info": (
        "M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1 15h-2v-6h2v6zm0-8h-2V7h2v2z",
        "accent_secondary",
        "bg_elevated",
    ),
    "success": (
        "M9 16.17L4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41L9 16.17z",
        "accent_success",
        "accent_success_dim",
    ),
    "warning": (
        "M1 21h22L12 2 1 21zm12-3h-2v-2h2v2zm0-4h-2v-4h2v4z",
        "accent_warning",
        "accent_warning_dim",
    ),
    "error": (
        "M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z",
        "accent_error",
        "accent_error_dim",
    ),
}


def _render_toast_icon(svg_path: str, color: str, size: int = 18) -> QPixmap:
    """渲染一个带颜色的 SVG 图标为 QPixmap。"""
    if not svg_path:
        return QPixmap()
    svg_xml = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
        f'fill="{color}"><path d="{svg_path}"/></svg>'
    )
    renderer = QSvgRenderer(QByteArray(svg_xml.encode("utf-8")))
    if not renderer.isValid():
        return QPixmap()
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    renderer.render(painter)
    painter.end()
    return pixmap


class Toast(QFrame):
    """单个 Toast 通知卡片。

    带类型图标、标题、消息文本、关闭按钮，以及淡入/淡出/滑入动画。
    """

    dismissed = Signal(object)  # 发射自身，供 ToastManager 移除

    def __init__(
        self,
        message: str,
        *,
        toast_type: str = "info",
        title: str = "",
        duration_ms: int = 0,
        parent=None,
    ):
        super().__init__(parent)
        self._toast_type = toast_type if toast_type in _TOAST_META else "info"
        self._message = message
        self._title = title
        # 默认时长：info/success 3s，warning/error 5s
        if duration_ms <= 0:
            duration_ms = 3000 if self._toast_type in ("info", "success") else 5000
        self._duration_ms = duration_ms

        self.setFrameShape(QFrame.NoFrame)
        self.setAttribute(Qt.WA_DeleteOnClose, False)
        # 让 Toast 浮在父窗口之上
        self.setWindowFlags(Qt.ToolTip | Qt.FramelessWindowHint)

        c = get_theme()
        meta = _TOAST_META[self._toast_type]
        accent = getattr(c, meta[1])
        bg = getattr(c, meta[2]) if meta[2].startswith("accent_") else getattr(c, meta[2], c.bg_elevated)

        self._build_ui(accent, bg, c)

        # 透明度效果用于淡入淡出
        self._opacity_effect = QGraphicsOpacityEffect(self)
        self._opacity_effect.setOpacity(0.0)
        self.setGraphicsEffect(self._opacity_effect)

        self._fade_anim: QPropertyAnimation | None = None
        self._slide_anim: QPropertyAnimation | None = None
        self._auto_timer = QTimer(self)
        self._auto_timer.setSingleShot(True)
        self._auto_timer.timeout.connect(self.dismiss)
        self._closing = False

    def _build_ui(self, accent: str, bg: str, c) -> None:
        """构建 Toast 内部布局。"""
        self.setObjectName("toast")
        self.setStyleSheet(
            f"""
            QFrame#toast {{
                background: {bg};
                border: 1px solid {accent};
                border-radius: 8px;
            }}
            QLabel {{
                background: transparent;
                border: none;
            }}
            QPushButton {{
                background: transparent;
                border: none;
                color: {c.text_tertiary};
                font-size: 14px;
                min-width: 20px;
                min-height: 20px;
                max-width: 20px;
                max-height: 20px;
                padding: 0;
            }}
            QPushButton:hover {{ color: {c.text_primary}; }}
            """
        )
        # 固定宽度，避免过长消息撑爆屏幕
        self.setFixedWidth(320)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 10, 10, 10)
        layout.setSpacing(10)

        # 类型图标
        meta = _TOAST_META[self._toast_type]
        icon_pix = _render_toast_icon(meta[0], accent, size=20)
        if not icon_pix.isNull():
            icon_lbl = QLabel()
            icon_lbl.setPixmap(icon_pix)
            layout.addWidget(icon_lbl, 0, Qt.AlignTop)

        # 文本区
        text_box = QVBoxLayout()
        text_box.setContentsMargins(0, 0, 0, 0)
        text_box.setSpacing(2)
        if self._title:
            title_lbl = QLabel(self._title)
            title_lbl.setStyleSheet(
                f"font-size:13px;font-weight:600;color:{c.text_primary};"
            )
            text_box.addWidget(title_lbl)
        msg_lbl = QLabel(self._message)
        msg_lbl.setWordWrap(True)
        msg_lbl.setStyleSheet(f"font-size:12px;color:{c.text_secondary};")
        text_box.addWidget(msg_lbl)

        # 动作按钮容器（默认隐藏，add_action 时显示）
        self._action_container = QWidget()
        self._action_container.setStyleSheet("background:transparent;border:none;")
        self._action_layout = QHBoxLayout(self._action_container)
        self._action_layout.setContentsMargins(0, 4, 0, 0)
        self._action_layout.setSpacing(6)
        self._action_container.setVisible(False)
        text_box.addWidget(self._action_container)

        layout.addLayout(text_box, 1)

        # 关闭按钮
        close_btn = QPushButton("×")
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.clicked.connect(self.dismiss)
        layout.addWidget(close_btn, 0, Qt.AlignTop)

    # -- 动画与显示 ----------------------------------------------------------
    def appear(self, start_pos=None) -> None:
        """淡入并（可选）从右侧滑入。"""
        self.show()
        # 淡入
        self._fade_anim = QPropertyAnimation(self._opacity_effect, b"opacity", self)
        self._fade_anim.setDuration(220)
        self._fade_anim.setStartValue(0.0)
        self._fade_anim.setEndValue(1.0)
        self._fade_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._fade_anim.start()
        # 滑入
        if start_pos is not None:
            end_pos = self.pos()
            offset = 24
            self.move(start_pos.x() + offset, start_pos.y())
            self._slide_anim = QPropertyAnimation(self, b"pos", self)
            self._slide_anim.setDuration(260)
            self._slide_anim.setStartValue(self.pos())
            self._slide_anim.setEndValue(end_pos)
            self._slide_anim.setEasingCurve(QEasingCurve.OutCubic)
            self._slide_anim.start()
        self._auto_timer.start(self._duration_ms)

    def dismiss(self) -> None:
        """淡出并关闭。"""
        if self._closing:
            return
        self._closing = True
        self._auto_timer.stop()
        self._fade_anim = QPropertyAnimation(self._opacity_effect, b"opacity", self)
        self._fade_anim.setDuration(180)
        self._fade_anim.setStartValue(self._opacity_effect.opacity())
        self._fade_anim.setEndValue(0.0)
        self._fade_anim.setEasingCurve(QEasingCurve.InCubic)
        self._fade_anim.finished.connect(self._on_fade_out_done)
        self._fade_anim.start()

    def _on_fade_out_done(self) -> None:
        self.dismissed.emit(self)
        self.close()

    def enterEvent(self, event):
        """鼠标悬停时暂停自动关闭。"""
        self._auto_timer.stop()
        super().enterEvent(event)

    def leaveEvent(self, event):
        """鼠标离开后恢复自动关闭（给 800ms 缓冲）。"""
        if not self._closing:
            self._auto_timer.start(800)
        super().leaveEvent(event)

    def add_action(self, label: str, callback) -> None:
        """在 Toast 底部添加一个可点击的动作按钮。

        Args:
            label: 按钮显示文字
            callback: 点击回调（无参数）
        """
        c = get_theme()
        btn = QPushButton(label)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setStyleSheet(
            f"QPushButton {{"
            f"  background:{c.accent_primary_dim};color:{c.accent_primary};"
            f"  border:1px solid {c.accent_primary};border-radius:4px;"
            f"  font-size:11px;font-weight:500;padding:3px 10px;"
            f"  min-width:auto;min-height:auto;max-width:none;max-height:none;"
            f"}}"
            f"QPushButton:hover {{ background:{c.accent_primary};color:#ffffff; }}"
        )
        btn.clicked.connect(lambda: (callback(), self.dismiss()))
        self._action_layout.addWidget(btn)
        self._action_container.setVisible(True)
        # 有动作按钮时延长显示时间
        self._duration_ms = max(self._duration_ms, 8000)
        if self._auto_timer.isActive():
            remaining = self._auto_timer.remainingTime()
            self._auto_timer.start(max(remaining, 8000))


class ToastManager(QObject):
    """Toast 通知管理器。

    挂载到主窗口，负责在右上角堆叠显示 Toast。用法::

        manager = ToastManager(main_window)
        manager.show("录制已开始", toast_type="success")
        manager.show("连接失败", toast_type="error", title=room_url)
    """

    def __init__(self, host: QWidget):
        super().__init__(host)
        self._host = host
        self._toasts: list[Toast] = []
        self._margin_right = 20
        self._margin_top = 20
        self._gap = 10
        # 监听宿主窗口大小变化以重新布局
        host.installEventFilter(self)

    def show(
        self,
        message: str,
        *,
        toast_type: str = "info",
        title: str = "",
        duration_ms: int = 0,
    ) -> Toast:
        """显示一个 Toast。"""
        toast = Toast(
            message,
            toast_type=toast_type,
            title=title,
            duration_ms=duration_ms,
            parent=self._host,
        )
        toast.dismissed.connect(self._on_toast_dismissed)
        self._toasts.append(toast)
        self._layout_toasts()
        # 出现位置：目标位置（layout 后的位置）
        target = toast.pos()
        toast.appear(start_pos=target)
        return toast

    def info(self, message: str, title: str = "", duration_ms: int = 0) -> Toast:
        return self.show(message, toast_type="info", title=title, duration_ms=duration_ms)

    def success(self, message: str, title: str = "", duration_ms: int = 0) -> Toast:
        return self.show(message, toast_type="success", title=title, duration_ms=duration_ms)

    def warning(self, message: str, title: str = "", duration_ms: int = 0) -> Toast:
        return self.show(message, toast_type="warning", title=title, duration_ms=duration_ms)

    def error(self, message: str, title: str = "", duration_ms: int = 0) -> Toast:
        return self.show(message, toast_type="error", title=title, duration_ms=duration_ms)

    def _layout_toasts(self) -> None:
        """从右上角向下堆叠所有 Toast。"""
        host_rect = self._host.rect()
        y = self._margin_top
        for toast in self._toasts:
            # 调整大小以获取正确高度
            toast.adjustSize()
            w = toast.width()
            h = toast.height()
            x = host_rect.width() - w - self._margin_right
            toast.move(x, y)
            y += h + self._gap

    def _on_toast_dismissed(self, toast: Toast) -> None:
        """Toast 淡出后从列表移除并重新布局。"""
        if toast in self._toasts:
            self._toasts.remove(toast)
        self._layout_toasts()

    def eventFilter(self, obj, event):
        if obj is self._host and event.type() == QEvent.Resize:
            self._layout_toasts()
        return super().eventFilter(obj, event)

    def clear(self) -> None:
        """立即关闭所有 Toast。"""
        for toast in list(self._toasts):
            toast.dismiss()


__all__ = [
    "Card",
    "ChipGroup",
    "EmptyState",
    "FadeInWidget",
    "InputField",
    "ParamPanel",
    "Toast",
    "ToastManager",
]
