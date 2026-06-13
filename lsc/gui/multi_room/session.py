"""Session model for multi-room workbench."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from lsc.platforms.base import StreamInfo


@dataclass(slots=True)
class RoomSession:
    room_id: str
    room_url: str
    platform: str = ""
    stream_info: StreamInfo | None = None
    selected_quality: str = ""
    preview_muted: bool = True
    is_connected: bool = False
    is_recording: bool = False
    record_output_path: str = ""
    record_started_at: datetime | None = None
    last_error: str = ""
    controller: object | None = None

    def apply_stream_info(self, info: StreamInfo) -> None:
        self.platform = info.platform
        self.stream_info = info
        self.selected_quality = info.selected_quality
        self.is_connected = bool(info.is_live and info.stream_url)
        self.last_error = ""

    def set_error(self, message: str) -> None:
        self.is_connected = False
        self.last_error = message
