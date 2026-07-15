from __future__ import annotations

import asyncio
import os
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from handlers import room_handler
from lsc.gui.multi_room.session import RoomSession

ROOT = Path(__file__).resolve().parents[1]


def _start_analysis_export_handler_source() -> str:
    source = (ROOT / "python-backend/handlers/room_handler.py").read_text(encoding="utf-8")
    start = source.index("@server.on('start_analysis_export')")
    end = source.index("@server.on('cancel_analysis')", start)
    return source[start:end]


class _FakeManager:
    def __init__(self, rooms):
        self._rooms = {room.room_id: room for room in rooms}
        self.room_connect_finished = _FakeSignal()
        self.batch_record_progress = _FakeSignal()
        self.batch_record_finished = _FakeSignal()
        self.medium_tick = _FakeSignal()
        self.low_tick = _FakeSignal()
        self.recording_stopped = _FakeSignal()

    def get_room(self, room_id: str):
        return self._rooms.get(room_id)

    def list_rooms(self):
        return list(self._rooms.values())


class _FakeSignal:
    def __init__(self) -> None:
        self.callbacks = []

    def connect(self, callback) -> None:
        self.callbacks.append(callback)


class _FakeServer:
    def __init__(self) -> None:
        self.handlers = {}
        self.connect_handler = None

    def on(self, name):
        def decorator(handler):
            self.handlers[name] = handler
            return handler

        return decorator

    def on_connect(self, handler):
        self.connect_handler = handler
        return handler

    async def broadcast(self, _name, _data):
        return None


class _FakeBridge:
    def __init__(self, manager) -> None:
        self.manager = manager
        self.broadcasts = []

    def queue_broadcast(self, message) -> None:
        self.broadcasts.append(message)

    def call(self, func):
        return func()


class _FakeTask:
    def __init__(self, coro) -> None:
        self.coro = coro
        self.cancelled_called = False
        close = getattr(coro, "close", None)
        if callable(close):
            close()

    def cancel(self) -> None:
        self.cancelled_called = True


def _room(tmp_path, room_id: str, offset: float, group: str = "group-a"):
    video = tmp_path / f"{room_id}.mp4"
    video.write_bytes(b"fake-video")
    return SimpleNamespace(
        room_id=room_id,
        streamer_name=room_id,
        record_output_path=str(video),
        content_offset=offset,
        align_group_id=group,
    )


def test_validate_synced_targets_accepts_same_align_group(tmp_path) -> None:
    main = _room(tmp_path, "main", offset=10.0)
    side = _room(tmp_path, "side", offset=3.0)
    manager = _FakeManager([main, side])

    ok, error, resolved_main, target_rooms = room_handler._validate_synced_analysis_targets(
        manager,
        "main",
        ["main", "side"],
    )

    assert ok is True
    assert error == ""
    assert resolved_main is main
    assert target_rooms == [main, side]


def test_validate_synced_targets_rejects_different_align_group(tmp_path) -> None:
    main = _room(tmp_path, "main", offset=10.0, group="group-a")
    side = _room(tmp_path, "side", offset=3.0, group="group-b")
    manager = _FakeManager([main, side])

    ok, error, resolved_main, target_rooms = room_handler._validate_synced_analysis_targets(
        manager,
        "main",
        ["main", "side"],
    )

    assert ok is False
    assert "不在同一对齐组" in error
    assert resolved_main is None
    assert target_rooms == []


def test_validate_synced_targets_accepts_single_room_without_align_group(tmp_path) -> None:
    main = _room(tmp_path, "main", offset=10.0, group="")
    manager = _FakeManager([main])

    ok, error, resolved_main, target_rooms = room_handler._validate_synced_analysis_targets(
        manager,
        "main",
        ["main"],
    )

    assert ok is True
    assert error == ""
    assert resolved_main is main
    assert target_rooms == [main]


def test_map_highlight_to_room_uses_content_offset_delta(tmp_path) -> None:
    main = _room(tmp_path, "main", offset=10.0)
    side = _room(tmp_path, "side", offset=3.0)
    highlight = {
        "start": 30.0,
        "end": 45.0,
        "score": 0.9,
        "reason": "round",
        "source": "round_detector",
        "speech_score": 0.0,
        "visual_score": 0.0,
        "transcript": "",
    }

    mapped = room_handler._map_highlight_to_room(highlight, main, side)

    assert mapped["start"] == 37.0
    assert mapped["end"] == 52.0
    assert mapped["source_start"] == 30.0
    assert mapped["source_end"] == 45.0
    assert mapped["source_room_id"] == "main"
    assert mapped["room_id"] == "side"
    assert mapped["offset_delta"] == 7.0
    assert mapped["source"] == "round_detector"


def test_map_highlights_by_room_returns_room_keyed_payload(tmp_path) -> None:
    main = _room(tmp_path, "main", offset=10.0)
    side = _room(tmp_path, "side", offset=3.0)
    highlights = [
        {"start": 30.0, "end": 45.0, "score": 0.9},
        {"start": 60.0, "end": 70.0, "score": 0.8},
    ]

    mapped = room_handler._map_highlights_by_room(highlights, main, [main, side])

    assert set(mapped) == {"main", "side"}
    assert mapped["main"][0]["start"] == 30.0
    assert mapped["main"][0]["end"] == 45.0
    assert mapped["side"][0]["start"] == 37.0
    assert mapped["side"][0]["end"] == 52.0


