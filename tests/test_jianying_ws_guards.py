from __future__ import annotations

from pathlib import Path


def test_load_settings_default_includes_jianying_draft_dir():
    text = Path("python-backend/handlers/room_handler.py").read_text(encoding="utf-8")
    assert "'jianying_draft_dir'" in text or '"jianying_draft_dir"' in text
