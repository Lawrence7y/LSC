from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from handlers import room_handler


ROOT = Path(__file__).resolve().parents[1]


class _FakeSignal:
    def __init__(self) -> None:
        self.callbacks: list = []

    def connect(self, callback) -> None:
        self.callbacks.append(callback)


class _FakeTask:
    def __init__(self, coro) -> None:
        self._coro = coro
        self.cancelled_called = False
        close = getattr(coro, "close", None)
        if callable(close):
            close()

    def cancel(self) -> None:
        self.cancelled_called = True


class _FakeServer:
    def __init__(self) -> None:
        self.handlers: dict = {}

    def on(self, name: str):
        def decorator(fn):
            self.handlers[name] = fn
            return fn
        return decorator

    def on_connect(self, handler):
        return handler


class _FakeBridge:
    def __init__(self, manager) -> None:
        self.manager = manager
        self.broadcasts: list[dict] = []

    def queue_broadcast(self, msg: dict) -> None:
        self.broadcasts.append(msg)

    def call(self, func):
        return func()


class _FakeManager:
    def __init__(self, rooms) -> None:
        self._rooms = {r.room_id: r for r in rooms}
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


def _room(tmp_path, room_id: str, offset: float = 0.0):
    path = tmp_path / f"{room_id}.mp4"
    path.write_bytes(b"\x00" * 1024)
    return SimpleNamespace(
        room_id=room_id,
        room_url=f"https://example/{room_id}",
        streamer_name=room_id,
        record_output_path=str(path),
        output_path=str(path),
        is_recording=False,
        recording_start_mono=0.0,
        content_offset=offset,
        align_group_id="g1",
    )


def test_stop_sets_stopping_until_resources_exit(tmp_path, monkeypatch) -> None:
    """stop 请求须设 stopping + scan_abort，响应不得声称已完全停止。"""
    main = _room(tmp_path, "main")
    manager = _FakeManager([main])
    server = _FakeServer()
    bridge = _FakeBridge(manager)
    created_tasks: list[_FakeTask] = []

    def fake_create_task(coro):
        task = _FakeTask(coro)
        created_tasks.append(task)
        return task

    monkeypatch.setattr(asyncio, "create_task", fake_create_task)

    async def scenario():
        room_handler.register_room_handlers(server, bridge)
        await server.handlers["start_continuous_analysis"]({
            "main_room_id": "main",
            "target_room_ids": ["main"],
            "mode": "valorant_round",
        })
        stop = await server.handlers["stop_continuous_analysis"]({"room_id": "main"})
        state = room_handler._continuous_tasks.get("main", {})
        return stop, state, bridge.broadcasts

    try:
        stop_result, state, broadcasts = asyncio.run(scenario())

        assert stop_result["success"] is True
        assert stop_result.get("status") == "stopping"
        assert stop_result.get("status") != "stopped"
        assert state.get("cancelled") is True
        assert state.get("scan_abort") is True
        assert state.get("status") == "stopping"
        assert created_tasks[0].cancelled_called is True
        stopping_msgs = [
            m for m in broadcasts
            if m.get("type") == "continuous_analysis_status"
            and m.get("data", {}).get("status") == "stopping"
        ]
        assert stopping_msgs, "stop 应广播 stopping 状态"
    finally:
        room_handler._continuous_tasks.clear()
