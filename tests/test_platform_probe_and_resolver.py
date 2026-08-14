from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from lsc.platforms.base import StreamInfo
from lsc.platforms.credentials import CredentialContext, CredentialStatus
from lsc.platforms.failure import FailureKind
from lsc.platforms.lease_manager import LeaseManager
from lsc.platforms.models import ProbeRequest, ProbeResult, ResolveRequest, StreamCandidate
from lsc.platforms.probe import ProbeService, select_best_candidate
from lsc.platforms.resolver import resolve_stream_v2, select_stream_lease


def test_resolver_invalidates_provider_on_expired_login():
    invalidations = []

    class Provider:
        def get_context(self, platform, account_ref, purpose):
            return CredentialContext(
                platform=platform,
                account_ref=account_ref,
                purpose=purpose,
                status=CredentialStatus.AVAILABLE,
            )

        def invalidate(self, platform, account_ref="default", reason=""):
            invalidations.append((platform, account_ref, reason))

    class Adapter:
        platform = "direct"

        def can_handle(self, url):
            return True

        def parse(self, url):
            return StreamInfo(
                platform="direct",
                room_url=url,
                error="登录态已过期",
                is_live=False,
            )

    result = resolve_stream_v2(
        ResolveRequest("https://room.example/live", account_ref="account-1"),
        adapters=(Adapter(),),
        credential_provider=Provider(),
    )

    assert result.error is not None
    assert result.error.refresh_credentials is True
    assert invalidations == [("direct", "account-1", "AUTH_EXPIRED")]


def test_resolver_stops_before_legacy_parse_when_required_credentials_missing():
    class Provider:
        def get_context(self, platform, account_ref, purpose):
            return CredentialContext(
                platform=platform,
                account_ref=account_ref,
                purpose=purpose,
                status=CredentialStatus.NOT_CONFIGURED,
            )

    class Adapter:
        platform = "douyin"

        def can_handle(self, url):
            return True

        def parse(self, url):
            raise AssertionError("required-credential adapter must not parse")

    result = resolve_stream_v2(
        ResolveRequest("https://room.example/live"),
        adapters=(Adapter(),),
        credential_provider=Provider(),
    )

    assert result.ok is False
    assert result.credential_status == CredentialStatus.NOT_CONFIGURED
    assert result.error is not None
    assert result.error.code == "AUTH_REQUIRED"
    assert result.error.retryable is False


def test_resolver_reports_expired_required_credentials_without_retry():
    class Provider:
        def get_context(self, platform, account_ref, purpose):
            return CredentialContext(
                platform=platform,
                account_ref=account_ref,
                purpose=purpose,
                status=CredentialStatus.INVALID,
            )

    result = resolve_stream_v2(
        ResolveRequest("https://room.example/live"),
        adapters=(type("Adapter", (), {"platform": "douyin", "can_handle": lambda self, _url: True, "parse": lambda self, _url: None})(),),
        credential_provider=Provider(),
    )

    assert result.error is not None
    assert result.error.code == "AUTH_EXPIRED"
    assert result.error.refresh_credentials is True


def test_resolver_maps_legacy_machine_error_code_without_error_text():
    class Adapter:
        platform = "huya"

        def can_handle(self, url):
            return True

        def parse(self, url):
            return StreamInfo(
                platform="huya",
                room_url=url,
                error_code="offline",
                is_live=False,
            )

    result = resolve_stream_v2(
        ResolveRequest("https://room.example/live"),
        adapters=(Adapter(),),
    )

    assert result.error is not None
    assert result.error.code == "offline"
    assert result.error.category == "OFFLINE"
    assert result.live_status == "OFFLINE"
    assert result.error.retryable is False


def test_resolver_maps_legacy_auth_error_code_and_requests_refresh():
    class Adapter:
        platform = "bilibili"

        def can_handle(self, url):
            return True

        def parse(self, url):
            return StreamInfo(
                platform="bilibili",
                room_url=url,
                error_code="auth_required",
            )

    result = resolve_stream_v2(
        ResolveRequest("https://room.example/live"),
        adapters=(Adapter(),),
    )

    assert result.error is not None
    assert result.error.code == "auth_required"
    assert result.error.category == "AUTH"
    assert result.error.refresh_credentials is True
    assert result.error.retryable is False


