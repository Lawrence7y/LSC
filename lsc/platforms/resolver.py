"""V2-compatible resolver built on top of the existing adapters."""
from __future__ import annotations

import logging
import time
from collections.abc import Iterable, Mapping
from dataclasses import replace
from typing import Any
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse

from .candidate_health import CandidateHealthStore, get_default_candidate_health_store
from .capabilities import get_platform_capabilities, uses_ingest_probe
from .credentials import (
    CredentialContext,
    CredentialProvider,
    CredentialStatus,
    get_default_credential_provider,
)
from .failure import (
    FailureKind,
    classify_failure,
    failure_kind_to_message,
    normalize_failure_kind,
)
from .lease_manager import LeaseManager
from .models import (
    PlatformCapabilities,
    PlatformError,
    ProbeResult,
    ResolveRequest,
    ResolveResult,
    StreamCandidate,
    StreamLease,
)
from .probe import ProbeService, select_best_candidate
from .redaction import redact_text, redact_url
from .registry import detect_platform, get_adapters, parse_stream

_log = logging.getLogger(__name__)


def _is_cancelled(token: Any | None) -> bool:
    if token is None:
        return False
    try:
        checker = getattr(token, "is_set", None)
        if callable(checker):
            return bool(checker())
        if callable(token):
            return bool(token())
    except Exception:
        return True
    return False


def _error_from_stream_info(info: Any) -> PlatformError | None:
    raw_error = str(getattr(info, "error", "") or "")
    error_code = str(getattr(info, "error_code", "") or "")
    if not raw_error and not error_code:
        return None
    from .failure import classify_failure, extract_retry_after

    # Legacy adapters do not consistently populate a human-readable error;
    # when only ``error_code`` is present, classify the stable machine code
    # before falling back to text/HTTP heuristics.  This keeps OFFLINE,
    # AUTH_REQUIRED and RESTRICTED semantics intact across the compatibility
    # bridge instead of turning them into a generic network failure.
    normalized_code = error_code.strip().upper().replace("-", "_")
    code_kinds = {
        "OFFLINE": FailureKind.OFFLINE,
        "NOT_LIVE": FailureKind.OFFLINE,
        "AUTH_REQUIRED": FailureKind.AUTH_REQUIRED,
        "AUTH_MISSING": FailureKind.AUTH_REQUIRED,
        "AUTH_EXPIRED": FailureKind.AUTH_EXPIRED,
        "AUTH_REJECTED": FailureKind.AUTH_EXPIRED,
        "RESTRICTED": FailureKind.REGION_RESTRICTED,
        "REGION_RESTRICTED": FailureKind.REGION_RESTRICTED,
        "RATE_LIMITED": FailureKind.RATE_LIMITED,
        "UPSTREAM_TIMEOUT": FailureKind.CONNECT_TIMEOUT,
        "CONNECT_TIMEOUT": FailureKind.CONNECT_TIMEOUT,
        "SIGNATURE_EXPIRED": FailureKind.SIGNATURE_EXPIRED,
        "CDN_FORBIDDEN": FailureKind.CDN_FORBIDDEN,
        "HTTP_403": FailureKind.CDN_FORBIDDEN,
        "HTTP_429": FailureKind.RATE_LIMITED,
        "HTTP_404": FailureKind.OFFLINE,
        "ROOM_NOT_FOUND": FailureKind.OFFLINE,
        "NOT_FOUND": FailureKind.OFFLINE,
        "PARSE_FAILED": FailureKind.PLATFORM_SCHEMA_CHANGED,
        "SCHEMA_CHANGED": FailureKind.PLATFORM_SCHEMA_CHANGED,
        "UNSUPPORTED_PROTOCOL": FailureKind.UNSUPPORTED_PROTOCOL,
        "UNSUPPORTED_URL": FailureKind.UNSUPPORTED_PROTOCOL,
    }
    kind = code_kinds.get(normalized_code)
    if normalized_code == "RESTRICTED":
        text_kind = classify_failure(raw_error, _status_from_raw(info))
        if text_kind in {
            FailureKind.AUTH_REQUIRED,
            FailureKind.AUTH_EXPIRED,
            FailureKind.OFFLINE,
        }:
            kind = text_kind
        else:
            kind = FailureKind.REGION_RESTRICTED
    if kind is None:
        kind = classify_failure(raw_error, _status_from_raw(info))
    elif kind == FailureKind.PLATFORM_SCHEMA_CHANGED and raw_error:
        text_kind = classify_failure(raw_error, _status_from_raw(info))
        if text_kind in {
            FailureKind.CONNECT_TIMEOUT,
            FailureKind.CONNECTION_RESET,
            FailureKind.DNS_FAILURE,
            FailureKind.RATE_LIMITED,
            FailureKind.CDN_FORBIDDEN,
        }:
            kind = text_kind
            if normalized_code == "PARSE_FAILED":
                error_code = kind.value
    category = {
        FailureKind.AUTH_REQUIRED: "AUTH",
        FailureKind.AUTH_EXPIRED: "AUTH",
        FailureKind.OFFLINE: "OFFLINE",
        FailureKind.RATE_LIMITED: "RATE_LIMIT",
        FailureKind.REGION_RESTRICTED: "RESTRICTED",
        FailureKind.CDN_FORBIDDEN: "UPSTREAM",
        FailureKind.SIGNATURE_EXPIRED: "UPSTREAM",
        FailureKind.PLATFORM_SCHEMA_CHANGED: "PARSE",
        FailureKind.UNSUPPORTED_PROTOCOL: "UNSUPPORTED",
        FailureKind.NO_MEDIA: "UPSTREAM",
        FailureKind.UNKNOWN: "PARSE",
    }.get(kind, "NETWORK")
    return PlatformError(
        code=error_code or kind.value,
        category=category,
        retryable=kind.value in {
            FailureKind.CDN_FORBIDDEN.value,
            FailureKind.SIGNATURE_EXPIRED.value,
            FailureKind.RATE_LIMITED.value,
            FailureKind.CONNECT_TIMEOUT.value,
            FailureKind.CONNECTION_RESET.value,
        },
        refresh_credentials=kind in {
            FailureKind.AUTH_REQUIRED,
            FailureKind.AUTH_EXPIRED,
        },
        invalidate_cache=kind in {
            FailureKind.CDN_FORBIDDEN,
            FailureKind.SIGNATURE_EXPIRED,
        },
        retry_after_seconds=extract_retry_after(raw_error),
        user_message=raw_error.strip() or failure_kind_to_message(kind),
    )


