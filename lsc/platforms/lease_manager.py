"""Stream lease lifecycle management.

A candidate URL is a time-limited grant from the platform, not a constant.
This manager derives an expiry from the richest signal available and tracks
refresh windows and failure budgets so recovery logic never reuses a known-
dead URL (plan §6.4, §9).
"""
from __future__ import annotations

import logging
import re
import time
import uuid
from dataclasses import dataclass
from typing import Any

from .failure import FailureKind, normalize_failure_kind
from .models import (
    PlatformCapabilities,
    StreamCandidate,
    StreamLease,
)

_log = logging.getLogger(__name__)

# Conservative lifetime used when a candidate carries NO expiry information at
# all (plan: fixed caches are only a fallback, never a first choice).
DEFAULT_REUSE_WHEN_UNKNOWN_SEC = 120.0

# Minimum fraction of the total lifetime we treat as "still comfortably valid".
_REFRESH_MARGIN_RATIO = 0.15

# Failure budget before a lease is marked revoked.
DEFAULT_MAX_LEASE_FAILURES = 3


# Signature-ish markers that hint a URL is time-limited even without explicit
# expires_at. Presence of any of these (but no expiry) forces a conservative
# refresh window so a 403 is avoided rather than recovered from.
_SIGNATURE_MARKERS = re.compile(
    r"(?:[?&](?:exp|expires|ts|t|signature|sign|wsSecret|auth_key|token|pxcode)\b=)",
    re.I,
)


@dataclass(slots=True)
class LeasePolicy:
    """Per-platform tweaks for lease lifetime decisions."""

    reuse_when_unknown_sec: float = DEFAULT_REUSE_WHEN_UNKNOWN_SEC
    max_failures: int = DEFAULT_MAX_LEASE_FAILURES


def _now() -> float:
    # Time is injected via `now` params in every public method; this helper is
    # used only by tests/standalone accidentally — better keep explicit.
    import time

    return time.monotonic()


def _derive_expiry(candidate: StreamCandidate, policy: LeasePolicy) -> float | None:
    """Highest-confidence expiry, in the same clock domain as ``issued_at``.

    Priority: platform-provided ``expires_at`` > signature hint (conservative
    window derived from policy reuse) > None (caller picks the fallback).
    """
    if candidate.expires_at is not None:
        return candidate.expires_at
    if _SIGNATURE_MARKERS.search(candidate.url):
        return policy.reuse_when_unknown_sec
    return None


def _derive_ttl(candidate: StreamCandidate, policy: LeasePolicy) -> float:
    """Return a lifetime in seconds for the current monotonic clock."""
    if candidate.expires_at is not None:
        raw = float(candidate.expires_at)
        if raw >= 100_000_000:
            return max(0.0, raw - time.time())
        return max(0.0, raw)
    if _SIGNATURE_MARKERS.search(candidate.url):
        return policy.reuse_when_unknown_sec
    return policy.reuse_when_unknown_sec


