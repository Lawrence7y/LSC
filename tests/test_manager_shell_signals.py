"""Smoke test: EventBus events forward to MultiRoomManager Qt Signals."""
from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

app = QApplication.instance() or QApplication(sys.argv)

from lsc.gui.multi_room.manager import MultiRoomManager


def test_bus_forwards_connect_finished() -> None:
    mgr = MultiRoomManager(controller_factory=lambda: None, preview_factory=lambda: None)
    seen: list[tuple] = []
    mgr.room_connect_finished.connect(lambda *a: seen.append(a))
    # EventBus emit must run on the orchestrator thread
    mgr._orch.call(lambda: mgr._orch.bus.emit("room_connect_finished", "r1", True, ""))
    app.processEvents()
    assert seen == [("r1", True, "")]
    mgr.shutdown()
