"""Platform-owned recovery hints used by generic runtime code."""
from __future__ import annotations

from urllib.parse import urlparse

from .capabilities import get_platform_capabilities, uses_ingest_probe
from .failure import FailureKind, classify_failure, is_recoverable_failure


def should_force_refresh_when_recording(stream_info: object | None) -> bool:
    """Whether preview must obtain a fresh lease while recording is active."""
    platform = str(getattr(stream_info, "platform", "") or "")
    return get_platform_capabilities(platform).preview_refresh_when_recording


def should_force_recovery(stream_info: object | None, error: object) -> bool:
    """Return the adapter policy decision for an otherwise non-recoverable exit."""
    platform = str(getattr(stream_info, "platform", "") or "")
    capabilities = get_platform_capabilities(platform)
    if not capabilities.preview_refresh_when_recording:
        return False
    text = str(error or "")
    kind = classify_failure(text)
    # ``code=0`` is a common FFmpeg symptom of a Huya CDN/signature exit;
    # classify it centrally instead of making the generic runtime inspect
    # platform-specific stderr text.  Keep the old wording as a bounded
    # compatibility fallback for adapters that emit no typed signal.
    return is_recoverable_failure(kind) or any(
        marker in text.lower()
        for marker in ("abnormal", "异常退出")
    )


def recovery_action(
    stream_info: object | None,
    error: object,
    *,
    saw_first_ts: bool = False,
) -> str:
    """Choose a platform-capability recovery action for a media failure."""
    platform = str(getattr(stream_info, "platform", "") or "")
    capabilities = get_platform_capabilities(platform)
    kind = classify_failure(str(error or ""))
    text = str(error or "")
    lowered = text.lower()
    ingest = uses_ingest_probe(capabilities)
    if (
        kind is FailureKind.PREVIEW_ENCODER_FAILURE
        or "preview stdout stalled" in lowered
    ):
        return "restart_preview_sink"
    if kind is FailureKind.RECORDING_SINK_FAILURE:
        return "restart_recording_sink"
    if kind is FailureKind.OFFLINE:
        return "offline"
    family_kinds = {
        FailureKind.AUTH_EXPIRED,
        FailureKind.SIGNATURE_EXPIRED,
        FailureKind.CDN_FORBIDDEN,
    }
    early_exit_kinds = {
        FailureKind.CONNECTION_RESET,
        FailureKind.NO_MEDIA,
        FailureKind.CONNECT_TIMEOUT,
    }
    if ingest and (kind in family_kinds or (not saw_first_ts and kind in early_exit_kinds)):
        return "invalidate_family"
    if kind in {
        FailureKind.CONNECT_TIMEOUT,
        FailureKind.CONNECTION_RESET,
        FailureKind.DNS_FAILURE,
    }:
        return "quarantine_cdn"
    return "none"


def mark_failed_candidate(
    stream_info: object | None,
    error: object,
    *,
    room_id: str = "",
    network_profile: str = "",
    saw_first_ts: bool = False,
) -> bool:
    """Give the owning adapter a chance to quarantine a failed CDN line."""
    if stream_info is None:
        return False
    platform = str(getattr(stream_info, "platform", "") or "")
    capabilities = get_platform_capabilities(platform)
    kind = classify_failure(str(error or ""))
    action = recovery_action(stream_info, error, saw_first_ts=saw_first_ts)
    if action in {"restart_preview_sink", "restart_recording_sink"}:
        return False
    if action == "invalidate_family":
        kind = FailureKind.SIGNATURE_EXPIRED
    url = str(getattr(stream_info, "stream_url", "") or "")
    host = urlparse(url).netloc.lower()
    cdn_name = host.split(".")[0] if host else ""
    raw = getattr(stream_info, "raw", {}) or {}
    if isinstance(raw, dict):
        cdn_name = str(raw.get("candidate_cdn_id") or cdn_name or "")
        room_id = room_id or str(raw.get("room_id") or "")
        raw_network_context = raw.get("network_context", {})
        raw_profile = raw.get("network_profile")
        if not raw_profile and isinstance(raw_network_context, dict):
            raw_profile = raw_network_context.get("profile")
        network_profile = network_profile or str(raw_profile or "")

    health_recorded = False
    if isinstance(raw, dict) and raw.get("v2") and raw.get("candidate_id"):
        # A real ingest failure is stronger evidence than the earlier probe
        # success. Feed it into candidate scoring so forced recovery prefers
        # another CDN/line instead of repeatedly selecting the same lease.
        from .candidate_health import get_default_candidate_health_store
        from .models import ProbeResult, StreamCandidate

        candidate = StreamCandidate(
            candidate_id=str(raw.get("candidate_id") or ""),
            url="",
            quality_id=str(raw.get("candidate_quality_id") or ""),
            protocol=str(raw.get("candidate_protocol") or ""),
            cdn_id=cdn_name,
        )
        get_default_candidate_health_store().record(
            candidate,
            ProbeResult(
                candidate_id=candidate.candidate_id,
                failure_kind=kind.value,
                failure_detail=str(error or ""),
            ),
            platform=platform,
            account_ref=str(raw.get("account_ref") or "default"),
            network_context=(
                raw.get("network_context")
                if isinstance(raw.get("network_context"), dict)
                else None
            ),
        )
        health_recorded = True

    if action == "invalidate_family":
        return False
    if action == "quarantine_cdn":
        if not cdn_name or not capabilities.multi_cdn:
            return health_recorded
        if platform == "huya":
            from .huya import mark_cdn_bad

            try:
                scoped_room_key = str(
                    getattr(stream_info, "room_url", "") or room_id or ""
                )
                mark_cdn_bad(
                    cdn_name,
                    room_key=scoped_room_key,
                    network_profile=network_profile,
                )
            except TypeError:
                # Keep test/plugin monkeypatches and older adapter policy shims
                # compatible while the scoped signature rolls out.
                mark_cdn_bad(cdn_name)
            return True
        return health_recorded
    if kind not in {
        FailureKind.CDN_FORBIDDEN,
        FailureKind.SIGNATURE_EXPIRED,
        FailureKind.CONNECTION_RESET,
        FailureKind.CONNECT_TIMEOUT,
    }:
        return False
    return health_recorded


__all__ = [
    "mark_failed_candidate",
    "recovery_action",
    "should_force_recovery",
    "should_force_refresh_when_recording",
]
