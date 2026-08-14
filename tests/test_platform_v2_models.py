"""Tests for V2 platform data models (PR-2)."""
import pytest

from lsc.platforms.base import StreamInfo
from lsc.platforms.models import (
    CONN_POLICY_SHARED_UPSTREAM,
    PlatformCapabilities,
    ProbeResult,
    StreamCandidate,
    StreamLease,
    candidate_to_stream_info,
    stream_info_to_candidate,
)


def test_platform_capabilities_defaults():
    caps = PlatformCapabilities(platform="bilibili")
    assert caps.connection_policy == CONN_POLICY_SHARED_UPSTREAM
    assert caps.max_resolve_concurrency == 1
    assert caps.probe_timeout_sec == 8.0


def test_resolve_result_exposes_platform_id_alias():
    from lsc.platforms.models import ResolveResult

    result = ResolveResult(platform="huya", room_url="https://www.huya.com/1")
    assert result.platform_id == "huya"


def test_platform_capabilities_rejects_unknown_policy():
    with pytest.raises(ValueError):
        PlatformCapabilities(platform="x", connection_policy="bogus")


def test_platform_capabilities_rejects_anonymous_when_not_supported():
    with pytest.raises(ValueError):
        PlatformCapabilities(platform="x", auth_mode="anonymous", supports_anonymous=False)


def test_stream_candidate_redacted_omits_url():
    cand = StreamCandidate(
        candidate_id="bilibili|source",
        url="https://cdn.example/live?token=SECRET",
        request_headers={"Referer": "https://live.bilibili.com"},
        cdn_id="qn",
    )
    redacted = cand.redacted()
    assert redacted["candidate_id"] == "bilibili|source"
    assert "url" not in redacted
    assert "SECRET" not in str(redacted)


def test_stream_candidate_fingerprint_keeps_distinct_signed_urls_separate():
    first = StreamCandidate(
        candidate_id="c1",
        url="https://cdn.example/live.m3u8?token=first&expires=100",
        quality_id="origin",
        cdn_id="cdn-a",
    )
    second = StreamCandidate(
        candidate_id="c2",
        url="https://cdn.example/live.m3u8?token=second&expires=101",
        quality_id="origin",
        cdn_id="cdn-a",
    )

    assert first.safe_url == second.safe_url
    assert first.fingerprint != second.fingerprint


def test_probe_result_ok_requires_real_video():
    good = ProbeResult(
        candidate_id="c1", reachable=True, has_video=True,
        timestamp_ok=True, protocol="hls", container="mpegts",
    )
    assert good.ok
    assert good.success
    assert good.failure_code == ""

    no_ts = ProbeResult(
        candidate_id="c1", reachable=True, has_video=True, timestamp_ok=False,
    )
    assert not no_ts.ok

    failed = ProbeResult(
        candidate_id="c1", reachable=False, failure_kind="CONNECT_TIMEOUT",
    )
    assert not failed.ok
    assert failed.failure_code == "CONNECT_TIMEOUT"


def test_stream_lease_redacted():
    cand = StreamCandidate(candidate_id="c1", url="https://x/secret")
    lease = StreamLease(
        lease_id="L1", room_id="R1", candidate=cand,
        issued_at=0.0, state="active", failure_count=2,
    )
    d = lease.redacted()
    assert d["lease_id"] == "L1"
    assert d["failure_count"] == 2


def test_stream_info_to_candidate_roundtrip():
    info = StreamInfo(
        platform="bilibili",
        room_url="https://live.bilibili.com/1",
        stream_url="https://cdn.example/live",
        title="Room",
        streamer="Streamer",
        selected_quality="原画",
        headers={"Referer": "https://live.bilibili.com"},
        raw={"qn": 10000},
    )
    cand = stream_info_to_candidate(info, candidate_id="bili|1")
    assert cand is not None
    assert cand.url == "https://cdn.example/live"
    assert cand.quality_label == "原画"
    assert cand.protocol == ""  # filled by probe
    assert cand.raw_metadata["room_url"] == "https://live.bilibili.com/1"

    # reverse bridge
    back = candidate_to_stream_info(cand)
    assert back["streamUrl"] == "https://cdn.example/live"
    assert back["selectedQuality"] == "原画"
    assert back["protocol"] == ""


def test_stream_info_to_candidate_returns_none_when_no_stream():
    assert stream_info_to_candidate(None) is None
    assert stream_info_to_candidate(StreamInfo(platform="x", room_url="u")) is None


def test_stream_info_to_candidate_uses_explicit_id_when_absent():
    info = StreamInfo(
        platform="bilibili",
        room_url="u",
        stream_url="https://cdn/live",
    )
    cand = stream_info_to_candidate(info)
    assert cand is not None
    assert cand.candidate_id.startswith("bilibili|")
