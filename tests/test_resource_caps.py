"""Regression tests for resource-exhaustion caps added by the security scan fix."""
from __future__ import annotations

import pytest

from lsc.core.orchestrator import RoomOrchestrator
from lsc.core.services import frame_capture
from lsc.core.session import RoomSession


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


def test_preview_cap_is_four_and_rejects_the_fifth(monkeypatch):
    class _Widget:
        def is_available(self):
            return True

        def stop(self):
            return None

    monkeypatch.setattr(
        "lsc.core.orchestrator._get_configured_max_previews",
        lambda: 4,
    )
    orch = RoomOrchestrator(preview_factory=_Widget)
    rooms = [
        RoomSession(f"room-{index}", f"https://live.example/{index}")
        for index in range(5)
    ]
    for room in rooms:
        room.is_connected = True
        orch._rooms[room.room_id] = room

    try:
        assert [orch.start_preview(room.room_id) for room in rooms[:4]] == [True] * 4
        assert orch.get_active_preview_count() == 4
        assert orch.start_preview(rooms[4].room_id) is False
        assert rooms[4].preview_enabled is False
        assert rooms[4].preview_error
    finally:
        for room in rooms:
            orch.stop_preview(room.room_id)
