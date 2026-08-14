"""Adapter for Xiaohongshu (小红书/RED) live room URLs."""
from __future__ import annotations

import logging

_log = logging.getLogger(__name__)

import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlparse

from .base import (
    DEFAULT_USER_AGENT,
    ERROR_OFFLINE,
    ERROR_PARSE_FAILED,
    ERROR_RESTRICTED,
    BasePlatformAdapter,
    StreamInfo,
    fetch_head,
    fetch_url,
)
from .redaction import redact_url

XHS_HEADERS = {
    "Referer": "https://www.xiaohongshu.com/",
    "User-Agent": DEFAULT_USER_AGENT,
}
# Live room URL patterns:
#   https://www.xiaohongshu.com/live/{id}
#   https://www.xiaohongshu.com/livestream/{id}
#   https://www.xiaohongshu.com/user/profile/{uid}?live_id={id}
_LIVE_PATH_RE = re.compile(r"^/live/([a-zA-Z0-9_-]+)/?$")
_LIVESTREAM_PATH_RE = re.compile(r"^/livestream/([a-zA-Z0-9_-]+)/?$")
_USER_PATH_RE = re.compile(r"^/user/profile/([a-zA-Z0-9_-]+)/?$")
_SHORT_LINK_HOSTS = {"xhslink.com"}
_LIVE_HOSTS = {"www.xiaohongshu.com"}


