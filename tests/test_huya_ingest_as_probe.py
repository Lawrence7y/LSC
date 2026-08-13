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
