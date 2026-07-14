"""平台适配器注册中心 — 管理直播平台适配器的发现、解析和画质选择。

提供 Protocol + Registry 模式：
- 所有平台适配器实现统一的 PlatformAdapter 协议
- 注册中心维护适配器列表，按 host 路由到可能匹配的平台
- 解析结果按 URL 缓存，避免频繁请求平台 API
"""
from __future__ import annotations

import logging
import threading
import time
from collections.abc import Iterable, Mapping
from urllib.parse import parse_qs, urlparse

from .base import ERROR_PARSE_FAILED, ERROR_UNSUPPORTED_URL, PlatformAdapter, StreamInfo
from .bilibili import BilibiliAdapter
from .direct import DirectAdapter
from .douyin import DouyinAdapter
from .douyu import DouyuAdapter
from .generic import GenericPageAdapter
from .huya import HuyaAdapter
from .kuaishou import KuaishouAdapter
from .weibo import WeiboAdapter
from .xiaohongshu import XiaohongshuAdapter

_log = logging.getLogger(__name__)

QUALITY_PRESET_CANDIDATES = {
    "原画": ["origin", "source", "蓝光", "超清", "FULL_HD1", "uhd", "UHD1", "10000", "400", "hd", "HD1", "sd", "SD1", "SD2", "ld", "ao"],
    "高清": ["hd", "HD1", "250", "300", "uhd", "UHD1", "FULL_HD1", "origin", "source", "sd", "SD1", "SD2", "ld", "ao"],
    "标清": ["sd", "SD1", "SD2", "150", "300", "ld", "hd", "HD1", "origin", "source", "uhd", "UHD1", "FULL_HD1", "ao"],
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
    WeiboAdapter(),
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
    "weibo.com": ("weibo",),
    "www.weibo.com": ("weibo",),
    "live.weibo.com": ("weibo",),
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
        self._stop_event = threading.Event()
        self._cleanup_thread = threading.Thread(target=self._cleanup_worker, daemon=True)
        self._cleanup_thread.start()

    def _ttl_for(self, info: StreamInfo) -> float:
        return self._failure_ttl if info.error else self._success_ttl

    @staticmethod
    def _is_stream_url_expired(info: StreamInfo) -> bool:
        """检查 StreamInfo 中的 stream_url 是否已过期。

        平台 CDN URL 通常包含过期时间戳参数：
        - 抖音: expire=<hex_timestamp> 或 wsTime=<hex_timestamp>
        - B站: expires=<decimal_timestamp>
        - 虎牙: wsTime=<hex_timestamp>
        当 URL 已过期或即将过期（60 秒内）时返回 True，促使调用方重新解析。
        """
        url = info.stream_url
        if not url:
            return False
        try:
            params = parse_qs(urlparse(url).query)
            now = time.time()
            for key in ('expire', 'expires', 'wsTime'):
                vals = params.get(key, [])
                if not vals:
                    continue
                raw = vals[0]
                try:
                    ts = int(raw, 16) if all(c in '0123456789abcdefABCDEF' for c in raw) and len(raw) >= 6 else int(raw)
                except (ValueError, OverflowError):
                    continue
                if now > ts - 60:
                    return True
        except Exception as exc:
            _log.debug("操作异常（已忽略）: %s", exc)
        return False

    def _cleanup(self) -> None:
        """清理过期条目。单次加锁内完成检查和删除，防止竞态误删新条目。"""
        now = time.monotonic()
        with self._lock:
            expired_keys = []
            for key, (timestamp, info) in list(self._store.items()):
                if now - timestamp > self._ttl_for(info):
                    # 二次验证：加锁后重新检查时间戳，确保未被 set() 刷新
                    existing = self._store.get(key)
                    if existing and existing[0] == timestamp:
                        expired_keys.append(key)
            for key in expired_keys:
                self._store.pop(key, None)
            if expired_keys:
                _log.debug("cleaned up %d expired cache entries", len(expired_keys))

    def _cleanup_worker(self) -> None:
        """单后台线程，复用而非每次创建。"""
        while not self._stop_event.wait(timeout=60):
            try:
                self._cleanup()
            except Exception as exc:
                _log.debug("cleanup worker error: %s", exc)

    def get(self, url: str, platform: str) -> StreamInfo | None:
        with self._lock:
            self._access_count += 1
            should_cleanup = self._access_count >= self._cleanup_every
            if should_cleanup:
                self._access_count = 0

            entry = self._store.get((url, platform))
            if entry is None:
                result: StreamInfo | None = None
            else:
                timestamp, info = entry
                if (time.monotonic() - timestamp > self._ttl_for(info)) or (
                    not info.error and self._is_stream_url_expired(info)
                ):
                    self._store.pop((url, platform), None)
                    result = None
                else:
                    result = info

        # 单后台线程定期清理，无需每次创建新线程
        # cleanup_worker 由 __init__ 启动，通过 _stop_event 控制生命周期
        return result

    def set(self, url: str, platform: str, info: StreamInfo) -> None:
        with self._lock:
            self._store[(url, platform)] = (time.monotonic(), info)
        _log.debug("cached parse result for %s (%s) ttl=%.0fs", url[:60], platform, self._ttl_for(info))


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
    """获取平台适配器列表。

    传入自定义适配器列表时返回其元组形式；
    传入 None 时返回默认的全局适配器集合。

    Args:
        adapters: 适配器可迭代对象，为 None 时使用默认适配器

    Returns:
        平台适配器元组
    """
    if adapters is None:
        return _DEFAULT_ADAPTERS
    return tuple(adapters)


def detect_platform(url: str, adapters: Iterable[PlatformAdapter] | None = None) -> str:
    """检测 URL 对应的平台标识符。

    遍历所有适配器，返回第一个能处理该 URL 的平台标识符。
    未找到匹配适配器时返回 "unknown"。

    Args:
        url: 直播间 URL
        adapters: 适配器列表，为 None 时使用默认适配器

    Returns:
        平台标识符（如 "bilibili"、"douyin"），或 "unknown"
    """
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
    """解析直播间 URL，返回流信息。

    根据 URL 的 host 路由到可能匹配的平台适配器，
    逐个调用适配器的 parse() 方法获取 StreamInfo。
    结果会按 URL 和平台缓存，避免频繁请求平台 API。

    Args:
        url: 直播间 URL
        adapters: 适配器列表，为 None 时使用默认适配器
        force_refresh: 是否强制刷新，忽略缓存

    Returns:
        StreamInfo 对象；解析失败时返回错误信息
    """
    clean_url = (url or "").strip()
    adapter_list = get_adapters(adapters)

    # Try host-based routing first to avoid probing irrelevant adapters.
    candidate_platforms = _candidate_platforms_for_url(clean_url)
    if candidate_platforms is not None:
        candidate_set = set(candidate_platforms)
        candidates = [a for a in adapter_list if a.platform in candidate_set]
        _log.debug("host routing matched %s for %s", candidate_platforms, clean_url[:60])
    else:
        candidates = list(adapter_list)
        _log.debug("no host route match, scanning all %d adapters", len(candidates))

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

    _log.warning("no adapter could handle URL: %s", clean_url[:100])
    return StreamInfo(
        platform="unknown",
        room_url=clean_url,
        is_live=False,
        error="不支持的直播间链接或直播流地址",
        error_code=ERROR_UNSUPPORTED_URL,
    )


def select_quality(info: StreamInfo | Mapping[str, object], quality_preset: str) -> tuple[str, str]:
    """根据画质预设选择直播流地址。

    按 QUALITY_PRESET_CANDIDATES 的候选顺序匹配第一个可用的流地址。
    如果预设匹配失败，回退到适配器已选中的画质。

    Args:
        info: StreamInfo 对象或包含 qualityUrls/streamUrl/selectedQuality 的字典
        quality_preset: 画质预设，如 "原画"、"高清"、"流畅"

    Returns:
        (stream_url, quality_key) 元组
    """
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
            _log.debug("selected quality '%s' for preset '%s'", quality_key, quality_preset)
            return url, quality_key
    _log.debug("no quality matched preset '%s', falling back to '%s'", quality_preset, selected_quality)
    return stream_url, selected_quality
