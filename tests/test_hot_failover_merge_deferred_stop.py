"""hot-failover 合并期间停止录制的 deferred 守卫（CLAUDE.md §11.3，2026-09-24 修复）。

合并是 concat 转码，最长达 1800s。此前它在编排线程内联执行
（shared_ingest._stop_recording_process 持 failover 锁跑合并）⇒ 全部
orchestrator.call 排队冻结，与 2026-09-01 22:56 全房断流事故同形。
现在有待合并分段时，停 sink + 合并 + 定稿整体投 worker 池。
"""
from __future__ import annotations

import time

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


class _FakeRegistry:
    def __init__(self, ingest) -> None:
        self._ingest = ingest
        self.stopped_rooms: list[tuple[str, str]] = []

    def get(self, room_id: str):
        return self._ingest

    def stop_room(self, room_id: str, reason: str = "") -> None:
        self.stopped_rooms.append((room_id, reason))


class _SlowMergeIngest:
    """NVENC→libx264 热切换后、待合并分段的共享进样替身。"""

    recording_active = True
    recording_failover_in_progress = True
    recording_error = ""
    upstream_error = ""
    preview_error = ""
    upstream_bytes = 1
    is_stopped = False
    preview_subscribers = 0
    _recording_path = ""

    #: 模拟合并耗时（真实最坏 1800s；测试取 2.5s 足以区分同步/异步）
    MERGE_SIMULATED_SEC = 2.5

    def __init__(self) -> None:
        self._recording_failover_source_paths = ["seg-0.mp4"]
        self.stop_calls = 0

    def stop_recording_sink(self, reason: str = "") -> None:
        self.stop_calls += 1
        time.sleep(self.MERGE_SIMULATED_SEC)


def _make_manager(monkeypatch, ingest, registry):
    from lsc.core.orchestrator import RoomOrchestrator

    _patch_config(monkeypatch)
    monkeypatch.setattr(
        "lsc.core.orchestrator.get_shared_ingest_registry",
        lambda: registry,
    )
    manager = RoomOrchestrator(controller_factory=lambda: object())
    monkeypatch.setattr(manager, "save_rooms", lambda: 0)
    finalized: list[str] = []
    monkeypatch.setattr(
        manager,
        "_finalize_and_commit_recording",
        lambda room, path: (finalized.append(path), "final.mp4")[1],
    )
    manager.start()
    room = manager.add_room("https://example.com/live.m3u8")
    room.is_connected = True
    room.is_recording = True
    room.record_output_path = "in-progress.mp4"
    return manager, room, finalized


def test_stop_with_pending_merge_returns_before_merge_finishes(monkeypatch) -> None:
    """有待合并分段时 stop_recording 必须立即返回，合并与定稿在 worker 完成。"""
    ingest = _SlowMergeIngest()
    registry = _FakeRegistry(ingest)
    manager, room, finalized = _make_manager(monkeypatch, ingest, registry)

    started = time.monotonic()
    ok = manager.stop_recording(room.room_id)
    elapsed = time.monotonic() - started

    assert ok is True
    assert elapsed < ingest.MERGE_SIMULATED_SEC  # 同步内联至少 2.5s
    assert room.is_recording is False
    assert finalized == []  # 合并未跑完，定稿尚未发生

    # 等 worker 跑完：停 sink → 合并 → 定稿回填
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline and not finalized:
        time.sleep(0.02)

    assert ingest.stop_calls == 1
    assert registry.stopped_rooms  # preview_subscribers=0 ⇒ 停房
    assert finalized == ["in-progress.mp4"]
    assert room.record_output_path == "final.mp4"


def test_stop_async_with_pending_merge_also_defers(monkeypatch) -> None:
    """stop_recording_async 同样不得让合并占编排线程。"""
    ingest = _SlowMergeIngest()
    registry = _FakeRegistry(ingest)
    manager, room, finalized = _make_manager(monkeypatch, ingest, registry)

    started = time.monotonic()
    ok = manager.stop_recording_async(room.room_id)
    elapsed = time.monotonic() - started

    assert ok is True
    assert elapsed < ingest.MERGE_SIMULATED_SEC
    assert room.is_recording is False

    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline and not finalized:
        time.sleep(0.02)
    assert ingest.stop_calls == 1
    assert finalized == ["in-progress.mp4"]


class _FastIngest(_SlowMergeIngest):
    """无待合并分段：走原同步快路径（stop_recording_sink 即时返回）。"""

    recording_failover_in_progress = False
    MERGE_SIMULATED_SEC = 0.0

    def __init__(self) -> None:
        self._recording_failover_source_paths = []
        self.stop_calls = 0


def test_stop_without_pending_merge_stays_synchronous(monkeypatch) -> None:
    """无 hot-failover 分段时保持原同步语义（不得无故改路径）。"""
    ingest = _FastIngest()
    registry = _FakeRegistry(ingest)
    manager, room, finalized = _make_manager(monkeypatch, ingest, registry)

    ok = manager.stop_recording(room.room_id)

    assert ok is True
    assert ingest.stop_calls == 1  # 返回时即已完成，无需轮询
    assert finalized == ["in-progress.mp4"]
    assert room.is_recording is False
