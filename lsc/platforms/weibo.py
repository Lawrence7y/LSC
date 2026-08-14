"""Adapter for Weibo live room URLs.

Weibo live pages embed stream metadata in JSON within script tags.
We look for ``window.__INITIAL_STATE__`` or ``stream_url`` / ``hls_url``
patterns in the HTML, similar to the generic adapter but with Weibo-specific
header and URL extraction logic.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from urllib.parse import parse_qs, urlparse

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
_SHOW_PATH_RE = re.compile(r"^/show/?$")
_LIVE_HOSTS = {"weibo.com", "www.weibo.com", "live.weibo.com", "m.weibo.cn"}


class WeiboAdapter(BasePlatformAdapter):
    platform = "weibo"
    display_name = "微博"

    def can_handle(self, url: str) -> bool:
        parsed = urlparse((url or "").strip())
        host = parsed.netloc.lower()
        if host not in _LIVE_HOSTS:
            return False
        if _ROOM_PATH_RE.search(parsed.path):
            return True
        if _SHOW_PATH_RE.match(parsed.path):
            return bool((parse_qs(parsed.query).get("id") or [""])[0])
        return False

    def parse(self, url: str) -> StreamInfo:
        return self._parse(url)

    def parse_with_context(self, url: str, context: object) -> StreamInfo:
        """Use one scoped request context for the page and media candidate."""
        headers = dict(WEIBO_HEADERS)
        headers.update(dict(getattr(context, "headers", {}) or {}))
        return self._parse(
            url,
            request_headers=headers,
            network_context=getattr(context, "network_context", {}) or {},
        )

    def _parse(
        self,
        url: str,
        *,
        request_headers: Mapping[str, str] | None = None,
        network_context: Mapping[str, object] | None = None,
    ) -> StreamInfo:
        clean_url = (url or "").strip()
        headers = dict(request_headers or WEIBO_HEADERS)
        try:
            if request_headers is None and not network_context:
                html = self._fetch_page(clean_url)
            else:
                html = self._fetch_page(
                    clean_url,
                    headers=headers,
                    network_context=network_context,
                )
        except Exception as exc:
            return self._failed(clean_url, f"微博直播页获取失败: {exc}", ERROR_PARSE_FAILED, headers=headers)

        # 尝试从 __INITIAL_STATE__ 提取
        data = extract_json_after_marker(html, "window.__INITIAL_STATE__")
        if data is None:
            data = extract_json_after_marker(html, "__INITIAL_STATE__")

        stream_url = ""
        title = ""
        streamer = ""
        quality_urls: dict[str, str] = {}
        is_live = False
        source_kind = "fallback"
        confidence = 0.35

        if data is not None:
            source_kind = "official"
            confidence = 0.75
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
            return self._failed(clean_url, "微博直播间未开播或无法获取流地址", ERROR_OFFLINE, headers=headers)

        if not stream_url:
            return self._failed(clean_url, "微博未找到公开流", ERROR_RESTRICTED, headers=headers)

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
            headers=headers,
            raw={
                "source_kind": source_kind,
                "confidence": confidence,
                "state_source": "initial_state" if data is not None else "page_pattern",
            },
        )

    def _fetch_page(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        network_context: Mapping[str, object] | None = None,
    ) -> str:
        context = dict(network_context or {})
        proxy_url = str(
            context.get("proxy_url")
            or context.get("http_proxy")
            or context.get("https_proxy")
            or ""
        ).strip()
        try:
            timeout = max(1, min(300, int(float(context.get("timeout_sec", 12)))))
        except (TypeError, ValueError):
            timeout = 12
        return fetch_url(
            url,
            headers=dict(headers or WEIBO_HEADERS),
            timeout=timeout,
            proxy_url=proxy_url,
        )
