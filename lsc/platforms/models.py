"""V2 platform data models.

These models replace the single-URL ``StreamInfo`` contract with a richer
candidate/lease model. Migration note: legacy adapters still return
:class:`~lsc.platforms.base.StreamInfo`; ``stream_info_to_candidate`` bridges
the two until every adapter returns a candidate set.
"""
from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from .credentials import CredentialContext, CredentialStatus

# Connection policy strings (see PlatformCapabilities.connection_policy).
CONN_POLICY_SHARED_UPSTREAM = "shared_upstream"
CONN_POLICY_INDEPENDENT_LEASE = "independent_lease"
CONN_POLICY_REUSABLE_URL = "reusable_url"
CONN_POLICY_DIRECT = "direct"

_DEFAULT_ERROR_RECOVERY = {
    "403": "invalidate_lease_and_probe_next_candidate",
    "410": "refresh_lease",
    "429": "honor_retry_after_then_backoff",
    "5xx": "bounded_backoff_and_probe_next_candidate",
    "TIMEOUT": "retry_same_lease_with_budget",
    "OFFLINE": "poll_room_without_reconnect_storm",
}


@dataclass(frozen=True, slots=True)
class PlatformCapabilities:
    """Static, declarative capabilities a platform adapter must expose."""

    platform: str
    support_level: str = "EXPERIMENTAL"  # EXPERIMENTAL | PREVIEW | STABLE | DEGRADED | DISABLED
    auth_mode: str = "anonymous"          # anonymous | cookie | signed
    supports_anonymous: bool = True
    preferred_protocols: tuple[str, ...] = ("hls", "flv")
    preferred_video_codecs: tuple[str, ...] = ("avc1", "hev1")
    connection_policy: str = CONN_POLICY_SHARED_UPSTREAM
    max_resolve_concurrency: int = 1
    max_connect_concurrency: int = 1
    resolve_timeout_sec: float = 12.0
    probe_timeout_sec: float = 8.0
    refresh_margin_sec: float = 10.0
    credential_kinds: tuple[str, ...] = ()
    qualities: tuple[str, ...] = ()
    multi_cdn: bool = False
    signed_url: bool = False
    expected_ttl_seconds: float | None = None
    refresh_triggers: tuple[str, ...] = ()
    probe_profile: str = "default"
    quality_mapping: Mapping[str, str] = field(default_factory=dict)
    max_probe_candidates: int = 0
    error_recovery: Mapping[str, str] = field(
        default_factory=lambda: dict(_DEFAULT_ERROR_RECOVERY)
    )
    # Adapter-owned policy hints consumed by generic preview/recovery code;
    # platform names must not leak into orchestrator/handler branches.
    anonymous_quality_fallback: bool = False
    preview_refresh_when_recording: bool = False
    preview_auto_reconnect: bool = True

    @property
    def platform_id(self) -> str:
        return self.platform

    @property
    def protocols(self) -> tuple[str, ...]:
        return self.preferred_protocols

    def __post_init__(self) -> None:
        if self.support_level not in {
            "EXPERIMENTAL", "PREVIEW", "STABLE", "DEGRADED", "DISABLED",
        }:
            raise ValueError(f"unknown support_level: {self.support_level}")
        if self.connection_policy not in {
            CONN_POLICY_SHARED_UPSTREAM,
            CONN_POLICY_INDEPENDENT_LEASE,
            CONN_POLICY_REUSABLE_URL,
            CONN_POLICY_DIRECT,
        }:
            raise ValueError(f"unknown connection_policy: {self.connection_policy}")
        if not self.supports_anonymous and self.auth_mode == "anonymous":
            raise ValueError("anonymous auth_mode with supports_anonymous=False")
        if self.max_resolve_concurrency < 1 or self.max_connect_concurrency < 1:
            raise ValueError("concurrency limits must be positive")
        if self.max_probe_candidates < 0:
            raise ValueError("max_probe_candidates must be >= 0")
        if self.resolve_timeout_sec <= 0 or self.probe_timeout_sec <= 0:
            raise ValueError("timeouts must be positive")


