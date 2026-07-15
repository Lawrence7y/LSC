"""offline 确认后切换录制文件 MSE 回看 — 源码守卫回归。"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_offline_finalize_calls_file_mse_helper() -> None:
    source = (ROOT / "python-backend/handlers/room_handler.py").read_text(encoding="utf-8")
    chunk = source.split("async def _finalize_mse_error", 1)[1].split("while True:", 1)[0]
    assert "reason == 'offline'" in chunk
    assert "_start_recording_file_mse" in chunk


def test_offline_file_mse_uses_validate_recording() -> None:
    source = (ROOT / "python-backend/handlers/room_handler.py").read_text(encoding="utf-8")
    helper = source.split("async def _start_recording_file_mse", 1)[1].split(
        "def _invalidate_room_timeline", 1
    )[0]
    assert "validate_recording" in helper
    assert "recording_review" in helper
    assert "preview_mode" in helper


def test_recording_stopped_offline_hooks_file_mse() -> None:
    source = (ROOT / "python-backend/handlers/room_handler.py").read_text(encoding="utf-8")
    chunk = source.split("def _on_manager_recording_stopped_offline", 1)[1].split(
        "def _broadcast_system_stats", 1
    )[0]
    assert "reason != 'offline'" in chunk
    assert "_start_recording_file_mse" in chunk
    assert "stop_recording_if_active=False" in chunk


def test_invalid_recording_sets_degraded_preview_mode() -> None:
    source = (ROOT / "python-backend/handlers/room_handler.py").read_text(encoding="utf-8")
    helper = source.split("async def _start_recording_file_mse", 1)[1].split(
        "def _invalidate_room_timeline", 1
    )[0]
    assert 'preview_mode = "degraded"' in helper or "preview_mode = 'degraded'" in helper


def test_mse_streamer_file_mode_skips_network_flags() -> None:
    source = (ROOT / "lsc/core/services/mse_streamer.py").read_text(encoding="utf-8")
    assert "is_file: bool = False" in source or "is_file" in source
    assert "if not self._is_file:" in source
    # 网络重连选项仅在非文件分支
    file_branch = source.split("if not self._is_file:", 1)[1]
    assert "-reconnect" in file_branch
    assert "-timeout" in file_branch
    assert "headers_to_ffmpeg_input_args" in file_branch


def test_room_session_has_preview_mode_field() -> None:
    source = (ROOT / "lsc/gui/multi_room/session.py").read_text(encoding="utf-8")
    assert "preview_mode" in source
    assert "recording_review" in source or "live_mse" in source
