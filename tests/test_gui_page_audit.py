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


def test_record_config_uses_saved_defaults(tmp_path: Path) -> None:
    _qapp()
    settings = _settings()
    settings.setValue("output_dir", str(tmp_path))
    settings.setValue("encoder", "H.264 CPU")
    settings.setValue("quality", "高清")
    settings.setValue("param_mode", "码率限制")
    settings.setValue("crf", "21")
    settings.setValue("bitrate_value", "4500")
    settings.setValue("bitrate_unit", "kbps")

    from lsc.gui.pages.record import ConfigPanel

    panel = ConfigPanel()

    assert panel.output_path == str(tmp_path)
    assert panel.encoder_selection == "H.264 CPU"
    assert panel.quality_selection == "高清"
    assert panel.param_mode_selection == "码率限制"
    assert panel.crf_value == 21
    assert panel.bitrate_value == "4500"
    assert panel.bitrate_unit == "kbps"
    assert not panel._analyze_btn.isEnabled()
    assert not panel._export_analysis_btn.isEnabled()


def test_dashboard_has_real_record_entry_signal() -> None:
    _qapp()

    from lsc.gui.pages.dashboard import DashboardPage

    page = DashboardPage()
    hits = []
    page.record_requested.connect(lambda: hits.append(True))

    assert page._record_btn.isEnabled()
    page._record_btn.click()

    assert hits == [True]


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

    assert "QPushButton:disabled { color: #6f7685;" in stylesheet
    assert "background: #e3e7ee;" in stylesheet
    assert "QLabel#empty_state {\n        color: #6f7685;" in stylesheet


def test_light_theme_primary_buttons_use_segmented_pill_style() -> None:
    from lsc.gui.theme import LIGHT, generate_stylesheet

    stylesheet = generate_stylesheet(LIGHT, dark=False)

    assert "QPushButton#btnPrimary {" in stylesheet
    assert "background: rgba(230,114,47,0.12);" in stylesheet
    assert "color: #e6722f;" in stylesheet
    assert "border: 1px solid #e6722f;" in stylesheet
    assert "border-radius: 18px;" in stylesheet
    assert "QPushButton#btnPrimary:disabled { background: #e3e7ee; color: #6f7685;" in stylesheet


def test_light_theme_add_room_button_has_visible_text() -> None:
    from lsc.gui.theme import LIGHT, generate_stylesheet

    stylesheet = generate_stylesheet(LIGHT, dark=False)

    assert "QPushButton#addRoomButton {" in stylesheet
    assert "QPushButton#addRoomButton {\n        background: rgba(230,114,47,0.12); color: #e6722f;" in stylesheet
    assert "QPushButton#addRoomButton:disabled { background: #e3e7ee; color: #6f7685;" in stylesheet


def test_light_theme_hover_states_stay_distinct_from_white_surfaces() -> None:
    from lsc.gui.theme import LIGHT, generate_stylesheet

    stylesheet = generate_stylesheet(LIGHT, dark=False)

    assert "QPushButton:hover {\n        background: #dfe5ee;" in stylesheet
    assert "QPushButton#btnSecondary:hover {\n        background: #dfe5ee;" in stylesheet
    assert "QPushButton#roomCardSmallBtn:hover {\n        background: #dfe5ee;" in stylesheet
    assert "border-color: rgba(0,0,0,0.22);" in stylesheet


def test_control_bar_play_button_has_readable_disabled_state() -> None:
    from lsc.gui.theme import DARK, LIGHT, generate_stylesheet

    light = generate_stylesheet(LIGHT, dark=False)
    dark = generate_stylesheet(DARK, dark=True)

    assert "QPushButton#ctrlPlay:disabled { color: #6f7685;" in light
    assert "QPushButton#ctrlPlay:disabled {" in dark
    assert "border-color:" in light
    assert "background:" in light


