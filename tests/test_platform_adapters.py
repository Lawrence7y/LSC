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


def test_headers_to_ffmpeg_input_args_emits_user_agent_and_referer():
    args = headers_to_ffmpeg_input_args(
        {
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://www.huya.com/",
            "Origin": "https://www.huya.com",
        }
    )
    assert args[args.index("-user_agent") + 1] == "Mozilla/5.0"
    assert "-referer" not in args
    assert "-headers" in args
    blob = args[args.index("-headers") + 1]
    assert "Referer: https://www.huya.com/" in blob
    assert "Origin: https://www.huya.com" in blob


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


def test_direct_adapter_accepts_query_based_flv_and_m3u8_hints():
    flv_url = "https://cdn.example.com/live?id=123&type=flv"
    m3u8_url = "https://media.example.com/watch?format=m3u8&token=abc"

    flv_info = parse_stream(flv_url)
    m3u8_info = parse_stream(m3u8_url)

    assert DirectAdapter().can_handle(flv_url) is True
    assert DirectAdapter().can_handle(m3u8_url) is True
    assert flv_info.platform == "direct"
    assert flv_info.stream_url == flv_url
    assert m3u8_info.platform == "direct"
    assert m3u8_info.stream_url == m3u8_url


def test_direct_adapter_keeps_plain_web_pages_outside_direct_scope():
    adapter = DirectAdapter()

    assert adapter.can_handle("https://example.com/watch?id=123") is False
    assert adapter.can_handle("https://example.com/live?format=html") is False


def test_direct_adapter_rejects_missing_netloc_and_non_http_scheme():
    adapter = DirectAdapter()

    assert adapter.can_handle("https:///broken/live.m3u8") is False
    assert adapter.can_handle("ftp://cdn.example.com/live.m3u8") is False


def test_unknown_url_returns_structured_error():
    info = parse_stream("https://example.com/not-a-live-room")

    # Generic adapter now handles unknown URLs — it tries to fetch and extract streams
    assert info.platform == "generic"
    assert info.is_live is False
    assert info.stream_url == ""
    assert info.error_code in (ERROR_RESTRICTED, ERROR_PARSE_FAILED)


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
        def fetch_page(url, cookies=None):
            assert url == "https://live.douyin.com/123456"
            return "<html>fake</html>", None

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
    monkeypatch.setattr(adapter, "_get_douyin_cookies", lambda: {"ttwid": "x"})

    info = adapter.parse("https://live.douyin.com/123456")

    assert info.platform == "douyin"
    assert info.is_live is True
    assert info.title == "鏃犵晱濂戠害鐩存挱"
    assert info.streamer == "涓绘挱A"
    assert info.stream_url == "https://pull.example.com/live.m3u8"
    assert info.headers["Referer"] == "https://live.douyin.com/"
    assert info.headers["User-Agent"].startswith("Mozilla/5.0")


def test_douyin_context_parser_uses_scoped_cookie(monkeypatch):
    from lsc.platforms.credentials import CredentialContext, CredentialStatus
    from lsc.platforms.douyin import DouyinAdapter

    seen = []

    class FakeDouyinModule:
        @staticmethod
        def fetch_page(url, cookies=None):
            seen.append(dict(cookies or {}))
            return "<html>scoped</html>", None

        @staticmethod
        def extract_ssr_data(html):
            return {
                "isLive": True,
                "title": "上下文抖音直播",
                "streamerName": "上下文主播",
                "streamUrl": "https://pull.example.com/scoped.m3u8",
                "qualityUrls": {"origin": "https://pull.example.com/scoped.m3u8"},
            }

    adapter = DouyinAdapter()
    monkeypatch.setattr(adapter, "_load_script_module", lambda: FakeDouyinModule)
    monkeypatch.setattr(
        adapter,
        "_get_douyin_cookies",
        lambda: (_ for _ in ()).throw(AssertionError("legacy cookie lookup must not run")),
    )

    info = adapter.parse_with_context(
        "https://live.douyin.com/123456",
        CredentialContext(
            platform="douyin",
            status=CredentialStatus.AVAILABLE,
            headers={"Cookie": "ttwid=scoped; msToken=token"},
        ),
    )

    assert info.is_live is True
    assert seen == [{"ttwid": "scoped", "msToken": "token"}]
    assert info.headers["Cookie"] == "ttwid=scoped; msToken=token"
    assert info.headers["Referer"] == "https://live.douyin.com/"


def test_douyin_context_parser_scopes_proxy_and_timeout(monkeypatch):
    from lsc.platforms.credentials import CredentialContext, CredentialStatus
    from lsc.platforms.douyin import DouyinAdapter

    seen = {}

    class FakeDouyinModule:
        @staticmethod
        def fetch_page(url, cookies=None, *, proxy_url="", timeout_sec=None):
            seen.update(
                url=url,
                cookies=dict(cookies or {}),
                proxy_url=proxy_url,
                timeout_sec=timeout_sec,
            )
            return "<html>scoped</html>", None

        @staticmethod
        def extract_ssr_data(html):
            return {
                "isLive": True,
                "title": "代理上下文抖音直播",
                "streamerName": "代理上下文主播",
                "streamUrl": "https://pull.example.com/scoped.m3u8",
                "qualityUrls": {"origin": "https://pull.example.com/scoped.m3u8"},
            }

    adapter = DouyinAdapter()
    monkeypatch.setattr(adapter, "_load_script_module", lambda: FakeDouyinModule)
    info = adapter.parse_with_context(
        "https://live.douyin.com/123456",
        CredentialContext(
            platform="douyin",
            status=CredentialStatus.AVAILABLE,
            headers={"Cookie": "ttwid=scoped"},
            network_context={"proxy_url": "http://proxy.example:8080", "timeout_sec": 7},
        ),
    )

    assert info.is_live is True
    assert seen == {
        "url": "https://live.douyin.com/123456",
        "cookies": {"ttwid": "scoped"},
        "proxy_url": "http://proxy.example:8080",
        "timeout_sec": 7,
    }


def test_douyin_registry_detection():
    assert detect_platform("https://live.douyin.com/123456") == "douyin"


