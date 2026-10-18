"""磁盘满保护守卫（CLAUDE.md §5.1，2026-09-24 审查修复）。

生产默认 ``shared_ingest_enabled=True``：低频磁盘检查原本位于 tick 的
legacy 专属区，被共享进样分支末尾的 ``continue`` 旁路 ⇒ 2GB 在录底线对
共享房间永不可达，磁盘写满时只能等 FFmpeg sink 自己报错。现在检查前置到
路径分流之前，本文件锁住共享与 legacy 两条路径都必须覆盖。
"""
from __future__ import annotations

import shutil

from lsc.config import LscConfig


def _patch_config(monkeypatch, *, shared_ingest_enabled: bool) -> None:
    monkeypatch.setattr(
        "lsc.core.orchestrator.load_config",
        lambda: LscConfig(
            ffmpeg_path="ffmpeg",
            ffprobe_path="ffprobe",
            shared_ingest_enabled=shared_ingest_enabled,
        ),
    )


class _FakeSharedIngest:
    """最小共享进样替身：健康录制中、无任何错误。"""

    recording_active = True
    recording_failover_in_progress = False
    recording_error = ""
    preview_error = ""
    upstream_error = ""
    upstream_bytes = 1_048_576
    is_stopped = False
    _upstream_has_produced_data = True


def _patch_registry(monkeypatch, ingest) -> None:
    monkeypatch.setattr(
        "lsc.core.orchestrator.get_shared_ingest_registry",
        lambda: type("Reg", (), {"get": staticmethod(lambda _rid: ingest)})(),
    )


def _low_disk(monkeypatch, free_bytes: int) -> None:
    monkeypatch.setattr(
        shutil,
        "disk_usage",
        lambda _path: type("U", (), {"free": free_bytes})(),
    )


def test_shared_ingest_room_enforces_disk_floor(monkeypatch, tmp_path) -> None:
    """共享进样房间磁盘低于 2GB 也必须强制停止录制（回归：曾被 continue 旁路）。"""
    from lsc.core.orchestrator import RoomOrchestrator

    _patch_config(monkeypatch, shared_ingest_enabled=True)
    _patch_registry(monkeypatch, _FakeSharedIngest())
    _low_disk(monkeypatch, 512 * 1024 * 1024)

    stopped: list[str] = []
    manager = RoomOrchestrator(controller_factory=lambda: object())
    monkeypatch.setattr(manager, "save_rooms", lambda: 0)
    monkeypatch.setattr(manager, "stop_recording_async", lambda room_id: stopped.append(room_id))

    manager.start()
    room = manager.add_room("https://example.com/live.m3u8")
    room.is_connected = True
    room.is_recording = True
    room.record_output_path = str(tmp_path / "room.mp4")

    manager._tick_counter = 3  # 自增后为 4 = _LOW_FREQ_INTERVAL 的倍数
    manager.call(manager._on_global_tick)

    assert stopped == [room.room_id]
    assert "磁盘空间不足" in room.last_error
    assert room.is_recording is True  # 停止异步执行，本 tick 只负责触发与置错


def test_legacy_room_still_enforces_disk_floor(monkeypatch, tmp_path) -> None:
    """重构不能弄丢 legacy（capture）路径原有的磁盘检查覆盖。"""
    from lsc.core.orchestrator import RoomOrchestrator

    _patch_config(monkeypatch, shared_ingest_enabled=False)
    _patch_registry(monkeypatch, None)
    _low_disk(monkeypatch, 256 * 1024 * 1024)

    stopped: list[str] = []
    manager = RoomOrchestrator(controller_factory=lambda: object())
    monkeypatch.setattr(manager, "save_rooms", lambda: 0)
    monkeypatch.setattr(manager, "stop_recording_async", lambda room_id: stopped.append(room_id))

    manager.start()
    room = manager.add_room("https://example.com/live.m3u8")
    room.is_connected = True
    room.is_recording = True
    room.record_output_path = str(tmp_path / "legacy.mp4")

    manager._tick_counter = 3
    manager.call(manager._on_global_tick)

    assert stopped == [room.room_id]
    assert "磁盘空间不足" in room.last_error


def test_healthy_disk_does_not_stop_recording(monkeypatch, tmp_path) -> None:
    """空间充足时不得误停（防止把阈值判断写反）。"""
    from lsc.core.orchestrator import RoomOrchestrator

    _patch_config(monkeypatch, shared_ingest_enabled=True)
    _patch_registry(monkeypatch, _FakeSharedIngest())
    _low_disk(monkeypatch, 64 * 1024 * 1024 * 1024)

    stopped: list[str] = []
    manager = RoomOrchestrator(controller_factory=lambda: object())
    monkeypatch.setattr(manager, "save_rooms", lambda: 0)
    monkeypatch.setattr(manager, "stop_recording_async", lambda room_id: stopped.append(room_id))

    manager.start()
    room = manager.add_room("https://example.com/live.m3u8")
    room.is_connected = True
    room.is_recording = True
    room.record_output_path = str(tmp_path / "room.mp4")

    manager._tick_counter = 3
    manager.call(manager._on_global_tick)

    assert stopped == []
    assert "磁盘" not in (room.last_error or "")
