"""Regression tests for resource-exhaustion caps added by the security scan fix."""
from __future__ import annotations

import queue

import pytest

from lsc.core.orchestrator import RoomOrchestrator
from lsc.core.services import frame_capture


def test_orchestrator_submit_rejects_when_queue_full():
    orch = RoomOrchestrator()
    for _ in range(orch._MAX_QUEUED_COMMANDS):
        orch._cmd_queue.put(lambda: None)
    with pytest.raises(RuntimeError, match="command queue full"):
        orch.submit(lambda: None)


def test_frame_capture_buffer_capped_when_eoi_missing():
    worker = frame_capture.FrameCaptureWorker("https://example.com/live.m3u8")
    worker._retry_count = frame_capture._MAX_RETRY

    class _FakeStdout:
        def __init__(self) -> None:
            self._sent = False
            self.payload = b"\xff\xd8" + b"\x00" * (frame_capture._MAX_BUFFER_BYTES + 4096)

        def read(self, _size: int = 0) -> bytes:
            if not self._sent:
                self._sent = True
                return self.payload
            return b""

    worker._process = type(
        "FakeProcess",
        (),
        {
            "stdout": _FakeStdout(),
            "stderr": None,
            "wait": lambda self, timeout=0: 0,
        },
    )()
    worker._reader_loop()
    assert worker.get_latest_frame() is None
    assert worker._error