class XiaohongshuAdapter(BasePlatformAdapter):
    platform = "xiaohongshu"
    display_name = "小红书"

    def can_handle(self, url: str) -> bool:
        _log.debug("Xiaohongshu: checking %s", redact_url(url)[:60])
        parsed = urlparse((url or "").strip())
        host = parsed.netloc.lower()
        if host in _SHORT_LINK_HOSTS:
            return bool(parsed.path.strip("/"))
        if host not in _LIVE_HOSTS:
            return False
        return (
            bool(_LIVE_PATH_RE.match(parsed.path))
            or bool(_LIVESTREAM_PATH_RE.match(parsed.path))
            or bool(_USER_PATH_RE.match(parsed.path))
        )

    def parse(self, url: str) -> StreamInfo:
        return self._parse(url)

    def parse_with_context(self, url: str, context: object) -> StreamInfo:
        """Resolve both profile and live pages through the scoped context."""
        headers = dict(XHS_HEADERS)
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
        _log.info("Xiaohongshu: parsing %s", redact_url(url)[:80])
        clean_url = (url or "").strip()
        parsed = urlparse(clean_url)
        headers = dict(request_headers or XHS_HEADERS)
        context = dict(network_context or {})

        if parsed.netloc.lower() in _SHORT_LINK_HOSTS:
            expanded = self._expand_short_link(
                clean_url,
                request_headers=headers,
                network_context=context,
            )
            if not expanded:
                return self._failed(clean_url, "小红书短链解析失败", ERROR_PARSE_FAILED)
            clean_url = expanded
            parsed = urlparse(clean_url)

        # Direct live room URL
        live_match = _LIVE_PATH_RE.match(parsed.path) or _LIVESTREAM_PATH_RE.match(parsed.path)
        if live_match:
            return self._parse_live_room(
                clean_url,
                live_match.group(1),
                request_headers=headers if request_headers is not None else None,
                network_context=context if request_headers is not None or context else None,
            )

        # User profile URL — need to find their live room
        user_match = _USER_PATH_RE.match(parsed.path)
        if user_match:
            return self._parse_user_profile(
                clean_url,
                user_match.group(1),
                request_headers=headers if request_headers is not None else None,
                network_context=context if request_headers is not None or context else None,
            )

        return self._failed(clean_url, "无法识别小红书链接", ERROR_PARSE_FAILED)

    def _parse_live_room(
        self,
        url: str,
        live_id: str,
        *,
        request_headers: Mapping[str, str] | None = None,
        network_context: Mapping[str, object] | None = None,
    ) -> StreamInfo:
        """Parse a direct live room URL."""
        try:
            if request_headers is None and network_context is None:
                html = fetch_url(url, headers=XHS_HEADERS)
            else:
                html = self._fetch_page(
                    url,
                    headers=request_headers,
                    network_context=network_context,
                )
        except Exception as exc:
            return self._failed(url, f"小红书页面加载失败: {exc}", ERROR_PARSE_FAILED)

        stream_url = self._extract_stream_url(html)
        if not stream_url:
            # Check if the room is live
            if '"status"' in html and '"LIVING"' not in html and '"isLive":true' not in html:
                return self._failed(url, "小红书直播间未开播", ERROR_OFFLINE)
            return self._failed(url, "小红书直播流获取失败，可能需要登录", ERROR_RESTRICTED)

        title = self._extract_field(html, r'"title"\s*:\s*"([^"]*)"')
        streamer = self._extract_field(html, r'"nickname"\s*:\s*"([^"]*)"')

        return self._success(
            url,
            stream_url=stream_url,
            title=title or f"小红书直播 {live_id}",
            streamer=streamer or "小红书主播",
            is_live=True,
            quality_urls={"source": stream_url},
            selected_quality="source",
            headers=dict(request_headers or XHS_HEADERS),
            raw={
                "source_kind": "fallback",
                "confidence": 0.45,
                "state_source": "live_page",
            },
        )

    def _parse_user_profile(
        self,
        url: str,
        user_id: str,
        *,
        request_headers: Mapping[str, str] | None = None,
        network_context: Mapping[str, object] | None = None,
    ) -> StreamInfo:
        """Parse a user profile URL — try to find active live room."""
        try:
            if request_headers is None and network_context is None:
                html = fetch_url(url, headers=XHS_HEADERS)
            else:
                html = self._fetch_page(
                    url,
                    headers=request_headers,
                    network_context=network_context,
                )
        except Exception as exc:
            return self._failed(url, f"小红书用户页面加载失败: {exc}", ERROR_PARSE_FAILED)

        # Look for live room ID in the user page
        live_id_match = re.search(r'"liveId"\s*:\s*"([a-zA-Z0-9_-]+)"', html)
        if live_id_match:
            live_id = live_id_match.group(1)
            return self._parse_live_room(
                f"https://www.xiaohongshu.com/live/{live_id}",
                live_id,
                request_headers=request_headers,
                network_context=network_context,
            )

        # Check for live status indicators
        if '"isLive":true' in html or '"LIVING"' in html:
            stream_url = self._extract_stream_url(html)
            if stream_url:
                return self._success(
                    url,
                    stream_url=stream_url,
                    title="小红书直播",
                    streamer="小红书主播",
                    is_live=True,
                    quality_urls={"source": stream_url},
                    selected_quality="source",
                    headers=dict(request_headers or XHS_HEADERS),
                    raw={
                        "source_kind": "fallback",
                        "confidence": 0.4,
                        "state_source": "profile_page",
                    },
                )

        return self._failed(url, "该用户未在直播", ERROR_OFFLINE)

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
            headers=dict(headers or XHS_HEADERS),
            timeout=timeout,
            proxy_url=proxy_url,
        )

    def _expand_short_link(
        self,
        url: str,
        *,
        request_headers: Mapping[str, str] | None = None,
        network_context: Mapping[str, object] | None = None,
    ) -> str:
        """Follow xhslink.com redirects to a www.xiaohongshu.com live URL."""
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
        final_url = fetch_head(
            url,
            headers=dict(request_headers or XHS_HEADERS),
            timeout=timeout,
            proxy_url=proxy_url,
        )
        if not final_url or final_url == url:
            return ""
        parsed = urlparse(final_url)
        host = parsed.netloc.lower()
        if host not in _LIVE_HOSTS:
            return ""
        if not (
            _LIVE_PATH_RE.match(parsed.path)
            or _LIVESTREAM_PATH_RE.match(parsed.path)
            or _USER_PATH_RE.match(parsed.path)
        ):
            return ""
        return final_url

    def _extract_stream_url(self, html: str) -> str:
        """Extract stream URL from Xiaohongshu page."""
        # Method 1: Look for HLS/m3u8 URL
        m3u8_match = re.search(r'(https?://[^"\']*\.m3u8[^"\'\s]*)', html)
        if m3u8_match:
            url = m3u8_match.group(1).replace("\\u002F", "/").replace("\\/", "/")
            return url

        # Method 2: Look for FLV URL
        flv_match = re.search(r'(https?://[^"\']*\.flv[^"\'\s]*)', html)
        if flv_match:
            url = flv_match.group(1).replace("\\u002F", "/").replace("\\/", "/")
            return url

        # Method 3: Look for stream URL in JSON data
        stream_match = re.search(r'"streamUrl"\s*:\s*"(https?://[^"]*)"', html)
        if stream_match:
            return stream_match.group(1).replace("\\u002F", "/").replace("\\/", "/")

        # Method 4: Look for hlsUrl or flvUrl
        hls_match = re.search(r'"hlsUrl"\s*:\s*"(https?://[^"]*)"', html)
        if hls_match:
            return hls_match.group(1).replace("\\u002F", "/").replace("\\/", "/")

        flv_url_match = re.search(r'"flvUrl"\s*:\s*"(https?://[^"]*)"', html)
        if flv_url_match:
            return flv_url_match.group(1).replace("\\u002F", "/").replace("\\/", "/")

        return ""

    @staticmethod
    def _extract_field(html: str, pattern: str) -> str:
        match = re.search(pattern, html)
        return match.group(1) if match else ""

    def _failed(
        self,
        url: str,
        error: str,
        error_code: str = "parse_failed",
        *,
        headers: dict[str, str] | None = None,
        raw: dict[str, Any] | None = None,
    ) -> StreamInfo:
        return super()._failed(url, error, error_code, headers=dict(XHS_HEADERS), raw=raw or {})
