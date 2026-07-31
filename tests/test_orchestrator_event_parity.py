"""Event parity between RoomOrchestrator (EventBus) and MultiRoomManager (Qt Signals)."""
from __future__ import annotations

import threading
import time
from collections.abc import Callable

import pytest

from lsc.config import LscConfig
from lsc.platforms.base import StreamInfo

ROOM_URL = "https://live.bilibili.com/parity-test"
TICK_COUNT = 4

TRACKED_EVENTS = (
    "room_connect_finished",
    "recording_stopped",
    "global_tick",
    "medium_tick",
    "low_tick",
)


def _fake_stream_info(url: str = ROOM_URL) -> StreamInfo:
    return StreamInfo(
        platform="bilibili",
        room_url=url,
        stream_url="https://example.com/live.m3u8",
        title="parity-stream",
        streamer="tester",
        is_live=True,
        quality_urls={"origin": "https://example.com/live.m3u8"},
        selected_quality="origin",
        headers={"Referer": "https://example.com/"},
    )


class FakeController:
    def __init__(self) -> None:
        self.stream_url = "https://example.com/live.m3u8"
        self.input_args: list[str] = []
        self.selected_quality = "origin"
        self.calls: list[tuple] = []

    def start_recording_with_crf(self, stream_url, output_dir, encoder, crf, **kwargs):
        self.calls.append(("start", stream_url, output_dir, encoder, crf, kwargs))
        return True, f"{output_dir}/recording.mp4", encoder, ""

    def stop_recording(self):
        self.calls.append(("stop",))
        return True, 12.3, f"{self.calls[-2][2]}/recording.mp4" if len(self.calls) >= 2 else "recording.mp4"


class EventCollector:
    def __init__(self) -> None:
        self.events: list[tuple[str, tuple]] = []
        self._lock = threading.Lock()

    def record(self, name: str, *args) -> None:
        with self._lock:
            self.events.append((name, args))

    def subscribe_bus(self, bus, event_names: tuple[str, ...] = TRACKED_EVENTS) -> None:
        for name in event_names:
            bus.subscribe(name, lambda *a, n=name: self.record(n, *a))

    def wait_for(
        self,
        event_name: str,
        timeout: float = 5.0,
        predicate: Callable[[tuple], bool] | None = None,
    ) -> tuple | None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                for name, args in self.events:
                    if name == event_name and (predicate is None or predicate(args)):
                        return args
            time.sleep(0.02)
        return None

    def names(self) -> list[str]:
        with self._lock:
            return [name for name, _ in self.events]

    def counts(self) -> dict[str, int]:
        with self._lock:
            out: dict[str, int] = {}
            for name, _ in self.events:
                out[name] = out.get(name, 0) + 1
            return out


def _patch_parse(monkeypatch, module_path: str) -> None:
    def fake_parse_stream(url: str) -> StreamInfo:
        return _fake_stream_info(url)

    monkeypatch.setattr(f"{module_path}.parse_stream", fake_parse_stream)


def _patch_recording_config(monkeypatch, module_path: str) -> None:
    monkeypatch.setattr(
        f"{module_path}.load_config",
        lambda: LscConfig(
            ffmpeg_path="ffmpeg",
            ffprobe_path="ffprobe",
            shared_ingest_enabled=False,
        ),
    )


