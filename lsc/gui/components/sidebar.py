"""LSC 侧边栏导航组件。"""
from __future__ import annotations

from PySide6.QtCore import QByteArray, QSettings, QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QFrame, QLabel, QPushButton, QHBoxLayout, QVBoxLayout, QWidget

from lsc.gui.theme import connect_theme_changed, get_theme, is_dark, toggle_theme

try:
    from PySide6.QtSvg import QSvgRenderer

    _HAS_SVG = True
except ImportError:
    _HAS_SVG = False

_SUN_PATH = "M12 7a5 5 0 100 10 5 5 0 000-10zM2 13h2a1 1 0 001-1 1 1 0 00-1-1H2a1 1 0 00-1 1 1 1 0 001 1zm18 0h2a1 1 0 001-1 1 1 0 00-1-1h-2a1 1 0 00-1 1 1 1 0 001 1zm-9-9a1 1 0 001 1V2a1 1 0 00-2 0v2a1 1 0 001 1zm0 16a1 1 0 001 1v-2a1 1 0 00-2 0v2a1 1 0 001 1zM6.34 4.93a1 1 0 00-1.41 0 1 1 0 000 1.41l1.06 1.06a1 1 0 101.41-1.41L6.34 4.93zm11.32 11.32a1 1 0 00-1.41 0 1 1 0 000 1.41l1.06 1.06a1 1 0 101.41-1.41l-1.06-1.06zm1.06-11.32a1 1 0 10-1.41-1.41l-1.06 1.06a1 1 0 101.41 1.41l1.06-1.06zM7.4 18.66a1 1 0 101.41-1.41l-1.06-1.06a1 1 0 10-1.41 1.41l1.06 1.06z"

_MOON_PATH = "M9 2a7 7 0 100 14 9 9 0 018.87-7.53A8 8 0 119 2z"

_LOGO_PATH = (
    "M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-2 "
    "14.5v-9l6 4.5-6 4.5z"
)

_NAV_ITEMS: list[tuple[str, str, str, str]] = [
    ("dashboard", "仪表盘", "M4 5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V5zm10 0a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2a1 1 0 0 1-1 1h-4a1 1 0 0 1-1-1V5zM4 15a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v4a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1v-4zm10-2a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v6a1 1 0 0 1-1 1h-4a1 1 0 0 1-1-1v-6z", "Ctrl+1"),
    ("workbench", "工作台", "M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5", "Ctrl+2"),
    ("settings", "设置", "M12 15.5A3.5 3.5 0 1 0 12 8.5a3.5 3.5 0 0 0 0 7zm7.43-2.53c.04-.32.07-.64.07-.97s-.03-.66-.07-.98l2.11-1.65c.19-.15.24-.42.12-.64l-2-3.46c-.12-.22-.39-.3-.61-.22l-2.49 1c-.52-.4-1.08-.73-1.69-.98l-.38-2.65C14.46 2.18 14.25 2 14 2h-4c-.25 0-.46.18-.49.42l-.38 2.65c-.61.25-1.17.59-1.69.98l-2.49-1c-.23-.09-.49 0-.61.22l-2 3.46c-.13.22-.07.49.12.64l2.11 1.65c-.04.32-.07.65-.07.98s.03.66.07.97l-2.11 1.65c-.19.15-.24.42-.12.64l2 3.46c.12.22.39.3.61.22l2.49-1c.52.4 1.08.73 1.69.98l.38 2.66c.03.24.24.42.49.42h4c.25 0 .46-.18.49-.42l.38-2.66c.61-.25 1.17-.58 1.69-.98l2.49 1c.23.09.49 0 .61-.22l2-3.46c.12-.22.07-.49-.12-.64l-2.11-1.65z", "Ctrl+3"),
]


def _render_svg_icon(svg_path: str, color: str, size: int = 18) -> QIcon:
    """将 SVG path 渲染为单色图标。"""
    if not _HAS_SVG or not svg_path:
        return QIcon()
    svg_xml = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
        f'fill="{color}"><path d="{svg_path}"/></svg>'
    )
    renderer = QSvgRenderer(QByteArray(svg_xml.encode("utf-8")))
    if not renderer.isValid():
        return QIcon()
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    renderer.render(painter)
    painter.end()
    return QIcon(pixmap)


