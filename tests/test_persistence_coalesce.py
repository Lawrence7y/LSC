"""持久化写合并 / fsync 降频测试。

本文件原先那条"合并窗口内不应落盘"的断言写成
`assert not path.exists() or load_rooms(path) == [...] or True` ——
**结尾的 `or True` 让它永远为真**，加上一个 0.05s 的 `time.sleep` 装作在测时序。
（债务报告 §5 / §11.29-C 同一类：不能失败的测试不是测试。）

现在改成不依赖挂钟的判据：合并的实现机制是"取消上一个定时器 + 覆盖待写快照"，
那就直接断言这两件事，并用注入的 `save_rooms` 记录真实落盘次数与 fsync 标记。
"""
from __future__ import annotations

import os
import sys
import threading
import types
from pathlib import Path

import pytest

_backend = os.path.join(os.path.dirname(__file__), "..", "python-backend")
if _backend not in sys.path:
    sys.path.insert(0, _backend)

import persistence  # noqa: E402
from persistence import (  # noqa: E402
    flush_pending_room_saves,
    load_rooms,
    schedule_save_rooms,
)


@pytest.fixture(autouse=True)
def _clean_pending():
    """模块级单例状态必须在用例间归零，否则上一条的残留 timer 会污染下一条。"""
    yield
    if persistence._save_rooms_timer is not None:
        persistence._save_rooms_timer.cancel()
        persistence._save_rooms_timer = None
    persistence._pending_rooms_payload = None


class _FakeTimer:
    """替掉 `persistence.threading.Timer`：合并、取消、到点触发全部变成可直接调用的事实。

    用注入替掉真线程定时器，是因为原用例靠 `time.sleep` 猜"合并窗口应该还没到"，
    而窗口是否真的生效与调度无关 —— 断言 `cancelled` / `delay` / 直接调 `fn()`
    才是被测判据本身。
    """

    created: list[_FakeTimer] = []

    def __init__(self, delay: float, fn) -> None:
        self.delay = delay
        self.fn = fn
        self.started = False
        self.cancelled = False
        _FakeTimer.created.append(self)

    def start(self) -> None:
        self.started = True

    def cancel(self) -> None:
        self.cancelled = True


@pytest.fixture()
def fake_timers(monkeypatch: pytest.MonkeyPatch) -> list[_FakeTimer]:
    _FakeTimer.created = []
    monkeypatch.setattr(
        persistence, "threading",
        types.SimpleNamespace(Timer=_FakeTimer, Lock=threading.Lock),
    )
    return _FakeTimer.created


def test_schedule_twice_cancels_previous_timer_and_keeps_latest_snapshot(
    tmp_path: Path, fake_timers: list[_FakeTimer]
) -> None:
    path = tmp_path / "rooms.json"
    schedule_save_rooms([{"room_id": "a"}], path, delay_sec=0.2)
    schedule_save_rooms([{"room_id": "b"}], path, delay_sec=0.2)

    assert len(fake_timers) == 2, "两次调度各建一个定时器"
    first, second = fake_timers
    assert first.cancelled is True, "合并 = 取消上一个定时器，否则会落两次盘"
    assert second.cancelled is False
    assert second.started is True
    assert persistence._save_rooms_timer is second
    payload, target, _save_seq = persistence._pending_rooms_payload
    assert payload == [{"room_id": "b"}], "待写快照必须是最后一次的内容"
    assert Path(target) == path
    assert isinstance(_save_seq, int) and _save_seq >= 2, "调度分配单调序号（2026-09-24 防旧快照后写）"
    assert second.delay == 0.2, "delay_sec 必须透传，不能写死默认 1s"


def test_pending_payload_is_a_snapshot_not_a_shared_reference(
    tmp_path: Path, fake_timers: list[_FakeTimer]
) -> None:
    """调用方随后原地改列表，不得污染已经排队的快照。"""
    path = tmp_path / "rooms.json"
    rooms: list[dict[str, object]] = [{"room_id": "a"}]
    schedule_save_rooms(rooms, path, delay_sec=0.2)
    rooms.append({"room_id": "injected"})
    payload, _, _save_seq = persistence._pending_rooms_payload
    assert payload == [{"room_id": "a"}]


def test_flush_writes_latest_and_noop_flush_reports_true(tmp_path: Path) -> None:
    path = tmp_path / "rooms.json"
    # 没有待写内容时，flush 是"无事发生"而不是失败
    assert flush_pending_room_saves(fsync=False) is True
    assert not path.exists()

    schedule_save_rooms([{"room_id": "a"}], path, delay_sec=60)
    schedule_save_rooms([{"room_id": "b"}, {"room_id": "c"}], path, delay_sec=60)
    assert flush_pending_room_saves(fsync=False) is True
    assert load_rooms(path) == [{"room_id": "b"}, {"room_id": "c"}]
    assert persistence._save_rooms_timer is None
    assert persistence._pending_rooms_payload is None
    # 再 flush 一次不应重复写
    assert flush_pending_room_saves(fsync=False) is True


def test_scheduled_timer_fires_and_writes_without_manual_flush(
    tmp_path: Path, fake_timers: list[_FakeTimer]
) -> None:
    """到点自动落盘：直接调用定时器回调，不用真等 0.01s。"""
    path = tmp_path / "rooms.json"
    schedule_save_rooms([{"room_id": "z"}], path, delay_sec=0.2)
    assert len(fake_timers) == 1
    assert not path.exists(), "没到点之前不该落盘（此处由假定时器保证，与调度无关）"

    fake_timers[0].fn()  # 模拟定时器到点
    assert load_rooms(path) == [{"room_id": "z"}]
    assert persistence._pending_rooms_payload is None
    assert persistence._save_rooms_timer is None
    # 回调再跑一次不该凭空写出内容
    fake_timers[0].fn()
    assert load_rooms(path) == [{"room_id": "z"}]


def test_fsync_only_every_n_writes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """fsync 降频是文档承诺的行为：每 `_FSYNC_EVERY_N_WRITES` 次落盘才真 fsync 一次。

    必须**包一层**真实现而不是整个替换：写计数 `_rooms_write_count` 是在 `save_rooms`
    内部自增的，替掉它就让节奏判据失去输入，测出来永远是"不 fsync"。
    """
    calls: list[bool] = []
    real_save = persistence.save_rooms

    def spy(rooms, file_path=None, **kwargs):
        result = real_save(rooms, file_path, **kwargs)
        calls.append(bool(kwargs.get("fsync", False)))
        return result

    monkeypatch.setattr(persistence, "save_rooms", spy)
    monkeypatch.setattr(persistence, "_rooms_write_count", 0)
    n = persistence._FSYNC_EVERY_N_WRITES
    for _ in range(n):
        # 序号经模块计数器分配（2026-09-24 起的 payload 是三元组），
        # 递增保证每次都大于已落盘序号、不被"过期跳过"拦掉。
        persistence._rooms_save_seq += 1
        persistence._pending_rooms_payload = (
            [{"room_id": "a"}], tmp_path / "rooms.json", persistence._rooms_save_seq,
        )
        persistence._flush_scheduled_rooms()
    assert len(calls) == n
    assert sum(calls) >= 1, f"每 {n} 次至少一次 fsync，否则崩溃后可能读到旧配置"
    assert not all(calls), f"{n} 次里全部 fsync 就是没降频"
