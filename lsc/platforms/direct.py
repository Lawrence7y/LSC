"""Adapter for direct stream URLs."""
from __future__ import annotations

from urllib.parse import urlparse

from .base import PlatformAdapter, StreamInfo

_DIRECT_SUFFIXES = (".m3u8", ".flv")


class DirectAdapter(PlatformAdapter):
    platform = "direct"

    def can_handle(self, url: str) -> bool:
        clean_url = (url or "").strip()
        parsed = urlparse(clean_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return False
        return parsed.path.lower().endswith(_DIRECT_SUFFIXES)

    def parse(self, url: str) -> StreamInfo:
        clean_url = (url or "").strip()
        return StreamInfo(
            platform=self.platform,
            room_url=clean_url,
            stream_url=clean_url,
            title="公开直播流",
            streamer="直链",
            is_live=True,
            quality_urls={"origin": clean_url},
            selected_quality="origin",
        )
