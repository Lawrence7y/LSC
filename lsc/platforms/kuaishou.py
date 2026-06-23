"""Adapter for Kuaishou (Kwai) live room URLs.

Kuaishou's web live room exposes an initial state object on the page.
When the room is live, ``liveroom.playList[0].liveStream.playUrls``
contains H.264/H.265 adaptation sets with signed stream URLs. The page
also uses ``undefined`` literals, so the raw text must be sanitized
before JSON parsing.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any
from urllib.parse import urlparse

from .base import (
    ERROR_OFFLINE,
    ERROR_PARSE_FAILED,
    ERROR_RESTRICTED,
    BasePlatformAdapter,
    StreamInfo,
    extract_braced_block,
    extract_json_after_marker,
    fetch_url,
    sanitize_undefined_to_null,
)

_log = logging.getLogger(__name__)

KUAISHOU_HEADERS = {
    "Referer": "https://live.kuaishou.com/",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
}

# Known live room URL patterns:
#   https://live.kuaishou.com/u/<user_id>
#   https://live.kuaishou.com/w/<id>
#   https://live.kuaishou.com/profile/<user_id> (may redirect to live)
_ROOM_PATH_RE = re.compile(r"^/(u|w|profile)/(?P<user_id>[^/?#]+)/?$")


class KuaishouAdapter(BasePlatformAdapter):
    platform = "kuaishou"
    display_name = "快手"

    def can_handle(self, url: str) -> bool:
        parsed = urlparse((url or "").strip())
        host = parsed.netloc.lower()
        return host in {"live.kuaishou.com", "kuaishou.com"} and bool(
            _ROOM_PATH_RE.fullmatch(parsed.path)
        )

    def parse(self, url: str) -> StreamInfo:
        clean_url = (url or "").strip()
        try:
            html = self._fetch_page(clean_url)
            data = self._extract_initial_state(html)
        except Exception as exc:
            return self._failed(clean_url, f"快手页面解析失败: {exc}", ERROR_PARSE_FAILED)

        liveroom = data.get("liveroom") if isinstance(data, dict) else {}
        liveroom = liveroom if isinstance(liveroom, dict) else {}
        play_list = liveroom.get("playList") or []
        if not isinstance(play_list, list) or not play_list:
            return self._failed(
                clean_url,
                "快手直播间未找到播放列表，可能需要登录或房间已下播。",
                ERROR_RESTRICTED,
                raw=data,
            )

        item = play_list[0]
        if not isinstance(item, dict):
            return self._failed(clean_url, "快手播放列表格式异常", ERROR_PARSE_FAILED, raw=data)

        author = item.get("author") or {}
        author = author if isinstance(author, dict) else {}
        streamer = str(author.get("name") or "")
        title = str(author.get("description") or streamer)

        # Status indicators vary across page revisions.
        is_living = bool(item.get("isLiving") or author.get("living"))
        status = item.get("status") or {}
        forbidden_state = int(status.get("forbiddenState") or 0) if isinstance(status, dict) else 0
        if not is_living or forbidden_state:
            return self._failed(
                clean_url,
                "快手直播间未开播或当前不可访问。",
                ERROR_OFFLINE,
                raw=data,
            )

        live_stream = item.get("liveStream") or {}
        live_stream = live_stream if isinstance(live_stream, dict) else {}
        play_urls = live_stream.get("playUrls") or {}
        play_urls = play_urls if isinstance(play_urls, dict) else {}

        quality_urls = self._extract_quality_urls(play_urls)
        if not quality_urls:
            return self._failed(
                clean_url,
                "快手直播间暂无公开播放地址，可能需要登录或平台限制。",
                ERROR_RESTRICTED,
                raw=data,
            )

        selected_quality = next(iter(quality_urls), "")
        return self._success(
            clean_url,
            stream_url=quality_urls.get(selected_quality, ""),
            title=title,
            streamer=streamer,
            is_live=True,
            quality_urls=quality_urls,
            selected_quality=selected_quality,
            headers=dict(KUAISHOU_HEADERS),
            raw={},  # discard large page payload on success to save memory
        )

    def _fetch_page(self, url: str) -> str:
        return fetch_url(url, headers=KUAISHOU_HEADERS)

    def _extract_initial_state(self, html: str) -> dict[str, Any]:
        """Extract and sanitize ``window.__INITIAL_STATE__`` from the HTML."""
        marker = "window.__INITIAL_STATE__"

        # Fast path: the marker is followed by a standard JSON object.
        data = extract_json_after_marker(
            html, marker, sanitize=sanitize_undefined_to_null
        )
        if isinstance(data, dict):
            return data

        # Fallback: brace-balanced block extraction for malformed pages.
        marker_index = html.find(marker)
        if marker_index < 0:
            raise ValueError("页面中未找到 window.__INITIAL_STATE__")

        brace_index = html.find("{", marker_index)
        if brace_index < 0:
            raise ValueError("无法定位初始化数据起始位置")

        payload = extract_braced_block(html, brace_index)
        if not payload:
            raise ValueError("无法提取初始化数据块")

        payload = sanitize_undefined_to_null(payload)
        data = json.loads(payload)
        return data if isinstance(data, dict) else {}

    def _extract_quality_urls(self, play_urls: dict[str, Any]) -> dict[str, str]:
        """Collect stream URLs from H.264 adaptation sets.

        Falls back to HEVC if no H.264 URLs are available.
        """
        quality_urls: dict[str, str] = {}
        for codec_key in ("h264", "hevc"):
            codec_data = play_urls.get(codec_key)
            if not isinstance(codec_data, dict):
                continue
            adaptation_set = codec_data.get("adaptationSet") or []
            if not isinstance(adaptation_set, list):
                continue
            for adaptation in adaptation_set:
                if not isinstance(adaptation, dict):
                    continue
                representations = adaptation.get("representation") or []
                if not isinstance(representations, list):
                    continue
                for rep in representations:
                    if not isinstance(rep, dict):
                        continue
                    stream_url = rep.get("url") or ""
                    if not isinstance(stream_url, str) or not stream_url.startswith(
                        ("http://", "https://")
                    ):
                        continue
                    # Build a readable quality key. Prefer an explicit name,
                    # otherwise fall back to resolution/bitrate.
                    name = str(rep.get("name") or rep.get("qualityLabel") or "").strip()
                    height = rep.get("height") or 0
                    bitrate = rep.get("bitrate") or 0
                    if name:
                        key = name
                    elif height:
                        key = f"{height}p"
                    elif bitrate:
                        key = f"{bitrate}k"
                    else:
                        key = f"{codec_key}_{len(quality_urls) + 1}"
                    # Avoid overwriting; keep the first URL for each key.
                    if key not in quality_urls:
                        quality_urls[key] = stream_url
            if quality_urls:
                break
        return quality_urls

    def _failed(
        self,
        url: str,
        error: str,
        code: str,
        raw: dict[str, Any] | None = None,
    ) -> StreamInfo:
        """Failed result always carries Kuaishou request headers."""
        return super()._failed(url, error, code, headers=dict(KUAISHOU_HEADERS), raw=raw)