class LeaseManager:
    """Owns the lifetime of :class:`StreamLease` for one assistant loop.

    Pure logic — no network/no process. Keep instances per room or per
    resolution loop; the manager is deliberately not thread-safe (call from a
    single supervisor task).
    """

    def __init__(self, policy: LeasePolicy | None = None) -> None:
        self._policy = policy or LeasePolicy()
        self._active: dict[str, StreamLease] = {}
        self._room_generations: dict[str, int] = {}
        self._consumed_families: set[str] = set()

    # -- lifecycle ---------------------------------------------------------

    def issue(
        self,
        room_id: str,
        candidate: StreamCandidate,
        capabilities: PlatformCapabilities,
        *,
        now: float,
    ) -> StreamLease:
        """Issue a lease for ``candidate``.

        ``expires_at`` is relative (seconds from issue, in the caller's clock
        domain via ``now``) when unknown; the caller maps it to monotonic time.
        """
        lease_id = f"lease-{uuid.uuid4().hex[:12]}"
        ttl = _derive_ttl(candidate, self._policy)
        generation = self._room_generations.get(room_id, 0) + 1
        self._room_generations[room_id] = generation

        refresh_margin = max(
            capabilities.refresh_margin_sec,
            ttl * _REFRESH_MARGIN_RATIO,
        )
        # Public lease timestamps use the platform's Unix clock whenever the
        # resolver supplied an absolute expiry.  The monotonic equivalents
        # below are the values used for all scheduling decisions.  Small
        # relative values remain accepted only as a legacy adapter bridge.
        if candidate.expires_at is not None and float(candidate.expires_at) >= 100_000_000:
            refresh_at = max(
                0.0,
                float(candidate.expires_at) - refresh_margin,
            )
            refresh_offset = max(0.0, ttl - refresh_margin)
        else:
            refresh_at = max(0.0, ttl - refresh_margin)
            refresh_offset = refresh_at

        lease = StreamLease(
            lease_id=lease_id,
            room_id=room_id,
            candidate=candidate,
            issued_at=now,
            refresh_at=refresh_at,
            expires_at=(
                candidate.expires_at
                if candidate.expires_at is not None
                else ttl
            ),
            state="active",
            failure_count=0,
            deadline_mono=now + ttl,
            refresh_deadline_mono=now + refresh_offset,
            generation=generation,
        )
        self._active[lease_id] = lease
        _log.debug(
            "lease issued %s room=%s exp=%.0fs refresh=%.0fs",
            lease_id, room_id, ttl, refresh_at,
        )
        return lease

    # -- queries -----------------------------------------------------------

    def get(self, lease_id: str) -> StreamLease | None:
        return self._active.get(lease_id)

    def needs_refresh(self, lease: StreamLease, *, now: float, issued_at_epoch: float | None = None) -> bool:
        """Whether the lease is inside its refresh window or already expired.

        ``now`` is monotonic; if the caller tracked wall-clock issue, pass
        ``issued_at_epoch`` so relative expires_at can be compared.
        """
        if lease.state in {"expired", "revoked"}:
            return True
        if lease.refresh_deadline_mono is not None:
            return now >= lease.refresh_deadline_mono
        if lease.refresh_at is None:
            return False
        return (now - lease.issued_at) >= lease.refresh_at

    def is_expired(self, lease: StreamLease, *, now: float) -> bool:
        if lease.state in {"expired", "revoked"}:
            return True
        if lease.deadline_mono is not None:
            return now >= lease.deadline_mono
        if lease.expires_at is None:
            return False
        raw = float(lease.expires_at)
        ttl = raw - time.time() if raw >= 100_000_000 else raw
        return (now - lease.issued_at) >= max(0.0, ttl)

    # -- failures / recovery ----------------------------------------------

    def apply_failure(self, lease: StreamLease, kind: FailureKind | str) -> bool:
        """Record a failure; returns True when the budget is exhausted.

        Exhaustion leaves the lease ``revoked`` so the caller must re-resolve.
        Non-recoverable kinds always exhaust immediately.
        """
        kind_str = normalize_failure_kind(kind).value
        lease.failure_count += 1

        if kind_str in {"AUTH_REQUIRED", "AUTH_EXPIRED", "OFFLINE", "DISK_FULL", "PERMISSION_DENIED"}:
            lease.state = "revoked"
            _log.info("lease %s revoked (non-recoverable %s)", lease.lease_id, kind_str)
            return True

        if lease.failure_count >= self._policy.max_failures:
            lease.state = "revoked"
            _log.warning(
                "lease %s revoked after %d failures (%s)",
                lease.lease_id, lease.failure_count, kind_str,
            )
            return True

        if kind_str in {"SIGNATURE_EXPIRED", "CDN_FORBIDDEN"}:
            lease.state = "refreshing"
        return False

    def revoke(self, lease: StreamLease) -> None:
        lease.state = "revoked"

    def refresh(
        self,
        lease: StreamLease,
        candidate: StreamCandidate,
        capabilities: PlatformCapabilities,
        *,
        now: float,
    ) -> StreamLease:
        """Replace a lease atomically while isolating the old generation."""
        self.invalidate(lease, "lease refreshed")
        return self.issue(lease.room_id, candidate, capabilities, now=now)

    def invalidate(self, lease: StreamLease, reason: str = "") -> None:
        """Revoke a lease and retain a structured reason for diagnostics."""
        lease.invalidation_reason = str(reason or "")[:200]
        self.revoke(lease)

    # -- cleanup -----------------------------------------------------------

    def mark_consumed(self, lease_id: str) -> bool:
        """Mark the signed URL as opened by the real ingest process."""
        lease = self._active.get(lease_id)
        if lease is None:
            return False
        lease.consumed = True
        family = str(getattr(lease.candidate, "signature_family_id", "") or "")
        if not family:
            from .signature_family import signature_family_id

            family = signature_family_id(str(getattr(lease.candidate, "url", "") or ""))
        if family:
            self._consumed_families.add(family)
        return True

    def is_consumed(self, lease_id: str) -> bool:
        lease = self._active.get(lease_id)
        return bool(lease is not None and lease.consumed)

    def is_family_consumed(self, family_id: str) -> bool:
        family = str(family_id or "")
        return bool(family) and family in self._consumed_families

    def drop(self, lease_id: str) -> None:
        self._active.pop(lease_id, None)

    def prune(self, *, now: float) -> list[str]:
        """Remove expired/revoked leases and return their ids."""
        stale = [
            lid for lid, lease in self._active.items()
            if lease.state in {"expired", "revoked"} or self.is_expired(lease, now=now)
        ]
        for lid in stale:
            self._active.pop(lid, None)
        return stale

    def redacted_snapshot(self) -> list[dict[str, Any]]:
        return [lease.redacted() for lease in self._active.values()]
