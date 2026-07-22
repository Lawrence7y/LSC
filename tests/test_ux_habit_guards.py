from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _workbench_source() -> str:
    return (ROOT / "lsc-electron/src/pages/Workbench/index.tsx").read_text(encoding="utf-8")


def _clip_list_source() -> str:
    return (ROOT / "lsc-electron/src/pages/Workbench/components/ClipList.tsx").read_text(encoding="utf-8")


def _can_export_for_shortcut_body(source: str) -> str:
    return source.split("function canExportForShortcut", 1)[1].split("\n}", 1)[0]


def _handle_export_many_body(source: str) -> str:
    return source.split("const handleExportMany", 1)[1].split("const handleOpenExportFile", 1)[0]


def test_can_export_for_shortcut_rejects_pending_and_refining() -> None:
    """Ctrl+E 与 canExportClip 一致：pending/refining 不可导出。"""
    body = _can_export_for_shortcut_body(_workbench_source())

    assert "confirm_status === 'pending'" not in body
    assert "confirm_status === 'refining'" not in body
    assert "user_confirmed" in body
    assert "ocr_confirmed" in body
    assert "vision_confirmed" in body


def test_handle_export_many_does_not_auto_confirm_pending_with_bounds_only() -> None:
    """批量导出可对 pending 走 boundsOnly 确认再导出，但不得改精修态/同步目标。"""
    body = _handle_export_many_body(_workbench_source())

    assert "boundsOnly: true" in body
    assert "syncTargets: false" in body
    # 禁止无 boundsOnly 的静默 handleConfirmClip（会改 refining / 同步）
    assert "handleConfirmClip(clip, { syncTargets: false, boundsOnly: true })" in body


def test_can_export_clip_includes_vision_confirmed() -> None:
    """视觉确认回合与 ocr_confirmed 一样可直接导出。"""
    source = _clip_list_source()
    body = source.split("function canExportClip", 1)[1].split("\n}", 1)[0]

    assert "vision_confirmed" in body
    assert "case 'vision_confirmed'" in source


def test_clip_list_batch_export_uses_can_export_clip_only() -> None:
    """导出全部/所选包含可确认导出项（pending 经确认并导出路径），与单条一致。"""
    source = _clip_list_source()

    assert "canExportOrConfirmExport" in source
    actionable_block = source.split("const actionableClips = useMemo", 1)[1].split("const selectedClips", 1)[0]
    assert "canExportOrConfirmExport" in actionable_block

    selected_block = source.split("const selectedActionable = useMemo", 1)[1].split("const pendingCount", 1)[0]
    assert "canExportOrConfirmExport" in selected_block


def test_clip_list_batch_export_tooltip_warns_pending() -> None:
    """存在 pending 且无可导项时，批量按钮 disabled 并提示先确认。"""
    source = _clip_list_source()

    assert "请先确认待调整的切片" in source
    assert "待确认切片将先确认再导出" not in source
    assert "disabled={actionableClips.length === 0}" in source


def test_single_confirm_and_export_path_preserved() -> None:
    """单条「确认并导出」仍保留，不与批量路径混用。"""
    clip_list = _clip_list_source()
    workbench = _workbench_source()

    assert "onConfirmAndExport" in clip_list
    assert "handleConfirmAndExport" in workbench
    assert "const handleConfirmAndExport" in workbench


def _export_clip_shortcut_body(source: str) -> str:
    return source.split("case 'export:clip': {", 1)[1].split("        }\n      }", 1)[0]


def test_ctrl_e_export_shortcut_does_not_auto_export_refining() -> None:
    """Ctrl+E 不得对 refining 条走 handleExportMany 自动确认导出。"""
    export_clip_case = _export_clip_shortcut_body(_workbench_source())

    assert "refiningClip" not in export_clip_case
    assert "没有可导出的切片" in export_clip_case or "未确认" in export_clip_case


