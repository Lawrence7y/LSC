"""GUI test fixtures — isolate room persistence between tests."""
from __future__ import annotations

import os
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(autouse=True)
def _isolate_room_config(tmp_path, monkeypatch):
    """Redirect MultiRoomManager._config_file_path to a temp directory.

    This prevents tests from loading or corrupting the user's real
    ~/.lsc/LiveStreamClipper/rooms.json file.
    """
    from lsc.gui.multi_room.manager import MultiRoomManager

    config_file = str(tmp_path / "rooms.json")

    def _patched_config_file_path(self) -> str:
        os.makedirs(os.path.dirname(config_file), exist_ok=True)
        return config_file

    monkeypatch.setattr(
        MultiRoomManager, "_config_file_path", _patched_config_file_path
    )