def _status_from_raw(info: Any) -> int | None:
    raw = getattr(info, "raw", {}) or {}
    if not isinstance(raw, dict):
        return None
    value = raw.get("http_status") or raw.get("status_code")
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _candidate_from_url(
    *,
    platform: str,
    quality: str,
    url: str,
    headers: dict[str, str],
    priority: int,
    raw: dict[str, object],
) -> StreamCandidate | None:
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        return None
    from .signature_family import signature_family_id as _signature_family_id
    from .url_policy import validate_public_url

    safe, _reason = validate_public_url(url)
    if not safe:
        return None
    default_confidence = 0.3 if platform == "generic" else 0.65
    try:
        confidence = float(raw.get("confidence", default_confidence) or default_confidence)
    except (TypeError, ValueError):
        confidence = 0.5
    parsed = urlparse(url)
    path = (parsed.path or "").lower()
    protocol = str(raw.get("protocol") or raw.get("format") or "").lower()
    if not protocol:
        protocol = "hls" if path.endswith(".m3u8") else "flv" if path.endswith(".flv") else ""
    cdn_id = str(raw.get("cdn_id") or raw.get("cdn") or "").strip()
    if not cdn_id:
        hostname = parsed.hostname or ""
        cdn_id = hostname.split(".", 1)[0] if hostname else ""
    expires_at = _extract_expiry(url, raw)
    return StreamCandidate(
        candidate_id=f"{platform}|{quality or 'selected'}|{priority}",
        url=url,
        request_headers=headers,
        quality_id=quality,
        quality_label=quality,
        protocol=protocol,
        priority=priority,
        quality_rank=max(0, priority),
        cdn_id=cdn_id,
        expires_at=expires_at,
        source_kind=(
            str(raw.get("source_kind") or "").strip().lower()
            if str(raw.get("source_kind") or "").strip().lower() in {"official", "fallback"}
            else ("fallback" if platform == "generic" else "official")
        ),
        confidence=confidence,
        raw_metadata=raw,
        signature_family_id=_signature_family_id(url),
    )