def test_resolver_maps_legacy_parse_and_restricted_codes_without_text():
    class Adapter:
        platform = "kuaishou"

        def can_handle(self, url):
            return True

        def parse(self, url):
            return StreamInfo(
                platform="kuaishou",
                room_url=url,
                error_code="parse_failed",
            )

    parse_result = resolve_stream_v2(
        ResolveRequest("https://room.example/live"),
        adapters=(Adapter(),),
    )
    assert parse_result.error is not None
    assert parse_result.error.category == "PARSE"
    assert parse_result.error.code == "parse_failed"

    class RestrictedAdapter(Adapter):
        def parse(self, url):
            return StreamInfo(
                platform="kuaishou",
                room_url=url,
                error_code="restricted",
            )

    restricted_result = resolve_stream_v2(
        ResolveRequest("https://room.example/live"),
        adapters=(RestrictedAdapter(),),
    )
    assert restricted_result.error is not None
    assert restricted_result.error.category == "RESTRICTED"
    assert restricted_result.error.retryable is False


def test_resolver_maps_parse_timeout_to_connect_timeout_not_unknown():
    class TimeoutAdapter:
        platform = "bilibili"

        def can_handle(self, url):
            return True

        def parse_with_context(self, url, context):
            raise TimeoutError("timed out")

    result = resolve_stream_v2(
        ResolveRequest("https://live.bilibili.com/6?live_from=81001"),
        adapters=(TimeoutAdapter(),),
    )
    assert result.error is not None
    assert result.error.code == "CONNECT_TIMEOUT"
    assert result.error.retryable is True
    assert "超时" in result.error.user_message


def test_resolver_maps_parse_failed_timeout_text_to_connect_timeout():
    class TimeoutInfoAdapter:
        platform = "bilibili"

        def can_handle(self, url):
            return True

        def parse(self, url):
            return StreamInfo(
                platform="bilibili",
                room_url=url,
                error="连接直播服务器超时",
                error_code="parse_failed",
            )

    result = resolve_stream_v2(
        ResolveRequest("https://live.bilibili.com/6?live_from=81001"),
        adapters=(TimeoutInfoAdapter(),),
    )
    assert result.error is not None
    assert result.error.code == "CONNECT_TIMEOUT"
    assert result.error.retryable is True
    assert "超时" in result.error.user_message


def test_resolver_maps_login_restricted_to_auth_required():
    class LoginWallAdapter:
        platform = "kuaishou"

        def can_handle(self, url):
            return True

        def parse(self, url):
            return StreamInfo(
                platform="kuaishou",
                room_url=url,
                error="快手直播间当前受访问限制或需要登录。",
                error_code="restricted",
            )

    result = resolve_stream_v2(
        ResolveRequest("https://live.kuaishou.com/u/user"),
        adapters=(LoginWallAdapter(),),
    )
    assert result.error is not None
    assert result.error.category == "AUTH"
    assert "登录" in result.error.user_message


def test_select_stream_lease_invalidates_only_auth_probe_failures():
    invalidations = []

    class Provider:
        def invalidate(self, platform, account_ref="default", reason=""):
            invalidations.append((platform, account_ref, reason))

    candidate = StreamCandidate("c1", "https://cdn.example/live")
    result = resolve_stream_v2(
        ResolveRequest("https://room.example/live"),
        adapters=[],
    )
    result = result.__class__(
        platform="direct",
        room_url=result.room_url,
        candidates=(candidate,),
        capabilities=__import__("lsc.platforms.capabilities", fromlist=["get_platform_capabilities"]).get_platform_capabilities("direct"),
    )

    lease = select_stream_lease(
        result,
        {
            "c1": ProbeResult(
                "c1",
                http_status=401,
                failure_kind="AUTH_REQUIRED",
                failure_detail="Cookie rejected token=SECRET",
            )
        },
        room_id="room-1",
        lease_manager=LeaseManager(),
        credential_provider=Provider(),
        account_ref="account-1",
    )
    assert lease is None
    assert invalidations == [("direct", "account-1", "Cookie rejected token=<redacted>")]


