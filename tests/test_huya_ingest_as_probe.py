from lsc.platforms.capabilities import get_platform_capabilities, uses_ingest_probe
from lsc.platforms.models import PlatformCapabilities


def test_huya_capabilities_are_ingest_probe_and_no_auto_reconnect():
    caps = get_platform_capabilities("huya")
    assert caps.probe_profile == "ingest"
    assert caps.preview_auto_reconnect is False
    assert caps.preview_refresh_when_recording is False
    assert caps.max_connect_concurrency == 1
    assert caps.signed_url is True
    assert uses_ingest_probe(caps) is True


def test_uses_ingest_probe_defaults_from_signed_single_connect():
    caps = PlatformCapabilities(
        platform="custom",
        signed_url=True,
        max_connect_concurrency=1,
        probe_profile="default",
    )
    assert uses_ingest_probe(caps) is True


def test_bilibili_keeps_remote_probe():
    caps = get_platform_capabilities("bilibili")
    assert caps.probe_profile in {"default", "ffprobe"}
    assert uses_ingest_probe(caps) is False


def test_huya_v2_hard_blocklist_wins_over_allowlist_and_shared_ingest():
    from lsc.config import LscConfig, is_platform_pipeline_v2_enabled

    cfg = LscConfig(
        platform_pipeline_v2_enabled=True,
        platform_pipeline_v2_allowlist=["bilibili", "huya"],
        shared_ingest_enabled=True,
        ingest_supervisor_v2=True,
    )
    assert is_platform_pipeline_v2_enabled("huya", cfg) is False
    assert is_platform_pipeline_v2_enabled("bilibili", cfg) is True
    assert is_platform_pipeline_v2_enabled("虎牙", cfg) is False


def test_shared_ingest_v2_gate_rejects_huya_even_when_global_shared_on(monkeypatch):
    import os
    import sys
    from types import SimpleNamespace

    from lsc.config import LscConfig

    backend_dir = os.path.join(os.path.dirname(__file__), "..", "python-backend")
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)

    from handlers.room_handler import _shared_ingest_v2_enabled

    cfg = LscConfig(
        platform_pipeline_v2_enabled=True,
        platform_pipeline_v2_allowlist=["huya"],
        shared_ingest_enabled=True,
    )
    monkeypatch.setattr("handlers.room_handler.load_config", lambda: cfg)
    room = SimpleNamespace(
        platform="huya",
        platform_name="huya",
        room_url="https://www.huya.com/1",
    )
    manager = SimpleNamespace(get_room=lambda _rid: room)
    assert _shared_ingest_v2_enabled(manager, "room-1") is False


def test_huya_preview_auto_reconnect_is_disabled():
    import os
    import sys

    from lsc.platforms.base import StreamInfo

    backend_dir = os.path.join(os.path.dirname(__file__), "..", "python-backend")
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)

    from handlers.room_handler import _preview_auto_reconnect_allowed

    info = StreamInfo(platform="huya", room_url="https://www.huya.com/1")
    assert _preview_auto_reconnect_allowed(info) is False
    assert _preview_auto_reconnect_allowed("huya") is False
    assert _preview_auto_reconnect_allowed("bilibili") is True


def test_huya_tx_and_al_share_signature_family():
    from lsc.platforms.signature_family import signature_family_id

    secret = "abc123"
    ws_time = "68f0aa00"
    tx = f"https://tx.flv.huya.com/src/room.flv?wsSecret={secret}&wsTime={ws_time}&codec=264"
    al = f"https://al.flv.huya.com/src/room.flv?wsSecret={secret}&wsTime={ws_time}&codec=264"
    assert signature_family_id(tx) == signature_family_id(al)
    assert signature_family_id(tx)
    other = f"https://tx.flv.huya.com/src/room.flv?wsSecret=zzz&wsTime={ws_time}"
    assert signature_family_id(tx) != signature_family_id(other)


def test_candidate_from_url_sets_signature_family_id():
    from lsc.platforms.resolver import _candidate_from_url
    from lsc.platforms.signature_family import signature_family_id

    url = "https://tx.flv.huya.com/src/room.flv?wsSecret=abc&wsTime=1"
    candidate = _candidate_from_url(
        platform="huya",
        quality="source",
        url=url,
        headers={},
        priority=0,
        raw={},
    )
    assert candidate is not None
    assert candidate.signature_family_id == signature_family_id(url)
    assert candidate.signature_family_id
    assert "abc" not in str(candidate.redacted())


