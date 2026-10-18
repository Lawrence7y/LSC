"""tick 逐房隔离守卫（CLAUDE.md §7.3.3，2026-09-24 修复）。

修复前 tick 的共享进样分支没有逐房 try/except：单房一处未预期异常
（如 registry 状态读取抛错）会中止本轮对其余房间的处理，并跳过
global_tick 广播——线程靠 _safe_on_deadlines 兜底不死，但"继续推进
其它房间"的字面契约不成立，UI 也会缺一拍刷新。
"""
from __future__ import annotations

from lsc.config import LscConfig


class _PoisonIngest:
    """读到 recording_active 即抛的共享进样替身（模拟单房状态损坏）。"""

    @property
    def recording_active(self) -> bool:
        raise RuntimeError("poisoned shared ingest state")


class _RoutingRegistry:
    """按 room_id 路由：A 房返回毒 ingest，B 房无 ingest（legacy）。"""

    def __init__(self, poison_room_id: str) -> None:
        self._poison_room_id = poison_room_id
        self._ingest = _PoisonIngest()

    def get(self, room_id: str):
        return self._ingest if room_id == self._poison_room_id else None

    def get_supervisor_if_exists(self, room_id: str):
        return None

    def stop_room(self, room_id: str, reason: str = "") -> None:
        return None


def test_shared_branch_exception_does_not_block_other_rooms(monkeypatch) -> None:
    from lsc.core.orchestrator import RoomOrchestrator

    monkeypatch.setattr(
        "lsc.core.orchestrator.load_config",
        lambda: LscConfig(
            ffmpeg_path="ffmpeg",
            ffprobe_path="ffprobe",
            shared_ingest_enabled=True,
        ),
    )

    class _LegacyController:
        stream_url = "https://example.com/live.m3u8"
        input_args: list[str] = []
        is_recording = False

        def __init__(self) -> None:
            self.tick_calls = 0

        def tick(self) -> None:
            self.tick_calls += 1

    controllers: dict[str, _LegacyController] = {}

    def _factory() -> _LegacyController:
        controller = _LegacyController()
        controllers[id(controller)] = controller
        return controller

    manager = RoomOrchestrator(controller_factory=_factory)
    monkeypatch.setattr(manager, "save_rooms", lambda: 0)
    manager.start()

    room_a = manager.add_room("https://example.com/a.m3u8")
    room_b = manager.add_room("https://example.com/b.m3u8")
    for room in (room_a, room_b):
        room.is_connected = True
        room.is_recording = True  # 都走录制态；A 有 ingest、B 没有
    monkeypatch.setattr(
        "lsc.core.orchestrator.get_shared_ingest_registry",
        lambda: _RoutingRegistry(room_a.room_id),
    )

    global_ticks: list[int] = []
    manager.bus.subscribe("global_tick", lambda *_: global_ticks.append(1))

    manager._tick_counter = 3
    manager.call(manager._on_global_tick)

    # B 房（legacy）必须被处理到：A 房的毒 ingest 异常被隔离在本房
    assert len(controllers) == 2
    assert any(c.tick_calls == 1 for c in controllers.values()), "其余房间被单房异常跳过"
    # global_tick 广播不得因单房异常缺席
    assert global_ticks == [1]
    # A 房状态未被半途修改（异常发生在任何状态写入之前）
    assert room_a.is_recording is True