def test_workbench_control_bar_uses_icons_not_fragile_glyph_text() -> None:
    _qapp()

    from lsc.gui.components.control_bar import ControlBar

    bar = ControlBar()

    assert bar._play.text() == ""
    assert not bar._play.icon().isNull()
    assert bar._back.text() == "10s"
    assert not bar._back.icon().isNull()
    assert bar._fwd.text() == "10s"
    assert not bar._fwd.icon().isNull()
    assert not bar._fullscreen.isVisible()


def test_record_timeline_cursor_uses_theme_text_color_in_light_mode() -> None:
    _qapp()

    from lsc.gui.theme import set_dark
    from lsc.gui.components.timeline import InlineTimeline

    set_dark(False)
    timeline = InlineTimeline()

    try:
        assert timeline.cursor_color_name() == "#1a1d26"
    finally:
        set_dark(True)


def test_record_video_preview_initializes_player_lazily(monkeypatch) -> None:
    _qapp()

    from lsc.gui.pages import record as record_module

    def fail_if_constructed(*args, **kwargs):
        raise AssertionError("MpvWidget should be lazy")

    monkeypatch.setattr(record_module, "MpvWidget", fail_if_constructed)

    preview = record_module.VideoPreview()

    assert preview._mpv_widget is None
    assert not preview._fullscreen_btn.isHidden()


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


def test_record_page_emits_dashboard_stats() -> None:
    _qapp()

    from lsc.gui.pages.record import RecordPage

    page = RecordPage()
    hits = []
    page.stats_changed.connect(lambda recording, duration, clips: hits.append((recording, duration, clips)))
    page._ctrl.is_recording = True
    page._ctrl.total_sec = 42
    page._ctrl.exported = [("a",), ("b",)]

    page._emit_stats()

    assert hits[-1] == (1, 42, 2)
    page.cleanup()


def test_set_video_path_loads_player_and_timeline(tmp_path: Path) -> None:
    _qapp()

    from lsc.gui.pages.record import RecordPage

    video = tmp_path / "sample.mp4"
    video.write_bytes(b"fake")

    class FakeController:
        video_path = ""
        total_sec = 0

        class Timer:
            def __init__(self):
                self.started = []

            def start(self, interval):
                self.started.append(interval)

        timer = Timer()

        def probe_video_duration(self):
            return 12.5

    class FakePreview:
        def __init__(self):
            self.played = None

        def play_video(self, path):
            self.played = path

    class FakeTimeline:
        def __init__(self):
            self.data = None

        def set_data(self, **kwargs):
            self.data = kwargs

    class FakeControls:
        def __init__(self):
            self.timeline = FakeTimeline()
            self.playing = []
            self.times = []

        def set_playing(self, value):
            self.playing.append(value)

        def set_time(self, position, duration):
            self.times.append((position, duration))

    page = RecordPage.__new__(RecordPage)
    page._ctrl = FakeController()
    page._preview = FakePreview()
    page._controls = FakeControls()

    RecordPage.set_video_path(page, str(video))

    assert page._ctrl.video_path == str(video)
    assert page._ctrl.total_sec == 12.5
    assert page._preview.played == str(video)
    assert page._controls.playing == [True]
    assert page._controls.timeline.data == {"duration": 12.5, "position": 0}
    assert page._controls.times == [(0, 12.5)]
    assert page._ctrl.timer.started == [1000]


def test_set_video_path_enables_analyze_button(tmp_path: Path) -> None:
    _qapp()

    from lsc.gui.pages.record import RecordPage

    video = tmp_path / "sample.mp4"
    video.write_bytes(b"fake")

    class FakeController:
        video_path = ""
        total_sec = 0

        class Timer:
            def start(self, _interval):
                pass

        timer = Timer()

        def probe_video_duration(self):
            return 12.5

    class FakePreview:
        def play_video(self, _path):
            pass

    class FakeTimeline:
        def set_data(self, **_kwargs):
            pass

    class FakeControls:
        def __init__(self):
            self.timeline = FakeTimeline()

        def set_playing(self, _value):
            pass

        def set_time(self, _position, _duration):
            pass

    class FakeConfig:
        def __init__(self):
            self.analyze_enabled = []

        def set_analyze_enabled(self, enabled):
            self.analyze_enabled.append(enabled)

    page = RecordPage.__new__(RecordPage)
    page._ctrl = FakeController()
    page._preview = FakePreview()
    page._controls = FakeControls()
    page._config = FakeConfig()

    RecordPage.set_video_path(page, str(video))

    assert page._config.analyze_enabled == [True]


