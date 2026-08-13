from __future__ import annotations

from lsc.platforms.base import StreamInfo
from lsc.platforms.recovery_policy import (
    mark_failed_candidate,
    should_force_recovery,
    should_force_refresh_when_recording,
)


def test_v2_runtime_failure_updates_candidate_health():
    from lsc.platforms.candidate_health import get_default_candidate_health_store
    from lsc.platforms.models import StreamCandidate

    store = get_default_candidate_health_store()
    store.clear()
    info = StreamInfo(
        platform="bilibili",
        room_url="https://live.bilibili.com/1",
        stream_url="https://cn-a.example/live.flv",
        raw={
            "v2": True,
            "candidate_id": "bilibili|10000|1",
            "candidate_cdn_id": "cn-a",
            "candidate_protocol": "flv",
            "candidate_quality_id": "10000",
            "account_ref": "default",
            "network_context": {"profile": "direct"},
        },
    )

    assert mark_failed_candidate(info, "HTTP 403 forbidden") is True
    snapshot = store.snapshot(
        StreamCandidate(
            candidate_id="bilibili|10000|1",
            url="",
            quality_id="10000",
            protocol="flv",
            cdn_id="cn-a",
        ),
        platform="bilibili",
        network_context={"profile": "direct"},
    )
    assert snapshot.failures == 1
    assert snapshot.last_failure_kind == "CDN_FORBIDDEN"
    store.clear()


def test_platform_recovery_policy_is_capability_driven():
    huya = StreamInfo(
        platform="huya",
        room_url="https://www.huya.com/1",
        stream_url="https://cdn1.huya.com/live.flv",
    )
    bilibili = StreamInfo(
        platform="bilibili",
        room_url="https://live.bilibili.com/1",
        stream_url="https://cdn.example/live.flv",
    )
    assert should_force_refresh_when_recording(huya) is False
    assert should_force_refresh_when_recording(bilibili) is False
    assert should_force_recovery(huya, "ffmpeg abnormal exit code=0") is False
    assert should_force_recovery(bilibili, "ffmpeg abnormal exit code=0") is False


def test_failed_candidate_policy_ignores_non_cdn_errors():
    info = StreamInfo(
        platform="bilibili",
        room_url="https://live.bilibili.com/1",
        stream_url="https://cdn.example/live.flv",
    )
    assert mark_failed_candidate(info, "disk is full") is False


def test_huya_connect_timeout_quarantines_cdn():
    from lsc.platforms.huya import _is_cdn_blacklisted, clear_cdn_blacklist

    clear_cdn_blacklist()
    info = StreamInfo(
        platform="huya",
        room_url="https://www.huya.com/1",
        stream_url="https://tx.flv.huya.com/src/live.flv",
    )
    assert mark_failed_candidate(
        info,
        "Connection to tcp://tx.flv.huya.com:443 failed: Error number -138 occurred",
        room_id="https://www.huya.com/1",
        saw_first_ts=True,
    ) is True
    assert _is_cdn_blacklisted("tx", room_key="https://www.huya.com/1")
    clear_cdn_blacklist()


def test_huya_cdn_quarantine_is_scoped_by_room_and_network():
    from lsc.platforms.huya import (
        _is_cdn_blacklisted,
        clear_cdn_blacklist,
        mark_cdn_bad,
    )

    clear_cdn_blacklist()
    mark_cdn_bad("tx", room_key="room-a", network_profile="wifi-a")

    assert _is_cdn_blacklisted("tx", room_key="room-a", network_profile="wifi-a")
    assert not _is_cdn_blacklisted("tx", room_key="room-b", network_profile="wifi-a")
    assert not _is_cdn_blacklisted("tx", room_key="room-a", network_profile="wifi-b")

    clear_cdn_blacklist(room_key="room-a", network_profile="wifi-a")
    assert not _is_cdn_blacklisted("tx", room_key="room-a", network_profile="wifi-a")


def test_huya_candidate_selection_consumes_the_same_scope():
    from lsc.platforms.huya import HuyaAdapter, clear_cdn_blacklist, mark_cdn_bad

    clear_cdn_blacklist()
    payload = {
        "stream": {
            "data": [{
                "gameStreamInfoList": [
                    {
                        "sFlvUrl": "https://al.huya.com/live",
                        "sStreamName": "room",
                        "sFlvUrlSuffix": "flv",
                    },
                    {
                        "sFlvUrl": "https://tx.huya.com/live",
                        "sStreamName": "room",
                        "sFlvUrlSuffix": "flv",
                    },
                ],
            }],
        },
    }
    mark_cdn_bad("tx", room_key="https://www.huya.com/room", network_profile="wifi-a")
    urls = HuyaAdapter()._extract_stream_urls(
        payload,
        room_key="https://www.huya.com/room",
        network_profile="wifi-a",
    )
    assert urls["source"].startswith("https://al.huya.com/")
