from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_websocket_defines_disconnected_queue_policy() -> None:
    source = (ROOT / "lsc-electron/src/services/websocket.ts").read_text(encoding="utf-8")

    assert "DISCONNECTED_QUEUEABLE_TYPES" in source
    assert "shouldQueueWhenDisconnected" in source
    assert "'get_rooms'" in source
    assert "'request_mse_init'" not in source.split("DISCONNECTED_QUEUEABLE_TYPES", 1)[1].split(")", 1)[0]


def test_media_source_player_source_open_and_cleanup_are_single_lifecycle_paths() -> None:
    source = (ROOT / "lsc-electron/src/services/mediaSourcePlayer.ts").read_text(encoding="utf-8")
    sourceopen_body = source.split("addEventListener('sourceopen'", 1)[1].split("}, { signal })", 1)[0]
    cleanup_body = source.split("private _cleanup(): void", 1)[1].split("private _setState", 1)[0]

    assert sourceopen_body.count("this._onSourceOpen?.()") == 1
    assert "this._abortController?.abort()" in cleanup_body
    assert "this._abortController = null" in cleanup_body


def test_mse_init_retry_timers_are_tracked_per_room() -> None:
    source = (ROOT / "lsc-electron/src/hooks/useWebSocket.ts").read_text(encoding="utf-8")

    assert "_mseInitRetryTimers" in source
    assert "_mseInitRetryTimers[roomId]" in source
    assert "Object.values(_mseInitRetryTimers).forEach(clearTimeout)" in source


def test_mse_caches_have_ttl_and_room_cleanup() -> None:
    source = (ROOT / "lsc-electron/src/hooks/useWebSocket.ts").read_text(encoding="utf-8")

    assert "_MSE_CACHE_TTL_MS" in source
    assert "_pruneExpiredMseCache" in source
    assert "export function clearMseRoomCache" in source
    clear_body = source.split("export function clearMseRoomCache", 1)[1].split("function _pruneExpiredMseCache", 1)[0]

    assert "delete _mseInitCache[roomId]" in clear_body
    assert "delete _mseSegmentCache[roomId]" in clear_body
    assert "clearTimeout(_mseInitRetryTimers[roomId])" in clear_body


def test_video_preview_updates_registry_after_web_audio_route_creation() -> None:
    source = (ROOT / "lsc-electron/src/components/VideoPreview.tsx").read_text(encoding="utf-8")
    sourceopen_body = source.split("onSourceOpen: () => {", 1)[1].split("},", 1)[0]

    assert "__msePlayers" in sourceopen_body
    assert "audioSource: audioSourceRef.current" in sourceopen_body
    assert "gainNode: gainNodeRef.current" in sourceopen_body


def test_video_preview_resets_player_on_preview_source_change() -> None:
    """preview_mode / preview_epoch_id 切换须重建 MsePlayer，避免丢弃新 mse_init。"""
    source = (ROOT / "lsc-electron/src/components/VideoPreview.tsx").read_text(encoding="utf-8")
    reset_body = source.split("预览源切换：", 1)[1].split("useEffect(() => {", 1)[1].split("}, [active, roomId, previewMode", 1)[0]

    assert "preview_mode" in source or "previewMode" in source
    assert "preview_epoch_id" in source or "previewEpochId" in source
    assert "playerGeneration" in source
    assert "disposePlayerFully" in reset_body
    assert "clearMseRoomCache" in reset_body
    assert "setPlayerGeneration" in reset_body
    assert "[active, roomId, playerGeneration]" in source


def test_preview_audio_capture_disconnects_only_current_recorder_from_shared_source() -> None:
    source = (ROOT / "lsc-electron/src/utils/previewAudioAligner.ts").read_text(encoding="utf-8")
    cleanup_body = source.split("const cleanup = () => {", 1)[1].split("const timeout = setTimeout", 1)[0]

    assert "source.disconnect(node)" in cleanup_body
    assert "source.disconnect()" not in cleanup_body


def test_preview_audio_capture_temporarily_unmutes_shared_media_element() -> None:
    source = (ROOT / "lsc-electron/src/utils/previewAudioAligner.ts").read_text(encoding="utf-8")
    shared_branch = source.split("if (sharedSource) {", 1)[1].split("} else {", 1)[0]
    cleanup_body = source.split("const cleanup = () => {", 1)[1].split("const timeout = setTimeout", 1)[0]

    assert "const previousMuted = video.muted" in source
    assert "__lscSuppressMuteSync" in source
    assert "mutedOverridden = true" in shared_branch
    assert "video.muted = false" in shared_branch
    assert "video.muted = previousMuted" in source
    assert "restoreMutedOverride()" in cleanup_body


def test_video_preview_ignores_internal_capture_mute_overrides() -> None:
    source = (ROOT / "lsc-electron/src/components/VideoPreview.tsx").read_text(encoding="utf-8")
    volumechange_body = source.split("const handleVolumeChange = () => {", 1)[1].split("video.addEventListener", 1)[0]

    assert "__lscSuppressMuteSync" in volumechange_body
    assert "return" in volumechange_body.split("__lscSuppressMuteSync", 1)[1].split("if (video.muted", 1)[0]


def test_preview_audio_capture_restores_mute_override_on_setup_failure() -> None:
    source = (ROOT / "lsc-electron/src/utils/previewAudioAligner.ts").read_text(encoding="utf-8")
    cleanup_body = source.split("const cleanup = () => {", 1)[1].split("const timeout = setTimeout", 1)[0]
    catch_body = source.rsplit("} catch (e) {", 1)[1].split("return null", 1)[0]

    assert "const restoreMutedOverride = () => {" in source
    assert "restoreMutedOverride()" in cleanup_body
    assert "restoreMutedOverride()" in catch_body


def test_go_live_button_calls_force_live_edge_method() -> None:
    source = (ROOT / "lsc-electron/src/pages/Workbench/index.tsx").read_text(encoding="utf-8")
    # Go Live / 贴右回 Live 统一走 enterTimelineLive → player.goLive()
    live_body = source.split("const enterTimelineLive = useCallback", 1)[1].split(
        "const handleTimelineSeek = useCallback", 1
    )[0]
    handle_body = source.split("const handleGoLive = useCallback(() => {", 1)[1].split(
        "  }, [selectedRoomIds", 1
    )[0]

    assert "enterTimelineLive(targets)" in handle_body
    assert "typeof player.goLive === 'function'" in live_body
    assert "player.goLive()" in live_body


