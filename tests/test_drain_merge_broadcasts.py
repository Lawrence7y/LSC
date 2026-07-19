"""行为测试：drain_merge_broadcasts 分桶合并。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python-backend"))

from server import drain_merge_broadcasts  # noqa: E402


class _FakeBridge:
    def __init__(self, messages: list[dict]) -> None:
        self._q = list(messages)

    def get_broadcast(self, block: bool = False):
        if not self._q:
            return None
        return self._q.pop(0)


def test_drain_merge_preserves_two_room_recording_stopped() -> None:
    msgs = [
        {"type": "recording_stopped", "data": {"room_id": "a", "reason": "x"}},
        {"type": "recording_stopped", "data": {"room_id": "b", "reason": "y"}},
        {"type": "rooms_updated", "data": {"rooms": [1]}},
        {"type": "rooms_updated", "data": {"rooms": [2]}},
    ]
    out = drain_merge_broadcasts(_FakeBridge(msgs))
    stopped = [m for m in out if m["type"] == "recording_stopped"]
    assert len(stopped) == 2
    assert {m["data"]["room_id"] for m in stopped} == {"a", "b"}
    rooms = [m for m in out if m["type"] == "rooms_updated"]
    assert len(rooms) == 1
    assert rooms[0]["data"]["rooms"] == [2]


def test_drain_merge_coalesces_export_progress_by_job() -> None:
    msgs = [
        {"type": "export_progress", "data": {"job_id": "j1", "percent": 10}},
        {"type": "export_progress", "data": {"job_id": "j2", "percent": 20}},
        {"type": "export_progress", "data": {"job_id": "j1", "percent": 90}},
    ]
    out = drain_merge_broadcasts(_FakeBridge(msgs))
    progress = [m for m in out if m["type"] == "export_progress"]
    assert len(progress) == 2
    by_job = {m["data"]["job_id"]: m["data"]["percent"] for m in progress}
    assert by_job["j1"] == 90
    assert by_job["j2"] == 20
