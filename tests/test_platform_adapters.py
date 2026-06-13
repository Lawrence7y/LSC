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

def test_douyin_adapter_wraps_existing_parser(monkeypatch):
    from lsc.platforms.douyin import DouyinAdapter

    class FakeDouyinModule:
        @staticmethod
        def fetch_page(url):
            assert url == "https://live.douyin.com/123456"
            return "<html>fake</html>"

        @staticmethod
        def extract_ssr_data(html):
            assert html == "<html>fake</html>"
            return {
                "platform": "douyin",
                "isLive": True,
                "title": "鏃犵晱濂戠害鐩存挱",
                "streamerName": "涓绘挱A",
                "streamUrl": "https://pull.example.com/live.m3u8",
                "selectedQuality": "origin",
                "qualityUrls": {"origin": "https://pull.example.com/live.m3u8"},
            }

    adapter = DouyinAdapter()
    monkeypatch.setattr(adapter, "_load_script_module", lambda: FakeDouyinModule)

    info = adapter.parse("https://live.douyin.com/123456")

    assert info.platform == "douyin"
    assert info.is_live is True
    assert info.title == "鏃犵晱濂戠害鐩存挱"
    assert info.streamer == "涓绘挱A"
    assert info.stream_url == "https://pull.example.com/live.m3u8"
    assert info.headers["Referer"] == "https://live.douyin.com/"
    assert info.headers["User-Agent"].startswith("Mozilla/5.0")


def test_douyin_registry_detection():
    assert detect_platform("https://live.douyin.com/123456") == "douyin"


def test_douyin_adapter_returns_parse_failed_when_fetch_page_is_empty(monkeypatch):
    from lsc.platforms.base import ERROR_PARSE_FAILED
    from lsc.platforms.douyin import DouyinAdapter

    class FakeDouyinModule:
        @staticmethod
        def fetch_page(url):
            assert url == "https://live.douyin.com/123456"
            return ""

        @staticmethod
        def extract_ssr_data(html):
            raise AssertionError("extract_ssr_data should not be called")

    adapter = DouyinAdapter()
    monkeypatch.setattr(adapter, "_load_script_module", lambda: FakeDouyinModule)

    info = adapter.parse("https://live.douyin.com/123456")

    assert info.platform == "douyin"
    assert info.is_live is False
    assert info.error_code == ERROR_PARSE_FAILED
    assert info.headers["Referer"] == "https://live.douyin.com/"


def test_douyin_adapter_returns_offline_when_not_live_or_missing_stream(monkeypatch):
    from lsc.platforms.base import ERROR_OFFLINE
    from lsc.platforms.douyin import DouyinAdapter

    class FakeDouyinModule:
        @staticmethod
        def fetch_page(url):
            assert url == "https://live.douyin.com/123456"
            return "<html>fake</html>"

        @staticmethod
        def extract_ssr_data(html):
            assert html == "<html>fake</html>"
            return {
                "isLive": False,
                "title": "offline room",
                "streamerName": "host",
                "streamUrl": "",
            }

    adapter = DouyinAdapter()
    monkeypatch.setattr(adapter, "_load_script_module", lambda: FakeDouyinModule)

    info = adapter.parse("https://live.douyin.com/123456")

    assert info.platform == "douyin"
    assert info.is_live is False
    assert info.error_code == ERROR_OFFLINE
    assert info.raw["isLive"] is False


def test_douyin_adapter_does_not_claim_non_live_douyin_pages():
    from lsc.platforms.douyin import DouyinAdapter

    adapter = DouyinAdapter()

    assert adapter.can_handle("https://www.douyin.com/video/123456") is False


def test_bilibili_adapter_parses_live_room_with_public_play_info(monkeypatch):
    from lsc.platforms.bilibili import BILIBILI_HEADERS, BilibiliAdapter

    adapter = BilibiliAdapter()
    responses = iter(
        [
            {
                "code": 0,
                "data": {
                    "room_id": 12345,
                    "live_status": 1,
                    "title": "B 站直播标题",
                    "uname": "主播A",
                },
            },
            {
                "code": 0,
                "data": {
                    "playurl_info": {
                        "playurl": {
                            "stream": [
                                {
                                    "format": [
                                        {
                                            "codec": [
                                                {
                                                    "accept_qn": [10000, 400, 250],
                                                    "base_url": "/live-bvc/master.m3u8",
                                                    "url_info": [
                                                        {
                                                            "host": "https://cn-gotcha204-2.example.com",
                                                            "extra": "?qn=10000&token=abc",
                                                        }
                                                    ],
                                                }
                                            ]
                                        }
                                    ]
                                }
                            ]
                        }
                    }
                },
            },
        ]
    )

    monkeypatch.setattr(adapter, "_fetch_json", lambda url, params=None: next(responses))

    info = adapter.parse("https://live.bilibili.com/12345")

    assert info.platform == "bilibili"
    assert info.title == "B 站直播标题"
    assert info.streamer == "主播A"
    assert info.stream_url == "https://cn-gotcha204-2.example.com/live-bvc/master.m3u8?qn=10000&token=abc"
    assert info.quality_urls == {
        "10000": "https://cn-gotcha204-2.example.com/live-bvc/master.m3u8?qn=10000&token=abc",
        "400": "https://cn-gotcha204-2.example.com/live-bvc/master.m3u8?qn=400&token=abc",
        "250": "https://cn-gotcha204-2.example.com/live-bvc/master.m3u8?qn=250&token=abc",
    }
    assert info.headers == BILIBILI_HEADERS