def test_select_stream_lease_normalizes_failure_kind_enum_for_invalidation():
    invalidations = []

    class Provider:
        def invalidate(self, platform, account_ref="default", reason=""):
            invalidations.append((platform, account_ref, reason))

    candidate = StreamCandidate("c1", "https://cdn.example/live")
    result = resolve_stream_v2(ResolveRequest("https://room.example/live"), adapters=[])
    result = result.__class__(
        platform="direct",
        room_url=result.room_url,
        candidates=(candidate,),
        capabilities=__import__("lsc.platforms.capabilities", fromlist=["get_platform_capabilities"]).get_platform_capabilities("direct"),
    )
    assert select_stream_lease(
        result,
        {
            "c1": ProbeResult(
                "c1",
                failure_kind=FailureKind.AUTH_REQUIRED,
                failure_detail="expired login",
            )
        },
        room_id="room-1",
        lease_manager=LeaseManager(),
        credential_provider=Provider(),
    ) is None
    assert invalidations == [("direct", "default", "expired login")]


def test_select_stream_lease_does_not_invalidate_cdn_forbidden_probe():
    invalidations = []

    class Provider:
        def invalidate(self, *args, **kwargs):
            invalidations.append((args, kwargs))

    candidate = StreamCandidate("c1", "https://cdn.example/live")
    result = resolve_stream_v2(ResolveRequest("https://room.example/live"), adapters=[])
    result = result.__class__(
        platform="direct",
        room_url=result.room_url,
        candidates=(candidate,),
        capabilities=__import__("lsc.platforms.capabilities", fromlist=["get_platform_capabilities"]).get_platform_capabilities("direct"),
    )
    assert select_stream_lease(
        result,
        {"c1": ProbeResult("c1", http_status=403, failure_kind="CDN_FORBIDDEN")},
        room_id="room-1",
        lease_manager=LeaseManager(),
        credential_provider=Provider(),
    ) is None
    assert invalidations == []


def test_probe_uses_media_metadata_and_shared_headers(monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "streams": [
                        {"codec_type": "video", "codec_name": "h264", "time_base": "1/25"},
                        {"codec_type": "audio", "codec_name": "aac"},
                    ],
                    "packets": [{"codec_type": "video", "pts_time": "0.0"}, {"codec_type": "video", "pts_time": "0.04"}],
                    "format": {
                        "format_name": "flv",
                        "duration": "12.5",
                        "bit_rate": "1000000",
                    },
                }
            ),
            stderr="",
        )

    monkeypatch.setattr("lsc.platforms.probe.subprocess.run", fake_run)
    candidate = StreamCandidate(
        "c1",
        "https://cdn.example/live.flv?token=SECRET",
        request_headers={"Referer": "https://room.example", "Cookie": "SECRET"},
    )
    result = ProbeService("ffprobe-test").probe(
        ProbeRequest(candidate=candidate, timeout_sec=3.0)
    )
    assert result.ok
    assert result.container == "flv"
    assert result.video_codec == "h264"
    assert result.duration_ms == 12500
    assert result.probe_duration_ms >= 0
    assert result.first_byte_ms == result.first_packet_ms
    assert calls[0][0][0] == "ffprobe-test"
    assert "-i" in calls[0][0]
    assert calls[0][1].get("shell", False) is False


def test_probe_reuses_proxy_context_and_retry_after(monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(
            returncode=1,
            stdout="{}",
            stderr="HTTP error 429 Too Many Requests; Retry-After: 2.5",
        )

    monkeypatch.setattr("lsc.platforms.probe.subprocess.run", fake_run)
    result = ProbeService().probe(
        ProbeRequest(
            StreamCandidate("c1", "https://cdn.example/live"),
            network_context={"proxy_url": "http://proxy.example:8080"},
        )
    )
    assert result.http_status == 429
    assert result.retry_after_seconds == 2.5
    assert "-http_proxy" in calls[0]
    assert "http://proxy.example:8080" in calls[0]


def test_probe_http_status_ignores_quality_number_in_url(monkeypatch):
    monkeypatch.setattr(
        "lsc.platforms.probe.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=1,
            stdout="{}",
            stderr=(
                "https://d1.example/live.flv?qn=400&trid=abc "
                "Server returned 403 Forbidden (access denied)\n"
            ),
        ),
    )
    result = ProbeService().probe(
        ProbeRequest(StreamCandidate("bilibili|400|1", "https://d1.example/live.flv?qn=400"))
    )
    assert result.http_status == 403
    assert result.failure_kind == "CDN_FORBIDDEN"


