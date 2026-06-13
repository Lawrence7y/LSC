"""Tests for session history store."""
from __future__ import annotations

from lsc.gui.pages.session_history import SessionHistoryStore


def test_session_history_store_round_trips_sessions(tmp_path) -> None:
    store = SessionHistoryStore(tmp_path / "session_history.json")
    payload = {
        "source_url": "https://live.douyin.com/123",
        "video_path": "D:/recordings/a.mp4",
        "duration_sec": 120,
        "clips": [{"title": "clip_001", "path": "D:/output/clip_001.mp4"}],
        "status": "completed",
    }

    store.append_session(payload)
    sessions = store.load_sessions()

    assert sessions[0]["status"] == "completed"
    assert sessions[0]["clips"][0]["title"] == "clip_001"
