from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLIPLIST = ROOT / "lsc-electron/src/pages/Workbench/components/ClipList.tsx"
WORKBENCH = ROOT / "lsc-electron/src/pages/Workbench/index.tsx"


def test_cliplist_has_no_draft_export_target_segmented() -> None:
    text = CLIPLIST.read_text(encoding="utf-8")
    assert "value: 'draft'" not in text
    assert "label: '草稿'" not in text
    assert "onExportTargetChange" not in text


def test_workbench_nav_has_no_jianying_menu() -> None:
    text = WORKBENCH.read_text(encoding="utf-8")
    # D2 Modal 文案含「完成后生成剪映草稿」；禁止恢复导航/导出预览独立草稿入口
    stripped = text.replace("完成后生成剪映草稿", "")
    assert "生成剪映草稿" not in stripped
    assert "仅剪映草稿" not in text
    assert "导出并生成草稿" not in text


def test_cliplist_forbids_horizontal_scroll() -> None:
    text = CLIPLIST.read_text(encoding="utf-8")
    assert "overflowX: 'hidden'" in text or 'overflow-x: hidden' in text or 'overflowX: "hidden"' in text


def test_analysis_modal_has_draft_switch_copy() -> None:
    text = WORKBENCH.read_text(encoding="utf-8")
    assert "完成后生成剪映草稿" in text


def test_auto_draft_sends_include_pending_and_no_fallback() -> None:
    text = WORKBENCH.read_text(encoding="utf-8")
    assert "include_pending" in text
    assert "allow_single_fallback" in text or "allowSingleFallback" in text


def test_continuous_auto_draft_does_not_preset_autofired() -> None:
    """持续分析 effect 不得在调用 runAnalysisDraftIfNeeded 前预置 autoFired。"""
    text = WORKBENCH.read_text(encoding="utf-8")
    marker = "持续分析终态"
    idx = text.find(marker)
    assert idx >= 0, "missing continuous draft effect marker"
    window = text[idx : idx + 2000]
    assert "runAnalysisDraftIfNeeded('auto')" in window or 'runAnalysisDraftIfNeeded("auto")' in window
    # 在调用前不得出现 s.autoFired = true
    call_at = window.find("runAnalysisDraftIfNeeded")
    before = window[:call_at]
    assert "autoFired = true" not in before
    assert "autoFired=true" not in before.replace(" ", "")


def test_auto_draft_switch_persists_across_restarts() -> None:
    """「完成后生成剪映草稿」开关必须持久化，重启后不得静默复位为关。"""
    text = WORKBENCH.read_text(encoding="utf-8")
    assert "localStorage.getItem('lsc.wantAnalysisDraft')" in text
    assert "localStorage.setItem('lsc.wantAnalysisDraft'" in text


def test_auto_draft_rearms_session_at_terminal_state() -> None:
    """终态 effect 在开关开启但会话未武装时（应用重启后）就地补武装。"""
    text = WORKBENCH.read_text(encoding="utf-8")
    marker = "持续分析终态"
    idx = text.find(marker)
    assert idx >= 0
    window = text[idx : idx + 1600]
    assert "armDraftSession(status.room_id, targets)" in window
    assert "s.sawRunning = true" in window


def test_auto_draft_falls_back_to_target_room_clips() -> None:
    """补武装后 clipKeys 为空：按目标房回退收集切片，避免「无切片跳过草稿」。"""
    text = WORKBENCH.read_text(encoding="utf-8")
    assert "s.targetRoomIds.includes(c.room_id)" in text
