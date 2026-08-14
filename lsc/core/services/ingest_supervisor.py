"""Room-level supervisor around SharedRoomIngest.

The supervisor owns lifecycle state and recovery serialization while the
existing SharedRoomIngest remains the media-process implementation. This
adapter lets legacy callers continue using SharedRoomIngest directly during
the migration window.
"""
from __future__ import annotations

import enum
import logging
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from lsc.platforms.failure import (
    classify_failure,
    extract_retry_after,
    normalize_failure_kind,
)
from lsc.platforms.redaction import redact_mapping, redact_text

from .shared_ingest import SharedPreviewHandle, SharedRoomIngest

_log = logging.getLogger(__name__)


class IngestState(str, enum.Enum):
    IDLE = "IDLE"
    STARTING = "STARTING"
    CONNECTING = "CONNECTING"
    RUNNING = "RUNNING"
    DEGRADED = "DEGRADED"
    REFRESHING = "REFRESHING"
    RECONNECTING = "RECONNECTING"
    BACKING_OFF = "BACKING_OFF"
    OFFLINE = "OFFLINE"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    FAILED = "FAILED"
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"


@dataclass(frozen=True, slots=True)
class IngestEvent:
    event_type: str
    room_id: str
    state: str
    occurred_at: float
    event_id: str = field(default_factory=lambda: f"evt-{uuid.uuid4().hex[:16]}")
    session_id: str = ""
    lease_id: str = ""
    candidate_id: str = ""
    stage: str = ""
    failure_kind: str = ""
    attempt: int = 0
    max_attempts: int = 0
    next_retry_at: float | None = None
    user_action: str = ""
    reason_code: str = ""
    recovery_id: str = ""
    generation: int = 0
    context: Mapping[str, object] = field(default_factory=dict)
    schema_version: int = 1
    # RuntimeEvent v2 aliases/additions.  They are appended with defaults so
    # legacy consumers that construct/read IngestEvent keep working.
    room_session_id: str = ""
    recording_session_id: str = ""
    platform_id: str = ""
    component: str = "ingest"
    state_from: str = ""
    state_to: str = ""
    severity: str = "INFO"
    lease_generation: int = 0
    retry_after_seconds: float | None = None
    safe_context: Mapping[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "event_id": self.event_id,
            "event_type": self.event_type,
            "room_id": self.room_id,
            "room_session_id": self.room_session_id or self.room_id,
            "recording_session_id": self.recording_session_id,
            "platform_id": self.platform_id,
            "component": self.component,
            "state": self.state,
            "state_from": self.state_from,
            "state_to": self.state_to or self.state,
            "severity": self.severity,
            "occurred_at": self.occurred_at,
            "session_id": self.session_id,
            "lease_id": self.lease_id,
            "candidate_id": self.candidate_id,
            "stage": self.stage,
            "failure_kind": self.failure_kind,
            "attempt": self.attempt,
            "max_attempts": self.max_attempts,
            "next_retry_at": self.next_retry_at,
            "user_action": self.user_action,
            "reason_code": self.reason_code,
            "recovery_id": self.recovery_id,
            "generation": self.generation,
            "lease_generation": self.lease_generation or self.generation,
            "retry_after_seconds": self.retry_after_seconds,
            "context": redact_mapping(self.context),
            "safe_context": redact_mapping(self.safe_context or self.context),
        }


