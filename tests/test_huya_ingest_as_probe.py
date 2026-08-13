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