def test_douyin_adapter_returns_parse_failed_when_fetch_page_is_empty(monkeypatch):
    from lsc.platforms.base import ERROR_PARSE_FAILED
    from lsc.platforms.douyin import DouyinAdapter

    class FakeDouyinModule:
        @staticmethod
        def fetch_page(url, cookies=None):
            assert url == "https://live.douyin.com/123456"
            return None, "HTTP 403"

        @staticmethod
        def extract_ssr_data(html):
            raise AssertionError("extract_ssr_data should not be called")

    adapter = DouyinAdapter()
    monkeypatch.setattr(adapter, "_load_script_module", lambda: FakeDouyinModule)
    monkeypatch.setattr(adapter, "_get_douyin_cookies", lambda: {"ttwid": "x"})

    info = adapter.parse("https://live.douyin.com/123456")

    assert info.platform == "douyin"
    assert info.is_live is False
    assert info.error_code == ERROR_PARSE_FAILED
    assert "HTTP 403" in info.error
    assert info.headers["Referer"] == "https://live.douyin.com/"


def test_douyin_adapter_returns_offline_when_not_live_or_missing_stream(monkeypatch):
    from lsc.platforms.base import ERROR_OFFLINE
    from lsc.platforms.douyin import DouyinAdapter

    class FakeDouyinModule:
        @staticmethod
        def fetch_page(url, cookies=None):
            assert url == "https://live.douyin.com/123456"
            return "<html>fake</html>", None

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
    monkeypatch.setattr(adapter, "_get_douyin_cookies", lambda: {"ttwid": "x"})

    info = adapter.parse("https://live.douyin.com/123456")

    assert info.platform == "douyin"
    assert info.is_live is False
    assert info.error_code == ERROR_OFFLINE
    assert info.raw["isLive"] is False


def test_douyin_adapter_requires_cookies_instead_of_claiming_offline(monkeypatch):
    from lsc.platforms.base import ERROR_RESTRICTED
    from lsc.platforms.douyin import DouyinAdapter

    adapter = DouyinAdapter()
    monkeypatch.setattr(adapter, "_get_douyin_cookies", lambda: {})
    monkeypatch.setattr(
        adapter,
        "_load_script_module",
        lambda: (_ for _ in ()).throw(AssertionError("should not fetch without cookies")),
    )

    info = adapter.parse("https://live.douyin.com/123456")
    assert info.error_code == ERROR_RESTRICTED
    assert "Cookie" in info.error


def test_douyin_adapter_detects_verify_page(monkeypatch):
    from lsc.platforms.base import ERROR_RESTRICTED
    from lsc.platforms.douyin import DouyinAdapter

    class FakeDouyinModule:
        @staticmethod
        def fetch_page(url, cookies=None):
            return (
                "<html><head><title>验证码中间页</title>"
                "<script src='https://lf-cdn-tos.bytescm.com/obj/static/sec_sdk_build/3.5.2/captcha.js'></script>"
                "</head><body></body></html>",
                None,
            )

        @staticmethod
        def extract_ssr_data(html):
            raise AssertionError("should not parse captcha html")

    adapter = DouyinAdapter()
    monkeypatch.setattr(adapter, "_load_script_module", lambda: FakeDouyinModule)
    monkeypatch.setattr(adapter, "_get_douyin_cookies", lambda: {"ttwid": "stale"})

    info = adapter.parse("https://live.douyin.com/123456")
    assert info.error_code == ERROR_RESTRICTED
    assert "验证" in info.error or "Cookie" in info.error


def test_douyin_adapter_does_not_claim_non_live_douyin_pages():
    from lsc.platforms.douyin import DouyinAdapter

    adapter = DouyinAdapter()

    assert adapter.can_handle("https://www.douyin.com/video/123456") is False


def test_douyin_adapter_handles_follow_live_url():
    from lsc.platforms.douyin import DouyinAdapter

    adapter = DouyinAdapter()

    # 关注直播URL格式: www.douyin.com/follow/live/{room_id}
    assert adapter.can_handle("https://www.douyin.com/follow/live/4577510133") is True
    assert adapter.can_handle("https://www.douyin.com/follow/live/4577510133?anchor_id=4253376104366720") is True

    # 非数字房间ID应该不匹配
    assert adapter.can_handle("https://www.douyin.com/follow/live/abc") is False

    # 其他路径应该不匹配
    assert adapter.can_handle("https://www.douyin.com/follow/123") is False


def test_douyin_adapter_loads_real_script_module_from_repo():
    from lsc.platforms.douyin import DouyinAdapter

    module = DouyinAdapter()._load_script_module()

    assert callable(module.fetch_page)
    assert callable(module.extract_ssr_data)


def test_bilibili_adapter_parses_live_room_with_public_play_info(monkeypatch):
    from lsc.platforms.bilibili import BILIBILI_HEADERS, BilibiliAdapter

    adapter = BilibiliAdapter()
    room_init_payload = {
        "code": 0,
        "data": {
            "room_id": 12345,
            "live_status": 1,
            "title": "B 站直播标题",
            "uname": "主播A",
        },
    }
    play_info_payload = {
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
                                                },
                                                {
                                                    "host": "https://cn-gotcha205.example.com",
                                                    "extra": "?qn=10000&token=backup",
                                                },
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
    }

    def mock_fetch(url, params=None):
        if "room_init" in url:
            return room_init_payload
        elif "getRoomPlayInfo" in url:
            return play_info_payload
        elif "getInfoByRoom" in url:
            return {
                "code": 0,
                "data": {
                    "title": "B 站直播标题",
                    "uname": "主播A",
                    "area_v2_name": "无畏契约",
                    "parent_area_name": "单机游戏",
                }
            }
        return {"code": 0, "data": {}}

    monkeypatch.setattr(adapter, "_fetch_json", mock_fetch)
    # Ensure no cookies so quality selection is deterministic
    monkeypatch.setattr("lsc.platforms.bilibili._get_bilibili_cookies", lambda: {})

    info = adapter.parse("https://live.bilibili.com/12345")

    assert info.platform == "bilibili"
    assert info.title == "B 站直播标题"
    assert info.streamer == "主播A"
    # 无登录 Cookie 时，默认选择最低画质以避免 CDN 403
    assert info.selected_quality == "250"
    assert info.stream_url == "https://cn-gotcha204-2.example.com/live-bvc/master.m3u8?qn=250&token=abc"
    assert info.quality_urls == {
        "250": "https://cn-gotcha204-2.example.com/live-bvc/master.m3u8?qn=250&token=abc",
        "400": "https://cn-gotcha204-2.example.com/live-bvc/master.m3u8?qn=400&token=abc",
        "10000": "https://cn-gotcha204-2.example.com/live-bvc/master.m3u8?qn=10000&token=abc",
    }
    assert len(info.candidate_urls) == 6
    assert {item["cdn_id"] for item in info.candidate_urls} == {
        "cn-gotcha204-2",
        "cn-gotcha205",
    }
    assert info.headers == BILIBILI_HEADERS


