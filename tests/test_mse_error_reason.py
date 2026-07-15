"""mse_error / recording_stopped 分层 reason 回归。"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_mse_error_broadcast_includes_reason_key() -> None:
    source = (ROOT / "python-backend/handlers/room_handler.py").read_text(encoding="utf-8")
    chunk = source.split("async def _finalize_mse_error", 1)[1].split("while True:", 1)[0]
    assert "'reason'" in chunk or '"reason"' in chunk


def test_all_mse_error_broadcasts_include_reason() -> None:
    source = (ROOT / "python-backend/handlers/room_handler.py").read_text(encoding="utf-8")
    for match in re.finditer(r"broadcast\('mse_error',\s*\{([^}]+)\}", source):
        chunk = match.group(1)
        assert "'reason'" in chunk or '"reason"' in chunk


def test_unified_offline_detection_rule() -> None:
    source = (ROOT / "python-backend/handlers/room_handler.py").read_text(encoding="utf-8")
    helper = source.split("def _is_stream_info_offline", 1)[1].split(
        "def _mse_offline_error_message", 1
    )[0]
    assert "not info.is_live" in helper
    assert "_is_stream_offline_error" in helper
    probe = source.split("def _probe_stream_offline", 1)[1].split(
        "def _invalidate_room_timeline", 1
    )[0]
    assert "_is_stream_info_offline" in probe
    assert "_room_stream_offline_confirmed" not in source


def test_mse_reconnect_early_exits_on_offline() -> None:
    source = (ROOT / "python-backend/handlers/room_handler.py").read_text(encoding="utf-8")
    reconnect = source.split("async def _on_mse_error", 1)[1].split("                def _start():", 1)[0]
    assert "_finalize_mse_error" in reconnect
    assert "'offline'" in reconnect or '"offline"' in reconnect
    assert "主播已下线" in reconnect or "_mse_offline_error_message" in reconnect
    for block in reconnect.split("if offline:")[1:]:
        segment = block.split("current_error", 1)[0]
        assert "await _finalize_mse_error" in segment
        assert "return" in segment
        assert "continue" not in segment


def test_mse_reconnect_exhausted_offline_message() -> None:
    source = (ROOT / "python-backend/handlers/room_handler.py").read_text(encoding="utf-8")
    exhausted = source.split("MSE reconnect exhausted", 1)[1].split("# 4. 计算指数退避", 1)[0]
    assert "mse_failure_reason == 'offline'" in exhausted
    assert "_mse_offline_error_message" in exhausted


def test_recording_stopped_offline_emit_in_manager() -> None:
    source = (ROOT / "lsc/gui/multi_room/manager.py").read_text(encoding="utf-8")
    marker = "reconnect stopped because stream is offline"
    idx = source.find(marker)
    assert idx != -1
    chunk = source[idx:idx + 400]
    assert "recording_stopped.emit" in chunk
    assert "'offline'" in chunk or '"offline"' in chunk
