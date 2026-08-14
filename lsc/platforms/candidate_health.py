"""Bounded, redaction-safe health history for stream candidates.

The store is intentionally process-local.  It is not a credential cache and
never keys on a signed URL; platform, account, network profile, CDN and
protocol are the only identity dimensions.  A later persistence layer can
snapshot this structure without changing resolver or probe contracts.
"""
from __future__ import annotations

import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass, replace

from .models import ProbeResult, StreamCandidate


@dataclass(slots=True)
class CandidateHealthSnapshot:
    successes: int = 0
    failures: int = 0
    consecutive_failures: int = 0
    last_failure_kind: str = ""
    last_latency_ms: int = -1
    last_seen_at: float = 0.0
    score: float = 0.0

    @property
    def failure_rate(self) -> float:
        total = self.successes + self.failures
        return self.failures / total if total else 0.0


def _safe_profile(network_context: Mapping[str, object] | None) -> str:
    context = dict(network_context or {})
    return str(
        context.get("profile")
        or context.get("proxy_url")
        or context.get("http_proxy")
        or "direct"
    ).strip().lower()[:160]


class CandidateHealthStore:
    """Thread-safe bounded health history keyed without signed URLs."""

    def __init__(self, *, max_entries: int = 2048) -> None:
        self.max_entries = max(1, int(max_entries))
        self._lock = threading.RLock()
        self._entries: dict[tuple[str, str, str, str, str], CandidateHealthSnapshot] = {}

    @staticmethod
    def key(
        candidate: StreamCandidate,
        *,
        platform: str = "",
        account_ref: str = "default",
        network_context: Mapping[str, object] | None = None,
    ) -> tuple[str, str, str, str, str]:
        metadata = dict(candidate.raw_metadata or {})
        platform_value = str(platform or metadata.get("_health_platform", "unknown"))
        account_value = str(account_ref or "")
        if (not account_value or account_value == "default") and metadata.get("_health_account_ref"):
            account_value = str(metadata.get("_health_account_ref"))
        return (
            platform_value.strip().lower(),
            (account_value or "default").strip()[:128],
            _safe_profile(network_context)
            if network_context is not None
            else str(metadata.get("_health_profile", "direct")).strip().lower()[:160],
            str(candidate.cdn_id or metadata.get("cdn_id", "")).strip().lower()[:160],
            str(candidate.protocol or metadata.get("protocol", "")).strip().lower()[:32],
        )

    def snapshot(
        self,
        candidate: StreamCandidate,
        *,
        platform: str = "",
        account_ref: str = "default",
        network_context: Mapping[str, object] | None = None,
    ) -> CandidateHealthSnapshot:
        key = self.key(
            candidate,
            platform=platform,
            account_ref=account_ref,
            network_context=network_context,
        )
        with self._lock:
            current = self._entries.get(key)
            if current is None:
                return CandidateHealthSnapshot()
            return CandidateHealthSnapshot(
                successes=current.successes,
                failures=current.failures,
                consecutive_failures=current.consecutive_failures,
                last_failure_kind=current.last_failure_kind,
                last_latency_ms=current.last_latency_ms,
                last_seen_at=current.last_seen_at,
                score=current.score,
            )

    def enrich(
        self,
        candidates: tuple[StreamCandidate, ...] | list[StreamCandidate],
        *,
        platform: str,
        account_ref: str = "default",
        network_context: Mapping[str, object] | None = None,
    ) -> tuple[StreamCandidate, ...]:
        profile = _safe_profile(network_context)
        enriched: list[StreamCandidate] = []
        for candidate in candidates:
            snapshot = self.snapshot(
                candidate,
                platform=platform,
                account_ref=account_ref,
                network_context=network_context,
            )
            metadata = dict(candidate.raw_metadata or {})
            metadata.update({
                "_health_platform": platform,
                "_health_account_ref": account_ref,
                "_health_profile": profile,
                "history_score": snapshot.score,
                "cdn_health_score": snapshot.score,
            })
            enriched.append(replace(candidate, raw_metadata=metadata))
        return tuple(enriched)

    def record(
        self,
        candidate: StreamCandidate,
        result: ProbeResult,
        *,
        platform: str = "",
        account_ref: str = "default",
        network_context: Mapping[str, object] | None = None,
        now: float | None = None,
    ) -> None:
        key = self.key(
            candidate,
            platform=platform,
            account_ref=account_ref,
            network_context=network_context,
        )
        with self._lock:
            snapshot = self._entries.setdefault(key, CandidateHealthSnapshot())
            snapshot.last_seen_at = time.time() if now is None else float(now)
            snapshot.last_latency_ms = int(getattr(result, "first_packet_ms", -1) or -1)
            if bool(getattr(result, "ok", False)):
                snapshot.successes += 1
                snapshot.consecutive_failures = 0
                snapshot.score = min(100.0, snapshot.score + 8.0)
                snapshot.last_failure_kind = ""
            else:
                snapshot.failures += 1
                snapshot.consecutive_failures += 1
                snapshot.last_failure_kind = str(
                    getattr(result, "failure_kind", "") or "UNKNOWN"
                )[:64]
                snapshot.score = max(
                    -100.0,
                    snapshot.score - min(30.0, 8.0 + snapshot.consecutive_failures * 3.0),
                )
            if len(self._entries) > self.max_entries:
                oldest = min(
                    self._entries.items(),
                    key=lambda item: item[1].last_seen_at,
                )[0]
                self._entries.pop(oldest, None)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


_DEFAULT_STORE = CandidateHealthStore()


def get_default_candidate_health_store() -> CandidateHealthStore:
    return _DEFAULT_STORE


__all__ = [
    "CandidateHealthSnapshot",
    "CandidateHealthStore",
    "get_default_candidate_health_store",
]
