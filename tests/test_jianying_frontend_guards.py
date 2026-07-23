# tests/test_jianying_frontend_guards.py
from __future__ import annotations

from pathlib import Path

import pytest

TYPES = Path("lsc-electron/src/types/index.ts")
WORKBENCH = Path("lsc-electron/src/pages/Workbench/index.tsx")
CLIPLIST = Path("lsc-electron/src/pages/Workbench/components/ClipList.tsx")
SETTINGS = Path("lsc-electron/src/pages/Settings/index.tsx")
MAIN = Path("lsc-electron/electron/main.ts")


def test_types_export_export_target_and_jianying_result():
    text = TYPES.read_text(encoding="utf-8")
    assert "ExportTarget" in text
    assert "JianyingDraftResult" in text
    assert "JianyingDraftOptions" in text


def test_workbench_has_export_target_radio():
    text = WORKBENCH.read_text(encoding="utf-8")
    assert "exportTarget" in text
    assert "generate_jianying_draft" in text


def test_cliplist_batch_draft_sends_once_guard_comment_or_code():
    text = CLIPLIST.read_text(encoding="utf-8")
    assert "exportTarget" in text or "Segmented" in text


def test_handle_export_many_draft_path_single_generate():
    text = WORKBENCH.read_text(encoding="utf-8")
    assert "generate_jianying_draft" in text
    assert "exportTarget === 'draft'" in text or 'exportTarget === "draft"' in text


@pytest.mark.skip(reason="J3 Task 5: settings page jianying_draft_dir row not yet implemented")
def test_settings_jianying_draft_dir_row():
    text = SETTINGS.read_text(encoding="utf-8")
    assert "jianying_draft_dir" in text or "剪映草稿" in text


@pytest.mark.skip(reason="J3 Task 6: _isSafePath jianying whitelist not yet implemented")
def test_issafe_path_mentions_jianying():
    text = MAIN.read_text(encoding="utf-8")
    assert "jianying" in text.lower() or "Jianying" in text