@dataclass(frozen=True, slots=True)
class StreamCandidate:
    """A single playable candidate produced by a resolver.

    ``candidate_id`` must be stable and must NOT embed the full signed URL.
    ``credential_ref`` is a runtime pointer — secrets never live here.
    Protocol/container/codec fields may be empty at resolve time and are
    filled by the probe stage.
    """

    candidate_id: str
    url: str
    request_headers: Mapping[str, str] = field(default_factory=dict)
    credential_ref: str = ""
    quality_id: str = ""
    quality_label: str = ""
    protocol: str = ""                    # hls | flv | rtmp | ...
    container: str = ""                   # mpegts | flv | mp4 | ...
    video_codec: str = ""                 # avc1 | hev1 | ...
    audio_codec: str = ""                 # mp4a | opus | ...
    cdn_id: str = ""
    # Platform-provided expiry is a Unix timestamp.  Legacy adapters may
    # still omit it (or provide a small relative value); LeaseManager keeps
    # its monotonic deadline separately during the migration bridge.
    expires_at: float | None = None
    max_connections: int | None = None
    priority: int = 0
    quality_rank: int = 0
    line_id: str = ""
    proxy_policy: str = "direct"
    resolved_at: float = field(default_factory=lambda: __import__("time").time())
    source_kind: str = "fallback"
    confidence: float = 0.0
    raw_metadata: Mapping[str, object] = field(default_factory=dict)
    signature_family_id: str = ""

    def redacted(self) -> dict[str, object]:
        """Serializable view with the URL removed (logs/broadcast safe)."""
        return {
            "candidate_id": self.candidate_id,
            "quality_id": self.quality_id,
            "quality_label": self.quality_label,
            "protocol": self.protocol,
            "container": self.container,
            "video_codec": self.video_codec,
            "audio_codec": self.audio_codec,
            "cdn_id": self.cdn_id,
            "expires_at": self.expires_at,
            "priority": self.priority,
            "quality_rank": self.quality_rank,
            "line_id": self.line_id,
            "proxy_policy": self.proxy_policy,
            "resolved_at": self.resolved_at,
            "source_kind": self.source_kind,
            "confidence": self.confidence,
            "credential_ref": self.credential_ref,
            "signature_family_id": self.signature_family_id,
        }

    @property
    def safe_url(self) -> str:
        from .redaction import redact_url

        return redact_url(self.url)

    @property
    def headers(self) -> Mapping[str, str]:
        """Compatibility alias used by the resolver/probe contract."""
        return self.request_headers

    @property
    def fingerprint(self) -> str:
        """Stable non-secret identity for deduplication and diagnostics."""
        # Do not use ``safe_url`` here.  Redaction intentionally replaces
        # signed query values, so two different live leases could otherwise
        # collapse into one candidate before probing.  The raw URL is hashed
        # only in memory and the digest is the only value exposed to logs or
        # diagnostics; credentials never leave the process in clear text.
        from urllib.parse import urldefrag

        canonical_url, _fragment = urldefrag(self.url)
        value = "|".join((canonical_url, self.protocol, self.quality_id, self.cdn_id))
        return hashlib.sha256(value.encode("utf-8", "ignore")).hexdigest()[:20]


@dataclass(frozen=True, slots=True)
class ProbeResult:
    """Outcome of probing a candidate with real media."""

    candidate_id: str
    reachable: bool = False
    first_packet_ms: int = -1
    http_status: int | None = None
    protocol: str = ""
    container: str = ""
    video_codec: str = ""
    audio_codec: str = ""
    has_video: bool = False
    has_audio: bool = False
    timestamp_ok: bool = False
    failure_kind: str = ""               # empty = no failure recorded
    failure_detail: str = ""
    duration_ms: int = -1
    bitrate: int = -1
    redirect_count: int = 0
    retry_after_seconds: float | None = None
    probe_duration_ms: int = -1
    read_bytes: int = 0
    server_id: str = ""
    cdn_id: str = ""

    @property
    def ok(self) -> bool:
        """A candidate is selectable only when it carries real video."""
        return (
            self.reachable
            and self.has_video
            and self.timestamp_ok
            and not self.failure_kind
        )

    @property
    def success(self) -> bool:
        """Specification-compatible alias for ``ok``."""
        return self.ok

    @property
    def failure_code(self) -> str:
        """Specification-compatible alias for the typed failure kind."""
        return self.failure_kind

    @property
    def first_byte_ms(self) -> int:
        return self.first_packet_ms

    @property
    def timestamp_progressed(self) -> bool:
        return self.timestamp_ok


