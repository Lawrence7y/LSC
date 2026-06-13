"""Tests for multi-room session state."""
from __future__ import annotations

from lsc.gui.multi_room import RoomSession
from lsc.platforms.base import StreamInfo


def test_room_session_defaults_to_muted_and_disconnected() -> None:
    session = RoomSession(room_id="room-1", room_url="https://live.douyin.com/123")

    assert session.room_id == "room-1"
    assert session.room_url == "https://live.douyin.com/123"
    assert session.platform == ""
    assert session.stream_info is None
    assert session.selected_quality == ""
    assert session.preview_muted is True
    assert session.is_connected is False
    assert session.is_recording is False
    assert session.record_output_path == ""
    assert session.record_started_at is None
    assert session.last_error == ""
    assert session.controller is None


def test_room_session_can_apply_stream_info_fields() -> None:
    session = RoomSession(room_id="room-1", room_url="https://live.bilibili.com/123")
    session.set_error("连接失败")
    info = StreamInfo(
        platform="bilibili",
        room_url=session.room_url,
        stream_url="https://example.com/live.m3u8",
        title="深夜直播",
        streamer="测试主播",
        is_live=True,
        quality_urls={"origin": "https://example.com/live.m3u8"},
        selected_quality="origin",
    )

    session.apply_stream_info(info)

    assert session.platform == "bilibili"
    assert session.stream_info is info
    assert session.selected_quality == "origin"
    assert session.is_connected is True
    assert session.last_error == ""


def test_room_session_can_capture_error_without_marking_connected() -> None:
    session = RoomSession(room_id="room-1", room_url="https://www.huya.com/123")

    session.set_error("虎牙直播间未开播")

    assert session.is_connected is False
    assert session.last_error == "虎牙直播间未开播"
