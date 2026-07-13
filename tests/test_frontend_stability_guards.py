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
    go_live_body = source.split("const handleGoLive = useCallback(() => {", 1)[1].split("  }, [selectedRoomIds])", 1)[0]

    assert "typeof player.goLive === 'function'" in go_live_body
    assert "player.goLive()" in go_live_body


def test_go_live_button_logs_player_and_buffer_diagnostics() -> None:
    source = (ROOT / "lsc-electron/src/pages/Workbench/index.tsx").read_text(encoding="utf-8")
    go_live_body = source.split("const handleGoLive = useCallback(() => {", 1)[1].split("  }, [selectedRoomIds")[
        0
    ]

    assert "直播按钮诊断" in go_live_body
    assert "hasPlayer" in go_live_body
    assert "bufferedStart" in go_live_body
    assert "bufferedEnd" in go_live_body
    assert "readyState" in go_live_body


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


def test_workbench_align_live_uses_longer_preview_audio_window() -> None:
    source = (ROOT / "lsc-electron/src/pages/Workbench/index.tsx").read_text(encoding="utf-8")
    align_body = source.split("const handleAlignLive = useCallback(async () => {", 1)[1].split("  }, [selectedRoomIds, send])", 1)[0]

    assert "const previewAlignDuration = 8.0" in align_body
    assert "captureAudio(rid, video, previewAlignDuration)" in align_body


def test_workbench_alignment_request_includes_preview_diagnostics() -> None:
    source = (ROOT / "lsc-electron/src/pages/Workbench/index.tsx").read_text(encoding="utf-8")
    align_body = source.split("const handleAlignLive = useCallback(async () => {", 1)[1].split("  }, [selectedRoomIds, send])", 1)[0]

    assert "diagnostics:" in align_body
    assert "current_time" in align_body
    assert "buffer_start" in align_body
    assert "buffer_end" in align_body
    assert "ingest_mode" in align_body


def test_workbench_alignment_request_includes_audio_capture_diagnostics() -> None:
    source = (ROOT / "lsc-electron/src/pages/Workbench/index.tsx").read_text(encoding="utf-8")
    align_body = source.split("const handleAlignLive = useCallback(async () => {", 1)[1].split("  }, [selectedRoomIds, send])", 1)[0]

    assert "ready_state" in align_body
    assert "has_audio_track" in align_body
    assert "rms" in align_body
    assert "sample_count" in align_body
    assert "capture_reason" in align_body


def test_workbench_alignment_shortage_message_includes_failure_summary() -> None:
    source = (ROOT / "lsc-electron/src/pages/Workbench/index.tsx").read_text(encoding="utf-8")
    align_body = source.split("const handleAlignLive = useCallback(async () => {", 1)[1].split("  }, [selectedRoomIds, send])", 1)[0]

    assert "captureFailures" in align_body
    assert "formatCaptureFailureSummary" in align_body
    assert "音频捕获不足" in align_body


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


def test_workbench_alignment_response_does_not_count_low_confidence_zero_offsets() -> None:
    source = (ROOT / "lsc-electron/src/pages/Workbench/index.tsx").read_text(encoding="utf-8")
    response_body = source.split("on('align_preview_audio_response'", 1)[1].split("const scoreValues", 1)[0]

    assert "const alignmentTrustThreshold = 0.3" in response_body
    assert "score < alignmentTrustThreshold" in response_body
    assert "send('set_content_offset', { room_id: rid, offset: 0 })" in response_body
    low_confidence_branch = response_body.split("score < alignmentTrustThreshold", 1)[1].split("if (offset < 0.05)", 1)[0]
    assert "return" in low_confidence_branch


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
    assert "interval: 20" in source
    assert "interval: 120" not in source


def test_workbench_sync_export_freezes_target_rooms_until_response() -> None:
    source = (ROOT / "lsc-electron/src/pages/Workbench/index.tsx").read_text(encoding="utf-8")

    assert "const syncTargetRoomIdsRef = useRef<string[]>([])" in source
    request_body = source.split("send('start_analysis_export'", 1)[0]
    assert "syncTargetRoomIdsRef.current = [...targetRoomIds]" in request_body
    response_body = source.split("on('start_analysis_export_response'", 1)[1].split("on('start_continuous_analysis_response'", 1)[0]
    assert "const targetIds = syncTargetRoomIdsRef.current" in response_body
    assert "selectedRoomIdsRef.current" not in response_body


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
    assert "一键导出" in source
    assert "选择导出" in source
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