def test_go_live_button_logs_player_and_buffer_diagnostics() -> None:
    source = (ROOT / "lsc-electron/src/pages/Workbench/index.tsx").read_text(encoding="utf-8")
    live_body = source.split("const enterTimelineLive = useCallback", 1)[1].split(
        "const handleTimelineSeek = useCallback", 1
    )[0]

    assert "直播按钮诊断" in live_body
    assert "hasPlayer" in live_body
    assert "bufferedStart" in live_body
    assert "bufferedEnd" in live_body
    assert "readyState" in live_body


def test_mse_player_go_live_always_seeks_to_buffer_end() -> None:
    source = (ROOT / "lsc-electron/src/services/mediaSourcePlayer.ts").read_text(encoding="utf-8")
    go_live_body = source.split("goLive(): void {", 1)[1].split("  /**", 1)[0]

    assert "const target = Math.max(bufStart, bufEnd - 0.3)" in go_live_body
    assert "this._video.currentTime = target" in go_live_body
    assert "this._liveEdgeAligned = false" in go_live_body


def test_mse_player_go_live_empty_buffer_waits_for_next_segment() -> None:
    source = (ROOT / "lsc-electron/src/services/mediaSourcePlayer.ts").read_text(encoding="utf-8")
    go_live_body = source.split("goLive(): void {", 1)[1].split("  /**", 1)[0]

    assert "buffer empty" in go_live_body
    assert "this._liveEdgeAligned = false" in go_live_body
    assert "this._tryPlay(0)" in go_live_body


def test_shared_preview_keeps_mse_event_names() -> None:
    source = (ROOT / "python-backend/handlers/room_handler.py").read_text(encoding="utf-8")

    assert "mse_init" in source
    assert "mse_segment" in source
    assert "mse_error" in source
    assert "mse_reconnecting" in source
    assert "mse_reconnected" in source
    assert "request_mse_init" in source


def _workbench_align_live_body(source: str) -> str:
    return source.split("const handleAlignLive = useCallback(async () => {", 1)[1].split(
        "  }, [selectedRoomIds, send", 1
    )[0]


def test_workbench_align_live_uses_longer_preview_audio_window() -> None:
    source = (ROOT / "lsc-electron/src/pages/Workbench/index.tsx").read_text(encoding="utf-8")
    align_body = _workbench_align_live_body(source)

    assert "const previewAlignDuration = 8.0" in align_body
    assert "captureAudio(rid, video, previewAlignDuration)" in align_body
    # Phase 1 必须按房间各自跳直播沿，禁止共用同一个 currentTime 绝对值
    assert "goLive" in align_body
    assert "minBufferEnd" not in align_body
    assert "const targetTime = Math.max(0, minBufferEnd" not in align_body
    assert "end - 0.5" in align_body


def test_preview_audio_aligner_rejects_near_silent_rms() -> None:
    source = (ROOT / "lsc-electron/src/utils/previewAudioAligner.ts").read_text(encoding="utf-8")
    # 仅丢弃近乎全零；弱信号峰值归一化后仍可对齐
    assert "peak < 1e-5 || rms < 1e-5" in source
    assert "silent_audio" in source
    assert "0.5 / peak" in source


def test_workbench_alignment_request_includes_preview_diagnostics() -> None:
    source = (ROOT / "lsc-electron/src/pages/Workbench/index.tsx").read_text(encoding="utf-8")
    align_body = _workbench_align_live_body(source)

    assert "diagnostics:" in align_body
    assert "current_time" in align_body
    assert "buffer_start" in align_body
    assert "buffer_end" in align_body
    assert "ingest_mode" in align_body


def test_workbench_alignment_request_includes_audio_capture_diagnostics() -> None:
    source = (ROOT / "lsc-electron/src/pages/Workbench/index.tsx").read_text(encoding="utf-8")
    align_body = _workbench_align_live_body(source)

    assert "ready_state" in align_body
    assert "has_audio_track" in align_body
    assert "rms" in align_body
    assert "sample_count" in align_body
    assert "capture_reason" in align_body


def test_workbench_alignment_shortage_message_includes_failure_summary() -> None:
    source = (ROOT / "lsc-electron/src/pages/Workbench/index.tsx").read_text(encoding="utf-8")
    align_body = _workbench_align_live_body(source)

    assert "captureFailures" in align_body
    assert "formatCaptureFailureSummary" in align_body
    assert "未精确对齐" in align_body
    assert "message.success" not in align_body.split("results.length < 2", 1)[1].split("send('align_preview_audio'", 1)[0]


def test_alignment_buffer_fallback_is_not_success() -> None:
    workbench = (ROOT / "lsc-electron/src/pages/Workbench/index.tsx").read_text(encoding="utf-8")
    assert "未精确对齐" in workbench

    # 捕获失败 / 后端失败路径：warning「未精确对齐」，禁止 message.success 宣称精确对齐
    align_body = _workbench_align_live_body(workbench)
    shortage = align_body.split("results.length < 2", 1)[1].split("send('align_preview_audio'", 1)[0]
    assert "message.warning" in shortage
    assert "未精确对齐" in shortage
    assert "message.success" not in shortage

    catch_path = align_body.split("} catch (err) {", 1)[1]
    assert "未精确对齐" in catch_path
    assert "message.success" not in catch_path

    response_fail = workbench.split("on('align_preview_audio_response'", 1)[1].split(
        "const offsets = data.offsets", 1
    )[0]
    assert "未精确对齐" in response_fail
    assert "message.success" not in response_fail

    # W6.1: 不再自动静音，toast 中提供「点击静音」按钮供用户手动操作
    assert "点击静音" in workbench


def test_preview_audio_aligner_records_capture_failure_reasons() -> None:
    source = (ROOT / "lsc-electron/src/utils/previewAudioAligner.ts").read_text(encoding="utf-8")

    assert "getLastCaptureDiagnostics" in source
    assert "no_audio_track" in source
    assert "silent" in source
    assert "buffer_empty" in source
    assert "sample_count" in source