def test_bilibili_parse_timeout_returns_failed_info_instead_of_raising(monkeypatch):
    from lsc.platforms.bilibili import BilibiliAdapter

    adapter = BilibiliAdapter()

    def boom(*_args, **_kwargs):
        raise TimeoutError("timed out")

    monkeypatch.setattr(adapter, "_fetch_json", boom)
    info = adapter.parse("https://live.bilibili.com/6?live_from=81001")

    assert info.platform == "bilibili"
    assert info.is_live is False
    assert info.error_code == ERROR_PARSE_FAILED
    assert "超时" in info.error or "timeout" in info.error.lower()


def test_bilibili_play_info_timeout_does_not_raise(monkeypatch):
    from lsc.platforms.bilibili import BilibiliAdapter

    adapter = BilibiliAdapter()

    def fake_fetch(url, params=None, headers=None, network_context=None):
        if "room_init" in url:
            return {"code": 0, "data": {"room_id": 7734200, "live_status": 1, "title": "T", "uname": "U"}}
        raise TimeoutError("timed out")

    monkeypatch.setattr(adapter, "_fetch_json", fake_fetch)
    info = adapter.parse("https://live.bilibili.com/6?live_from=81001")
    assert info.error_code == ERROR_PARSE_FAILED
    assert info.stream_url == ""


def test_bilibili_context_parser_uses_scoped_cookie_without_legacy_lookup(monkeypatch):
    from lsc.platforms.bilibili import BilibiliAdapter
    from lsc.platforms.credentials import CredentialContext, CredentialStatus

    adapter = BilibiliAdapter()
    seen_headers = []

    def fake_fetch_json(url, params=None, headers=None):
        seen_headers.append(dict(headers or {}))
        if "room_init" in url:
            return {
                "code": 0,
                "data": {
                    "room_id": 12345,
                    "live_status": 1,
                    "title": "上下文直播",
                    "uname": "上下文主播",
                },
            }
        return {
            "code": 0,
            "data": {
                "playurl_info": {
                    "playurl": {
                        "stream": [{
                            "format": [{
                                "codec": [{
                                    "accept_qn": [10000],
                                    "base_url": "/live/master.flv",
                                    "url_info": [{
                                        "host": "https://cdn.example.com",
                                        "extra": "?token=scoped",
                                    }],
                                }],
                            }],
                        }],
                    },
                },
            },
        }

    monkeypatch.setattr(adapter, "_fetch_json", fake_fetch_json)
    monkeypatch.setattr(
        "lsc.platforms.bilibili._get_bilibili_cookies",
        lambda: (_ for _ in ()).throw(AssertionError("legacy cookie lookup must not run")),
    )

    info = adapter.parse_with_context(
        "https://live.bilibili.com/12345",
        CredentialContext(
            platform="bilibili",
            purpose="RESOLVE",
            status=CredentialStatus.AVAILABLE,
            headers={"Cookie": "SESSDATA=scoped"},
        ),
    )

    assert info.is_live is True
    assert info.headers["Cookie"] == "SESSDATA=scoped"
    assert seen_headers and all(item["Cookie"] == "SESSDATA=scoped" for item in seen_headers)


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


def test_bilibili_short_link_is_classified_as_bilibili_and_returns_parse_failure():
    url = "https://b23.tv/abc123"

    info = parse_stream(url)

    assert detect_platform(url) == "bilibili"
    assert info.platform == "bilibili"
    assert info.is_live is False
    assert info.error_code in {ERROR_PARSE_FAILED, ERROR_RESTRICTED}
    # Expansion failure message mentions b23.tv or short link.
    assert "短链" in info.error or "b23.tv" in info.error


def test_bilibili_adapter_rejects_short_link_as_room_id_parse_failure():
    from lsc.platforms.bilibili import BilibiliAdapter

    adapter = BilibiliAdapter()
    info = adapter.parse("https://b23.tv/xyz987")

    assert adapter.can_handle("https://b23.tv/xyz987") is True
    assert info.platform == "bilibili"
    assert info.is_live is False
    assert info.error_code == ERROR_PARSE_FAILED
    assert "短链" in info.error or "b23.tv" in info.error


def test_bilibili_adapter_expands_short_link_to_real_url(monkeypatch):
    """When the short link expands successfully, parsing proceeds normally."""
    from lsc.platforms.bilibili import BilibiliAdapter

    adapter = BilibiliAdapter()

    def fake_expand(self, url):
        return "https://live.bilibili.com/12345"

    monkeypatch.setattr(BilibiliAdapter, "_expand_short_link", fake_expand)

    def fake_fetch_json(self, url, params=None):
        if "room_init" in url:
            return {"code": 0, "data": {"room_id": 12345, "title": "T", "uname": "U", "live_status": 1}}
        return {"code": 0, "data": {"playurl_info": {"playurl": {"stream": []}}}}

    monkeypatch.setattr(BilibiliAdapter, "_fetch_json", fake_fetch_json)

    info = adapter.parse("https://b23.tv/abc123")
    # Expansion succeeded but no stream URL found -> RESTRICTED, not PARSE_FAILED
    assert info.platform == "bilibili"
    assert info.error_code != ERROR_PARSE_FAILED or "短链" not in info.error


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
    monkeypatch.setattr(adapter, "_fetch_page", lambda url, **_kwargs: html)

    info = adapter.parse("https://www.huya.com/123")

    assert info.platform == "huya"
    assert info.is_live is True
    assert info.title == "虎牙直播标题"
    assert info.streamer == "虎牙主播"
    assert info.stream_url == "https://huya.example.com/live/room-123.flv?fm=abc&txyp=1"
    assert info.quality_urls == {
        "huya": "https://huya.example.com/live/room-123.flv?fm=abc&txyp=1",
        "source": "https://huya.example.com/live/room-123.flv?fm=abc&txyp=1",
    }
    assert info.selected_quality == "source"
    assert "Origin" not in info.headers
    assert "Range" not in info.headers
    assert info.headers["Referer"] == HUYA_HEADERS["Referer"]
    assert info.headers["User-Agent"] == HUYA_HEADERS["User-Agent"]


