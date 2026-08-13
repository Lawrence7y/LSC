"""Static capabilities for the built-in platform adapters."""
from __future__ import annotations

from .models import (
    CONN_POLICY_DIRECT,
    CONN_POLICY_REUSABLE_URL,
    CONN_POLICY_SHARED_UPSTREAM,
    PlatformCapabilities,
)

_QUALITY_MAPPING = {"原画": "origin", "高清": "hd", "标清": "sd", "流畅": "ld"}

_CAPABILITIES: dict[str, PlatformCapabilities] = {
    "bilibili": PlatformCapabilities(
        platform="bilibili",
        support_level="PREVIEW",
        auth_mode="cookie",
        credential_kinds=("cookie",),
        preferred_protocols=("flv", "hls"),
        qualities=("原画", "高清", "标清", "流畅"),
        quality_mapping=_QUALITY_MAPPING,
        max_connect_concurrency=2,
        expected_ttl_seconds=120.0,
        multi_cdn=True,
        signed_url=True,
        anonymous_quality_fallback=True,
        refresh_triggers=("CDN_FORBIDDEN", "SIGNATURE_EXPIRED"),
        max_probe_candidates=8,
    ),
    "huya": PlatformCapabilities(
        platform="huya",
        support_level="PREVIEW",
        auth_mode="signed",
        # The adapter obtains signed public CDN URLs from the room page; it
        # does not depend on a persisted user Cookie.
        credential_kinds=(),
        preferred_protocols=("flv", "hls"),
        qualities=("原画", "高清", "标清", "流畅"),
        quality_mapping=_QUALITY_MAPPING,
        max_connect_concurrency=1,
        expected_ttl_seconds=120.0,
        multi_cdn=True,
        signed_url=True,
        preview_refresh_when_recording=False,
        preview_auto_reconnect=True,
        probe_profile="ingest",
        refresh_triggers=("SIGNATURE_EXPIRED",),
        max_probe_candidates=4,
        probe_timeout_sec=5.0,
    ),
    "kuaishou": PlatformCapabilities(
        platform="kuaishou",
        auth_mode="anonymous",
        credential_kinds=(),
        preferred_protocols=("hls", "flv"),
        qualities=("原画", "高清", "标清", "流畅"),
        quality_mapping=_QUALITY_MAPPING,
        max_connect_concurrency=2,
        expected_ttl_seconds=90.0,
        signed_url=True,
    ),
    "douyu": PlatformCapabilities(
        platform="douyu",
        auth_mode="signed",
        credential_kinds=(),
        preferred_protocols=("flv", "hls"),
        qualities=("原画", "高清", "标清", "流畅"),
        quality_mapping=_QUALITY_MAPPING,
        max_connect_concurrency=2,
        expected_ttl_seconds=90.0,
        signed_url=True,
        multi_cdn=True,
    ),
    "xiaohongshu": PlatformCapabilities(
        platform="xiaohongshu",
        auth_mode="anonymous",
        # The current compatibility adapter only uses public page headers;
        # a future authenticated resolver can opt into Cookie explicitly.
        credential_kinds=(),
        preferred_protocols=("hls", "flv"),
        qualities=("原画", "高清", "标清", "流畅"),
        quality_mapping=_QUALITY_MAPPING,
    ),
    "weibo": PlatformCapabilities(
        platform="weibo",
        auth_mode="anonymous",
        credential_kinds=(),
        preferred_protocols=("hls", "flv"),
        qualities=("原画", "高清", "标清", "流畅"),
        quality_mapping=_QUALITY_MAPPING,
    ),
    "douyin": PlatformCapabilities(
        platform="douyin",
        auth_mode="cookie",
        supports_anonymous=False,
        credential_kinds=("cookie",),
        preferred_protocols=("flv", "hls"),
        qualities=("原画", "高清", "标清", "流畅"),
        quality_mapping=_QUALITY_MAPPING,
        expected_ttl_seconds=60.0,
        signed_url=True,
        multi_cdn=True,
    ),
    "direct": PlatformCapabilities(
        platform="direct",
        support_level="PREVIEW",
        auth_mode="anonymous",
        credential_kinds=(),
        preferred_protocols=("flv", "hls", "ts"),
        qualities=("原画", "高清", "标清", "流畅"),
        quality_mapping=_QUALITY_MAPPING,
        connection_policy=CONN_POLICY_DIRECT,
    ),
    "generic": PlatformCapabilities(
        platform="generic",
        auth_mode="anonymous",
        credential_kinds=(),
        preferred_protocols=("hls", "flv", "ts"),
        qualities=("原画", "高清", "标清", "流畅"),
        quality_mapping=_QUALITY_MAPPING,
        connection_policy=CONN_POLICY_REUSABLE_URL,
    ),
}


def uses_ingest_probe(capabilities: PlatformCapabilities | None) -> bool:
    """Whether the real shared upstream is the media probe.

    ``probe_profile="ingest"`` is explicit. Signed single-connect platforms
    also take this path so a forgotten profile cannot reopen the same
    signature URL via ffprobe.
    """
    if capabilities is None:
        return False
    profile = str(capabilities.probe_profile or "").strip().lower()
    if profile == "ingest":
        return True
    return bool(capabilities.signed_url) and int(capabilities.max_connect_concurrency) <= 1


def get_platform_capabilities(platform: str) -> PlatformCapabilities:
    key = str(platform or "").strip().lower()
    return _CAPABILITIES.get(
        key,
        PlatformCapabilities(
            platform=key or "unknown",
            connection_policy=CONN_POLICY_SHARED_UPSTREAM,
        ),
    )


def all_platform_capabilities() -> dict[str, PlatformCapabilities]:
    return dict(_CAPABILITIES)


__all__ = [
    "all_platform_capabilities",
    "get_platform_capabilities",
    "uses_ingest_probe",
]
