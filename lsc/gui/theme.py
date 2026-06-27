"""LSC Theme System — Design Tokens from ui-language-unification-design.md"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

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
    accent_primary_hover: str
    accent_primary_pressed: str
    accent_secondary: str
    accent_secondary_dim: str
    accent_success: str
    accent_success_dim: str
    accent_success_pressed: str
    accent_warning: str
    accent_warning_dim: str
    accent_error: str
    accent_error_dim: str
    accent_error_pressed: str


DARK = ThemeColors(
    bg_primary="#000000",
    bg_secondary="#1c1c1e",
    bg_tertiary="#2c2c2e",
    bg_elevated="#3a3a3c",
    text_primary="#f5f5f7",
    text_secondary="#aeaeb2",
    text_tertiary="#8e8e93",
    border_subtle="#1CFFFFFF",
    border_default="#33FFFFFF",
    border_strong="#4DFFFFFF",
    accent_primary="#2e8dff",
    accent_primary_dim="#262E8DFF",
    accent_primary_glow="#402E8DFF",
    accent_primary_hover="#66abff",
    accent_primary_pressed="#0064d6",
    accent_secondary="#5e5ce6",
    accent_secondary_dim="#1F5E5CE6",
    accent_success="#30d158",
    accent_success_dim="#1F30D158",
    accent_success_pressed="#28b84c",
    accent_warning="#ff9f0a",
    accent_warning_dim="#1FFF9F0A",
    accent_error="#ff453a",
    accent_error_dim="#1FFF453A",
    accent_error_pressed="#e0362c",
)

LIGHT = ThemeColors(
    bg_primary="#f2f2f7",
    bg_secondary="#ffffff",
    bg_tertiary="#e5e5ea",
    bg_elevated="#ffffff",
    text_primary="#1d1d1f",
    text_secondary="#3c3c43",
    text_tertiary="#8e8e93",
    border_subtle="#0F000000",
    border_default="#1A000000",
    border_strong="#33000000",
    accent_primary="#007aff",
    accent_primary_dim="#1A007AFF",
    accent_primary_glow="#33007AFF",
    accent_primary_hover="#2e8dff",
    accent_primary_pressed="#0064d6",
    accent_secondary="#5856d6",
    accent_secondary_dim="#1A5856D6",
    accent_success="#34c759",
    accent_success_dim="#1A34C759",
    accent_success_pressed="#2ab04a",
    accent_warning="#ff9500",
    accent_warning_dim="#1AFF9500",
    accent_error="#ff3b30",
    accent_error_dim="#1AFF3B30",
    accent_error_pressed="#d63028",
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
        from PySide6.QtWidgets import QApplication, QPushButton
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
                    if isinstance(w, QPushButton):
                        w.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
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
                            if isinstance(w, QPushButton):
                                w.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
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
    light_hover_bg = "#e5e5ea"
    light_hover_border = "rgba(0,0,0,0.15)"
    bg_btn_hover = c.bg_elevated if dark else light_hover_bg
    text_btn_idle = c.text_secondary if dark else c.text_primary
    text_btn_hover = c.text_primary
    border_btn = c.border_default if dark else c.border_strong
    hover_border = c.border_strong if dark else light_hover_border
    disabled_text = c.text_tertiary if dark else "#aeaeb2"
    disabled_bg = c.bg_secondary if dark else "#e5e5ea"
    disabled_border = c.border_subtle if dark else c.border_strong
    # 浅色模式下主按钮 hover/pressed 使用深色文字保证对比度
    primary_btn_text = "#ffffff" if dark else "#1d1d1f"
    # 浅色模式下 Tooltip 需要确保对比度
    tooltip_bg = "#1d1d1f" if dark else "#ffffff"
    tooltip_text = "#ffffff" if dark else "#1d1d1f"
    tooltip_border = "rgba(255,255,255,0.15)" if dark else "rgba(0,0,0,0.15)"

    return f"""
    * {{
        font-family: "SF Pro Display", "SF Pro Text", "Helvetica Neue", "PingFang SC", "Noto Sans SC", "Source Han Sans SC", system-ui, -apple-system, sans-serif;
        outline: none;
    }}
    QMainWindow {{ background-color: {c.bg_primary}; }}
    QWidget#mainCentralWidget {{ background-color: {c.bg_primary}; }}
    QWidget {{ background-color: transparent; color: {c.text_primary}; }}

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
        border-radius: 10px; padding: 7px 12px; color: {c.text_primary};
        font-size: 13px; min-height: 34px; max-height: 34px;
    }}
    QLineEdit:focus {{ border-color: {c.accent_primary}; background: {c.bg_secondary if dark else c.bg_elevated}; }}
    QLineEdit::placeholder {{ color: {c.text_tertiary}; }}
    QLineEdit:disabled {{ color: {disabled_text}; background: {disabled_bg}; }}

    QLabel {{ color: {c.text_primary}; background: transparent; }}
    QLabel#label_primary {{ font-size: 13px; font-weight: 500; color: {c.text_primary}; }}
    QLabel#label_secondary {{ font-size: 13px; font-weight: 400; color: {c.text_secondary}; }}
    QLabel#label_tertiary {{ font-size: 12px; font-weight: 400; color: {c.text_tertiary}; }}
    QLabel#label_accent {{ color: {c.accent_primary}; }}
    QLabel#label_mono {{
        font-size: 12px; color: {c.text_tertiary};
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
    QWidget#pageHeader {{
        background: {c.bg_primary};
        border-bottom: 1px solid {c.border_subtle};
    }}
    QLabel#pageHeaderTitle {{
        font-size: 20px; font-weight: 700; color: {c.text_primary};
        background: transparent;
    }}
    QLabel#pageHeaderSubtitle {{
        font-size: 12px; color: {c.text_secondary};
        background: transparent;
    }}
    QLabel#sidebarTitle {{
        font-size: 16px; font-weight: 700; color: {c.text_primary}; background: transparent;
    }}
    QLabel#sidebarSubtitle {{
        font-size: 11px; color: {c.text_tertiary}; background: transparent;
    }}
    QWidget#sidebarBrandHeader {{
        background: transparent;
    }}
    QLabel#sidebarLogo {{
        background: {c.accent_primary_dim};
        border-radius: 10px;
        padding: 4px;
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
        font-size: 10px; font-weight: 600; color: {c.text_secondary}; background: {c.bg_tertiary};
        padding: 2px 6px; border-radius: 4px;
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

    /* ── Standard Button (default QPushButton) ── */
    QPushButton {{
        background-color: {bg_btn_idle}; color: {text_btn_idle};
        border: 1px solid {border_btn}; border-radius: 10px;
        font-size: 13px; font-weight: 500;
        min-height: 34px; max-height: 34px;
        padding: 0 16px;
    }}
    QPushButton:hover {{
        background-color: {bg_btn_hover}; color: {text_btn_hover};
        border-color: {hover_border};
    }}
    QPushButton:pressed {{ background-color: {c.bg_tertiary}; border-color: {c.border_default}; }}
    QPushButton:disabled {{ color: {disabled_text}; border-color: {disabled_border}; background-color: {disabled_bg}; }}
    QPushButton:checked {{
        background-color: {c.accent_primary_dim}; color: {c.accent_primary};
        border-color: {c.accent_primary}; font-weight: 500;
    }}
    QPushButton:focus {{
        border-color: {c.accent_primary};
    }}

    QPushButton#btnPrimary {{
        background-color: {c.accent_primary} !important; color: {primary_btn_text} !important;
        border: none; border-radius: 10px; font-weight: 600;
        min-height: 34px; max-height: 34px; padding: 0 20px;
        font-size: 13px;
    }}
    QPushButton#btnPrimary:hover {{
        background-color: {c.accent_primary_hover} !important; color: {primary_btn_text} !important;
        border-color: {c.accent_primary_hover};
    }}
    QPushButton#btnPrimary:pressed {{
        background-color: {c.accent_primary_pressed} !important; color: {primary_btn_text} !important;
        border-color: {c.accent_primary_pressed};
    }}
    QPushButton#btnPrimary:disabled {{ background-color: {disabled_bg} !important; color: {disabled_text} !important; border: 1px solid {disabled_border}; }}

    QPushButton#btnSecondary {{
        background-color: {c.bg_tertiary if not dark else 'transparent'} !important; color: {c.text_secondary} !important;
        border: 1px solid {c.border_default if dark else c.border_strong};
        border-radius: 10px;
        font-size: 13px; font-weight: 500;
        min-height: 34px; max-height: 34px;
        padding: 0 16px;
    }}
    QPushButton#btnSecondary:hover {{
        background-color: {c.bg_elevated if dark else light_hover_bg} !important; color: {c.text_primary} !important;
        border-color: {hover_border};
    }}
    QPushButton#btnSecondary:pressed {{
        background-color: {c.bg_tertiary} !important; color: {c.text_primary} !important;
        border-color: {c.border_strong};
    }}
    QPushButton#btnSecondary:disabled {{ color: {disabled_text} !important; border-color: {disabled_border}; background-color: {disabled_bg} !important; }}

    QPushButton#btnSmall {{
        background-color: {bg_btn_idle} !important; color: {text_btn_idle} !important;
        border: 1px solid {border_btn}; border-radius: 8px;
        font-size: 12px; font-weight: 500;
        min-height: 26px; max-height: 26px;
        padding: 0 10px;
    }}
    QPushButton#btnSmall:hover {{
        background-color: {bg_btn_hover} !important; color: {text_btn_hover} !important;
        border-color: {hover_border};
    }}
    QPushButton#btnSmall:pressed {{ background-color: {c.bg_tertiary} !important; border-color: {c.border_default}; }}
    QPushButton#btnSmall:disabled {{ color: {disabled_text} !important; border-color: {disabled_border}; background-color: {disabled_bg} !important; }}

    QPushButton#navButton {{
        background-color: transparent !important;
        color: {c.text_secondary} !important;
        border: 1px solid transparent;
        border-left: 3px solid transparent;
        border-radius: 10px;
        font-size: 13px;
        font-weight: 500;
        text-align: left;
        padding-left: 28px;
        min-height: 38px;
        max-height: 38px;
    }}
    QPushButton#navButton:hover {{
        background-color: {c.bg_elevated if dark else c.bg_tertiary} !important;
        color: {c.text_primary} !important;
        border-color: {c.border_default};
    }}
    QPushButton#navButton[active="true"] {{
        background-color: {c.accent_primary_dim} !important;
        color: {c.accent_primary} !important;
        border-color: {c.accent_primary};
        border-left: 3px solid {c.accent_primary};
    }}
    QPushButton#navButton[active="true"]:hover {{
        background-color: {c.accent_primary_dim} !important;
        color: {c.accent_primary} !important;
        border-color: {c.accent_primary};
        border-left: 3px solid {c.accent_primary};
    }}

    QPushButton#themeButton {{
        background-color: transparent !important;
        color: {c.text_secondary} !important;
        border: 1px solid {c.accent_primary};
        border-radius: 10px;
        font-size: 13px;
        font-weight: 500;
        text-align: left;
        padding-left: 28px;
        min-height: 38px;
        max-height: 38px;
    }}
    QPushButton#themeButton:hover {{
        background-color: {c.accent_primary_dim} !important;
        color: {c.text_primary} !important;
    }}

    /* ── Accent buttons (standard size) ── */
    QPushButton#btnAccent {{
        background-color: {c.accent_primary_dim} !important; color: {c.accent_primary} !important;
        border: 1px solid {c.accent_primary}; border-radius: 10px;
        font-size: 13px; font-weight: 500;
        min-height: 34px; max-height: 34px; padding: 0 16px;
    }}
    QPushButton#btnAccent:hover {{
        background-color: {c.accent_primary} !important; color: {primary_btn_text} !important;
        border-color: {c.accent_primary};
    }}
    QPushButton#btnAccent:pressed {{
        background-color: {c.accent_primary_pressed} !important; color: {primary_btn_text} !important;
        border-color: {c.accent_primary_pressed};
    }}
    QPushButton#btnAccent:disabled {{ color: {disabled_text} !important; border-color: {disabled_border}; background-color: {disabled_bg} !important; }}

    QPushButton#btnSuccess {{
        background-color: {c.accent_success_dim} !important; color: {c.accent_success} !important;
        border: 1px solid {c.accent_success}; border-radius: 10px;
        font-size: 13px; font-weight: 500;
        min-height: 34px; max-height: 34px; padding: 0 16px;
    }}
    QPushButton#btnSuccess:hover {{
        background-color: {c.accent_success} !important; color: {primary_btn_text} !important;
        border-color: {c.accent_success};
    }}
    QPushButton#btnSuccess:pressed {{
        background-color: {c.accent_success_pressed} !important; color: {primary_btn_text} !important;
        border-color: {c.accent_success_pressed};
    }}
    QPushButton#btnSuccess:checked {{
        background-color: {c.accent_success} !important; color: {primary_btn_text} !important; border-color: {c.accent_success};
    }}
    QPushButton#btnSuccess:disabled {{ color: {disabled_text} !important; border-color: {disabled_border}; background-color: {disabled_bg} !important; }}

    QPushButton#btnDanger {{
        background-color: {c.accent_error_dim} !important; color: {c.accent_error} !important;
        border: 1px solid {c.accent_error}; border-radius: 10px;
        font-size: 13px; font-weight: 500;
        min-height: 34px; max-height: 34px; padding: 0 16px;
    }}
    QPushButton#btnDanger:hover {{
        background-color: {c.accent_error} !important; color: {primary_btn_text} !important;
        border-color: {c.accent_error};
    }}
    QPushButton#btnDanger:pressed {{
        background-color: {c.accent_error_pressed} !important; color: {primary_btn_text} !important;
        border-color: {c.accent_error_pressed};
    }}
    QPushButton#btnDanger:checked {{
        background-color: {c.accent_error} !important; color: {primary_btn_text} !important; border-color: {c.accent_error};
    }}
    QPushButton#btnDanger:disabled {{ color: {disabled_text} !important; border-color: {disabled_border}; background-color: {disabled_bg} !important; }}

    QPushButton#btnWarning {{
        background-color: {c.accent_warning_dim} !important; color: {c.accent_warning} !important;
        border: 1px solid {c.accent_warning}; border-radius: 10px;
        font-size: 13px; font-weight: 500;
        min-height: 34px; max-height: 34px; padding: 0 16px;
    }}
    QPushButton#btnWarning:hover {{
        background-color: {c.accent_warning} !important; color: {primary_btn_text} !important;
        border-color: {c.accent_warning};
    }}
    QPushButton#btnWarning:pressed {{
        background-color: {c.accent_warning if dark else '#cc8400'} !important; color: {primary_btn_text} !important;
        border-color: {c.accent_warning if dark else '#cc8400'};
    }}
    QPushButton#btnWarning:disabled {{ color: {disabled_text} !important; border-color: {disabled_border}; background-color: {disabled_bg} !important; }}

    /* ── Accent buttons (small size) ── */
    QPushButton#btnAccentSmall {{
        background-color: {c.accent_primary_dim} !important; color: {c.accent_primary} !important;
        border: 1px solid {c.accent_primary}; border-radius: 8px;
        font-size: 12px; font-weight: 500;
        min-height: 26px; max-height: 26px; padding: 0 10px;
    }}
    QPushButton#btnAccentSmall:hover {{
        background-color: {c.accent_primary} !important; color: {primary_btn_text} !important;
        border-color: {c.accent_primary};
    }}
    QPushButton#btnAccentSmall:pressed {{
        background-color: {c.accent_primary_pressed} !important; color: {primary_btn_text} !important;
        border-color: {c.accent_primary_pressed};
    }}
    QPushButton#btnAccentSmall:disabled {{ color: {disabled_text} !important; border-color: {disabled_border}; background-color: {disabled_bg} !important; }}

    QPushButton#btnSuccessSmall {{
        background-color: {c.accent_success_dim} !important; color: {c.accent_success} !important;
        border: 1px solid {c.accent_success}; border-radius: 8px;
        font-size: 12px; font-weight: 500;
        min-height: 26px; max-height: 26px; padding: 0 10px;
    }}
    QPushButton#btnSuccessSmall:hover {{
        background-color: {c.accent_success} !important; color: {primary_btn_text} !important;
        border-color: {c.accent_success};
    }}
    QPushButton#btnSuccessSmall:pressed {{
        background-color: {c.accent_success_pressed} !important; color: {primary_btn_text} !important;
        border-color: {c.accent_success_pressed};
    }}
    QPushButton#btnSuccessSmall:disabled {{ color: {disabled_text} !important; border-color: {disabled_border}; background-color: {disabled_bg} !important; }}

    QPushButton#btnDangerSmall {{
        background-color: {c.accent_error_dim} !important; color: {c.accent_error} !important;
        border: 1px solid {c.accent_error}; border-radius: 8px;
        font-size: 12px; font-weight: 500;
        min-height: 26px; max-height: 26px; padding: 0 10px;
    }}
    QPushButton#btnDangerSmall:hover {{
        background-color: {c.accent_error} !important; color: {primary_btn_text} !important;
        border-color: {c.accent_error};
    }}
    QPushButton#btnDangerSmall:pressed {{
        background-color: {c.accent_error_pressed} !important; color: {primary_btn_text} !important;
        border-color: {c.accent_error_pressed};
    }}
    QPushButton#btnDangerSmall:disabled {{ color: {disabled_text} !important; border-color: {disabled_border}; background-color: {disabled_bg} !important; }}

    QPushButton#btnWarningSmall {{
        background-color: {c.accent_warning_dim} !important; color: {c.accent_warning} !important;
        border: 1px solid {c.accent_warning}; border-radius: 8px;
        font-size: 12px; font-weight: 500;
        min-height: 26px; max-height: 26px; padding: 0 10px;
    }}
    QPushButton#btnWarningSmall:hover {{
        background-color: {c.accent_warning} !important; color: {primary_btn_text} !important;
        border-color: {c.accent_warning};
    }}
    QPushButton#btnWarningSmall:pressed {{
        background-color: {c.accent_warning if dark else '#cc8400'} !important; color: {primary_btn_text} !important;
        border-color: {c.accent_warning if dark else '#cc8400'};
    }}
    QPushButton#btnWarningSmall:disabled {{ color: {disabled_text} !important; border-color: {disabled_border}; background-color: {disabled_bg} !important; }}

    QPushButton#btnStopRecording {{
        background-color: {c.accent_error} !important; color: {primary_btn_text} !important;
        border: none; border-radius: 10px; font-weight: 600;
        min-height: 34px; max-height: 34px; padding: 0 20px;
        font-size: 13px;
    }}
    QPushButton#btnStopRecording:hover {{
        background-color: {c.accent_error_pressed if dark else c.accent_error} !important; color: {primary_btn_text} !important;
        border-color: {c.accent_error_pressed if dark else c.accent_error};
    }}
    QPushButton#btnStopRecording:pressed {{
        background-color: {c.accent_error_pressed} !important; color: {primary_btn_text} !important;
        border-color: {c.accent_error_pressed};
    }}
    QPushButton#btnStopRecording:disabled {{ background-color: {disabled_bg} !important; color: {disabled_text} !important; border: 1px solid {disabled_border}; }}

    /* ── Legacy ID aliases (map old names to unified styles) ── */
    QPushButton#addRoomButton, QPushButton#roomCardActionBtn {{
        background-color: {c.accent_primary_dim} !important; color: {c.accent_primary} !important;
        border: 1px solid {c.accent_primary}; border-radius: 10px;
        font-size: 13px; font-weight: 500;
        min-height: 34px; max-height: 34px; padding: 0 16px;
    }}
    QPushButton#addRoomButton:hover, QPushButton#roomCardActionBtn:hover {{
        background-color: {c.accent_primary} !important; color: {primary_btn_text} !important;
        border-color: {c.accent_primary};
    }}
    QPushButton#addRoomButton:pressed, QPushButton#roomCardActionBtn:pressed {{
        background-color: {c.accent_primary_pressed} !important; color: {primary_btn_text} !important;
        border-color: {c.accent_primary_pressed};
    }}
    QPushButton#addRoomButton:disabled, QPushButton#roomCardActionBtn:disabled {{
        color: {disabled_text} !important; border-color: {disabled_border}; background-color: {disabled_bg} !important;
    }}

    QPushButton#ctrlExport {{
        background-color: {c.accent_primary} !important; color: {primary_btn_text} !important;
        border: none; border-radius: 8px;
        font-size: 12px; font-weight: 600;
        min-height: 26px; max-height: 26px; padding: 0 10px;
    }}
    QPushButton#ctrlExport:hover {{
        background-color: {c.accent_primary_hover} !important; color: {primary_btn_text} !important;
        border-color: {c.accent_primary_hover};
    }}
    QPushButton#ctrlExport:pressed {{
        background-color: {c.accent_primary_pressed} !important; color: {primary_btn_text} !important;
        border-color: {c.accent_primary_pressed};
    }}
    QPushButton#ctrlExport:disabled {{
        color: {disabled_text} !important; border-color: {disabled_border}; background-color: {disabled_bg} !important;
    }}

    QPushButton#ctrlSecondary, QPushButton#roomCardSmallBtn {{
        background-color: {bg_btn_idle} !important; color: {text_btn_idle} !important;
        border: 1px solid {border_btn}; border-radius: 8px;
        font-size: 12px; font-weight: 500;
        min-height: 26px; max-height: 26px; padding: 0 10px;
    }}
    QPushButton#ctrlSecondary:hover, QPushButton#roomCardSmallBtn:hover {{
        background-color: {bg_btn_hover} !important; color: {text_btn_hover} !important;
        border-color: {hover_border};
    }}
    QPushButton#ctrlSecondary:pressed, QPushButton#roomCardSmallBtn:pressed {{
        background-color: {c.bg_tertiary} !important; border-color: {c.border_default};
    }}
    QPushButton#ctrlSecondary:disabled, QPushButton#roomCardSmallBtn:disabled {{
        color: {disabled_text} !important; border-color: {disabled_border}; background-color: {disabled_bg} !important;
    }}

    QPushButton#roomCardActionBtnDanger {{
        background-color: {c.accent_error_dim} !important; color: {c.accent_error} !important;
        border: 1px solid {c.accent_error}; border-radius: 8px;
        font-size: 12px; font-weight: 500;
        min-height: 26px; max-height: 26px; padding: 0 10px;
    }}
    QPushButton#roomCardActionBtnDanger:hover {{
        background-color: {c.accent_error} !important; color: {primary_btn_text} !important; border-color: {c.accent_error};
    }}
    QPushButton#roomCardActionBtnDanger:pressed {{
        background-color: {c.accent_error_pressed} !important; color: {primary_btn_text} !important; border-color: {c.accent_error_pressed};
    }}

    QPushButton#roomCardActionBtnWarning {{
        background-color: {c.accent_warning_dim} !important; color: {c.accent_warning} !important;
        border: 1px solid {c.accent_warning}; border-radius: 8px;
        font-size: 12px; font-weight: 500;
        min-height: 26px; max-height: 26px; padding: 0 10px;
    }}
    QPushButton#roomCardActionBtnWarning:hover {{
        background-color: {c.accent_warning} !important; color: {primary_btn_text} !important; border-color: {c.accent_warning};
    }}
    QPushButton#roomCardActionBtnWarning:pressed {{
        background-color: {c.accent_warning if dark else '#cc8400'} !important; color: {primary_btn_text} !important; border-color: {c.accent_warning if dark else '#cc8400'};
    }}

    QPushButton#roomCardRemoveBtn {{
        background-color: transparent !important; color: {c.text_tertiary} !important;
        border: none; border-radius: 4px; font-size: 16px; font-weight: 600;
        min-width: 22px; max-width: 22px; min-height: 22px; max-height: 22px;
    }}
    QPushButton#roomCardRemoveBtn:hover {{
        background-color: {c.accent_error_dim} !important; color: {c.accent_error} !important;
    }}

    /* 导航项状态徽标 */
    QLabel#navBadge {{
        background: {c.accent_primary};
        color: #ffffff;
        border-radius: 4px;
        font-size: 10px;
        font-weight: 600;
        padding: 2px 6px;
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
        border-radius: 14px;
    }}
    QFrame#roomCard:hover {{
        border-color: {c.border_default if dark else c.border_strong};
        background: {c.bg_secondary};
    }}
    QFrame#roomCardSelected {{
        background: {c.bg_secondary}; border: 2px solid {c.accent_primary};
        border-radius: 14px;
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
        background-color: {bg_btn_idle} !important; color: {text_btn_idle} !important;
        border: 1px solid {border_btn}; border-radius: 6px;
        font-size: 12px; font-weight: 500;
        min-height: 28px; max-height: 28px; padding: 0 10px;
    }}
    QWidget#roomCardPreviewControls QPushButton#roomCardSmallBtn:hover {{
        background-color: {bg_btn_hover} !important; color: {text_btn_hover} !important;
        border-color: {hover_border};
    }}
    QWidget#roomCardPreviewControls QCheckBox#roomCardCheckBox {{
        color: {c.text_secondary}; background: transparent; font-size: 11px; spacing: 4px;
    }}
    QWidget#roomCardPreviewControls QLabel#roomCardTimeLabel {{
        color: {c.text_secondary}; background: transparent;
        font-family: 'JetBrains Mono', 'Consolas', monospace;
        font-size: 11px; padding: 0 4px;
    }}
    QWidget#roomCardPreviewControls QCheckBox#roomCardCheckBox::indicator {{
        width: 14px; height: 14px;
        border: 1px solid {c.border_default}; border-radius: 4px;
        background: {c.bg_tertiary};
    }}
    QWidget#roomCardPreviewControls QCheckBox#roomCardCheckBox::indicator:checked {{
        background: {c.accent_primary}; border-color: {c.accent_primary};
    }}
    QWidget#roomCardOverlayControls {{
        background: {c.bg_tertiary};
        border-radius: 10px;
        border: 1px solid {c.border_subtle};
    }}
    QWidget#roomCardOverlayControls QPushButton#roomCardOverlayBtn {{
        background: {c.bg_secondary};
        color: {c.text_primary};
        border: 1px solid {c.border_default};
        border-radius: 4px;
        font-size: 11px; font-weight: 500;
        min-height: 24px; max-height: 24px;
        padding: 0 6px;
    }}
    QWidget#roomCardOverlayControls QPushButton#roomCardOverlayBtn:hover {{
        background: {c.accent_primary_dim};
        border-color: {c.accent_primary};
        color: {c.accent_primary};
    }}
    QWidget#roomCardOverlayControls QPushButton#roomCardOverlayBtn:disabled {{
        color: {c.text_tertiary};
    }}
    QWidget#roomCardOverlayControls QLabel#roomCardOverlayTimeLabel {{
        color: {c.text_secondary};
        background: transparent;
        font-family: 'JetBrains Mono', 'Consolas', monospace;
        font-size: 11px; padding: 0 4px;
    }}
    QWidget#roomCardOverlayControls QCheckBox#roomCardOverlayCheckBox {{
        color: {c.text_secondary};
        background: transparent;
        font-size: 11px; spacing: 4px;
    }}
    QWidget#roomCardOverlayControls QCheckBox#roomCardOverlayCheckBox::indicator {{
        width: 14px; height: 14px;
        border: 1px solid {c.border_default}; border-radius: 4px;
        background: {c.bg_secondary};
    }}
    QWidget#roomCardOverlayControls QCheckBox#roomCardOverlayCheckBox::indicator:checked {{
        background: {c.accent_primary}; border-color: {c.accent_primary};
    }}
    QPushButton#previewFullscreenButton {{
        background-color: rgba(0,0,0,0.45) !important; color: #ffffff !important;
        border: 1px solid rgba(255,255,255,0.18); border-radius: 10px;
    }}
    QPushButton#previewFullscreenButton:hover {{
        background-color: rgba(0,0,0,0.68) !important; border-color: rgba(255,255,255,0.35);
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
    QLabel#roomCardBadgeRec {{
        font-size: 10px; font-weight: 600; color: #ffffff;
        background: {c.accent_error}; border-radius: 4px; padding: 2px 6px;
    }}
    QLabel#roomCardBadgeMute {{
        font-size: 10px; font-weight: 600; color: #ffffff;
        background: {c.text_tertiary}; border-radius: 4px; padding: 2px 6px;
    }}

    QCheckBox#roomCardCheckBox {{
        font-size: 11px; color: {c.text_secondary}; background: transparent;
        spacing: 4px;
    }}
    QCheckBox#roomCardCheckBox::indicator {{
        width: 14px; height: 14px;
        border: 1px solid {c.border_default}; border-radius: 4px;
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
        background: rgba(0, 0, 0, 180);
        border: none;
        border-top: 1px solid rgba(255, 255, 255, 30);
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
        border-radius: 4px;
        background: rgba(0,0,0,0.22);
    }}
    QWidget#fullscreenPlayerControls QCheckBox#fullscreenMuteButton::indicator:checked {{
        background: {c.accent_primary}; border-color: {c.accent_primary};
    }}
    QSlider#fullscreenProgressSlider::groove:horizontal {{
        background: rgba(255,255,255,0.25);
        height: 3px;
        border-radius: 2px;
    }}
    QSlider#fullscreenProgressSlider::sub-page:horizontal {{
        background: {c.accent_primary};
        border-radius: 2px;
    }}
    QSlider#fullscreenProgressSlider::handle:horizontal {{
        background: #ffffff;
        border: 2px solid {c.accent_primary};
        width: 12px;
        height: 12px;
        margin: -5px 0;
        border-radius: 6px;
    }}
    QSlider#fullscreenProgressSlider::handle:horizontal:hover {{
        background: {c.accent_primary};
        border: 2px solid #ffffff;
        width: 14px;
        height: 14px;
        margin: -6px 0;
        border-radius: 7px;
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

    /* 回直播浮窗按钮 */
    QPushButton#returnLiveButton {{
        background: rgba(46, 141, 255, 200);
        color: #ffffff;
        border: none;
        border-radius: 4px;
        padding: 4px 10px;
        font-size: 12px;
        font-weight: bold;
    }}
    QPushButton#returnLiveButton:hover {{
        background: rgba(46, 141, 255, 230);
    }}
    QPushButton#returnLiveButton:pressed {{
        background: rgba(46, 141, 255, 255);
    }}

    QFrame#card {{
        background: {c.bg_secondary}; border: 1px solid {c.border_subtle if dark else c.border_default};
        border-radius: 14px;
    }}

    /* ── Dashboard ── */
    QFrame#dashboardRoomStatusRow {{
        background: transparent; border: none; border-radius: 8px;
    }}
    QFrame#dashboardRoomStatusRow:hover {{
        background: {c.bg_tertiary};
    }}
    QLabel#dashboardRoomName {{ font-size: 13px; font-weight: 600; color: {c.text_primary}; }}
    QLabel#dashboardPlatformBadge {{
        font-size: 10px; font-weight: 500; color: {c.text_secondary};
        background: {c.bg_tertiary}; border-radius: 4px; padding: 1px 6px;
    }}
    QLabel#dashboardStatusBadge {{
        font-size: 10px; font-weight: 600; color: #ffffff;
        border-radius: 4px; padding: 1px 6px;
    }}
    QLabel#dashboardStatusBadge[status="accent_success"] {{ background: {c.accent_success}; }}
    QLabel#dashboardStatusBadge[status="accent_primary"] {{ background: {c.accent_primary}; }}
    QLabel#dashboardStatusBadge[status="accent_warning"] {{ background: {c.accent_warning}; }}
    QLabel#dashboardStatusBadge[status="accent_error"] {{ background: {c.accent_error}; }}
    QLabel#dashboardStatusBadge[status="text_tertiary"] {{ background: {c.text_tertiary}; }}

    QFrame#dashboardHistoryRow {{
        background: transparent; border: none; border-radius: 8px;
    }}
    QFrame#dashboardHistoryRow:hover {{
        background: {c.bg_tertiary};
    }}
    QLabel#dashboardHistoryTitle {{ font-size: 13px; font-weight: 600; color: {c.text_primary}; }}

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
        min-height: 30px; max-height: 30px;
        padding: 0 14px;
        font-size: 12px;
        color: {c.text_secondary};
        background: {c.bg_tertiary};
        border: 1px solid {c.border_default};
        border-radius: 15px;
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
        border-radius: 8px;
        text-align: center;
        font-size: 10px;
        color: {c.text_secondary};
    }}
    QProgressBar::chunk {{
        background: {c.accent_primary};
        border-radius: 7px;
    }}

    QMenu {{
        background: {c.bg_elevated}; color: {c.text_primary};
        border: 1px solid {c.border_default}; border-radius: 10px;
        padding: 6px;
    }}
    QMenu::item {{
        background: transparent; border-radius: 8px;
        padding: 8px 24px;
    }}
    QMenu::item:selected {{
        background: {c.accent_primary_dim}; color: {c.accent_primary};
    }}
    QMenu::separator {{
        background: {c.border_subtle}; height: 1px; margin: 4px 8px;
    }}

    QToolTip {{
        background: {tooltip_bg}; color: {tooltip_text};
        border: 1px solid {tooltip_border}; border-radius: 8px;
        padding: 4px 8px; font-size: 12px;
    }}

    QComboBox {{
        background: {c.bg_tertiary}; border: 1px solid {c.border_default};
        border-radius: 10px; padding: 7px 12px; color: {c.text_primary};
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
        border: 1px solid {c.border_default}; border-radius: 10px;
        padding: 4px;
    }}

    QSpinBox, QDoubleSpinBox {{
        background: {c.bg_tertiary}; border: 1px solid {c.border_default};
        border-radius: 10px; padding: 7px 12px; color: {c.text_primary};
        font-size: 13px; min-height: 34px;
    }}
    QSpinBox:focus, QDoubleSpinBox:focus {{ border-color: {c.accent_primary}; }}
    QSpinBox::up-button, QDoubleSpinBox::up-button,
    QSpinBox::down-button, QDoubleSpinBox::down-button {{ border: none; width: 20px; }}

    QSlider::groove:horizontal {{
        background: {c.border_default}; height: 4px; border-radius: 2px;
    }}
    QSlider::handle:horizontal {{
        background: {c.accent_primary}; width: 14px; height: 14px;
        margin: -5px 0; border-radius: 6px;
    }}
    QSlider::handle:horizontal:hover {{
        background: {c.accent_primary}; width: 16px; height: 16px; margin: -6px 0;
    }}
    QSlider::sub-page:horizontal {{
        background: {c.accent_primary}; border-radius: 2px;
    }}
    QSlider:disabled {{ opacity: 0.4; }}

    QCheckBox {{ color: {c.text_secondary}; font-size: 12px; spacing: 6px; }}
    QCheckBox::indicator {{ width: 18px; height: 18px; border-radius: 5px; border: 1px solid {c.border_default}; background: {c.bg_tertiary}; }}
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
        background-color: {c.accent_primary} !important; color: {primary_btn_text} !important;
        border: none; font-weight: 500;
    }}
    QWidget#clipList QPushButton#btnPrimary:hover {{ background-color: {c.accent_primary} !important; }}
    QWidget#clipList QPushButton#btnPrimary:disabled {{
        background-color: {c.bg_tertiary} !important; color: {c.text_tertiary} !important;
    }}
    QWidget#clipList QPushButton:disabled {{ color: {c.text_tertiary}; }}
    QWidget#clipList QScrollArea {{ background: transparent; border: none; }}
    QFrame#clipItem {{
        background: {c.bg_tertiary};
        border: 1px solid {c.border_subtle};
        border-radius: 10px;
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
    QWidget#clipThumbContainer {{
        border-radius: 4px;
        overflow: hidden;
    }}
    QLabel#clipThumbPlaceholder {{
        background: {c.bg_secondary};
        border: 1px solid {c.border_subtle};
        border-radius: 4px;
        font-size: 16px;
    }}
    QLabel#clipPlayOverlay {{
        background: rgba(0, 0, 0, 120);
        border-radius: 4px;
        color: white;
        font-size: 18px;
        font-weight: bold;
    }}
    QLabel#clipCountBadge {{
        background: {c.accent_primary_dim}; color: {c.accent_primary};
        border-radius: 4px; font-size: 10px; font-weight: 600;
        padding: 2px 6px; min-width: 16px;
    }}
    QLabel#clipEmptyState {{
        font-size: 11px; padding: 20px; color: {c.text_tertiary};
    }}

    /* ── Control bar buttons ── */
    QPushButton#ctrlMarkIn {{
        border: 1.5px solid {c.accent_success}; color: {c.accent_success} !important;
        background-color: transparent !important; border-radius: 6px; padding: 0 8px;
        font-size: 12px; font-weight: 500;
        min-height: 28px; max-height: 28px;
    }}
    QPushButton#ctrlMarkIn:hover {{ background-color: {c.accent_success} !important; color: {primary_btn_text} !important; }}
    QPushButton#ctrlMarkIn:checked {{ background-color: {c.accent_success} !important; color: {primary_btn_text} !important; }}
    QPushButton#ctrlMarkOut {{
        border: 1.5px solid {c.accent_error}; color: {c.accent_error} !important;
        background-color: transparent !important; border-radius: 6px; padding: 0 8px;
        font-size: 12px; font-weight: 500;
        min-height: 28px; max-height: 28px;
    }}
    QPushButton#ctrlMarkOut:hover {{ background-color: {c.accent_error} !important; color: {primary_btn_text} !important; }}
    QPushButton#ctrlMarkOut:checked {{ background-color: {c.accent_error} !important; color: {primary_btn_text} !important; }}
    QPushButton#ctrlPlay {{
        border: 1.5px solid {c.accent_primary}; color: {c.accent_primary} !important;
        background-color: transparent !important; border-radius: 18px; font-size: 16px;
        min-height: 36px; max-height: 36px; min-width: 36px; max-width: 36px;
    }}
    QPushButton#ctrlPlay:hover {{ background-color: {c.accent_primary} !important; color: {primary_btn_text} !important; }}
    QPushButton#ctrlPlay:disabled {{ color: {disabled_text} !important; border-color: {disabled_border}; background-color: {disabled_bg} !important; }}

    /* ── Export confirm dialog ── */
    QDialog#exportConfirmDialog {{
        background: {c.bg_secondary};
    }}
    QDialog#exportConfirmDialog QLabel {{ background: transparent; border: none; }}
    QDialog#exportConfirmDialog QPushButton {{
        font-size: 13px; padding: 0 20px; border-radius: 10px;
        background: {c.bg_tertiary}; color: {c.text_secondary};
        border: 1px solid {c.border_subtle};
    }}
    QDialog#exportConfirmDialog QPushButton:hover {{
        background: {c.bg_elevated}; color: {c.text_primary};
    }}
    QDialog#exportConfirmDialog QPushButton#btnPrimary {{
        background-color: {c.accent_primary} !important; color: {primary_btn_text} !important;
        border: none; font-weight: 500;
    }}
    QDialog#exportConfirmDialog QPushButton#btnPrimary:hover {{
        background-color: {c.accent_primary} !important;
    }}
    QDialog#exportConfirmDialog QComboBox {{
        background: {c.bg_secondary}; border: 1px solid {c.border_default};
        border-radius: 10px; padding: 6px 10px; font-size: 12px;
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
        border-radius: 10px; padding: 6px 10px; font-size: 12px;
        color: {c.text_primary}; min-height: 30px;
    }}
    QDialog#exportConfirmDialog QLineEdit:disabled {{ color: {c.text_tertiary}; }}
    QDialog#exportConfirmDialog QWidget#encodingBox {{
        background: {c.bg_tertiary}; border-radius: 10px;
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
