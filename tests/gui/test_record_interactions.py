"""Tests for record page interactions (shortcuts, timeline)."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication


def _qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_record_page_space_i_o_shortcuts_trigger_expected_actions(monkeypatch) -> None:
    _qapp()

    from lsc.gui.pages.record import RecordPage

    page = RecordPage()
    hits: list[str] = []
    monkeypatch.setattr(page, "_on_play_pause", lambda: hits.append("play"))
    monkeypatch.setattr(page, "_on_mark_in", lambda: hits.append("in"))
    monkeypatch.setattr(page, "_on_mark_out", lambda: hits.append("out"))

    page.keyPressEvent(QKeyEvent(QKeyEvent.KeyPress, Qt.Key_Space, Qt.NoModifier))
    page.keyPressEvent(QKeyEvent(QKeyEvent.KeyPress, Qt.Key_I, Qt.NoModifier))
    page.keyPressEvent(QKeyEvent(QKeyEvent.KeyPress, Qt.Key_O, Qt.NoModifier))

    assert hits == ["play", "in", "out"]
    page.cleanup()