def test_single_room_analysis_export_allowed() -> None:
    """非持续分析允许单房间入列，不再强制 ≥2 房间。"""
    source = _workbench_source()

    assert "请至少选中 2 个房间（或开启持续分析）" not in source
    confirm_body = source.split("const handleConfirmAnalysisExport = () => {", 1)[1].split(
        "// 监听分析结果与进度", 1
    )[0]
    assert "targetRoomIds.length < 2" not in confirm_body.split("} else {", 1)[1]


def _mark_handler_bodies(source: str) -> tuple[str, str]:
    mark_in = source.split("const handleControlMarkIn = useCallback(() => {", 1)[1].split(
        "const handleControlMarkOut = useCallback", 1
    )[0]
    mark_out = source.split("const handleControlMarkOut = useCallback(() => {", 1)[1].split(
        "const handleControlAddClip = useCallback", 1
    )[0]
    return mark_in, mark_out


def test_mark_in_out_requires_preview_before_marking() -> None:
    """无预览时 I/O 标记须拦截并提示先开预览。"""
    source = _workbench_source()
    mark_in, mark_out = _mark_handler_bodies(source)

    assert "请先开启预览再标记入出点" in source
    assert "ensureMarkPreviewReady" in mark_in
    assert "ensureMarkPreviewReady" in mark_out
    assert "preview_enabled" in source.split("const ensureMarkPreviewReady", 1)[1].split(
        "const handleControlMarkIn", 1
    )[0]
    assert "__msePlayers" in source.split("const ensureMarkPreviewReady", 1)[1].split(
        "const handleControlMarkIn", 1
    )[0]


def test_mark_in_warns_when_multi_room_not_aligned() -> None:
    """多选未对齐时 mark:in 警告但仍继续标记；mark:out 不重复提示。"""
    source = _workbench_source()
    mark_in, mark_out = _mark_handler_bodies(source)

    assert "多房间未对齐：各房入出点按各自预览时间标记，导出可能不同步。建议先「一键对齐」" in mark_in
    assert "selectedRoomIds.size >= 2" in mark_in
    assert "status !== 'ready'" in mark_in
    assert "多房间未对齐" not in mark_out


def test_clip_list_shows_approximate_tag_in_row() -> None:
    """近似定位切片行内显示橙色「近似」Tag，不只靠 hover。"""
    source = _clip_list_source()

    assert "isApprox" in source
    assert "近似" in source
    assert 'color="orange"' in source


def test_approximate_export_requires_modal_confirm() -> None:
    """单条/批量导出近似切片须先 Modal.confirm，标题「近似定位切片」。"""
    source = _workbench_source()

    assert "近似定位切片" in source
    assert "仍要导出" in source
    confirm_body = source.split("const handleConfirmExport = () => {", 1)[1].split(
        "const handleCancelExportModal", 1
    )[0]
    assert "Modal.confirm" in confirm_body
    assert "isApproximateClip(previewClip)" in confirm_body or "isApproximate" in confirm_body

    export_many_body = _handle_export_many_body(source)
    assert "Modal.confirm" in export_many_body
    assert "approxCount" in export_many_body


def test_stop_recording_finalize_copy_does_not_imply_auto_export() -> None:
    """停录/收尾提示不得暗示收尾会自动导出，应说明回合入列待确认。"""
    source = _workbench_source()

    assert "确认回合并导出" not in source
    assert "收尾确认回合并导出" not in source
    assert "收尾并将回合入列待确认" in source
    assert "请勿立刻停止分析" in source


def _mute_toggle_shortcut_body(source: str) -> str:
    return source.split("case 'mute:toggle': {", 1)[1].split("        case 'fullscreen':", 1)[0]


def test_m_key_mute_toggles_single_primary_room_only() -> None:
    """M 键只切换主选/参考/首间静音，不得 forEach 多选房间。"""
    body = _mute_toggle_shortcut_body(_workbench_source())

    assert "selectedRoomIds.forEach" not in body
    assert "selectedRoomId || referenceRoomId" in body
    assert "handleToggleMute(muteRoomId)" in body


def _timeline_invalidated_body(source: str) -> str:
    return source.split("on('timeline_invalidated'", 1)[1].split("return () => {", 1)[0]


