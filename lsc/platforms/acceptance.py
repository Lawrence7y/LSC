"""可重复执行的真实平台验收运行器。

该模块只负责编排验收，不改变生产 Resolver/Sink 的状态机。默认只执行
Resolver -> Probe -> Lease；只有调用方明确提供输出目录和时长时，才会启动
真实进样生命周期。报告始终使用脱敏后的 URL、headers 和诊断文本。
"""
from __future__ import annotations

import json
import math
import os
import re
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any

from .candidate_health import get_default_candidate_health_store
from .failure import normalize_failure_kind
from .capabilities import uses_ingest_probe
from .lease_manager import LeaseManager
from .models import ProbeResult, ResolveRequest, StreamCandidate, StreamLease
from .redaction import redact_mapping, redact_text, redact_url
from .resolver import (
    limit_probe_candidates,
    probe_candidates,
    resolve_stream_v2,
    select_ingest_lease,
    select_stream_lease,
)


def _runtime_commit() -> str:
    """Return a short, validated revision without exposing command output."""
    try:
        root = Path(__file__).resolve().parents[2]
        completed = subprocess.run(
            ["git", "rev-parse", "--short=12", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=1.0,
            check=False,
        )
        value = (completed.stdout or "").strip().lower()
        return value if re.fullmatch(r"[0-9a-f]{7,40}", value) else "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def _runtime_version() -> str:
    value = str(os.environ.get("LSC_APP_VERSION", "unknown") or "unknown").strip()
    return value[:80] if value else "unknown"


@dataclass(frozen=True, slots=True)
class AcceptanceOptions:
    """一次验收运行的显式参数。

    ``record_dir`` 为空时不启动 FFmpeg；这使得 CI 可以安全执行控制面验收。
    """

    source_url: str
    expected_platform: str = ""
    room_id: str = "acceptance"
    ffprobe_path: str = "ffprobe"
    resolve_timeout_sec: float = 20.0
    probe_timeout_sec: float = 8.0
    max_connect_concurrency: int = 3
    requested_quality: str = ""
    account_ref: str = "default"
    record_dir: str = ""
    duration_sec: float = 0.0
    recording: bool = True
    max_no_progress_sec: float = 30.0
    preview: bool = False
    segmented: bool = True
    segment_seconds: int = 60
    network_context: dict[str, object] = field(default_factory=dict)


@dataclass(slots=True)
class AcceptanceReport:
    started_at: float
    finished_at: float = 0.0
    source_url: str = ""
    expected_platform: str = ""
    app_version: str = "unknown"
    commit: str = "unknown"
    network_context: dict[str, object] = field(default_factory=dict)
    requested_quality: str = ""
    actual_quality: str = ""
    timings_ms: dict[str, int] = field(default_factory=dict)
    platform: str = "unknown"
    support_level: str = "EXPERIMENTAL"
    credential_status: str = "NOT_CONFIGURED"
    candidate_count: int = 0
    probe_count: int = 0
    selected_lease: dict[str, object] = field(default_factory=dict)
    probes: list[dict[str, object]] = field(default_factory=list)
    lifecycle: dict[str, object] = field(default_factory=dict)
    failures: list[dict[str, object]] = field(default_factory=list)
    passed: bool = False

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(slots=True)
class AcceptanceBatchReport:
    """多个授权房间的验收汇总。

    每个房间仍使用独立的 ``AcceptanceOptions`` 和 supervisor；批量层只
    负责并发编排与汇总，避免把房间状态或明文凭据写入共享对象。
    """

    started_at: float
    finished_at: float = 0.0
    reports: list[dict[str, object]] = field(default_factory=list)
    passed: bool = False
    mode: str = "batch"
    attempts: int = 0
    successful_attempts: int = 0
    required_successes: int = 0
    success_rate: float = 0.0

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(slots=True)
class AcceptanceSuiteReport:
    """Three-stage real-media verification report.

    The suite covers recording-only, preview-only and parallel operation with
    fresh supervisors for each stage. Network interruption and application
    restart remain explicit operator gates because they require manipulating
    the target environment, not merely restarting an in-process object.
    """

    started_at: float
    finished_at: float = 0.0
    source_url: str = ""
    expected_platform: str = ""
    platform: str = "unknown"
    stages: list[dict[str, object]] = field(default_factory=list)
    external_gates: dict[str, str] = field(default_factory=lambda: {
        "network_interrupt_recovery": "REQUIRES_OPERATOR",
        "application_restart_recovery": "REQUIRES_OPERATOR",
    })
    passed: bool = False

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


_OPERATOR_GATE_NAMES = (
    "network_interrupt_recovery",
    "application_restart_recovery",
)


def _operator_gate_is_passed(value: object) -> bool:
    """Accept only explicit operator acknowledgement for external gates."""
    if value is True:
        return True
    if isinstance(value, Mapping):
        status = str(value.get("status", "") or "").strip().upper()
        return status in {"PASS", "PASSED", "VERIFIED"}
    return str(value or "").strip().upper() in {"PASS", "PASSED", "VERIFIED"}


def _probe_snapshot(probe: Any) -> dict[str, object]:
    raw_failure_code = (
        getattr(probe, "failure_code", "")
        or getattr(probe, "failure_kind", "")
        or ""
    )
    raw_failure_kind = getattr(probe, "failure_kind", "") or ""
    return {
        "candidate_id": str(getattr(probe, "candidate_id", "")),
        "ok": bool(getattr(probe, "ok", False)),
        "success": bool(getattr(probe, "success", getattr(probe, "ok", False))),
        "failure_code": (
            normalize_failure_kind(raw_failure_code).value
            if raw_failure_code
            else ""
        ),
        "reachable": bool(getattr(probe, "reachable", False)),
        "http_status": getattr(probe, "http_status", None),
        "protocol": str(getattr(probe, "protocol", "") or ""),
        "container": str(getattr(probe, "container", "") or ""),
        "video_codec": str(getattr(probe, "video_codec", "") or ""),
        "audio_codec": str(getattr(probe, "audio_codec", "") or ""),
        "has_video": bool(getattr(probe, "has_video", False)),
        "has_audio": bool(getattr(probe, "has_audio", False)),
        "timestamp_ok": bool(getattr(probe, "timestamp_ok", False)),
        "first_packet_ms": int(getattr(probe, "first_packet_ms", -1) or -1),
        "first_byte_ms": int(getattr(probe, "first_byte_ms", getattr(probe, "first_packet_ms", -1)) or -1),
        "duration_ms": int(getattr(probe, "duration_ms", -1) or -1),
        "probe_duration_ms": int(getattr(probe, "probe_duration_ms", -1) or -1),
        "read_bytes": int(getattr(probe, "read_bytes", 0) or 0),
        "retry_after_seconds": getattr(probe, "retry_after_seconds", None),
        "server_id": str(getattr(probe, "server_id", "") or ""),
        "cdn_id": str(getattr(probe, "cdn_id", "") or ""),
        "failure_kind": (
            normalize_failure_kind(raw_failure_kind).value
            if raw_failure_kind
            else ""
        ),
        "failure_detail": redact_text(getattr(probe, "failure_detail", "")),
    }


def _lease_snapshot(lease: StreamLease | None) -> dict[str, object]:
    if lease is None:
        return {}
    payload = lease.redacted()
    payload["probe_summary"] = redact_mapping(lease.probe_summary)
    return payload


def _event_summary(events: list[dict[str, object]]) -> dict[str, object]:
    """Summarize lifecycle events without exposing event payload secrets."""
    counts: dict[str, int] = {}
    recovery_ids: list[str] = []
    generations: list[int] = []
    for event in events:
        event_type = str(event.get("event_type", "") or "")
        if event_type:
            counts[event_type] = counts.get(event_type, 0) + 1
        recovery_id = str(event.get("recovery_id", "") or "")
        if recovery_id and recovery_id not in recovery_ids:
            recovery_ids.append(recovery_id)
        generation = event.get("generation")
        if isinstance(generation, int) and generation not in generations:
            generations.append(generation)
    return {
        "event_counts": counts,
        "recovery_ids": recovery_ids,
        "generations": sorted(generations),
        "recovery_count": sum(
            count for name, count in counts.items()
            if name.startswith("RECOVERY_")
        ),
    }


def _record_failure(report: AcceptanceReport, code: str, detail: object = "") -> None:
    report.failures.append({
        "code": str(code),
        "detail": redact_text(detail),
    })


def _bind_ingest_lease(ingest: Any, manager: LeaseManager | None, lease: StreamLease | None) -> None:
    bind = getattr(ingest, "bind_lease", None)
    if not callable(bind) or manager is None or lease is None:
        return
    bind(manager, getattr(lease, "lease_id", ""))


def _run_lifecycle(
    options: AcceptanceOptions,
    *,
    lease: StreamLease,
    platform: str,
    canonical_room_id: str,
    lease_manager: LeaseManager | None = None,
    recovery_fn: Callable[[Any, str], bool] | None = None,
    event_sink: Callable[[Any], None] | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> dict[str, object]:
    """Run recording/preview sinks for an explicitly requested interval."""
    if not options.record_dir or options.duration_sec <= 0:
        return {"status": "SKIPPED", "reason": "record_dir_or_duration_not_set"}

    from lsc.core.services.ingest_supervisor import IngestSupervisor
    from lsc.core.services.shared_ingest import SharedRoomIngest

    output_root = Path(options.record_dir).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    output_path = output_root / f"acceptance-{options.room_id}.mkv"
    ingest = SharedRoomIngest(
        room_id=options.room_id,
        url=lease.candidate.url,
        headers=dict(lease.candidate.request_headers),
        network_context=dict(options.network_context),
    )
    events: list[dict[str, object]] = []

    def on_event(event: Any) -> None:
        try:
            payload = event.to_dict() if hasattr(event, "to_dict") else dict(event)
        except Exception:
            payload = {"event_type": type(event).__name__}
        events.append(redact_mapping(payload))
        if event_sink is not None:
            event_sink(event)

    supervisor = IngestSupervisor(options.room_id, ingest, event_callback=on_event)
    supervisor.set_lease_context(
        session_id=f"acceptance-{options.room_id}",
        platform_id=platform,
        lease_id=lease.lease_id,
        candidate_id=lease.candidate.candidate_id,
        generation=lease.generation,
        quality_id=lease.candidate.quality_id,
        protocol=lease.candidate.protocol,
        cdn_id=lease.candidate.cdn_id,
        expires_at=lease.expires_at,
        refresh_at=lease.refresh_at,
    )
    _bind_ingest_lease(ingest, lease_manager, lease)
    preview_handle = None
    started_recording = False
    started_preview = False
    cleaned = False

    def finish(payload: dict[str, object]) -> dict[str, object]:
        """Stop every sink exactly once and attach post-cleanup evidence."""
        nonlocal cleaned
        if cleaned:
            return payload
        cleaned = True
        cleanup_error = ""
        try:
            if started_recording:
                supervisor.stop_recording("acceptance complete")
            if preview_handle is not None:
                supervisor.stop_preview("acceptance complete")
            ingest.stop("acceptance complete")
        except Exception as exc:
            cleanup_error = redact_text(exc)
            events.append({"event_type": "CLEANUP_FAILED", "error": cleanup_error})
        cleanup_health = supervisor.health()
        payload["cleanup_health"] = redact_mapping(cleanup_health)
        payload["resources_released"] = bool(
            cleanup_health.get("upstream_pid") is None
            and cleanup_health.get("recording_pid") is None
            and cleanup_health.get("preview_pid") is None
            and int(cleanup_health.get("preview_subscribers", 0) or 0) == 0
            and not bool(cleanup_health.get("recording_active"))
            and not cleanup_error
        )
        if cleanup_error:
            payload["cleanup_error"] = cleanup_error
        if options.segmented:
            manifest_path = str(
                payload.get("manifest_path")
                or cleanup_health.get("manifest_path", "")
                or ""
            )
            if not started_recording:
                payload["segment_validation"] = {}
                payload["segments_valid"] = None
                payload["recording_validation_status"] = "SKIPPED"
                payload["events"] = events
                payload.update(_event_summary(events))
                return payload
            if not manifest_path:
                payload["segment_validation"] = {}
                payload["segments_valid"] = False
                payload["recording_validation_status"] = "FAILED"
                payload["segment_validation_error"] = "recording manifest missing"
                payload["events"] = events
                payload.update(_event_summary(events))
                return payload
            try:
                from lsc.recorder.assets import RecordingAsset

                asset = RecordingAsset.recover(manifest_path)
                validation = asset.validate_segments(
                    ffprobe_path=options.ffprobe_path,
                    timeout=max(1.0, min(30.0, float(options.probe_timeout_sec))),
                )
                payload["segment_validation"] = redact_mapping(validation)
                payload["segments_valid"] = bool(validation) and all(
                    bool(details.get("readable"))
                    for details in validation.values()
                )
            except Exception as exc:
                payload["segment_validation"] = {}
                payload["segments_valid"] = False
                payload["segment_validation_error"] = redact_text(exc)
        else:
            if not started_recording:
                payload["recording_valid"] = None
                payload["recording_validation_status"] = "SKIPPED"
                payload["events"] = events
                payload.update(_event_summary(events))
                return payload
            try:
                from lsc.recorder.capture import validate_recording

                valid, validation_error = validate_recording(str(output_path))
                payload["recording_valid"] = bool(valid)
                if validation_error:
                    payload["recording_validation_error"] = redact_text(validation_error)
            except Exception as exc:
                payload["recording_valid"] = False
                payload["recording_validation_error"] = redact_text(exc)
        payload["events"] = events
        payload.update(_event_summary(events))
        return payload

    try:
        if options.preview:
            preview_handle = supervisor.attach_preview(
                on_init_segment=lambda _segment: None,
                on_media_segment=lambda _segment: None,
            )
            started_preview = True
        if options.recording:
            started_recording = supervisor.start_recording(
                str(output_path),
                segmented=options.segmented,
                segment_seconds=options.segment_seconds,
                platform_id=platform,
                canonical_room_id=canonical_room_id,
            )
        else:
            started_recording = False
        initial_health = supervisor.health()
        max_no_progress = max(1.0, float(options.max_no_progress_sec))
        last_recording_size = int(initial_health.get("recording_size_bytes", 0) or 0)
        last_upstream_bytes = int(initial_health.get("upstream_bytes", 0) or 0)
        last_preview_segments = int(
            initial_health.get("preview_segment_count", 0) or 0
        )
        last_recording_progress = time.monotonic()
        last_upstream_progress = last_recording_progress
        last_preview_progress = last_recording_progress

        def attempt_recovery(health: Mapping[str, object]) -> bool:
            nonlocal started_recording
            nonlocal last_recording_progress
            nonlocal last_upstream_progress
            nonlocal last_preview_progress
            nonlocal last_recording_size
            nonlocal last_upstream_bytes
            nonlocal last_preview_segments
            if recovery_fn is None:
                return False
            raw_failure_kind = health.get("failure_kind", "") or ""
            failure_kind = (
                normalize_failure_kind(raw_failure_kind).value
                if raw_failure_kind
                else ""
            )
            # Authentication, offline and local resource failures require a
            # user/operator action; repeatedly resolving them would violate
            # the no-tight-retry contract and can amplify platform rate limits.
            if failure_kind in {
                "AUTH_REQUIRED",
                "AUTH_EXPIRED",
                "OFFLINE",
                "DISK_FULL",
                "PERMISSION_DENIED",
                "UNSUPPORTED_CODEC",
                "UNSUPPORTED_PROTOCOL",
            }:
                return False
            try:
                recovered = bool(
                    supervisor.run_recovery(
                        lambda _recovery_id: recovery_fn(
                            supervisor,
                            str(output_path),
                        ),
                        reason_code=str(
                            health.get("failure_kind") or "UPSTREAM_FAILURE"
                        ),
                    )
                )
            except Exception as exc:
                events.append({
                    "event_type": "ACCEPTANCE_RECOVERY_EXCEPTION",
                    "error": redact_text(exc),
                })
                return False
            if not recovered:
                return False
            refreshed = supervisor.health()
            if options.recording and not bool(refreshed.get("recording_active")):
                return False
            if options.recording:
                # The initial start can fail after spawning FFmpeg (for
                # example an immediately expired signed URL).  Recovery owns
                # the replacement sink, so reflect that fact in cleanup and
                # manifest validation bookkeeping.
                started_recording = True
            now = time.monotonic()
            last_recording_progress = now
            last_upstream_progress = now
            last_preview_progress = now
            last_recording_size = int(
                refreshed.get("recording_size_bytes", 0) or 0
            )
            last_upstream_bytes = int(
                refreshed.get("upstream_bytes", 0) or 0
            )
            last_preview_segments = int(
                refreshed.get("preview_segment_count", 0) or 0
            )
            return True

        # Give the same bounded recovery path a chance to repair an
        # immediately dead upstream/sink.  Previously these checks returned
        # before ``attempt_recovery`` was defined, making startup-time 403 or
        # signature expiry unrecoverable even though steady-state recovery
        # worked correctly.
        initial_health = supervisor.health()

        def recover_startup(
            health: Mapping[str, object],
            is_healthy: Callable[[Mapping[str, object]], bool],
        ) -> tuple[bool, Mapping[str, object]]:
            """Try a small, supervisor-bounded number of startup repairs.

            A signed URL can pass ffprobe and still be rejected by the real
            FFmpeg ingest a moment later.  Startup therefore needs the same
            bounded candidate-failover path as steady state, instead of
            giving up after the first sink process exits.
            """
            current = health
            for _ in range(3):
                if is_healthy(current):
                    return True, current
                if not attempt_recovery(current):
                    current = supervisor.health()
                    continue
                current = supervisor.health()
            return is_healthy(current), current

        if options.recording and (
            not started_recording or not bool(initial_health.get("recording_active"))
        ):
            recovered, initial_health = recover_startup(
                initial_health,
                lambda value: bool(value.get("recording_active")),
            )
            if not recovered:
                return finish({
                    "status": "FAILED",
                    "recording_started": started_recording,
                    "preview_started": started_preview,
                    "reason": "recording_not_active_after_start",
                    "recovery_attempted": recovery_fn is not None,
                    "events": events,
                    **_event_summary(events),
                    "health": redact_mapping(supervisor.health()),
                })
        elif not options.recording and not bool(initial_health.get("upstream_pid")):
            recovered, initial_health = recover_startup(
                initial_health,
                lambda value: bool(value.get("upstream_pid")),
            )
            if not recovered:
                return finish({
                    "status": "FAILED",
                    "recording_started": False,
                    "preview_started": started_preview,
                    "reason": "preview_upstream_not_active_after_start",
                    "recovery_attempted": recovery_fn is not None,
                    "events": events,
                    **_event_summary(events),
                    "health": redact_mapping(supervisor.health()),
                })
        if options.preview and not bool(initial_health.get("preview_pid")):
            recovered, initial_health = recover_startup(
                initial_health,
                lambda value: bool(value.get("preview_pid")),
            )
            if not recovered:
                return finish({
                    "status": "FAILED",
                    "recording_started": options.recording,
                    "preview_started": started_preview,
                    "reason": "preview_not_active_after_start",
                    "recovery_attempted": recovery_fn is not None,
                    "events": events,
                    **_event_summary(events),
                    "health": redact_mapping(supervisor.health()),
                })

        deadline = time.monotonic() + max(0.1, float(options.duration_sec))
        while time.monotonic() < deadline:
            sleep_fn(min(0.5, max(0.01, deadline - time.monotonic())))
            health = supervisor.health()
            # Recording is the lossless sink and must survive a preview
            # disconnect.  When the upstream/recording process has failed,
            # give the injected V2 recovery coordinator a bounded opportunity
            # to resolve a fresh lease and restart only the failed sink.  A
            # preview degradation is reported separately and does not mask a
            # healthy recording.
            if options.recording and not bool(health.get("recording_active")):
                if attempt_recovery(health):
                    continue
                return finish({
                    "status": "FAILED",
                    "recording_started": options.recording,
                    "preview_started": started_preview,
                    "recovery_attempted": recovery_fn is not None,
                    "reason": "recording_stopped_during_acceptance",
                    "events": events,
                    **_event_summary(events),
                    "health": redact_mapping(health),
                })
            now = time.monotonic()
            recording_size = int(health.get("recording_size_bytes", 0) or 0)
            upstream_bytes = int(health.get("upstream_bytes", 0) or 0)
            preview_segments = int(health.get("preview_segment_count", 0) or 0)
            if options.recording and recording_size > last_recording_size:
                last_recording_size = recording_size
                last_recording_progress = now
            if upstream_bytes > last_upstream_bytes:
                last_upstream_bytes = upstream_bytes
                last_upstream_progress = now
            if preview_segments > last_preview_segments:
                last_preview_segments = preview_segments
                last_preview_progress = now
            if options.recording and now - last_recording_progress > max_no_progress:
                if attempt_recovery(health):
                    continue
                return finish({
                    "status": "FAILED",
                    "recording_started": options.recording,
                    "preview_started": started_preview,
                    "reason": "recording_media_progress_stalled",
                    "events": events,
                    **_event_summary(events),
                    "health": redact_mapping(health),
                })
            if now - last_upstream_progress > max_no_progress:
                if attempt_recovery(health):
                    continue
                return finish({
                    "status": "FAILED",
                    "recording_started": options.recording,
                    "preview_started": started_preview,
                    "reason": "upstream_media_progress_stalled",
                    "events": events,
                    **_event_summary(events),
                    "health": redact_mapping(health),
                })
            if started_preview and now - last_preview_progress > max_no_progress:
                if attempt_recovery(health):
                    continue
                return finish({
                    "status": "FAILED",
                    "recording_started": options.recording,
                    "preview_started": True,
                    "preview_status": "DEGRADED",
                    "reason": "preview_media_progress_stalled",
                    "events": events,
                    **_event_summary(events),
                    "health": redact_mapping(health),
                })
        health = supervisor.health()
        preview_degraded = bool(
            started_preview
            and (
                getattr(ingest, "preview_error", "")
                or int(health.get("preview_segment_count", 0) or 0) <= 0
                or any(
                    str(item.get("stage", "")) == "preview"
                    and str(item.get("event_type", "")) in {"SINK_FAILED", "UPSTREAM_FAILED"}
                    for item in events
                )
            )
        )
        return finish({
            "status": "PASSED",
            "recording_started": options.recording,
            "preview_started": started_preview,
            "preview_status": "DEGRADED" if preview_degraded else "PASSED",
            "output_path": str(output_path),
            "manifest_path": str(health.get("manifest_path", "") or ""),
            "events": events,
            **_event_summary(events),
            "health": redact_mapping(health),
        })
    except Exception as exc:
        return finish({
            "status": "FAILED",
            "recording_started": started_recording,
            "preview_started": started_preview,
            "error": redact_text(exc),
            "events": events,
            **_event_summary(events),
            "health": redact_mapping(supervisor.health()),
        })


def run_acceptance(
    options: AcceptanceOptions,
    *,
    resolve_fn: Callable[..., Any] = resolve_stream_v2,
    probe_fn: Callable[..., dict[str, Any]] = probe_candidates,
    lease_manager: LeaseManager | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> AcceptanceReport:
    """执行一次控制面验收，必要时再执行真实录制/预览生命周期。"""
    started = time.time()
    report = AcceptanceReport(
        started_at=started,
        source_url=redact_url(options.source_url),
        expected_platform=str(options.expected_platform or "").strip().lower(),
        app_version=_runtime_version(),
        commit=_runtime_commit(),
        network_context=redact_mapping(options.network_context),
        requested_quality=options.requested_quality,
    )
    monotonic_started = time.monotonic()

    def finish_report() -> AcceptanceReport:
        report.timings_ms["total"] = max(
            0,
            int((time.monotonic() - monotonic_started) * 1000),
        )
        report.finished_at = time.time()
        return report

    deadline = time.monotonic() + max(0.1, float(options.resolve_timeout_sec))
    resolve_started = time.monotonic()
    try:
        result = resolve_fn(
            ResolveRequest(
                source_url=options.source_url,
                requested_quality=options.requested_quality,
                account_ref=options.account_ref,
                force_refresh=True,
                request_id=f"acceptance:{options.room_id}",
                deadline_monotonic=deadline,
                network_context=dict(options.network_context),
            )
        )
    except Exception as exc:
        report.timings_ms["resolve"] = max(
            0, int((time.monotonic() - resolve_started) * 1000)
        )
        _record_failure(report, "RESOLVE_EXCEPTION", exc)
        return finish_report()
    report.timings_ms["resolve"] = max(
        0, int((time.monotonic() - resolve_started) * 1000)
    )

    report.platform = str(getattr(result, "platform", "unknown") or "unknown")
    expected_platform = report.expected_platform
    if expected_platform and report.platform.strip().lower() != expected_platform:
        _record_failure(
            report,
            "PLATFORM_MISMATCH",
            f"expected {expected_platform}, resolved {report.platform}",
        )
        return finish_report()
    capabilities = getattr(result, "capabilities", None)
    report.support_level = str(getattr(capabilities, "support_level", "EXPERIMENTAL"))
    credential_status = getattr(result, "credential_status", "NOT_CONFIGURED")
    report.credential_status = (
        getattr(credential_status, "value", str(credential_status))
        if credential_status is not None
        else "NOT_CONFIGURED"
    )
    candidates = tuple(getattr(result, "candidates", ()) or ())
    report.candidate_count = len(candidates)
    if getattr(result, "error", None) is not None:
        error = result.error
        _record_failure(report, getattr(error, "code", "RESOLVE_FAILED"), getattr(error, "user_message", error))
    if not candidates:
        _record_failure(report, "NO_CANDIDATE", "resolver returned no candidates")
        return finish_report()

    manager = lease_manager or LeaseManager()
    probe_started = time.monotonic()
    probes: dict = {}
    if uses_ingest_probe(capabilities):
        report.timings_ms["probe"] = 0
        report.probe_count = 0
        report.probes = []
        lease = select_ingest_lease(
            result,
            room_id=options.room_id,
            lease_manager=manager,
            requested_quality=options.requested_quality,
        )
    else:
        try:
            probes = probe_fn(
                limit_probe_candidates(candidates, capabilities),
                ffprobe_path=options.ffprobe_path,
                timeout_sec=min(float(options.probe_timeout_sec), max(0.1, deadline - time.monotonic())),
                max_concurrency=max(1, int(options.max_connect_concurrency)),
                request_id=f"acceptance-probe:{options.room_id}",
                deadline_monotonic=deadline,
                network_context=dict(options.network_context),
                platform=report.platform,
                account_ref=options.account_ref,
            )
        except Exception as err:
            report.timings_ms["probe"] = max(
                0, int((time.monotonic() - probe_started) * 1000)
            )
            _record_failure(report, "PROBE_EXCEPTION", err)
            return finish_report()
        report.timings_ms["probe"] = max(
            0, int((time.monotonic() - probe_started) * 1000)
        )
        report.probe_count = len(probes)
        report.probes = [_probe_snapshot(item) for item in probes.values()]
        lease = select_stream_lease(
            result,
            probes,
            room_id=options.room_id,
            lease_manager=manager,
            requested_quality=options.requested_quality,
        )
    report.selected_lease = _lease_snapshot(lease)
    if lease is None:
        _record_failure(
            report,
            "NO_PLAYABLE_CANDIDATE",
            "未找到可播放的直播流"
            if uses_ingest_probe(capabilities)
            else "no candidate passed real media probe",
        )
        return finish_report()

    report.actual_quality = str(getattr(lease.candidate, "quality_id", "") or "")
    current_candidate: StreamCandidate = lease.candidate
    # Do not immediately retry the lease that just failed to start.  A
    # signed URL may be accepted by ffprobe and rejected by the subsequent
    # FFmpeg process; the first recovery should therefore prefer a different
    # CDN/line whenever one exists.  If it is the only candidate, the filter
    # naturally falls back to it so transient failures remain recoverable.
    failed_candidate_ids: set[str] = {lease.candidate.candidate_id}

    def recover_lifecycle(supervisor: Any, output_path: str) -> bool:
        """Resolve and install one fresh lease for the acceptance run.

        The callback is intentionally bounded by the same resolver/probe
        contracts used for the initial connection.  It never mutates the
        caller's room state and lets ``IngestSupervisor`` serialize retries.
        """
        nonlocal current_candidate
        previous_health = supervisor.health()
        previous_failure = normalize_failure_kind(
            str(previous_health.get("failure_kind", "") or previous_health.get("reason_code", ""))
        ).value
        if previous_failure and current_candidate.candidate_id:
            failed_candidate_ids.add(current_candidate.candidate_id)
            # A real ingest failure is stronger evidence than a successful
            # ffprobe snapshot: signed CDNs can reject the subsequent FFmpeg
            # connection.  Feed it into the same scoped health store so the
            # next resolver/probe cycle changes line when possible.
            get_default_candidate_health_store().record(
                current_candidate,
                ProbeResult(
                    candidate_id=current_candidate.candidate_id,
                    failure_kind=previous_failure,
                    failure_detail=str(previous_health.get("last_error", "") or ""),
                ),
                platform=report.platform,
                account_ref=options.account_ref,
                network_context=options.network_context,
            )
        recovery_deadline = time.monotonic() + max(
            0.1,
            float(options.resolve_timeout_sec),
        )
        next_result = resolve_fn(
            ResolveRequest(
                source_url=options.source_url,
                requested_quality=options.requested_quality,
                account_ref=options.account_ref,
                force_refresh=True,
                request_id=f"acceptance-recovery:{options.room_id}",
                deadline_monotonic=recovery_deadline,
                network_context=dict(options.network_context),
            )
        )
        next_candidates = tuple(getattr(next_result, "candidates", ()) or ())
        alternate_candidates = tuple(
            candidate
            for candidate in next_candidates
            if candidate.candidate_id not in failed_candidate_ids
        )
        if alternate_candidates:
            next_candidates = alternate_candidates
        if not next_candidates:
            return False
        ingest_recovery = uses_ingest_probe(getattr(next_result, "capabilities", None))
        if ingest_recovery:
            next_probes = {}
            preferred_lease = select_ingest_lease(
                next_result,
                room_id=options.room_id,
                lease_manager=manager,
                requested_quality=options.requested_quality,
            )
        else:
            next_probes = probe_fn(
                next_candidates,
                ffprobe_path=options.ffprobe_path,
                timeout_sec=min(
                    float(options.probe_timeout_sec),
                    max(0.1, recovery_deadline - time.monotonic()),
                ),
                max_concurrency=max(1, int(options.max_connect_concurrency)),
                request_id=f"acceptance-recovery-probe:{options.room_id}",
                deadline_monotonic=recovery_deadline,
                network_context=dict(options.network_context),
                platform=str(getattr(next_result, "platform", "") or report.platform),
                account_ref=options.account_ref,
            )
            preferred_lease = select_stream_lease(
                next_result,
                next_probes,
                room_id=options.room_id,
                lease_manager=manager,
                requested_quality=options.requested_quality,
            )
        if preferred_lease is None:
            return False
        report.candidate_count = max(
            report.candidate_count,
            len(next_candidates),
        )
        report.probe_count = max(report.probe_count, len(next_probes))
        next_platform = str(
            getattr(next_result, "platform", report.platform) or report.platform
        )
        next_canonical_room_id = str(
            getattr(next_result, "canonical_room_id", "")
            or getattr(result, "canonical_room_id", "")
            or ""
        )
        ordered_candidates = [preferred_lease.candidate]
        ordered_candidates.extend(
            candidate
            for candidate in next_candidates
            if candidate.candidate_id != preferred_lease.candidate.candidate_id
        )
        available_candidates = [
            candidate
            for candidate in ordered_candidates
            if candidate.candidate_id not in failed_candidate_ids
        ]
        if available_candidates:
            ordered_candidates = available_candidates
        else:
            # A single-CDN stream has no alternate line; permit one retry of
            # the preferred lease after its URL has been freshly resolved.
            ordered_candidates = [preferred_lease.candidate]
        # One recovery callback may try several already-probed lines.  This
        # avoids handing control back to the supervisor's backoff after the
        # first FFmpeg startup probe failure, while still bounding work by the
        # resolver's finite candidate set.
        for candidate in ordered_candidates:
            if ingest_recovery:
                if candidate.candidate_id == preferred_lease.candidate.candidate_id:
                    candidate_lease = preferred_lease
                else:
                    candidate_lease = select_ingest_lease(
                        replace(next_result, candidates=(candidate,)),
                        room_id=options.room_id,
                        lease_manager=manager,
                        requested_quality=options.requested_quality,
                    )
            else:
                candidate_lease = select_stream_lease(
                    replace(next_result, candidates=(candidate,)),
                    {candidate.candidate_id: next_probes.get(candidate.candidate_id)},
                    room_id=options.room_id,
                    lease_manager=manager,
                    requested_quality=options.requested_quality,
                )
            if candidate_lease is None:
                failed_candidate_ids.add(candidate.candidate_id)
                continue
            current_candidate = candidate_lease.candidate
            report.selected_lease = _lease_snapshot(candidate_lease)
            supervisor.set_lease_context(
                session_id=f"acceptance-{options.room_id}",
                platform_id=next_platform,
                lease_id=candidate_lease.lease_id,
                candidate_id=candidate_lease.candidate.candidate_id,
                generation=candidate_lease.generation,
                quality_id=candidate_lease.candidate.quality_id,
                protocol=candidate_lease.candidate.protocol,
                cdn_id=candidate_lease.candidate.cdn_id,
                expires_at=candidate_lease.expires_at,
                refresh_at=candidate_lease.refresh_at,
            )
            _bind_ingest_lease(getattr(supervisor, "ingest", None), manager, candidate_lease)
            if not supervisor.switch_upstream(
                candidate_lease.candidate.url,
                headers=dict(candidate_lease.candidate.request_headers),
                network_context=dict(options.network_context),
                generation=candidate_lease.generation,
                reason_code="ACCEPTANCE_RECOVERY_LEASE",
            ):
                failed_candidate_ids.add(candidate_lease.candidate.candidate_id)
                continue

            ingest = supervisor.ingest
            health = supervisor.health()
            if (
                options.recording
                and not bool(health.get("recording_active"))
                and not supervisor.start_recording(
                    output_path,
                    segmented=options.segmented,
                    segment_seconds=options.segment_seconds,
                    platform_id=next_platform,
                    canonical_room_id=next_canonical_room_id,
                )
            ):
                failed_candidate_ids.add(candidate_lease.candidate.candidate_id)
                continue
            if options.preview and not health.get("preview_pid"):
                start_preview = getattr(ingest, "start_preview", None)
                if callable(start_preview):
                    preview_result = start_preview()
                    if preview_result is not None and not bool(
                        getattr(preview_result, "ok", preview_result)
                    ):
                        failed_candidate_ids.add(candidate_lease.candidate.candidate_id)
                        continue
            recovered_health = supervisor.health()
            if bool(
                recovered_health.get("recording_active")
                if options.recording
                else recovered_health.get("upstream_pid")
            ):
                return True
            failed_candidate_ids.add(candidate_lease.candidate.candidate_id)
        return False

    lifecycle_started = time.monotonic()
    report.lifecycle = _run_lifecycle(
        options,
        lease=lease,
        platform=report.platform,
        canonical_room_id=str(getattr(result, "canonical_room_id", "") or ""),
        lease_manager=manager,
        recovery_fn=recover_lifecycle,
        sleep_fn=sleep_fn,
    )
    report.timings_ms["lifecycle"] = max(
        0, int((time.monotonic() - lifecycle_started) * 1000)
    )
    if report.lifecycle.get("status") == "FAILED":
        _record_failure(report, "LIFECYCLE_FAILED", report.lifecycle.get("error", report.lifecycle.get("health", {})))
    elif report.lifecycle.get("preview_status") == "DEGRADED":
        _record_failure(report, "PREVIEW_DEGRADED", report.lifecycle.get("health", {}))
    event_counts = report.lifecycle.get("event_counts", {})
    if isinstance(event_counts, dict) and event_counts.get("RECOVERY_BUDGET_EXHAUSTED", 0):
        _record_failure(report, "RECOVERY_BUDGET_EXHAUSTED", report.lifecycle.get("health", {}))
    if (
        report.lifecycle.get("status") != "SKIPPED"
        and report.lifecycle.get("resources_released") is False
    ):
        _record_failure(
            report,
            "RESOURCE_CLEANUP_FAILED",
            report.lifecycle.get("cleanup_health", report.lifecycle.get("health", {})),
        )
    if (
        report.lifecycle.get("status") != "SKIPPED"
        and (
            report.lifecycle.get("segments_valid") is False
            or report.lifecycle.get("recording_valid") is False
        )
    ):
        _record_failure(
            report,
            "RECORDING_VALIDATION_FAILED",
            report.lifecycle.get(
                "segment_validation_error",
                report.lifecycle.get("recording_validation_error", {}),
            ),
        )
    report.passed = not report.failures
    return finish_report()


def run_acceptance_batch(
    options: Sequence[AcceptanceOptions],
    *,
    max_concurrency: int = 1,
    max_targets: int = 12,
    preview_limit: int = 4,
    run_fn: Callable[[AcceptanceOptions], AcceptanceReport] = run_acceptance,
) -> AcceptanceBatchReport:
    """并行运行多个房间验收并返回统一脱敏汇总。

    ``max_concurrency`` 只限制验收任务数，不改变单房间内部的探测并发
    上限。``max_targets`` 和 ``preview_limit`` 对应产品的房间/预览资源
    上限；超出房间不会启动进程，超出预览仅记录容量跳过。发生未捕获
    异常时也会生成结构化失败报告，保证批量验收不会因单房间异常而丢失
    其它房间的结果。
    """

    started = time.time()
    batch = AcceptanceBatchReport(started_at=started)
    items = tuple(options)
    if not items:
        _record_failure_report = {
            "source_url": "",
            "platform": "unknown",
            "failures": [{"code": "NO_TARGETS", "detail": "no acceptance targets"}],
            "passed": False,
        }
        batch.reports.append(_record_failure_report)
        batch.finished_at = time.time()
        return batch

    target_limit = max(1, int(max_targets))
    preview_limit = max(0, int(preview_limit))
    runnable_items = items[:target_limit]
    overflow_reports = [
        AcceptanceReport(
            started_at=time.time(),
            finished_at=time.time(),
            source_url=redact_url(item.source_url),
            expected_platform=str(item.expected_platform or "").strip().lower(),
            failures=[{
                "code": "RESOURCE_CAPACITY_EXCEEDED",
                "detail": f"acceptance target limit is {target_limit}",
            }],
            passed=False,
        )
        for item in items[target_limit:]
    ]

    # Preview capacity is a limit on active preview sinks, not on the
    # position of a room in the batch.  Keep a separate ordinal for preview
    # requests so ordinary recording-only rooms do not consume preview slots.
    preview_ordinals: dict[int, int] = {}
    preview_count = 0
    for index, item in enumerate(runnable_items):
        if item.preview:
            preview_ordinals[index] = preview_count
            preview_count += 1

    def run_one(index: int, item: AcceptanceOptions) -> AcceptanceReport:
        preview_suppressed = bool(
            item.preview
            and preview_ordinals.get(index, preview_limit) >= preview_limit
        )
        effective_item = replace(item, preview=False) if preview_suppressed else item
        try:
            report = run_fn(effective_item)
            if preview_suppressed and report.passed:
                report.lifecycle.update({
                    "preview_requested": True,
                    "preview_status": "SKIPPED_CAPACITY",
                    "preview_capacity": preview_limit,
                })
            return report
        except Exception as exc:  # defensive boundary for worker isolation
            report = AcceptanceReport(
                started_at=time.time(),
                source_url=redact_url(effective_item.source_url),
            )
            _record_failure(report, "ACCEPTANCE_EXCEPTION", exc)
            report.finished_at = time.time()
            return report

    workers = max(1, min(int(max_concurrency), len(items)))
    if workers == 1:
        reports = [run_one(index, item) for index, item in enumerate(runnable_items)]
    else:
        reports_by_index: dict[int, AcceptanceReport] = {}
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="platform-acceptance") as pool:
            futures = {
                pool.submit(run_one, index, item): index
                for index, item in enumerate(runnable_items)
            }
            for future in as_completed(futures):
                reports_by_index[futures[future]] = future.result()
        reports = [reports_by_index[index] for index in range(len(runnable_items))]

    reports.extend(overflow_reports)
    batch.reports = [redact_mapping(report.to_dict()) for report in reports]
    batch.attempts = len(reports)
    batch.successful_attempts = sum(1 for report in reports if report.passed)
    batch.required_successes = batch.attempts
    batch.success_rate = (
        batch.successful_attempts / batch.attempts if batch.attempts else 0.0
    )
    batch.passed = bool(reports) and batch.successful_attempts == batch.required_successes
    batch.finished_at = time.time()
    return batch


def run_acceptance_loops(
    options: AcceptanceOptions,
    *,
    iterations: int = 20,
    min_successes: int | None = None,
    max_concurrency: int = 1,
    run_fn: Callable[[AcceptanceOptions], AcceptanceReport] = run_acceptance,
) -> AcceptanceBatchReport:
    """Run repeated control-plane connection checks with an explicit pass gate.

    Connection loops intentionally disable recording and preview for every
    attempt.  They validate resolve -> probe -> lease repeatedly without
    creating media processes, so the result can be used as the 20-connection
    release gate independently from the long recording/preview run.
    """
    count = max(1, int(iterations))
    required = (
        max(1, int(min_successes))
        if min_successes is not None
        else max(1, int(math.ceil(count * 0.95)))
    )
    required = min(required, count)
    loop_options = tuple(
        replace(
            options,
            room_id=f"{options.room_id}-loop-{index + 1:02d}",
            record_dir="",
            duration_sec=0.0,
            preview=False,
        )
        for index in range(count)
    )
    batch = run_acceptance_batch(
        loop_options,
        max_concurrency=max_concurrency,
        max_targets=count,
        preview_limit=0,
        run_fn=run_fn,
    )
    batch.mode = "connection_loops"
    batch.required_successes = required
    batch.passed = batch.successful_attempts >= required
    return batch


def run_acceptance_verification_suite(
    options: AcceptanceOptions,
    *,
    recording_duration_sec: float = 15 * 60,
    preview_duration_sec: float = 15 * 60,
    parallel_duration_sec: float = 30 * 60,
    operator_evidence: Mapping[str, object] | None = None,
    run_fn: Callable[[AcceptanceOptions], AcceptanceReport] = run_acceptance,
) -> AcceptanceSuiteReport:
    """Run the three required media-lifecycle acceptance stages.

    Each stage gets a fresh room id and supervisor through ``run_fn``.  The
    suite deliberately reports network interruption and process-restart gates
    separately because those require operator control of the live environment.
    """
    started = time.time()
    suite = AcceptanceSuiteReport(
        started_at=started,
        source_url=redact_url(options.source_url),
        expected_platform=str(options.expected_platform or "").strip().lower(),
    )
    if operator_evidence:
        for gate_name in _OPERATOR_GATE_NAMES:
            if _operator_gate_is_passed(operator_evidence.get(gate_name)):
                suite.external_gates[gate_name] = "PASSED"
    if not options.record_dir:
        suite.stages.append({
            "stage": "suite",
            "status": "FAILED",
            "reason": "record_dir_required",
        })
        suite.finished_at = time.time()
        return suite
    durations = (
        ("recording_15m", recording_duration_sec, True, False),
        ("preview_15m", preview_duration_sec, False, True),
        ("parallel_30m", parallel_duration_sec, True, True),
    )
    for stage_name, duration, recording, preview in durations:
        if float(duration) <= 0:
            suite.stages.append({
                "stage": stage_name,
                "status": "FAILED",
                "reason": "duration_must_be_positive",
                "duration_sec": float(duration),
            })
            continue
        stage_options = replace(
            options,
            room_id=f"{options.room_id}-{stage_name}",
            duration_sec=float(duration),
            recording=recording,
            preview=preview,
        )
        try:
            report = run_fn(stage_options)
            payload = redact_mapping(report.to_dict())
            resolved_platform = str(payload.get("platform", "unknown") or "unknown").strip().lower()
            if suite.platform == "unknown":
                suite.platform = resolved_platform
            payload.update({
                "stage": stage_name,
                "requested_recording": recording,
                "requested_preview": preview,
                "requested_duration_sec": float(duration),
            })
            suite.stages.append(payload)
        except Exception as exc:
            suite.stages.append({
                "stage": stage_name,
                "status": "FAILED",
                "requested_recording": recording,
                "requested_preview": preview,
                "requested_duration_sec": float(duration),
                "error": redact_text(exc),
                "passed": False,
            })
    lifecycle_passed = bool(suite.stages) and len(suite.stages) == 3 and all(
        bool(stage.get("passed")) for stage in suite.stages
    )
    operator_gates_passed = all(
        suite.external_gates.get(name) == "PASSED"
        for name in _OPERATOR_GATE_NAMES
    )
    suite.passed = lifecycle_passed and operator_gates_passed
    suite.finished_at = time.time()
    return suite


def write_report(
    report: AcceptanceReport | AcceptanceBatchReport | AcceptanceSuiteReport,
    path: str,
) -> str:
    """以原子替换方式写入脱敏 JSON 报告。"""
    target = Path(path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp")
    temporary.write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    temporary.replace(target)
    return str(target)


__all__ = [
    "AcceptanceOptions",
    "AcceptanceReport",
    "AcceptanceBatchReport",
    "AcceptanceSuiteReport",
    "run_acceptance",
    "run_acceptance_batch",
    "run_acceptance_loops",
    "run_acceptance_verification_suite",
    "write_report",
]