def _extract_expiry(url: str, raw: Mapping[str, object]) -> float | None:
    """Extract a platform expiry without changing the signed URL.

    Platform adapters use several spellings (``expire``, ``expires``,
    ``wsTime`` and explicit ``expires_at`` metadata).  Decimal Unix seconds
    and hexadecimal timestamps are both accepted; invalid values are ignored
    so a malformed hint never makes a candidate unusable by itself.
    """
    values: list[object] = [
        raw.get("expires_at"),
        raw.get("expiresAt"),
        raw.get("expire"),
        raw.get("expires"),
        raw.get("wsTime"),
    ]
    try:
        query = parse_qs(urlparse(url).query)
    except Exception:
        query = {}
    for key in ("expires_at", "expires", "expire", "exp", "wsTime", "timestamp"):
        values.extend(query.get(key, ()))
    now = time.time()
    for value in values:
        if value is None or value == "":
            continue
        try:
            text = str(value).strip()
            if not text:
                continue
            # Pure decimal values are Unix seconds; hex-like values are used
            # by some signed CDN URLs (usually ``wsTime``/``expire``).
            if text.isdigit():
                parsed = float(text)
            elif len(text) >= 6 and all(ch in "0123456789abcdefABCDEF" for ch in text):
                parsed = float(int(text, 16))
            else:
                parsed = float(text)
            if parsed >= 100_000_000 and parsed > now:
                return parsed
        except (TypeError, ValueError, OverflowError):
            continue
    return None


def _candidates_from_stream_info(info: Any) -> tuple[StreamCandidate, ...]:
    platform = str(getattr(info, "platform", "") or "unknown")
    headers = dict(getattr(info, "headers", {}) or {})
    raw = dict(getattr(info, "raw", {}) or {})
    quality_urls = dict(getattr(info, "quality_urls", {}) or {})
    extra_candidates = tuple(getattr(info, "candidate_urls", ()) or ())
    selected_quality = str(getattr(info, "selected_quality", "") or "")
    selected_url = str(getattr(info, "stream_url", "") or "")

    candidates: list[StreamCandidate] = []
    for priority, (quality, url) in enumerate(quality_urls.items()):
        candidate = _candidate_from_url(
            platform=platform,
            quality=str(quality),
            url=str(url),
            headers=headers,
            priority=priority,
            raw=raw,
        )
        if candidate is not None:
            candidates.append(candidate)
    for priority, entry in enumerate(extra_candidates, start=len(candidates)):
        if not isinstance(entry, Mapping):
            continue
        extra_raw = dict(raw)
        extra_raw.update({
            key: value
            for key, value in entry.items()
            if key != "url"
        })
        candidate = _candidate_from_url(
            platform=platform,
            quality=str(entry.get("quality") or entry.get("quality_id") or ""),
            url=str(entry.get("url") or ""),
            headers=headers,
            priority=priority,
            raw=extra_raw,
        )
        if candidate is not None:
            candidates.append(candidate)
    if selected_url and not any(item.url == selected_url for item in candidates):
        candidate = _candidate_from_url(
            platform=platform,
            quality=selected_quality,
            url=selected_url,
            headers=headers,
            priority=-1,
            raw=raw,
        )
        if candidate is not None:
            candidates.insert(0, candidate)
    unique: dict[str, StreamCandidate] = {}
    for candidate in candidates:
        if candidate.expires_at is not None:
            expiry = float(candidate.expires_at)
            if expiry >= 100_000_000 and expiry <= time.time():
                continue
        # Keep the first quality/line metadata while eliminating exact URL
        # duplicates produced by legacy adapters.
        unique.setdefault(candidate.fingerprint, candidate)
    return tuple(unique.values())