def test_huya_stream_headers_drop_origin_even_when_page_headers_include_it(monkeypatch):
    from types import SimpleNamespace

    from lsc.platforms.huya import HuyaAdapter

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
    monkeypatch.setattr(adapter, "_fetch_page", lambda url, **kwargs: html)
    context = SimpleNamespace(
        headers={
            "Referer": "https://www.huya.com/",
            "Origin": "https://www.huya.com",
            "User-Agent": "Mozilla/5.0",
        },
        network_profile="",
        network_context={},
    )

    info = adapter.parse_with_context("https://www.huya.com/123", context)

    assert "Origin" not in info.headers
    assert "Range" not in info.headers
    assert info.headers["Referer"] == "https://www.huya.com/"
    assert info.headers["User-Agent"] == "Mozilla/5.0"


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
    monkeypatch.setattr(adapter, "_fetch_page", lambda url, **_kwargs: html)

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
    monkeypatch.setattr(adapter, "_fetch_page", lambda url, **_kwargs: html)

    info = adapter.parse("https://www.huya.com/123")

    assert info.platform == "huya"
    assert info.is_live is False
    assert info.error_code == ERROR_RESTRICTED
    assert "公开流" in info.error


def test_huya_adapter_parses_hyplayer_config_modern_payload(monkeypatch):
    """现代 ``var hyPlayerConfig`` 路径（2025-2026 虎牙页面结构）。"""
    from lsc.platforms.huya import HUYA_HEADERS, HuyaAdapter

    adapter = HuyaAdapter()
    html = """
    <html>
      <script>
        var hyPlayerConfig = {
          "stream": {
            "data": [{
              "gameStreamInfoList": [{
                "sFlvUrl": "https://tx.example.com/live",
                "sStreamName": "room-456",
                "sFlvUrlSuffix": "flv",
                "sFlvAntiCode": "fm=xyz&txyp=1&wsTime=6d1a3c00"
              }],
              "gameLiveInfo": {
                "roomName": " hyPlayerConfig 标题",
                "nick": "hyPlayerConfig 主播",
                "gameFullName": "英雄联盟",
                "isSecret": 0
              }
            }]
          }
        };
      </script>
    </html>
    """
    monkeypatch.setattr(adapter, "_fetch_page", lambda url, **_kwargs: html)

    info = adapter.parse("https://www.huya.com/456")

    assert info.platform == "huya"
    assert info.is_live is True
    assert info.title == " hyPlayerConfig 标题"
    assert info.streamer == "hyPlayerConfig 主播"
    assert info.category == "英雄联盟"
    assert info.stream_url == "https://tx.example.com/live/room-456.flv?fm=xyz&txyp=1&wsTime=6d1a3c00"
    assert info.selected_quality == "source"
    assert "Origin" not in info.headers
    assert "Range" not in info.headers
    assert info.headers["Referer"] == HUYA_HEADERS["Referer"]


def test_build_huya_live_query_recomputes_sdk_secret(monkeypatch):
    """Page wsSecret is a web-player token; live FLV needs the SDK query."""
    import base64
    import hashlib
    from urllib.parse import parse_qs, quote

    from lsc.platforms import huya as huya_mod

    monkeypatch.setattr(huya_mod.random, "randint", lambda _a, _b: 12345678)
    monkeypatch.setattr(huya_mod.time, "time", lambda: 1700000000.0)

    fm = quote(base64.b64encode(b"PREFIX_0_1_2_3").decode())
    anti = (
        "wsSecret=deadbeefdeadbeefdeadbeefdeadbeef&wsTime=6a7d8ee5"
        f"&fm={fm}&ctype=huya_live&fs=bgct&txyp=o%3Ax"
    )
    query = huya_mod.build_huya_live_query(anti, "stream-name", ratio=0)
    qs = {key: values[0] for key, values in parse_qs(query).items()}

    uid = 12345678
    convert_uid = (uid << 8 | uid >> 24) & 0xFFFFFFFF
    timestamp = 1700000000000
    seqid = uid + timestamp
    secret_hash = hashlib.md5(f"{seqid}|huya_live|100".encode()).hexdigest()
    expected_secret = hashlib.md5(
        f"PREFIX_{convert_uid}_stream-name_{secret_hash}_6a7d8ee5".encode()
    ).hexdigest()

    assert "txyp" not in qs
    assert qs["wsSecret"] == expected_secret
    assert qs["wsSecret"] != "deadbeefdeadbeefdeadbeefdeadbeef"
    assert qs["seqid"] == str(seqid)
    assert qs["u"] == str(convert_uid)
    assert qs["sdk_sid"] == str(timestamp)
    assert qs["codec"] == "264"
    assert qs["t"] == "100"


def test_build_huya_live_query_keeps_raw_anti_code_when_fm_is_not_sdk_material():
    from lsc.platforms.huya import build_huya_live_query

    raw = "fm=abc&txyp=1"
    assert build_huya_live_query(raw, "room-123") == raw


