# tests/test_jianying_frontend_guards.py
"""Jianying draft frontend guards (post analysis-auto-draft UX).

Draft entry is no longer ClipList Segmented / exportTarget / nav menu.
Settings dir + generate_jianying_draft API + safe path remain required.
"""
from __future__ import annotations

from pathlib import Path

TYPES = Path("lsc-electron/src/types/index.ts")
WORKBENCH = Path("lsc-electron/src/pages/Workbench/index.tsx")
CLIPLIST = Path("lsc-electron/src/pages/Workbench/components/ClipList.tsx")
SETTINGS = Path("lsc-electron/src/pages/Settings/index.tsx")
MAIN = Path("lsc-electron/electron/main.ts")


def test_types_export_jianying_result():
    text = TYPES.read_text(encoding="utf-8")
    assert "JianyingDraftResult" in text
    assert "JianyingDraftOptions" in text


def test_workbench_keeps_generate_jianying_draft_hook():
    text = WORKBENCH.read_text(encoding="utf-8")
    assert "generate_jianying_draft" in text
    assert "requestJianyingDraft" in text
    # D1: no UI draft target radio / segmented; D2 Modal uses「完成后生成剪映草稿」
    stripped = text.replace("完成后生成剪映草稿", "")
    assert "仅剪映草稿" not in text
    assert "生成剪映草稿" not in stripped


def test_cliplist_has_no_draft_segmented():
    text = CLIPLIST.read_text(encoding="utf-8")
    assert "onExportTargetChange" not in text
    assert "value: 'draft'" not in text
    assert "label: '草稿'" not in text


def test_settings_jianying_draft_dir_row():
    text = SETTINGS.read_text(encoding="utf-8")
    assert "jianying_draft_dir" in text or "剪映草稿" in text


def test_issafe_path_mentions_jianying():
    text = MAIN.read_text(encoding="utf-8")
    assert "jianying" in text.lower() or "Jianying" in text
