from types import SimpleNamespace
import logging

from lsc.core.services.runtime_health import (
    build_room_health,
    clear_health_log_cache,
    log_room_health_if_changed,
)


def test_room_health_exposes_five_dimensions():
    room = SimpleNamespace(
        is_connected=True, is_connecting=False,
        stream_info=SimpleNamespace(is_live=True), last_error="",
        is_recording=True, is_recording_starting=False,
        record_output_path="/tmp/out.ts", preview_enabled=True,
        preview_paused=False, preview_phase="streaming", mse_error="", preview_error="",
    )
    health = build_room_health(room)
    assert health["platform"] == "READY"
    assert health["resolver"] == "READY"
    assert health["ingest"] == "RUNNING"
    assert health["recording"] == "RECORDING"
    assert health["preview"] == "PLAYING"
    assert health["platform_id"] == "unknown"
    assert health["pipeline_mode"] == "LEGACY"
    assert health["credential_status"] == "NOT_CONFIGURED"
    assert health["resources"]["upstream_pid"] is None


def test_room_health_preview_failure_does_not_stop_recording():
    room = SimpleNamespace(
        is_connected=True, is_connecting=False,
        stream_info=SimpleNamespace(is_live=True), last_error="",
        is_recording=True, is_recording_starting=False,
        record_output_path="/tmp/out.ts", preview_enabled=True,
        preview_paused=False, preview_phase="error",
        mse_error="ffmpeg https://example.test/live?token=secret", preview_error="",
    )
    health = build_room_health(room)
    assert health["recording"] == "RECORDING"
    assert health["preview"] == "ERROR"
    assert "secret" not in health["error"]


def test_public_direct_stream_does_not_require_credentials():
    room = SimpleNamespace(
        platform="direct",
        is_connected=True,
        is_connecting=False,
        stream_info=SimpleNamespace(is_live=True),
        last_error="",
        is_recording=False,
        is_recording_starting=False,
        record_output_path="",
        preview_enabled=False,
        preview_paused=False,
        preview_phase="",
        mse_error="",
        preview_error="",
    )
    health = build_room_health(room)
    assert health["credential_status"] == "AVAILABLE"


def test_public_platforms_without_credentials_are_available():
    for platform in ("huya", "kuaishou", "douyu", "xiaohongshu", "weibo"):
        room = SimpleNamespace(
            platform=platform,
            is_connected=False,
            is_connecting=False,
            stream_info=None,
            last_error="",
            is_recording=False,
            is_recording_starting=False,
            record_output_path="",
            preview_enabled=False,
            preview_paused=False,
            preview_phase="",
            mse_error="",
            preview_error="",
        )
        assert build_room_health(room)["credential_status"] == "AVAILABLE"


def test_huya_optional_cookie_does_not_paint_missing_credentials():
    """虎牙可匿名解析；未配置 Cookie 时健康态仍为 AVAILABLE，但应声明 cookie 凭据。"""
    room = SimpleNamespace(
        platform="huya",
        is_connected=False,
        is_connecting=False,
        stream_info=None,
        last_error="",
        is_recording=False,
        is_recording_starting=False,
        record_output_path="",
        preview_enabled=False,
        preview_paused=False,
        preview_phase="",
        mse_error="",
        preview_error="",
    )
    health = build_room_health(room)
    assert health["credential_status"] == "AVAILABLE"
    assert "cookie" in health["credential_kinds"]