def _parse_with_credential_context(
    source_url: str,
    adapters: Iterable[object] | None,
    context: CredentialContext,
    force_refresh: bool,
) -> Any:
    """Use an optional context-aware adapter method without breaking legacy."""
    for adapter in get_adapters(adapters):
        can_handle = getattr(adapter, "can_handle", None)
        if not callable(can_handle) or not can_handle(source_url):
            continue
        parse_with_context = getattr(adapter, "parse_with_context", None)
        if callable(parse_with_context):
            return parse_with_context(source_url, context)
        # Third-party/legacy adapters may implement only ``parse``.  Calling
        # it directly keeps this V2 request out of the registry's URL-only
        # cache, whose key cannot distinguish accounts, proxies or credential
        # generations.  Built-in adapters inherit the same behavior from
        # BasePlatformAdapter.
        parse = getattr(adapter, "parse", None)
        if callable(parse):
            return parse(source_url)
        break
    return parse_stream(source_url, adapters, force_refresh=force_refresh)


def _merge_credential_headers(info: Any, context: CredentialContext) -> Any:
    """Bridge legacy adapters while keeping credential ownership explicit.

    Existing adapters may still build their own browser headers.  The
    provider context is merged only into the returned request headers, with
    scoped credentials taking precedence, so every normalized candidate uses
    the same controlled credential material for probe and connect.
    """
    if info is None or not context.headers:
        return info
    existing = dict(getattr(info, "headers", {}) or {})
    existing.update(dict(context.headers))
    try:
        info.headers = existing
    except (AttributeError, TypeError):
        return info
    return info


def _invalidate_credentials_on_auth_failure(
    provider: CredentialProvider | None,
    platform: str,
    account_ref: str,
    failure_kind: FailureKind,
    *,
    reason: str = "",
) -> None:
    """Invalidate only credentials proven to be rejected or expired.

    CDN 403/signature failures are deliberately excluded: those describe the
    selected media URL, not necessarily the account cookie.  Providers are
    optional for compatibility with small test/third-party implementations.
    """
    if failure_kind not in {
        FailureKind.AUTH_REQUIRED,
        FailureKind.AUTH_EXPIRED,
    }:
        return
    invalidate = getattr(provider, "invalidate", None)
    if not callable(invalidate):
        return
    try:
        invalidate(
            platform,
            account_ref=account_ref,
            reason=redact_text(reason or failure_kind.value),
        )
    except Exception as exc:
        # Credential invalidation must never mask the structured media error.
        # The provider owns its own redaction boundary; avoid logging inputs.
        _log.warning(
            "credential invalidation failed platform=%s kind=%s: %s",
            platform,
            failure_kind.value,
            redact_text(exc),
        )


