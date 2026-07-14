"""Persistence layer unit tests.

Covers room loading/saving (atomic write), analysis result persistence,
stale detection, and edge cases (missing files, malformed JSON).
"""
from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

# python-backend has hyphen in directory name — import via importlib
_backend_dir = os.path.join(os.path.dirname(__file__), '..', 'python-backend')
_persistence_mod = importlib.import_module('persistence', package=os.path.basename(_backend_dir) if False else '')
# Re-import properly: add dir to path and import directly
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

from persistence import (  # noqa: E402
    load_rooms,
    save_rooms,
    save_analysis_results,
    load_analysis_results,
    is_analysis_stale,
    _ensure_dir,
    _analysis_json_path,
)


class TestEnsureDir:
    """Test directory creation helper."""

    def test_creates_missing_dir(self, tmp_path: Path):
        target = tmp_path / "sub" / "dir"
        _ensure_dir(target)
        assert target.is_dir()

    def test_existing_dir_no_error(self, tmp_path: Path):
        target = tmp_path / "existing"
        target.mkdir()
        _ensure_dir(target)  # should not raise
        assert target.is_dir()


class TestLoadRooms:
    """Test room loading from JSON."""

    def test_file_not_exists(self, tmp_path: Path):
        result = load_rooms(tmp_path / "nonexistent.json")
        assert result == []

    def test_dict_format_with_rooms(self, tmp_path: Path):
        data = {"rooms": [{"url": "https://example.com", "platform": "bilibili"}]}
        path = tmp_path / "rooms.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        result = load_rooms(path)
        assert len(result) == 1
        assert result[0]["url"] == "https://example.com"

    def test_legacy_list_format(self, tmp_path: Path):
        data = [{"url": "https://a.com"}, {"url": "https://b.com"}]
        path = tmp_path / "rooms.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        result = load_rooms(path)
        assert len(result) == 2

    def test_malformed_json(self, tmp_path: Path):
        path = tmp_path / "rooms.json"
        path.write_text("{not valid json", encoding="utf-8")
        result = load_rooms(path)
        assert result == []

    def test_dict_without_rooms_key(self, tmp_path: Path):
        data = {"not_rooms": []}
        path = tmp_path / "rooms.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        result = load_rooms(path)
        assert result == []

    def test_rooms_not_list(self, tmp_path: Path):
        data = {"rooms": "not a list"}
        path = tmp_path / "rooms.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        result = load_rooms(path)
        assert result == []


class TestSaveRooms:
    """Test room saving with atomic write."""

    def test_save_and_load_roundtrip(self, tmp_path: Path):
        rooms = [{"url": "https://test.com", "platform": "direct"}]
        path = tmp_path / "rooms.json"
        assert save_rooms(rooms, path) is True
        loaded = load_rooms(path)
        assert loaded == rooms

    def test_atomic_no_tmp_file_left(self, tmp_path: Path):
        rooms = [{"url": "https://example.com"}]
        path = tmp_path / "rooms.json"
        save_rooms(rooms, path)
        # No leftover .tmp files
        assert not path.with_suffix(".json.tmp").exists()

    def test_save_empty_list(self, tmp_path: Path):
        path = tmp_path / "rooms.json"
        assert save_rooms([], path) is True
        loaded = load_rooms(path)
        assert loaded == []

    def test_save_unicode_content(self, tmp_path: Path):
        rooms = [{"name": "测试房间", "url": "https://bilibili.com/123"}]
        path = tmp_path / "rooms.json"
        assert save_rooms(rooms, path) is True
        loaded = load_rooms(path)
        assert loaded[0]["name"] == "测试房间"


class TestAnalysisPersistence:
    """Test analysis results save/load/stale detection."""

    def _make_video(self, tmp_path: Path) -> str:
        """Create a fake video file and return its path."""
        video = tmp_path / "recording.mp4"
        video.write_bytes(b"fake video data")
        return str(video)

    def test_analysis_json_path(self, tmp_path: Path):
        video = self._make_video(tmp_path)
        path = _analysis_json_path(video)
        assert path.name == "recording.analysis.json"
        assert path.parent == tmp_path

    def test_save_and_load_analysis(self, tmp_path: Path):
        video = self._make_video(tmp_path)
        highlights = [{"start": 0.0, "end": 10.0, "score": 0.8}]
        assert save_analysis_results(video, "room1", "combined", highlights, 5.2, {"audio": 0.5}) is True

        loaded = load_analysis_results(video)
        assert loaded is not None
        assert loaded["room_id"] == "room1"
        assert loaded["mode"] == "combined"
        assert loaded["analysis_time_sec"] == 5.2
        assert len(loaded["highlights"]) == 1
        assert loaded["video_mtime"] > 0

    def test_load_nonexistent_analysis(self, tmp_path: Path):
        video = self._make_video(tmp_path)
        assert load_analysis_results(video) is None

    def test_is_stale_file_removed(self, tmp_path: Path):
        video = self._make_video(tmp_path)
        highlights = []
        save_analysis_results(video, "room1", "combined", highlights)
        loaded = load_analysis_results(video)

        # Remove the video file
        os.remove(video)
        assert is_analysis_stale(video, loaded) is True

    def test_is_stale_mtime_changed(self, tmp_path: Path):
        video = self._make_video(tmp_path)
        save_analysis_results(video, "room1", "combined", [])
        loaded = load_analysis_results(video)

        # Manually set the video mtime to be in the past (beyond 1.0s threshold)
        old_mtime = os.path.getmtime(video) - 5.0
        os.utime(video, (old_mtime, old_mtime))
        # The stored mtime is from save time; 5s difference > 1.0s threshold
        assert is_analysis_stale(video, loaded) is True

    def test_is_not_stale_when_mtime_same(self, tmp_path: Path):
        video = self._make_video(tmp_path)
        save_analysis_results(video, "room1", "combined", [])
        loaded = load_analysis_results(video)

        assert is_analysis_stale(video, loaded) is False

    def test_is_stale_no_mtime_field(self, tmp_path: Path):
        video = self._make_video(tmp_path)
        stored = {"room_id": "room1"}  # no video_mtime
        assert is_analysis_stale(video, stored) is True

    def test_save_analysis_malformed_path(self, tmp_path: Path):
        # Save to a directory that can't be created (invalid)
        highlights = [{"start": 0.0, "end": 5.0}]
        # Should not raise, just return False or True depending on OS
        result = save_analysis_results(str(tmp_path / "nonexistent" / "vid.mp4"), "r1", "ai", highlights)
        # Either True (if dir created) or False — just verify no crash
        assert isinstance(result, bool)
