"""GUI page audit regression tests."""
from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication


def _qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def _settings() -> QSettings:
    settings = QSettings("LSC", "LiveStreamClipper")
    settings.clear()
    return settings


def test_dashboard_has_navigate_to_signal() -> None:
    _qapp()

    from lsc.gui.pages.dashboard import DashboardPage

    page = DashboardPage()
    assert hasattr(page, 'navigate_to')
    assert hasattr(page, 'history_item_clicked')


def test_settings_only_exposes_effective_recording_options() -> None:
    _qapp()
    _settings()

    from lsc.gui.pages.settings import SettingsPage

    page = SettingsPage()

    assert page._encoder._items == ["H.264 NVENC", "H.264 CPU", "Copy"]
    assert page._quality._items == ["原画", "高清", "流畅"]
    assert not hasattr(page, "_lang")
    assert not hasattr(page, "_min_duration")


def test_settings_page_reloads_saved_values() -> None:
    _qapp()
    settings = _settings()
    settings.setValue("theme", "浅色")
    settings.setValue("encoder", "Copy")
    settings.setValue("quality", "流畅")
    settings.setValue("param_mode", "CRF 质量")
    settings.setValue("crf", "28")
    settings.setValue("bitrate_value", "6")
    settings.setValue("bitrate_unit", "mbps")

    from lsc.gui.pages.settings import SettingsPage

    page = SettingsPage()

    assert page._theme.selected == "浅色"
    assert page._encoder.selected == "Copy"
    assert page._quality.selected == "流畅"
    assert page._param_mode.selected == "CRF 质量"
    assert page._crf.text() == "28"
    assert page._bitrate_value.text() == "6"
    assert page._bitrate_unit.text() == "mbps"


def test_settings_page_load_does_not_trigger_theme_refresh(monkeypatch) -> None:
    _qapp()
    settings = _settings()
    settings.setValue("theme", "浅色")

    from lsc.gui.pages import settings as settings_module

    calls = []
    monkeypatch.setattr(settings_module, "set_dark", lambda dark: calls.append(dark))

    page = settings_module.SettingsPage()

    assert page._theme.selected == "浅色"
    assert calls == []


def test_light_theme_uses_readable_disabled_and_empty_state_colors() -> None:
    from lsc.gui.theme import LIGHT, generate_stylesheet

    stylesheet = generate_stylesheet(LIGHT, dark=False)

    assert "QPushButton:disabled { color: #aeaeb2" in stylesheet
    assert "background-color: #e5e5ea" in stylesheet
    assert "QLabel#empty_state {\n        color: #aeaeb2;" in stylesheet


def test_light_theme_primary_buttons_use_solid_accent_style() -> None:
    from lsc.gui.theme import LIGHT, generate_stylesheet

    stylesheet = generate_stylesheet(LIGHT, dark=False)

    assert "QPushButton#btnPrimary {" in stylesheet
    assert "background-color: #007aff !important;" in stylesheet
    assert "color: #1d1d1f !important;" in stylesheet
    assert "QPushButton#btnPrimary:disabled { background-color: #e5e5ea" in stylesheet


def test_light_theme_add_room_button_has_visible_text() -> None:
    from lsc.gui.theme import LIGHT, generate_stylesheet

    stylesheet = generate_stylesheet(LIGHT, dark=False)

    # addRoomButton is now aliased with roomCardActionBtn in a combined rule
    assert "QPushButton#addRoomButton" in stylesheet
    assert "background-color: #1A007AFF !important;" in stylesheet
    assert "color: #007aff !important;" in stylesheet
    assert "QPushButton#addRoomButton:disabled" in stylesheet
    assert "background-color: #e5e5ea" in stylesheet


def test_light_theme_hover_states_stay_distinct_from_white_surfaces() -> None:
    from lsc.gui.theme import LIGHT, generate_stylesheet

    stylesheet = generate_stylesheet(LIGHT, dark=False)

    assert "QPushButton:hover {\n        background-color: #e5e5ea" in stylesheet
    assert "QPushButton#btnSecondary:hover {" in stylesheet
    assert "background-color: #e5e5ea !important;" in stylesheet
    assert "QPushButton#roomCardSmallBtn:hover" in stylesheet
    assert "border-color: rgba(0,0,0,0.15);" in stylesheet


def test_control_bar_play_button_has_readable_disabled_state() -> None:
    from lsc.gui.theme import DARK, LIGHT, generate_stylesheet

    light = generate_stylesheet(LIGHT, dark=False)
    dark = generate_stylesheet(DARK, dark=True)

    assert "QPushButton#ctrlPlay:disabled { color: #aeaeb2" in light
    assert "QPushButton#ctrlPlay:disabled {" in dark
    assert "border-color:" in light
    assert "background-color:" in light


def test_workbench_control_bar_uses_icons_not_fragile_glyph_text() -> None:
    _qapp()

    from lsc.gui.components.control_bar import ControlBar

    bar = ControlBar()

    assert bar._play.text() == ""
    assert not bar._play.icon().isNull()
    assert bar._back.text() == "5s"
    assert not bar._back.icon().isNull()
    assert bar._fwd.text() == "5s"
    assert not bar._fwd.icon().isNull()
    assert not bar._fullscreen.isVisible()


def test_record_timeline_cursor_uses_theme_text_color_in_light_mode() -> None:
    _qapp()

    from lsc.gui.theme import set_dark
    from lsc.gui.components.timeline import InlineTimeline

    set_dark(False)
    timeline = InlineTimeline()

    try:
        assert timeline.cursor_color_name() == "#1d1d1f"
    finally:
        set_dark(True)


def test_settings_page_persists_bitrate_defaults() -> None:
    _qapp()
    settings = _settings()

    from lsc.gui.pages.settings import SettingsPage

    page = SettingsPage()
    page._bitrate_value.set_text("5200")
    page._bitrate_unit.set_text("kbps")

    assert settings.value("bitrate_value") == "5200"
    assert settings.value("bitrate_unit") == "kbps"


def test_apply_saved_theme_uses_qsettings_value(monkeypatch) -> None:
    _qapp()
    settings = _settings()
    settings.setValue("theme", "浅色")

    from lsc.gui import main_window
    from lsc.gui.theme import is_dark, set_dark

    set_dark(True)
    monkeypatch.setattr(main_window.SettingsPage, "_is_system_dark", staticmethod(lambda: True))

    main_window._apply_saved_theme()

    assert not is_dark()


def test_dashboard_refresh_replaces_empty_state_with_sessions() -> None:
    _qapp()

    from lsc.gui.pages.dashboard import DashboardPage

    page = DashboardPage()
    page.set_sessions(
        [
            {
                "title": "主播A",
                "status": "recording",
                "duration_text": "00:12:30",
                "path": "D:/recordings/a.mp4",
            }
        ]
    )

    assert len(page._room_rows) == 1
