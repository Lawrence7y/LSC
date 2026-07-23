from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _workbench_source() -> str:
    return (ROOT / "lsc-electron/src/pages/Workbench/index.tsx").read_text(encoding="utf-8")


def _room_actions_source() -> str:
    return (ROOT / "lsc-electron/src/hooks/useRoomActions.ts").read_text(encoding="utf-8")


def _handle_refresh_short_click_body(source: str) -> str:
    return source.split("const handleRefreshShortClick = useCallback", 1)[1].split(
        "const handleRefreshLongPress", 1
    )[0]


def _handle_disconnect_body(source: str) -> str:
    # handleDisconnect 已迁移至 useRoomActions.ts
    return source.split("const handleDisconnect = useCallback", 1)[1].split("}, [send])", 1)[0]


def _timeline_invalidated_body(source: str) -> str:
    return source.split("on('timeline_invalidated'", 1)[1].split("return () => {", 1)[0]


def _timeline_ready_body(source: str) -> str:
    return source.split("on('timeline_ready'", 1)[1].split("const unsubInvalid", 1)[0]


def _handle_remove_body(source: str) -> str:
    # handleRemove 已迁移至 useRoomActions.ts
    return source.split("const handleRemove = useCallback", 1)[1].split(
        "}, [send, setExpandedRoomId, setSelectedRoomIds, pendingRoomSavesRef])", 1
    )[0]


def _apply_select_clip_body(source: str) -> str:
    return source.split("const applySelectClip = (clip: ClipSegment", 1)[1].split(
        "const hasRefineMarksChanged", 1
    )[0]


def _ensure_not_aligning_body(source: str) -> str:
    return source.split("const ensureNotAligning = useCallback", 1)[1].split(
        "const handleControlMarkIn = useCallback", 1
    )[0]


def _handle_confirm_clip_body(source: str) -> str:
    return source.split("const handleConfirmClip = (", 1)[1].split(
        "const handleConfirmAndExport", 1
    )[0]


def _handle_batch_stop_body(source: str) -> str:
    return source.split("const handleBatchStop = useCallback", 1)[1].split(
        "}, [send])", 1
    )[0]


def _clip_confirm_status_body(source: str) -> str:
    return source.split("on('clip_confirm_status'", 1)[1].split("on('clip_export_started'", 1)[0]


def test_handle_remove_stops_continuous_analysis_when_involved() -> None:
    """Task 1: 删房时若房间在持续分析中须先 stop_continuous_analysis。"""
    body = _handle_remove_body(_room_actions_source())

    assert "stop_continuous_analysis" in body
    assert "continuousStatus?.running" in body or "continuousStatus.running" in body
    assert "target_room_ids" in body


def test_apply_select_clip_mark_in_only_current_room() -> None:
    """Task 2: 精修选片只向本房写入 mark，禁止展开 selectedRoomIds。"""
    body = _apply_select_clip_body(_workbench_source())

    assert "roomId ? [roomId] : []" in body
    assert "...selectedRoomIds" not in body


def test_short_refresh_confirms_when_analyzing_or_aligned() -> None:
    """Task 3: 短按刷新在对齐就绪或持续分析中须二次确认。"""
    body = _handle_refresh_short_click_body(_workbench_source())

    assert "Modal.confirm" in body
    assert "刷新预览将使公共轴失效" in body
    assert "continuousAnalyzing" in body
    assert "getAlignStatus" in body
    assert "refresh_room_status" in body


def test_disconnect_warns_secondary_room_leaves_analysis_mapping() -> None:
    """Task 4: 副房断连时提示已退出持续分析映射，主房仍停分析。"""
    body = _handle_disconnect_body(_room_actions_source())

    assert "该房间已退出持续分析映射，后续回合可能仅入列主房" in body
    assert "target_room_ids" in body
    assert "stop_continuous_analysis" in body
    assert "continuousStatus.room_id === roomId" in body or "continuousStatus.room_id !== roomId" in body


def test_timeline_invalidation_clears_refining_state() -> None:
    """Task 5: 公共轴失效时取消精修并清空 refining / 本地拖拽标记。"""
    invalid_body = _timeline_invalidated_body(_workbench_source())

    assert "setRefiningClipId(null)" in invalid_body
    assert "setLocalDragMark(null)" in invalid_body
    assert "cancel_refine_clip" in invalid_body


