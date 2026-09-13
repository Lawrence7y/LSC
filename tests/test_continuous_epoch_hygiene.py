from __future__ import annotations

from types import SimpleNamespace

from continuous_finalization import FinalizationJob

# ── 新录制 epoch 隔离 ────────────────────────────────────────────────────────


def test_epoch_changed_on_recording_id_mismatch() -> None:
    from handlers.room_handler import _continuous_epoch_changed

    room = SimpleNamespace(recording_id="rec-b")
    assert _continuous_epoch_changed({"recording_id": "rec-a"}, room, resume=False) is True


def test_epoch_unchanged_for_same_recording() -> None:
    from handlers.room_handler import _continuous_epoch_changed

    room = SimpleNamespace(recording_id="rec-a")
    assert (
        _continuous_epoch_changed({"recording_id": "rec-a"}, room, resume=False) is False
    )


def test_epoch_changed_when_recording_id_missing() -> None:
    from handlers.room_handler import _continuous_epoch_changed

    # 缺信息时按新 epoch 处理：宁可清空，也不把旧切片带进新录制
    assert _continuous_epoch_changed({}, SimpleNamespace(recording_id="rec-a"), resume=False) is True
    assert _continuous_epoch_changed(
        {"recording_id": "rec-a"}, SimpleNamespace(recording_id=""), resume=False
    ) is True
    assert _continuous_epoch_changed(None, SimpleNamespace(recording_id=""), resume=False) is True


def test_epoch_resume_keeps_previous_session() -> None:
    from handlers.room_handler import _continuous_epoch_changed

    room = SimpleNamespace(recording_id="rec-b")
    # 崩溃恢复（resume）延续同一录制，不算新 epoch
    assert _continuous_epoch_changed({"recording_id": "rec-old"}, room, resume=True) is False


def test_strip_room_scoped_keys_clears_only_target_room() -> None:
    from handlers.room_handler import _strip_room_scoped_keys

    listed_ids = {"r1:round-000000": None, "r1:round-000002": None, "r2:round-000000": None}
    bounds = {"r1:round-000000": (0.0, 1.0, "pending"), "r2:round-000000": (0.0, 1.0, "pending")}
    refined = {"r1:round-000000", "r2:round-000000"}
    removed = _strip_room_scoped_keys("r1", listed_ids, bounds, refined)
    assert removed == 4
    assert "r1:round-000000" not in listed_ids
    assert "r1:round-000002" not in listed_ids
    assert "r1:round-000000" not in refined
    # 其他房间的登记不受影响
    assert "r2:round-000000" in listed_ids
    assert "r2:round-000000" in bounds
    assert "r2:round-000000" in refined


# ── 审计拒绝终态同步清理权威切片快照 ────────────────────────────────────────


def test_prune_rejected_listed_clips_removes_all_rooms() -> None:
    from handlers.room_handler import _prune_rejected_listed_clips

    task_state = {
        "listed_clips": {
            "r1:round-000000": {"round_key": "round-000000"},
            "r2:round-000000": {"round_key": "round-000000"},
            "r1:round-000002": {"round_key": "round-000002"},
        },
    }
    candidate = {"round_key": "round-000000", "start": 1.0, "end": 22.3}
    removed = _prune_rejected_listed_clips(task_state, candidate)
    assert sorted(removed) == ["r1:round-000000", "r2:round-000000"]
    # 其他回合不受影响
    assert set(task_state["listed_clips"]) == {"r1:round-000002"}


def test_prune_rejected_listed_clips_tolerates_missing_state() -> None:
    from handlers.room_handler import _prune_rejected_listed_clips

    assert _prune_rejected_listed_clips({}, {"round_key": "round-000000"}) == []
    assert _prune_rejected_listed_clips({"listed_clips": {}}, {}) == []


def test_reject_outcome_prunes_listed_and_broadcasts() -> None:
    from handlers.room_handler import _consume_broadcast_audit_outcome

    task_state: dict = {
        "room_id": "r1",
        "listed_clips": {
            "r1:round-000000": {"round_key": "round-000000"},
            "r2:round-000000": {"round_key": "round-000000"},
            "r1:round-000002": {"round_key": "round-000002"},
        },
    }
    rejected_candidate = {"round_key": "round-000000", "start": 1.0, "end": 22.3}
    other_candidate = {"round_key": "round-000002", "start": 19.3, "end": 96.2}
    outcome = SimpleNamespace(
        status="rejected", candidate=rejected_candidate, reason="no_stable_combat_start",
    )
    broadcasts: list[dict] = []
    ok = _consume_broadcast_audit_outcome(
        [rejected_candidate, other_candidate],
        0,
        outcome,
        [],
        task_state,
        current_duration=100.0,
        broadcast=broadcasts.append,
    )
    assert ok is True
    # 拒绝终态：两个房间的 listed 快照条目都被移除，其他回合保留
    assert set(task_state["listed_clips"]) == {"r1:round-000002"}
    # 每个被清理的房间都收到 rejected 广播（前端同步移除切片）
    assert {b["data"]["room_id"] for b in broadcasts} == {"r1", "r2"}
    assert all(b["data"]["confirm_status"] == "rejected" for b in broadcasts)
    assert all(b["data"]["round_key"] == "round-000000" for b in broadcasts)
    # 拒绝 tombstone 已写入任务状态（防 OCR upsert 复活）
    assert "round-000000" in task_state["rejected_round_keys"]
    assert task_state["audit_rejected_count"] == 1