def test_limit_probe_candidates_keeps_signed_cdn_budget():
    from lsc.platforms.models import PlatformCapabilities
    from lsc.platforms.resolver import limit_probe_candidates

    candidates = tuple(
        StreamCandidate(f"huya|{name}|{idx}", f"https://{name}.example/live.flv")
        for idx, name in enumerate(("source", "al", "tx", "hs"))
    )
    limited = limit_probe_candidates(
        candidates,
        PlatformCapabilities(platform="huya", max_probe_candidates=1),
    )
    assert [item.candidate_id for item in limited] == ["huya|source|0"]


def test_huya_capabilities_probe_unique_cdns_serially():
    from lsc.platforms.capabilities import get_platform_capabilities

    caps = get_platform_capabilities("huya")
    assert caps.max_connect_concurrency == 1
    assert caps.max_probe_candidates >= 3


def test_probe_candidates_serial_stops_after_first_success(monkeypatch):
    from lsc.platforms.models import ProbeResult

    seen: list[str] = []

    def fake_probe(self, request):
        seen.append(request.candidate.candidate_id)
        ok = request.candidate.candidate_id == "al"
        return ProbeResult(
            candidate_id=request.candidate.candidate_id,
            reachable=ok,
            has_video=ok,
            timestamp_ok=ok,
            failure_kind="" if ok else "CDN_FORBIDDEN",
        )

    monkeypatch.setattr(ProbeService, "probe", fake_probe)
    results = ProbeService().probe_candidates(
        [
            StreamCandidate("tx", "https://tx.example/live.flv"),
            StreamCandidate("al", "https://al.example/live.flv"),
            StreamCandidate("hs", "https://hs.example/live.flv"),
        ],
        max_concurrency=1,
    )
    assert seen == ["tx", "al"]
    assert results["al"].ok is True
    assert "hs" not in results


def test_summarize_probe_failures_includes_cdn_forbidden():
    from lsc.platforms.models import ProbeResult
    from lsc.platforms.probe import summarize_probe_failures

    message = summarize_probe_failures({
        "tx": ProbeResult("tx", failure_kind="CDN_FORBIDDEN"),
        "al": ProbeResult("al", failure_kind="CONNECT_TIMEOUT"),
    })
    assert "候选直播流未通过真实媒体探测" in message
    assert "线路被拒绝" in message
    assert "连接超时" in message


def test_probe_rejects_non_media_response(monkeypatch):
    monkeypatch.setattr(
        "lsc.platforms.probe.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=1,
            stdout="{}",
            stderr="Server returned 403 Forbidden",
        ),
    )
    result = ProbeService().probe(
        ProbeRequest(StreamCandidate("c1", "https://cdn.example/live"))
    )
    assert not result.ok
    assert result.failure_kind == "CDN_FORBIDDEN"
    assert result.http_status == 403


def test_probe_rejects_metadata_without_timestamp_progress(monkeypatch):
    monkeypatch.setattr(
        "lsc.platforms.probe.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(
                returncode=0,
                stdout=json.dumps({
                    "streams": [{"codec_type": "video", "codec_name": "h264", "time_base": "1/25"}],
                    "packets": [{"codec_type": "video", "pts_time": "0.0"}],
                    "format": {"format_name": "flv"},
                }),
            stderr="",
        ),
    )
    result = ProbeService().probe(
        ProbeRequest(StreamCandidate("c1", "https://cdn.example/live"))
    )
    assert not result.ok
    assert result.failure_kind == "TIMESTAMP_DISCONTINUITY"


@pytest.mark.parametrize(
    ("payload", "expected_failure"),
    [
        (
            {
                "streams": [{"codec_type": "video", "codec_name": "h264"}],
                "packets": [{"codec_type": "video", "pts_time": "0.0"}],
                "format": {"duration": "1.0"},
            },
            "UNSUPPORTED_PROTOCOL",
        ),
        (
            {
                "streams": [{"codec_type": "video"}],
                "packets": [{"codec_type": "video", "pts_time": "0.0"}],
                "format": {"format_name": "flv", "duration": "1.0"},
            },
            "UNSUPPORTED_CODEC",
        ),
        (
            {
                "streams": [{"codec_type": "video", "codec_name": "h264"}],
                "format": {"format_name": "flv", "duration": "1.0"},
            },
            "NO_MEDIA",
        ),
    ],
)
def test_probe_requires_container_codec_and_packet_window(
    monkeypatch,
    payload,
    expected_failure,
):
    monkeypatch.setattr(
        "lsc.platforms.probe.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout=json.dumps(payload),
            stderr="",
        ),
    )
    result = ProbeService().probe(
        ProbeRequest(StreamCandidate("c1", "https://cdn.example/live"))
    )
    assert not result.ok
    assert result.failure_kind == expected_failure
    if expected_failure == "NO_MEDIA":
        assert result.first_packet_ms == -1