def test_backend_alignment_handler_reads_preview_diagnostics() -> None:
    source = (ROOT / "python-backend/handlers/room_handler.py").read_text(encoding="utf-8")
    handler_body = source.split("@server.on('align_preview_audio')", 1)[1].split("@server.on('export_clip')", 1)[0]

    assert "diagnostics" in handler_body
    assert "ready_state" in handler_body
    assert "has_audio_track" in handler_body
    assert "rms" in handler_body
    assert "sample_count" in handler_body
    assert "capture_reason" in handler_body
    assert "pcm_base64" not in handler_body.split("diagnostics", 1)[1].split("_align_log.info", 1)[0]


def test_low_confidence_align_does_not_write_group_for_failed_rooms() -> None:
    source = (ROOT / "python-backend/handlers/room_handler.py").read_text(encoding="utf-8")
    handler_body = source.split("@server.on('align_preview_audio')", 1)[1].split("@server.on('export_clip')", 1)[0]

    assert "align_group_id" in handler_body
    # 仅可信 offset（≥0.3）写入 group；可信不足 2 路时不写 group
    assert "0.3" in handler_body
    assert "trusted" in handler_body
    assert "buffer_only" in handler_body
    apply_body = handler_body.split("def _apply_alignment", 1)[1].split("try:", 1)[0]
    assert "align_group_id" in apply_body
    # 低置信房间不得写入 align_group_id（分支内跳过或清零 offset）
    assert "content_offset = 0" in apply_body or "content_offset = 0.0" in apply_body


def test_workbench_alignment_response_does_not_count_low_confidence_zero_offsets() -> None:
    source = (ROOT / "lsc-electron/src/pages/Workbench/index.tsx").read_text(encoding="utf-8")
    response_body = source.split("on('align_preview_audio_response'", 1)[1]

    assert "const alignmentTrustThreshold = 0.3" in response_body
    assert "score < alignmentTrustThreshold" in response_body
    assert "send('set_content_offset', { room_id: rid, offset: 0 })" in response_body
    low_confidence_branch = response_body.split("score < alignmentTrustThreshold", 1)[1].split(
        "if (offset < 0.05)", 1
    )[0]
    assert "return" in low_confidence_branch
    # 部分成功用 warning，不得对低置信房间宣称全面精确成功
    assert "置信度不足" in response_body


def test_workbench_continuous_analysis_uses_explicit_game_modes() -> None:
    source = (ROOT / "lsc-electron/src/pages/Workbench/index.tsx").read_text(encoding="utf-8")

    assert "type AnalysisMode = 'valorant_round' | 'generic'" in source
    assert "useState<AnalysisMode>('generic')" in source
    assert "const isValorantRoundCutting = analysisGameType === 'valorant_round'" in source
    assert "mode: isValorantRoundCutting ? 'valorant_round' : 'scene'" in source
    assert "game: isValorantRoundCutting ? 'valorant' : 'generic'" in source
    assert '<Radio.Button value="valorant_round">无畏契约回合切割</Radio.Button>' in source
    assert '<Radio.Button value="generic">通用直播</Radio.Button>' in source
    assert "setAnalysisGameType('valorant')" not in source
    # 无畏契约持续分析：无预览 45s / 有预览 60s，禁止再写死 20s
    continuous_start = source.split("send('start_continuous_analysis'", 1)[1].split("})", 1)[0]
    assert "interval: 20" not in continuous_start
    assert "interval: 120" not in continuous_start
    assert "preview_enabled" in continuous_start
    assert "? 60 : 45" in continuous_start
    assert "isValorantRoundCutting" in continuous_start


def test_workbench_sync_export_freezes_target_rooms_until_response() -> None:
    source = (ROOT / "lsc-electron/src/pages/Workbench/index.tsx").read_text(encoding="utf-8")

    assert "const syncTargetRoomIdsRef = useRef<string[]>([])" in source
    request_body = source.split("send('start_analysis_export'", 1)[0]
    assert "syncTargetRoomIdsRef.current = [...targetRoomIds]" in request_body
    response_body = source.split("on('start_analysis_export_response'", 1)[1].split("on('start_continuous_analysis_response'", 1)[0]
    # W3: 同步分析与持续分析一致，仅 list_only 入列，不自动 queue_export；
    # response 只显示入列数，不再预创建 clips 关联 job_id
    assert "syncTargetRoomIdsRef.current = []" in response_body
    assert "入列" in response_body or "已分析" in response_body


def test_continuous_analysis_types_expose_round_progress_and_export_status() -> None:
    source = (ROOT / "lsc-electron/src/types/index.ts").read_text(encoding="utf-8")

    assert "recorded_duration?: number" in source
    assert "confirmed_rounds?: number" in source
    assert "pending_rounds?: number" in source
    assert "analysis_stage?:" in source
    assert "export_status?: 'queued' | 'exporting' | 'completed' | 'failed' | 'pending'" in source
    assert "export_error?: string" in source


def test_analysis_progress_renders_recorded_duration_round_counts_and_export_summary() -> None:
    source = (ROOT / "lsc-electron/src/components/AnalysisProgress.tsx").read_text(encoding="utf-8")

    assert "recorded_duration" in source
    assert "confirmed_rounds" in source
    assert "pending_rounds" in source
    assert "analysis_stage" in source
    assert "export_status" in source


def test_workbench_updates_clip_export_status_for_queue_progress_completion_and_failure() -> None:
    source = (ROOT / "lsc-electron/src/pages/Workbench/index.tsx").read_text(encoding="utf-8")

    assert "export_status: 'queued'" in source
    assert "export_status: 'exporting'" in source
    assert "export_status: 'completed'" in source
    assert "export_status: 'failed'" in source
    assert "export_error" in source


def test_clip_list_blocks_duplicate_export_and_allows_failed_retry() -> None:
    source = (ROOT / "lsc-electron/src/pages/Workbench/components/ClipList.tsx").read_text(encoding="utf-8")

    assert "clip.export_status === 'queued'" in source
    assert "clip.export_status === 'exporting'" in source
    assert "clip.export_status === 'failed'" in source
    assert "export_error" in source
    assert "一键导出" in source or "导出全部" in source
    assert "选择导出" in source or "导出所选" in source
    assert "Checkbox" in source


