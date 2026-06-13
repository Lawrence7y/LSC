"""Registry helpers for platform adapters."""
from __future__ import annotations

from collections.abc import Iterable, Mapping

from .base import ERROR_PARSE_FAILED, ERROR_UNSUPPORTED_URL, PlatformAdapter, StreamInfo
from .direct import DirectAdapter

QUALITY_PRESET_CANDIDATES = {
    "原画": ["origin", "source", "蓝光", "超清", "FULL_HD1", "uhd", "UHD1", "10000", "400", "hd", "HD1", "sd"],
    "高清": ["hd", "HD1", "250", "300", "uhd", "UHD1", "origin", "source", "sd", "SD1"],
    "流畅": ["sd", "SD1", "150", "80", "ld", "origin", "source", "hd"],
}

_DEFAULT_ADAPTERS: tuple[PlatformAdapter, ...] = (DirectAdapter(),)


def get_adapters(adapters: Iterable[PlatformAdapter] | None = None) -> tuple[PlatformAdapter, ...]:
    if adapters is None:
        return _DEFAULT_ADAPTERS
    return tuple(adapters)


def detect_platform(url: str, adapters: Iterable[PlatformAdapter] | None = None) -> str:
    clean_url = (url or "").strip()
    for adapter in get_adapters(adapters):
        if adapter.can_handle(clean_url):
            return adapter.platform
    return "unknown"


def parse_stream(url: str, adapters: Iterable[PlatformAdapter] | None = None) -> StreamInfo:
    clean_url = (url or "").strip()
    for adapter in get_adapters(adapters):
        if adapter.can_handle(clean_url):
            try:
                return adapter.parse(clean_url)
            except Exception as exc:
                return StreamInfo(
                    platform=adapter.platform,
                    room_url=clean_url,
                    is_live=False,
                    error=f"解析失败: {exc}",
                    error_code=ERROR_PARSE_FAILED,
                )

    return StreamInfo(
        platform="unknown",
        room_url=clean_url,
        is_live=False,
        error="不支持的直播间链接或直播流地址",
        error_code=ERROR_UNSUPPORTED_URL,
    )


def select_quality(info: StreamInfo | Mapping[str, object], quality_preset: str) -> tuple[str, str]:
    if isinstance(info, StreamInfo):
        quality_urls = info.quality_urls
        stream_url = info.stream_url
        selected_quality = info.selected_quality
    else:
        raw_quality_urls = info.get("qualityUrls") or {}
        quality_urls = raw_quality_urls if isinstance(raw_quality_urls, Mapping) else {}
        stream_url = str(info.get("streamUrl", "") or "")
        selected_quality = str(info.get("selectedQuality", "") or "")

    for quality_key in QUALITY_PRESET_CANDIDATES.get(quality_preset, ()):
        url = quality_urls.get(quality_key, "")
        if isinstance(url, str) and url.startswith(("http://", "https://")):
            return url, quality_key
    return stream_url, selected_quality
