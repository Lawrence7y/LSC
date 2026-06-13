"""Adapter for public Huya live room URLs."""
from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .base import ERROR_OFFLINE, ERROR_PARSE_FAILED, ERROR_RESTRICTED, StreamInfo

HUYA_HEADERS = {
    "Referer": "https://www.huya.com/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
}
_ROOM_PATH_RE = re.compile(r"^/[^/?#]+/?$")


class HuyaAdapter:
    platform = "huya"

    def can_handle(self, url: str) -> bool:
        parsed = urlparse((url or "").strip())
        host = parsed.netloc.lower()
        return host in {"www.huya.com", "huya.com"} and bool(_ROOM_PATH_RE.fullmatch(parsed.path))

    def parse(self, url: str) -> StreamInfo:
        clean_url = (url or "").strip()
        try:
            html = self._fetch_page(clean_url)
            data = self._extract_global_init(html)
        except Exception as exc:
            return self._failed(clean_url, f"虎牙直播间解析失败: {exc}", ERROR_PARSE_FAILED)

        room_info = data.get("roomInfo")
        room_info = room_info if isinstance(room_info, dict) else {}
        profile_info = data.get("profileInfo")
        profile_info = profile_info if isinstance(profile_info, dict) else {}

        if int(room_info.get("tLiveStatus") or 0) != 1:
            return self._failed(clean_url, "虎牙直播间未开播", ERROR_OFFLINE, raw=data)

        quality_urls = self._extract_stream_urls(data)
        stream_url = next(iter(quality_urls.values()), "")
        if not stream_url:
            return self._failed(clean_url, "虎牙未找到公开流", ERROR_RESTRICTED, raw=data)

        return StreamInfo(
            platform=self.platform,
            room_url=clean_url,
            stream_url=stream_url,
            title=str(room_info.get("sIntroduction") or ""),
            streamer=str(profile_info.get("nick") or ""),
            is_live=True,
            quality_urls=quality_urls,
            selected_quality=next(iter(quality_urls), ""),
            headers=dict(HUYA_HEADERS),
            raw=data,
        )

    def _fetch_page(self, url: str) -> str:
        request = Request(url, headers=HUYA_HEADERS)
        with urlopen(request, timeout=15) as response:
            return response.read().decode("utf-8", errors="replace")

    def _extract_global_init(self, html: str) -> dict[str, Any]:
        marker = "window.HNF_GLOBAL_INIT"
        marker_index = html.find(marker)
        if marker_index < 0:
            raise ValueError("未找到虎牙页面初始化数据")

        brace_index = html.find("{", marker_index)
        if brace_index < 0:
            raise ValueError("虎牙页面初始化数据缺少对象内容")

        decoder = json.JSONDecoder()
        data, _ = decoder.raw_decode(html[brace_index:])
        return data if isinstance(data, dict) else {}

    def _extract_stream_urls(self, data: dict[str, Any]) -> dict[str, str]:
        stream = data.get("stream")
        stream = stream if isinstance(stream, dict) else {}
        for item in stream.get("data") or []:
            if not isinstance(item, dict):
                continue
            for stream_info in item.get("gameStreamInfoList") or []:
                if not isinstance(stream_info, dict):
                    continue
                flv_url = str(stream_info.get("sFlvUrl") or "")
                stream_name = str(stream_info.get("sStreamName") or "")
                suffix = str(stream_info.get("sFlvUrlSuffix") or "flv")
                anti_code = str(stream_info.get("sFlvAntiCode") or "")
                if not flv_url.startswith(("http://", "https://")) or not stream_name:
                    continue
                stream_url = f"{flv_url.rstrip('/')}/{stream_name}.{suffix}"
                if anti_code:
                    stream_url = f"{stream_url}?{anti_code}"
                return {"source": stream_url}
        return {}

    def _failed(self, url: str, error: str, code: str, raw: dict[str, Any] | None = None) -> StreamInfo:
        return StreamInfo(
            platform=self.platform,
            room_url=url,
            is_live=False,
            headers=dict(HUYA_HEADERS),
            raw=raw or {},
            error=error,
            error_code=code,
        )