def test_reject_outcome_without_broadcast_callable_still_prunes() -> None:
    from handlers.room_handler import _consume_broadcast_audit_outcome

    task_state: dict = {"listed_clips": {"r1:round-000000": {}}}
    candidate = {"round_key": "round-000000", "start": 1.0, "end": 22.3}
    outcome = SimpleNamespace(status="rejected", candidate=candidate, reason="x")
    ok = _consume_broadcast_audit_outcome(
        [candidate], 0, outcome, [], task_state, current_duration=10.0,
    )
    assert ok is True
    assert task_state["listed_clips"] == {}


# ── 收尾 coverage 账本以 state 权威 payload 为准 ───────────────────────────


def _make_job(coverage: list[tuple[float, float]]) -> FinalizationJob:
    job = FinalizationJob.create(
        job_id="job-x",
        room_id="r1",
        recording_id="rec-x",
        source_path="final.mp4",
        final_duration=100.0,
    )
    for start, end in coverage:
        job.add_coverage(start, end)
    return job


def test_finalization_job_from_state_recovers_coverage_ledger() -> None:
    """worker 本地 job 对象不含运行时 coverage；必须从 state payload 重建判定。

    旧实现直接用本地对象 is_fully_covered() → 恒 False → 无限补扫
    （coverage_complete=true 却反复「继续补扫」的根因）。
    """
    from handlers.room_handler import _finalization_job_from_state

    # state 权威 payload 携带运行时 coverage 账本
    state = {
        "finalization_job": _make_job([(0.0, 100.0)]).to_dict(),
    }
    # 模拟 worker 本地旧对象：账本为空
    local_stale = FinalizationJob.create(
        job_id="job-x",
        room_id="r1",
        recording_id="rec-x",
        source_path="final.mp4",
        final_duration=100.0,
    )
    assert local_stale.is_fully_covered() is False  # 旧对象恒 False
    refreshed = _finalization_job_from_state(state)
    assert refreshed is not None
    assert refreshed.is_fully_covered() is True  # 权威账本判定覆盖完整


def test_finalization_job_from_state_handles_missing_payload() -> None:
    from handlers.room_handler import _finalization_job_from_state

    assert _finalization_job_from_state({}) is None
    assert _finalization_job_from_state({"finalization_job": "not-a-dict"}) is None


# ── 源码级装配守卫（防回归）─────────────────────────────────────────────────

from pathlib import Path

ROOM_HANDLER = Path("python-backend/handlers/room_handler.py")


def test_analysis_start_wires_epoch_reset_and_listed_snapshot() -> None:
    """持续分析启动必须做新 epoch 隔离：清理键位登记并显式装配 listed_clips。"""
    src = ROOM_HANDLER.read_text(encoding="utf-8")
    assert "_continuous_epoch_changed(" in src
    assert "_reset_epoch_scoped_clip_state(room_id" in src
    assert "'listed_clips':" in src


def test_clip_queued_payloads_carry_recording_id() -> None:
    """clip_queued 载荷必须携带 recording_id（草稿 recording_id 校验的数据源）。"""
    src = ROOM_HANDLER.read_text(encoding="utf-8")
    hits = src.count("'recording_id': str(getattr(target_room, 'recording_id'")
    assert hits >= 3, f"expected >=3 stamped payloads, got {hits}"


def test_finalization_gate_rebuilds_job_from_state() -> None:
    """收尾判定/落盘前必须从权威 state 重建 job（coverage 账本不得被本地对象清掉）。"""
    src = ROOM_HANDLER.read_text(encoding="utf-8")
    assert src.count("_finalization_job_from_state(state) or _finalization_job") >= 7
    assert "_finalization_job.is_fully_covered()" in src
    # 覆盖账本并集（2026-09-11：job 只记尾窗时 coverage_complete 恒 False →
    # 有界兜底永不计数 → 「继续补扫」无限循环）
    assert "_continuous_coverage_snapshot(state)" in src
    assert "_finalization_job.coverage_ranges = merge_ranges(" in src


def test_server_logs_connection_closed_ok_as_debug() -> None:
    """WS 1000 (OK) 正常关闭不得记为 ERROR。"""
    src = Path("python-backend/server.py").read_text(encoding="utf-8")
    assert "ConnectionClosedOK" in src
    assert "Client disconnected during" in src


def test_jianying_count_uses_placed_clip_count() -> None:
    """included_clip_count 必须来自实际写入切片轨的 placed_clip_count。"""
    src = Path("python-backend/handlers/jianying_handlers.py").read_text(encoding="utf-8")
    assert "placed_clip_count" in src
    assert "included_clip_count = (" in src