def test_analysis_progress_receives_real_export_summary_counts() -> None:
    progress = (ROOT / "lsc-electron/src/components/AnalysisProgress.tsx").read_text(encoding="utf-8")
    workbench = (ROOT / "lsc-electron/src/pages/Workbench/index.tsx").read_text(encoding="utf-8")

    assert "exportSummary" in progress
    assert "ExportSummary" in progress
    assert "queued" in progress and "exporting" in progress and "completed" in progress and "failed" in progress
    assert "useMemo" in workbench
    assert "exportSummary" in workbench
    assert "queued" in workbench and "exporting" in workbench and "completed" in workbench and "failed" in workbench


def test_continuous_status_preserves_task_snapshot_and_labels_waiting_recording() -> None:
    workbench = (ROOT / "lsc-electron/src/pages/Workbench/index.tsx").read_text(encoding="utf-8")
    progress = (ROOT / "lsc-electron/src/components/AnalysisProgress.tsx").read_text(encoding="utf-8")

    assert "const previous = useAppStore.getState().continuousAnalysisStatus" in workbench
    assert "{ ...previous, ...data }" in workbench
    assert "等待新录制" in progress
    assert "等待录制" in progress


def test_connect_room_response_uses_accepted_not_fake_success() -> None:
    """connect_room_response 不得 toast「连接成功」；仅 accepted=false 时回滚 is_connecting。"""
    workbench = (ROOT / "lsc-electron/src/pages/Workbench/index.tsx").read_text(encoding="utf-8")
    connect_resp = workbench.split("on('connect_room_response'", 1)[1].split("unsubs.push", 1)[0]
    finished = workbench.split("on('room_connect_finished'", 1)[1].split("unsubs.push", 1)[0]

    assert "accepted" in connect_resp
    assert "is_connecting: false" in connect_resp
    # 异步受理成功不得在 response 上 toast 连接成功
    assert "连接成功" not in connect_resp
    # 失败 toast 由 room_connect_finished 负责（success 可选）
    assert "连接失败" in finished
    assert "message.error" in finished


def test_workbench_optimistically_updates_connect_record_and_mute() -> None:
    """房间连接/录制/静音点击必须乐观更新 store，避免等 rooms_updated 才有反馈。"""
    workbench = (ROOT / "lsc-electron/src/pages/Workbench/index.tsx").read_text(encoding="utf-8")
    room_card = (ROOT / "lsc-electron/src/pages/Workbench/components/RoomCard.tsx").read_text(encoding="utf-8")
    types = (ROOT / "lsc-electron/src/types/index.ts").read_text(encoding="utf-8")

    assert "is_recording_starting" in types
    connect_body = workbench.split("const handleConnect = useCallback((roomId: string) => {", 1)[1].split("}, [send])", 1)[0]
    assert "is_connecting: true" in connect_body
    mute_body = workbench.split("const handleToggleMute = useCallback((roomId: string) => {", 1)[1].split("}, [send])", 1)[0]
    assert "preview_muted: newMuted" in mute_body
    record_body = workbench.split("const handleStartRecord = useCallback((roomId: string) => {", 1)[1].split("}, [send])", 1)[0]
    assert "is_recording_starting: true" in record_body
    assert "loading={!!room.is_recording_starting}" in room_card
    assert "启动中" in room_card


def test_room_handler_mute_awaits_before_broadcast_and_exposes_recording_starting() -> None:
    source = (ROOT / "python-backend/handlers/room_handler.py").read_text(encoding="utf-8")
    mute_body = source.split("async def handle_set_preview_muted(data):", 1)[1].split("@server.on(", 1)[0]
    assert "bridge.call(manager.set_preview_muted" in mute_body
    assert "bridge.submit(manager.set_preview_muted" not in mute_body
    assert "_broadcast_rooms(force=True)" in mute_body
    assert "'is_recording_starting': room_id in _recording_starting" in source
    start_body = source.split("async def handle_start_recording(data):", 1)[1].split("@server.on('stop_recording')", 1)[0]
    assert "_recording_starting.add(room_id)" in start_body
    assert "_broadcast_rooms(force=True)" in start_body


def test_workbench_does_not_auto_disconnect_on_missing_is_live() -> None:
    """后端 rooms_updated 不带 is_live 时，前端不得把已连接房间自动断开。

    回归：启动后首次连接成功再点录制/预览，会因 !r.is_live（undefined）误发
    disconnect_room，房间弹回未连接；disconnectedRef 又让第二次连接看似正常。
    """
    workbench = (ROOT / "lsc-electron/src/pages/Workbench/index.tsx").read_text(encoding="utf-8")
    room_to_dict = (
        (ROOT / "python-backend/handlers/room_handler.py")
        .read_text(encoding="utf-8")
        .split("def _room_to_dict(", 1)[1]
        .split("\ndef ", 1)[0]
    )

    assert "disconnectedRef" not in workbench
    assert "if (!r.is_live && (r.is_connected || r.is_recording || r.preview_enabled))" not in workbench
    # 当前后端未序列化 is_live；若以后补上并做自动断连，须用 === false 而非 !is_live
    assert "'is_live'" not in room_to_dict


def test_add_clip_snapshots_wallclock_fields() -> None:
    """切片入队时必须快照墙钟字段，避免导出时被房间当前 mark 覆盖。"""
    workbench = (ROOT / "lsc-electron/src/pages/Workbench/index.tsx").read_text(encoding="utf-8")
    body = workbench.split("const handleAddClip = useCallback", 1)[1].split("}, [addClip])", 1)[0]
    assert "mark_in_wallclock" in body
    assert "mark_out_wallclock" in body
    assert "recording_start_mono" in body
    assert "recording_media_start_mono" in body
    # mark_precision 必须与后端 exact 门控对齐：双墙钟 + rec mono
    assert "mark_precision" in body
    assert "recording_media_start_mono ?? room.recording_start_mono" in body

    export_many = workbench.split("const handleExportMany = ", 1)[1].split(
        "const handleOpenExportFile", 1
    )[0]
    assert "mark_in_wallclock" in export_many
    assert "use_room_marks: false" in export_many

    confirm_export = workbench.split("const handleConfirmExport = ", 1)[1].split(
        "const store = useAppStore.getState()", 1
    )[0]
    assert "mark_in_wallclock" in confirm_export
    assert "use_room_marks: false" in confirm_export


