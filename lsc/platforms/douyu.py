"""Adapter for Douyu live room URLs."""
from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlparse

from .base import (
    ERROR_OFFLINE,
    ERROR_PARSE_FAILED,
    ERROR_RESTRICTED,
    BasePlatformAdapter,
    DEFAULT_USER_AGENT,
    StreamInfo,
    fetch_url,
)

DOUYU_HEADERS = {
    "Referer": "https://www.douyu.com/",
    "User-Agent": DEFAULT_USER_AGENT,
}
_ROOM_PATH_RE = re.compile(r"^/(\d+)/?$")


class DouyuAdapter(BasePlatformAdapter):
    platform = "douyu"
    display_name = "斗鱼"

    def can_handle(self, url: str) -> bool:
        parsed = urlparse((url or "").strip())
        host = parsed.netloc.lower()
        return host in {"www.douyu.com", "douyu.com"} and bool(_ROOM_PATH_RE.fullmatch(parsed.path))

    def parse(self, url: str) -> StreamInfo:
        clean_url = (url or "").strip()
        match = _ROOM_PATH_RE.fullmatch(urlparse(clean_url).path)
        if not match:
            return self._failed(clean_url, "无法识别斗鱼房间号", ERROR_PARSE_FAILED)

        room_id = match.group(1)

        # Fetch the room page to check live status and get stream info
        try:
            html = fetch_url(clean_url, headers=DOUYU_HEADERS)
        except Exception as exc:
            return self._failed(clean_url, f"斗鱼页面加载失败: {exc}", ERROR_PARSE_FAILED)

        # Check if room is live by looking for live status in page
        if '"status":1' not in html and '"show_status":1' not in html:
            # Try to find room title to confirm the page loaded
            if '<title>' not in html.lower():
                return self._failed(clean_url, "斗鱼页面加载异常", ERROR_PARSE_FAILED)
            return self._failed(clean_url, "斗鱼直播间未开播", ERROR_OFFLINE)

        # Extract stream URL from page JS
        stream_url = self._extract_stream_url(html, room_id)
        if not stream_url:
            return self._failed(clean_url, "斗鱼直播流获取失败，可能需要登录或房间已加密", ERROR_RESTRICTED)

        # Extract title and streamer
        title = self._extract_field(html, r'"room_name"\s*:\s*"([^"]*)"')
        streamer = self._extract_field(html, r'"nickname"\s*:\s*"([^"]*)"')

        return self._success(
            clean_url,
            stream_url=stream_url,
            title=title or f"斗鱼直播间 {room_id}",
            streamer=streamer or "斗鱼主播",
            is_live=True,
            quality_urls={"source": stream_url},
            selected_quality="source",
            headers=dict(DOUYU_HEADERS),
        )

    def _extract_stream_url(self, html: str, room_id: str) -> str:
        """Extract stream URL from Douyu page. Tries multiple methods."""
        # Method 1: Look for hls_url in page data
        hls_match = re.search(r'"hls_url"\s*:\s*"(https?://[^"]*\.m3u8[^"]*)"', html)
        if hls_match:
            url = hls_match.group(1).replace("\\u002F", "/")
            if url.startswith("http"):
                return url

        # Method 2: Look for rtmp/rtmp_url
        rtmp_match = re.search(r'"rtmp_url"\s*:\s*"(rtmp://[^"]*)"', html)
        if rtmp_match:
            return rtmp_match.group(1).replace("\\u002F", "/")

        # Method 3: Look for any .flv URL
        flv_match = re.search(r'(https?://[^"\']*\.flv[^"\'\s]*)', html)
        if flv_match:
            return flv_match.group(1).replace("\\u002F", "/")

        # Method 4: Try the Douyu API endpoint
        try:
            api_url = f"https://m.douyu.com/html5/live?roomId={room_id}"
            api_html = fetch_url(api_url, headers=DOUYU_HEADERS)
            data = json.loads(api_html)
            hls = data.get("data", {}).get("hls_url", "")
            if hls:
                return hls
        except Exception:
            pass

        return ""

    @staticmethod
    def _extract_field(html: str, pattern: str) -> str:
        match = re.search(pattern, html)
        return match.group(1) if match else ""

    def _failed(self, url: str, error: str, code: str, raw: Any = None) -> StreamInfo:
        return super()._failed(url, error, code, headers=dict(DOUYU_HEADERS), raw=raw or {})
