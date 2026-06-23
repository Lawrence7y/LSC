"""LSC Theme System — Design Tokens from ui-language-unification-design.md"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import QObject, Signal


@dataclass(frozen=True)
class ThemeColors:
    bg_primary: str
    bg_secondary: str
    bg_tertiary: str
    bg_elevated: str
    text_primary: str
    text_secondary: str
    text_tertiary: str
    border_subtle: str
    border_default: str
    border_strong: str
    accent_primary: str
    accent_primary_dim: str
    accent_primary_glow: str
    accent_secondary: str
    accent_secondary_dim: str
    accent_success: str
    accent_success_dim: str
    accent_warning: str
    accent_warning_dim: str
    accent_error: str
    accent_error_dim: str


DARK = ThemeColors(
    bg_primary="#0c0e12",
    bg_secondary="#14161c",
    bg_tertiary="#1c1f28",
    bg_elevated="#242733",
    text_primary="#f0f1f5",
    text_secondary="#9aa0b0",
    text_tertiary="#5c6270",
    border_subtle="rgba(255,255,255,0.06)",
    border_default="rgba(255,255,255,0.10)",
    border_strong="rgba(255,255,255,0.15)",
    accent_primary="#ff8c42",
    accent_primary_dim="rgba(255,140,66,0.15)",
    accent_primary_glow="rgba(255,140,66,0.35)",
    accent_secondary="#5b8def",
    accent_secondary_dim="rgba(91,141,239,0.12)",
    accent_success="#3dd598",
    accent_success_dim="rgba(61,213,152,0.12)",
    accent_warning="#ffc542",
    accent_warning_dim="rgba(255,197,66,0.12)",
    accent_error="#ff6b6b",
    accent_error_dim="rgba(255,107,107,0.12)",
)

LIGHT = ThemeColors(
    bg_primary="#f5f6f8",
    bg_secondary="#ffffff",
    bg_tertiary="#eef0f4",
    bg_elevated="#ffffff",
    text_primary="#1a1d26",
    text_secondary="#5c6270",
    text_tertiary="#6e7686",
    border_subtle="rgba(0,0,0,0.06)",
    border_default="rgba(0,0,0,0.10)",
    border_strong="rgba(0,0,0,0.15)",
    accent_primary="#e6722f",
    accent_primary_dim="rgba(230,114,47,0.12)",
    accent_primary_glow="rgba(230,114,47,0.25)",
    accent_secondary="#4a7de4",
    accent_secondary_dim="rgba(74,125,228,0.12)",
    accent_success="#2cb980",
    accent_success_dim="rgba(44,185,128,0.12)",
    accent_warning="#e5a830",
    accent_warning_dim="rgba(229,168,48,0.12)",
    accent_error="#e05050",
    accent_error_dim="rgba(224,80,80,0.12)",
)

_current = DARK
_rebuild_callback: Callable[[], None] | None = None
_stylesheet_cache: dict[bool, str] = {}


class _ThemeNotifier(QObject):
    theme_changed = Signal()


_theme_notifier: _ThemeNotifier | None = None


def _get_notifier() -> _ThemeNotifier:
    global _theme_notifier
    if _theme_notifier is None:
        _theme_notifier = _ThemeNotifier()
    return _theme_notifier


def connect_theme_changed(slot: Callable[[], None]) -> None:
    """连接主题切换通知信号。"""
    _get_notifier().theme_changed.connect(slot)


def get_theme() -> ThemeColors:
    return _current


def register_rebuild_callback(cb: Callable[[], None]) -> None:
    global _rebuild_callback
    _rebuild_callback = cb


def is_dark() -> bool:
    return _current is DARK


def toggle_theme() -> None:
    global _current
    _current = LIGHT if _current is DARK else DARK
    _refresh_app_style()


def set_dark(dark: bool) -> None:
    global _current
    _current = DARK if dark else LIGHT
    _refresh_app_style()


def _apply_palette() -> None:
    """根据当前主题设置应用 QPalette，保证 QScrollArea viewport 等原生组件背景正确。"""
    try:
        from PySide6.QtGui import QBrush, QColor, QGuiApplication, QPalette

        c = get_theme()
        bg = QColor(c.bg_primary)
        text = QColor(c.text_primary)
        base = QColor(c.bg_secondary)
        alternate = QColor(c.bg_tertiary)
        button = QColor(c.bg_tertiary)
        highlight = QColor(c.accent_primary)

        palette = QPalette()
        palette.setColor(QPalette.ColorRole.Window, bg)
        palette.setColor(QPalette.ColorRole.WindowText, text)
        palette.setColor(QPalette.ColorRole.Base, base)
        palette.setColor(QPalette.ColorRole.AlternateBase, alternate)
        palette.setColor(QPalette.ColorRole.Text, text)
        palette.setColor(QPalette.ColorRole.Button, button)
        palette.setColor(QPalette.ColorRole.ButtonText, text)
        palette.setColor(QPalette.ColorRole.Highlight, highlight)
        palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
        palette.setBrush(QPalette.ColorRole.PlaceholderText, QBrush(QColor(c.text_tertiary)))

        app = QGuiApplication.instance()
        if app is not None:
            app.setPalette(palette)
    except Exception:
        pass


def _refresh_app_style() -> None:
    import logging
    try:
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        _apply_palette()
        if app:
            dark = is_dark()
            ss = _stylesheet_cache.get(dark)
            if ss is None:
                ss = generate_stylesheet(_current, dark=dark)
                _stylesheet_cache[dark] = ss
            app.setStyleSheet(ss)
            # Use batch update with deferred processing to reduce flicker
            # Only update visible widgets immediately, defer hidden ones
            visible_widgets = []
            hidden_widgets = []
            for w in app.allWidgets():
                if w.isVisible():
                    visible_widgets.append(w)
                else:
                    hidden_widgets.append(w)

            # Update visible widgets immediately
            for w in visible_widgets:
                try:
                    w.style().unpolish(w)
                    w.style().polish(w)
                    w.update()
                except Exception:
                    pass

            # Defer hidden widgets update to avoid blocking
            if hidden_widgets:
                from PySide6.QtCore import QTimer

                def _update_hidden():
                    for w in hidden_widgets:
                        try:
                            if not w.isVisible():
                                continue  # Still hidden, skip
                            w.style().unpolish(w)
                            w.style().polish(w)
                            w.update()
                        except Exception:
                            pass

                QTimer.singleShot(50, _update_hidden)

        if _rebuild_callback:
            _rebuild_callback()
        _get_notifier().theme_changed.emit()
    except Exception as exc:
        logging.getLogger(__name__).warning("刷新主题样式失败: %s", exc)


def get_option_button_palette(
    c: ThemeColors,
    *,
    active: bool = False,
    hover: bool = False,
    dark: bool | None = None,
) -> dict[str, str]:
    if dark is None:
        dark = c == DARK
    if active:
        return {"border": c.accent_primary, "background": c.accent_primary_dim, "text": c.accent_primary}
    if dark:
        if hover:
            return {"border": c.border_default, "background": c.bg_elevated, "text": c.text_primary}
        return {"border": c.border_default, "background": "transparent", "text": c.text_secondary}
    if hover:
        return {"border": c.accent_primary, "background": c.accent_primary_dim, "text": c.text_primary}
    return {"border": c.accent_primary, "background": c.bg_secondary, "text": c.text_primary}


def generate_stylesheet(c: ThemeColors, *, dark: bool = True) -> str:
    bg_btn_idle = c.bg_tertiary if dark else c.bg_tertiary
    light_hover_bg = "#dfe5ee"
    light_hover_border = "rgba(0,0,0,0.22)"
    bg_btn_hover = c.bg_elevated if dark else light_hover_bg
    text_btn_idle = c.text_secondary if dark else c.text_primary
    text_btn_hover = c.text_primary
    border_btn = c.border_default if dark else c.border_strong
    hover_border = c.border_strong if dark else light_hover_border
    disabled_text = c.text_tertiary if dark else "#6f7685"
    disabled_bg = c.bg_secondary if dark else "#e3e7ee"
    disabled_border = c.border_subtle if dark else c.border_strong

    return f"""
    * {{
        font-family: "Microsoft YaHei", "Microsoft YaHei UI", "SimHei", "PingFang SC", "Noto Sans SC", "Source Han Sans SC", system-ui, -apple-system, sans-serif;
        outline: none;
    }}
    QMainWindow {{ background: {c.bg_primary}; }}
    QWidget#mainCentralWidget {{ background: {c.bg_primary}; }}
    QWidget {{ background: transparent; color: {c.text_primary}; }}

    /* ── 焦点可见性(键盘导航可访问性)── 用边框高亮,避免布局抖动 */
    QPushButton:focus, QToolButton:focus, QCheckBox:focus, QRadioButton:focus,
    QComboBox:focus, QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus,
    QSlider:focus, QScrollBar:focus, QListWidget:focus, QTreeWidget:focus,
    QSpinBox:focus, QDoubleSpinBox:focus {{
        border-color: {c.accent_primary};
    }}
    QPushButton:focus {{
        outline: none;
    }}
    QScrollArea {{ border: none; background: {c.bg_primary}; }}
    QScrollArea QWidget#qt_scrollarea_viewport {{ background: {c.bg_primary}; border: none; }}

    QScrollBar:vertical {{
        background: transparent; width: 6px; margin: 0px;
    }}
    QScrollBar::handle:vertical {{
        background: {c.border_default}; border-radius: 3px; min-height: 24px;
    }}
    QScrollBar::handle:vertical:hover {{ background: {c.text_tertiary}; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QScrollBar:horizontal {{
        background: transparent; height: 6px; margin: 0px;
    }}
    QScrollBar::handle:horizontal {{
        background: {c.border_default}; border-radius: 3px; min-width: 24px;
    }}
    QScrollBar::handle:horizontal:hover {{ background: {c.text_tertiary}; }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}

    QLineEdit {{
        background: {c.bg_tertiary}; border: 1px solid {c.border_default};
        border-radius: 8px; padding: 8px 12px; color: {c.text_primary};
        font-size: 13px; min-height: 36px; max-height: 36px;
    }}
    QLineEdit:focus {{ border-color: {c.accent_primary}; background: {c.bg_secondary if dark else c.bg_elevated}; }}
    QLineEdit::placeholder {{ color: {c.text_tertiary}; }}
    QLineEdit:disabled {{ color: {disabled_text}; background: {disabled_bg}; }}

    QLabel {{ color: {c.text_primary}; background: transparent; }}
    QLabel#label_primary {{ color: {c.text_primary}; }}
    QLabel#label_secondary {{ color: {c.text_secondary}; }}
    QLabel#label_tertiary {{ color: {c.text_tertiary}; }}
    QLabel#label_accent {{ color: {c.accent_primary}; }}
    QLabel#label_mono {{
        font-size: 11px; color: {c.text_tertiary};
        font-family: 'JetBrains Mono', 'Cascadia Code', Consolas, monospace;
    }}
    QLabel#label_size {{
        font-size: 11px; color: {c.text_tertiary};
    }}
    QLabel#time_label {{
        font-size: 14px; font-weight: 600; color: {c.text_primary};
        font-family: 'JetBrains Mono', 'Cascadia Code', Consolas, monospace;
    }}
    QLabel#card_title {{
        font-size: 15px; font-weight: 600; color: {c.text_primary}; padding-bottom: 4px;
    }}
    QLabel#section_title {{
        font-size: 15px; font-weight: 600; color: {c.text_primary};
        border-bottom: 1px solid {c.border_subtle};
        padding-bottom: 6px;
    }}
    QLabel#page_title {{
        font-size: 22px; font-weight: 700; color: {c.text_primary};
    }}
    QLabel#sidebarTitle {{
        font-size: 18px; font-weight: 700; color: {c.text_primary}; background: transparent;
    }}
    QLabel#sidebarSubtitle {{
        font-size: 11px; color: {c.text_tertiary}; background: transparent;
    }}
    QLabel#page_subtitle {{
        font-size: 13px; color: {c.text_secondary}; background: transparent;
    }}
    QLabel#stat_value {{
        font-size: 26px; font-weight: 700;
    }}
    QFrame#h_line {{
        background: {c.border_subtle if dark else c.border_default};
        max-height: 1px;
        border: none;
    }}
    QFrame#v_line {{
        background: {c.border_subtle if dark else c.border_default};
        max-width: 1px;
        border: none;
    }}
    QLabel#info_label {{
        font-size: 11px; color: {c.text_tertiary};
    }}
    QLabel#info_value {{
        font-size: 12px; color: {c.text_secondary}; font-weight: 500;
    }}
    QLabel#duration_badge {{
        background: rgba(0,0,0,0.65); color: #ffffff; border-radius: 4px;
        font-size: 10px; font-weight: 600; padding: 2px 6px;
    }}
    QLabel#card_thumb {{ color: {c.text_tertiary}; background: transparent; }}
    QLabel#card_info {{ color: {c.text_secondary}; background: transparent; }}
    QLabel#empty_state {{
        color: {disabled_text}; font-size: 13px; padding: 60px;
        line-height: 1.6;
    }}

    QLabel#roomLimitBadge {{
        font-size: 11px; color: {c.text_secondary}; background: {c.bg_tertiary};
        padding: 2px 8px; border-radius: 10px;
    }}

    QWidget#detailPanel {{
        background: {c.bg_primary}; border: none;
    }}
    QLabel#detailPanelHeader {{
        font-size: 13px; font-weight: 600; color: {c.text_primary};
        padding: 14px 16px; border-bottom: 1px solid {c.border_subtle};
        background: transparent;
    }}
    QScrollArea#detailPanelScroll {{
        background: {c.bg_primary}; border: none;
    }}
    QScrollArea#detailPanelScroll QWidget#qt_scrollarea_viewport {{
        background: {c.bg_primary}; border: none;
    }}
    QWidget#detailPanelBody {{
        background: {c.bg_primary}; border: none;
    }}
    QLabel#detailEmptyIcon {{
        font-size: 32px; color: {c.text_tertiary}; background: transparent;
    }}
    QLabel#detailEmptyText {{
        font-size: 13px; color: {c.text_secondary}; background: transparent;
    }}
    QLabel#detailSectionHeader {{
        font-size: 10px; font-weight: 600; color: {c.text_tertiary};
        text-transform: uppercase; letter-spacing: 0.5px;
        margin-bottom: 6px; background: transparent;
    }}

    QWidget#multiRoomStatusBar {{
        background: transparent; border: none;
    }}
    QLabel#statusBarDot {{
        font-size: 7px; padding-bottom: 1px; background: transparent;
    }}
    QLabel#statusBarLabel {{
        font-size: 11px; color: {c.text_tertiary}; background: transparent;
    }}
    QLabel#statusBarValue {{
        font-size: 12px; font-weight: 600; color: {c.text_primary}; background: transparent;
    }}
    QLabel#statusBarMessage {{
        font-size: 11px; color: {c.text_secondary}; background: transparent;
    }}
    QLabel#statusBarError {{
        font-size: 11px; color: {c.accent_error}; background: transparent;
    }}

    QPushButton {{
        background: {bg_btn_idle}; color: {text_btn_idle};
        border: 1px solid {border_btn}; border-radius: 18px;
        font-size: 13px; min-height: 36px; max-height: 36px;
        padding: 0 16px;
    }}
    QPushButton:hover {{
        background: {bg_btn_hover}; color: {text_btn_hover};
        border-color: {hover_border};
    }}
    QPushButton:pressed {{ background: {c.bg_tertiary}; border-color: {c.border_default}; }}
    QPushButton:disabled {{ color: {disabled_text}; border-color: {disabled_border}; background: {disabled_bg}; }}
    QPushButton:checked {{
        background: {c.accent_primary_dim}; color: {c.accent_primary};
        border-color: {c.accent_primary}; font-weight: 500;
    }}
    QPushButton:focus {{
        border-color: {c.accent_primary};
    }}

    QPushButton#btnPrimary {{
        background: {c.accent_primary_dim}; color: {c.accent_primary};
        border: 1px solid {c.accent_primary}; border-radius: 18px; font-weight: 500;
    }}
    QPushButton#btnPrimary:hover {{ background: {c.accent_primary_dim}; color: {c.accent_primary}; border-color: {c.accent_primary}; }}
    QPushButton#btnPrimary:pressed {{ background: {c.accent_primary_dim}; color: {c.accent_primary}; border-color: {c.accent_primary}; }}
    QPushButton#btnPrimary:disabled {{ background: {disabled_bg}; color: {disabled_text}; border: 1px solid {disabled_border}; }}

    QPushButton#btnSecondary {{
        background: {c.bg_tertiary if not dark else 'transparent'}; color: {c.text_secondary};
        border: 1px solid {c.border_default if dark else c.border_strong};
        border-radius: 18px;
    }}
    QPushButton#btnSecondary:hover {{
        background: {c.bg_elevated if dark else light_hover_bg}; color: {c.text_primary};
        border-color: {hover_border};
    }}
    QPushButton#btnSecondary:disabled {{ color: {disabled_text}; border-color: {disabled_border}; background: {disabled_bg}; }}

    QPushButton#addRoomButton {{
        background: {c.accent_primary_dim}; color: {c.accent_primary};
        border: 1px solid {c.accent_primary}; border-radius: 18px;
        font-weight: 500; min-height: 36px; max-height: 36px;
    }}
    QPushButton#addRoomButton:hover {{
        background: {c.accent_primary_dim}; color: {c.accent_primary}; border-color: {c.accent_primary};
    }}
    QPushButton#addRoomButton:pressed {{
        background: {c.accent_primary_dim}; color: {c.accent_primary}; border-color: {c.accent_primary};
    }}
    QPushButton#addRoomButton:disabled {{ background: {disabled_bg}; color: {disabled_text}; border: 1px solid {disabled_border}; }}

    /* 控制栏专用副按钮：更小的左右内边距，防止底部栏按钮被截断 */
    QPushButton#ctrlSecondary {{
        background: {c.bg_tertiary if not dark else 'transparent'}; color: {c.text_secondary};
        border: 1px solid {c.border_default if dark else c.border_strong};
        border-radius: 6px; padding: 0 8px; font-size: 12px; min-height: 28px;
    }}
    QPushButton#ctrlSecondary:hover {{
        background: {c.bg_elevated if dark else light_hover_bg}; color: {c.text_primary};
        border-color: {hover_border};
    }}
    QPushButton#ctrlSecondary:disabled {{ color: {disabled_text}; border-color: {disabled_border}; background: {disabled_bg}; }}

    QPushButton#btnSuccess {{
        background: {c.accent_success_dim if not dark else 'transparent'}; color: {c.accent_success};
        border: 1px solid {c.accent_success};
    }}
    QPushButton#btnSuccess:hover {{
        background: {c.accent_success_dim}; color: {c.accent_success};
    }}
    QPushButton#btnSuccess:checked {{
        background: {c.accent_success}; color: #ffffff; border-color: {c.accent_success};
    }}

    QPushButton#btnDanger {{
        background: {c.accent_error_dim if not dark else 'transparent'}; color: {c.accent_error};
        border: 1px solid {c.accent_error};
    }}
    QPushButton#btnDanger:hover {{
        background: {c.accent_error_dim}; color: {c.accent_error};
    }}
    QPushButton#btnDanger:checked {{
        background: {c.accent_error}; color: #ffffff; border-color: {c.accent_error};
    }}

    QPushButton#navButton {{
        background: transparent;
        color: {c.text_secondary};
        border: 1px solid transparent;
        border-left: 3px solid transparent;
        border-radius: 8px;
        font-size: 13px;
        font-weight: 500;
        text-align: left;
        padding-left: 35px;
        min-height: 42px;
        max-height: 42px;
    }}
    QPushButton#navButton:hover {{
        background: {c.bg_elevated if dark else c.bg_tertiary};
        color: {c.text_primary};
        border-color: {c.border_default};
    }}
    QPushButton#navButton[active="true"] {{
        background: {c.accent_primary_dim};
        color: {c.accent_primary};
        border-color: {c.accent_primary};
        border-left: 3px solid {c.accent_primary};
    }}
    QPushButton#navButton[active="true"]:hover {{
        background: {c.accent_primary_dim};
        color: {c.accent_primary};
        border-color: {c.accent_primary};
        border-left: 3px solid {c.accent_primary};
    }}

    QPushButton#themeButton {{
        background: transparent;
        color: {c.text_secondary};
        border: 1px solid {c.accent_primary};
        border-radius: 8px;
        font-size: 13px;
        font-weight: 500;
        text-align: left;
        padding-left: 38px;
        min-height: 42px;
        max-height: 42px;
    }}
    QPushButton#themeButton:hover {{
        background: {c.accent_primary_dim};
        color: {c.text_primary};
    }}

    /* 导航项状态徽标 */
    QLabel#navBadge {{
        background: {c.accent_primary};
        color: #ffffff;
        border-radius: 8px;
        font-size: 11px;
        font-weight: 600;
        padding: 1px 6px;
        min-width: 16px;
    }}
    QLabel#navBadge[kind="recording"] {{
        background: {c.accent_warning};
    }}
    QLabel#navBadge[kind="error"] {{
        background: {c.accent_error};
    }}

    QFrame#roomCard {{
        background: {c.bg_secondary}; border: 1px solid {c.border_subtle if dark else c.border_default};
        border-radius: 12px;
    }}
    QFrame#roomCard:hover {{
        border-color: {c.border_default if dark else c.border_strong};
    }}
    QFrame#roomCardSelected {{
        background: {c.bg_secondary}; border: 2px solid {c.accent_primary};
        border-radius: 12px;
    }}
    QFrame#roomCardTopBar {{
        background: transparent; border-radius: 0;
    }}
    QFrame#roomCardSelected QFrame#roomCardTopBar {{
        background: {c.accent_primary};
    }}

    QLabel#roomCardTitle {{
        font-size: 13px; font-weight: 600; color: {c.text_primary}; background: transparent;
    }}
    QLabel#roomCardStatus {{
        font-size: 11px; color: {c.text_tertiary}; background: transparent;
    }}
    QFrame#roomCardPreviewArea {{
        background: {c.bg_tertiary}; border-radius: 14px;
    }}
    QSplitter::handle:horizontal {{
        background: transparent;
        width: 8px;
        margin: 4px 1px;
        border-radius: 4px;
    }}
    QSplitter::handle:horizontal:hover {{
        background: {c.accent_primary_dim};
    }}

    /* 录制页预览容器(复用 PreviewSurface) */
    QFrame#recordPreviewArea {{
        background: {c.bg_tertiary}; border: 1px solid {c.border_subtle}; border-radius: 14px;
    }}
    QLabel#recordPreviewPlaceholder {{
        color: {c.text_tertiary}; background: transparent; font-size: 13px;
    }}
    QWidget#recordPreviewOverlay {{
        background: rgba(0, 0, 0, 110); border-radius: 8px;
    }}
    QWidget#recordPreviewOverlay QPushButton {{
        color: #ffffff;
    }}
    QWidget#roomCardPreviewControls {{
        background: transparent; border: none;
        padding: 0px;
    }}
    QWidget#roomCardPreviewControls QPushButton#roomCardSmallBtn {{
        background: {c.bg_tertiary}; color: {c.text_secondary};
        border: 1px solid {c.border_default}; border-radius: 14px;
        font-size: 11px; min-height: 28px; max-height: 28px; padding: 0 10px;
    }}
    QWidget#roomCardPreviewControls QPushButton#roomCardSmallBtn:hover {{
        background: {c.bg_elevated if dark else light_hover_bg}; color: {c.text_primary};
        border-color: {hover_border};
    }}
    QWidget#roomCardPreviewControls QCheckBox#roomCardCheckBox {{
        color: {c.text_secondary}; background: transparent; font-size: 11px; spacing: 4px;
    }}
    QWidget#roomCardPreviewControls QCheckBox#roomCardCheckBox::indicator {{
        width: 14px; height: 14px;
        border: 1px solid {c.border_default}; border-radius: 3px;
        background: {c.bg_tertiary};
    }}
    QWidget#roomCardPreviewControls QCheckBox#roomCardCheckBox::indicator:checked {{
        background: {c.accent_primary}; border-color: {c.accent_primary};
    }}
    QPushButton#previewFullscreenButton {{
        background: rgba(0,0,0,0.45); color: #ffffff;
        border: 1px solid rgba(255,255,255,0.18); border-radius: 8px;
    }}
    QPushButton#previewFullscreenButton:hover {{
        background: rgba(0,0,0,0.68); border-color: rgba(255,255,255,0.35);
    }}
    QWidget#roomCardPreviewEmbed {{
        background: transparent; border: none;
    }}
    QLabel#roomCardPlaceholderIcon {{
        font-size: 28px; color: {c.accent_primary}; background: transparent;
    }}
    QLabel#roomCardPlaceholderText {{
        font-size: 13px; font-weight: 500; color: {c.text_primary}; background: transparent;
    }}
    QLabel#roomCardBadge {{
        font-size: 10px; font-weight: 600; color: #ffffff;
        border-radius: 4px; padding: 2px 6px;
    }}
    QLabel#roomCardBadgeRec {{
        font-size: 10px; font-weight: 600; color: #ffffff;
        background: {c.accent_error}; border-radius: 4px; padding: 2px 6px;
    }}
    QLabel#roomCardBadgeMute {{
        font-size: 10px; font-weight: 600; color: #ffffff;
        background: {c.text_tertiary}; border-radius: 4px; padding: 2px 6px;
    }}

    QPushButton#roomCardSmallBtn {{
        background: {c.bg_tertiary}; color: {c.text_secondary};
        border: 1px solid {c.border_default}; border-radius: 14px;
        font-size: 11px; min-height: 28px; max-height: 28px; padding: 0 10px;
    }}
    QPushButton#roomCardSmallBtn:hover {{
        background: {c.bg_elevated if dark else light_hover_bg}; color: {c.text_primary};
        border-color: {hover_border};
    }}
    QPushButton#roomCardSmallBtn:pressed {{
        background: {c.bg_tertiary}; border-color: {c.border_default};
    }}
    QPushButton#roomCardSmallBtn:disabled {{
        color: {disabled_text}; border-color: {disabled_border}; background: {disabled_bg};
    }}

    QPushButton#roomCardActionBtn {{
        background: {c.accent_primary_dim}; color: {c.accent_primary};
        border: 1px solid {c.accent_primary}; border-radius: 15px;
        font-size: 12px; font-weight: 500; min-height: 30px; max-height: 30px; padding: 0 12px;
    }}
    QPushButton#roomCardActionBtn:hover {{
        background: {c.accent_primary_dim}; color: {c.accent_primary}; border-color: {c.accent_primary};
    }}
    QPushButton#roomCardActionBtn:pressed {{
        background: {c.accent_primary_dim}; color: {c.accent_primary}; border-color: {c.accent_primary_dim};
    }}
    QPushButton#roomCardActionBtn:disabled {{
        color: {disabled_text}; border-color: {disabled_border}; background: {disabled_bg};
    }}

    QPushButton#roomCardActionBtnDanger {{
        background: {c.accent_error_dim}; color: {c.accent_error};
        border: 1px solid {c.accent_error}; border-radius: 15px;
        font-size: 12px; font-weight: 500; min-height: 30px; max-height: 30px; padding: 0 12px;
    }}
    QPushButton#roomCardActionBtnDanger:hover {{
        background: {c.accent_error_dim}; color: {c.accent_error}; border-color: {c.accent_error};
    }}
    QPushButton#roomCardActionBtnDanger:pressed {{
        background: {c.accent_error_dim}; color: {c.accent_error}; border-color: {c.accent_error_dim};
    }}

    QPushButton#roomCardActionBtnWarning {{
        background: {c.accent_warning_dim}; color: {c.accent_warning};
        border: 1px solid {c.accent_warning}; border-radius: 15px;
        font-size: 12px; font-weight: 500; min-height: 30px; max-height: 30px; padding: 0 12px;
    }}
    QPushButton#roomCardActionBtnWarning:hover {{
        background: {c.accent_warning_dim}; color: {c.accent_warning}; border-color: {c.accent_warning};
    }}
    QPushButton#roomCardActionBtnWarning:pressed {{
        background: {c.accent_warning_dim}; color: {c.accent_warning}; border-color: {c.accent_warning_dim};
    }}

    QPushButton#roomCardRemoveBtn {{
        background: transparent; color: {c.text_tertiary};
        border: none; border-radius: 4px; font-size: 16px; font-weight: 600;
        min-width: 22px; max-width: 22px; min-height: 22px; max-height: 22px;
    }}
    QPushButton#roomCardRemoveBtn:hover {{
        background: {c.accent_error_dim}; color: {c.accent_error};
    }}

    QCheckBox#roomCardCheckBox {{
        font-size: 11px; color: {c.text_secondary}; background: transparent;
        spacing: 4px;
    }}
    QCheckBox#roomCardCheckBox::indicator {{
        width: 14px; height: 14px;
        border: 1px solid {c.border_default}; border-radius: 3px;
        background: {c.bg_tertiary};
    }}
    QCheckBox#roomCardCheckBox::indicator:checked {{
        background: {c.accent_primary}; border-color: {c.accent_primary};
    }}

    QWidget#fullscreenPreviewWindow {{
        background: {c.bg_primary}; border: none;
    }}
    QWidget#fullscreenPreviewSurface {{
        background: #000000; border: none;
    }}
    QWidget#fullscreenPlayerControls {{
        background: transparent;
        border: none;
        border-radius: 0px;
    }}
    QWidget#fullscreenPlayerControls QLabel#fullscreenTimeLabel {{
        color: #ffffff;
        font-size: 12px;
        font-family: 'JetBrains Mono', 'Cascadia Code', Consolas, monospace;
    }}
    QWidget#fullscreenPlayerControls QCheckBox#fullscreenMuteButton {{
        color: #ffffff;
        background: transparent;
        font-size: 12px;
        spacing: 5px;
    }}
    QWidget#fullscreenPlayerControls QCheckBox#fullscreenMuteButton::indicator {{
        width: 14px; height: 14px;
        border: 1px solid rgba(255,255,255,0.38);
        border-radius: 3px;
        background: rgba(0,0,0,0.22);
    }}
    QWidget#fullscreenPlayerControls QCheckBox#fullscreenMuteButton::indicator:checked {{
        background: {c.accent_primary}; border-color: {c.accent_primary};
    }}
    QSlider#fullscreenProgressSlider::groove:horizontal {{
        background: rgba(255,255,255,0.34);
        height: 4px;
        border-radius: 2px;
    }}
    QSlider#fullscreenProgressSlider::sub-page:horizontal {{
        background: {c.accent_primary};
        border-radius: 2px;
    }}
    QSlider#fullscreenProgressSlider::handle:horizontal {{
        background: #ffffff;
        width: 10px;
        height: 10px;
        margin: -3px 0;
        border-radius: 5px;
    }}
    QPushButton#fullscreenPlayButton,
    QPushButton#fullscreenExitButton,
    QPushButton#fullscreenMinimizeButton {{
        background: transparent;
        border: none;
        padding: 0px;
    }}
    QPushButton#fullscreenPlayButton:hover,
    QPushButton#fullscreenExitButton:hover,
    QPushButton#fullscreenMinimizeButton:hover {{
        background: transparent;
        border: none;
    }}

    QFrame#card {{
        background: {c.bg_secondary}; border: 1px solid {c.border_subtle if dark else c.border_default};
        border-radius: 12px;
    }}

    /* ── Dashboard cards ── */
    QFrame#dashboardStatCard {{
        background: {c.bg_secondary}; border: 1px solid {c.border_subtle};
        border-radius: 12px;
    }}
    QFrame#dashboardStatAccentBar {{
        background: {c.accent_primary}; border-radius: 2px;
        min-width: 4px; max-width: 4px;
    }}
    QFrame#dashboardStatAccentBar[accent="accent_primary"] {{ background: {c.accent_primary}; }}
    QFrame#dashboardStatAccentBar[accent="accent_success"] {{ background: {c.accent_success}; }}
    QFrame#dashboardStatAccentBar[accent="accent_warning"] {{ background: {c.accent_warning}; }}
    QFrame#dashboardStatAccentBar[accent="accent_secondary"] {{ background: {c.accent_secondary}; }}

    QLabel#dashboardStatValue {{
        font-size: 26px; font-weight: 700;
    }}
    QLabel#dashboardStatValue[accent="accent_primary"] {{ color: {c.accent_primary}; }}
    QLabel#dashboardStatValue[accent="accent_success"] {{ color: {c.accent_success}; }}
    QLabel#dashboardStatValue[accent="accent_warning"] {{ color: {c.accent_warning}; }}
    QLabel#dashboardStatValue[accent="accent_secondary"] {{ color: {c.accent_secondary}; }}

    QFrame#dashboardActionCard {{
        background: {c.bg_secondary}; border: 1px solid {c.border_subtle};
        border-radius: 12px;
    }}
    QFrame#dashboardActionCard:hover {{
        background: {c.bg_elevated}; border-color: {c.border_default};
    }}
    QFrame#dashboardActionCard:pressed {{
        background: {c.bg_tertiary}; border-color: {c.border_subtle};
    }}
    QFrame#dashboardActionCard:disabled {{
        opacity: 0.45;
    }}
    QLabel#dashboardActionCardTitle {{ font-size: 15px; font-weight: 600; color: {c.text_primary}; }}
    QLabel#dashboardActionCardDesc {{ font-size: 13px; color: {c.text_secondary}; }}

    QFrame#dashboardActionCardPrimary {{
        background: {c.bg_secondary}; border: 1px solid {c.border_subtle};
        border-radius: 12px;
    }}
    QFrame#dashboardActionCardPrimary:hover {{
        background: {c.bg_elevated}; border-color: {c.border_default};
    }}
    QFrame#dashboardActionCardPrimary:pressed {{
        background: {c.bg_tertiary}; border-color: {c.border_subtle};
    }}
    QFrame#dashboardActionCardPrimary:disabled {{
        opacity: 0.45;
    }}
    QLabel#dashboardActionCardPrimaryTitle {{ font-size: 15px; font-weight: 600; color: {c.text_primary}; }}
    QLabel#dashboardActionCardPrimaryDesc {{ font-size: 13px; color: {c.text_secondary}; }}

    QFrame#dashboardSessionCard {{
        background: {c.bg_secondary}; border: 1px solid {c.border_subtle};
        border-radius: 10px;
    }}
    QFrame#dashboardSessionCard:hover {{
        background: {c.bg_elevated}; border-color: {c.border_default};
    }}
    QFrame#dashboardSessionCard:pressed {{
        background: {c.bg_tertiary}; border-color: {c.border_subtle};
    }}
    QFrame#dashboardSessionAccentBar {{
        background: {c.accent_secondary}; border-radius: 2px;
        min-width: 3px; max-width: 3px;
    }}
    QFrame#dashboardSessionAccentBar[status="recording"] {{ background: {c.accent_success}; }}
    QFrame#dashboardSessionAccentBar[status="other"] {{ background: {c.accent_secondary}; }}
    QLabel#dashboardSessionTitle {{ font-size: 14px; font-weight: 600; color: {c.text_primary}; }}
    QLabel#dashboardSessionStatus {{
        font-size: 10px; font-weight: 600; color: #ffffff;
        padding: 2px 8px; border-radius: 4px;
    }}
    QLabel#dashboardSessionStatus[status="recording"] {{ background: {c.accent_success}; }}
    QLabel#dashboardSessionStatus[status="other"] {{ background: {c.accent_secondary}; }}

    /* InputField (styled QLineEdit inside the widget) */
    QWidget#inputField QLineEdit {{
        background: {c.bg_tertiary};
        border: 1px solid {c.border_default};
        border-radius: 8px;
        padding: 8px 12px;
        font-size: 13px;
        color: {c.text_primary};
        selection-background-color: {c.accent_primary_dim};
    }}
    QWidget#inputField QLineEdit:focus {{
        border: 1px solid {c.accent_primary};
    }}
    QWidget#inputField QLineEdit:disabled {{
        background: {c.bg_secondary}; color: {c.text_tertiary};
    }}

    /* ChipGroup: global style matching record page option groups */
    QWidget#chipGroup QPushButton {{
        min-height: 32px; max-height: 32px;
        padding: 0 14px;
        font-size: 12px;
        color: {c.text_secondary};
        background: {c.bg_tertiary};
        border: 1px solid {c.border_default};
        border-radius: 16px;
    }}
    QWidget#chipGroup QPushButton:checked {{
        color: {c.accent_primary};
        background: {c.accent_primary_dim};
        border: 1px solid {c.accent_primary};
    }}
    QWidget#chipGroup QPushButton:hover:!checked {{
        background: {c.bg_elevated};
        color: {c.text_primary};
    }}
    QWidget#chipGroup QPushButton:disabled {{
        background: {c.bg_secondary}; color: {c.text_tertiary}; border-color: {c.border_subtle};
    }}

    QStatusBar {{
        background: {c.bg_secondary}; color: {c.text_tertiary};
        font-size: 11px; border-top: 1px solid {c.border_subtle};
    }}

    QProgressBar {{
        background: {c.bg_tertiary};
        border: 1px solid {c.border_subtle};
        border-radius: 7px;
        text-align: center;
        font-size: 10px;
        color: {c.text_secondary};
    }}
    QProgressBar::chunk {{
        background: {c.accent_primary};
        border-radius: 6px;
    }}

    QMenu {{
        background: {c.bg_elevated}; color: {c.text_primary};
        border: 1px solid {c.border_default}; border-radius: 8px;
        padding: 6px;
    }}
    QMenu::item {{
        background: transparent; border-radius: 6px;
        padding: 6px 18px;
    }}
    QMenu::item:selected {{
        background: {c.accent_primary_dim}; color: {c.accent_primary};
    }}
    QMenu::separator {{
        background: {c.border_subtle}; height: 1px; margin: 4px 8px;
    }}

    QToolTip {{
        background: {c.bg_elevated}; color: {c.text_primary};
        border: 1px solid {c.border_default}; border-radius: 6px;
        padding: 4px 8px; font-size: 12px;
    }}

    QComboBox {{
        background: {c.bg_tertiary}; border: 1px solid {c.border_default};
        border-radius: 8px; padding: 6px 10px; color: {c.text_primary};
        font-size: 13px; min-height: 34px;
    }}
    QComboBox:focus {{ border-color: {c.accent_primary}; }}
    QComboBox::drop-down {{ border: none; width: 24px; }}
    QComboBox::down-arrow {{
        image: none;
        border-left: 4px solid transparent;
        border-right: 4px solid transparent;
        border-top: 5px solid {c.text_tertiary};
        width: 0px; height: 0px;
    }}
    QComboBox QAbstractItemView {{
        background: {c.bg_elevated}; color: {c.text_primary};
        selection-background-color: {c.accent_primary_dim};
        selection-color: {c.accent_primary};
        border: 1px solid {c.border_default}; border-radius: 8px;
        padding: 4px;
    }}

    QSlider::groove:horizontal {{
        background: {c.border_default}; height: 4px; border-radius: 2px;
    }}
    QSlider::handle:horizontal {{
        background: {c.accent_primary}; width: 14px; height: 14px;
        margin: -5px 0; border-radius: 7px;
    }}
    QSlider::handle:horizontal:hover {{
        background: {c.accent_primary}; width: 16px; height: 16px; margin: -6px 0;
    }}
    QSlider::sub-page:horizontal {{
        background: {c.accent_primary}; border-radius: 2px;
    }}
    QSlider:disabled {{ opacity: 0.4; }}

    QCheckBox {{ color: {c.text_secondary}; font-size: 12px; spacing: 6px; }}
    QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 4px; border: 1px solid {c.border_default}; background: {c.bg_tertiary}; }}
    QCheckBox::indicator:checked {{ background: {c.accent_primary}; border-color: {c.accent_primary}; image: none; }}
    QCheckBox::indicator:hover {{ border-color: {c.accent_primary}; }}

    /* ── Clip list ── */
    QWidget#clipList {{
        background: {c.bg_secondary};
        border-top: 1px solid {c.border_subtle};
    }}
    QWidget#clipList QPushButton {{
        font-size: 12px; padding: 0 12px; border-radius: 6px;
    }}
    QWidget#clipList QPushButton#btnPrimary {{
        background: {c.accent_primary}; color: white;
        border: none; font-weight: 500;
    }}
    QWidget#clipList QPushButton#btnPrimary:hover {{ background: {c.accent_primary}; }}
    QWidget#clipList QPushButton#btnPrimary:disabled {{
        background: {c.bg_tertiary}; color: {c.text_tertiary};
    }}
    QWidget#clipList QPushButton:disabled {{ color: {c.text_tertiary}; }}
    QWidget#clipList QScrollArea {{ background: transparent; border: none; }}
    QFrame#clipItem {{
        background: {c.bg_tertiary};
        border: 1px solid {c.border_subtle};
        border-radius: 6px;
    }}
    QFrame#clipItem:hover {{
        border-color: {c.border_default};
        background: {c.bg_elevated};
    }}
    QFrame#clipItem QLabel {{ background: transparent; border: none; color: {c.text_secondary}; }}
    QLabel#clipIdx {{ color: {c.accent_primary}; font-weight: 600; }}
    QLabel#clipTimeLabel {{
        font-family: 'JetBrains Mono', 'Cascadia Code', Consolas, monospace;
        font-size: 11px; background: transparent; border: none;
    }}
    QFrame#clipItem QPushButton {{
        background: transparent; border: none;
        color: {c.text_tertiary}; font-size: 14px; border-radius: 4px;
    }}
    QFrame#clipItem QPushButton:hover {{ background: {c.accent_error_dim}; color: {c.accent_error}; }}
    QLabel#clipCountBadge {{
        background: {c.accent_primary_dim}; color: {c.accent_primary};
        border-radius: 9px; font-size: 11px; font-weight: 600;
        padding: 0 8px; min-width: 18px;
    }}
    QLabel#clipEmptyState {{
        font-size: 11px; padding: 20px; color: {c.text_tertiary};
    }}

    /* ── Control bar buttons ── */
    QPushButton#ctrlMarkIn {{
        border: 1.5px solid {c.accent_success}; color: {c.accent_success};
        background: transparent; border-radius: 6px; padding: 0 8px;
        font-size: 12px; font-weight: 500;
    }}
    QPushButton#ctrlMarkIn:hover {{ background: {c.accent_success_dim}; color: {c.accent_success}; }}
    QPushButton#ctrlMarkIn:checked {{ background: {c.accent_success}; color: #fff; }}
    QPushButton#ctrlMarkOut {{
        border: 1.5px solid {c.accent_error}; color: {c.accent_error};
        background: transparent; border-radius: 6px; padding: 0 8px;
        font-size: 12px; font-weight: 500;
    }}
    QPushButton#ctrlMarkOut:hover {{ background: {c.accent_error_dim}; color: {c.accent_error}; }}
    QPushButton#ctrlMarkOut:checked {{ background: {c.accent_error}; color: #fff; }}
    QPushButton#ctrlExport {{
        border: 1.5px solid {c.accent_primary}; color: {c.accent_primary};
        background: {c.accent_primary_dim}; border-radius: 6px; padding: 0 8px;
        font-size: 12px; font-weight: 600;
    }}
    QPushButton#ctrlExport:hover {{ background: {c.accent_primary_dim}; color: {c.accent_primary}; }}
    QPushButton#ctrlExport:disabled {{ color: {disabled_text}; border-color: {disabled_border}; background: {disabled_bg}; }}
    QPushButton#ctrlPlay {{
        border: 1.5px solid {c.accent_primary}; color: {c.accent_primary};
        background: transparent; border-radius: 20px; font-size: 16px;
    }}
    QPushButton#ctrlPlay:hover {{ background: {c.accent_primary_dim}; color: {c.accent_primary}; }}
    QPushButton#ctrlPlay:disabled {{ color: {disabled_text}; border-color: {disabled_border}; background: {disabled_bg}; }}

    /* ── Export confirm dialog ── */
    QDialog#exportConfirmDialog {{
        background: {c.bg_secondary};
    }}
    QDialog#exportConfirmDialog QLabel {{ background: transparent; border: none; }}
    QDialog#exportConfirmDialog QPushButton {{
        font-size: 13px; padding: 0 20px; border-radius: 6px;
        background: {c.bg_tertiary}; color: {c.text_secondary};
        border: 1px solid {c.border_subtle};
    }}
    QDialog#exportConfirmDialog QPushButton:hover {{
        background: {c.bg_elevated}; color: {c.text_primary};
    }}
    QDialog#exportConfirmDialog QPushButton#btnPrimary {{
        background: {c.accent_primary}; color: white;
        border: none; font-weight: 500;
    }}
    QDialog#exportConfirmDialog QPushButton#btnPrimary:hover {{
        background: {c.accent_primary};
    }}
    QDialog#exportConfirmDialog QComboBox {{
        background: {c.bg_secondary}; border: 1px solid {c.border_default};
        border-radius: 6px; padding: 6px 10px; font-size: 12px;
        color: {c.text_primary}; min-height: 30px;
    }}
    QDialog#exportConfirmDialog QComboBox::drop-down {{ border: none; width: 20px; }}
    QDialog#exportConfirmDialog QComboBox QAbstractItemView {{
        background: {c.bg_elevated}; color: {c.text_primary};
        selection-background-color: {c.accent_primary_dim};
        selection-color: {c.accent_primary};
        border: 1px solid {c.border_default};
    }}
    QDialog#exportConfirmDialog QLineEdit {{
        background: {c.bg_secondary}; border: 1px solid {c.border_default};
        border-radius: 6px; padding: 6px 10px; font-size: 12px;
        color: {c.text_primary}; min-height: 30px;
    }}
    QDialog#exportConfirmDialog QLineEdit:disabled {{ color: {c.text_tertiary}; }}
    QDialog#exportConfirmDialog QWidget#encodingBox {{
        background: {c.bg_tertiary}; border-radius: 8px;
    }}

    /* ── Status bar dots ── */
    QLabel#statusBarDotSuccess {{ color: {c.accent_success}; font-size: 9px; background: transparent; }}
    QLabel#statusBarDotError {{ color: {c.accent_error}; font-size: 9px; background: transparent; }}
    QLabel#statusBarDotPrimary {{ color: {c.accent_primary}; font-size: 9px; background: transparent; }}

    /* ── Control bar sync indicator ── */
    QLabel#syncIndicator {{
        font-size: 11px; color: {c.accent_primary};
        background: {c.accent_primary_dim}; border-radius: 4px;
        padding: 2px 8px;
    }}
    """