def test_destructive_stop_recording_paths_require_confirm() -> None:
    """凡会停止录制的路径（断开/R 键/长按刷新）必须二次确认。"""
    workbench = (ROOT / "lsc-electron/src/pages/Workbench/index.tsx").read_text(encoding="utf-8")
    disconnect = workbench.split("const handleDisconnect = useCallback", 1)[1].split("}, [send])", 1)[0]
    assert "Modal.confirm" in disconnect
    # R 键停止录制不得直接 handleStopRecord 而无确认
    toggle = workbench.split("case 'record:toggle'", 1)[1].split("case '", 1)[0]
    assert "Modal.confirm" in toggle or "confirmStopRecording" in toggle
    longpress = workbench.split("const handleRefreshLongPress", 1)[1].split("}, [", 1)[0]
    assert "Modal.confirm" in longpress


def test_scrub_mark_surfaces_approximate_precision() -> None:
    """拖拽标记须 live:false，并在 UI/导出路径标明近似，避免假精确。"""
    workbench = (ROOT / "lsc-electron/src/pages/Workbench/index.tsx").read_text(encoding="utf-8")
    clip_list = (ROOT / "lsc-electron/src/pages/Workbench/components/ClipList.tsx").read_text(
        encoding="utf-8"
    )

    assert "live: false" in workbench
    assert "approximate" in workbench or "近似" in workbench
    assert "近似" in workbench or "近似" in clip_list

    scrub = workbench.split("const handleMarkerDragEnd = useCallback", 1)[1].split(
        "const handleDeleteMarker", 1
    )[0]
    assert "live: false" in scrub
    assert "近似" in scrub

    assert "function isApproximateClip" in workbench

    export_many = workbench.split("const handleExportMany = ", 1)[1].split(
        "const handleOpenExportFile", 1
    )[0]
    confirm_export = workbench.split("const handleConfirmExport = ", 1)[1].split(
        "const handleCancelExportModal", 1
    )[0]
    assert "isApproximateClip" in export_many
    assert "message.warning" in export_many and "近似" in export_many
    assert "已排队" in export_many or "已提交" in export_many
    assert "skipped" in export_many
    assert "isApproximateClip" in confirm_export
    assert "message.warning" in confirm_export and "近似" in confirm_export
    assert "导出任务已提交" in confirm_export

    assert "mark_precision" in clip_list
    assert "近似" in clip_list


def test_preview_starting_state_shows_pull_stream_message() -> None:
    """预览已启用但尚未出画时，应显示拉流/转码等待文案。"""
    video_preview = (ROOT / "lsc-electron/src/components/VideoPreview.tsx").read_text(encoding="utf-8")
    assert "正在拉流/转码" in video_preview


def test_douyin_cookie_error_shows_settings_guidance() -> None:
    """抖音 Cookie/验证页类错误应在房间卡片引导去设置。"""
    room_card = (ROOT / "lsc-electron/src/pages/Workbench/components/RoomCard.tsx").read_text(encoding="utf-8")
    assert "去设置" in room_card
    assert "Cookie" in room_card or "抖音" in room_card
    assert "setSettingsDrawerOpen" in room_card or "settingsDrawerOpen" in room_card


def test_recording_queue_broadcast_before_semaphore() -> None:
    """多路开录进入 semaphore 前应广播 recording_queue 含 position/waiting。"""
    source = (ROOT / "python-backend/handlers/room_handler.py").read_text(encoding="utf-8")
    start_body = source.split("async def handle_start_recording(data):", 1)[1].split("@server.on('stop_recording')", 1)[0]
    assert "recording_queue" in start_body
    assert "position" in start_body
    assert "waiting" in start_body
    assert "_recording_semaphore" in start_body


def test_room_card_distinguishes_recording_queue_states() -> None:
    """录制启动中应区分排队中与启动 FFmpeg。"""
    room_card = (ROOT / "lsc-electron/src/pages/Workbench/components/RoomCard.tsx").read_text(encoding="utf-8")
    types = (ROOT / "lsc-electron/src/types/index.ts").read_text(encoding="utf-8")
    assert "排队中" in room_card
    assert "启动中" in room_card
    assert "recording_queue_position" in types or "is_recording_queued" in types


def test_shared_ingest_risk_warning_in_settings() -> None:
    """设置页共享进样开关旁须有风险说明，且不改后端默认。"""
    settings = (ROOT / "lsc-electron/src/pages/Settings/index.tsx").read_text(encoding="utf-8")
    assert "shared_ingest_enabled" in settings
    assert "录制中断会导致预览中断" in settings or "预览与录制共用同一进程" in settings


def test_add_clip_requires_recording_file() -> None:
    """未录制（无 record_output_path）时不得添加切片。"""
    workbench = (ROOT / "lsc-electron/src/pages/Workbench/index.tsx").read_text(encoding="utf-8")
    control_bar = (ROOT / "lsc-electron/src/pages/Workbench/components/ControlBar.tsx").read_text(encoding="utf-8")
    body = workbench.split("const handleAddClip = useCallback", 1)[1].split("}, [addClip])", 1)[0]
    assert "record_output_path" in body
    assert "请先开始录制后再添加切片" in workbench
    assert "record_output_path" in control_bar
    assert "请先开始录制" in control_bar


def test_workbench_blocks_exact_clip_when_clip_not_ready() -> None:
    """common 模式添加精确切片前必须校验 ctx.clip_ready。"""
    workbench = (ROOT / "lsc-electron/src/pages/Workbench/index.tsx").read_text(encoding="utf-8")
    body = workbench.split("const handleControlAddClip = useCallback(async () => {", 1)[1].split(
        "}, [selectedRoomIds, handleAddClip, addClip, commonMarkIn, commonMarkOut, send, on])", 1
    )[0]

    guard = body.find("!ctx.clip_ready")
    snap = body.find("create_clip_snapshot")
    assert guard != -1 and snap != -1 and guard < snap
    assert "录制未就绪" in body or "正在录制" in body


