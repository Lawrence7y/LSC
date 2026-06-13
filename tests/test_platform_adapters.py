"""Tests for platform adapter primitives and direct stream URLs."""
from __future__ import annotations

from lsc.platforms.base import (
    ERROR_OFFLINE,
    ERROR_PARSE_FAILED,
    ERROR_RESTRICTED,
    ERROR_UNSUPPORTED_URL,
    PlatformAdapter,
    StreamInfo,
    headers_to_ffmpeg_input_args,
)
from lsc.platforms.direct import DirectAdapter
from lsc.platforms.registry import detect_platform, parse_stream, select_quality


def test_stream_info_legacy_dict_contains_current_gui_keys():
    info = StreamInfo(
        platform="direct",
        room_url="https://example.com/live.m3u8",
        stream_url="https://cdn.example.com/live.m3u8",
        title="公开直链",
        streamer="直链",
        is_live=True,
        quality_urls={"origin": "https://cdn.example.com/live.m3u8"},
        selected_quality="origin",
        headers={"Referer": "https://example.com/"},
        raw={"kind": "direct"},
    )

    legacy = info.to_legacy_dict()

    assert legacy["platform"] == "direct"
    assert legacy["isLive"] is True
    assert legacy["streamUrl"] == "https://cdn.example.com/live.m3u8"
    assert legacy["streamerName"] == "直链"
    assert legacy["availableQualities"] == ["origin"]
    assert legacy["qualityUrls"] == {"origin": "https://cdn.example.com/live.m3u8"}
    assert legacy["_headers"] == {"Referer": "https://example.com/"}
    assert legacy["_raw"] == {"kind": "direct"}
    assert legacy["_inputArgs"] == [
        "-headers",
        "Referer: https://example.com/\r\n",
    ]


def test_stream_info_defaults_include_raw_and_empty_stream_url():
    info = StreamInfo(platform="direct", room_url="https://example.com/live")

    assert info.stream_url == ""
    assert info.raw == {}


def test_error_constants_are_exposed_for_adapter_failures():
    assert ERROR_UNSUPPORTED_URL == "unsupported_url"
    assert ERROR_OFFLINE == "offline"
    assert ERROR_RESTRICTED == "restricted"
    assert ERROR_PARSE_FAILED == "parse_failed"


def test_headers_to_ffmpeg_input_args_returns_empty_for_no_headers():
    assert headers_to_ffmpeg_input_args({}) == []


def test_headers_to_ffmpeg_input_args_strips_newlines_from_keys_and_values():
    assert headers_to_ffmpeg_input_args(
        {
            "Referer\r\nX-Bad: injected": "https://example.com/\nInjected: value",
        }
    ) == [
        "-headers",
        "RefererX-Bad: injected: https://example.com/Injected: value\r\n",
    ]


def test_direct_m3u8_url_is_detected_and_parsed():
    url = "  https://cdn.example.com/path/live.m3u8?token=abc  "

    assert detect_platform(url) == "direct"

    info = parse_stream(url)

    assert DirectAdapter().can_handle(url) is True
    assert info.platform == "direct"
    assert info.is_live is True
    assert info.title == "公开直播流"
    assert info.streamer == "直链"
    assert info.stream_url == url.strip()
    assert info.quality_urls == {"origin": url.strip()}
    assert info.selected_quality == "origin"


def test_direct_flv_url_is_detected_and_parsed():
    url = "https://cdn.example.com/live/room.flv"

    info = parse_stream(url)

    assert info.platform == "direct"
    assert info.is_live is True
    assert info.stream_url == url


def test_direct_adapter_rejects_missing_netloc_and_non_http_scheme():
    adapter = DirectAdapter()

    assert adapter.can_handle("https:///broken/live.m3u8") is False
    assert adapter.can_handle("ftp://cdn.example.com/live.m3u8") is False


def test_unknown_url_returns_structured_error():
    info = parse_stream("https://example.com/not-a-live-room")

    assert info.platform == "unknown"
    assert info.is_live is False
    assert info.stream_url == ""
    assert info.error_code == ERROR_UNSUPPORTED_URL
    assert "不支持" in info.error


def test_platform_adapter_contract_uses_can_handle():
    class DemoAdapter(PlatformAdapter):
        platform = "demo"

        def can_handle(self, url: str) -> bool:
            return url.startswith("demo:")

        def parse(self, url: str) -> StreamInfo:
            return StreamInfo(
                platform="demo",
                room_url=url,
                stream_url="https://example.com/live.m3u8",
                is_live=True,
            )

    adapter = DemoAdapter()

    assert adapter.can_handle("demo:test") is True
    assert detect_platform("demo:test", adapters=[adapter]) == "demo"


def test_parse_stream_wraps_adapter_parse_exceptions_into_structured_error():
    class BrokenAdapter(PlatformAdapter):
        platform = "broken"

        def can_handle(self, url: str) -> bool:
            return url.startswith("broken:")

        def parse(self, url: str) -> StreamInfo:
            raise RuntimeError("boom")

    info = parse_stream("broken:test", adapters=[BrokenAdapter()])

    assert info.platform == "broken"
    assert info.room_url == "broken:test"
    assert info.is_live is False
    assert info.error_code == ERROR_PARSE_FAILED
    assert "boom" in info.error


def test_select_quality_uses_quality_candidates_then_fallback():
    info = StreamInfo(
        platform="direct",
        room_url="https://example.com/live",
        stream_url="https://example.com/origin.m3u8",
        is_live=True,
        quality_urls={
            "source": "https://example.com/source.m3u8",
            "origin": "https://example.com/origin.m3u8",
            "hd": "https://example.com/hd.m3u8",
            "sd": "https://example.com/sd.m3u8",
            "250": "https://example.com/250.m3u8",
            "150": "https://example.com/150.m3u8",
        },
        selected_quality="origin",
    )

    assert select_quality(info, "高清") == ("https://example.com/hd.m3u8", "hd")
    assert select_quality(info, "流畅") == ("https://example.com/sd.m3u8", "sd")
    assert select_quality(info, "原画") == ("https://example.com/origin.m3u8", "origin")


def test_select_quality_accepts_legacy_dict_and_new_candidate_keys():
    info = {
        "streamUrl": "https://example.com/fallback.m3u8",
        "selectedQuality": "source",
        "qualityUrls": {
            "source": "https://example.com/source.m3u8",
            "250": "https://example.com/250.m3u8",
            "150": "https://example.com/150.m3u8",
        },
    }

    assert select_quality(info, "原画") == ("https://example.com/source.m3u8", "source")
    assert select_quality(info, "高清") == ("https://example.com/250.m3u8", "250")
    assert select_quality(info, "流畅") == ("https://example.com/150.m3u8", "150")
