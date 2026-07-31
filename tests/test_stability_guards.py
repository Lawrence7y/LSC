"""稳定性优化回归测试（2026-07 稳定性专项）。

覆盖：
1. persistence.py 房间配置备份/恢复机制
2. MultiRoomManager _rooms 并发访问安全
3. SharedRoomIngest stderr 线程清理（防内存泄漏）
4. StreamCapture 孤儿进程标志位
"""
from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

_python_backend = os.path.join(os.path.dirname(__file__), '..', 'python-backend')
if _python_backend not in sys.path:
    sys.path.insert(0, _python_backend)

from persistence import load_rooms, save_rooms

from lsc.gui.multi_room.manager import MultiRoomManager


class TestRoomsBackupRecovery:
    """rooms.json 损坏时应从 .bak 备份恢复。"""

    def test_save_creates_backup_on_second_save(self, tmp_path: Path):
        rooms_file = tmp_path / "rooms.json"
        assert save_rooms([{"room_url": "https://a"}], path=rooms_file)
        # 第一次保存无备份（无旧文件可备份）
        assert not (tmp_path / "rooms.json.bak").exists()
        assert save_rooms([{"room_url": "https://b"}], path=rooms_file)
        # 第二次保存时应把上一版备份为 .bak
        bak = tmp_path / "rooms.json.bak"
        assert bak.exists()
        backed = json.loads(bak.read_text(encoding="utf-8"))
        assert backed["rooms"][0]["room_url"] == "https://a"

    def test_load_recovers_from_backup_when_main_corrupted(self, tmp_path: Path):
        rooms_file = tmp_path / "rooms.json"
        save_rooms([{"room_url": "https://a"}], path=rooms_file)
        save_rooms([{"room_url": "https://b"}], path=rooms_file)
        # 模拟主文件损坏（断电/写坏）
        rooms_file.write_text("{corrupted json!!", encoding="utf-8")

        rooms = load_rooms(path=rooms_file)
        assert rooms == [{"room_url": "https://a"}]

    def test_load_returns_empty_when_both_corrupted(self, tmp_path: Path):
        rooms_file = tmp_path / "rooms.json"
        rooms_file.write_text("{bad", encoding="utf-8")
        (tmp_path / "rooms.json.bak").write_text("{also bad", encoding="utf-8")

        assert load_rooms(path=rooms_file) == []

    def test_load_returns_empty_when_missing(self, tmp_path: Path):
        assert load_rooms(path=tmp_path / "nonexistent.json") == []


class TestManagerRoomsConcurrency:
    """MultiRoomManager._rooms 并发读写不应崩溃或丢数据。"""

    def test_manager_has_lock(self):
        manager = MultiRoomManager(controller_factory=lambda: SimpleNamespace())
        assert hasattr(manager, "_lock")

    def test_concurrent_add_and_list_rooms(self):
        manager = MultiRoomManager(controller_factory=lambda: SimpleNamespace())
        errors: list[Exception] = []

        def _add(idx: int):
            try:
                manager.add_room(f"https://example.com/live-{idx}.m3u8")
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

        def _list():
            try:
                for _ in range(50):
                    manager.list_rooms()
                    manager.room_count()
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

        threads = [threading.Thread(target=_add, args=(i,)) for i in range(8)]
        threads += [threading.Thread(target=_list) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert not errors
        assert manager.room_count() == 8

    def test_add_room_respects_max_rooms_under_concurrency(self):
        manager = MultiRoomManager(controller_factory=lambda: SimpleNamespace())
        results: list[object] = []
        lock = threading.Lock()

        def _add(idx: int):
            room = manager.add_room(f"https://example.com/live-{idx}.m3u8")
            with lock:
                results.append(room)

        threads = [threading.Thread(target=_add, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        added = [r for r in results if r is not None]
        # MAX_ROOMS=12：并发加房也不应突破上限
        assert len(added) <= 12
        assert manager.room_count() == len(added)

    def test_remove_room_thread_safe(self):
        manager = MultiRoomManager(controller_factory=lambda: SimpleNamespace())
        room = manager.add_room("https://example.com/live.m3u8")
        assert room is not None

        results: list[bool] = []
        lock = threading.Lock()

        def _remove():
            ok = manager.remove_room(room.room_id)
            with lock:
                results.append(ok)

        threads = [threading.Thread(target=_remove) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        # 只有一个线程能成功移除（pop 原子性）
        assert results.count(True) == 1
        assert manager.room_count() == 0


class TestSharedIngestStderrThreadCleanup:
    """stderr 读取线程列表应清理已结束线程，防止 24x7 运行内存累积。"""

    def _make_ingest(self):
        from lsc.core.services.shared_ingest import SharedRoomIngest
        return SharedRoomIngest(room_id="r1", url="https://example.com/live.flv")

    def test_dead_threads_pruned_on_new_reader(self):
        ingest = self._make_ingest()
        # 模拟历史遗留的已结束线程
        dead = threading.Thread(target=lambda: None)
        dead.start()
        dead.join()
        ingest._stderr_threads.extend([dead] * 5)

        # 启动新 reader（fake proc 的 stderr 立即 EOF）
        import io
        fake_proc = SimpleNamespace(stderr=io.BytesIO(b""))
        from collections import deque
        ingest._start_stderr_reader(fake_proc, deque(maxlen=10), "test")

        # 已结束线程应被清除，仅保留活跃/新线程
        assert all(
            t.is_alive() or t not in [dead]
            for t in ingest._stderr_threads
        )
        assert ingest._stderr_threads.count(dead) == 0

    def test_stop_clears_stderr_threads(self):
        ingest = self._make_ingest()
        dead = threading.Thread(target=lambda: None)
        dead.start()
        dead.join()
        ingest._stderr_threads.append(dead)

        ingest.stop(reason="test cleanup")

        assert ingest._stderr_threads == []


class TestCaptureOrphanFlag:
    """FFmpeg 孤儿进程标志：终止失败时置位，避免外部循环无限重试。"""

    def test_orphan_flag_default_false(self):
        from lsc.config import LscConfig
        from lsc.recorder.capture import StreamCapture

        capture = StreamCapture(LscConfig())
        assert capture._terminated_with_orphan is False
