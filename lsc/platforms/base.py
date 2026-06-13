"""Core platform adapter primitives."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

ERROR_UNSUPPORTED_URL = "unsupported_url"
ERROR_OFFLINE = "offline"
ERROR_RESTRICTED = "restricted"
ERROR_PARSE_FAILED = "parse_failed"


def headers_to_ffmpeg_input_args(headers: dict[str, str] | None) -> list[str]:
    """Convert request headers to FFmpeg input arguments."""
    def _sanitize_header_part(value: object) -> str:
        return str(value).replace("\r", "").replace("\n", "").strip()

    clean_headers = {
        _sanitize_header_part(key): _sanitize_header_part(value)
        for key, value in (headers or {}).items()
        if _sanitize_header_part(key) and _sanitize_header_part(value)
    }
    if not clean_headers:
        return []

    header_blob = "".join(f"{key}: {value}\r\n" for key, value in clean_headers.items())
    return ["-headers", header_blob]


@dataclass(slots=True)
class StreamInfo:
    platform: str
    room_url: str
    stream_url: str = ""
    title: str = ""
    streamer: str = ""
    is_live: bool = False
    quality_urls: dict[str, str] = field(default_factory=dict)
    selected_quality: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    error_code: str = ""

    def to_legacy_dict(self) -> dict[str, Any]:
        """Return the dictionary shape consumed by the current GUI code."""
        return {
            "platform": self.platform,
            "roomUrl": self.room_url,
            "streamUrl": self.stream_url,
            "title": self.title,
            "streamerName": self.streamer,
            "isLive": self.is_live,
            "selectedQuality": self.selected_quality,
            "availableQualities": list(self.quality_urls.keys()),
            "qualityUrls": dict(self.quality_urls),
            "error": self.error,
            "errorCode": self.error_code,
            "_headers": dict(self.headers),
            "_inputArgs": headers_to_ffmpeg_input_args(self.headers),
            "_raw": dict(self.raw),
        }


class PlatformAdapter(Protocol):
    """Interface implemented by concrete platform adapters."""

    platform: str

    def can_handle(self, url: str) -> bool:
        """Return whether this adapter owns the URL."""

    def parse(self, url: str) -> StreamInfo:
        """Parse a room URL or stream URL into StreamInfo."""
