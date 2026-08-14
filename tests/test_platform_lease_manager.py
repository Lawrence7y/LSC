"""Tests for LeaseManager lifecycle (PR-4)."""
import time

import pytest

from lsc.platforms.failure import FailureKind
from lsc.platforms.lease_manager import LeaseManager, LeasePolicy
from lsc.platforms.models import PlatformCapabilities, StreamCandidate


def _cand(**kw) -> StreamCandidate:
    base = dict(candidate_id="bili|source", url="https://cdn/live")
    base.update(kw)
    return StreamCandidate(**base)


def _caps(policy="shared_upstream", **kw) -> PlatformCapabilities:
    kw.setdefault("platform", "bilibili")
    kw.setdefault("connection_policy", policy)
    kw.setdefault("refresh_margin_sec", 10.0)
    return PlatformCapabilities(**kw)


def test_issue_uses_platform_expiry():
    mgr = LeaseManager()
    lease = mgr.issue(
        "R1",
        _cand(expires_at=300.0),
        _caps(),
        now=1000.0,
    )
    assert lease.state == "active"
    assert lease.expires_at == 300.0
    # refresh window: expiry - max(margin, 15% of expiry) = 300 - 45 = 255
    assert lease.refresh_at == pytest.approx(255.0)


def test_issue_fallback_when_no_expiry_info():
    mgr = LeaseManager(LeasePolicy(reuse_when_unknown_sec=120.0))
    lease = mgr.issue("R1", _cand(), _caps(), now=0.0)
    assert lease.expires_at == pytest.approx(120.0)
    assert lease.generation == 1
    assert lease.room_session_id == "R1"


def test_signature_hint_forces_conservative_refresh():
    mgr = LeaseManager(LeasePolicy(reuse_when_unknown_sec=120.0))
    lease = mgr.issue(
        "R1",
        _cand(url="https://cdn/live?wsSecret=abc&exp=1700000000"),
        _caps(),
        now=0.0,
    )
    # signature present but no absolute expiry -> fallback window
    assert lease.expires_at == pytest.approx(120.0)


def test_refresh_and_expiry_clock():
    mgr = LeaseManager()
    lease = mgr.issue("R1", _cand(expires_at=100.0), _caps(refresh_margin_sec=20.0), now=0.0)
    assert mgr.needs_refresh(lease, now=0.0) is False
    assert mgr.is_expired(lease, now=0.0) is False
    # at 85s: inside refresh window (100-20=80 .. 100)
    assert mgr.needs_refresh(lease, now=85.0) is True
    assert mgr.is_expired(lease, now=85.0) is False
    # at 105s: expired
    assert mgr.is_expired(lease, now=105.0) is True


def test_epoch_expiry_uses_monotonic_deadline(monkeypatch):
    now_epoch = 1_700_000_000.0
    monkeypatch.setattr(time, "time", lambda: now_epoch)
    mgr = LeaseManager()
    lease = mgr.issue(
        "R1",
        _cand(expires_at=now_epoch + 100.0),
        _caps(refresh_margin_sec=20.0),
        now=50.0,
    )
    assert lease.expires_at == now_epoch + 100.0
    assert mgr.needs_refresh(lease, now=129.0) is False
    assert mgr.needs_refresh(lease, now=131.0) is True
    assert mgr.is_expired(lease, now=149.0) is False
    assert mgr.is_expired(lease, now=151.0) is True
    # Public refresh_at remains an epoch timestamp; scheduling uses the
    # monotonic refresh_deadline_mono internally.
    assert lease.refresh_at == pytest.approx(now_epoch + 80.0)


def test_failure_budget_revokes_after_limit():
    mgr = LeaseManager(LeasePolicy(max_failures=2))
    lease = mgr.issue("R1", _cand(), _caps(), now=0.0)
    assert mgr.apply_failure(lease, FailureKind.CONNECTION_RESET) is False
    assert mgr.apply_failure(lease, FailureKind.CONNECTION_RESET) is True
    assert lease.state == "revoked"


def test_non_recoverable_kind_revokes_immediately():
    mgr = LeaseManager()
    lease = mgr.issue("R1", _cand(), _caps(), now=0.0)
    assert mgr.apply_failure(lease, FailureKind.AUTH_EXPIRED) is True
    assert lease.state == "revoked"


def test_failure_budget_normalizes_wire_enum_name():
    mgr = LeaseManager()
    lease = mgr.issue("R1", _cand(), _caps(), now=0.0)
    assert mgr.apply_failure(lease, "FailureKind.AUTH_EXPIRED") is True
    assert lease.state == "revoked"


def test_signature_failure_marks_refreshing_not_revoked():
    mgr = LeaseManager()
    lease = mgr.issue("R1", _cand(), _caps(), now=0.0)
    assert mgr.apply_failure(lease, FailureKind.SIGNATURE_EXPIRED) is False
    assert lease.state == "refreshing"


def test_prune_removes_stale():
    mgr = LeaseManager()
    l1 = mgr.issue("R1", _cand(expires_at=10.0), _caps(), now=0.0)
    l2 = mgr.issue("R2", _cand(expires_at=1000.0), _caps(), now=0.0)
    mgr.apply_failure(l2, FailureKind.DISK_FULL)  # revoke l2
    stale = mgr.prune(now=50.0)
    assert l1.lease_id in stale
    assert l2.lease_id in stale
    assert mgr.get(l1.lease_id) is None


def test_redacted_snapshot_omits_urls():
    mgr = LeaseManager()
    mgr.issue("R1", _cand(url="https://cdn/secret?t=TOKEN"), _caps(), now=0.0)
    snap = mgr.redacted_snapshot()
    assert len(snap) == 1
    assert "url" not in snap[0]["candidate"]
    assert "secret" not in str(snap)


def test_refresh_revokes_old_generation_and_issues_new_lease():
    mgr = LeaseManager()
    old = mgr.issue("R1", _cand(expires_at=100.0), _caps(), now=0.0)
    new = mgr.refresh(old, _cand(expires_at=200.0), _caps(), now=10.0)
    assert old.state == "revoked"
    assert old.invalidation_reason == "lease refreshed"
    assert new.state == "active"
    assert new.generation > old.generation