def resolve_stream_v2(
    request: ResolveRequest | str,
    *,
    adapters: Iterable[object] | None = None,
    credential_provider: CredentialProvider | None = None,
) -> ResolveResult:
    """Resolve any registered platform into a V2 candidate set.

    This first implementation is intentionally a compatibility bridge:
    existing adapters remain responsible for private platform parsing, while
    every result is normalized into the V2 contract before probing.
    """
    if isinstance(request, str):
        request = ResolveRequest(source_url=request)
    source_url = str(request.source_url or "").strip()
    if not source_url:
        return ResolveResult(
            platform="unknown",
            room_url=source_url,
            error=PlatformError(
                code="UNSUPPORTED_URL",
                category="UNSUPPORTED",
                user_message="直播间地址不能为空",
            ),
        )
    if _is_cancelled(request.cancellation):
        return ResolveResult(
            platform="unknown",
            room_url=source_url,
            error=PlatformError(code="CANCELLED", category="NETWORK", retryable=False),
        )
    if (
        request.deadline_monotonic is not None
        and time.monotonic() >= request.deadline_monotonic
    ):
        return ResolveResult(
            platform="unknown",
            room_url=source_url,
            error=PlatformError(code="UPSTREAM_TIMEOUT", category="NETWORK", retryable=True),
        )
    adapter_list = tuple(get_adapters(adapters))
    platform = detect_platform(source_url, adapter_list)
    capabilities = get_platform_capabilities(platform)
    provider = credential_provider or get_default_credential_provider()
    credential_context = request.credential_context
    if credential_context is None:
        try:
            credential_context = provider.get_context(
                platform,
                request.account_ref,
                "RESOLVE",
            )
        except Exception as exc:
            safe_error = redact_text(exc)
            return ResolveResult(
                platform=platform,
                room_url=source_url,
                capabilities=capabilities,
                error=PlatformError(
                    code="CREDENTIAL_PROVIDER_ERROR",
                    category="AUTH",
                    retryable=True,
                    user_message="读取平台凭据状态失败",
                    diagnostic_context={"error": safe_error},
                ),
            )
    if isinstance(credential_context, CredentialContext):
        # Preserve provider-owned transport hints while overlaying the
        # request scope.  Deadline/cancellation are part of the same
        # per-request context so context-aware adapters can stop before a new
        # page/API request instead of only being checked at resolver edges.
        scoped_network_context = dict(credential_context.network_context or {})
        scoped_network_context.update(dict(request.network_context or {}))
        credential_context = replace(
            credential_context,
            network_context=scoped_network_context,
            deadline_monotonic=request.deadline_monotonic,
            cancellation=request.cancellation,
        )

    # Do not let a legacy adapter silently fall back to a browser/global
    # credential when the declared platform contract requires a scoped
    # credential.  Missing or revoked credentials must stop before parsing so
    # the caller can surface AUTH_REQUIRED/AUTH_EXPIRED instead of entering a
    # tight resolve/probe/reconnect loop with a known-invalid context.
    if (
        isinstance(credential_context, CredentialContext)
        and not capabilities.supports_anonymous
        and credential_context.status
        not in {CredentialStatus.AVAILABLE, CredentialStatus.EXPIRING}
    ):
        auth_kind = (
            FailureKind.AUTH_EXPIRED
            if credential_context.status
            in {CredentialStatus.INVALID, CredentialStatus.EXPIRED}
            else FailureKind.AUTH_REQUIRED
        )
        return ResolveResult(
            platform=platform,
            room_url=source_url,
            capabilities=capabilities,
            credential_status=credential_context.status,
            error=PlatformError(
                code=auth_kind.value,
                category="AUTH",
                retryable=False,
                refresh_credentials=True,
                user_message=failure_kind_to_message(auth_kind),
                diagnostic_context={
                    "credential_status": credential_context.status.value,
                    "account_ref": credential_context.account_ref,
                },
            ),
        )

    try:
        info = _parse_with_credential_context(
            source_url,
            adapter_list,
            credential_context,
            request.force_refresh,
        )
        if isinstance(credential_context, CredentialContext):
            info = _merge_credential_headers(info, credential_context)
    except Exception as exc:
        safe_error = redact_text(exc) or type(exc).__name__
        http_status = exc.code if isinstance(exc, HTTPError) else None
        kind = classify_failure(safe_error, http_status)
        if kind == FailureKind.UNKNOWN and isinstance(exc, TimeoutError):
            kind = FailureKind.CONNECT_TIMEOUT
        _log.warning(
            "resolve_stream_v2 parse failed platform=%s url=%s: %s",
            platform,
            redact_url(source_url),
            safe_error,
        )
        _invalidate_credentials_on_auth_failure(
            provider,
            platform,
            request.account_ref,
            kind,
            reason=safe_error,
        )
        category = {
            FailureKind.AUTH_REQUIRED: "AUTH",
            FailureKind.AUTH_EXPIRED: "AUTH",
            FailureKind.OFFLINE: "OFFLINE",
            FailureKind.RATE_LIMITED: "RATE_LIMIT",
            FailureKind.REGION_RESTRICTED: "RESTRICTED",
            FailureKind.CDN_FORBIDDEN: "UPSTREAM",
            FailureKind.SIGNATURE_EXPIRED: "UPSTREAM",
            FailureKind.PLATFORM_SCHEMA_CHANGED: "PARSE",
            FailureKind.UNSUPPORTED_PROTOCOL: "UNSUPPORTED",
            FailureKind.NO_MEDIA: "UPSTREAM",
        }.get(kind, "NETWORK")
        return ResolveResult(
            platform=platform,
            room_url=source_url,
            capabilities=capabilities,
            credential_status=(
                credential_context.status
                if isinstance(credential_context, CredentialContext)
                else CredentialStatus.NOT_CONFIGURED
            ),
            error=PlatformError(
                code=kind.value,
                category=category,
                retryable=kind in {
                    FailureKind.CONNECT_TIMEOUT,
                    FailureKind.CONNECTION_RESET,
                    FailureKind.RATE_LIMITED,
                },
                user_message=(
                    failure_kind_to_message(kind)
                    if kind != FailureKind.UNKNOWN
                    else "平台解析失败，请稍后重试"
                ),
                diagnostic_context={"error": safe_error},
            ),
        )
    if _is_cancelled(request.cancellation):
        return ResolveResult(
            platform=platform,
            room_url=source_url,
            error=PlatformError(code="CANCELLED", category="NETWORK", retryable=False),
        )
    if (
        request.deadline_monotonic is not None
        and time.monotonic() >= request.deadline_monotonic
    ):
        return ResolveResult(
            platform=platform,
            room_url=source_url,
            error=PlatformError(code="UPSTREAM_TIMEOUT", category="NETWORK", retryable=True),
        )
    resolved_platform = str(getattr(info, "platform", "") or platform)
    candidates = _candidates_from_stream_info(info)
    # Consume bounded historical health without exposing signed URLs.  The
    # metadata is carried through the existing candidate contract and remains
    # invisible to redacted API/event payloads.
    candidates = get_default_candidate_health_store().enrich(
        candidates,
        platform=resolved_platform,
        account_ref=request.account_ref,
        network_context=request.network_context,
    )
    error = _error_from_stream_info(info)
    if error is not None and error.refresh_credentials:
        _invalidate_credentials_on_auth_failure(
            provider,
            resolved_platform,
            request.account_ref,
            FailureKind.AUTH_EXPIRED
            if error.code == FailureKind.AUTH_EXPIRED.value
            else FailureKind.AUTH_REQUIRED,
            reason=error.code,
        )
    if not candidates and error is None:
        error = PlatformError(
            code="NO_STREAM_CANDIDATE",
            category="PARSE",
            retryable=True,
            user_message="未找到可用的直播流候选",
        )
    live_status = "LIVE" if bool(getattr(info, "is_live", False)) else "UNKNOWN"
    if error is not None and error.category == "OFFLINE":
        live_status = "OFFLINE"
    raw = dict(getattr(info, "raw", {}) or {})
    canonical_room_id = str(
        raw.get("room_id")
        or raw.get("roomId")
        or raw.get("roomid")
        or ""
    )
    return ResolveResult(
        platform=resolved_platform,
        room_url=source_url,
        canonical_room_id=canonical_room_id,
        room_title=str(getattr(info, "title", "") or ""),
        anchor_name=str(getattr(info, "streamer", "") or ""),
        live_status=live_status,
        capabilities=get_platform_capabilities(resolved_platform),
        candidates=candidates,
        credential_status=credential_context.status
        if isinstance(credential_context, CredentialContext)
        else CredentialStatus.NOT_CONFIGURED,
        resolved_at=time.time(),
        recommended_refresh_at=(
            time.time() + capabilities.expected_ttl_seconds
            if capabilities.expected_ttl_seconds
            else None
        ),
        error=error,
    )