@dataclass(frozen=True, slots=True)
class ProbeRequest:
    candidate: StreamCandidate
    timeout_sec: float = 8.0
    ffprobe_path: str = "ffprobe"
    request_id: str = ""
    deadline_monotonic: float | None = None
    network_context: Mapping[str, object] = field(default_factory=dict)
    cancellation: Any | None = None


@dataclass(slots=True)
class StreamLease:
    """A time-bounded right to consume one candidate.

    Mutable: state/failure_count are updated by the lease manager.
    """

    lease_id: str
    room_id: str
    candidate: StreamCandidate
    issued_at: float
    refresh_at: float | None = None
    expires_at: float | None = None
    state: str = "active"                 # active | refreshing | expired | revoked
    failure_count: int = 0
    # Internal monotonic deadlines.  These are the only values used for
    # scheduling; public expires_at/refresh_at remain Unix timestamps when an
    # absolute platform expiry is available.
    deadline_mono: float | None = None
    refresh_deadline_mono: float | None = None
    generation: int = 0
    invalidation_reason: str = ""
    probe_summary: Mapping[str, object] = field(default_factory=dict)
    consumed: bool = False

    def redacted(self) -> dict[str, object]:
        return {
            "lease_id": self.lease_id,
            "room_id": self.room_id,
            "candidate": self.candidate.redacted(),
            "issued_at": self.issued_at,
            "refresh_at": self.refresh_at,
            "expires_at": self.expires_at,
            "state": self.state,
            "failure_count": self.failure_count,
            "generation": self.generation,
            "invalidation_reason": self.invalidation_reason,
            "probe_summary": dict(self.probe_summary),
            "consumed": self.consumed,
        }

    @property
    def room_session_id(self) -> str:
        return self.room_id

    @property
    def candidate_snapshot(self) -> dict[str, object]:
        return self.candidate.redacted()


@dataclass(frozen=True, slots=True)
class ResolveRequest:
    source_url: str
    requested_quality: str = ""
    credential_context: CredentialContext | None = None
    account_ref: str = "default"
    force_refresh: bool = False
    request_id: str = ""
    deadline_monotonic: float | None = None
    network_context: Mapping[str, object] = field(default_factory=dict)
    cancellation: Any | None = None


@dataclass(frozen=True, slots=True)
class PlatformError:
    code: str
    category: str = "INTERNAL"
    retryable: bool = False
    retry_after_seconds: float | None = None
    refresh_credentials: bool = False
    invalidate_cache: bool = False
    user_message: str = ""
    diagnostic_context: Mapping[str, object] = field(default_factory=dict)

    def redacted(self) -> dict[str, object]:
        from .redaction import redact_mapping, redact_text

        return {
            "code": self.code,
            "category": self.category,
            "retryable": self.retryable,
            "retry_after_seconds": self.retry_after_seconds,
            "refresh_credentials": self.refresh_credentials,
            "invalidate_cache": self.invalidate_cache,
            "user_message": redact_text(self.user_message),
            "diagnostic_context": redact_mapping(self.diagnostic_context),
        }


@dataclass(frozen=True, slots=True)
class ResolveResult:
    platform: str
    room_url: str
    canonical_room_id: str = ""
    room_title: str = ""
    anchor_name: str = ""
    live_status: str = "UNKNOWN"
    capabilities: PlatformCapabilities | None = None
    candidates: tuple[StreamCandidate, ...] = ()
    credential_status: CredentialStatus | None = None
    resolved_at: float = field(default_factory=lambda: __import__("time").time())
    recommended_refresh_at: float | None = None
    warnings: tuple[str, ...] = ()
    error: PlatformError | None = None

    @property
    def ok(self) -> bool:
        return bool(self.candidates) and self.error is None

    @property
    def platform_id(self) -> str:
        """Specification-compatible alias for the canonical platform id."""
        return self.platform

    @property
    def capabilities_snapshot(self) -> PlatformCapabilities | None:
        """Spec-compatible alias; capabilities remains the legacy name."""
        return self.capabilities


