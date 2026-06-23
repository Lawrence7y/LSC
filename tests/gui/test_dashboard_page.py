"""Dashboard page UI structure and signal tests."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QFrame, QLabel

from lsc.gui.components.widgets import Card
from lsc.gui.pages.dashboard import DashboardPage


def _qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture(autouse=True)
def _ensure_qapp():
    _qapp()


def test_dashboard_record_button_emits_signal() -> None:
    page = DashboardPage()
    hits = []
    page.record_requested.connect(lambda: hits.append(True))

    assert page._record_btn.isEnabled()
    page._record_btn.click()

    assert hits == [True]


def test_dashboard_set_sessions_updates_count_and_hides_empty_state() -> None:
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
    assert not page._empty.isVisible()


def test_dashboard_stat_cards_use_card_container_and_left_accent() -> None:
    page = DashboardPage()

    for card in page._stat_cards:
        assert isinstance(card, Card)
        assert card.objectName() == "dashboardStatCard"
        assert not hasattr(card, "_top_accent")
        accent_bar = card.findChild(QFrame, "dashboardStatAccentBar")
        assert accent_bar is not None
        assert accent_bar.width() == 4


def test_dashboard_action_cards_use_card_container() -> None:
    page = DashboardPage()
    buttons = [page._record_btn, page._clips_btn, page._multi_room_btn, page._settings_btn]
    expected_names = [
        "dashboardActionCardPrimary",
        "dashboardActionCard",
        "dashboardActionCard",
        "dashboardActionCard",
    ]

    for btn, expected_name in zip(buttons, expected_names):
        assert isinstance(btn, Card)
        assert btn.objectName() == expected_name


def test_dashboard_primary_action_uses_same_card_surface_as_other_actions() -> None:
    from lsc.gui.theme import generate_stylesheet, get_theme

    stylesheet = generate_stylesheet(get_theme())

    primary_block_start = stylesheet.index("QFrame#dashboardActionCardPrimary {")
    primary_block = stylesheet[primary_block_start:stylesheet.index("}", primary_block_start)]

    assert "background: #e6722f" not in primary_block
    assert "border: 1px solid #e6722f" not in primary_block


def test_dashboard_recent_activity_uses_card_container() -> None:
    page = DashboardPage()
    # The recent activity container is a Card added directly to the page layout.
    recent_card = page._session_container.parentWidget()
    assert isinstance(recent_card, Card)
    assert recent_card.objectName() == "card"


def test_dashboard_session_cards_render_status_badge() -> None:
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

    session_card = page._session_layout.itemAt(0).widget()
    assert session_card.objectName() == "dashboardSessionCard"
    status_label = session_card.findChild(QLabel, "dashboardSessionStatus")
    assert status_label is not None
    assert status_label.text() == "录制中"
