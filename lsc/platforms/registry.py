"""Registry helpers for platform adapters."""
from __future__ import annotations

import logging
import threading
import time
from collections.abc import Iterable, Mapping
from urllib.parse import urlparse

from .base import ERROR_PARSE_FAILED, ERROR_UNSUPPORTED_URL, PlatformAdapter, StreamInfo
from .bilibili import BilibiliAdapter
from .direct import DirectAdapter
from .douyin import DouyinAdapter
from .douyu import DouyuAdapter
from .generic import GenericPageAdapter
from .huya import HuyaAdapter
from .kuaishou import KuaishouAdapter
from .xiaohongshu import XiaohongshuAdapter

_log = logging.getLogger(__name__)

QUALITY_PRESET_CANDIDATES = {
    "原画": ["origin", "source", "蓝光", "超清", "FULL_HD1", "uhd", "UHD1", "10000", "400", "hd", "HD1", "sd", "SD1", "SD2", "ld", "ao"],
    "高清": ["hd", "HD1", "250", "300", "uhd", "UHD1", "FULL_HD1", "origin", "source", "sd", "SD1", "SD2", "ld", "ao"],
    "流畅": ["sd", "SD1", "SD2", "150", "80", "ld", "origin", "source", "hd", "HD1", "uhd", "UHD1", "FULL_HD1", "ao"],
}

# Module-level singleton adapter instances.
#
# These adapters are stateless (see PlatformAdapter Protocol contract in
# lsc.platforms.base) and therefore safe to share across all rooms and
# threads. Do NOT add mutable instance state to adapter classes; if a
# per-call scratch area is needed, keep it as a local variable inside
# ``parse`` and return results via StreamInfo.
_DEFAULT_ADAPTERS: tuple[PlatformAdapter, ...] = (
    DirectAdapter(),
    DouyinAdapter(),
    BilibiliAdapter(),
    HuyaAdapter(),
    KuaishouAdapter(),
    DouyuAdapter(),
    XiaohongshuAdapter(),
    GenericPageAdapter(),  # Last — fallback for unknown platforms
)

# Host -> platform identifiers routing table. Allows parse_stream to skip
# adapters that can never match a URL, avoiding unnecessary HTTP requests.
_URL_ROUTER: dict[str, tuple[str, ...]] = {
    "live.bilibili.com": ("bilibili",),
    "www.bilibili.com": ("bilibili",),
    "b23.tv": ("bilibili",),
    "www.b23.tv": ("bilibili",),
    "live.douyin.com": ("douyin",),
    "www.douyin.com": ("douyin",),
    "douyin.com": ("douyin",),
    "www.huya.com": ("huya",),
    "huya.com": ("huya",),
    "live.kuaishou.com": ("kuaishou",),
    "kuaishou.com": ("kuaishou",),
    "www.douyu.com": ("douyu",),
    "douyu.com": ("douyu",),
    "www.xiaohongshu.com": ("xiaohongshu",),
    "xhslink.com": ("xiaohongshu",),
}


class _ParseCache:
    """Thread-safe TTL cache for adapter parse results.

    Successful parses are cached longer than failures to avoid hammering
    platform APIs, while failures are cached briefly to prevent tight retry
    loops from the UI.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._store: dict[tuple[str, str], tuple[float, StreamInfo]] = {}
        self._access_count = 0
        self._cleanup_every = 20
        self._success_ttl = 30.0
        self._failure_ttl = 10.0

    def _ttl_for(self, info: StreamInfo) -> float:
        return self._failure_ttl if info.error else self._success_ttl

    def _cleanup(self) -> None:
        now = time.monotonic()
        expired = [
            key
            for key, (timestamp, info) in self._store.items()
            if now - timestamp > self._ttl_for(info)
        ]
        for key in expired:
            self._store.pop(key, None)

    def get(self, url: str, platform: str) -> StreamInfo | None:
        with self._lock:
            self._access_count += 1
            if self._access_count >= self._cleanup_every:
                self._access_count = 0
                self._cleanup()

            entry = self._store.get((url, platform))
            if entry is None:
                return None
            timestamp, info = entry
            if time.monotonic() - timestamp > self._ttl_for(info):
                self._store.pop((url, platform), None)
                return None
            return info

    def set(self, url: str, platform: str, info: StreamInfo) -> None:
        with self._lock:
            self._store[(url, platform)] = (time.monotonic(), info)


_parse_cache = _ParseCache()


def _candidate_platforms_for_url(url: str) -> tuple[str, ...] | None:
    """Return platform identifiers that might handle the URL based on its host.

    Returns ``None`` when the host is not in the router, meaning callers
    should fall back to a full linear scan.
    """
    try:
        parsed = urlparse((url or "").strip())
        host = parsed.netloc.lower()
        return _URL_ROUTER.get(host)
    except Exception:
        return None


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


def get_display_name(platform_key: str, adapters: Iterable[PlatformAdapter] | None = None) -> str:
    """Return the user-facing display name for a platform key.

    Falls back to the platform key itself if no matching adapter is found.
    """
    for adapter in get_adapters(adapters):
        if adapter.platform == platform_key:
            return getattr(adapter, "display_name", platform_key)
    return platform_key


def parse_stream(
    url: str,
    adapters: Iterable[PlatformAdapter] | None = None,
    *,
    force_refresh: bool = False,
) -> StreamInfo:
    clean_url = (url or "").strip()
    adapter_list = get_adapters(adapters)

    # Try host-based routing first to avoid probing irrelevant adapters.
    candidate_platforms = _candidate_platforms_for_url(clean_url)
    if candidate_platforms is not None:
        candidate_set = set(candidate_platforms)
        candidates = [a for a in adapter_list if a.platform in candidate_set]
    else:
        candidates = list(adapter_list)

    for adapter in candidates:
        if not adapter.can_handle(clean_url):
            continue

        cached = None if force_refresh else _parse_cache.get(clean_url, adapter.platform)
        if cached is not None:
            _log.debug("Using cached parse result for %s via %s", clean_url, adapter.platform)
            return cached

        try:
            info = adapter.parse(clean_url)
        except Exception as exc:
            info = StreamInfo(
                platform=adapter.platform,
                room_url=clean_url,
                is_live=False,
                error=f"解析失败: {exc}",
                error_code=ERROR_PARSE_FAILED,
            )
        _parse_cache.set(clean_url, adapter.platform, info)
        return info

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