def test_select_best_candidate_ignores_failed_probe():
    good = StreamCandidate("good", "https://good/live", quality_id="origin")
    bad = StreamCandidate("bad", "https://bad/live", quality_id="origin")
    selected = select_best_candidate(
        [bad, good],
        {
            "bad": ProbeResult("bad", reachable=False, failure_kind="NO_MEDIA"),
            "good": ProbeResult(
                "good",
                reachable=True,
                has_video=True,
                has_audio=True,
                timestamp_ok=True,
                protocol="flv",
            ),
        },
    )
    assert selected is not None
    assert selected.candidate_id == "good"


def test_select_best_candidate_uses_history_health_score():
    cold = StreamCandidate(
        "cold",
        "https://cold/live",
        quality_id="origin",
        raw_metadata={"history_score": 0},
    )
    healthy = StreamCandidate(
        "healthy",
        "https://healthy/live",
        quality_id="origin",
        raw_metadata={"history_score": 80},
    )
    probe = {
        item.candidate_id: ProbeResult(
            item.candidate_id,
            reachable=True,
            has_video=True,
            timestamp_ok=True,
            protocol="flv",
        )
        for item in (cold, healthy)
    }
    selected = select_best_candidate([cold, healthy], probe)
    assert selected is not None
    assert selected.candidate_id == "healthy"


def test_compat_resolver_returns_all_quality_candidates():
    class FakeAdapter:
        platform = "direct"
        display_name = "Direct"

        def can_handle(self, url):
            return url.startswith("https://room.example")

        def parse(self, url):
            return StreamInfo(
                platform="direct",
                room_url=url,
                stream_url="https://cdn.example/origin.flv",
                selected_quality="origin",
                quality_urls={
                    "origin": "https://cdn.example/origin.flv",
                    "hd": "https://cdn.example/hd.flv",
                },
                is_live=True,
            )

    result = resolve_stream_v2(
        ResolveRequest("https://room.example/live"),
        adapters=[FakeAdapter()],
    )
    assert result.ok
    assert len(result.candidates) == 2
    assert result.live_status == "LIVE"


def test_context_aware_adapter_receives_scoped_credentials():
    seen = []

    class ContextAdapter:
        platform = "direct"

        def can_handle(self, url):
            return True

        def parse_with_context(self, url, context):
            seen.append(context.purpose)
            return StreamInfo(
                platform="direct",
                room_url=url,
                stream_url="https://cdn.example/live.flv",
                is_live=True,
            )

        def parse(self, url):
            raise AssertionError("context-aware parser should be selected")

    result = resolve_stream_v2(
        ResolveRequest(
            "https://room.example/live",
            credential_context=CredentialContext(
                platform="direct",
                purpose="PROBE",
                status=CredentialStatus.AVAILABLE,
            ),
        ),
        adapters=[ContextAdapter()],
    )
    assert result.ok
    assert seen == ["PROBE"]


def test_context_aware_adapter_receives_deadline_cancellation_and_network_scope():
    import threading

    cancellation = threading.Event()
    import time

    deadline = time.monotonic() + 123.5
    seen = {}

    class ContextAdapter:
        platform = "direct"

        def can_handle(self, url):
            return True

        def parse_with_context(self, url, context):
            seen.update(
                deadline=context.deadline_monotonic,
                cancellation=context.cancellation,
                network=dict(context.network_context),
            )
            return StreamInfo(
                platform="direct",
                room_url=url,
                stream_url="https://cdn.example/live.flv",
                is_live=True,
            )

        def parse(self, url):
            raise AssertionError("context-aware parser should be selected")

    result = resolve_stream_v2(
        ResolveRequest(
            "https://room.example/live",
            deadline_monotonic=deadline,
            cancellation=cancellation,
            network_context={"proxy_url": "http://proxy.example:8080"},
        ),
        adapters=[ContextAdapter()],
    )

    assert result.ok
    assert seen["deadline"] == deadline
    assert seen["cancellation"] is cancellation
    assert seen["network"] == {"proxy_url": "http://proxy.example:8080"}


