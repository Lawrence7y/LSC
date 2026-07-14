"""Adapter for Weibo live room URLs.

Weibo live pages embed stream metadata in JSON within script tags.
We look for ``window.__INITIAL_STATE__`` or ``stream_url`` / ``hls_url``
patterns in the HTML, similar to the generic adapter but with Weibo-specific
header and URL extraction logic.
"""
from __future__ import annotations

import logging
import re
from urllib.parse import urlparse

from .base import (
    DEFAULT_USER_AGENT,
    ERROR_OFFLINE,
    ERROR_PARSE_FAILED,
    ERROR_RESTRICTED,
    BasePlatformAdapter,
    StreamInfo,
    extract_json_after_marker,
    fetch_url,
)

_log = logging.getLogger(__name__)

WEIBO_HEADERS = {
    "Referer": "https://weibo.com/",
    "User-Agent": DEFAULT_USER_AGENT,
}

_ROOM_PATH_RE = re.compile(r"^/l/wblive/(?P<live_id>\w+)", re.IGNORECASE)
_LIVE_HOSTS = {"weibo.com", "www.weibo.com", "live.weibo.com", "m.weibo.cn"}


class WeiboAdapter(BasePlatformAdapter):
    platform = "weibo"
    display_name = "微博"

    def can_handle(self, url: str) -> bool:
        parsed = urlparse((url or "").strip())
        host = parsed.netloc.lower()
        return host in _LIVE_HOSTS and bool(_ROOM_PATH_RE.search(parsed.path))

    def parse(self, url: str) -> StreamInfo:
        clean_url = (url or "").strip()
        try:
            html = self._fetch_page(clean_url)
        except Exception as exc:
            return self._failed(clean_url, f"微博直播页获取失败: {exc}", ERROR_PARSE_FAILED, headers=dict(WEIBO_HEADERS))

        # 尝试从 __INITIAL_STATE__ 提取
        data = extract_json_after_marker(html, "window.__INITIAL_STATE__")
        if data is None:
            data = extract_json_after_marker(html, "__INITIAL_STATE__")

        stream_url = ""
        title = ""
        streamer = ""
        quality_urls: dict[str, str] = {}
        is_live = False

        if data is not None:
            # 尝试多种可能的路径提取流地址
            for key in ("stream_url", "streamUrl", "hls_url", "hlsUrl", "flv_url", "flvUrl", "playUrl", "play_url"):
                val = data.get(key)
                if isinstance(val, str) and val.startswith(("http://", "https://")):
                    stream_url = val
                    quality_urls["source"] = val
                    break
                if isinstance(val, dict):
                    for qk in ("source", "origin", "hd", "sd"):
                        qv = val.get(qk)
                        if isinstance(qv, str) and qv.startswith(("http://", "https://")):
                            stream_url = stream_url or qv
                            quality_urls[qk] = qv

            title = str(data.get("title") or data.get("live_title") or "")
            streamer = str(data.get("nickname") or data.get("screen_name") or data.get("uname") or "")
            is_live = bool(data.get("is_live") or data.get("isLiving") or data.get("living"))
        else:
            # 回退到正则搜索
            for pattern in [
                r'"(https?://[^"]*\.m3u8[^"]*)"',
                r'"(https?://[^"]*\.flv[^"]*)"',
                r'"stream_url"\s*:\s*"(https?://[^"]*)"',
                r'"hls_url"\s*:\s*"(https?://[^"]*)"',
                r'"flv_url"\s*:\s*"(https?://[^"]*)"',
            ]:
                m = re.search(pattern, html)
                if m:
                    stream_url = m.group(1)
                    quality_urls["source"] = stream_url
                    break

            title_match = re.search(r'<title[^>]*>([^<]+)</title>', html, re.IGNORECASE)
            if title_match:
                page_title = title_match.group(1).strip()
                if " - " in page_title:
                    parts = page_title.split(" - ", 1)
                    streamer = parts[0].strip()
                    title = parts[1].strip()
                else:
                    title = page_title
            is_live = bool(stream_url)

        if not is_live and not stream_url:
            return self._failed(clean_url, "微博直播间未开播或无法获取流地址", ERROR_OFFLINE, headers=dict(WEIBO_HEADERS))

        if not stream_url:
            return self._failed(clean_url, "微博未找到公开流", ERROR_RESTRICTED, headers=dict(WEIBO_HEADERS))

        if not title:
            title = "微博直播"
        if not streamer:
            streamer = "微博主播"

        return self._success(
            clean_url,
            stream_url=stream_url,
            title=title,
            streamer=streamer,
            is_live=True,
            quality_urls=quality_urls,
            selected_quality="source",
            headers=dict(WEIBO_HEADERS),
            raw={},
        )

    def _fetch_page(self, url: str) -> str:
        return fetch_url(url, headers=WEIBO_HEADERS)