def test_huya_403_invalidates_family_and_does_not_quarantine_cdn():
    from lsc.platforms.base import StreamInfo
    from lsc.platforms.huya import _is_cdn_blacklisted, clear_cdn_blacklist
    from lsc.platforms.recovery_policy import mark_failed_candidate, recovery_action

    clear_cdn_blacklist()
    info = StreamInfo(
        platform="huya",
        room_url="https://www.huya.com/1",
        stream_url="https://tx.flv.huya.com/src/live.flv?wsSecret=abc&wsTime=1",
        raw={"v2": True, "candidate_id": "huya|source|0", "candidate_cdn_id": "tx"},
    )
    action = recovery_action(info, "Server returned 403 Forbidden", saw_first_ts=False)
    assert action == "invalidate_family"
    assert mark_failed_candidate(info, "Server returned 403 Forbidden") is False
    assert not _is_cdn_blacklisted("tx", room_key="https://www.huya.com/1")
    clear_cdn_blacklist()


def test_huya_connect_timeout_after_media_quarantines_cdn():
    from lsc.platforms.base import StreamInfo
    from lsc.platforms.huya import _is_cdn_blacklisted, clear_cdn_blacklist
    from lsc.platforms.recovery_policy import mark_failed_candidate, recovery_action

    clear_cdn_blacklist()
    info = StreamInfo(
        platform="huya",
        room_url="https://www.huya.com/1",
        stream_url="https://tx.flv.huya.com/src/live.flv?wsSecret=abc&wsTime=1",
    )
    error = "Connection to tcp://tx.flv.huya.com:443 failed: Error number -138 occurred"
    assert recovery_action(info, error, saw_first_ts=True) == "quarantine_cdn"
    assert mark_failed_candidate(info, error, room_id="https://www.huya.com/1", saw_first_ts=True) is True
    assert _is_cdn_blacklisted("tx", room_key="https://www.huya.com/1")
    clear_cdn_blacklist()


def test_huya_eof_before_first_ts_invalidates_family():
    from lsc.platforms.base import StreamInfo
    from lsc.platforms.recovery_policy import recovery_action

    info = StreamInfo(
        platform="huya",
        room_url="https://www.huya.com/1",
        stream_url="https://tx.flv.huya.com/src/live.flv",
    )
    assert recovery_action(info, "End of file", saw_first_ts=False) == "invalidate_family"
    assert recovery_action(info, "preview encoder failed", saw_first_ts=True) == "restart_preview_sink"


def test_select_ingest_lease_does_not_require_probe_ok():
    from lsc.platforms.capabilities import get_platform_capabilities
    from lsc.platforms.lease_manager import LeaseManager
    from lsc.platforms.models import ResolveResult, StreamCandidate
    from lsc.platforms.resolver import select_ingest_lease

    caps = get_platform_capabilities("huya")
    tx = StreamCandidate(
        candidate_id="huya|source|0",
        url="https://tx.flv.huya.com/src/a.flv?wsSecret=abc&wsTime=1",
        quality_id="source",
        cdn_id="tx",
        protocol="flv",
        signature_family_id="fam1",
    )
    al = StreamCandidate(
        candidate_id="huya|al|1",
        url="https://al.flv.huya.com/src/a.flv?wsSecret=abc&wsTime=1",
        quality_id="al",
        cdn_id="al",
        protocol="flv",
        signature_family_id="fam1",
    )
    result = ResolveResult(
        platform="huya",
        room_url="https://www.huya.com/1",
        candidates=(al, tx),
        capabilities=caps,
        live_status="LIVE",
    )
    lease = select_ingest_lease(result, room_id="r1", lease_manager=LeaseManager(), now=0.0)
    assert lease is not None
    assert lease.candidate.cdn_id == "tx"
    assert lease.probe_summary.get("mode") == "ingest"
    assert lease.consumed is False


def test_resolve_playable_lease_skips_probe_candidates_for_ingest(monkeypatch):
    from lsc.platforms.capabilities import get_platform_capabilities
    from lsc.platforms.lease_manager import LeaseManager
    from lsc.platforms.models import ResolveResult, StreamCandidate
    from lsc.platforms.resolver import resolve_playable_lease

    calls = {"n": 0}

    def _boom(*_args, **_kwargs):
        calls["n"] += 1
        raise AssertionError("probe_candidates must not run for ingest-probe platforms")

    monkeypatch.setattr("lsc.platforms.resolver.probe_candidates", _boom)
    caps = get_platform_capabilities("huya")
    candidate = StreamCandidate(
        candidate_id="huya|source|0",
        url="https://tx.flv.huya.com/src/a.flv?wsSecret=abc&wsTime=1",
        quality_id="source",
        cdn_id="tx",
        protocol="flv",
    )
    result = ResolveResult(
        platform="huya",
        room_url="https://www.huya.com/1",
        candidates=(candidate,),
        capabilities=caps,
        live_status="LIVE",
    )
    lease = resolve_playable_lease(
        result,
        room_id="r1",
        lease_manager=LeaseManager(),
        now=0.0,
    )
    assert lease is not None
    assert calls["n"] == 0
