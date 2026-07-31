from __future__ import annotations

import importlib
import sys
from pathlib import Path

from lsc.platforms.base import ERROR_OFFLINE, StreamInfo

BACKEND_DIR = Path(__file__).resolve().parents[1] / "python-backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

room_handler = importlib.import_module("handlers.room_handler")


def test_room_url_validation_rejects_non_http_without_parsing(monkeypatch) -> None:
    def fail_if_called(_url: str) -> StreamInfo:
        raise AssertionError("格式校验失败时不应调用平台解析")

    monkeypatch.setattr(room_handler, "parse_stream", fail_if_called)

    result = room_handler._validate_room_url_candidate("file:///tmp/video.m3u8")

    assert result["valid"] is False
    assert result["error_code"] == "invalid_url"
    assert "http://" in result["error"]


def test_room_url_validation_accepts_live_stream(monkeypatch) -> None:
    monkeypatch.setattr(
        room_handler,
        "parse_stream",
        lambda url: StreamInfo(
            platform="bilibili",
            room_url=url,
            stream_url="https://cdn.example/live.m3u8",
            streamer="测试主播",
            is_live=True,
        ),
    )

    result = room_handler._validate_room_url_candidate("https://live.bilibili.com/123")

    assert result["valid"] is True
    assert result["platform"] == "bilibili"
    assert result["streamer"] == "测试主播"


def test_room_url_validation_accepts_recognized_offline_room(monkeypatch) -> None:
    monkeypatch.setattr(
        room_handler,
        "parse_stream",
        lambda url: StreamInfo(
            platform="huya",
            room_url=url,
            is_live=False,
            error="虎牙直播间未开播",
            error_code=ERROR_OFFLINE,
        ),
    )

    result = room_handler._validate_room_url_candidate("https://www.huya.com/123")

    assert result["valid"] is True
    assert result["is_live"] is False
    assert "未开播" in result["warning"]


def test_room_url_validation_rejects_parse_failure(monkeypatch) -> None:
    monkeypatch.setattr(
        room_handler,
        "parse_stream",
        lambda url: StreamInfo(
            platform="unknown",
            room_url=url,
            is_live=False,
            error="不支持的直播间链接或直播流地址",
            error_code="unsupported_url",
        ),
    )

    result = room_handler._validate_room_url_candidate("https://example.com/not-live")

    assert result["valid"] is False
    assert result["error_code"] == "unsupported_url"

