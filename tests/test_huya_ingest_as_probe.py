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


def test_start_preview_without_subscribers_is_not_media_ready():
    from lsc.core.services.shared_ingest import SharedRoomIngest

    ingest = SharedRoomIngest(room_id="r", url="https://example/live.flv")
    result = ingest.start_preview()
    assert result.accepted is True
    assert result.media_ready is False
    assert result.ok is False


def test_start_preview_live_process_is_not_media_ready_until_segments(monkeypatch):
    from lsc.core.services.shared_ingest import PreviewSubscriber, SharedRoomIngest

    ingest = SharedRoomIngest(room_id="r", url="https://example/live.flv")
    ingest._preview_subscribers.append(PreviewSubscriber(1024))

    class _LiveProc:
        pid = 42
        returncode = None
        stdin = None
        stdout = None
        stderr = None

        def poll(self):
            return None

        def terminate(self):
            return None

    monkeypatch.setattr(ingest, "_launch_process", lambda _command: _LiveProc())
    monkeypatch.setattr(ingest, "_ensure_upstream_started", lambda: "")
    monkeypatch.setattr(ingest, "_start_stderr_reader", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(ingest, "_start_thread", lambda *_args, **_kwargs: None)

    result = ingest.start_preview()
    assert result.accepted is True
    assert result.media_ready is False
    assert result.ok is False

    ingest.publish_preview_segment(b"init", kind="init")
    ingest.publish_preview_segment(b"seg", kind="media")
    ready = ingest.start_preview()
    assert ready.accepted is True
    assert ready.media_ready is True
    assert ready.ok is True


def _room_handler():
    import os
    import sys

    backend_dir = os.path.join(os.path.dirname(__file__), "..", "python-backend")
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)
    from handlers import room_handler

    return room_handler


def test_preview_encoder_failure_does_not_refresh_stream():
    _should_refresh_failed_stream = _room_handler()._should_refresh_failed_stream
    assert _should_refresh_failed_stream("preview encoder failed") is False
    assert _should_refresh_failed_stream("shared preview stdout stalled (15s)") is False
    assert _should_refresh_failed_stream("Server returned 403 Forbidden") is True


def test_reconnect_budget_does_not_reset_on_accepted_or_media_ready():
    _reconnect_attempts_after_event = _room_handler()._reconnect_attempts_after_event
    now = 100.0
    state = {"attempts": 1, "running": True}
    state = _reconnect_attempts_after_event(state, event="accepted", now=now)
    state = _reconnect_attempts_after_event(state, event="media_ready", now=now)
    assert state["attempts"] == 1
    state = _reconnect_attempts_after_event(state, event="exit", now=now + 3)
    assert state["attempts"] == 2


def test_reconnect_budget_resets_only_after_durable_window():
    _reconnect_attempts_after_event = _room_handler()._reconnect_attempts_after_event
    state = {"attempts": 2, "running": True, "media_ready_at": 100.0}
    state = _reconnect_attempts_after_event(
        state, event="durable", now=130.0, durable_sec=30.0,
    )
    assert state["attempts"] == 0


def test_begin_mse_reconnect_preserves_attempts_until_durable():
    _begin_mse_reconnect = _room_handler()._begin_mse_reconnect
    prev = {"attempts": 2, "running": False, "media_ready_at": 10.0}
    state = _begin_mse_reconnect(prev)
    assert state["attempts"] == 2
    assert state["running"] is True
    durable = {"attempts": 0, "durable": True}
    fresh = _begin_mse_reconnect(durable)
    assert fresh["attempts"] == 0
    assert fresh["running"] is True


def test_recording_stdin_backpressure_does_not_block_upstream_dispatch():
    import time

    from lsc.core.services.shared_ingest import SharedRoomIngest

    class _BlockingStdin:
        def write(self, data):
            time.sleep(2.0)
            return len(data)

        def flush(self):
            return None

    class _RecordingProc:
        pid = 7
        returncode = None
        stdin = _BlockingStdin()

        def poll(self):
            return None

    ingest = SharedRoomIngest(room_id="r", url="https://example/a.flv")
    ingest._recording_process = _RecordingProc()
    ingest.recording_active = True
    packet = b"\x47" * 188 * 8
    started = time.monotonic()
    ingest._dispatch_ts_batch(packet)
    ingest._dispatch_ts_batch(packet)
    assert time.monotonic() - started < 1.0


