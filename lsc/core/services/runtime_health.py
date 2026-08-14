"""Read-only five-dimensional room health projection."""
from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from lsc.config import is_platform_pipeline_v2_enabled, load_config
from lsc.platforms.capabilities import get_platform_capabilities
from lsc.platforms.failure import normalize_failure_kind
from lsc.platforms.redaction import redact_text

_log = logging.getLogger(__name__)

_HEALTH_LOG_FIELDS = (
    "pipeline_mode",
    "credential_status",
    "platform",
    "support_level",
    "resolver",
    "ingest",
    "recording",
    "preview",
    "failure_kind",
)
_last_health_log: dict[str, tuple[object, ...]] = {}


def _error_text(room: Any, supervisor: Any | None) -> str:
    error = getattr(room, "last_error", "") or ""
    if not error and supervisor is not None:
        try:
            error = supervisor.health().get("last_error", "") or ""
        except Exception:
            error = ""
    return redact_text(str(error)) if error else ""


def build_room_health(room: Any, *, supervisor: Any | None = None) -> dict[str, Any]:
    """Return stable platform/resolver/ingest/recording/preview statuses."""
    error = _error_text(room, supervisor)
    platform_id = str(
        getattr(room, "platform", "")
        or getattr(room, "platform_name", "")
        or "unknown"
    ).strip().lower()
    capabilities = get_platform_capabilities(platform_id)
    try:
        cfg = load_config()
        pipeline_mode = (
            "V2"
            if is_platform_pipeline_v2_enabled(platform_id, cfg)
            and bool(getattr(cfg, "ingest_supervisor_v2", True))
            else "LEGACY"
        )
    except Exception:
        pipeline_mode = "LEGACY"
    connected = bool(getattr(room, "is_connected", False))
    connecting = bool(getattr(room, "is_connecting", False))
    live = bool(getattr(getattr(room, "stream_info", None), "is_live", connected))

    ingest_health: dict[str, Any] = {}
    if supervisor is not None:
        try:
            ingest_health = dict(supervisor.health())
        except Exception:
            ingest_health = {}
    ingest_status = str(ingest_health.get("state") or ("RUNNING" if connected else "IDLE"))
    raw_failure_kind = ingest_health.get("failure_kind", "") or ""
    failure_kind = (
        normalize_failure_kind(raw_failure_kind).value
        if raw_failure_kind
        else ""
    )

    platform_status = (
        "AUTH_REQUIRED" if failure_kind in {"AUTH_REQUIRED", "AUTH_EXPIRED"} else
        "RESTRICTED" if failure_kind in {"REGION_RESTRICTED", "CDN_FORBIDDEN"} else
        "OFFLINE" if failure_kind == "OFFLINE" else
        "CONNECTING" if connecting else
        "ERROR" if error and not connected else
        "READY" if connected and live else "OFFLINE"
    )
    resolver_status = (
        "PENDING" if connecting else
        "READY" if connected and getattr(room, "stream_info", None) is not None else
        "ERROR" if error else "IDLE"
    )

    if getattr(room, "is_recording", False):
        recording_status = "RECORDING"
    elif getattr(room, "is_recording_starting", False):
        recording_status = "STARTING"
    elif error and getattr(room, "record_output_path", ""):
        recording_status = "ERROR"
    else:
        recording_status = "IDLE"

    if getattr(room, "preview_enabled", False):
        if getattr(room, "preview_paused", False):
            preview_status = "PAUSED"
        elif getattr(room, "preview_phase", "") == "error" or getattr(room, "mse_error", ""):
            preview_status = "ERROR"
        elif getattr(room, "preview_phase", "") in {"refreshing_url", "probing"}:
            preview_status = "STARTING"
        else:
            preview_status = "PLAYING"
    elif getattr(room, "preview_error", "") or getattr(room, "mse_error", ""):
        preview_status = "ERROR"
    else:
        preview_status = "IDLE"

    credential_status = str(
        getattr(room, "credential_status", "NOT_CONFIGURED")
        or "NOT_CONFIGURED"
    )
    # Anonymous-capable platforms do not require a credential store. Treating
    # an empty context as NOT_CONFIGURED would paint every public URL orange
    # even when Cookie is optional.
    if (
        platform_id != "unknown"
        and capabilities.supports_anonymous
        and credential_status in {"", "NOT_CONFIGURED"}
    ):
        credential_status = "AVAILABLE"

    health = {
        "schema_version": 1,
        "platform": platform_status,
        "resolver": resolver_status,
        "ingest": ingest_status,
        "recording": recording_status,
        "preview": preview_status,
        "error": error,
        "failure_kind": failure_kind,
        "platform_id": platform_id,
        "pipeline_mode": pipeline_mode,
        "support_level": capabilities.support_level,
        "connection_policy": capabilities.connection_policy,
        "credential_status": credential_status,
        "credential_kinds": list(capabilities.credential_kinds),
        "lease_id": str(ingest_health.get("lease_id", "") or ""),
        "candidate_id": str(ingest_health.get("candidate_id", "") or ""),
        "quality_id": str(ingest_health.get("quality_id", "") or ""),
        "protocol": str(ingest_health.get("protocol", "") or ""),
        "cdn_id": str(ingest_health.get("cdn_id", "") or ""),
        "lease_expires_at": ingest_health.get("lease_expires_at"),
        "lease_refresh_at": ingest_health.get("lease_refresh_at"),
        "generation": int(ingest_health.get("generation", 0) or 0),
        "upstream_generation": int(
            ingest_health.get("upstream_generation", 0) or 0
        ),
        "recovery_attempt": int(ingest_health.get("recovery_attempt", 0) or 0),
        "max_recovery_attempts": int(
            ingest_health.get("max_recovery_attempts", 0) or 0
        ),
        "resources": {
            "upstream_pid": ingest_health.get("upstream_pid"),
            "recording_pid": ingest_health.get("recording_pid"),
            "preview_pid": ingest_health.get("preview_pid"),
            "preview_subscribers": int(
                ingest_health.get("preview_subscribers", 0) or 0
            ),
            "upstream_bytes": int(ingest_health.get("upstream_bytes", 0) or 0),
            "recording_size_bytes": int(
                ingest_health.get("recording_size_bytes", 0) or 0
            ),
            "preview_segment_count": int(
                ingest_health.get("preview_segment_count", 0) or 0
            ),
            "preview_media_bytes": int(
                ingest_health.get("preview_media_bytes", 0) or 0
            ),
        },
        "updated_at": __import__("time").time(),
    }
    log_room_health_if_changed(str(getattr(room, "room_id", "") or ""), health)
    return health


def log_room_health_if_changed(room_id: str, health: Mapping[str, Any] | None) -> None:
    """Write compact pipeline health to logs when the visible snapshot changes."""
    if not room_id or not health:
        return
    snapshot = tuple(str(health.get(key, "") or "") for key in _HEALTH_LOG_FIELDS)
    if _last_health_log.get(room_id) == snapshot:
        return
    _last_health_log[room_id] = snapshot
    _log.info(
        "pipeline health room=%s mode=%s cred=%s platform=%s support=%s "
        "resolver=%s ingest=%s recording=%s preview=%s failure=%s",
        room_id,
        *snapshot,
    )


def clear_health_log_cache() -> None:
    _last_health_log.clear()


__all__ = ["build_room_health", "log_room_health_if_changed", "clear_health_log_cache"]