def test_timeline_ready_clears_refining_state() -> None:
    """Task 5: 对齐成功建立新公共轴时同样退出精修。"""
    ready_body = _timeline_ready_body(_workbench_source())

    assert "setRefiningClipId(null)" in ready_body
    assert "setLocalDragMark(null)" in ready_body


def _settings_source() -> str:
    return (ROOT / "lsc-electron/src/pages/Settings/index.tsx").read_text(encoding="utf-8")


def _handle_confirm_analysis_export_body(source: str) -> str:
    return source.split("const handleConfirmAnalysisExport = () => {", 1)[1].split(
        "// 监听分析结果与进度", 1
    )[0]


def _enter_timeline_live_body(source: str) -> str:
    return source.split("const enterTimelineLive = useCallback", 1)[1].split(
        "const handleTimelineSeek", 1
    )[0]


def test_continuous_analysis_warns_when_main_room_preview_disabled() -> None:
    """Task 10: 主房无预览时提示以状态面板有效间隔为准（后端 valorant 强制 interval=5）。"""
    body = _handle_confirm_analysis_export_body(_workbench_source())

    assert "mainRoomPreviewEnabled" in body
    assert "主房未开启预览：请以状态面板中的有效间隔为准" in body
    assert "message.info" in body


def test_enter_timeline_live_skips_no_dvr_but_goes_live_for_dvr_rooms() -> None:
    """Task 11: 混选 no-DVR 时仍对可 DVR 房间 goLive，并提示回看房间。"""
    body = _enter_timeline_live_body(_workbench_source())

    assert "skippedNoDvr" in body or "isNoDvrPreviewMode" in body
    assert "dvrIds" in body
    assert "部分房间为回看模式，未跳转直播沿" in body
    assert "goLive" in body
    # 不应再整集 no-op
    assert "if (targetsIncludeNoDvrMode(ids, roomList)) return" not in body


def test_preview_quality_change_warns_timeline_realign() -> None:
    """Task 12: 预览画质变更时提示公共轴可能失效需重新对齐。"""
    workbench = _workbench_source()
    settings = _settings_source()

    assert "set_preview_quality_response" in workbench
    assert "公共轴" in workbench or "对齐" in workbench
    assert "preview_quality" in settings
    assert "公共轴" in settings or "对齐" in settings


def test_aligning_mutex_blocks_mark_add_and_export() -> None:
    """Task 6: 对齐中互斥标记、加切片与导出操作。"""
    source = _workbench_source()
    helper = _ensure_not_aligning_body(source)

    assert "aligning" in helper
    assert "正在对齐，请稍候" in helper

    for marker in (
        "const handleControlMarkIn = useCallback",
        "const handleControlMarkOut = useCallback",
        "const handleControlAddClip = useCallback",
        "const handleExportMany = (targets: ClipSegment[])",
        "const handleConfirmExport = async () =>",
        "const handleExportClip = (clip: ClipSegment",
    ):
        fn_body = source.split(marker, 1)[1].split("\n  const ", 1)[0]
        assert "ensureNotAligning()" in fn_body, marker


def test_confirm_clip_filters_disconnected_targets() -> None:
    """Task 7: 确认目标集过滤断连房并优先 continuousTargetRoomIds。"""
    body = _handle_confirm_clip_body(_workbench_source())

    assert "continuousTargetRoomIds" in body
    assert "is_connected" in body
    assert "clip.room_id" in body
    assert "align_group_id" in body


def test_batch_stop_warns_when_continuous_analysis_running() -> None:
    """Task 8: 批量停录时若持续分析运行须提示收尾入列。"""
    body = _handle_batch_stop_body(_workbench_source())

    assert "continuousAnalysisStatus" in body or "continuousStatus" in body
    assert "持续分析将收尾并将回合入列待确认，请勿立刻停止分析" in body


def test_pending_clip_confirm_skips_existing_manual_marks() -> None:
    """Task 9: AI pending 不覆盖已有有效手标（非本回合精修时）。"""
    body = _clip_confirm_status_body(_workbench_source())

    assert "mark_in" in body
    assert "mark_out" in body
    assert "refiningClipIdRef" in body
    assert "set_mark_in" in body
    assert "hasBothMarks" in body or (
        "mark_in != null" in body and "mark_out != null" in body
    )
