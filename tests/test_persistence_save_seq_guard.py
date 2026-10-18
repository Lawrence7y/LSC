"""rooms.json 持久化竞态守卫（2026-09-24 修复）。

Timer 已触发时 ``cancel()`` 失效、写盘又可能很慢 ⇒ 两个 flush 线程可以
以旧 payload 后写，把 rooms.json 退回旧快照（丢一次房间增删）。修法是
schedule 时分配单调序号，``save_rooms`` 在 ``_persist_lock`` 内跳过过期
序号——落盘的永远是已调度序列里的最新快照。

注意：生产环境序号只由 schedule_save_rooms 分配，天然单调。测试必须经
模块计数器取号（``_seq()``），手写小序号会与调度序号混用造成假阳性。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_backend_dir = ROOT / "python-backend"
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))

import persistence as p  # noqa: E402


def _seq() -> int:
    """经模块计数器分配序号（与 schedule_save_rooms 同源，保证单调）。"""
    p._rooms_save_seq += 1
    return p._rooms_save_seq


def _room(room_id: str) -> dict:
    return {"room_id": room_id, "room_url": f"https://live.example/{room_id}"}


def _read_ids(path: Path) -> list[str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [r["room_id"] for r in payload["rooms"]]


@pytest.fixture(autouse=True)
def _isolate_persistence_state():
    """模块级保存状态跨用例隔离：计数器、待写 payload、Timer 全部快照还原。"""
    saved = (
        p._rooms_save_seq,
        p._rooms_saved_seq,
        p._pending_rooms_payload,
        p._save_rooms_timer,
    )
    yield
    if p._save_rooms_timer is not None:
        p._save_rooms_timer.cancel()
    (
        p._rooms_save_seq,
        p._rooms_saved_seq,
        p._pending_rooms_payload,
        p._save_rooms_timer,
    ) = saved


def test_newer_snapshot_wins_regardless_of_write_order(tmp_path: Path) -> None:
    """旧序号后写不得覆盖新序号内容（两个 flush 乱序完成的两种排序都要锁住）。"""
    target = tmp_path / "rooms.json"

    # 排序一：新快照先落盘，旧序号的迟到写入必须被跳过
    new_seq = _seq()
    assert p.save_rooms([_room("new")], target, save_seq=new_seq) is True
    older = new_seq - 1  # 显式取更小的序号，模拟"旧 payload 后写"
    assert p.save_rooms([_room("old")], target, save_seq=older) is True  # 过期跳过=成功
    assert _read_ids(target) == ["new"]

    # 更新的序号照常落盘
    assert p.save_rooms([_room("newest")], target, save_seq=_seq()) is True
    assert _read_ids(target) == ["newest"]

    # 排序二：旧快照先写、新快照后写——最终必须还是新快照
    target2 = tmp_path / "rooms2.json"
    older2 = _seq()
    newer2 = _seq()
    assert p.save_rooms([_room("A-old")], target2, save_seq=older2) is True
    assert p.save_rooms([_room("B-new")], target2, save_seq=newer2) is True
    assert _read_ids(target2) == ["B-new"]


def test_direct_save_without_seq_always_writes(tmp_path: Path) -> None:
    """直接 save_rooms（无序号，如 handler 显式保存）不受序号门控。"""
    target = tmp_path / "rooms.json"
    assert p.save_rooms([_room("a")], target, save_seq=_seq()) is True
    assert p.save_rooms([_room("b")], target) is True
    assert _read_ids(target) == ["b"]


def test_schedule_flush_carries_monotonic_seqs_and_newest_wins(tmp_path: Path, monkeypatch) -> None:
    """schedule 分配单调序号；flush 把序号带给 save_rooms，最新快照胜出。"""
    target = tmp_path / "rooms.json"
    seen: list[int | None] = []
    real_save = p.save_rooms

    def _spy(rooms, path=None, *, fsync=False, save_seq=None):
        seen.append(save_seq)
        return real_save(rooms, path, fsync=fsync, save_seq=save_seq)

    monkeypatch.setattr(p, "save_rooms", _spy)

    p.schedule_save_rooms([_room("x")], target, delay_sec=10.0)
    p.schedule_save_rooms([_room("y")], target, delay_sec=10.0)
    assert p.flush_pending_room_saves() is True

    seqs = [s for s in seen if s is not None]
    assert len(seqs) == 1  # 两次 schedule 合并成一次 flush
    assert _read_ids(target) == ["y"]  # 最后一次 schedule 的快照胜出

    # 再排一次：序号必须严格大于上一次 flush 携带的序号
    p.schedule_save_rooms([_room("z")], target, delay_sec=10.0)
    assert p.flush_pending_room_saves() is True
    seqs2 = [s for s in seen if s is not None]
    assert len(seqs2) == 2
    assert seqs2[1] > seqs2[0]
    assert _read_ids(target) == ["z"]


def test_flush_pending_after_concurrent_write_keeps_content(tmp_path: Path) -> None:
    """关机 flush 时同序号内容已被并发 flush 落盘：重写同内容无害，终态正确。"""
    target = tmp_path / "rooms.json"
    p.schedule_save_rooms([_room("first")], target, delay_sec=10.0)
    pending = p._pending_rooms_payload
    assert pending is not None
    _rooms, _path, seq = pending
    # 模拟并发 flush 已用同一序号落盘
    assert p.save_rooms([_room("first")], target, save_seq=seq) is True
    assert p.flush_pending_room_saves() is True
    assert _read_ids(target) == ["first"]
