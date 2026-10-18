"""三层验证接入生产停止路径的守卫（CLAUDE.md §5.1.1，2026-09-24 修复）。

修复前生产停止/重连路径全部绕过 validate_recording：0 字节 / <0.1MB /
无视频格式头的损坏录像会被定稿改名并上报成功，用户到导出阶段才发现。
本文件锁住：stop_recording 对无效成品必须置 room.last_error。
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


class _FastIngest:
    """无待合并分段的共享进样替身（走同步停止路径）。"""

    recording_active = True
    recording_failover_in_progress = False
    recording_error = ""
    upstream_error = ""
    preview_error = ""
    upstream_bytes = 1
    is_stopped = False
    preview_subscribers = 0
    _recording_path = ""

    def __init__(self) -> None:
        self._recording_failover_source_paths = []
        self.stop_calls = 0

    def stop_recording_sink(self, reason: str = "") -> None:
        self.stop_calls += 1


class _FakeRegistry:
    def __init__(self, ingest) -> None:
        self._ingest = ingest

    def get(self, room_id: str):
        return self._ingest

    def stop_room(self, room_id: str, reason: str = "") -> None:
        return None


def _make_manager(monkeypatch, ingest, tmp_path, recording_bytes: bytes):
    from lsc.core.orchestrator import RoomOrchestrator

    _patch_config(monkeypatch)
    monkeypatch.setattr(
        "lsc.core.orchestrator.get_shared_ingest_registry",
        lambda: _FakeRegistry(ingest),
    )
    manager = RoomOrchestrator(controller_factory=lambda: object())
    monkeypatch.setattr(manager, "save_rooms", lambda: 0)
    monkeypatch.setattr(
        manager,
        "_finalize_and_commit_recording",
        lambda room, path: path,  # 跳过改名，聚焦验证本身
    )
    manager.start()
    room = manager.add_room("https://example.com/live.m3u8")
    room.is_connected = True
    room.is_recording = True
    output = tmp_path / "room.mp4"
    output.write_bytes(recording_bytes)
    room.record_output_path = str(output)
    return manager, room, output


def test_stop_recording_flags_invalid_output(monkeypatch, tmp_path) -> None:
    """200KB 全零文件：通过大小层、倒在格式签名层 ⇒ 必须置 last_error。"""
    manager, room, _output = _make_manager(
        monkeypatch, _FastIngest(), tmp_path, b"\x00" * (200 * 1024),
    )

    ok = manager.stop_recording(room.room_id)

    assert ok is True  # 停止动作本身成功
    assert "录制文件无效" in room.last_error
    assert room.is_recording is False


def test_stop_recording_flags_undersized_output(monkeypatch, tmp_path) -> None:
    """带合法 MP4 头但只有 4KB：倒在大小层（<0.1MB）。"""
    header = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * (4 * 1024)
    manager, room, _output = _make_manager(
        monkeypatch, _FastIngest(), tmp_path, header,
    )

    manager.stop_recording(room.room_id)

    assert "录制文件无效" in room.last_error


def test_stop_recording_accepts_valid_output(monkeypatch, tmp_path) -> None:
    """合法 MP4（ftyp@4 + >0.1MB）不得被误判。"""
    payload = b"\x00\x00\x00\x18ftypmp42" + b"\x11" * (300 * 1024)
    manager, room, _output = _make_manager(
        monkeypatch, _FastIngest(), tmp_path, payload,
    )

    manager.stop_recording(room.room_id)

    assert "录制文件无效" not in (room.last_error or "")


def test_validate_helper_covers_all_three_layers(tmp_path) -> None:
    """辅助方法本身覆盖路径/大小/格式三层，且验证故障不阻断。"""
    from lsc.core.orchestrator import RoomOrchestrator
    from lsc.core.session import RoomSession

    manager = RoomOrchestrator(controller_factory=lambda: object())
    room = RoomSession(room_id="r1", room_url="https://example.com/live.m3u8")

    # 路径不存在：本层不判定失败（外部移动/已定稿）
    assert manager._validate_recording_output(room, str(tmp_path / "nope.mp4")) is True

    # 格式签名错误
    bad = tmp_path / "bad.mp4"
    bad.write_bytes(b"\x00" * 1024)
    assert manager._validate_recording_output(room, str(bad)) is False
    assert "录制文件无效" in room.last_error

    # FLV 合法签名
    flv = tmp_path / "ok.flv"
    flv.write_bytes(b"FLV" + b"\x00" * (200 * 1024))
    room.last_error = ""
    assert manager._validate_recording_output(room, str(flv)) is True
    assert room.last_error == ""
