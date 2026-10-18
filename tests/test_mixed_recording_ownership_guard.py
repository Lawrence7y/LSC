"""混合态录制归属守卫（2026-09-24 修复，P1-5）。

共享录制启动失败（VMware pipe 等场景）会回退到 legacy controller 录制，
此后 ingest 只承载预览、``recording_active=False`` 是正常状态。修复前
tick 与 get_recording_status 据此把这种房误判为「共享录制已停止」：
is_recording 置 False 并广播 recording_stopped，而 legacy FFmpeg 仍在
写盘，状态与磁盘事实长期不一致。
"""
from __future__ import annotations

from lsc.config import LscConfig


def _patch_config(monkeypatch) -> None:
    monkeypatch.setattr(
        "lsc.core.orchestrator.load_config",
        lambda: LscConfig(
            ffmpeg_path="ffmpeg",
            ffprobe_path="ffprobe",
            shared_ingest_enabled=True,
        ),
    )


class _PreviewOnlyIngest:
    """只承载预览的共享进样替身：录制不在它手上。"""

    recording_active = False
    recording_failover_in_progress = False
    recording_error = ""
    preview_error = ""
    upstream_error = ""
    upstream_bytes = 0
    is_stopped = False
    preview_subscribers = 1
    _upstream_has_produced_data = True

    def __init__(self) -> None:
        self._recording_failover_source_paths = []


class _FakeRegistry:
    def __init__(self, ingest) -> None:
        self._ingest = ingest

    def get(self, room_id: str):
        return self._ingest

    def get_supervisor_if_exists(self, room_id: str):
        return None

    def stop_room(self, room_id: str, reason: str = "") -> None:
        return None


def _make_manager(monkeypatch, *, controller_recording: bool):
    from lsc.core.orchestrator import RoomOrchestrator

    _patch_config(monkeypatch)

    class _FallbackController:
        # 回退路径的唯一权威状态源：controller 自报在录
        is_recording = controller_recording
        stream_url = "https://example.com/live.m3u8"
        input_args: list[str] = []

    ingest = _PreviewOnlyIngest()
    monkeypatch.setattr(
        "lsc.core.orchestrator.get_shared_ingest_registry",
        lambda: _FakeRegistry(ingest),
    )
    manager = RoomOrchestrator(controller_factory=_FallbackController)
    monkeypatch.setattr(manager, "save_rooms", lambda: 0)
    stopped: list[tuple] = []
    manager.bus.subscribe("recording_stopped", lambda *args: stopped.append(args))
    manager.start()
    room = manager.add_room("https://example.com/live.m3u8")
    room.is_connected = True
    room.is_recording = True
    room.preview_enabled = True
    return manager, room, stopped


def test_mixed_state_tick_keeps_recording_alive(monkeypatch) -> None:
    """混合态（legacy 在录 + ingest 只预览）tick 不得判死录制。"""
    manager, room, stopped = _make_manager(monkeypatch, controller_recording=True)
    manager._tick_counter = 3  # 低频 tick，覆盖磁盘/主动刷新段
    manager.call(manager._on_global_tick)

    assert room.is_recording is True
    assert stopped == []


def test_mixed_state_get_recording_status_reports_recording(monkeypatch) -> None:
    """状态快照同样不得把混合态报成未在录制（停止确认回查读它）。"""
    manager, _room, _stopped = _make_manager(monkeypatch, controller_recording=True)

    status = manager.get_recording_status(_room.room_id)

    assert status["is_recording"] is True


def test_pure_shared_death_still_marks_stopped(monkeypatch) -> None:
    """反证：controller 不在录（纯共享录制真死亡）时仍按原语义判死。"""
    manager, room, stopped = _make_manager(monkeypatch, controller_recording=False)
    manager._tick_counter = 3
    manager.call(manager._on_global_tick)

    assert room.is_recording is False
    assert len(stopped) == 1
    assert stopped[0][1] == "shared_ingest_stopped"