def test_unaligned_drag_add_forces_approximate() -> None:
    """未对齐拖拽路径必须 live:false + approximate，且不调用 create_clip_snapshot。"""
    wb = (ROOT / "lsc-electron/src/pages/Workbench/index.tsx").read_text(encoding="utf-8")
    assert "live: false" in wb or "live:false" in wb.replace(" ", "")
    assert "近似定位" in wb or "approximate" in wb

    add_body = wb.split("const handleAddClip = useCallback", 1)[1].split("}, [", 1)[0]
    assert "approximate" in add_body
    # 单房降级路径不得伪装精确切片
    assert "clip_snapshot_id" not in add_body or "create_clip_snapshot" not in add_body


def test_timeline_invalidation_clears_common_marks_and_waveform() -> None:
    """对齐失效时必须清空 common 标记与波形，并提示用户。"""
    workbench = (ROOT / "lsc-electron/src/pages/Workbench/index.tsx").read_text(encoding="utf-8")
    invalid_body = workbench.split("on('timeline_invalidated'", 1)[1].split("return () => {", 1)[0]

    assert "setCommonMarkIn(null)" in invalid_body
    assert "setCommonMarkOut(null)" in invalid_body
    assert "setWaveformPeaks([])" in invalid_body
    assert "message.warning" in invalid_body


def test_preview_phase_broadcast_and_ui() -> None:
    """后端广播 preview_phase，前端按阶段显示刷新流地址等文案。"""
    handler = (ROOT / "python-backend/handlers/room_handler.py").read_text(encoding="utf-8")
    assert "preview_phase" in handler
    assert "refreshing_url" in handler
    assert "probing" in handler

    preview = (ROOT / "lsc-electron/src/components/VideoPreview.tsx").read_text(encoding="utf-8")
    assert "刷新流地址" in preview or "refreshing_url" in preview
    assert "正在拉流/转码…" in preview  # 默认文案保留

    types = (ROOT / "lsc-electron/src/types/index.ts").read_text(encoding="utf-8")
    assert "preview_phase?:" in types

    ws = (ROOT / "lsc-electron/src/hooks/useWebSocket.ts").read_text(encoding="utf-8")
    assert "preview_phase" in ws


def test_frontend_sends_valorant_profile() -> None:
    """持续分析启动 payload 包含 valorant_profile，UI 提供游戏视角/赛事解说选择。"""
    wb = (ROOT / "lsc-electron/src/pages/Workbench/index.tsx").read_text(encoding="utf-8")
    assert "valorant_profile" in wb
    assert "游戏视角" in wb


def test_mse_error_does_not_unconditionally_stop_recording() -> None:
    workbench = (ROOT / "lsc-electron/src/pages/Workbench/index.tsx").read_text(encoding="utf-8")
    block = workbench.split("on('mse_error'", 1)[1].split("return () =>", 1)[0]
    assert "reason" in block
    assert "主播已下线" in block
    assert "offline" in block


def test_mse_error_offers_retry() -> None:
    """mse_error 覆盖层须有重试按钮，并走 enable_preview 重开预览。"""
    card = (ROOT / "lsc-electron/src/pages/Workbench/components/RoomCard.tsx").read_text(encoding="utf-8")
    preview = (ROOT / "lsc-electron/src/components/VideoPreview.tsx").read_text(encoding="utf-8")
    blob = card + preview
    assert "重试" in blob
    assert "enable_preview" in blob or "onRetryPreview" in blob
    # 重试须重开预览（enabled: true），而非仅停止
    retry_body = preview.split("const handleRetry = useCallback", 1)[1].split("}, [", 1)[0]
    assert "enabled: true" in retry_body


def test_timeline_coords_has_recording_converters() -> None:
    src = (ROOT / "lsc-electron/src/utils/timelineCoords.ts").read_text(encoding="utf-8")
    assert "commonToRecording" in src
    assert "recordingToCommon" in src
    assert "recording_to_common_delta" in src


def test_is_approximate_clip_excludes_ai_highlights() -> None:
    src = (ROOT / "lsc-electron/src/pages/Workbench/index.tsx").read_text(encoding="utf-8")
    body = src.split("function isApproximateClip", 1)[1].split("}", 1)[0]
    assert "is_ai_highlight" in body
    assert "mark_precision !== 'approximate'" in body


def test_confirm_clip_uses_common_to_recording() -> None:
    src = (ROOT / "lsc-electron/src/pages/Workbench/index.tsx").read_text(encoding="utf-8")
    body = src.split("const handleConfirmClip", 1)[1].split("setRefiningClipId(null)", 1)[0]
    assert "commonToRecording" in body
    assert "commonToPreview" not in body


def test_refine_banner_uses_dynamic_axis_label() -> None:
    src = (ROOT / "lsc-electron/src/pages/Workbench/index.tsx").read_text(encoding="utf-8")
    assert "公共时间轴" in src
    assert "预览时间轴" in src
    assert "当前为录制时间轴" not in src


def test_shortcuts_blocked_when_modal_visible() -> None:
    """存在可见 Ant Design Modal 或 role=dialog 时，业务快捷键应被拦截。"""
    src = (ROOT / "lsc-electron/src/hooks/useKeyboardShortcuts.ts").read_text(encoding="utf-8")
    assert "hasVisibleModal" in src
    assert ".ant-modal-wrap:not([style*=\"display: none\"])" in src
    assert "[role=\"dialog\"]" in src


def test_no_room_selected_shortcuts_show_feedback() -> None:
    """无选中房间时，业务快捷键应弹出 message.info 而非静默 return。"""
    src = (ROOT / "lsc-electron/src/pages/Workbench/index.tsx").read_text(encoding="utf-8")
    handler_body = src.split("const handleWorkbenchShortcut = useCallback(", 1)[1].split(
        "useKeyboardShortcuts(", 1
    )[0]
    assert "请先选择房间" in handler_body
    assert "message.info" in handler_body


# ── W2: 命名与手动切片模型守卫 ──


