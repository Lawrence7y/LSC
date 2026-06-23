"""Generic fallback adapter for unknown platforms.

Tries to detect live stream URLs from any web page by searching for
common stream patterns (.m3u8, .flv, rtmp://) in HTML source.
"""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from .base import (
    ERROR_PARSE_FAILED,
    ERROR_RESTRICTED,
    BasePlatformAdapter,
    DEFAULT_USER_AGENT,
    StreamInfo,
    fetch_url,
)

GENERIC_HEADERS = {
    "User-Agent": DEFAULT_USER_AGENT,
}

# URL patterns that are already direct stream URLs (handled by DirectAdapter)
_DIRECT_SUFFIXES = (".m3u8", ".flv")


class GenericPageAdapter(BasePlatformAdapter):
    """Fallback adapter: fetch any web page and search for stream URLs.

    This adapter is registered LAST in _DEFAULT_ADAPTERS. It only handles
    URLs that no other adapter matched. Useful for small/niche platforms
    that embed stream URLs in their page HTML.
    """

    platform = "generic"
    display_name = "通用"

    def can_handle(self, url: str) -> bool:
        """Match any http(s) URL that looks like a web page (not a direct stream)."""
        clean_url = (url or "").strip()
        parsed = urlparse(clean_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return False
        # Skip direct stream URLs (handled by DirectAdapter)
        if parsed.path.lower().endswith(_DIRECT_SUFFIXES):
            return False
        # Skip API endpoints with stream-like query params
        if any(k in parsed.query.lower() for k in ("stream=", "video=", "m3u8", ".flv")):
            return False
        return True

    def parse(self, url: str) -> StreamInfo:
        clean_url = (url or "").strip()

        try:
            html = fetch_url(clean_url, headers=GENERIC_HEADERS, timeout=10, retries=1)
        except Exception as exc:
            return self._failed(clean_url, f"页面加载失败: {exc}", ERROR_PARSE_FAILED)

        if not html or len(html) < 100:
            return self._failed(clean_url, "页面内容为空或过短", ERROR_PARSE_FAILED)

        # Search for stream URLs in the page
        stream_url = self._find_stream_url(html)

        if not stream_url:
            return self._failed(
                clean_url,
                "未在页面中找到直播流地址。该平台可能不支持或需要登录。",
                ERROR_RESTRICTED,
            )

        # Extract title if available
        title = ""
        title_match = re.search(r"<title[^>]*>([^<]+)</title>", html, re.IGNORECASE)
        if title_match:
            title = title_match.group(1).strip()[:100]

        return self._success(
            clean_url,
            stream_url=stream_url,
            title=title or "直播流",
            streamer="未知主播",
            is_live=True,
            quality_urls={"source": stream_url},
            selected_quality="source",
            headers=dict(GENERIC_HEADERS),
        )

    def _find_stream_url(self, html: str) -> str:
        """Search for stream URLs in page HTML using multiple strategies."""

        # Strategy 1: Direct .m3u8 URLs
        m3u8_urls = re.findall(r'(https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*)', html)
        for url in m3u8_urls:
            cleaned = self._clean_url(url)
            if cleaned:
                return cleaned

        # Strategy 2: Direct .flv URLs
        flv_urls = re.findall(r'(https?://[^\s"\'<>]+\.flv[^\s"\'<>]*)', html)
        for url in flv_urls:
            cleaned = self._clean_url(url)
            if cleaned:
                return cleaned

        # Strategy 3: rtmp:// URLs
        rtmp_urls = re.findall(r'(rtmp://[^\s"\'<>]+)', html)
        for url in rtmp_urls:
            cleaned = self._clean_url(url)
            if cleaned:
                return cleaned

        # Strategy 4: Look in <video> or <source> tags
        video_src = re.search(
            r'<(?:video|source)[^>]*\ssrc=["\']?(https?://[^\s"\'<>]+)["\']?',
            html, re.IGNORECASE
        )
        if video_src:
            cleaned = self._clean_url(video_src.group(1))
            if cleaned:
                return cleaned

        # Strategy 5: Common JS variable patterns for stream URLs
        js_patterns = [
            r'(?:streamUrl|playUrl|videoUrl|hlsUrl|flvUrl|liveUrl)\s*[:=]\s*["\']?(https?://[^\s"\'<>;,}]+)',
            r'(?:url|src)\s*:\s*["\']?(https?://[^\s"\'<>;,}]+(?:\.m3u8|\.flv)[^\s"\'<>;,}]*)',
        ]
        for pattern in js_patterns:
            match = re.search(pattern, html, re.IGNORECASE)
            if match:
                cleaned = self._clean_url(match.group(1))
                if cleaned:
                    return cleaned

        return ""

    @staticmethod
    def _clean_url(url: str) -> str:
        """Clean and validate a URL."""
        url = url.strip().rstrip("\\").rstrip('"').rstrip("'")
        url = url.replace("\\u002F", "/").replace("\\/", "/")
        if url.startswith("http://") or url.startswith("https://"):
            # Basic sanity check
            if len(url) > 20 and "." in url:
                return url
        return ""

    def _failed(self, url: str, error: str, code: str, raw: Any = None) -> StreamInfo:
        return super()._failed(url, error, code, headers=dict(GENERIC_HEADERS), raw=raw or {})