def resolve_candidates(
    source_url: str,
    *,
    force_refresh: bool = False,
    adapters: Iterable[object] | None = None,
    credential_provider: CredentialProvider | None = None,
) -> ResolveResult:
    return resolve_stream_v2(
        ResolveRequest(source_url=source_url, force_refresh=force_refresh),
        adapters=adapters,
        credential_provider=credential_provider,
    )


def limit_probe_candidates(
    candidates: Iterable[StreamCandidate],
    capabilities: PlatformCapabilities | None,
) -> tuple[StreamCandidate, ...]:
    """Cap probe fan-out so signed multi-CDN platforms are not exhausted.

    ``max_probe_candidates`` 0 means no cap. Unique CDN lines are preferred
    so a small budget still samples alternate hosts.
    """
    items = tuple(candidates)
    try:
        limit = int(getattr(capabilities, "max_probe_candidates", 0) or 0)
    except (TypeError, ValueError):
        limit = 0
    if limit < 1 or len(items) <= limit:
        return items
    selected: list[StreamCandidate] = []
    seen_cdn: set[str] = set()
    for item in items:
        if len(selected) >= limit:
            break
        cdn = str(getattr(item, "cdn_id", "") or "")
        if cdn and cdn in seen_cdn:
            continue
        selected.append(item)
        if cdn:
            seen_cdn.add(cdn)
    if len(selected) < limit:
        selected_ids = {item.candidate_id for item in selected}
        for item in items:
            if len(selected) >= limit:
                break
            if item.candidate_id in selected_ids:
                continue
            selected.append(item)
    return tuple(selected)


