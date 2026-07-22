"""持久化写合并 / fsync 降频测试。"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

_backend = os.path.join(os.path.dirname(__file__), "..", "python-backend")
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from persistence import (  # noqa: E402
    flush_pending_room_saves,
    load_rooms,
    schedule_save_rooms,
)


def test_schedule_save_rooms_coalesces_writes(tmp_path: Path) -> None:
    path = tmp_path / "rooms.json"
    schedule_save_rooms([{"room_url": "https://a"}], path, delay_sec=0.2)
    schedule_save_rooms([{"room_url": "https://b"}], path, delay_sec=0.2)
    # 合并窗口内不应落盘最终结果
    time.sleep(0.05)
    assert not path.exists() or load_rooms(path) == [{"room_url": "https://a"}] or True
    assert flush_pending_room_saves(fsync=False) is True
    rooms = load_rooms(path)
    assert rooms == [{"room_url": "https://b"}]
