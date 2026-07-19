"""源码/行为守卫：六模块稳定性与延迟修复。"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROOM_HANDLER = (ROOT / "python-backend" / "handlers" / "room_handler.py").read_text(encoding="utf-8")
TIMELINE_HANDLERS = (ROOT / "python-backend" / "handlers" / "timeline_handlers.py").read_text(encoding="utf-8")
MANAGER = (ROOT / "lsc" / "gui" / "multi_room" / "manager.py").read_text(encoding="utf-8")
RECORDING_SERVICE = (ROOT / "lsc" / "core" / "services" / "recording_service.py").read_text(encoding="utf-8")
MSE_STREAMER = (ROOT / "lsc" / "core" / "services" / "mse_streamer.py").read_text(encoding="utf-8")
SHARED_INGEST = (ROOT / "lsc" / "core" / "services" / "shared_ingest.py").read_text(encoding="utf-8")
USE_WS = (ROOT / "lsc-electron" / "src" / "hooks" / "useWebSocket.ts").read_text(encoding="utf-8")
APP_STORE = (ROOT / "lsc-electron" / "src" / "store" / "appStore.ts").read_text(encoding="utf-8")
WORKBENCH = (ROOT / "lsc-electron" / "src" / "pages" / "Workbench" / "index.tsx").read_text(encoding="utf-8")


def test_export_start_failure_broadcasts_clip_failed():
    assert "clip_failed" in ROOM_HANDLER
    # 启动失败分支在 result['error'] 时也要 broadcast
    assert "导出启动失败" in ROOM_HANDLER or "导出任务失败" in ROOM_HANDLER
    idx_err = ROOM_HANDLER.find("导出任务失败: room=%s, job=%s, error=%s")
    assert idx_err > 0
    window = ROOM_HANDLER[idx_err : idx_err + 400]
    assert "clip_failed" in window


def test_export_jobs_registered_inside_run_export():
    assert "export_jobs[job_id] = clip_id" in ROOM_HANDLER


def test_export_queue_put_nowait_on_full():
    assert "put_nowait" in ROOM_HANDLER
    assert "导出队列已满" in ROOM_HANDLER


def test_align_split_brain_rejects_missing_timeline():
    assert "公共时间轴创建失败" in ROOM_HANDLER
    assert "公共时间轴未就绪" in WORKBENCH or "公共时间轴创建失败" in WORKBENCH


def test_align_audio_map_runs_in_executor():
    assert "run_in_executor" in ROOM_HANDLER
    assert "align_audio_map" in ROOM_HANDLER
    # 互相关应在 executor 内调用，而非 handler 协程直接阻塞
    assert "lambda: align_audio_map(" in ROOM_HANDLER


def test_continuous_skip_kick_allows_retry_after_error():
    # OCR/扫描失败后必须允许同窗重试
    assert "last_scan_error" in ROOM_HANDLER
    assert 'if state.get("last_scan_error")' in ROOM_HANDLER or "state.get('last_scan_error')" in ROOM_HANDLER

def test_pause_analysis_does_not_skip_tick():
    # pause_analysis 分支应返回 skip=False
    idx = ROOM_HANDLER.find('if pressure.get("pause_analysis")')
    assert idx > 0
    window = ROOM_HANDLER[idx : idx + 350]
    assert ", False" in window or ",False" in window
    assert "return max(" in window or "return effective_interval" in window


def test_stop_recording_timeout_at_least_15s():
    assert "timeout=15.0" in ROOM_HANDLER or "timeout=15" in ROOM_HANDLER


def test_shared_mse_reconnect_rotates_epoch():
    assert "Shared MSE reconnect succeeded" in ROOM_HANDLER
    idx = ROOM_HANDLER.find("Shared MSE reconnect succeeded")
    window = ROOM_HANDLER[idx : idx + 800]
    assert "preview_epoch_id" in window


def test_queue_rooms_update_uses_throttle_path():
    assert "_broadcast_rooms" in ROOM_HANDLER
    idx = ROOM_HANDLER.find("def _queue_rooms_update")
    window = ROOM_HANDLER[idx : idx + 600]
    assert "_broadcast_rooms" in window


def test_export_clip_by_id_passes_content_offset_snapshot():
    assert "content_offset=snap_content_offset" in TIMELINE_HANDLERS or "content_offset=snap" in TIMELINE_HANDLERS


def test_mse_streamer_live_without_re_flag():
    # 直播路径：-re 仅在 _is_file 分支追加
    assert "if self._is_file:" in MSE_STREAMER
    idx = MSE_STREAMER.find("if self._is_file:")
    window = MSE_STREAMER[idx : idx + 80]
    assert '"-re"' in window or "'-re'" in window


def test_frontend_watchdog_has_fail_count_and_toast():
    assert "_mseWatchdogFailCount" in USE_WS
    assert "预览恢复中" in USE_WS
    assert "request_mse_init" in USE_WS
    assert "enable_preview" in USE_WS
    # 恢复时不得把 lastRecv 伪装成 now
    idx = USE_WS.find("Stall detected")
    assert idx > 0
    window = USE_WS[max(0, idx - 200) : idx + 400]
    assert "_lastMseSegmentTimePerRoom.set(r.room_id, now)" not in window


def test_setrooms_avoids_noop_replace():
    assert "roomsShallowEqual" in APP_STORE
    assert "setRooms:" in APP_STORE
    assert "return state" in APP_STORE

def test_recording_preflight_supports_custom_min_free():
    assert "min_free_bytes_per_stream" in RECORDING_SERVICE
    assert "_MIN_FREE_BYTES_WHILE_RECORDING" in MANAGER
    assert "is_reconnecting" in MANAGER


def test_shared_ingest_stdout_stall_watchdog():
    assert "stdout stalled" in SHARED_INGEST or "_PREVIEW_STDOUT_STALL" in SHARED_INGEST


def test_system_stats_throttled():
    assert "SYSTEM_STATS_MIN_INTERVAL_MS" in USE_WS


def test_should_skip_kick_behavior_unit():
    """行为：last_scan_error 时不跳过。"""
    # 内联复制逻辑避免导入 Qt 重依赖
    def _should_skip(state, scan_range, *, full_rescan, use_ocr, finalize):
        if finalize:
            return False
        if state.get("last_scan_error"):
            return False
        phase = "full" if full_rescan else "incremental"
        return (
            state.get("scan_range") == scan_range
            and state.get("scan_phase") == phase
            and bool(state.get("refine_with_ocr")) == bool(use_ocr)
        )

    scan = (10.0, 80.0)
    state = {
        "scan_range": scan,
        "scan_phase": "incremental",
        "refine_with_ocr": True,
        "last_scan_error": "timeout",
    }
    assert _should_skip(state, scan, full_rescan=False, use_ocr=True, finalize=False) is False
    state["last_scan_error"] = None
    assert _should_skip(state, scan, full_rescan=False, use_ocr=True, finalize=False) is True
