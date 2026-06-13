"""Tests for record page module re-exports."""
from __future__ import annotations

import importlib
import sys
import types


def _load_record_page_with_stubbed_record():
    record_stub = types.ModuleType("lsc.gui.pages.record")

    class RecordPage:
        pass

    record_stub.RecordPage = RecordPage
    sys.modules["lsc.gui.pages.record"] = record_stub
    sys.modules.pop("lsc.gui.pages.record_page", None)
    return importlib.import_module("lsc.gui.pages.record_page"), RecordPage


def test_record_page_module_reexports_record_page_symbol() -> None:
    module, record_page_type = _load_record_page_with_stubbed_record()

    assert module.RecordPage is record_page_type


def test_record_page_module_reexports_multi_room_page() -> None:
    module, _record_page_type = _load_record_page_with_stubbed_record()

    assert module.MultiRoomPage.__name__ == "MultiRoomPage"
