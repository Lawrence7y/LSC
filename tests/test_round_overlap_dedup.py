"""L2：定稿后组内择一（同一真实回合被多条候选认领时只保留一条）。

背景（2026-09-11 20:45 现场）：``round-000123`` 与 ``round-000135`` 是同一段真实回合的
两个候选（123 的区间跨了回合边界，见 L1；修好 L1 前它们的定稿区间重叠 68.75s），
导出侧只能靠"同轨重叠跳过"草率丢弃。L2 在**入列前**做归组择一，并把被并项写成
``merged_into`` + 原因码 ``DUPLICATE_ROUND``——不静默消失。

真实夹具里没有"两条都定稿且重叠"的组合（071/123 都在门禁处被拒），故本文件用
**自造用例**覆盖该形态（这是 L2 的必要回归）。
"""
from __future__ import annotations

from pathlib import Path

import pytest
from handlers import room_handler
from handlers.jianying_handlers import _skip_reason_code

ROOM = "room-l2"
OTHER_ROOM = "room-l2-other"
ROOT = Path(__file__).resolve().parents[1]


def _round(key: str, start: float, end: float, **overrides) -> dict:
    """一条"出点已定稿"的赛事切片（默认无起点密扫证据）。"""
    item = {
        "round_key": key,
        "room_id": ROOM,
        "start": start,
        "end": end,
        "source_profile": "broadcast",
        "broadcast_audit": "passed",
        "end_quality": "precise",
        "end_by": "broadcast_exclusion",
        "confirm_status": "vision_confirmed",
        "start_quality": "coarse",
        "start_review_required": True,
        "end_review_required": False,
    }
    item.update(overrides)
    return item


@pytest.fixture()
def clean_tasks():
    """隔离 `_continuous_tasks`（`_mark_merged_rounds` 会写权威快照与 durable 账本）。"""
    saved = dict(room_handler._continuous_tasks)
    room_handler._continuous_tasks.clear()
    try:
        yield room_handler._continuous_tasks
    finally:
        room_handler._continuous_tasks.clear()
        room_handler._continuous_tasks.update(saved)


# ── 归组与择一（纯函数）──────────────────────────────────────────────────


def test_overlapping_finalized_rounds_keep_stronger_start() -> None:
    """重叠的两条定稿切片：起点证据更强者胜（现场 123↔135 的形态）。"""
    loser = _round("round-000123", 1232.0, 1420.75, start_quality="coarse")
    winner = _round("round-000135", 1352.0, 1423.25, start_delta=0.4, start_quality="precise")
    kept, merged = room_handler._dedupe_overlapping_rounds([loser, winner])
    assert [r["round_key"] for r in kept] == ["round-000135"]
    assert len(merged) == 1
    assert merged[0]["round_key"] == "round-000123"
    assert merged[0]["merged_into"] == "round-000135"
    assert merged[0]["duplicate_round"] is True
    assert merged[0]["merge_reason"] == "duplicate_round"


def test_contained_span_is_grouped() -> None:
    """包含关系（一小条落在长条里）重叠度=1.0 ⇒ 同组，留更长的一条。"""
    short = _round("round-000200", 150.0, 200.0)
    long = _round("round-000201", 100.0, 300.0)
    kept, merged = room_handler._dedupe_overlapping_rounds([short, long])
    assert [r["round_key"] for r in kept] == ["round-000201"]
    assert merged[0]["merged_into"] == "round-000201"


def test_below_threshold_not_grouped() -> None:
    """相邻但不重叠的回合（重叠度 0.1 < 0.2）不得被合并。"""
    a = _round("round-000210", 100.0, 200.0)
    b = _round("round-000211", 190.0, 290.0)
    kept, merged = room_handler._dedupe_overlapping_rounds([a, b])
    assert [r["round_key"] for r in kept] == ["round-000210", "round-000211"]
    assert merged == []


def test_not_finalized_partner_is_not_grouped() -> None:
    """未定稿的邻条不参与归组（由各自门禁收口，现场 071 END_NOT_FINAL 即此类）。"""
    finalized = _round("round-000220", 100.0, 200.0)
    pending = _round(
        "round-000221", 150.0, 210.0, broadcast_audit="pending_lookahead",
        end_quality=None, end_by="next_prep", confirm_status="pending",
    )
    kept, merged = room_handler._dedupe_overlapping_rounds([finalized, pending])
    assert [r["round_key"] for r in kept] == ["round-000220", "round-000221"]
    assert merged == []


def test_different_rooms_never_grouped() -> None:
    """跨房间只按公共轴对齐，重叠不代表同一回合：绝不互相合并。"""
    a = _round("round-000230", 100.0, 200.0)
    b = _round("round-000231", 100.0, 200.0, room_id=OTHER_ROOM)
    kept, merged = room_handler._dedupe_overlapping_rounds([a, b])
    assert len(kept) == 2 and merged == []


