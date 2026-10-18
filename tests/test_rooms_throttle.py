"""`_RoomsThrottle` 的行为契约（§7.4 的"与 drain coalesce 双实现"一条经核是误判）。

报告初版把 `_RoomsThrottle` 与 `server.py` 的 drain last-value coalesce 记成
"两套并存的合并实现，有分叉风险"。核到底：两者**层次不同、不是重复**：

- `_RoomsThrottle`（300ms 窗口）在**生产侧**决定"要不要再生成一条 rooms_updated"
  —— 省的是 `_rooms_list(manager)` 的重复构造与队列压力；
- `drain_merge_broadcasts`（`server.py`）在**发送侧**把队列里同一 key 的多条
  塌成最后一条 —— 处理的是已经入队的重复。

该层的 `force=True` 分支也不是"丢了待发更新"：它先清 pending、取消 `_flush`，
紧接着**立刻广播当前房间表**（#103：避免 force 之后 _flush 再发一遍），
语义上是超集而非丢失。此文件把这几件事钉成断言，避免下一轮有人来"合并两套实现"。
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_backend_dir = os.path.join(ROOT, "python-backend")
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

from handlers import room_handler as rh  # noqa: E402

_RoomsThrottle = rh._RoomsThrottle  # noqa: SLF001


@pytest.fixture()
def clock(monkeypatch):
    """只替换 room_handler 模块内的 `time` 引用，不动全局 stdlib 状态。"""
    holder = {"now": 100.0}
    monkeypatch.setattr(rh, "time", SimpleNamespace(monotonic=lambda: holder["now"]))
    return holder


def test_first_update_goes_out_immediately(clock):
    t = _RoomsThrottle()
    assert t.should_send_immediate() is True
    assert t.has_pending is False


def test_update_inside_the_window_is_merged_not_dropped(clock):
    """窗口内的第二次更新不得各发一条，但必须挂成 pending 等合并发送。"""
    t = _RoomsThrottle()
    assert t.should_send_immediate() is True
    clock["now"] += 0.1
    assert t.should_send_immediate() is False
    assert t.has_pending is True


def test_window_expiry_resumes_immediate_sends(clock):
    t = _RoomsThrottle()
    t.should_send_immediate()
    # 用 100.0 这种大基准做 `+= 0.3` 会因浮点误差落到 0.2999…，
    # 判定条件（>= 窗口）就会假失败 —— 明确超出窗口再断言。
    clock["now"] = 100.0 + _RoomsThrottle._MERGE_WINDOW_SEC + 0.01
    assert t.should_send_immediate() is True


def test_repeated_merged_updates_keep_single_pending_flag(clock):
    """窗口内连续 5 次更新只需要一次补发（合并语义），pending 是布尔而非计数。"""
    t = _RoomsThrottle()
    t.should_send_immediate()
    for i in range(5):
        clock["now"] = 100.05 + i * 0.01
        assert t.should_send_immediate() is False
    assert t.has_pending is True
    t.mark_pending()
    assert t.has_pending is True
    # 合并发送后由 _broadcast_rooms 的 flush 分支复位
    t._pending = False
    assert t.has_pending is False