def test_huya_parse_prefers_hs_cdn_and_rebuilds_sdk_query(monkeypatch):
    import base64
    from urllib.parse import parse_qs, quote, urlparse

    from lsc.platforms import huya as huya_mod

    monkeypatch.setattr(huya_mod.random, "randint", lambda _a, _b: 12345678)
    monkeypatch.setattr(huya_mod.time, "time", lambda: 1700000000.0)

    fm = quote(base64.b64encode(b"PREFIX_0_1_2_3").decode())
    anti = f"wsSecret=deadbeefdeadbeefdeadbeefdeadbeef&wsTime=6a7d8ee5&fm={fm}&ctype=huya_live&fs=bgct"
    adapter = huya_mod.HuyaAdapter()
    html = f"""
    <html>
      <script>
        window.HNF_GLOBAL_INIT = {{
          "roomInfo": {{"tLiveStatus": 1, "sIntroduction": "虎牙直播标题"}},
          "profileInfo": {{"nick": "虎牙主播"}},
          "stream": {{
            "data": [{{
              "gameStreamInfoList": [
                {{
                  "sFlvUrl": "https://tx.flv.huya.com/src",
                  "sStreamName": "stream-name",
                  "sFlvUrlSuffix": "flv",
                  "sFlvAntiCode": "{anti}"
                }},
                {{
                  "sFlvUrl": "https://hs.flv.huya.com/src",
                  "sStreamName": "stream-name",
                  "sFlvUrlSuffix": "flv",
                  "sFlvAntiCode": "{anti}"
                }}
              ]
            }}]
          }}
        }};
      </script>
    </html>
    """
    monkeypatch.setattr(adapter, "_fetch_page", lambda url, **_kwargs: html)

    info = adapter.parse("https://www.huya.com/123")
    parsed = urlparse(info.stream_url)
    qs = parse_qs(parsed.query)

    assert parsed.hostname == "hs.flv.huya.com"
    assert "seqid" in qs
    assert qs["wsSecret"][0] != "deadbeefdeadbeefdeadbeefdeadbeef"
    assert "hs" in info.quality_urls
    assert "tx" in info.quality_urls


_HUYA_ROOM_HTML = """
<html>
  <script>
    window.HNF_GLOBAL_INIT = {
      "roomInfo": {"tLiveStatus": 1, "sIntroduction": "LPL 正式房间"},
      "profileInfo": {"nick": "LPL"},
      "stream": {
        "data": [{
          "gameStreamInfoList": [{
            "sFlvUrl": "https://hs.flv.huya.com/src",
            "sStreamName": "lpl-room",
            "sFlvUrlSuffix": "flv",
            "sFlvAntiCode": "fm=abc&txyp=1"
          }]
        }]
      }
    };
  </script>
</html>
"""


def test_huya_match_page_follows_profile_room_when_player_config_missing(monkeypatch):
    from lsc.platforms.huya import HuyaAdapter

    adapter = HuyaAdapter()
    match_html = """
    <link rel="canonical" href="https://www.huya.com/660000"/>
    <script>TT_ROOM_DATA = {"type":"MATCH","profileRoom":660000,"privateHost":"lpl"};</script>
    """
    seen: list[str] = []

    def fake_fetch(url, **_kwargs):
        path = url.rstrip("/").rsplit("/", 1)[-1]
        seen.append(path)
        if path == "lpl":
            return match_html
        return _HUYA_ROOM_HTML

    monkeypatch.setattr(adapter, "_fetch_page", fake_fetch)

    info = adapter.parse("https://www.huya.com/lpl")

    assert seen == ["lpl", "660000"]
    assert info.is_live is True
    assert not info.error
    assert info.stream_url.endswith("lpl-room.flv?fm=abc&txyp=1")
    assert info.streamer == "LPL"


def test_huya_numeric_room_does_not_refetch_itself_on_parse_failure(monkeypatch):
    from lsc.platforms.huya import HuyaAdapter

    adapter = HuyaAdapter()
    seen: list[str] = []

    def fake_fetch(url, **_kwargs):
        seen.append(url.rstrip("/").rsplit("/", 1)[-1])
        return "<html><title>empty</title></html>"

    monkeypatch.setattr(adapter, "_fetch_page", fake_fetch)

    info = adapter.parse("https://www.huya.com/660000")

    assert seen == ["660000"]
    assert info.error_code == "parse_failed"


def test_huya_parse_with_context_fetches_page_with_origin(monkeypatch):
    from types import SimpleNamespace

    from lsc.platforms.huya import HUYA_PAGE_HEADERS, HuyaAdapter

    adapter = HuyaAdapter()
    seen_headers: dict[str, str] = {}

    def fake_fetch(url, **kwargs):
        seen_headers.update(kwargs.get("headers") or {})
        return _HUYA_ROOM_HTML

    monkeypatch.setattr(adapter, "_fetch_page", fake_fetch)
    context = SimpleNamespace(headers={}, network_profile="", network_context={})

    info = adapter.parse_with_context("https://www.huya.com/660000", context)

    assert seen_headers.get("Origin") == HUYA_PAGE_HEADERS["Origin"]
    assert info.is_live is True
    assert "Origin" not in info.headers


def test_huya_context_parser_uses_scoped_cookie_without_legacy_lookup(monkeypatch):
    from lsc.platforms.credentials import CredentialContext, CredentialStatus
    from lsc.platforms.huya import HuyaAdapter

    adapter = HuyaAdapter()
    seen_headers: dict[str, str] = {}

    def fake_fetch(url, **kwargs):
        seen_headers.update(kwargs.get("headers") or {})
        return _HUYA_ROOM_HTML

    monkeypatch.setattr(adapter, "_fetch_page", fake_fetch)
    monkeypatch.setattr(
        "lsc.platforms.huya._get_huya_cookies",
        lambda: (_ for _ in ()).throw(AssertionError("legacy cookie lookup must not run")),
    )

    info = adapter.parse_with_context(
        "https://www.huya.com/660000",
        CredentialContext(
            platform="huya",
            purpose="RESOLVE",
            status=CredentialStatus.AVAILABLE,
            headers={"Cookie": "udb_uid=scoped"},
        ),
    )

    assert info.is_live is True
    assert seen_headers.get("Cookie") == "udb_uid=scoped"
    assert "Cookie" not in info.headers
    assert "Origin" not in info.headers


