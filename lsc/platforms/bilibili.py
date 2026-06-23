"""Adapter for Bilibili live room URLs."""
from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from .base import (
    ERROR_OFFLINE,
    ERROR_PARSE_FAILED,
    ERROR_RESTRICTED,
    BasePlatformAdapter,
    DEFAULT_USER_AGENT,
    StreamInfo,
    fetch_head,
    fetch_json,
)

_log = logging.getLogger(__name__)

ROOM_INIT_URL = "https://api.live.bilibili.com/room/v1/Room/room_init"
PLAY_INFO_URL = "https://api.live.bilibili.com/xlive/web-room/v2/index/getRoomPlayInfo"
BILIBILI_HEADERS = {
    "Referer": "https://live.bilibili.com/",
    "User-Agent": DEFAULT_USER_AGENT,
}


def _get_bilibili_cookies() -> dict[str, str]:
    """获取B站cookies用于认证。"""
    try:
        from .cookie_helper import get_bilibili_cookies
        return get_bilibili_cookies()
    except Exception as e:
        _log.debug("获取B站cookies失败: %s", e)
        return {}


def _build_headers_with_cookies() -> dict[str, str]:
    """构建包含cookies的请求头。"""
    headers = dict(BILIBILI_HEADERS)
    cookies = _get_bilibili_cookies()
    if cookies:
        cookie_str = "; ".join(f"{k}={v}" for k, v in cookies.items())
        headers["Cookie"] = cookie_str
    return headers


def _sort_quality_urls(quality_urls: dict[str, str], *, prefer_high: bool) -> list[tuple[str, str]]:
    """根据是否登录对 B 站画质进行排序。

    键通常为 qn 数字（如 10000、400、250）。有登录态时优先使用最高画质，
    无登录态时优先使用最低画质以避免 CDN 403。
    """
    def _key(item: tuple[str, str]) -> tuple[bool, int]:
        key, _ = item
        try:
            return (False, int(key))
        except ValueError:
            return (True, 0)

    items = list(quality_urls.items())
    items.sort(key=_key, reverse=prefer_high)
    return items


_ROOM_PATH_RE = re.compile(r"^/(?P<room_id>\d+)/?$")
_SHORT_LINK_HOSTS = {"b23.tv", "www.b23.tv"}


