"""主播下播离线退化 — 源码守卫回归（方案 A 契约迁移，2026-09-11）。

旧契约：确认下播后由后端 _start_recording_file_mse 新起一条「文件 MSE 回看流」
（review 通道 + 独立 session/epoch + 全局 2 路名额）。

新契约（docs/plans/scheme-a-local-file-review-20260911.md §3.2）：回看 = 前端直接播
本地录制文件，后端不再有任何文件流通道。下播时后端只做三件事：
停（可选）录制 → 停直播预览 sink → 置 preview_mode='degraded' + preview_error 并广播。
本文件因此断言新契约，并断言旧通道已彻底不存在（而不是把断言删掉）。
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROOM_HANDLER = (ROOT / "python-backend/handlers/room_handler.py").read_text(encoding="utf-8")


def _degrade_helper_body() -> str:
    return ROOM_HANDLER.split("async def _degrade_preview_offline", 1)[1].split(
        "def _invalidate_room_timeline", 1
    )[0]


def _finalize_mse_error_head() -> str:
    return ROOM_HANDLER.split("async def _finalize_mse_error", 1)[1].split("while True:", 1)[0]


def test_offline_finalize_degrades_preview_instead_of_starting_file_stream() -> None:
    """offline 分支必须改走降级（本地文件回看），且旧文件流 helper 已整条删除。"""
    chunk = _finalize_mse_error_head()
    assert "reason == 'offline'" in chunk
    assert "_degrade_preview_offline" in chunk
    # 旧通道不得留有悬空引用（源码级不存在该符号）
    assert "_start_recording_file_mse" not in ROOM_HANDLER


def test_degrade_helper_stops_live_sink_and_marks_degraded() -> None:
    helper = _degrade_helper_body()
    assert "_stop_live_preview_streamer" in helper
    assert 'preview_mode = "degraded"' in helper
    assert "preview_error" in helper
    assert "rooms_updated" in helper
    assert "preview_phase" in helper


def test_degrade_helper_starts_no_file_stream() -> None:
    """降级路径不得启动任何文件流：不再构造 MseStreamer / 不再传 is_file。"""
    helper = _degrade_helper_body()
    assert "MseStreamer" not in helper
    assert "is_file" not in helper
    assert "active_preview_channel" not in helper
    assert "review_session_id" not in helper


def test_degrade_helper_keeps_recording_file_available_for_local_review() -> None:
    """可回看文件存在才保持 preview_enabled（前端据此切本地文件回看），否则置 False。"""
    helper = _degrade_helper_body()
    assert "validate_recording" in helper
    assert "room.preview_enabled = bool(valid and path)" in helper


def test_recording_stopped_offline_hook_degrades_preview() -> None:
    chunk = ROOM_HANDLER.split("def _on_manager_recording_stopped_offline", 1)[1].split(
        "def _broadcast_system_stats", 1
    )[0]
    assert "reason != 'offline'" in chunk
    assert "_degrade_preview_offline" in chunk
    assert "stop_recording_if_active=False" in chunk


def test_mse_streamer_file_mode_skips_network_flags() -> None:
    """MseStreamer 自身仍保留 file 模式能力（源码未改），但业务侧已无文件流调用者。"""
    source = (ROOT / "lsc/core/services/mse_streamer.py").read_text(encoding="utf-8")
    assert "is_file: bool = False" in source or "is_file" in source
    assert "if not self._is_file:" in source
    # 网络重连选项仅在非文件分支
    file_branch = source.split("if not self._is_file:", 1)[1]
    assert "-reconnect" in file_branch
    assert "-timeout" in file_branch
    assert "headers_to_ffmpeg_input_args" in file_branch
    # 但 room_handler 里已没有任何文件流调用者
    assert "is_file=True" not in ROOM_HANDLER


def test_room_session_drops_review_fields_and_adds_dvr_path() -> None:
    source = (ROOT / "lsc/core/session.py").read_text(encoding="utf-8")
    assert "preview_mode" in source
    assert "live_mse" in source
    assert "dvr_output_path" in source
    for gone in (
        "active_preview_channel",
        "review_session_id",
        "review_start_sec",
        "review_window_end_sec",
        "preview_review_start_sec",
        "recording_review",
    ):
        assert gone not in source, "RoomSession 不应再保留回看通道字段: %s" % gone