def test_huya_adapter_hyplayer_config_defaults_to_live_when_isSecret_missing():
    """当 hyPlayerConfig 路径中 isSecret 字段缺失时，默认视为已开播。

    修复前：isSecret 缺失时 ``None == 0`` 为 False，导致 tLiveStatus=0（误判未开播）。
    修复后：room_info 缺失 tLiveStatus 时默认视为已开播（stream 数据存在即有直播内容）。
    """
    from lsc.platforms.huya import HuyaAdapter

    adapter = HuyaAdapter()
    html = """
    <html>
      <script>
        var hyPlayerConfig = {
          "stream": {
            "data": [{
              "gameStreamInfoList": [{
                "sFlvUrl": "https://hs.example.com/live",
                "sStreamName": "room-789",
                "sFlvUrlSuffix": "flv",
                "sFlvAntiCode": "fm=def"
              }],
              "gameLiveInfo": {
                "roomName": "无 isSecret 字段",
                "nick": "主播B"
              }
            }]
          }
        };
      </script>
    </html>
    """
    adapter._fetch_page = lambda url, **_kwargs: html

    info = adapter.parse("https://www.huya.com/789")

    assert info.platform == "huya"
    assert info.is_live is True
    assert info.title == "无 isSecret 字段"
    assert info.streamer == "主播B"


def test_huya_adapter_falls_back_to_html_title_when_stream_data_missing(monkeypatch):
    """当 stream 数据缺失时，从 HTML <title> 标签提取标题和主播名。"""
    from lsc.platforms.huya import HuyaAdapter

    adapter = HuyaAdapter()
    html = """
    <html>
      <head><title>主播名 - 房间标题-虎牙直播</title></head>
      <script>
        window.HNF_GLOBAL_INIT = {
          "roomInfo": {"tLiveStatus": 1, "sIntroduction": ""},
          "profileInfo": {"nick": ""},
          "stream": {
            "data": [{
              "gameStreamInfoList": [{
                "sFlvUrl": "https://huya.example.com/live",
                "sStreamName": "room-111",
                "sFlvUrlSuffix": "flv",
                "sFlvAntiCode": "fm=ghi"
              }]
            }]
          }
        };
      </script>
    </html>
    """
    monkeypatch.setattr(adapter, "_fetch_page", lambda url, **_kwargs: html)

    info = adapter.parse("https://www.huya.com/111")

    assert info.platform == "huya"
    assert info.is_live is True
    assert info.title == "房间标题"
    assert info.streamer == "主播名"


def test_huya_registry_detection():
    assert detect_platform("https://www.huya.com/123") == "huya"


def test_kuaishou_registry_detection():
    assert detect_platform("https://live.kuaishou.com/u/someuser") == "kuaishou"
    assert detect_platform("https://live.kuaishou.com/w/12345") == "kuaishou"


def test_kuaishou_adapter_returns_offline_when_not_living(monkeypatch):
    from lsc.platforms.kuaishou import KUAISHOU_HEADERS, KuaishouAdapter

    adapter = KuaishouAdapter()
    html = """
    <script>
      window.__INITIAL_STATE__ = {
        "liveroom": {
          "playList": [{
            "isLiving": false,
            "author": {"name": "快手主播"},
            "liveStream": {"playUrls": {"h264": {}, "hevc": {}}}
          }]
        }
      };
    </script>
    """
    monkeypatch.setattr(adapter, "_fetch_page", lambda url, **_kwargs: html)

    info = adapter.parse("https://live.kuaishou.com/u/offline")

    assert info.platform == "kuaishou"
    assert info.is_live is False
    assert info.error_code == ERROR_OFFLINE
    assert "未开播" in info.error
    assert info.headers == KUAISHOU_HEADERS


def test_kuaishou_adapter_maps_forbidden_state_to_restricted(monkeypatch):
    from lsc.platforms.base import ERROR_RESTRICTED
    from lsc.platforms.kuaishou import KuaishouAdapter

    adapter = KuaishouAdapter()
    html = """
    <script>
      window.__INITIAL_STATE__ = {
        "liveroom": {"playList": [{
          "isLiving": true,
          "status": {"forbiddenState": 1},
          "author": {"name": "受限主播"},
          "liveStream": {"playUrls": {}}
        }]}
      };
    </script>
    """
    monkeypatch.setattr(adapter, "_fetch_page", lambda url, **_kwargs: html)

    info = adapter.parse("https://live.kuaishou.com/u/restricted")

    assert info.error_code == ERROR_RESTRICTED
    assert info.is_live is False


def test_kuaishou_adapter_extracts_stream_urls_when_live(monkeypatch):
    from lsc.platforms.kuaishou import KuaishouAdapter

    adapter = KuaishouAdapter()
    html = """
    <script>
      window.__INITIAL_STATE__ = {
        "liveroom": {
          "playList": [{
            "isLiving": true,
            "author": {"name": "快手主播", "description": "直播标题"},
            "liveStream": {
              "playUrls": {
                "h264": {
                  "adaptationSet": [{
                    "representation": [
                      {"url": "https://ks.example.com/live_hd.m3u8", "height": 1080, "bitrate": 3000, "name": "原画"},
                      {"url": "https://ks.example.com/live_sd.m3u8", "height": 720, "bitrate": 1500, "name": "高清"}
                    ]
                  }]
                }
              }
            }
          }]
        }
      };
    </script>
    """
    monkeypatch.setattr(adapter, "_fetch_page", lambda url, **_kwargs: html)

    info = adapter.parse("https://live.kuaishou.com/u/liveuser")

    assert info.platform == "kuaishou"
    assert info.is_live is True
    assert info.streamer == "快手主播"
    assert info.title == "直播标题"
    assert info.stream_url == "https://ks.example.com/live_hd.m3u8"
    assert info.quality_urls == {
        "原画": "https://ks.example.com/live_hd.m3u8",
        "高清": "https://ks.example.com/live_sd.m3u8",
    }
    assert info.selected_quality == "原画"


def test_kuaishou_adapter_handles_undefined_literals(monkeypatch):
    from lsc.platforms.kuaishou import KuaishouAdapter

    adapter = KuaishouAdapter()
    html = """
    <script>
      window.__INITIAL_STATE__ = {
        "liveroom": {
          "playList": [{
            "isLiving": true,
            "author": {"name": "主播"},
            "authToken": undefined,
            "liveStream": {
              "playUrls": {
                "h264": {
                  "adaptationSet": [{
                    "representation": [
                      {"url": "https://ks.example.com/live.m3u8", "height": 1080}
                    ]
                  }]
                }
              }
            }
          }]
        }
      };
    </script>
    """
    monkeypatch.setattr(adapter, "_fetch_page", lambda url, **_kwargs: html)

    info = adapter.parse("https://live.kuaishou.com/u/abc")

    assert info.is_live is True
    assert info.stream_url == "https://ks.example.com/live.m3u8"


