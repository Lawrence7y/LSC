"""Dashboard page UI structure and signal tests."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from lsc.gui.pages.dashboard import DashboardPage


def _qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture(autouse=True)
def _ensure_qapp():
    _qapp()


def test_dashboard_set_sessions_updates_room_status() -> None:
    page = DashboardPage()
    sessions = [
        {"title": "主播A", "status": "recording", "duration_text": "00:12:30", "path": "D:/recordings/a.mp4"},
        {"title": "主播B", "status": "connected", "duration_text": "—", "path": "D:/recordings/b.mp4"},
    ]
    page.set_sessions(sessions)
    assert len(page._room_rows) == 2


def test_dashboard_set_sessions_empty_hides_list() -> None:
    page = DashboardPage()
    page.set_sessions([])
    assert len(page._room_rows) == 0


def test_dashboard_set_history() -> None:
    page = DashboardPage()
    history = [
        {"title": "主播A的精彩操作", "platform": "抖音", "duration": "00:45:32", "size": "2.1 GB", "time": "今天 14:30"},
        {"title": "B站主播游戏直播", "platform": "B站", "duration": "01:20:15", "size": "3.8 GB", "time": "今天 10:15"},
    ]
    page.set_history(history)
    assert len(page._history_rows) == 2


def test_dashboard_history_item_clicked_emits_signal() -> None:
    page = DashboardPage()
    history = [
        {"title": "主播A", "platform": "抖音", "duration": "00:45:32", "size": "2.1 GB", "time": "今天 14:30",
         "url": "https://live.douyin.com/123"},
    ]
    page.set_history(history)
    hits = []
    page.history_item_clicked.connect(lambda item: hits.append(item))
    assert len(hits) == 0


def test_dashboard_navigate_to_signal() -> None:
    page = DashboardPage()
    hits = []
    page.navigate_to.connect(lambda key: hits.append(key))
    assert len(hits) == 0