def test_v2_context_bypasses_legacy_url_only_cache_for_builtin_adapter(monkeypatch):
    from lsc.platforms.direct import DirectAdapter

    adapter = DirectAdapter()
    calls = []
    original_parse = adapter.parse

    def wrapped_parse(url):
        calls.append(url)
        return original_parse(url)

    monkeypatch.setattr(adapter, "parse", wrapped_parse)
    monkeypatch.setattr(
        "lsc.platforms.resolver.parse_stream",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("V2 context must not use the URL-only legacy cache")
        ),
    )

    result = resolve_stream_v2(
        ResolveRequest(
            "https://cdn.example/live.flv",
            credential_context=CredentialContext(
                platform="direct",
                purpose="RESOLVE",
                status=CredentialStatus.AVAILABLE,
            ),
        ),
        adapters=(adapter,),
    )

    assert result.ok
    assert calls == ["https://cdn.example/live.flv"]


def test_legacy_adapter_candidates_receive_provider_headers():
    class Adapter:
        platform = "direct"

        def can_handle(self, url):
            return True

        def parse(self, url):
            return StreamInfo(
                platform="direct",
                room_url=url,
                stream_url="https://cdn.example/live.flv",
                headers={"Referer": "https://room.example"},
                is_live=True,
            )

    provider = type(
        "Provider",
        (),
        {
            "get_context": lambda _self, *_args: CredentialContext(
                platform="direct",
                status=CredentialStatus.AVAILABLE,
                headers={"Authorization": "Bearer SECRET"},
            )
        },
    )()
    result = resolve_stream_v2(
        ResolveRequest("https://credentials.example/live"),
        adapters=(Adapter(),),
        credential_provider=provider,
    )
    assert result.ok
    assert result.candidates[0].request_headers["Authorization"] == "Bearer SECRET"


def test_resolver_converts_adapter_exception_to_structured_error():
    class BrokenAdapter:
        platform = "direct"

        def can_handle(self, url):
            return True

        def parse(self, url):
            raise RuntimeError("parse failed token=SECRET")

    result = resolve_stream_v2(
        ResolveRequest("https://broken.example/live", force_refresh=True),
        adapters=(BrokenAdapter(),),
    )
    assert not result.ok
    assert result.error is not None
    assert result.error.category == "PARSE"
    assert "SECRET" not in str(result.error.redacted())


def test_select_stream_lease_requires_successful_probe():
    candidate = StreamCandidate("c1", "https://cdn.example/live", expires_at=120.0)
    result = resolve_stream_v2(
        ResolveRequest("https://room.example/live"),
        adapters=[],
    )
    result = result.__class__(
        platform="direct",
        room_url=result.room_url,
        candidates=(candidate,),
        capabilities=__import__("lsc.platforms.capabilities", fromlist=["get_platform_capabilities"]).get_platform_capabilities("direct"),
    )
    lease = select_stream_lease(
        result,
        {
            "c1": ProbeResult(
                "c1",
                reachable=True,
                has_video=True,
                timestamp_ok=True,
            )
        },
        room_id="room-1",
        lease_manager=LeaseManager(),
        now=0.0,
    )
    assert lease is not None
    assert lease.room_id == "room-1"


def test_select_stream_lease_rejects_expired_candidate(monkeypatch):
    import time

    from lsc.platforms.capabilities import get_platform_capabilities

    now_epoch = 1_700_000_000.0
    monkeypatch.setattr(time, "time", lambda: now_epoch)
    candidate = StreamCandidate(
        "expired",
        "https://cdn.example/live",
        expires_at=now_epoch - 1,
    )
    result = ResolveRequest("https://room.example/live")
    resolved = resolve_stream_v2(result, adapters=[])
    resolved = resolved.__class__(
        platform="direct",
        room_url=resolved.room_url,
        candidates=(candidate,),
        capabilities=get_platform_capabilities("direct"),
    )
    lease = select_stream_lease(
        resolved,
        {"expired": ProbeResult("expired", reachable=True, has_video=True, timestamp_ok=True)},
        room_id="room-expired",
        lease_manager=LeaseManager(),
        now=0.0,
    )
    assert lease is None


