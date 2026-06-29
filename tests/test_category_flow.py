"""Tests for category field propagation across platform adapters and backend."""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from unittest.mock import MagicMock

_python_backend = os.path.join(os.path.dirname(__file__), '..', 'python-backend')
if _python_backend not in sys.path:
    sys.path.insert(0, _python_backend)

import pytest

from lsc.platforms.base import StreamInfo, BasePlatformAdapter
from lsc.gui.multi_room.session import RoomSession


class TestStreamInfoCategory:
    def test_category_default_empty(self):
        info = StreamInfo(platform="bilibili", room_url="https://live.bilibili.com/123")
        assert info.category == ""
        assert info.to_legacy_dict()["category"] == ""

    def test_category_set(self):
        info = StreamInfo(
            platform="huya",
            room_url="https://www.huya.com/123",
            category="无畏契约",
        )
        assert info.category == "无畏契约"
        assert info.to_legacy_dict()["category"] == "无畏契约"

    def test_base_adapter_success_includes_category(self):
        class TestAdapter(BasePlatformAdapter):
            platform = "test"
            display_name = "Test"

            def parse(self, url: str) -> StreamInfo:
                return self._success(url, category="TestCategory")

        adapter = TestAdapter()
        info = adapter.parse("https://test.com/123")
        assert info.category == "TestCategory"


class TestRoomSessionCategory:
    def test_apply_stream_info_sets_category(self):
        session = RoomSession(room_id="1", room_url="https://test.com")
        info = StreamInfo(platform="bilibili", room_url="https://test.com", category="游戏")
        session.apply_stream_info(info)
        assert session.category == "游戏"

    def test_apply_stream_info_empty_category(self):
        session = RoomSession(room_id="1", room_url="https://test.com")
        info = StreamInfo(platform="bilibili", room_url="https://test.com", category="")
        session.apply_stream_info(info)
        assert session.category == ""

    def test_apply_stream_info_none_category(self):
        session = RoomSession(room_id="1", room_url="https://test.com")
        info = StreamInfo(platform="bilibili", room_url="https://test.com")
        session.apply_stream_info(info)
        assert session.category == ""


class TestRoomToDictCategory:
    def _create_mock_room(self, category: str = ""):
        room = MagicMock()
        room.room_id = "1"
        room.room_url = "https://test.com"
        room.platform = "bilibili"
        room.platform_name = "B站"
        room.streamer_name = "Test"
        room.stream_title = "Title"
        room.stream_url = ""
        room.is_connecting = False
        room.is_connected = True
        room.is_recording = False
        room.record_output_path = ""
        room.record_started_at = None
        room.record_size_mb = 0
        room.last_error = ""
        room.preview_enabled = False
        room.preview_paused = False
        room.preview_muted = True
        room.mark_in = None
        room.mark_out = None
        room.mark_in_wallclock = None
        room.mark_out_wallclock = None
        room.recording_start_mono = None
        room.preview_latency = 2.0
        room.category = category
        return room

    def _load_room_to_dict(self):
        import importlib.util
        room_handler_path = os.path.join(
            os.path.dirname(__file__), '..', 'python-backend', 'handlers', 'room_handler.py'
        )
        spec = importlib.util.spec_from_file_location("room_handler", room_handler_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module._room_to_dict

    def test_room_to_dict_includes_category(self):
        _room_to_dict = self._load_room_to_dict()
        room = self._create_mock_room(category="无畏契约")
        result = _room_to_dict(room)
        assert result["category"] == "无畏契约"

    def test_room_to_dict_empty_category(self):
        _room_to_dict = self._load_room_to_dict()
        room = self._create_mock_room(category="")
        result = _room_to_dict(room)
        assert result["category"] == ""
