"""Tests for record page module re-exports."""
from __future__ import annotations


def test_record_module_reexports_record_page_symbols() -> None:
    from lsc.gui.pages.record import ConfigPanel, ControlBar, InlineTimeline, RecordPage, VideoPreview

    assert RecordPage.__name__ == "RecordPage"
    assert ConfigPanel.__name__ == "ConfigPanel"
    assert ControlBar.__name__ == "ControlBar"
    assert InlineTimeline.__name__ == "InlineTimeline"
    assert VideoPreview.__name__ == "VideoPreview"
