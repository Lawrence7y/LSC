"""High-level recording session wrapping StreamCapture.

Provides a simpler start/stop API with session status tracking,
used by tests and higher-level orchestration code.
"""
from __future__ import annotations

import os
import uuid
from dataclasses import dataclass

from lsc import get_logger
from lsc.config import LscConfig
from lsc.recorder.capture import StreamCapture, validate_recording

_log = get_logger(__name__)


@dataclass
class SessionResult:
    """Result of a session-level operation."""
    success: bool
    output_path: str = ""
    duration_sec: float = 0.0
    file_size_mb: float = 0.0
    error: str = ""
    is_valid: bool = True
    validation_error: str = ""


class _SessionStatus:
    """Lightweight session status holder exposed via RecordingSession.session."""

    __slots__ = ("status",)

    def __init__(self, status: str = ""):
        self.status = status


class RecordingSession:
    """Wraps StreamCapture with a session-level start/stop API.

    Attributes:
        config: The LscConfig used to create the capture.
        is_recording: True while a capture is active.
        session: A status holder (None before start, set after start/stop).
        duration: Elapsed seconds of the current/last recording.
    """

    def __init__(self, config: LscConfig):
        self.config = config
        self._capture = StreamCapture(config)
        self._session: _SessionStatus | None = None

    @property
    def is_recording(self) -> bool:
        return self._capture.is_recording

    @property
    def session(self) -> _SessionStatus | None:
        return self._session

    @property
    def duration(self) -> float:
        return self._capture.duration

    @property
    def capture(self) -> StreamCapture:
        return self._capture

    def start(self, url: str, output_dir: str, *,
              codec: str = "copy",
              input_args: list[str] | None = None,
              extra_args: list[str] | None = None) -> bool:
        """Start recording a stream into output_dir.

        Returns True if the capture started successfully.
        """
        if self._capture.is_recording:
            _log.warning("RecordingSession already recording")
            return False

        os.makedirs(output_dir, exist_ok=True)
        from datetime import datetime
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
        suffix = uuid.uuid4().hex[:6]
        output_path = os.path.join(output_dir, f"recording_{timestamp}_{suffix}.mp4")

        started = self._capture.start(
            url, output_path,
            codec=codec,
            input_args=input_args,
            extra_args=extra_args,
        )
        self._session = _SessionStatus(
            status=self._capture.status.value if started else "error"
        )
        return started

    def stop(self) -> SessionResult:
        """Stop the current recording."""
        if not self._capture.is_recording:
            self._session = _SessionStatus(status="idle")
            return SessionResult(False, error="Not recording")

        result = self._capture.stop()
        self._session = _SessionStatus(
            status=self._capture.status.value
        )

        # Validate recording if successful
        is_valid = True
        validation_error = ""
        if result.success and result.output_path:
            is_valid, validation_error = validate_recording(result.output_path)
            if not is_valid:
                _log.warning("Recording validation failed: %s", validation_error)

        return SessionResult(
            success=result.success,
            output_path=result.output_path,
            duration_sec=result.duration_sec,
            file_size_mb=result.file_size_mb,
            error=result.error,
            is_valid=is_valid,
            validation_error=validation_error,
        )


__all__ = ["RecordingSession", "SessionResult"]