class NavButton(QPushButton):
    """侧边栏导航按钮。"""

    def __init__(self, page_key: str, label: str, svg_path: str, shortcut: str = "", parent=None):
        super().__init__(parent)
        self.page_key = page_key
        self._svg_path = svg_path
        self._active = False
        self._hover = False
        self._shortcut = shortcut
        self.setText(label)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(44)
        self.setObjectName("navButton")
        from PySide6.QtCore import QSize

        self.setIconSize(QSize(20, 20))
        self.setProperty("active", False)
        self._refresh_icon()
        self._shortcut_label: QLabel | None = None
        if shortcut:
            self._shortcut_label = QLabel(shortcut, self)
            self._shortcut_label.setObjectName("navShortcut")
            self._shortcut_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._shortcut_label.setVisible(False)
        self._badge_label = QLabel("", self)
        self._badge_label.setObjectName("navBadge")
        self._badge_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._badge_label.setVisible(False)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._layout_badge()
        self._layout_shortcut()

    def _layout_shortcut(self) -> None:
        if self._shortcut_label is None:
            return
        if self._active or self._hover:
            self._shortcut_label.setVisible(True)
            c = get_theme()
            self._shortcut_label.setStyleSheet(
                f"color: {c.text_tertiary}; background: transparent; "
                f"font-size: 10px; font-family: 'JetBrains Mono', Consolas, monospace;"
            )
            self._shortcut_label.adjustSize()
            sw = self._shortcut_label.width()
            sh = self._shortcut_label.height()
            self._shortcut_label.setGeometry(
                self.width() - sw - 12,
                (self.height() - sh) // 2,
                sw, sh
            )
            self._shortcut_label.raise_()
        else:
            self._shortcut_label.setVisible(False)

    def _layout_badge(self) -> None:
        if not self._badge_label.isVisible():
            return
        self._badge_label.adjustSize()
        bw = self._badge_label.width()
        bh = self._badge_label.height()
        offset = 40 if (self._shortcut_label and self._shortcut_label.isVisible()) else 10
        self._badge_label.setGeometry(self.width() - bw - offset, (self.height() - bh) // 2, bw, bh)
        self._badge_label.raise_()

    def set_badge(self, text: str, *, kind: str = "info") -> None:
        """设置状态徽标。kind: info/recording/error,控制配色。text 为空则隐藏。"""
        if not text:
            self._badge_label.setVisible(False)
            return
        self._badge_label.setText(text)
        self._badge_label.setProperty("kind", kind)
        self._badge_label.style().unpolish(self._badge_label)
        self._badge_label.style().polish(self._badge_label)
        self._badge_label.setVisible(True)
        self._layout_badge()

    def is_active(self) -> bool:
        return self._active

    def set_active(self, active: bool) -> None:
        self._active = active
        self.setProperty("active", active)
        self.style().unpolish(self)
        self.style().polish(self)
        self._refresh_icon()

    def refresh_theme(self) -> None:
        """主题变化时刷新图标。"""
        self._refresh_icon()
        # 徽标用 QSS property 驱动,重新 polish 以刷新配色
        if self._badge_label.isVisible():
            self._badge_label.style().unpolish(self._badge_label)
            self._badge_label.style().polish(self._badge_label)

    def enterEvent(self, event) -> None:
        self._hover = True
        self._refresh_icon()
        if self._shortcut_label is not None:
            self._layout_shortcut()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hover = False
        self._refresh_icon()
        if self._shortcut_label is not None:
            self._shortcut_label.setVisible(False)
        super().leaveEvent(event)

    def _icon_color(self) -> str:
        c = get_theme()
        if self._active:
            return c.accent_primary
        if self._hover:
            return c.text_primary
        return c.text_secondary

    def _refresh_icon(self) -> None:
        self.setIcon(_render_svg_icon(self._svg_path, self._icon_color()))


class ThemeButton(QPushButton):
    """主题切换按钮。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(42)
        self.setObjectName("themeButton")
        self.setIconSize(QSize(18, 18))
        self._update_text()
        self._refresh_icon()

    def _update_text(self) -> None:
        self.setText("浅色模式" if is_dark() else "深色模式")
        # tooltip 明确当前态与目标态,并提示快捷键
        current = "深色" if is_dark() else "浅色"
        target = "浅色" if is_dark() else "深色"
        self.setToolTip(f"当前:{current}模式 · 点击切换到{target}(Ctrl+T)")

    def _current_icon(self) -> QIcon:
        path = _SUN_PATH if is_dark() else _MOON_PATH
        return _render_svg_icon(path, get_theme().text_secondary, size=18)

    def refresh(self) -> None:
        self._update_text()
        self._refresh_icon()

    def _refresh_icon(self) -> None:
        self.setIcon(self._current_icon())


class Sidebar(QFrame):
    """侧边栏导航组件。"""

    page_changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(200)
        self.setObjectName("sidebar")
        self._current_page = "dashboard"
        self._nav_buttons: dict[str, NavButton] = {}
        self._build_ui()

    def _build_ui(self) -> None:
        container = QWidget(self)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(8, 12, 8, 12)
        layout.setSpacing(2)

        # App header — 品牌区
        header = QWidget()
        header.setObjectName("sidebarBrandHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(6, 2, 6, 10)
        header_layout.setSpacing(8)

        # Logo 图标
        self._logo_label = QLabel()
        self._logo_label.setFixedSize(32, 32)
        self._logo_label.setObjectName("sidebarLogo")
        self._refresh_logo()

        # 标题文字区
        title_box = QWidget()
        title_layout = QVBoxLayout(title_box)
        title_layout.setContentsMargins(0, 0, 0, 0)
        title_layout.setSpacing(1)
        self._header_title = QLabel("LSC")
        self._header_title.setObjectName("sidebarTitle")
        self._header_subtitle = QLabel("直播切片系统")
        self._header_subtitle.setObjectName("sidebarSubtitle")
        title_layout.addWidget(self._header_title)
        title_layout.addWidget(self._header_subtitle)

        header_layout.addWidget(self._logo_label)
        header_layout.addWidget(title_box, 1)
        layout.addWidget(header)

        sep = QFrame()
        sep.setObjectName("h_line")
        sep.setFixedHeight(1)
        layout.addWidget(sep)
        layout.addSpacing(6)

        for item in _NAV_ITEMS:
            page_key, label, svg_path, shortcut = item
            btn = NavButton(page_key, label, svg_path, shortcut, self)
            btn.clicked.connect(lambda checked, k=page_key: self._on_nav_click(k))
            self._nav_buttons[page_key] = btn
            layout.addWidget(btn)
        self._workbench_btn = self._nav_buttons.get("workbench")

        layout.addStretch()

        self._theme_button = ThemeButton(self)
        self._theme_button.clicked.connect(self._on_theme_toggle)
        layout.addWidget(self._theme_button)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(container)

        self._update_active()
        connect_theme_changed(self._refresh_theme)

    def _refresh_theme(self) -> None:
        """主题变化时刷新侧边栏图标。"""
        self._refresh_logo()
        self._theme_button.refresh()
        self._refresh_nav_buttons()
        self.update()

    def _refresh_logo(self) -> None:
        """刷新 Logo 图标。"""
        c = get_theme()
        icon = _render_svg_icon(_LOGO_PATH, c.accent_primary, size=28)
        pixmap = icon.pixmap(28, 28)
        self._logo_label.setPixmap(pixmap)

    def _on_nav_click(self, page_key: str) -> None:
        self._current_page = page_key
        self._update_active()
        self.page_changed.emit(page_key)

    def _update_active(self) -> None:
        for key, btn in self._nav_buttons.items():
            btn.set_active(key == self._current_page)

    def _refresh_nav_buttons(self) -> None:
        for btn in self._nav_buttons.values():
            btn.refresh_theme()

    def _on_theme_toggle(self) -> None:
        toggle_theme()
        # 持久化到 QSettings，使下次启动保持一致
        settings = QSettings("LSC", "LiveStreamClipper")
        settings.setValue("theme", "深色" if is_dark() else "浅色")
        self._theme_button.refresh()
        self._refresh_nav_buttons()
        self.update()

    def set_current_page(self, page_key: str) -> None:
        if page_key in self._nav_buttons:
            self._current_page = page_key
            self._update_active()

    def update_workbench_badge(self, recording: int, errors: int) -> None:
        """更新工作台导航项的状态徽标。

        优先显示错误数(红),其次录制数(橙)。两者皆 0 则隐藏徽标,
        让用户在任意页面都能感知工作台有待处理状态。
        """
        btn = getattr(self, "_workbench_btn", None)
        if btn is None:
            return
        if errors > 0:
            btn.set_badge(str(errors), kind="error")
        elif recording > 0:
            btn.set_badge(str(recording), kind="recording")
        else:
            btn.set_badge("")

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = get_theme()
        painter.fillRect(self.rect(), QColor(c.bg_secondary))
        pen = QPen(QColor(c.border_subtle))
        pen.setWidthF(1.0)
        painter.setPen(pen)
        painter.drawLine(self.width() - 1, 0, self.width() - 1, self.height())
        painter.end()