def test_exported_clip_click_replays_exported_file(tmp_path: Path) -> None:
    _qapp()

    from lsc.gui.pages.record import RecordPage

    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"fake")

    class FakeController:
        exported = [("clip_001.mp4", 3.0, 8.0, "2.0 MB", str(clip))]
        video_path = ""
        total_sec = 0

    class FakePreview:
        def __init__(self):
            self.played = None

        def play_video(self, path):
            self.played = path

    class FakeControls:
        def __init__(self):
            self.playing = []

        def set_playing(self, value):
            self.playing.append(value)

        def set_range_state(self, *args, **kwargs):
            pass

        def set_export_enabled(self, *args, **kwargs):
            pass

        class _Timeline:
            def set_data(self, **kwargs):
                pass
            def set_cursor_mode(self, *args, **kwargs):
                pass

        timeline = _Timeline()

    page = RecordPage.__new__(RecordPage)
    page._ctrl = FakeController()
    page._preview = FakePreview()
    page._controls = FakeControls()

    RecordPage._on_clip_clicked(page, 0)

    assert page._ctrl.video_path == str(clip)
    assert page._preview.played == str(clip)
    assert page._controls.playing == [True]


def test_record_page_controls_keep_timeline_and_buttons_visible() -> None:
    _qapp()

    from lsc.gui.components.timeline import TIMELINE_HEIGHT
    from lsc.gui.pages.record import RecordPage

    page = RecordPage()

    assert page._controls.timeline.height() == TIMELINE_HEIGHT
    assert not page._controls._mark_in.isHidden()
    assert not page._controls._mark_out.isHidden()
    assert page._controls._time_label.width() >= 172
    page.cleanup()


def test_record_config_sidebar_does_not_overflow_horizontally() -> None:
    app = _qapp()

    from PySide6.QtWidgets import QScrollArea

    from lsc.gui.pages.record import RecordPage

    page = RecordPage()
    page.resize(1360, 860)
    page.show()
    app.processEvents()

    right_scroll = next(
        scroll
        for scroll in page.findChildren(QScrollArea)
        if scroll.widget() is not None
        and scroll.widget().findChild(type(page._config)) is page._config
    )

    assert right_scroll.horizontalScrollBar().maximum() == 0
    page.cleanup()


def test_record_page_preview_resizes_with_window_height() -> None:
    app = _qapp()

    from lsc.gui.pages.record import RecordPage

    page = RecordPage()
    page.resize(900, 600)
    page.show()
    app.processEvents()

    initial_preview_size = page._preview.size()

    page.resize(1400, 900)
    app.processEvents()

    grown_preview_size = page._preview.size()

    assert grown_preview_size.width() > initial_preview_size.width()
    assert grown_preview_size.height() >= initial_preview_size.height() + 120
    page.cleanup()


def test_record_page_updates_stream_info_after_recording_start(monkeypatch) -> None:
    _qapp()

    from lsc.gui.pages.record import RecordPage

    page = RecordPage()
    page._ctrl.probe_stream_metadata = lambda _source: ("1920x1080", "60 fps")
    page._config.set_info("res", "探测中...")
    page._config.set_info("fps", "探测中...")

    page._refresh_stream_info("https://example.com/live.m3u8")

    assert page._config._info_values["res"].text() == "1920x1080"
    assert page._config._info_values["fps"].text() == "60 fps"
    page.cleanup()


def test_config_panel_exposes_analysis_profile_selector() -> None:
    _qapp()

    from lsc.gui.pages.record import ConfigPanel

    panel = ConfigPanel()

    assert panel.analysis_profile == "valorant"
    assert panel._analysis_profile._items == ["valorant", "fps", "generic"]


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

    assert page._session_count == 1
