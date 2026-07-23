"""Multi-room workbench state helpers."""
from __future__ import annotations

from typing import Any

from .session import RoomSession

__all__ = ["MultiRoomManager", "RoomSession"]


def __getattr__(name: str) -> Any:
    # Lazy import avoids orchestrator ↔ manager circular import via package __init__.
    if name == "MultiRoomManager":
        from .manager import MultiRoomManager

        return MultiRoomManager
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
