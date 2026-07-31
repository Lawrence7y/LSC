"""第二轮守卫：跨线程重连、广播分桶合并、导出 semaphore 在途判断。"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANAGER = (ROOT / "lsc/gui/multi_room/manager.py").read_text(encoding="utf-8")
ORCHESTRATOR = (ROOT / "lsc/core/orchestrator.py").read_text(encoding="utf-8")
SERVER = (ROOT / "python-backend/server.py").read_text(encoding="utf-8")
ROOM_HANDLER = (ROOT / "python-backend/handlers/room_handler.py").read_text(encoding="utf-8")


def test_recording_reconnect_does_not_spawn_raw_thread() -> None:
    """录制重连必须在 Qt 主线程路径执行，禁止 threading.Thread 直接改房间状态。"""
    body = ORCHESTRATOR.split("def _start_recording_reconnect_thread", 1)[1].split(
        "def _start_global_timer", 1
    )[0]
    assert "threading.Thread" not in body
    assert "_attempt_recording_reconnect" in body


def test_proactive_reconnect_does_not_spawn_raw_thread() -> None:
    """URL 过期主动重连禁止后台线程直接调用 start_recording。"""
    chunk = ORCHESTRATOR.split("stream URL expiring soon, proactive reconnect", 1)[1].split(
        'self.bus.emit("global_tick")', 1
    )[0]
    assert "threading.Thread" not in chunk
    assert "_do_proactive_reconnect" in chunk
    do_body = ORCHESTRATOR.split("def _do_proactive_reconnect", 1)[1].split(
        "def _start_recording_reconnect_thread", 1
    )[0]
    assert "start_recording" in do_body
    assert "threading.Thread" not in do_body


def test_drain_merge_keeps_per_room_recording_stopped() -> None:
    """recording_stopped 须按 room_id 分桶，不得同 type 覆盖丢多房事件。"""
    assert "def drain_merge_broadcasts" in SERVER
    body = SERVER.split("def drain_merge_broadcasts", 1)[1].split("\ndef main", 1)[0]
    assert "recording_stopped" in body
    assert "room_id" in body
    assert "clip_completed" in body or "job_id" in body


def test_export_semaphore_waits_for_in_flight_jobs() -> None:
    """热更新须同时检查队列空与在途导出计数，避免 empty() 误判。"""
    body = ROOM_HANDLER.split("async def _ensure_export_queue", 1)[1].split(
        "async def queue_export", 1
    )[0]
    assert "_export_in_flight" in body
    assert "_export_queue.empty()" in body


def test_size_update_uses_room_lock() -> None:
    """SizeUpdateJob 写 record_size_mb 须持 RoomSession 锁。"""
    orchestrator = (ROOT / "lsc/core/orchestrator.py").read_text(encoding="utf-8")
    body = orchestrator.split("class SizeUpdateJob", 1)[1].split("class RoomOrchestrator", 1)[0]
    assert "_room_lock" in body