def test_clip_naming_helper_exists() -> None:
    """clipNaming.ts 必须存在并导出三个函数。"""
    src = (ROOT / "lsc-electron/src/utils/clipNaming.ts").read_text(encoding="utf-8")
    assert "sanitizeStreamerName" in src
    assert "formatManualClipLabel" in src
    assert "formatAiRoundClipLabel" in src
    assert "_M" in src
    assert "_R" in src


def test_workbench_imports_clip_naming() -> None:
    """Workbench 应导入并使用 clipNaming 工具（formatManualClipLabel 用于手动切片）。"""
    src = (ROOT / "lsc-electron/src/pages/Workbench/index.tsx").read_text(encoding="utf-8")
    assert "formatManualClipLabel" in src


def test_workbench_no_old_label_formats() -> None:
    """Workbench 不再使用旧格式字符串（`片段 ${` / `_高光` / `_回合{idx}_{start}s`）。"""
    src = (ROOT / "lsc-electron/src/pages/Workbench/index.tsx").read_text(encoding="utf-8")
    assert "片段 ${" not in src
    assert "_高光" not in src
    # 旧格式：`_回合{round_idx}_{int(export_start)}s`（Python 侧）
    # 前端不再有 `_高光${i + 1}` 这种拼接
    assert "_高光${" not in src


def test_manual_clip_shows_toast_not_refine() -> None:
    """手动切片点击时应 toast 提示，不进精修。"""
    src = (ROOT / "lsc-electron/src/pages/Workbench/index.tsx").read_text(encoding="utf-8")
    select_body = src.split("const handleSelectClip = ", 1)[1].split("const handleConfirmClip", 1)[0]
    assert "手动切片可直接导出" in select_body
    assert "message.info" in select_body


def test_manual_clip_uses_per_room_counter() -> None:
    """手动切片命名必须按 room_id 维度计数，而非全局 clips.length。"""
    src = (ROOT / "lsc-electron/src/pages/Workbench/index.tsx").read_text(encoding="utf-8")
    add_body = src.split("const handleAddClip = useCallback", 1)[1].split("}, [addClip])", 1)[0]
    # 必须过滤 room_id 来计数
    assert "room_id === roomId" in add_body or "filter" in add_body
    # 不得使用全局 clips.length 做序号
    assert "clips.length + 1" not in add_body


def test_python_handler_clip_naming_helpers() -> None:
    """Python room_handler 必须提供等价的命名 helper。"""
    src = (ROOT / "python-backend/handlers/room_handler.py").read_text(encoding="utf-8")
    assert "format_manual_clip_label" in src
    assert "format_ai_round_clip_label" in src
    assert "_M" in src
    assert "_R" in src


def test_python_no_old_label_formats() -> None:
    """Python handler 不再使用旧格式字符串。"""
    src = (ROOT / "python-backend/handlers/room_handler.py").read_text(encoding="utf-8")
    # 旧格式：`{room_name}_高光{i + 1}_s{int(t1)}`
    assert "_高光{i + 1}_s{int" not in src
    # 旧格式：`{room_name}_回合{round_idx}_{int(export_start)}s`
    assert "_回合{round_idx}_{int(export_start)}s" not in src


def test_settings_exposes_ocr_accel_control() -> None:
    settings = (ROOT / "lsc-electron/src/pages/Settings/index.tsx").read_text(encoding="utf-8")
    types = (ROOT / "lsc-electron/src/types/index.ts").read_text(encoding="utf-8")
    assert "ocr_accel" in types
    assert "OCR 加速" in settings
    assert 'value="dml"' in settings or "value={'dml'}" in settings or "|| 'dml'" in settings
    assert "DirectML" in settings
    assert "h264_nvenc (NVIDIA，推荐)" in settings


def test_timeline_experience_polish_shortcuts_and_loop() -> None:
    """体验抛光：步进/穿梭/速率快捷键 + A-B rAF 循环；不恢复波形渲染。"""
    shortcuts = (ROOT / "lsc-electron/src/hooks/useKeyboardShortcuts.ts").read_text(encoding="utf-8")
    workbench = (ROOT / "lsc-electron/src/pages/Workbench/index.tsx").read_text(encoding="utf-8")
    timeline = (ROOT / "lsc-electron/src/components/Timeline/index.tsx").read_text(encoding="utf-8")
    control = (ROOT / "lsc-electron/src/pages/Workbench/components/ControlBar.tsx").read_text(
        encoding="utf-8"
    )

    for sid in (
        "seek:back-1",
        "seek:fwd-1",
        "seek:back-fine",
        "seek:fwd-fine",
        "seek:back-2",
        "seek:fwd-2",
        "mark:nudge-out-back",
        "rate:cycle-up",
    ):
        assert sid in shortcuts
        assert sid in workbench

    assert "handleSeekByDelta" in workbench
    assert "requestAnimationFrame" in workbench
    assert "loopRafRef" in workbench
    assert "PLAYBACK_RATE_STEPS" in control or "playbackRate" in control
    assert "onPlaybackRateChange" in control
    # 波形仍停用（档 B）
    assert "波形已停用" in timeline or "waveformPeaks" in timeline


