"""持续分析切片标签：upsert（边界精修）必须复用首发标签，不得撞号。

2026-09-13 真机现场：EDG 夺冠回顾 16 分钟场，收尾审计把多条切片的边界精修后
以 upsert 重新入列；upsert 分支不自增 `_ai_clip_counters`、label 直接取当前
计数值 ⇒ upsert 的切片与"最近一条首发"的切片共用同一个标签
（列表里同时出现 R02[00:05:18-00:05:45] 与 R02[00:01:45-00:04:15] 等）。
用户无法凭标签分辨是哪条切片。
"""
from __future__ import annotations

import os
import sys

_backend_dir = os.path.join(os.path.dirname(__file__), "..", "python-backend")
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

from handlers import room_handler as rh


def test_upsert_reuses_first_assigned_label(monkeypatch):
    helper = getattr(rh, "_resolve_ai_clip_label", None)
    assert helper is not None, "缺少 upsert 标签复用入口 _resolve_ai_clip_label"

    rid = "test-room-label"
    monkeypatch.setitem(rh._ai_clip_counters, rid, 0)

    first_a = helper(f"{rid}:round-000001", rid, "EDG夺冠回顾", 1, is_first=True)
    first_b = helper(f"{rid}:round-000002", rid, "EDG夺冠回顾", 2, is_first=True)
    # 改前：upsert 不自增计数器、label 取当前值 ⇒ 与 first_b 撞号
    upsert_a = helper(f"{rid}:round-000001", rid, "EDG夺冠回顾", 1, is_first=False)

    assert first_a != first_b, f"两条不同切片首发标签相同: {first_a} vs {first_b}"
    assert upsert_a == first_a, (
        f"upsert 必须复用首发标签 {first_a}，实得 {upsert_a}（与 {first_b} 撞号）"
    )


def test_first_allocation_is_monotonic_per_room(monkeypatch):
    helper = getattr(rh, "_resolve_ai_clip_label", None)
    assert helper is not None
    rid = "test-room-label-mono"
    monkeypatch.setitem(rh._ai_clip_counters, rid, 0)
    labels = [helper(f"{rid}:k{i}", rid, "EDG夺冠回顾", i, is_first=True) for i in range(1, 5)]
    assert len(set(labels)) == 4, f"首发标签必须互不相同: {labels}"
    # 序号必须随分配单调递增（R01→R02→R03→R04）
    indexes = [int(l.rsplit("R", 1)[1]) for l in labels]
    assert indexes == sorted(indexes) and len(set(indexes)) == 4


def test_upsert_without_memory_falls_back_gracefully(monkeypatch):
    """进程重启等场景下无首发记忆：upsert 退回当前单调计数器，不得抛异常。"""
    helper = getattr(rh, "_resolve_ai_clip_label", None)
    assert helper is not None
    rid = "test-room-label-orphan"
    monkeypatch.setitem(rh._ai_clip_counters, rid, 7)
    label = helper(f"{rid}:round-000099", rid, "EDG夺冠回顾", 99, is_first=False)
    # 无记忆回退用当前计数器（单调、尽量不撞），而非分析器的 round_idx
    assert label.endswith("R07"), f"无记忆 upsert 应回退当前计数器，实得 {label}"


def test_label_uses_monotonic_counter_not_round_index(monkeypatch):
    """根因修复：标签序号必须来自 per-room 单调计数器，不能来自分析器 round_idx。

    2026-09-13 真机第二场：round-000024 与 round-000061 的 round_index 同为 2 ⇒
    列表两条 R02（不同回合不同区间）。round_idx 相同时标签也必须不同。
    """
    fmt = getattr(rh, "format_ai_round_clip_label", None)
    assert fmt is not None
    assert fmt("EDG夺冠回顾", 2, 1) != fmt("EDG夺冠回顾", 2, 2), (
        "round_idx 相同、计数器不同时标签必须不同（否则跨批次撞号）"
    )
    # index<=0 兼容回退：仍按 round_idx 命名
    assert fmt("EDG夺冠回顾", 3, 0).endswith("R03")
    assert fmt("EDG夺冠回顾", 3).endswith("R03")