# ── Legacy bridge ──────────────────────────────────────────────────────────


def stream_info_to_candidate(
    info: Any,
    *,
    candidate_id: str | None = None,
    priority: int = 0,
) -> StreamCandidate | None:
    """Convert a legacy :class:`StreamInfo` into a best-effort candidate.

    Returns ``None`` when there is nothing playable (error result or an empty
    stream URL). The candidate uses the legacy selected URL and headers; the
    probe stage is expected to fill protocol/container/codec fields.
    """
    if info is None:
        return None
    stream_url = str(getattr(info, "stream_url", "") or "")
    if not stream_url:
        return None

    platform = str(getattr(info, "platform", "") or "unknown")
    quality_label = str(getattr(info, "selected_quality", "") or "")
    from .signature_family import signature_family_id as _family_id

    return StreamCandidate(
        candidate_id=candidate_id or f"{platform}|{quality_label or 'legacy'}",
        url=stream_url,
        request_headers=dict(getattr(info, "headers", {}) or {}),
        quality_label=quality_label,
        quality_id=quality_label,
        protocol="",                                  # filled by probe
        container="",
        priority=priority,
        raw_metadata={
            "room_url": str(getattr(info, "room_url", "") or ""),
            "title": str(getattr(info, "title", "") or ""),
            "streamer": str(getattr(info, "streamer", "") or ""),
            "raw": dict(getattr(info, "raw", {}) or {}),
        },
        signature_family_id=_family_id(stream_url),
    )


def candidate_to_stream_info(candidate: StreamCandidate) -> dict[str, Any]:
    """Best-effort reverse bridge: candidate -> legacy GUI dict shape."""
    return {
        "streamUrl": candidate.url,
        "selectedQuality": candidate.quality_label,
        "availableQualities": [candidate.quality_label] if candidate.quality_label else [],
        "qualityUrls": {candidate.quality_label: candidate.url} if candidate.quality_label else {},
        "protocol": candidate.protocol,
        "container": candidate.container,
        "videoCodec": candidate.video_codec,
        "audioCodec": candidate.audio_codec,
        "cdnId": candidate.cdn_id,
    }


def resolve_result_to_stream_info(result: ResolveResult) -> Any:
    """将 V2 解析结果转换为旧 ``StreamInfo`` 门面。

    GUI 与旧 RecordingService 仍消费 ``StreamInfo``。该桥只在调用方已
    通过 V2 feature gate 后使用，候选 URL/headers 保持原样供后续连接，
    错误则统一映射为旧字段，避免门面重新调用平台私有 ``parse``。
    导入放在函数内部以保持 models 与 legacy base 模块的低耦合。
    """
    from .base import StreamInfo

    candidates = tuple(result.candidates or ())
    selected = candidates[0] if candidates else None
    quality_urls = {
        (candidate.quality_label or candidate.quality_id or candidate.candidate_id): candidate.url
        for candidate in candidates
    }
    error = result.error
    return StreamInfo(
        platform=result.platform,
        room_url=result.room_url,
        stream_url=selected.url if selected else "",
        title=result.room_title,
        streamer=result.anchor_name,
        is_live=str(result.live_status or "").upper() == "LIVE",
        quality_urls=quality_urls,
        selected_quality=(selected.quality_label or selected.quality_id) if selected else "",
        headers=dict(selected.request_headers) if selected else {},
        raw={
            "canonical_room_id": result.canonical_room_id,
            "candidate_count": len(candidates),
            "candidate_ids": [candidate.candidate_id for candidate in candidates],
            "credential_status": (
                result.credential_status.value
                if result.credential_status is not None
                else ""
            ),
        },
        error=error.user_message if error else "",
        error_code=error.code if error else "",
        category=error.category if error else "",
    )