def test_douyu_adapter_maps_live_page_to_stream_candidate(monkeypatch):
    from lsc.platforms.douyu import DouyuAdapter

    monkeypatch.setattr(
        "lsc.platforms.douyu.fetch_url",
        lambda *_args, **_kwargs: (
            '<title>斗鱼直播</title>'
            '"status":1,"room_name":"测试房间","nickname":"主播",'
            '"hls_url":"https://dy.example/live.m3u8"'
        ),
    )
    info = DouyuAdapter().parse("https://www.douyu.com/12345")
    assert info.is_live is True
    assert info.stream_url == "https://dy.example/live.m3u8"
    assert info.quality_urls == {"source": "https://dy.example/live.m3u8"}
    assert info.streamer == "主播"


def test_douyu_adapter_maps_missing_live_status_to_offline(monkeypatch):
    from lsc.platforms.douyu import DouyuAdapter

    monkeypatch.setattr(
        "lsc.platforms.douyu.fetch_url",
        lambda *_args, **_kwargs: '<title>斗鱼直播间</title>"status":0',
    )
    info = DouyuAdapter().parse("https://www.douyu.com/12345")
    assert info.is_live is False
    assert info.error_code == ERROR_OFFLINE


def test_douyu_escaped_show_status_is_treated_as_live(monkeypatch):
    from lsc.platforms.douyu import DouyuAdapter

    monkeypatch.setattr(
        "lsc.platforms.douyu.fetch_url",
        lambda *_args, **_kwargs: (
            '<title>斗鱼直播</title>'
            r'{\"cate2_id\":6,\"show_status\":1,\"owner_uid\":8745592}'
            '"room_name":"G2 vs BIG","nickname":"QuQu",'
            '"hls_url":"https://dy.example/live.m3u8"'
        ),
    )
    info = DouyuAdapter().parse("https://www.douyu.com/5232?dyshid=abc")
    assert info.is_live is True
    assert info.error_code == ""
    assert info.stream_url == "https://dy.example/live.m3u8"
    assert info.streamer == "QuQu"


def test_douyu_room_info_api_live_uses_real_rid_and_preview(monkeypatch):
    import json

    from lsc.platforms.douyu import DouyuAdapter

    def fake_fetch(url, **_kwargs):
        if "api/room/info" in url:
            return json.dumps({
                "code": 0,
                "data": {
                    "roomInfo": {
                        "rid": 178432,
                        "vipId": 5232,
                        "isLive": 1,
                        "roomName": "G2 vs BIG",
                        "nickname": "QuQu",
                        "cate2Name": "CS2",
                    }
                },
            })
        return "<title>斗鱼直播</title>"

    monkeypatch.setattr("lsc.platforms.douyu.fetch_url", fake_fetch)
    monkeypatch.setattr(
        DouyuAdapter,
        "_fetch_preview_stream",
        lambda *_args, **_kwargs: "https://dy.example/178432.m3u8",
        raising=False,
    )
    info = DouyuAdapter().parse("https://www.douyu.com/5232")
    assert info.is_live is True
    assert info.stream_url == "https://dy.example/178432.m3u8"
    assert info.title == "G2 vs BIG"
    assert info.streamer == "QuQu"
    assert info.category == "CS2"


def test_douyu_preview_stream_prefers_signed_flv_over_hls(monkeypatch):
    from urllib.parse import urlsplit

    from lsc.platforms.douyu import DouyuAdapter

    adapter = DouyuAdapter()
    monkeypatch.setattr(
        adapter,
        "_post_form",
        lambda *_args, **_kwargs: {
            "error": 0,
            "data": {
                "rtmp_url": "https://hlshw3a.example/live",
                "rtmp_live": "178432abc.m3u8?token=preview",
            },
        },
    )
    url = adapter._fetch_preview_stream("178432")
    parsed = urlsplit(url)
    assert parsed.path.endswith(".flv")
    assert parsed.path.endswith("178432abc.flv")
    assert "token=preview" in parsed.query


def test_douyu_room_info_api_not_live_is_offline(monkeypatch):
    import json

    from lsc.platforms.douyu import DouyuAdapter

    monkeypatch.setattr(
        "lsc.platforms.douyu.fetch_url",
        lambda url, **_kwargs: json.dumps({
            "code": 0,
            "data": {
                "roomInfo": {
                    "rid": 12345,
                    "isLive": 0,
                    "roomName": "未开播房间",
                    "nickname": "主播",
                }
            },
        }) if "api/room/info" in url else "<title>斗鱼直播间</title>",
    )
    info = DouyuAdapter().parse("https://www.douyu.com/12345")
    assert info.is_live is False
    assert info.error_code == ERROR_OFFLINE
    assert "未开播" in info.error


def test_xiaohongshu_adapter_maps_live_page_to_stream_candidate(monkeypatch):
    from lsc.platforms.xiaohongshu import XiaohongshuAdapter

    monkeypatch.setattr(
        "lsc.platforms.xiaohongshu.fetch_url",
        lambda *_args, **_kwargs: (
            '"status":"LIVING","title":"小红书直播","nickname":"小红书主播",'
            '"streamUrl":"https://xhs.example/live.flv"'
        ),
    )
    info = XiaohongshuAdapter().parse("https://www.xiaohongshu.com/live/room-1")
    assert info.is_live is True
    assert info.stream_url == "https://xhs.example/live.flv"
    assert info.selected_quality == "source"


def test_xiaohongshu_adapter_maps_non_living_page_to_offline(monkeypatch):
    from lsc.platforms.xiaohongshu import XiaohongshuAdapter

    monkeypatch.setattr(
        "lsc.platforms.xiaohongshu.fetch_url",
        lambda *_args, **_kwargs: '"status":"ENDED"',
    )
    info = XiaohongshuAdapter().parse("https://www.xiaohongshu.com/live/room-1")
    assert info.is_live is False
    assert info.error_code == ERROR_OFFLINE