def test_timeline_scrub_can_leave_live_edge() -> None:
    """Live 钉右 / 非 Live 可回看：同步监听 + 冻结 ws + 贴右回 Live。"""
    workbench = (ROOT / "lsc-electron/src/pages/Workbench/index.tsx").read_text(encoding="utf-8")
    control = (ROOT / "lsc-electron/src/pages/Workbench/components/ControlBar.tsx").read_text(encoding="utf-8")
    timeline = (ROOT / "lsc-electron/src/components/Timeline/index.tsx").read_text(encoding="utf-8")
    coords = (ROOT / "lsc-electron/src/utils/timelineCoords.ts").read_text(encoding="utf-8")

    assert "scrubOverrideRef" in workbench
    assert "timelineFollowLive" in workbench
    assert "LIVE_EDGE_TOLERANCE_SEC" in workbench
    assert "enterTimelineLive" in workbench
    assert "setTimelineFollowLive(false)" in workbench
    assert "followLive={timelineFollowLive}" in workbench
    assert "panTimelineWindowStart" in workbench
    assert "panTimelineWindowStart" in coords
    assert "TIMELINE_MAX_WINDOW * 0.15" not in workbench
    assert "TIMELINE_MAX_WINDOW * 0.15" not in control

    seek_body = workbench.split("const mseSeek = useCallback", 1)[1].split("const mseTogglePlayPause", 1)[0]
    assert "bufEnd - 0.5" not in seek_body
    assert "scrubOverrideRef.current[roomId]" in seek_body

    seek_handler = workbench.split("const handleTimelineSeek = useCallback", 1)[1].split(
        "const handleTimelineScrubStart", 1
    )[0]
    assert "enterTimelineLive" in seek_handler
    assert "LIVE_EDGE_TOLERANCE_SEC" in seek_handler

    scrub_start = workbench.split("const handleTimelineScrubStart = useCallback", 1)[1].split(
        "const handleTimelineScrubEnd", 1
    )[0]
    assert "setTimelineFollowLive(false)" in scrub_start
    assert "setFrozenWindowStart" in scrub_start

    scrub_end = workbench.split("const handleTimelineScrubEnd = useCallback", 1)[1].split(
        "const handleAddClip", 1
    )[0]
    assert "enterTimelineLive" in scrub_end
    assert "finalTime" in scrub_end

    # Live：钉最右
    assert "followLive && !isScrubbing" in control
    assert "trackDuration" in control.split("const displayCurrent", 1)[1].split("const roomClips", 1)[0]

    assert "attachWindowDragListeners" in timeline
    assert "onScrubStart?.(ws)" in timeline
    assert "skipCurrentTime" in timeline
    assert "lastPreviewSeekTimeRef" in timeline
    mousedown_body = timeline.split("const handleMouseDown = useCallback", 1)[1].split(
        "const handleMouseMove = useCallback", 1
    )[0]
    assert "attachWindowDragListeners()" in mousedown_body
    assert "useEffect(() => {\n    if (!isDragging && !draggingMarker) return" not in timeline
    # 拖拽过程不 onSeek，松手 onScrubEnd 落点
    assert "onSeek(abs)" not in mousedown_body and "onSeek(snapped" not in mousedown_body
    assert "onScrubEndRef.current?.(finalAbs)" in timeline

    # scrub 中避免每帧父级重渲染 / WS；内容右沿只增不减
    assert "timelineScrubbingRef" in workbench
    assert "quiet" in seek_body or "{ quiet:" in workbench
    assert "if (timelineScrubbingRef.current) return" in workbench
    assert "只增不减" in workbench or "Math.max(lastContentEndRef.current" in workbench
    assert "contentEdgeRef" in control
    assert "Math.max(contentEdgeRef.current" in control


def test_mse_player_exposes_buffered_range() -> None:
    src = (ROOT / "lsc-electron/src/services/mediaSourcePlayer.ts").read_text(encoding="utf-8")
    assert "getBufferedRange(" in src
    assert "buffered.start(0)" in src


def test_timeline_dvr_start_prop() -> None:
    timeline = (ROOT / "lsc-electron/src/components/Timeline/index.tsx").read_text(encoding="utf-8")
    control = (ROOT / "lsc-electron/src/pages/Workbench/components/ControlBar.tsx").read_text(encoding="utf-8")
    assert "dvrStart" in timeline
    assert "dvrStart=" in control or "dvrStart={" in control
    # 紫标应对齐 dvrStart
    assert "dvrStartPct" in timeline or ("dvrStart" in timeline and "lsc-timeline__record-end" in timeline)


def test_preview_mode_type_exists() -> None:
    types = (ROOT / "lsc-electron/src/types/index.ts").read_text(encoding="utf-8")
    assert "preview_mode" in types
    assert "recording_review" in types


def test_timeline_seek_snaps_left_of_dvr_to_live() -> None:
    workbench = (ROOT / "lsc-electron/src/pages/Workbench/index.tsx").read_text(encoding="utf-8")
    handler = workbench.split("const handleTimelineSeek = useCallback", 1)[1].split(
        "const handleTimelineScrubStart", 1
    )[0]
    assert "dvrStart" in handler or "bufStart" in handler
    assert "enterTimelineLive" in handler


def test_recording_review_timeline_guards() -> None:
    """recording_review：无紫标、强制退出 followLive、回看胶囊、禁 goLive。"""
    workbench = (ROOT / "lsc-electron/src/pages/Workbench/index.tsx").read_text(encoding="utf-8")
    control = (ROOT / "lsc-electron/src/pages/Workbench/components/ControlBar.tsx").read_text(encoding="utf-8")
    room_card = (ROOT / "lsc-electron/src/pages/Workbench/components/RoomCard.tsx").read_text(encoding="utf-8")
    coords = (ROOT / "lsc-electron/src/utils/timelineCoords.ts").read_text(encoding="utf-8")

    assert "isNoDvrPreviewMode" in workbench
    assert "isRecordingReviewMode" in workbench
    dvr_block = workbench.split("const dvrStart = useMemo", 1)[1].split("}, [referenceRoomId", 1)[0]
    assert "isNoDvrPreviewMode" in dvr_block

    assert "setTimelineFollowLive(false)" in workbench
    follow_block = workbench.split("// recording_review / degraded：强制退出 followLive", 1)[1].split("}, [rooms", 1)[0]
    assert "isNoDvrPreviewMode" in follow_block

    timeline_view = workbench.split("const timelineView = useMemo", 1)[1].split("const dvrStart = useMemo", 1)[0]
    assert "isRecordingReview" in timeline_view
    assert "resolveRecordingReviewSpan" in timeline_view

    enter_live = workbench.split("const enterTimelineLive = useCallback", 1)[1].split(
        "const handleTimelineSeek", 1
    )[0]
    assert "targetsIncludeNoDvrMode" in enter_live

    go_live = workbench.split("const handleGoLive = useCallback", 1)[1].split(
        "// Phase 3: 音频对齐", 1
    )[0]
    assert "targetsIncludeNoDvrMode" in go_live

    assert "回看" in room_card
    assert "recording_review" in room_card
    assert "isRecordingReview" in room_card

    assert "goLiveDisabled" in control
    assert "resolveRecordingReviewSpan" in control

    assert "resolveRecordingReviewSpan" in coords
    assert "isNoDvrPreviewMode" in coords