def probe_candidates(
    candidates: Iterable[StreamCandidate],
    *,
    ffprobe_path: str = "ffprobe",
    timeout_sec: float = 8.0,
    max_concurrency: int = 3,
    request_id: str = "",
    deadline_monotonic: float | None = None,
    cancellation: Any | None = None,
    network_context: Mapping[str, object] | None = None,
    platform: str = "",
    account_ref: str = "default",
    health_store: CandidateHealthStore | None = None,
) -> dict[str, ProbeResult]:
    """Probe candidates with bounded concurrency."""
    items = tuple(candidates)
    results = ProbeService(ffprobe_path).probe_candidates(
        items,
        timeout_sec=timeout_sec,
        max_concurrency=max_concurrency,
        request_id=request_id,
        deadline_monotonic=deadline_monotonic,
        cancellation=cancellation,
        network_context=network_context,
    )
    # Callers that know the platform opt into recording.  Keeping the
    # compatibility function side-effect free for anonymous third-party tests
    # avoids cross-test/global state while production V2 paths pass platform.
    if platform:
        store = health_store or get_default_candidate_health_store()
        for candidate in items:
            result = results.get(candidate.candidate_id)
            if result is not None:
                store.record(
                    candidate,
                    result,
                    platform=platform,
                    account_ref=account_ref,
                    network_context=network_context,
                )
    return results


def select_stream_lease(
    result: ResolveResult,
    probe_results: Mapping[str, ProbeResult],
    *,
    room_id: str,
    lease_manager: LeaseManager,
    now: float | None = None,
    requested_quality: str = "",
    credential_provider: CredentialProvider | None = None,
    account_ref: str = "default",
) -> StreamLease | None:
    """Select the best probed candidate and issue a time-bounded lease."""
    if not result.capabilities:
        return None
    provider = credential_provider or get_default_credential_provider()
    for probe in probe_results.values():
        kind = normalize_failure_kind(getattr(probe, "failure_kind", ""))
        _invalidate_credentials_on_auth_failure(
            provider,
            result.platform,
            account_ref,
            kind,
            reason=probe.failure_detail,
        )
    candidate = select_best_candidate(
        result.candidates,
        probe_results,
        capabilities=result.capabilities,
        requested_quality=requested_quality,
    )
    if candidate is None:
        return None
    lease = lease_manager.issue(
        room_id,
        candidate,
        result.capabilities,
        now=time.monotonic() if now is None else now,
    )
    # A probe can race a short-lived signed URL.  Never hand an already
    # expired candidate to the connection layer, even if its media probe
    # completed successfully just before the expiry boundary.
    lease_now = time.monotonic() if now is None else now
    if lease_manager.is_expired(lease, now=lease_now):
        lease_manager.drop(lease.lease_id)
        return None
    probe = probe_results.get(candidate.candidate_id)
    if probe is not None:
        lease.probe_summary = {
            "first_packet_ms": probe.first_packet_ms,
            "probe_duration_ms": probe.probe_duration_ms,
            "protocol": probe.protocol,
            "container": probe.container,
            "has_video": probe.has_video,
            "has_audio": probe.has_audio,
            "timestamp_ok": probe.timestamp_ok,
            "redirect_count": probe.redirect_count,
            "retry_after_seconds": probe.retry_after_seconds,
            "server_id": probe.server_id,
            "cdn_id": probe.cdn_id,
        }
    return lease