def test_consumed_lease_cannot_open_again():
    from lsc.platforms.capabilities import get_platform_capabilities
    from lsc.platforms.lease_manager import LeaseManager
    from lsc.platforms.models import StreamCandidate

    manager = LeaseManager()
    caps = get_platform_capabilities("huya")
    candidate = StreamCandidate(
        candidate_id="huya|source|0",
        url="https://tx.flv.huya.com/src/a.flv?wsSecret=abc&wsTime=1",
    )
    lease = manager.issue("r1", candidate, caps, now=0.0)
    assert manager.mark_consumed(lease.lease_id) is True
    assert manager.is_consumed(lease.lease_id) is True


def test_ensure_upstream_marks_and_refuses_consumed_lease(monkeypatch):
    from lsc.core.services.shared_ingest import SharedRoomIngest
    from lsc.platforms.capabilities import get_platform_capabilities
    from lsc.platforms.lease_manager import LeaseManager
    from lsc.platforms.models import StreamCandidate

    class _LiveProc:
        pid = 9
        returncode = None
        stdin = None
        stdout = None
        stderr = None

        def poll(self):
            return None

        def terminate(self):
            return None

    launched: list[list[str]] = []
    ingest = SharedRoomIngest(room_id="r", url="https://example/live.flv?wsSecret=abc")
    monkeypatch.setattr(ingest, "_launch_process", lambda cmd: launched.append(list(cmd)) or _LiveProc())
    monkeypatch.setattr(ingest, "_start_stderr_reader", lambda *_a, **_k: None)
    monkeypatch.setattr(ingest, "_start_thread", lambda *_a, **_k: None)
    manager = LeaseManager()
    lease = manager.issue(
        "r",
        StreamCandidate(candidate_id="huya|0", url=ingest.url),
        get_platform_capabilities("huya"),
        now=0.0,
    )
    ingest.bind_lease(manager, lease.lease_id)
    assert ingest._ensure_upstream_started() == ""
    assert manager.is_consumed(lease.lease_id) is True
    ingest._process = None
    error = ingest._ensure_upstream_started()
    assert "consumed" in error.lower()
    assert len(launched) == 1