def test_xiaohongshu_adapter_accepts_livestream_path():
    from lsc.platforms.xiaohongshu import XiaohongshuAdapter

    adapter = XiaohongshuAdapter()
    assert adapter.can_handle(
        "https://www.xiaohongshu.com/livestream/570401884760732992?xsec_token=token"
    )


def test_weibo_adapter_maps_initial_state_to_stream_candidate(monkeypatch):
    from lsc.platforms.weibo import WeiboAdapter

    adapter = WeiboAdapter()
    monkeypatch.setattr(
        adapter,
        "_fetch_page",
        lambda _url: (
            'window.__INITIAL_STATE__ = '
            '{"stream_url":"https://weibo.example/live.m3u8",'
            '"is_live":true,"title":"微博直播","nickname":"微博主播"};'
        ),
    )
    info = adapter.parse("https://live.weibo.com/l/wblive/room-1")
    assert info.is_live is True
    assert info.stream_url == "https://weibo.example/live.m3u8"
    assert info.title == "微博直播"
    assert info.streamer == "微博主播"


def test_weibo_adapter_maps_empty_initial_state_to_offline(monkeypatch):
    from lsc.platforms.weibo import WeiboAdapter

    adapter = WeiboAdapter()
    monkeypatch.setattr(
        adapter,
        "_fetch_page",
        lambda _url: 'window.__INITIAL_STATE__ = {"is_live":false};',
    )
    info = adapter.parse("https://live.weibo.com/l/wblive/room-1")
    assert info.is_live is False
    assert info.error_code == ERROR_OFFLINE


def test_weibo_mobile_host_is_pre_routed_before_adapter_scan():
    assert detect_platform("https://m.weibo.cn/l/wblive/room-1") == "weibo"


def test_parse_stream_falls_back_when_routed_adapter_rejects_path():
    class RoutedAdapter:
        platform = "bilibili"

        def can_handle(self, url: str) -> bool:
            return False

        def parse(self, url: str) -> StreamInfo:
            raise AssertionError("routed adapter rejected this path")

    class FallbackAdapter:
        platform = "generic"

        def can_handle(self, url: str) -> bool:
            return True

        def parse(self, url: str) -> StreamInfo:
            return StreamInfo(
                platform="generic",
                room_url=url,
                stream_url="https://cdn.example/live.flv",
                is_live=True,
            )

    info = parse_stream(
        "https://www.bilibili.com/123",
        adapters=(RoutedAdapter(), FallbackAdapter()),
    )
    assert info.platform == "generic"
    assert info.stream_url == "https://cdn.example/live.flv"


def test_common_non_douyin_share_urls_are_recognized():
    from lsc.platforms.bilibili import BilibiliAdapter
    from lsc.platforms.douyu import DouyuAdapter
    from lsc.platforms.huya import HuyaAdapter
    from lsc.platforms.kuaishou import KuaishouAdapter
    from lsc.platforms.weibo import WeiboAdapter
    from lsc.platforms.xiaohongshu import XiaohongshuAdapter

    assert BilibiliAdapter().can_handle("https://live.bilibili.com/h5/12345")
    assert BilibiliAdapter().can_handle("https://live.bilibili.com/blanc/12345")
    assert BilibiliAdapter().can_handle("https://live.bilibili.com/6?live_from=81001")
    assert BilibiliAdapter()._extract_room_id("https://live.bilibili.com/6?live_from=81001") == "6"
    assert HuyaAdapter().can_handle("https://m.huya.com/lpl")
    assert DouyuAdapter().can_handle("https://www.douyu.com/topic/event?rid=5720533")
    assert DouyuAdapter().can_handle("https://m.douyu.com/5720533")
    assert KuaishouAdapter().can_handle("https://www.kuaishou.com/u/someuser")
    assert KuaishouAdapter().can_handle("https://v.kuaishou.com/abc123")
    assert XiaohongshuAdapter().can_handle("https://xhslink.com/o/AbCdEf")
    assert WeiboAdapter().can_handle("https://live.weibo.com/show?id=123")
    assert detect_platform("https://xhslink.com/o/AbCdEf") == "xiaohongshu"
    assert detect_platform("https://m.huya.com/lpl") == "huya"
    assert detect_platform("https://live.weibo.com/show?id=123") == "weibo"


def test_douyu_topic_url_uses_rid_query(monkeypatch):
    from lsc.platforms.douyu import DouyuAdapter

    seen = []

    def fake_fetch(url, **_kwargs):
        seen.append(url)
        return (
            '<title>斗鱼直播</title>'
            '"status":1,"room_name":"活动房","nickname":"主播",'
            '"hls_url":"https://dy.example/live.m3u8"'
        )

    monkeypatch.setattr("lsc.platforms.douyu.fetch_url", fake_fetch)
    info = DouyuAdapter().parse("https://www.douyu.com/topic/event?rid=5720533")
    assert info.is_live is True
    assert info.stream_url == "https://dy.example/live.m3u8"
    assert any("5720533" in url for url in seen)


def test_xiaohongshu_expands_short_link_before_parsing_live_page(monkeypatch):
    from lsc.platforms.xiaohongshu import XiaohongshuAdapter

    adapter = XiaohongshuAdapter()
    monkeypatch.setattr(
        adapter,
        "_expand_short_link",
        lambda _url, **_kwargs: "https://www.xiaohongshu.com/livestream/room-1",
    )
    monkeypatch.setattr(
        "lsc.platforms.xiaohongshu.fetch_url",
        lambda *_args, **_kwargs: (
            '"status":"LIVING","title":"小红书直播","nickname":"小红书主播",'
            '"streamUrl":"https://xhs.example/live.flv"'
        ),
    )
    info = adapter.parse("https://xhslink.com/o/AbCdEf")
    assert info.is_live is True
    assert info.stream_url == "https://xhs.example/live.flv"


def test_bilibili_h5_and_blanc_paths_extract_room_id():
    from lsc.platforms.bilibili import BilibiliAdapter

    adapter = BilibiliAdapter()
    assert adapter._extract_room_id("https://live.bilibili.com/h5/12345") == "12345"
    assert adapter._extract_room_id("https://live.bilibili.com/blanc/67890") == "67890"
