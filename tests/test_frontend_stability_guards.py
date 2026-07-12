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
