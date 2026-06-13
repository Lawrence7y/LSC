"""Manager for multi-room workbench sessions."""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from uuid import uuid4

from lsc.platforms.registry import parse_stream

from .session import RoomSession

ControllerFactory = Callable[[], object]


class MultiRoomManager:
    """Own room session lifecycle and batch operations."""

    def __init__(self, controller_factory: ControllerFactory | None = None) -> None:
        self._controller_factory = controller_factory
        self._rooms: dict[str, RoomSession] = {}

    def _create_controller(self) -> object:
        if self._controller_factory is not None:
            return self._controller_factory()

        from lsc.gui.pages.recording_controller import RecordingController

        return RecordingController()

    def add_room(self, url: str) -> RoomSession:
        room_id = uuid4().hex
        room = RoomSession(
            room_id=room_id,
            room_url=url.strip(),
            controller=self._create_controller(),
        )
        self._rooms[room_id] = room
        return room

    def get_room(self, room_id: str) -> RoomSession | None:
        return self._rooms.get(room_id)

    def list_rooms(self) -> list[RoomSession]:
        return list(self._rooms.values())

    def remove_room(self, room_id: str) -> bool:
        room = self._rooms.pop(room_id, None)
        if room is None:
            return False

        controller = room.controller
        cleanup = getattr(controller, "cleanup", None)
        if callable(cleanup):
            cleanup()
        return True

    def connect_room(self, room_id: str) -> bool:
        room = self.get_room(room_id)
        if room is None:
            return False

        info = parse_stream(room.room_url)
        room.apply_stream_info(info)
        if not info.is_live or not info.stream_url:
            room.set_error(info.error or "连接失败")
            return False

        controller = room.controller
        if controller is not None:
            controller.stream_url = info.stream_url
            controller.input_args = info.to_legacy_dict().get("_inputArgs", [])
        return True

    def disconnect_room(self, room_id: str) -> bool:
        room = self.get_room(room_id)
        if room is None:
            return False

        room.is_connected = False
        return True

    def mute_room(self, room_id: str, muted: bool) -> None:
        room = self.get_room(room_id)
        if room is not None:
            room.preview_muted = muted

    def start_recording(self, room_id: str, output_dir: str, encoder: str, crf: int) -> bool:
        room = self.get_room(room_id)
        controller = None if room is None else room.controller
        if room is None or controller is None:
            return False

        stream_url = getattr(controller, "stream_url", "")
        input_args = getattr(controller, "input_args", [])
        ok, output_path, _encoder_used = controller.start_recording_with_crf(
            stream_url,
            output_dir,
            encoder,
            crf,
            input_args=input_args or None,
        )
        room.is_recording = ok
        room.record_output_path = output_path
        room.record_started_at = datetime.now() if ok else None
        if not ok:
            room.last_error = "录制启动失败"
        return ok

    def stop_recording(self, room_id: str) -> bool:
        room = self.get_room(room_id)
        controller = None if room is None else room.controller
        if room is None or controller is None:
            return False

        ok, _size_mb, output_path = controller.stop_recording()
        room.is_recording = False
        room.record_started_at = None
        if output_path:
            room.record_output_path = output_path
        return ok

    def start_recording_all(self, output_dir: str, encoder: str, crf: int) -> dict[str, bool]:
        return {
            room.room_id: self.start_recording(room.room_id, output_dir, encoder, crf)
            for room in self.list_rooms()
        }

    def stop_recording_all(self) -> dict[str, bool]:
        return {room.room_id: self.stop_recording(room.room_id) for room in self.list_rooms()}