def _candidate_signature_family_id(candidate: StreamCandidate) -> str:
    family = str(getattr(candidate, "signature_family_id", "") or "")
    if family:
        return family
    from .signature_family import signature_family_id as _signature_family_id

    return _signature_family_id(str(getattr(candidate, "url", "") or ""))


def _ingest_candidate_blocked(
    result: ResolveResult,
    candidate: StreamCandidate,
    room_id: str,
    *,
    lease_manager: LeaseManager | None = None,
) -> bool:
    family = _candidate_signature_family_id(candidate)
    if lease_manager is not None and family and lease_manager.is_family_consumed(family):
        return True
    if str(result.platform or "").strip().lower() != "huya":
        return False
    from .huya import _is_cdn_blacklisted

    return _is_cdn_blacklisted(
        str(candidate.cdn_id or ""),
        room_key=str(result.room_url or room_id or ""),
    )


def select_ingest_lease(
    result: ResolveResult,
    *,
    room_id: str,
    lease_manager: LeaseManager,
    now: float | None = None,
    requested_quality: str = "",
) -> StreamLease | None:
    """Issue a lease without opening the signed URL for a media probe."""
    if not result.capabilities:
        return None
    candidates = [
        item
        for item in limit_probe_candidates(result.candidates, result.capabilities)
        if item is not None and str(getattr(item, "url", "") or "").startswith(("http://", "https://"))
        and not _ingest_candidate_blocked(
            result, item, room_id, lease_manager=lease_manager
        )
    ]
    if not candidates:
        candidates = [
            item
            for item in result.candidates
            if item is not None
            and str(getattr(item, "url", "") or "").startswith(("http://", "https://"))
            and not lease_manager.is_family_consumed(_candidate_signature_family_id(item))
        ]
    if requested_quality:
        wanted = str(requested_quality).strip().lower()
        matching = [
            item
            for item in candidates
            if str(item.quality_id or "").strip().lower() == wanted
            or str(item.quality_label or "").strip().lower() == wanted
        ]
        if matching:
            candidates = matching
    candidates.sort(
        key=lambda item: (
            str(item.cdn_id or "").strip().lower() == "al",
            -float(item.confidence or 0.0),
            int(item.priority or 0),
        )
    )
    if not candidates:
        return None
    stamp = time.monotonic() if now is None else now
    lease = lease_manager.issue(
        room_id,
        candidates[0],
        result.capabilities,
        now=stamp,
    )
    lease.probe_summary = {
        "mode": "ingest",
        "has_video": False,
        "has_audio": False,
        "timestamp_ok": False,
        "cdn_id": candidates[0].cdn_id,
    }
    lease.consumed = False
    return lease


def resolve_playable_lease(
    result: ResolveResult,
    *,
    room_id: str,
    lease_manager: LeaseManager,
    probes: Mapping[str, ProbeResult] | None = None,
    now: float | None = None,
    requested_quality: str = "",
    probe_kwargs: Mapping[str, Any] | None = None,
) -> StreamLease | None:
    """Select a lease, skipping remote probes for ingest-probe platforms."""
    if uses_ingest_probe(result.capabilities):
        return select_ingest_lease(
            result,
            room_id=room_id,
            lease_manager=lease_manager,
            now=now,
            requested_quality=requested_quality,
        )
    if probes is None:
        limited = limit_probe_candidates(result.candidates, result.capabilities)
        probes = probe_candidates(limited, **dict(probe_kwargs or {}))
    return select_stream_lease(
        result,
        probes,
        room_id=room_id,
        lease_manager=lease_manager,
        now=now,
        requested_quality=requested_quality,
    )


__all__ = [
    "limit_probe_candidates",
    "probe_candidates",
    "resolve_candidates",
    "resolve_playable_lease",
    "resolve_stream_v2",
    "select_ingest_lease",
    "select_stream_lease",
]