def test_resolver_deduplicates_identical_quality_urls_and_honors_cancel():
    class Adapter:
        platform = "direct"

        def can_handle(self, url):
            return True

        def parse(self, url):
            return StreamInfo(
                platform="direct",
                room_url=url,
                stream_url="https://cdn.example/live.flv?token=a",
                quality_urls={
                    "origin": "https://cdn.example/live.flv?token=a",
                    "hd": "https://cdn.example/live.flv?token=a",
                },
                is_live=True,
            )

    result = resolve_stream_v2(ResolveRequest("https://room.example/live"), adapters=(Adapter(),))
    assert len(result.candidates) == 2
    assert result.capabilities_snapshot == result.capabilities

    import threading

    cancelled = threading.Event()
    cancelled.set()
    rejected = resolve_stream_v2(
        ResolveRequest("https://room.example/live", cancellation=cancelled),
        adapters=(Adapter(),),
    )
    assert rejected.error is not None
    assert rejected.error.code == "CANCELLED"


def test_probe_honors_cancellation_before_spawning_process(monkeypatch):
    import threading

    cancelled = threading.Event()
    cancelled.set()
    monkeypatch.setattr(
        "lsc.platforms.probe.subprocess.run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not spawn")),
    )
    result = ProbeService().probe(
        ProbeRequest(StreamCandidate("c1", "https://cdn.example/live"), cancellation=cancelled)
    )
    assert result.failure_kind == "CANCELLED"


def test_probe_cancellation_terminates_running_process(monkeypatch):
    import subprocess
    import threading

    cancelled = threading.Event()

    class FakeProcess:
        returncode = -15

        def __init__(self):
            self.terminated = False
            self.communicate_calls = 0

        def poll(self):
            return None if not self.terminated else self.returncode

        def communicate(self, timeout=None):
            if timeout is not None and self.communicate_calls == 0:
                self.communicate_calls += 1
                cancelled.set()
                raise subprocess.TimeoutExpired(["ffprobe"], timeout)
            return "", ""

        def terminate(self):
            self.terminated = True

        def wait(self, timeout=None):
            return self.returncode

        def kill(self):
            self.terminated = True

    process = FakeProcess()
    monkeypatch.setattr("lsc.platforms.probe.subprocess.Popen", lambda *args, **kwargs: process)
    result = ProbeService().probe(
        ProbeRequest(
            StreamCandidate("c1", "https://cdn.example/live"),
            cancellation=cancelled,
            timeout_sec=1.0,
        )
    )
    assert result.failure_kind == "CANCELLED"
    assert process.terminated is True


def test_probe_build_command_uses_network_proxy_context():
    request = ProbeRequest(
        StreamCandidate("c1", "https://cdn.example/live"),
        network_context={"proxy_url": "http://proxy.example:8080"},
    )
    command = ProbeService.build_command(request)
    assert "-http_proxy" in command
    assert command[command.index("-http_proxy") + 1] == "http://proxy.example:8080"


def test_probe_build_command_uses_restricted_protocol_whitelist():
    command = ProbeService.build_command(
        ProbeRequest(StreamCandidate("c1", "https://cdn.example/live"))
    )
    assert command[command.index("-protocol_whitelist") + 1] == (
        "file,http,https,tcp,tls,crypto"
    )


def test_probe_build_command_uses_scoped_network_timeouts():
    request = ProbeRequest(
        StreamCandidate("c1", "https://cdn.example/live"),
        timeout_sec=8.0,
        network_context={
            "connect_timeout_sec": 2.5,
            "read_timeout_sec": 4.0,
        },
    )
    command = ProbeService.build_command(request)
    assert command[command.index("-timeout") + 1] == "2500000"
    assert command[command.index("-rw_timeout") + 1] == "4000000"


def test_probe_build_command_clamps_malformed_timeout_context():
    request = ProbeRequest(
        StreamCandidate("c1", "https://cdn.example/live"),
        timeout_sec=8.0,
        network_context={"timeout_sec": "not-a-number", "read_timeout_sec": -2},
    )
    command = ProbeService.build_command(request)
    assert command[command.index("-timeout") + 1] == "8000000"
    assert command[command.index("-rw_timeout") + 1] == "100000"


def test_resolver_extracts_epoch_expiry_without_mutating_signed_url():
    from lsc.platforms.resolver import _candidate_from_url

    candidate = _candidate_from_url(
        platform="direct",
        quality="origin",
        url="https://cdn.example/live?expires=4102444800&token=SECRET",
        headers={},
        priority=0,
        raw={},
    )
    assert candidate is not None
    assert candidate.expires_at == 4102444800
    assert candidate.url.endswith("token=SECRET")
