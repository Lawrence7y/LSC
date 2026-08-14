from lsc.platforms.candidate_health import CandidateHealthStore
from lsc.platforms.models import ProbeResult, StreamCandidate


def _candidate(*, url: str = "https://cdn.example/live.m3u8", cdn: str = "edge-a") -> StreamCandidate:
    return StreamCandidate(
        candidate_id="candidate-1",
        url=url,
        protocol="hls",
        cdn_id=cdn,
        quality_id="720p",
    )


def test_health_key_ignores_signed_url_but_separates_scope() -> None:
    store = CandidateHealthStore()
    candidate = _candidate(url="https://cdn.example/live.m3u8?sig=one")
    other_url = _candidate(url="https://cdn.example/live.m3u8?sig=two")
    result = ProbeResult(
        candidate_id=candidate.candidate_id,
        reachable=True,
        has_video=True,
        timestamp_ok=True,
        first_packet_ms=42,
    )
    store.record(
        candidate,
        result,
        platform="huya",
        account_ref="acct-a",
        network_context={"profile": "proxy-a"},
        now=10,
    )

    assert store.snapshot(
        other_url,
        platform="huya",
        account_ref="acct-a",
        network_context={"profile": "proxy-a"},
    ).successes == 1
    assert store.snapshot(
        other_url,
        platform="douyu",
        account_ref="acct-a",
        network_context={"profile": "proxy-a"},
    ).successes == 0
    assert store.snapshot(
        other_url,
        platform="huya",
        account_ref="acct-a",
        network_context={"profile": "proxy-b"},
    ).successes == 0


def test_health_enriches_candidates_and_tracks_failure() -> None:
    store = CandidateHealthStore()
    candidate = _candidate()
    store.record(
        candidate,
        ProbeResult(candidate_id=candidate.candidate_id, failure_kind="CDN_FORBIDDEN"),
        platform="kuaishou",
        account_ref="acct-a",
    )
    enriched = store.enrich(
        (candidate,),
        platform="kuaishou",
        account_ref="acct-a",
    )[0]
    assert enriched.raw_metadata["history_score"] < 0
    assert enriched.raw_metadata["cdn_health_score"] == enriched.raw_metadata["history_score"]
    assert store.snapshot(enriched, platform="kuaishou", account_ref="acct-a").failure_rate == 1.0


def test_health_store_is_bounded() -> None:
    store = CandidateHealthStore(max_entries=1)
    success = ProbeResult(
        candidate_id="candidate-1", reachable=True, has_video=True, timestamp_ok=True
    )
    store.record(_candidate(cdn="edge-a"), success, platform="a", now=1)
    store.record(_candidate(cdn="edge-b"), success, platform="b", now=2)
    assert store.snapshot(_candidate(cdn="edge-a"), platform="a").successes == 0
    assert store.snapshot(_candidate(cdn="edge-b"), platform="b").successes == 1