def test_room_health_projects_v2_lease_and_resource_state(monkeypatch):
    monkeypatch.setattr(
        "lsc.core.services.runtime_health.is_platform_pipeline_v2_enabled",
        lambda _platform, _cfg: True,
    )
    room = SimpleNamespace(
        platform="bilibili",
        is_connected=True,
        is_connecting=False,
        stream_info=SimpleNamespace(is_live=True),
        last_error="",
        credential_status="AVAILABLE",
        is_recording=True,
        is_recording_starting=False,
        record_output_path="out.mkv",
        preview_enabled=True,
        preview_paused=False,
        preview_phase="streaming",
        mse_error="",
        preview_error="",
    )

    class _Supervisor:
        def health(self):
            return {
                "state": "RUNNING",
                "lease_id": "lease-1",
                "candidate_id": "candidate-1",
                "quality_id": "hd",
                "protocol": "flv",
                "cdn_id": "cdn-a",
                "lease_expires_at": 4102444800.0,
                "lease_refresh_at": 4102444700.0,
                "generation": 3,
                "upstream_generation": 7,
                "recovery_attempt": 1,
                "max_recovery_attempts": 3,
                "upstream_pid": 101,
                "recording_pid": 202,
                "preview_pid": 303,
                "preview_subscribers": 1,
                "failure_kind": "CDN_FORBIDDEN",
                "upstream_bytes": 4096,
                "recording_size_bytes": 8192,
                "preview_segment_count": 4,
                "preview_media_bytes": 16384,
            }

    health = build_room_health(room, supervisor=_Supervisor())
    assert health["pipeline_mode"] == "V2"
    assert health["credential_status"] == "AVAILABLE"
    assert health["lease_id"] == "lease-1"
    assert health["quality_id"] == "hd"
    assert health["protocol"] == "flv"
    assert health["cdn_id"] == "cdn-a"
    assert health["lease_expires_at"] == 4102444800.0
    assert health["generation"] == 3
    assert health["upstream_generation"] == 7
    assert health["resources"]["upstream_pid"] == 101
    assert health["resources"]["upstream_bytes"] == 4096
    assert health["resources"]["recording_size_bytes"] == 8192
    assert health["resources"]["preview_segment_count"] == 4
    assert health["failure_kind"] == "CDN_FORBIDDEN"


def test_room_health_reports_legacy_when_supervisor_component_is_disabled(monkeypatch):
    from lsc.config import LscConfig

    cfg = LscConfig(
        platform_pipeline_v2_enabled=True,
        platform_pipeline_v2_allowlist=["bilibili"],
        ingest_supervisor_v2=False,
    )
    monkeypatch.setattr(
        "lsc.core.services.runtime_health.load_config",
        lambda: cfg,
    )
    room = SimpleNamespace(
        platform="bilibili",
        is_connected=False,
        is_connecting=False,
        stream_info=None,
        last_error="",
        is_recording=False,
        is_recording_starting=False,
        record_output_path="",
        preview_enabled=False,
        preview_paused=False,
        preview_phase="",
        mse_error="",
        preview_error="",
    )
    assert build_room_health(room)["pipeline_mode"] == "LEGACY"


def test_room_health_projects_typed_auth_failure_without_text_matching():
    room = SimpleNamespace(
        platform="bilibili",
        is_connected=False,
        is_connecting=False,
        stream_info=None,
        last_error="",
        credential_status="EXPIRED",
        is_recording=False,
        is_recording_starting=False,
        record_output_path="",
        preview_enabled=False,
        preview_paused=False,
        preview_phase="",
        mse_error="",
        preview_error="",
    )

    class _Supervisor:
        def health(self):
            return {"state": "AUTH_REQUIRED", "failure_kind": "AUTH_EXPIRED"}

    health = build_room_health(room, supervisor=_Supervisor())
    assert health["platform"] == "AUTH_REQUIRED"
    assert health["failure_kind"] == "AUTH_EXPIRED"


def test_log_room_health_if_changed_writes_once_per_snapshot(caplog):
    clear_health_log_cache()
    health = {
        "pipeline_mode": "V2",
        "credential_status": "AVAILABLE",
        "platform": "READY",
        "support_level": "PREVIEW",
        "resolver": "READY",
        "ingest": "RUNNING",
        "recording": "IDLE",
        "preview": "PLAYING",
        "failure_kind": "",
        "error": "https://cdn.example/live.flv?wsSecret=hidden",
    }
    with caplog.at_level(logging.INFO, logger="lsc.core.services.runtime_health"):
        log_room_health_if_changed("room-a", health)
        log_room_health_if_changed("room-a", health)
        health["preview"] = "ERROR"
        log_room_health_if_changed("room-a", health)
    messages = [record.getMessage() for record in caplog.records if "pipeline health" in record.getMessage()]
    assert len(messages) == 2
    assert "room=room-a" in messages[0]
    assert "preview=PLAYING" in messages[0]
    assert "preview=ERROR" in messages[1]
    assert "wsSecret" not in "".join(messages)
