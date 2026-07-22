"""H2a: stable recording directory hash uses sha1, not PYTHONHASHSEED-dependent hash()."""

from __future__ import annotations

import hashlib
import inspect
from pathlib import Path

from lsc.core.models import RoomInfo
from lsc.core.services.recording_service import RecordingService


def test_make_room_output_dir_uses_sha1_short_id(tmp_path):
    url = "https://live.example/room/1"
    expected = hashlib.sha1(url.encode("utf-8")).hexdigest()[:6]
    room = RoomInfo(platform="bilibili", room_url=url, streamer="tester")

    out_dir = RecordingService._make_room_output_dir(str(tmp_path), room)

    assert out_dir.endswith(f"bilibili_tester_{expected}")
    assert Path(out_dir).is_dir()


def test_recording_service_source_uses_sha1_not_builtin_hash():
    source = inspect.getsource(RecordingService._make_room_output_dir)
    assert "hashlib.sha1" in source
    assert "hash(room.room_url)" not in source