class IngestSupervisor:
    """Serialize room lifecycle and isolate recording/preview operations."""

    def __init__(
        self,
        room_id: str,
        ingest: SharedRoomIngest,
        *,
        event_callback: Callable[[IngestEvent], None] | None = None,
    ) -> None:
        self.room_id = room_id
        self.ingest = ingest
        self._event_callback = event_callback
        self._event_callbacks: list[Callable[[IngestEvent], None]] = []
        if event_callback is not None:
            self._event_callbacks.append(event_callback)
        self._lock = threading.RLock()
        self._recovery_lock = threading.Lock()
        self._stop_requested = threading.Event()
        self._state = IngestState.IDLE
        self._generation = 0
        self._recovery_seq = 0
        self._active_recovery_id = ""
        self._recovery_attempt = 0
        self._max_recovery_attempts = 3
        self._next_recovery_at = 0.0
        self._recording_requested = False
        self._preview_requested = False
        self._last_error = ""
        self._last_failure_kind = ""
        self._last_transition_at = time.time()
        self._session_id = ""
        self._recording_session_id = ""
        self._platform_id = ""
        self._lease_id = ""
        self._candidate_id = ""
        self._quality_id = ""
        self._protocol = ""
        self._cdn_id = ""
        self._lease_expires_at: float | None = None
        self._lease_refresh_at: float | None = None
        add_error_callback = getattr(ingest, "add_error_callback", None)
        if callable(add_error_callback):
            add_error_callback(self._on_ingest_error)

    @property
    def state(self) -> IngestState:
        with self._lock:
            return self._state

    @property
    def generation(self) -> int:
        with self._lock:
            return self._generation

    @property
    def recording_requested(self) -> bool:
        with self._lock:
            return self._recording_requested

    @property
    def preview_requested(self) -> bool:
        with self._lock:
            return self._preview_requested

    def is_generation_current(self, generation: int) -> bool:
        """Return whether media produced by a lease generation is still valid."""
        with self._lock:
            return int(generation) == self._generation

    def begin_refresh(self, reason_code: str = "LEASE_REFRESH") -> int:
        """Enter the refresh state without stopping healthy sinks."""
        with self._lock:
            self._state = IngestState.REFRESHING
            generation = self._generation
            self._recovery_seq += 1
            self._active_recovery_id = f"{self.room_id}-refresh-{self._recovery_seq}"
            recovery_id = self._active_recovery_id
        self._emit(
            "LEASE_REFRESH_STARTED",
            reason_code=reason_code,
            recovery_id=recovery_id,
        )
        return generation

    def finish_refresh(self, success: bool, *, reason_code: str = "") -> None:
        with self._lock:
            has_sink = self._recording_requested or self._preview_requested
            recovery_id = self._active_recovery_id
            self._active_recovery_id = ""
        self._transition(
            IngestState.RUNNING if success and has_sink else (
                IngestState.BACKING_OFF if not success else IngestState.IDLE
            ),
            reason_code=reason_code or ("LEASE_REFRESH_SUCCEEDED" if success else "LEASE_REFRESH_FAILED"),
            recovery_id=recovery_id,
        )

    def set_lease_context(
        self,
        *,
        session_id: str = "",
        recording_session_id: str = "",
        platform_id: str = "",
        lease_id: str = "",
        candidate_id: str = "",
        generation: int | None = None,
        quality_id: str = "",
        protocol: str = "",
        cdn_id: str = "",
        expires_at: float | None = None,
        refresh_at: float | None = None,
    ) -> None:
        """Attach non-secret lease identity to subsequently emitted events."""
        with self._lock:
            self._session_id = str(session_id or "")
            self._recording_session_id = str(recording_session_id or session_id or "")
            if platform_id:
                self._platform_id = str(platform_id)
            self._lease_id = str(lease_id or "")
            self._candidate_id = str(candidate_id or "")
            self._quality_id = str(quality_id or "")
            self._protocol = str(protocol or "")
            self._cdn_id = str(cdn_id or "")
            self._lease_expires_at = expires_at
            self._lease_refresh_at = refresh_at
            if generation is not None:
                self._generation = max(self._generation, int(generation))

    def switch_upstream(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        network_context: Mapping[str, object] | None = None,
        generation: int | None = None,
        reason_code: str = "LEASE_GENERATION_SWITCH",
    ) -> bool:
        """Replace the remote source without detaching recording or preview.

        ``generation`` comes from the newly issued lease.  If a caller does
        not provide one, the supervisor advances its own generation so late
        bytes from the old upstream cannot be attributed to the new lease.
        """
        if self._stop_requested.is_set():
            return False
        with self._lock:
            if self._state in {IngestState.STOPPING, IngestState.STOPPED}:
                return False
            if generation is None:
                self._generation += 1
            else:
                self._generation = max(self._generation, int(generation))
            switch_generation = self._generation
            self._state = IngestState.REFRESHING
            self._recovery_seq += 1
            recovery_id = f"{self.room_id}-switch-{self._recovery_seq}"
        self._emit(
            "UPSTREAM_SWITCH_STARTED",
            reason_code=reason_code,
            recovery_id=recovery_id,
            context={"generation": switch_generation},
        )
        replace = getattr(self.ingest, "replace_upstream", None)
        if not callable(replace):
            error = "shared ingest does not support upstream replacement"
            self._transition(
                IngestState.DEGRADED,
                reason_code="UPSTREAM_SWITCH_UNSUPPORTED",
                recovery_id=recovery_id,
                failure_kind="UPSTREAM_SWITCH_UNSUPPORTED",
                context={"error": error},
            )
            return False
        try:
            try:
                error = str(
                    replace(
                        url,
                        headers=headers,
                        network_context=network_context,
                        preflight=True,
                    )
                    or ""
                )
            except TypeError as exc:
                # Keep third-party/legacy SharedIngest implementations
                # compatible while the built-in implementation performs the
                # first-packet preflight.
                if "preflight" not in str(exc):
                    raise
                error = str(
                    replace(
                        url,
                        headers=headers,
                        network_context=network_context,
                    )
                    or ""
                )
        except Exception as exc:
            error = redact_text(exc)
        if error:
            with self._lock:
                self._last_error = error
            self._transition(
                IngestState.DEGRADED,
                reason_code="UPSTREAM_SWITCH_FAILED",
                recovery_id=recovery_id,
                failure_kind="UPSTREAM_SWITCH_FAILED",
                context={"error": error},
            )
            return False
        has_sink = self.recording_requested or self.preview_requested
        self._transition(
            IngestState.RUNNING if has_sink else IngestState.IDLE,
            reason_code="UPSTREAM_SWITCH_SUCCEEDED",
            recovery_id=recovery_id,
        )
        self._emit(
            "UPSTREAM_SWITCH_SUCCEEDED",
            reason_code="UPSTREAM_SWITCH_SUCCEEDED",
            recovery_id=recovery_id,
        )
        return True

    def _emit(
        self,
        event_type: str,
        *,
        reason_code: str = "",
        recovery_id: str = "",
        context: Mapping[str, object] | None = None,
        stage: str = "",
        failure_kind: str = "",
        attempt: int = 0,
        max_attempts: int = 0,
        next_retry_at: float | None = None,
        user_action: str = "",
        state_from: str = "",
        state_to: str = "",
    ) -> None:
        with self._lock:
            session_id = self._session_id
            recording_session_id = self._recording_session_id
            platform_id = self._platform_id
            lease_id = self._lease_id
            candidate_id = self._candidate_id
            generation = self._generation
        event = IngestEvent(
            event_type=event_type,
            room_id=self.room_id,
            state=self.state.value,
            occurred_at=time.time(),
            session_id=session_id,
            lease_id=lease_id,
            candidate_id=candidate_id,
            stage=stage,
            failure_kind=failure_kind,
            attempt=max(0, int(attempt)),
            max_attempts=max(0, int(max_attempts)),
            next_retry_at=next_retry_at,
            user_action=user_action,
            reason_code=reason_code,
            recovery_id=recovery_id,
            generation=generation,
            context=dict(context or {}),
            room_session_id=self.room_id,
            recording_session_id=recording_session_id,
            platform_id=platform_id,
            component=("sink" if stage in {"recording", "preview"} else "ingest"),
            state_from=state_from,
            state_to=state_to or self.state.value,
            severity=(
                "ERROR" if failure_kind or event_type in {"SINK_FAILED", "UPSTREAM_FAILED"}
                else "INFO"
            ),
            lease_generation=generation,
            retry_after_seconds=(
                float((context or {}).get("retry_after"))
                if isinstance((context or {}).get("retry_after"), (int, float))
                else None
            ),
            safe_context=dict(context or {}),
        )
        callbacks = list(self._event_callbacks)
        if self._event_callback is not None and self._event_callback not in callbacks:
            callbacks.append(self._event_callback)
        for callback in callbacks:
            try:
                callback(event)
            except Exception as exc:
                _log.debug(
                    "ingest event callback failed room=%s event=%s: %s",
                    self.room_id,
                    event.event_type,
                    exc,
                )

    def add_event_callback(self, callback: Callable[[IngestEvent], None]) -> None:
        """Attach an idempotent observer without replacing existing sinks."""
        with self._lock:
            if callback not in self._event_callbacks:
                self._event_callbacks.append(callback)

    def _on_ingest_error(self, sink: str, error: str) -> None:
        """Project SharedRoomIngest process failures into typed events."""
        kind = classify_failure(error).value
        if sink == "preview" and kind == "UNKNOWN":
            kind = "PREVIEW_ENCODER_FAILURE"
        elif sink == "recording" and kind == "UNKNOWN":
            kind = "RECORDING_SINK_FAILURE"
        self._emit(
            "SINK_FAILED" if sink in {"preview", "recording"} else "UPSTREAM_FAILED",
            reason_code=kind,
            failure_kind=kind,
            stage=sink,
            context={"sink": sink, "error": redact_text(error)},
        )
        self.handle_failure(
            kind,
            error=error,
            retry_after=extract_retry_after(error),
        )

    def _transition(
        self,
        state: IngestState,
        *,
        reason_code: str = "",
        recovery_id: str = "",
        context: Mapping[str, object] | None = None,
        failure_kind: str = "",
        attempt: int = 0,
        max_attempts: int = 0,
        next_retry_at: float | None = None,
        user_action: str = "",
    ) -> None:
        with self._lock:
            previous_state = self._state.value
        with self._lock:
            self._state = state
            self._last_transition_at = time.time()
        self._emit(
            "INGEST_STATE_CHANGED",
            reason_code=reason_code,
            recovery_id=recovery_id,
            context=context,
            failure_kind=failure_kind,
            attempt=attempt,
            max_attempts=max_attempts,
            next_retry_at=next_retry_at,
            user_action=user_action,
            state_from=previous_state,
            state_to=state.value,
        )

    def attach_preview(
        self,
        *,
        on_init_segment: Callable[[bytes], None],
        on_media_segment: Callable[[bytes], None],
        on_error: Callable[[str], None] | None = None,
        pump_interval_sec: float = 0.05,
    ) -> SharedPreviewHandle:
        with self._lock:
            self._preview_requested = True
            if self._state in {IngestState.IDLE, IngestState.STOPPED}:
                self._state = IngestState.STARTING
        handle = SharedPreviewHandle(
            self.ingest,
            on_init_segment=on_init_segment,
            on_media_segment=on_media_segment,
            on_error=self._wrap_preview_error(on_error),
            pump_interval_sec=pump_interval_sec,
            auto_start=True,
        )
        # ``SharedPreviewHandle`` only subscribes to the distributor.  When
        # preview is the first sink there is no upstream yet, so the legacy
        # subscriber hook intentionally does not launch FFmpeg.  Start the
        # preview sink explicitly here; a later recording attach will reuse
        # the same upstream instead of creating a second remote connection.
        start_preview = getattr(self.ingest, "start_preview", None)
        if callable(start_preview):
            try:
                result = start_preview()
            except Exception as exc:
                error = redact_text(exc)
                self._wrap_preview_error(on_error)(error)
                try:
                    handle.stop()
                except Exception:
                    pass
                raise RuntimeError(error) from exc
            if result is not None and not bool(
                getattr(result, "accepted", False) or getattr(result, "ok", result)
            ):
                error = redact_text(getattr(result, "error", "preview start failed"))
                self._wrap_preview_error(on_error)(error)
                try:
                    handle.stop()
                except Exception:
                    pass
                raise RuntimeError(error)
        self._transition(
            IngestState.RUNNING
            if self.ingest.process_id is not None
            else IngestState.CONNECTING,
            reason_code="PREVIEW_ATTACHED",
        )
        self._emit("SINK_ATTACHED", context={"sink": "preview"})
        return handle

    def _wrap_preview_error(
        self,
        callback: Callable[[str], None] | None,
    ) -> Callable[[str], None]:
        def on_error(error: str) -> None:
            safe = redact_text(error)
            rotating = bool(
                getattr(self.ingest, "is_lease_rotating", lambda: False)()
            )
            if rotating:
                if callback is not None:
                    callback(safe)
                return
            with self._lock:
                self._last_error = safe
                self._state = (
                    IngestState.DEGRADED
                    if self._recording_requested
                    else IngestState.FAILED
                )
            self._emit("SINK_FAILED", reason_code="PREVIEW_SINK_FAILURE",
                       context={"sink": "preview", "error": safe})
            if callback is not None:
                callback(safe)

        return on_error

    def start_recording(
        self,
        recording_path: str,
        *,
        profile: Any | None = None,
        segmented: bool = False,
        segment_seconds: int = 60,
        platform_id: str = "",
        canonical_room_id: str = "",
        manifest_path: str = "",
    ) -> bool:
        with self._lock:
            self._recording_requested = True
            self._state = IngestState.STARTING
        self._emit("SINK_ATTACHED", context={"sink": "recording"})
        if (
            not segmented
            and segment_seconds == 60
            and not platform_id
            and not canonical_room_id
            and not manifest_path
        ):
            result = self.ingest.start_recording(recording_path, profile=profile)
        else:
            result = self.ingest.start_recording(
                recording_path,
                profile=profile,
                segmented=segmented,
                segment_seconds=segment_seconds,
                platform_id=platform_id,
                canonical_room_id=canonical_room_id,
                manifest_path=manifest_path,
            )
        if result.ok:
            self._transition(IngestState.RUNNING, reason_code="RECORDING_STARTED")
            return True
        error = redact_text(result.error)
        with self._lock:
            self._last_error = error
            self._state = (
                IngestState.DEGRADED
                if self._preview_requested
                else IngestState.FAILED
            )
        self._emit(
            "SINK_FAILED",
            reason_code="RECORDING_SINK_FAILURE",
            context={"sink": "recording", "error": error},
        )
        return False

    def stop_recording(self, reason: str = "recording stopped") -> None:
        self.ingest.stop_recording_sink(reason=reason)
        with self._lock:
            self._recording_requested = False
            has_preview = self._preview_requested
        self._emit("SINK_DETACHED", reason_code="RECORDING_STOPPED",
                   context={"sink": "recording"})
        self._transition(
            IngestState.RUNNING if has_preview else IngestState.STOPPED,
            reason_code="RECORDING_STOPPED",
        )

    def stop_preview(self, reason: str = "preview stopped") -> None:
        self.ingest.stop_preview_sink(reason=reason)
        with self._lock:
            self._preview_requested = False
            has_recording = self._recording_requested
        self._emit("SINK_DETACHED", reason_code="PREVIEW_STOPPED",
                   context={"sink": "preview"})
        self._transition(
            IngestState.RUNNING if has_recording else IngestState.STOPPED,
            reason_code="PREVIEW_STOPPED",
        )

    def run_recovery(
        self,
        callback: Callable[[str], bool],
        *,
        reason_code: str = "UPSTREAM_FAILURE",
    ) -> bool:
        """Run at most one recovery callback for this room at a time."""
        if self._stop_requested.is_set():
            return False
        if not self._recovery_lock.acquire(blocking=False):
            return False
        now = time.monotonic()
        with self._lock:
            if self._stop_requested.is_set() or self._state in {
                IngestState.STOPPING,
                IngestState.STOPPED,
            }:
                self._recovery_lock.release()
                return False
            if self._recovery_attempt >= self._max_recovery_attempts:
                self._state = IngestState.FAILED
                exhausted_id = f"{self.room_id}-recovery-budget-{self._recovery_seq + 1}"
                self._recovery_seq += 1
                self._last_error = "recovery budget exhausted"
                self._recovery_lock.release()
                self._emit(
                    "RECOVERY_BUDGET_EXHAUSTED",
                    reason_code="RECOVERY_BUDGET_EXHAUSTED",
                    recovery_id=exhausted_id,
                    attempt=self._recovery_attempt,
                    max_attempts=self._max_recovery_attempts,
                    user_action="请检查直播地址、凭据或网络后重试",
                )
                return False
            if now < self._next_recovery_at:
                retry_id = f"{self.room_id}-recovery-backoff-{self._recovery_seq}"
                next_retry = self._next_recovery_at
                self._recovery_lock.release()
                self._transition(
                    IngestState.BACKING_OFF,
                    reason_code="RECOVERY_BACKOFF",
                    recovery_id=retry_id,
                    attempt=self._recovery_attempt,
                    max_attempts=self._max_recovery_attempts,
                    next_retry_at=next_retry,
                )
                return False
            self._recovery_seq += 1
            recovery_id = f"{self.room_id}-recovery-{self._recovery_seq}"
            self._generation += 1
            self._recovery_attempt += 1
            attempt = self._recovery_attempt
        try:
            self._transition(
                IngestState.RECONNECTING,
                reason_code=reason_code,
                recovery_id=recovery_id,
                attempt=attempt,
                max_attempts=self._max_recovery_attempts,
            )
            try:
                ok = bool(callback(recovery_id))
            except Exception as exc:
                safe_error = redact_text(exc)
                with self._lock:
                    self._last_error = safe_error
                    self._next_recovery_at = time.monotonic() + min(
                        30.0, 2.0 ** max(0, attempt - 1)
                    )
                if self._stop_requested.is_set():
                    return False
                self._transition(
                    IngestState.BACKING_OFF,
                    reason_code="RECOVERY_EXCEPTION",
                    recovery_id=recovery_id,
                    context={"error": safe_error},
                    attempt=attempt,
                    max_attempts=self._max_recovery_attempts,
                    next_retry_at=self._next_recovery_at,
                )
                return False
            with self._lock:
                if self._stop_requested.is_set() or self._state in {
                    IngestState.STOPPING,
                    IngestState.STOPPED,
                }:
                    ok = False
                if ok:
                    self._recovery_attempt = 0
                    self._next_recovery_at = 0.0
                else:
                    self._next_recovery_at = time.monotonic() + min(
                        30.0, 2.0 ** max(0, attempt - 1)
                    )
            if self._stop_requested.is_set():
                return False
            self._transition(
                IngestState.RUNNING if ok else IngestState.BACKING_OFF,
                reason_code="RECOVERY_SUCCEEDED" if ok else "RECOVERY_FAILED",
                recovery_id=recovery_id,
                attempt=attempt,
                max_attempts=self._max_recovery_attempts,
                next_retry_at=(None if ok else self._next_recovery_at),
            )
            return ok
        finally:
            self._recovery_lock.release()

    def restart_preview_sink(self, reason: str = "preview encoder failure") -> bool:
        """Restart only the preview encoder; do not open a new signed upstream."""
        if self._stop_requested.is_set():
            return False
        with self._lock:
            if self._state in {IngestState.STOPPING, IngestState.STOPPED}:
                return False
            self._preview_requested = True
        stop = getattr(self.ingest, "stop_preview_sink", None)
        if callable(stop):
            try:
                stop(reason)
            except Exception as exc:
                _log.debug(
                    "preview sink stop before restart failed room=%s: %s",
                    self.room_id,
                    exc,
                )
        start_preview = getattr(self.ingest, "start_preview", None)
        if not callable(start_preview):
            return False
        try:
            result = start_preview()
        except Exception as exc:
            self.handle_failure("PREVIEW_ENCODER_FAILURE", error=redact_text(exc))
            return False
        ok = bool(
            result is None
            or getattr(result, "accepted", False)
            or getattr(result, "ok", False)
        )
        if ok:
            running = (
                getattr(self.ingest, "process_id", None) is not None
                or self._recording_requested
            )
            self._transition(
                IngestState.RUNNING if running else IngestState.CONNECTING,
                reason_code="PREVIEW_SINK_RESTARTED",
            )
            return True
        error = redact_text(getattr(result, "error", "") or "preview restart failed")
        self.handle_failure("PREVIEW_ENCODER_FAILURE", error=error)
        return False

    def handle_failure(
        self,
        failure_kind: str,
        *,
        error: str = "",
        retry_after: float | None = None,
        user_action: str = "",
    ) -> IngestState:
        """Project a typed upstream/sink failure into the supervisor state.

        Recovery execution remains owned by the single ``run_recovery``
        coordinator; this method only records the decision boundary so a
        failed preview sink cannot accidentally stop a healthy recording sink.
        """
        kind = normalize_failure_kind(failure_kind).value
        safe_error = redact_text(error) if error else ""
        if safe_error:
            with self._lock:
                self._last_error = safe_error
        with self._lock:
            self._last_failure_kind = kind
        if retry_after is not None and retry_after > 0:
            with self._lock:
                self._next_recovery_at = max(
                    self._next_recovery_at,
                    time.monotonic() + min(300.0, float(retry_after)),
                )
        if kind in {"AUTH_REQUIRED", "AUTH_EXPIRED"}:
            state = IngestState.AUTH_REQUIRED
        elif kind in {"OFFLINE"}:
            state = IngestState.OFFLINE
        elif kind in {"PREVIEW_ENCODER_FAILURE", "RECORDING_SINK_FAILURE"}:
            with self._lock:
                has_recording = self._recording_requested
                has_preview = self._preview_requested
            state = (
                IngestState.DEGRADED
                if (kind == "PREVIEW_ENCODER_FAILURE" and has_recording)
                or (kind == "RECORDING_SINK_FAILURE" and has_preview)
                else IngestState.FAILED
            )
        elif kind in {"DISK_FULL", "PERMISSION_DENIED"}:
            state = IngestState.FAILED
        else:
            state = IngestState.BACKING_OFF
        self._transition(
            state,
            reason_code=kind,
            context={"error": safe_error, "retry_after": retry_after},
            failure_kind=kind,
            user_action=user_action,
        )
        return state

    def health(self) -> dict[str, object]:
        ingest = self.ingest
        with self._lock:
            state = self._state.value
            last_error = self._last_error
            last_failure_kind = self._last_failure_kind
            session_id = self._session_id
            lease_id = self._lease_id
            candidate_id = self._candidate_id
            quality_id = self._quality_id
            protocol = self._protocol
            cdn_id = self._cdn_id
            lease_expires_at = self._lease_expires_at
            lease_refresh_at = self._lease_refresh_at
            generation = self._generation
            recovery_attempt = self._recovery_attempt
            next_recovery_at = self._next_recovery_at or None
        return {
            "room_id": self.room_id,
            "state": state,
            "session_id": session_id,
            "lease_id": lease_id,
            "candidate_id": candidate_id,
            "quality_id": quality_id,
            "protocol": protocol,
            "cdn_id": cdn_id,
            "lease_expires_at": lease_expires_at,
            "lease_refresh_at": lease_refresh_at,
            "generation": generation,
            "recording_requested": self.recording_requested,
            "preview_requested": self.preview_requested,
            "recording_active": bool(getattr(ingest, "recording_active", False)),
            "preview_subscribers": int(getattr(ingest, "preview_subscribers", 0)),
            "upstream_pid": getattr(ingest, "process_id", None),
            # Keep the media-process generation visible independently from the
            # lease generation.  A supervisor can issue a new lease while an
            # old FFmpeg reader is still winding down; exposing both makes
            # stale-byte races diagnosable without leaking source URLs.
            "upstream_generation": int(
                getattr(ingest, "upstream_generation", 0) or 0
            ),
            "recording_pid": getattr(ingest, "recording_process_id", None),
            "preview_pid": getattr(ingest, "preview_process_id", None),
            "upstream_bytes": int(getattr(ingest, "upstream_bytes", 0) or 0),
            "recording_size_bytes": int(getattr(ingest, "recording_size_bytes", 0) or 0),
            "preview_segment_count": int(
                getattr(ingest, "preview_segment_count", 0) or 0
            ),
            "preview_media_bytes": int(
                getattr(ingest, "preview_media_bytes", 0) or 0
            ),
            "manifest_path": str(getattr(ingest, "recording_manifest_path", "") or ""),
            "last_error": last_error,
            "failure_kind": last_failure_kind,
            "recovery_attempt": recovery_attempt,
            "max_recovery_attempts": self._max_recovery_attempts,
            "next_recovery_at": next_recovery_at,
            "last_transition_at": self._last_transition_at,
        }

    def stop(
        self,
        reason: str = "supervisor stopped",
        *,
        timeout_sec: float = 10.0,
    ) -> None:
        with self._lock:
            if self._state == IngestState.STOPPED:
                return
        self._stop_requested.set()
        self._transition(IngestState.STOPPING, reason_code="STOP_REQUESTED")
        deadline = time.monotonic() + max(0.1, float(timeout_sec))
        try:
            self.ingest.stop(
                reason=reason,
                deadline_monotonic=deadline,
            )
        except TypeError as exc:
            # Preserve third-party/legacy SharedRoomIngest implementations
            # while the built-in implementation enforces the total deadline.
            if "deadline_monotonic" not in str(exc):
                raise
            self.ingest.stop(reason=reason)
        with self._lock:
            self._recording_requested = False
            self._preview_requested = False
        self._transition(IngestState.STOPPED, reason_code="STOPPED")


__all__ = ["IngestEvent", "IngestState", "IngestSupervisor"]
