from __future__ import annotations

import inspect
from types import SimpleNamespace

from lsc.core.controller import HeadlessRecordingController
from lsc.core.orchestrator import RoomOrchestrator
from lsc.core.session import RoomSession


def test_headless_controller_exposes_export_contract():
    controller = HeadlessRecordingController
    assert callable(getattr(controller, "start_export", None))
    assert callable(getattr(controller, "cancel_export", None))

    start_params = inspect.signature(controller.start_export).parameters
    assert {"start_sec", "end_sec", "output_dir", "name"}.issubset(start_params)
    assert {"on_done", "on_progress", "profile"}.issubset(start_params)
    cancel_params = inspect.signature(controller.cancel_export).parameters
    assert "export_id" in cancel_params


def test_orchestrator_reports_missing_export_method_without_attribute_error():
    orchestrator = RoomOrchestrator()
    room = RoomSession("room-contract", "https://example.test/live")
    room.controller = SimpleNamespace()
    orchestrator._rooms[room.room_id] = room

    assert orchestrator.start_export(room.room_id, 0.0, 1.0, "out") == ""
    assert "接口缺失" in room.controller._last_export_error
    assert "start_export" in room.controller._last_export_error

    orchestrator.shutdown()