def test_map_highlights_by_room_drops_invalid_mapped_segments(tmp_path) -> None:
    main = _room(tmp_path, "main", offset=0.0)
    side = _room(tmp_path, "side", offset=100.0)
    highlights = [
        {"start": 30.0, "end": 45.0, "score": 0.9},
    ]

    mapped = room_handler._map_highlights_by_room(highlights, main, [main, side])

    assert len(mapped["main"]) == 1
    assert mapped["side"] == []


def test_stop_continuous_analysis_accepts_synced_target_room_id(tmp_path, monkeypatch) -> None:
    main = _room(tmp_path, "main", offset=10.0)
    side = _room(tmp_path, "side", offset=3.0)
    manager = _FakeManager([main, side])
    server = _FakeServer()
    bridge = _FakeBridge(manager)
    created_tasks = []

    def fake_create_task(coro):
        task = _FakeTask(coro)
        created_tasks.append(task)
        return task

    monkeypatch.setattr(asyncio, "create_task", fake_create_task)

    async def scenario():
        room_handler.register_room_handlers(server, bridge)
        start = await server.handlers["start_continuous_analysis"]({
            "main_room_id": "main",
            "target_room_ids": ["main", "side"],
        })
        stop = await server.handlers["stop_continuous_analysis"]({"room_id": "side"})
        return start, stop

    try:
        start_result, stop_result = asyncio.run(scenario())

        assert start_result["success"] is True
        assert list(room_handler._continuous_tasks) == ["main"]
        assert stop_result["success"] is True
        assert created_tasks[0].cancelled_called is True
        assert room_handler._continuous_tasks["main"]["cancelled"] is True
    finally:
        room_handler._continuous_tasks.clear()


def test_room_session_tracks_recording_media_start_mono() -> None:
    room = RoomSession(room_id="room-a", room_url="https://example/room")

    assert room.recording_media_start_mono is None


def test_validate_synced_analysis_targets_waits_for_file() -> None:
    """_validate_synced_analysis_targets 在 wait_for_file=True 时应等待文件创建。"""
    from handlers.room_handler import _validate_synced_analysis_targets, _wait_for_recording_file

    tmp_dir = tempfile.mkdtemp()
    dummy_path = os.path.join(tmp_dir, "dummy.mp4")

    mock_room = MagicMock()
    mock_room.room_id = "room-x"
    mock_room.record_output_path = dummy_path
    mock_room.align_group_id = ""
    mock_room.is_recording = True

    mock_mgr = MagicMock()
    mock_mgr.get_room.return_value = mock_room

    def _write_file_later():
        time.sleep(0.3)
        with open(dummy_path, "wb") as f:
            f.write(b"dummy")

    t = threading.Thread(target=_write_file_later)
    t.start()
    ok, error, _, rooms = _validate_synced_analysis_targets(
        mock_mgr, "room-x", ["room-x"], wait_for_file=True,
    )
    t.join()
    assert ok is True, f"应等待文件创建成功，但返回错误: {error}"
    assert len(rooms) == 1


def test_wait_for_recording_file_timeout() -> None:
    """_wait_for_recording_file 超时时应返回 False。"""
    from handlers.room_handler import _wait_for_recording_file

    mock_room = MagicMock()
    mock_room.record_output_path = "/nonexistent/path/recording.mp4"

    assert _wait_for_recording_file(mock_room, timeout_sec=0.5) is False


def test_wait_for_recording_file_immediate() -> None:
    """_wait_for_recording_file 文件已存在时应立即返回 True。"""
    from handlers.room_handler import _wait_for_recording_file

    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".mp4")
    os.close(tmp_fd)

    mock_room = MagicMock()
    mock_room.record_output_path = tmp_path

    assert _wait_for_recording_file(mock_room, timeout_sec=0.5) is True


def test_merge_round_windows_preserves_round_key_when_boundaries_shift() -> None:
    previous = [{
        "start": 30.0, "end": 80.0, "round_key": "round-003",
        "phase": "pending", "start_by": "ocr_buy_exit", "end_by": "open_tail",
    }]
    current = [{
        "start": 32.0, "end": 83.0, "phase": "combat",
        "start_by": "ocr_buy_exit", "end_by": "ocr_result",
    }]

    merged = room_handler._merge_round_windows(previous, current)

    assert len(merged) == 1
    assert merged[0]["round_key"] == "round-003"


def test_new_rounds_includes_pending_to_confirmed_update() -> None:
    previous = [{
        "start": 30.0, "end": 80.0, "round_key": "round-003",
        "phase": "pending", "start_by": "ocr_buy_exit", "end_by": "open_tail",
    }]
    current = [{
        "start": 32.0, "end": 83.0, "phase": "combat",
        "start_by": "ocr_buy_exit", "end_by": "ocr_result",
    }]

    fresh = room_handler._new_rounds(previous, current)

    assert len(fresh) == 1
    assert fresh[0]["start"] == current[0]["start"]
    assert fresh[0]["end"] == current[0]["end"]
    assert fresh[0]["round_key"] == "round-003"