def test_timeline_invalidation_keeps_room_local_marks() -> None:
    """对齐失效清空公共轴但不发 set_mark_in/out null；toast 说明本地入出点保留。"""
    body = _timeline_invalidated_body(_workbench_source())

    assert "setCommonMarkIn(null)" in body
    assert "setCommonMarkOut(null)" in body
    assert "set_mark_in" not in body
    assert "set_mark_out" not in body
    assert "各房间本地入出点仍保留" in body
    assert "多房间精确切片需对齐后重标公共轴" in body


def _analysis_modal_body(source: str) -> str:
    return source.split("title={analysisIsContinuous ? '持续分析设置' : '多房间同步分析'}", 1)[1].split(
        "</Modal>", 1
    )[0]


def test_sync_analysis_modal_has_no_export_preset_select() -> None:
    """非持续同步分析弹窗不得展示导出预设 Select。"""
    source = _workbench_source()
    modal = _analysis_modal_body(source)

    assert "入列待确认，不会自动导出" in modal
    assert "!analysisIsContinuous && (" not in modal or "导出预设" not in modal.split("!analysisIsContinuous", 1)[-1]
    assert "continuousPresetId" not in modal
    assert "preset_id:" not in source.split("send('start_analysis_export'", 1)[1].split("})", 1)[0]


def test_toolbar_mute_all_button_unchanged() -> None:
    """工具栏全员静音仍对所有房间生效。"""
    source = _workbench_source()
    toolbar_mute = source.split("setAllMuted(newMuted)", 1)[0].split("const [allMuted", 1)[1]

    assert "rooms.forEach" in toolbar_mute or "currentRooms.forEach" in toolbar_mute
    assert "set_preview_muted" in toolbar_mute


def _record_toggle_body(source: str) -> str:
    return source.split("case 'record:toggle': {", 1)[1].split("        }\n        case 'mute:toggle':", 1)[0]


def test_record_toggle_hybrid_state_stops_only() -> None:
    """R 键混合状态（部分录制/部分未录）仅停止，不启动未录房间。"""
    body = _record_toggle_body(_workbench_source())

    assert "hybridRecordState" in body
    assert "已选房间录制状态不一致：本次仅停止录制中的房间。未录制房间请再次按 R 启动" in body
    assert "toStart.forEach(rid => handleStartRecord(rid))" in body
    hybrid_true_block = body.split("if (hybridRecordState) {", 1)[1].split("} else {", 1)[0]
    assert "handleStartRecord" not in hybrid_true_block


def test_align_live_loading_shows_eight_second_hint() -> None:
    """一键对齐 loading 提示约 8 秒采集。"""
    source = _workbench_source()

    assert "采集预览音频并对齐（约 8 秒）..." in source
    assert "对齐中..." not in source.split("const handleAlignLive", 1)[1].split("const handle", 1)[0]


def test_claude_md_preview_align_capture_is_eight_seconds() -> None:
    """CLAUDE.md 文档：前端预览对齐采集约 8 秒。"""
    claude = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")

    assert "约 8 秒" in claude or "约 **8 秒**" in claude
    assert "3.0s PCM" not in claude


def test_continuous_modal_syncs_target_rooms_while_open() -> None:
    """分析 Modal 打开期间同步目标房间，并以 openedWithMultiRef 守卫持续分析。"""
    source = _workbench_source()

    assert "openedWithMultiRef" in source
    assert "openedWithMultiRef.current = targetRoomIds.length >= 2" in source
    sync_block = source.split("useEffect(() => {", 1)[1]
    modal_sync = sync_block.split("if (!continuousModalOpen || continuousAnalyzing) return", 1)[1].split("}, [continuousModalOpen", 1)[0]
    assert "setContinuousTargetRoomIds(nextIds)" in modal_sync
    assert "setContinuousMainRoom" in modal_sync

    assert "持续分析需要至少两间目标房间，请再选中一间" in source
    assert "请关闭弹窗后保持多选两间房再打开" not in source
    confirm_body = source.split("const handleConfirmAnalysisExport = () => {", 1)[1].split(
        "// 监听分析结果与进度", 1
    )[0]
    assert "openedWithMultiRef.current && targetRoomIds.length < 2" in confirm_body