def test_bilibili_adapter_returns_offline_when_room_is_not_live(monkeypatch):
    from lsc.platforms.bilibili import BilibiliAdapter

    adapter = BilibiliAdapter()
    monkeypatch.setattr(
        adapter,
        "_fetch_json",
        lambda url, params=None: {
            "code": 0,
            "data": {
                "room_id": 12345,
                "live_status": 0,
                "title": "未开播房间",
                "uname": "主播A",
            },
        },
    )

    info = adapter.parse("https://live.bilibili.com/12345")

    assert info.platform == "bilibili"
    assert info.is_live is False
    assert info.error_code == ERROR_OFFLINE


def test_bilibili_registry_detection():
    assert detect_platform("https://live.bilibili.com/12345") == "bilibili"


def test_huya_adapter_parses_public_page_payload(monkeypatch):
    from lsc.platforms.huya import HUYA_HEADERS, HuyaAdapter

    adapter = HuyaAdapter()
    html = """
    <html>
      <script>
        window.HNF_GLOBAL_INIT = {
          "roomInfo": {"tLiveStatus": 1, "sIntroduction": "虎牙直播标题"},
          "profileInfo": {"nick": "虎牙主播"},
          "stream": {
            "data": [{
              "gameStreamInfoList": [{
                "sFlvUrl": "https://huya.example.com/live",
                "sStreamName": "room-123",
                "sFlvUrlSuffix": "flv",
                "sFlvAntiCode": "fm=abc&txyp=1"
              }]
            }]
          }
        };
      </script>
    </html>
    """
    monkeypatch.setattr(adapter, "_fetch_page", lambda url: html)

    info = adapter.parse("https://www.huya.com/123")

    assert info.platform == "huya"
    assert info.is_live is True
    assert info.title == "虎牙直播标题"
    assert info.streamer == "虎牙主播"
    assert info.stream_url == "https://huya.example.com/live/room-123.flv?fm=abc&txyp=1"
    assert info.quality_urls == {"source": "https://huya.example.com/live/room-123.flv?fm=abc&txyp=1"}
    assert info.selected_quality == "source"
    assert info.headers == HUYA_HEADERS


def test_huya_adapter_returns_offline_when_room_not_live(monkeypatch):
    from lsc.platforms.huya import HuyaAdapter

    adapter = HuyaAdapter()
    html = """
    <script>
      window.HNF_GLOBAL_INIT = {
        "roomInfo": {"tLiveStatus": 0, "sIntroduction": "未开播房间"},
        "profileInfo": {"nick": "虎牙主播"}
      };
    </script>
    """
    monkeypatch.setattr(adapter, "_fetch_page", lambda url: html)

    info = adapter.parse("https://www.huya.com/123")

    assert info.platform == "huya"
    assert info.is_live is False
    assert info.error_code == ERROR_OFFLINE
    assert "未开播" in info.error
    assert info.headers["Referer"] == "https://www.huya.com/"


def test_huya_adapter_returns_restricted_when_no_public_stream_found(monkeypatch):
    from lsc.platforms.huya import HuyaAdapter

    adapter = HuyaAdapter()
    html = """
    <script>
      window.HNF_GLOBAL_INIT = {
        "roomInfo": {"tLiveStatus": 1, "sIntroduction": "限制房间"},
        "profileInfo": {"nick": "虎牙主播"},
        "stream": {"data": []}
      };
    </script>
    """
    monkeypatch.setattr(adapter, "_fetch_page", lambda url: html)

    info = adapter.parse("https://www.huya.com/123")

    assert info.platform == "huya"
    assert info.is_live is False
    assert info.error_code == ERROR_RESTRICTED
    assert "公开流" in info.error


def test_huya_registry_detection():
    assert detect_platform("https://www.huya.com/123") == "huya"