def _run_orchestrator_scenario(monkeypatch, tmp_path) -> EventCollector:
    from lsc.core.orchestrator import RoomOrchestrator

    _patch_parse(monkeypatch, "lsc.core.orchestrator")
    _patch_recording_config(monkeypatch, "lsc.core.orchestrator")
    monkeypatch.setattr("lsc.core.orchestrator.RoomOrchestrator.save_rooms", lambda self: 0)

    collector = EventCollector()
    orch = RoomOrchestrator(controller_factory=FakeController, preview_factory=lambda: object())
    orch.start()
    collector.subscribe_bus(orch.bus)
    try:
        room = orch.add_room(ROOM_URL)
        assert room is not None

        assert orch.connect_room(room.room_id, async_mode=True) is True
        connect_args = collector.wait_for(
            "room_connect_finished",
            predicate=lambda a: a[0] == room.room_id,
        )
        assert connect_args is not None
        assert connect_args[1] is True
        assert connect_args[2] == ""

        assert orch.start_recording(room.room_id, str(tmp_path), "Copy", 23) is True
        assert orch.stop_recording(room.room_id) is True

        for _ in range(TICK_COUNT):
            orch.call(orch._on_global_tick)
    finally:
        orch.shutdown(timeout_sec=5.0)

    return collector


def _run_manager_scenario(monkeypatch, tmp_path, qtbot) -> EventCollector:
    pytest.importorskip("PySide6")

    from lsc.gui.multi_room.manager import MultiRoomManager

    _patch_parse(monkeypatch, "lsc.gui.multi_room.manager")
    _patch_parse(monkeypatch, "lsc.core.orchestrator")
    _patch_recording_config(monkeypatch, "lsc.gui.multi_room.manager")
    _patch_recording_config(monkeypatch, "lsc.core.orchestrator")
    monkeypatch.setattr("lsc.gui.multi_room.manager.MultiRoomManager.save_rooms", lambda self: 0)
    monkeypatch.setattr("lsc.core.orchestrator.RoomOrchestrator.save_rooms", lambda self: 0)

    collector = EventCollector()
    manager = MultiRoomManager(controller_factory=FakeController)

    for signal_name in TRACKED_EVENTS:
        signal = getattr(manager, signal_name)

        def _handler(*args, _name=signal_name):
            collector.record(_name, *args)

        signal.connect(_handler)

    room = manager.add_room(ROOM_URL)
    assert room is not None

    with qtbot.waitSignal(manager.room_connect_finished, timeout=5000) as blocker:
        assert manager.connect_room(room.room_id, async_mode=True) is True
    assert blocker.args == [room.room_id, True, ""]

    assert manager.start_recording(room.room_id, str(tmp_path), "Copy", 23) is True
    assert manager.stop_recording(room.room_id) is True

    for _ in range(TICK_COUNT):
        manager._on_global_tick()

    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is not None:
        app.processEvents()

    return collector


def _tick_signature(collector: EventCollector) -> dict[str, int]:
    counts = collector.counts()
    return {name: counts.get(name, 0) for name in ("global_tick", "medium_tick", "low_tick")}


def test_orchestrator_event_sequence(monkeypatch, tmp_path) -> None:
    collector = _run_orchestrator_scenario(monkeypatch, tmp_path)

    names = collector.names()
    assert "room_connect_finished" in names
    assert names.index("room_connect_finished") < names.index("global_tick")

    assert collector.counts().get("recording_stopped", 0) == 0

    ticks = _tick_signature(collector)
    assert ticks["global_tick"] == TICK_COUNT
    assert ticks["medium_tick"] == TICK_COUNT
    assert ticks["low_tick"] == 1


def test_manager_orchestrator_event_parity(monkeypatch, tmp_path, qtbot) -> None:
    pytest.importorskip("PySide6")

    orch_collector = _run_orchestrator_scenario(monkeypatch, tmp_path)
    mgr_collector = _run_manager_scenario(monkeypatch, tmp_path, qtbot)

    orch_connect = [
        args for name, args in orch_collector.events if name == "room_connect_finished"
    ]
    mgr_connect = [
        args for name, args in mgr_collector.events if name == "room_connect_finished"
    ]
    assert len(orch_connect) == 1
    assert len(mgr_connect) == 1
    assert orch_connect[0][1:] == mgr_connect[0][1:]

    assert _tick_signature(orch_collector) == _tick_signature(mgr_collector)

    assert orch_collector.counts().get("recording_stopped", 0) == 0
    assert mgr_collector.counts().get("recording_stopped", 0) == 0
