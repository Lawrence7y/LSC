"""Tests for record page interactions (shortcuts, timeline)."""
from __future__ import annotations

import os
import sys
import types

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication


def _qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def _install_record_page_stubs(monkeypatch) -> None:
    helpers = types.ModuleType("lsc.utils.helpers")
    helpers.fmt_time = lambda sec: f"{int(sec):02d}"
    monkeypatch.setitem(sys.modules, "lsc.utils.helpers", helpers)

    mpv_widget = types.ModuleType("lsc.gui.components.mpv_widget")

    class _DummyMpvWidget:
        def __init__(self, *args, **kwargs):
            self._playing = False

        def setStyleSheet(self, _style):
            pass

        def hide(self):
            pass

        def show(self):
            pass

        def play_video(self, *_args, **_kwargs):
            self._playing = True

        def play_live(self, *_args, **_kwargs):
            self._playing = True

        def stop_video(self):
            self._playing = False

        def is_playing(self):
            return self._playing

        def toggle_play_pause(self):
            pass

        def seek_to(self, _sec):
            pass

        def position_sec(self):
            return 0.0

        def duration_sec(self):
            return 0.0

        def cleanup(self):
            pass

        def isVisible(self):
            return False

    mpv_widget.MpvWidget = _DummyMpvWidget
    monkeypatch.setitem(sys.modules, "lsc.gui.components.mpv_widget", mpv_widget)

    widgets = types.ModuleType("lsc.gui.components.widgets")

    class _DummyWidget:
        def __init__(self, *args, **kwargs):
            pass

    widgets.Card = _DummyWidget
    widgets.ChipGroup = _DummyWidget
    widgets.EmptyState = _DummyWidget
    widgets.FadeInWidget = _DummyWidget
    widgets.InputField = _DummyWidget
    widgets.ParamPanel = _DummyWidget
    monkeypatch.setitem(sys.modules, "lsc.gui.components.widgets", widgets)

    theme = types.ModuleType("lsc.gui.theme")
    theme.get_option_button_palette = lambda *args, **kwargs: {}
    theme.get_theme = lambda: types.SimpleNamespace(
        border_subtle="#000000",
        text_tertiary="#666666",
        text_primary="#ffffff",
        text_secondary="#cccccc",
        bg_tertiary="#000000",
        border_default="#333333",
        accent_primary="#00ff00",
        accent_primary_dim="#003300",
        bg_elevated="#111111",
        accent_success="#00ff00",
        accent_error="#ff0000",
    )
    theme.is_dark = lambda: True
    monkeypatch.setitem(sys.modules, "lsc.gui.theme", theme)


def test_record_page_space_i_o_shortcuts_trigger_expected_actions(monkeypatch) -> None:
    _qapp()
    _install_record_page_stubs(monkeypatch)

    from lsc.gui.pages.record import RecordPage

    page = RecordPage.__new__(RecordPage)
    hits: list[str] = []
    monkeypatch.setattr(page, "_on_play_pause", lambda: hits.append("play"))
    monkeypatch.setattr(page, "_on_mark_in", lambda: hits.append("in"))
    monkeypatch.setattr(page, "_on_mark_out", lambda: hits.append("out"))

    page.keyPressEvent(QKeyEvent(QKeyEvent.KeyPress, Qt.Key_Space, Qt.NoModifier))
    page.keyPressEvent(QKeyEvent(QKeyEvent.KeyPress, Qt.Key_I, Qt.NoModifier))
    page.keyPressEvent(QKeyEvent(QKeyEvent.KeyPress, Qt.Key_O, Qt.NoModifier))

    assert hits == ["play", "in", "out"]


def test_record_page_start_recording_passes_controller_input_args(monkeypatch, tmp_path) -> None:
    _qapp()
    _install_record_page_stubs(monkeypatch)

    from lsc.gui.pages.record import RecordPage

    page = RecordPage.__new__(RecordPage)
    page._live_cursor_sec = None
    page._preview = types.SimpleNamespace(
        stop_video=lambda: None,
        set_state=lambda **kwargs: None,
    )
    page._controls = types.SimpleNamespace(
        set_recording=lambda _value: None,
        set_range_state=lambda _has_in, _has_out: None,
        set_export_enabled=lambda _value: None,
        set_playing=lambda _value: None,
        timeline=types.SimpleNamespace(set_cursor_mode=lambda _mode: None),
    )
    page._config = types.SimpleNamespace(
        output_path=str(tmp_path),
        encoder_selection="H.264 CPU",
        crf_value=23,
        param_mode_selection="CRF 质量",
        bitrate_value="",
        bitrate_unit="kbps",
    )
    page.status_changed = types.SimpleNamespace(emit=lambda *_args: None)
    page._ctrl = types.SimpleNamespace(
        stream_url="https://example.com/live.m3u8",
        input_args=["-headers", "Referer: https://example.com/\r\n"],
        capture=types.SimpleNamespace(),
        output_dir="",
    )

    calls: list[dict] = []

    def fake_start_recording_with_crf(stream_url, output_dir, encoder, crf, **kwargs):
        calls.append(
            {
                "stream_url": stream_url,
                "output_dir": output_dir,
                "encoder": encoder,
                "crf": crf,
                "kwargs": kwargs,
            }
        )
        return False, "", encoder

    page._ctrl.start_recording_with_crf = fake_start_recording_with_crf

    assert page._start_recording() is False
    assert calls[-1]["kwargs"]["input_args"] == ["-headers", "Referer: https://example.com/\r\n"]
