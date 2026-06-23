"""Main window module — startup theme application and window assembly."""
from __future__ import annotations

from PySide6.QtCore import QSettings

from lsc.gui.pages.settings import SettingsPage
from lsc.gui.theme import set_dark

_ORG = "LSC"
_APP = "LiveStreamClipper"


def _apply_saved_theme() -> None:
    """Apply the theme stored in QSettings at startup.

    Reads the "theme" key (深色 / 浅色 / 跟随系统) and applies it via
    theme.set_dark(). Called once during application startup before the
    main window is shown.
    """
    settings = QSettings(_ORG, _APP)
    theme = settings.value("theme", "深色")
    if theme == "深色":
        set_dark(True)
    elif theme == "浅色":
        set_dark(False)
    else:  # 跟随系统
        set_dark(SettingsPage._is_system_dark())


__all__ = ["SettingsPage", "_apply_saved_theme"]
