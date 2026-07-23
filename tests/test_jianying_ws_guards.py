from __future__ import annotations

from pathlib import Path


def test_load_settings_default_includes_jianying_draft_dir():
    text = Path("python-backend/handlers/room_handler.py").read_text(encoding="utf-8")
    assert "'jianying_draft_dir'" in text or '"jianying_draft_dir"' in text


def test_jianying_handlers_module_exists():
    from handlers import jianying_handlers  # noqa: F401


def test_register_exports_expected_message_names():
    text = Path("python-backend/handlers/jianying_handlers.py").read_text(encoding="utf-8")
    assert "get_jianying_draft_dir" in text