def test_same_wssecret_second_http_get_is_403():
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from urllib.error import HTTPError
    from urllib.parse import parse_qs, urlparse
    from urllib.request import urlopen

    counts: dict[str, int] = {}

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            secret = (parse_qs(urlparse(self.path).query).get("wsSecret") or [""])[0]
            counts[secret] = counts.get(secret, 0) + 1
            if counts[secret] == 1:
                self.send_response(200)
                self.send_header("Content-Type", "video/mp2t")
                self.end_headers()
                self.wfile.write(b"\x47" * 188)
                return
            self.send_response(403)
            self.end_headers()

        def log_message(self, *_args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/live.flv?wsSecret=abc&wsTime=1"
        with urlopen(url, timeout=1) as response:
            assert response.status == 200
            assert response.read() == b"\x47" * 188
        try:
            urlopen(url, timeout=1)
            raise AssertionError("second GET should be 403")
        except HTTPError as exc:
            assert exc.code == 403
    finally:
        server.shutdown()
        thread.join(timeout=1)


def test_ingest_path_then_one_open_matches_single_use_secret(monkeypatch):
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from urllib.error import HTTPError
    from urllib.parse import parse_qs, urlparse
    from urllib.request import urlopen

    from lsc.platforms.capabilities import get_platform_capabilities
    from lsc.platforms.lease_manager import LeaseManager
    from lsc.platforms.models import ResolveResult, StreamCandidate
    from lsc.platforms.resolver import resolve_playable_lease

    counts: dict[str, int] = {}

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            secret = (parse_qs(urlparse(self.path).query).get("wsSecret") or [""])[0]
            counts[secret] = counts.get(secret, 0) + 1
            if counts[secret] == 1:
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"\x47" * 188)
                return
            self.send_response(403)
            self.end_headers()

        def log_message(self, *_args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/live.flv?wsSecret=abc&wsTime=1"
        calls = {"n": 0}

        def _boom(*_a, **_k):
            calls["n"] += 1
            raise AssertionError("probe_candidates must not run")

        monkeypatch.setattr("lsc.platforms.resolver.probe_candidates", _boom)
        caps = get_platform_capabilities("huya")
        result = ResolveResult(
            platform="huya",
            room_url="https://www.huya.com/1",
            candidates=(
                StreamCandidate(
                    candidate_id="huya|source|0",
                    url=url,
                    quality_id="source",
                    cdn_id="tx",
                    protocol="flv",
                ),
            ),
            capabilities=caps,
            live_status="LIVE",
        )
        lease = resolve_playable_lease(
            result, room_id="r1", lease_manager=LeaseManager(), now=0.0,
        )
        assert lease is not None
        assert calls["n"] == 0
        with urlopen(lease.candidate.url, timeout=1) as response:
            assert response.status == 200
        try:
            urlopen(lease.candidate.url, timeout=1)
            raise AssertionError("second open must 403")
        except HTTPError as exc:
            assert exc.code == 403
    finally:
        server.shutdown()
        thread.join(timeout=1)


def test_acceptance_ingest_skips_remote_probe(monkeypatch):
    from lsc.platforms.acceptance import AcceptanceOptions, run_acceptance
    from lsc.platforms.capabilities import get_platform_capabilities
    from lsc.platforms.models import ResolveResult, StreamCandidate

    calls = {"n": 0}

    def probe(*_args, **_kwargs):
        calls["n"] += 1
        raise AssertionError("probe_candidates must not run for ingest platforms")

    result = ResolveResult(
        platform="huya",
        room_url="https://www.huya.com/1",
        live_status="LIVE",
        capabilities=get_platform_capabilities("huya"),
        candidates=(
            StreamCandidate(
                candidate_id="huya|source|0",
                url="https://tx.flv.huya.com/src/a.flv?wsSecret=abc&wsTime=1",
                quality_id="source",
                cdn_id="tx",
            ),
        ),
    )
    monkeypatch.setattr(
        "lsc.platforms.acceptance._run_lifecycle",
        lambda *_args, **_kwargs: {"status": "SKIPPED"},
    )
    report = run_acceptance(
        AcceptanceOptions(source_url="https://www.huya.com/1"),
        resolve_fn=lambda _request: result,
        probe_fn=probe,
    )
    assert calls["n"] == 0
    assert report.probe_count == 0
    assert report.selected_lease
    assert not any(item["code"] == "NO_PLAYABLE_CANDIDATE" for item in report.failures)


def test_preview_encoder_failure_does_not_mark_cdn_or_family():
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
    assert recovery_action(info, "preview encoder failed") == "restart_preview_sink"
    assert recovery_action(info, "shared preview stdout stalled (15s)") == "restart_preview_sink"
    assert mark_failed_candidate(info, "preview encoder failed") is False
    assert not _is_cdn_blacklisted("tx", room_key="https://www.huya.com/1")
    clear_cdn_blacklist()


def test_huya_403_does_not_issue_sibling_cdn_with_same_secret():
    from lsc.platforms.capabilities import get_platform_capabilities
    from lsc.platforms.lease_manager import LeaseManager
    from lsc.platforms.models import ResolveResult, StreamCandidate
    from lsc.platforms.resolver import select_ingest_lease
    from lsc.platforms.signature_family import signature_family_id

    caps = get_platform_capabilities("huya")
    tx_url = "https://tx.flv.huya.com/src/a.flv?wsSecret=abc&wsTime=1"
    al_url = "https://al.flv.huya.com/src/a.flv?wsSecret=abc&wsTime=1"
    family = signature_family_id(tx_url)
    result = ResolveResult(
        platform="huya",
        room_url="https://www.huya.com/1",
        live_status="LIVE",
        capabilities=caps,
        candidates=(
            StreamCandidate(
                candidate_id="huya|source|0",
                url=tx_url,
                quality_id="source",
                cdn_id="tx",
                signature_family_id=family,
            ),
            StreamCandidate(
                candidate_id="huya|al|1",
                url=al_url,
                quality_id="source",
                cdn_id="al",
                signature_family_id=family,
            ),
        ),
    )
    manager = LeaseManager()
    lease = select_ingest_lease(result, room_id="r1", lease_manager=manager, now=0.0)
    assert lease is not None
    assert lease.candidate.cdn_id == "tx"
    assert manager.mark_consumed(lease.lease_id) is True
    next_lease = select_ingest_lease(result, room_id="r1", lease_manager=manager, now=1.0)
    assert next_lease is None


def test_huya_line_failure_allows_other_cdn_with_new_secret():
    from lsc.platforms.capabilities import get_platform_capabilities
    from lsc.platforms.huya import clear_cdn_blacklist, mark_cdn_bad
    from lsc.platforms.lease_manager import LeaseManager
    from lsc.platforms.models import ResolveResult, StreamCandidate
    from lsc.platforms.resolver import select_ingest_lease

    clear_cdn_blacklist()
    mark_cdn_bad("tx", room_key="https://www.huya.com/1")
    caps = get_platform_capabilities("huya")
    result = ResolveResult(
        platform="huya",
        room_url="https://www.huya.com/1",
        live_status="LIVE",
        capabilities=caps,
        candidates=(
            StreamCandidate(
                candidate_id="huya|source|0",
                url="https://tx.flv.huya.com/src/a.flv?wsSecret=old&wsTime=1",
                quality_id="source",
                cdn_id="tx",
                signature_family_id="old-family",
            ),
            StreamCandidate(
                candidate_id="huya|hs|1",
                url="https://hs.flv.huya.com/src/a.flv?wsSecret=new&wsTime=2",
                quality_id="source",
                cdn_id="hs",
                signature_family_id="new-family",
            ),
        ),
    )
    lease = select_ingest_lease(
        result, room_id="r1", lease_manager=LeaseManager(), now=0.0,
    )
    assert lease is not None
    assert lease.candidate.cdn_id == "hs"
    assert "wsSecret=new" in lease.candidate.url
    clear_cdn_blacklist()


def test_bilibili_resolve_playable_lease_still_probes(monkeypatch):
    from lsc.platforms.capabilities import get_platform_capabilities
    from lsc.platforms.lease_manager import LeaseManager
    from lsc.platforms.models import ProbeResult, ResolveResult, StreamCandidate
    from lsc.platforms.resolver import resolve_playable_lease

    calls = {"n": 0}

    def fake_probe(candidates, **_kwargs):
        calls["n"] += 1
        item = candidates[0]
        return {
            item.candidate_id: ProbeResult(
                item.candidate_id,
                reachable=True,
                has_video=True,
                timestamp_ok=True,
                protocol="flv",
                container="flv",
            )
        }

    monkeypatch.setattr("lsc.platforms.resolver.probe_candidates", fake_probe)
    candidate = StreamCandidate(
        candidate_id="bili|0",
        url="https://example.com/live.flv",
        quality_id="origin",
        cdn_id="gotcha",
    )
    result = ResolveResult(
        platform="bilibili",
        room_url="https://live.bilibili.com/1",
        live_status="LIVE",
        capabilities=get_platform_capabilities("bilibili"),
        candidates=(candidate,),
    )
    lease = resolve_playable_lease(
        result, room_id="r1", lease_manager=LeaseManager(), now=0.0,
    )
    assert calls["n"] == 1
    assert lease is not None
    assert lease.candidate.candidate_id == "bili|0"