def test_transitive_chain_single_winner() -> None:
    """传递成组（A∩B、B∩C）只留一条，其余全部并入同一条。"""
    a = _round("round-000240", 100.0, 200.0)
    b = _round("round-000241", 150.0, 250.0)
    c = _round("round-000242", 230.0, 320.0)
    kept, merged = room_handler._dedupe_overlapping_rounds([a, b, c])
    assert [r["round_key"] for r in kept] == ["round-000240"]  # 最长且起点最早
    assert {m["round_key"] for m in merged} == {"round-000241", "round-000242"}
    assert {m["merged_into"] for m in merged} == {"round-000240"}


def test_interior_boundary_span_loses_to_clean_span() -> None:
    """有内部边界（L1 标记）的那条优先被并，即使它更长。"""
    dirty = _round("round-000250", 100.0, 260.0, interior_boundary_resume_sec=150.0)
    clean = _round("round-000251", 150.0, 210.0)
    kept, merged = room_handler._dedupe_overlapping_rounds([dirty, clean])
    assert [r["round_key"] for r in kept] == ["round-000251"]
    assert merged[0]["round_key"] == "round-000250"


def test_second_pass_is_idempotent() -> None:
    """只对保留列表再跑一次不应再产生合并（幂等）。"""
    a = _round("round-000260", 100.0, 200.0)
    b = _round("round-000261", 150.0, 210.0, start_delta=0.4)
    kept, merged = room_handler._dedupe_overlapping_rounds([a, b])
    assert merged
    kept2, merged2 = room_handler._dedupe_overlapping_rounds(kept)
    assert kept2 == kept and merged2 == []


# ── 标记（权威快照 + durable 账本 + 广播）────────────────────────────────


def test_mark_merged_rounds_updates_snapshot_and_ledger(clean_tasks) -> None:
    """被并项必须：写进 listed_clips（rejected + merged_into）、进 durable 拒绝账本、并广播。"""
    loser = _round("round-000270", 1232.0, 1420.75)
    loser["merged_into"] = "round-000135"
    loser["duplicate_round"] = True
    clean_tasks[ROOM] = {
        "room_id": ROOM,
        "target_room_ids": [ROOM],
        "listed_clips": {f"{ROOM}:round-000270": dict(loser), f"{ROOM}:round-000135": dict(_round("round-000135", 1352.0, 1423.25))},
        "rejected_round_keys": {},
        "rejected_candidates": [],
        "accepted_candidates": [],
    }
    events: list[dict] = []
    marked = room_handler._mark_merged_rounds(ROOM, [loser], broadcast=events.append)
    assert marked == 1
    listed = clean_tasks[ROOM]["listed_clips"][f"{ROOM}:round-000270"]
    assert listed["confirm_status"] == "rejected"
    assert listed["broadcast_audit"] == "rejected_duplicate_round"
    assert listed["merged_into"] == "round-000135"
    assert listed["duplicate_round"] is True
    # 幸存者不动
    assert clean_tasks[ROOM]["listed_clips"][f"{ROOM}:round-000135"]["confirm_status"] == "vision_confirmed"
    # durable 账本：拒绝 tombstone + rejected 投影（收尾/导出侧据此同一结论）
    assert "round-000270" in clean_tasks[ROOM]["rejected_round_keys"]
    assert [
        c for c in clean_tasks[ROOM]["rejected_candidates"] if c.get("round_key") == "round-000270"
    ]
    # 广播：原因可辨
    assert events and events[0]["type"] == "clip_confirm_status"
    assert events[0]["data"]["reason"] == "duplicate_round"
    assert events[0]["data"]["merged_into"] == "round-000135"


# ── 接线与原因码 ─────────────────────────────────────────────────────────


def test_dedupe_is_wired_into_listing_path() -> None:
    """源码守卫：L2 必须挂在"终态投影之后、列循环之前"，且带广播回调。"""
    src = (ROOT / "python-backend/handlers/room_handler.py").read_text(encoding="utf-8")
    anchor = "_dedupe_overlapping_rounds(highlights)"
    assert anchor in src
    window = src[src.index(anchor): src.index(anchor) + 500]
    assert "_mark_merged_rounds(" in window
    assert "bridge.queue_broadcast" in window


def test_skip_reason_code_for_duplicate_round() -> None:
    """导出提示必须把"被并"与普通拒绝区分开。"""
    assert _skip_reason_code({"broadcast_audit": "rejected_duplicate_round"}) == "DUPLICATE_ROUND"
    assert _skip_reason_code({"broadcast_audit": "rejected_interior_boundary"}) == "INTERIOR_BOUNDARY"
    assert _skip_reason_code({"broadcast_audit": "rejected_no_stable_combat_start"}) == "REJECTED"