class BilibiliAdapter(BasePlatformAdapter):
    platform = "bilibili"
    display_name = "B站"

    def can_handle(self, url: str) -> bool:
        parsed = urlparse((url or "").strip())
        host = parsed.netloc.lower()
        if host in _SHORT_LINK_HOSTS:
            return True
        return host == "live.bilibili.com" and bool(_ROOM_PATH_RE.fullmatch(parsed.path))

    def parse(self, url: str) -> StreamInfo:
        clean_url = (url or "").strip()
        is_short_link = self._is_short_link(clean_url)
        if is_short_link:
            expanded = self._expand_short_link(clean_url)
            if expanded:
                clean_url = expanded
            else:
                return self._failed(
                    clean_url,
                    "无法展开 b23.tv 短链，请检查网络或使用完整直播间地址。",
                    ERROR_PARSE_FAILED,
                )

        room_id = self._extract_room_id(clean_url)
        if not room_id:
            if is_short_link:
                return self._failed(
                    url.strip(),
                    "b23.tv 短链展开后无法识别 B 站直播间号，请使用完整直播间地址。",
                    ERROR_PARSE_FAILED,
                )
            return self._failed(clean_url, "无法识别 B 站直播间号。", ERROR_PARSE_FAILED)

        room_init = self._fetch_json(ROOM_INIT_URL, params={"id": room_id})
        room_data = room_init.get("data")
        if room_init.get("code") != 0 or not isinstance(room_data, dict):
            return self._failed(clean_url, "B 站直播间初始化接口返回异常。", ERROR_PARSE_FAILED)

        real_room_id = str(room_data.get("room_id") or room_id)
        title = str(room_data.get("title") or "")
        streamer = str(room_data.get("uname") or "")
        if int(room_data.get("live_status") or 0) != 1:
            return self._failed(
                clean_url,
                "B 站直播间未开播。",
                ERROR_OFFLINE,
                raw={"room_init": room_init},
            )

        play_info = self._fetch_json(
            PLAY_INFO_URL,
            params={
                "room_id": real_room_id,
                "protocol": "0,1",
                "format": "0,1,2",
                "codec": "0,1",
                "qn": "10000",
                "platform": "web",
                "dolby": "5",
                "panorama": "1",
            },
        )
        play_data = play_info.get("data")
        if play_info.get("code") != 0 or not isinstance(play_data, dict):
            return self._failed(clean_url, "B 站播放信息接口返回异常。", ERROR_PARSE_FAILED)

        stream_url, quality_urls = self._extract_play_urls(play_data)
        if not quality_urls:
            return self._failed(
                clean_url,
                "B 站直播间暂无公开播放地址。",
                ERROR_RESTRICTED,
                raw={"room_init": room_init, "play_info": play_info},
            )

        # 无登录态时高画质（qn=10000/400）容易被 CDN 拒绝，优先返回最低画质；
        # 有 Cookie 时按原顺序使用最高画质。
        has_cookies = bool(_get_bilibili_cookies())
        sorted_qualities = _sort_quality_urls(quality_urls, prefer_high=has_cookies)
        quality_urls = {k: v for k, v in sorted_qualities}
        selected_quality = next(iter(quality_urls), "")
        stream_url = quality_urls.get(selected_quality, "")

        return StreamInfo(
            platform=self.platform,
            room_url=clean_url,
            stream_url=stream_url,
            title=title,
            streamer=streamer,
            is_live=True,
            quality_urls=quality_urls,
            selected_quality=selected_quality,
            headers=_build_headers_with_cookies(),
            raw={},  # discard large API responses on success to save memory
        )

    def _extract_room_id(self, url: str) -> str:
        match = _ROOM_PATH_RE.fullmatch(urlparse(url).path)
        if match is None:
            return ""
        return match.group("room_id")

    def _is_short_link(self, url: str) -> bool:
        return urlparse(url).netloc.lower() in _SHORT_LINK_HOSTS

    def _expand_short_link(self, url: str) -> str:
        """Follow HTTP redirects on b23.tv short links to obtain the real URL.

        Returns the final URL on success, or an empty string on failure.
        Uses a HEAD request with no redirect following to read the Location
        header directly, avoiding a full page download.
        """
        final_url = fetch_head(url, headers=BILIBILI_HEADERS)
        # fetch_head returns the original URL on failure; treat that as failure
        # for a short link because it should always redirect to live.bilibili.com.
        if final_url == url:
            return ""
        return final_url

    def _fetch_json(self, url: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        headers = _build_headers_with_cookies()
        return fetch_json(url, headers=headers, params=params)

    def _extract_play_urls(self, payload: dict[str, Any]) -> tuple[str, dict[str, str]]:
        playurl_info = payload.get("playurl_info")
        if not isinstance(playurl_info, dict):
            return "", {}
        playurl = playurl_info.get("playurl")
        if not isinstance(playurl, dict):
            return "", {}

        quality_urls: dict[str, str] = {}
        for stream in playurl.get("stream") or []:
            if not isinstance(stream, dict):
                continue
            for fmt in stream.get("format") or []:
                if not isinstance(fmt, dict):
                    continue
                for codec in fmt.get("codec") or []:
                    if not isinstance(codec, dict):
                        continue
                    built_urls = self._build_quality_urls(codec)
                    for quality, quality_url in built_urls.items():
                        quality_urls.setdefault(quality, quality_url)

        stream_url = next(iter(quality_urls.values()), "")
        return stream_url, quality_urls

    def _build_quality_urls(self, codec: dict[str, Any]) -> dict[str, str]:
        base_url = str(codec.get("base_url") or "")
        url_info_list = codec.get("url_info") or []
        if not base_url or not isinstance(url_info_list, list):
            return {}

        accept_qn = codec.get("accept_qn") or []
        qualities = [str(qn) for qn in accept_qn if str(qn)]
        quality_urls: dict[str, str] = {}

        for url_info in url_info_list:
            if not isinstance(url_info, dict):
                continue
            host = str(url_info.get("host") or "")
            extra = str(url_info.get("extra") or "")
            if not host.startswith(("http://", "https://")):
                continue
            stream_url = f"{host}{base_url}{extra}"
            if not qualities:
                quality_urls.setdefault("origin", stream_url)
                continue
            for quality in qualities:
                quality_urls.setdefault(quality, self._replace_qn(stream_url, quality))
            if quality_urls:
                break

        return quality_urls

    def _replace_qn(self, stream_url: str, quality: str) -> str:
        parsed = urlparse(stream_url)
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        query["qn"] = quality
        return urlunparse(parsed._replace(query=urlencode(query)))

    def _failed(self, url: str, error: str, code: str, raw: dict[str, Any] | None = None) -> StreamInfo:
        """Failed result always carries Bilibili request headers (cookies)."""
        return super()._failed(url, error, code, headers=_build_headers_with_cookies(), raw=raw)

