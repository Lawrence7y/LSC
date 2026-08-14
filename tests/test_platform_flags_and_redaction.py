from __future__ import annotations

import json
from types import SimpleNamespace

from lsc.config import (
    is_platform_pipeline_component_enabled,
    is_platform_pipeline_v2_enabled,
    load_config,
    reset_config,
)
from lsc.core.orchestrator import _room_platform_key
from lsc.platforms.redaction import (
    redact_command,
    redact_headers,
    redact_mapping,
    redact_url,
)


def test_platform_v2_flag_requires_global_switch_and_allowlist(monkeypatch, tmp_path):
    path = tmp_path / "lsc_config.json"
    path.write_text(
        json.dumps(
            {
                "platform_pipeline_v2_enabled": True,
                "platform_pipeline_v2_allowlist": ["bilibili"],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("LSC_CONFIG_PATH", str(path))
    reset_config()
    cfg = load_config()
    assert cfg.platform_pipeline_v2_enabled is True
    assert cfg.platform_pipeline_v2_allowlist == ["bilibili"]
    assert is_platform_pipeline_v2_enabled("BILIBILI", cfg)
    assert not is_platform_pipeline_v2_enabled("huya", cfg)


def test_v2_component_flags_and_segmented_opt_in():
    from lsc.config import LscConfig

    cfg = LscConfig(
        platform_pipeline_v2_enabled=True,
        platform_pipeline_v2_allowlist=["bilibili"],
        media_probe_v2=False,
        segmented_recording_v2=True,
        segmented_recording_enabled=False,
    )
    assert not is_platform_pipeline_component_enabled("media_probe", "bilibili", cfg)
    assert not is_platform_pipeline_component_enabled(
        "segmented_recording", "bilibili", cfg
    )
    cfg.segmented_recording_enabled = True
    assert is_platform_pipeline_component_enabled(
        "segmented_recording_v2", "bilibili", cfg
    )


def test_rollout_flags_support_room_user_account_and_app_version_dimensions():
    from lsc.config import LscConfig

    cfg = LscConfig(
        platform_pipeline_v2_enabled=True,
        platform_pipeline_v2_allowlist=["bilibili"],
        platform_pipeline_v2_room_allowlist=["room-1"],
        platform_pipeline_v2_user_allowlist=["user-1"],
        platform_pipeline_v2_account_allowlist=["account-1"],
        platform_pipeline_v2_app_version_allowlist=["3.1.0"],
    )
    assert is_platform_pipeline_v2_enabled(
        "bilibili",
        cfg,
        room_id="room-1",
        user_id="user-1",
        account_ref="account-1",
        app_version="3.1.0",
    )
    assert not is_platform_pipeline_v2_enabled(
        "bilibili",
        cfg,
        room_id="room-2",
        user_id="user-1",
        account_ref="account-1",
        app_version="3.1.0",
    )
    assert not is_platform_pipeline_component_enabled(
        "media_probe_v2",
        "bilibili",
        cfg,
        room_id="room-1",
        user_id="user-1",
        account_ref="account-1",
        app_version="3.0.0",
    )


def test_orchestrator_passes_room_rollout_context_to_component_gate():
    from lsc.config import LscConfig
    from lsc.core.orchestrator import RoomOrchestrator
    from lsc.core.session import RoomSession

    cfg = LscConfig(
        platform_pipeline_v2_enabled=True,
        platform_pipeline_v2_allowlist=["bilibili"],
        platform_pipeline_v2_room_allowlist=["room-1"],
        platform_pipeline_v2_user_allowlist=["user-1"],
    )
    room = RoomSession("room-1", "https://live.bilibili.com/1", platform="bilibili")
    room.network_context = {"user_id": "user-1"}
    assert RoomOrchestrator._pipeline_component_enabled(
        "media_probe_v2", room, cfg
    )


def test_invalid_allowlist_type_is_ignored(monkeypatch, tmp_path):
    path = tmp_path / "lsc_config.json"
    path.write_text(
        json.dumps(
            {
                "platform_pipeline_v2_enabled": True,
                "platform_pipeline_v2_allowlist": "bilibili",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("LSC_CONFIG_PATH", str(path))
    reset_config()
    cfg = load_config()
    assert cfg.platform_pipeline_v2_allowlist == []


def test_new_room_is_pre_routed_before_v2_flag_evaluation():
    room = SimpleNamespace(
        platform="",
        platform_name="",
        room_url="https://live.bilibili.com/12345",
    )
    assert _room_platform_key(room) == "bilibili"


def test_redact_url_keeps_safe_parts_and_hides_signature():
    value = "https://cdn.example/live.flv?quality=origin&token=SECRET&expires=123"
    redacted = redact_url(value)
    assert "https://cdn.example/live.flv" in redacted
    assert "quality=origin" in redacted
    assert "SECRET" not in redacted
    assert "123" not in redacted


def test_redact_url_scrubs_nested_signed_url_query():
    value = "https://wrapper.example/play?url=https%3A%2F%2Fcdn.example%2Flive%3Ftoken%3DNESTED"
    assert "NESTED" not in redact_url(value)


def test_redact_url_scrubs_platform_cookie_and_cdn_signature_params():
    value = (
        "https://live.example/play.m3u8?quality=origin"
        "&msToken=MS_SECRET&ttwid=TT_SECRET&hdnea=HD_SECRET"
        "&hdnts=HDNTS_SECRET&sig=SIG_SECRET&session_id=SESSION_SECRET"
    )
    redacted = redact_url(value)
    for secret in (
        "MS_SECRET",
        "TT_SECRET",
        "HD_SECRET",
        "HDNTS_SECRET",
        "SIG_SECRET",
        "SESSION_SECRET",
    ):
        assert secret not in redacted
    assert "quality=origin" in redacted


def test_redact_headers_and_command():
    headers = redact_headers(
        {"Cookie": "SESSDATA=SECRET", "Referer": "https://live.example"}
    )
    assert headers["Cookie"] == "<redacted>"
    assert headers["Referer"] == "<redacted>"
    command = redact_command(
        ["ffmpeg", "-headers", "Cookie: SECRET\r\n", "-i", "https://cdn/x?token=SECRET"]
    )
    assert "SECRET" not in str(command)
    referer_command = redact_command(
        ["ffmpeg", "-referer", "https://live.example/room?token=SECRET", "-i", "https://cdn/x"]
    )
    assert "SECRET" not in str(referer_command)
    assert referer_command[referer_command.index("-referer") + 1] == "<redacted>"


def test_redact_mapping_recurses():
    value = redact_mapping(
        {"headers": {"Cookie": "SECRET"}, "nested": [{"token": "ABC"}]}
    )
    assert value["headers"]["Cookie"] == "<redacted>"
    assert "ABC" not in str(value)


def test_room_snapshot_redacts_source_and_signed_stream_urls():
    import importlib.util

    path = "python-backend/handlers/room_handler.py"
    spec = importlib.util.spec_from_file_location("room_handler_redaction", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    room = SimpleNamespace(
        room_id="room-redaction",
        room_url="https://live.example/room?token=source-secret",
        platform="direct",
        platform_name="Direct",
        canonical_room_id="",
        streamer_name="",
        stream_title="",
        stream_info=SimpleNamespace(
            stream_url="https://cdn.example/live.flv?signature=stream-secret",
        ),
        is_connecting=False,
        is_connected=True,
        is_recording=False,
        record_output_path="",
        record_started_at=None,
        record_manifest_path="",
        preview_enabled=False,
        preview_paused=False,
        preview_muted=False,
        mark_in=None,
        mark_out=None,
        mark_in_wallclock=None,
        mark_out_wallclock=None,
        recording_start_mono=None,
        preview_latency=0.0,
        last_error="",
        record_size_mb=0.0,
        recording_media_start_mono=None,
        content_offset=0.0,
        align_group_id="",
        category="",
        preview_epoch_id="",
        recording_id="",
    )
    payload = module._room_to_dict(room)
    assert "source-secret" not in str(payload)
    assert "stream-secret" not in str(payload)
    internal = module._room_to_dict(room, redact_sensitive=False)
    assert "source-secret" in internal["room_url"]
    assert "stream-secret" not in internal["stream_url"]
