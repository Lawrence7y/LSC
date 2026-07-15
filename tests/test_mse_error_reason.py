"""mse_error / recording_stopped 分层 reason 回归。"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_mse_error_broadcast_includes_reason_key() -> None:
    source = (ROOT / "python-backend/handlers/room_handler.py").read_text(encoding="utf-8")
    chunk = source.split("MSE reconnect exhausted", 1)[1].split("return", 1)[0]
    assert "'reason'" in chunk or '"reason"' in chunk


def test_all_mse_error_broadcasts_include_reason() -> None:
    source = (ROOT / "python-backend/handlers/room_handler.py").read_text(encoding="utf-8")
    for match in re.finditer(r"broadcast\('mse_error',\s*\{([^}]+)\}", source):
        chunk = match.group(1)
        assert "'reason'" in chunk or '"reason"' in chunk


def test_mse_reconnect_loop_detects_offline_reason() -> None:
    source = (ROOT / "python-backend/handlers/room_handler.py").read_text(encoding="utf-8")
    reconnect = source.split("async def _on_mse_error", 1)[1].split("async def ", 1)[0]
    assert "_is_stream_offline_error" in reconnect or "offline" in reconnect


def test_recording_stopped_offline_emit_in_manager() -> None:
    source = (ROOT / "lsc/gui/multi_room/manager.py").read_text(encoding="utf-8")
    marker = "reconnect stopped because stream is offline"
    idx = source.find(marker)
    assert idx != -1
    chunk = source[idx:idx + 400]
    assert "recording_stopped.emit" in chunk
    assert "'offline'" in chunk or '"offline"' in chunk
