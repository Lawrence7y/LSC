"""赛事草稿导出：**已定稿切片不得因「权威不可达」被丢弃**（2026-09-11 20:45 现场回归）。

夹具：`tests/fixtures/broadcast_export_case_20260911_2045/`（README 有完整 provenance）。

现场事实（全部有日志/sidecar 佐证）：
- 20:43:26 `round-000105` 赛事审计定稿（passed / broadcast_exclusion / precise）；
- 20:45:20 收尾任务态被 pop，快照丢失；
- 20:45:21 用户导出：权威回落到 20:37 的**旧**分析 sidecar，把 105 改回
  `pending_lookahead`，随后按「未确认/近似定位/未通过赛事审计」跳过；
- `round-000135` 因为不在任何权威集合，被报成「旧分析会话遗留切片」（真实原因：
  20:45:17 才出结论且出点未定稿）。

红线：本测试**不要求放宽** `_broadcast_gate_passed`。105 能回归入列靠的是它的权威
终态本该可达（C1 终态快照）；71/123/135 的出点确实未定稿 / 无排除证据，必须继续被拒。
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from handlers import jianying_handlers, room_handler
from lsc.exporter.jianying_draft import clip_allowed_for_draft

ROOT = Path(__file__).resolve().parents[1]
FIX = Path(__file__).resolve().parent / "fixtures" / "broadcast_export_case_20260911_2045"
REC = FIX / "2026-09-11_20-10-29_至_2026-09-11_20-38-42.mp4"
ROOM_ID = "9c89b6b617ef40958d51e2c4a0242954"
RECORDING_ID = "009aa22700d9482fbd68ed63d5ad3428"


def _load(name: str) -> dict:
    return json.loads((FIX / name).read_text(encoding="utf-8"))


@pytest.fixture()
def case():
    """装载夹具并把三处权威注册表复位成 20:45:21 的真实状态。"""
    clips = _load("clips.json")["clips"]
    snapshot = _load("authority_snapshot.json")
    expected = _load("expected.json")

    # 复位（其它测试可能污染模块级注册表）
    saved_continuous = dict(jianying_handlers._continuous_tasks)
    saved_jobs = dict(jianying_handlers._analysis_jobs)
    saved_snapshots = getattr(jianying_handlers, "_authority_snapshots", None)
    saved_room_snapshots = getattr(room_handler, "_last_authority_snapshots", None)
    jianying_handlers._continuous_tasks.clear()
    jianying_handlers._analysis_jobs.clear()
    if saved_snapshots is not None:
        saved_snapshots.clear()

    # 注册注入的等价物：生产由 register_jianying_handlers(authority_snapshots=…) 完成
    if saved_snapshots is not None and saved_room_snapshots is not None:
        jianying_handlers._authority_snapshots = saved_room_snapshots

    # 模拟 20:45:20 的 pop（C1：pop 前保留终态快照）。修复前没有这个入口，
    # 快照不保留 ⇒ included 只有 3 条（行为红，而不是导入错误）。
    preserve = getattr(room_handler, "_preserve_authority_snapshot", None)
    if preserve is not None:
        preserve(ROOM_ID, {
            "listed_clips": snapshot["listed_clips"],
            "target_room_ids": [ROOM_ID],
            "recording_id": RECORDING_ID,
        })

    yield SimpleNamespace(clips=clips, snapshot=snapshot, expected=expected)

    jianying_handlers._continuous_tasks.clear()
    jianying_handlers._continuous_tasks.update(saved_continuous)
    jianying_handlers._analysis_jobs.clear()
    jianying_handlers._analysis_jobs.update(saved_jobs)
    if saved_snapshots is not None:
        saved_snapshots.clear()
        if saved_room_snapshots is not None:
            jianying_handlers._authority_snapshots = saved_snapshots
    if saved_room_snapshots is not None:
        saved_room_snapshots.clear()


def _stub_room() -> SimpleNamespace:
    return SimpleNamespace(
        room_id=ROOM_ID,
        record_output_path=str(REC),
        recording_id=RECORDING_ID,
    )


def _run_export(clips: list[dict], room: SimpleNamespace) -> tuple[list[str], dict[str, str]]:
    """按 `_make_clip_source` 的真实顺序跑：并权威字段 → 权威归属校验 → 草稿门禁。

    原因码与生产同源：从**合并权威字段之后**的切片上取（`_record_skip` 用的是 `c`），
    否则会拿前端陈旧副本判因（现场 123 的前端副本还是 pending_lookahead）。
    """
    included: list[str] = []
    skipped: dict[str, str] = {}
    for raw in clips:
        merged = jianying_handlers._merge_authoritative_clip(raw)
        reconciled, reason = jianying_handlers._reconcile_clip_with_authority(merged, room)
        key = str(raw.get("round_key") or "")
        if reconciled is None:
            skipped[key] = jianying_handlers._skip_reason_code(merged, reason or "无权威来源")
            continue
        if not clip_allowed_for_draft(reconciled, include_pending=False):
            skipped[key] = jianying_handlers._skip_reason_code(reconciled, "未通过草稿门禁")
            continue
        included.append(key)
    return included, skipped


def test_never_audited_reason_code():
    """reason_code 分类：pending_lookahead（从未审计）→ NEVER_AUDITED。"""
    assert jianying_handlers._skip_reason_code(
        {"confirm_status": "pending", "broadcast_audit": "pending_lookahead"},
        "blocked_by_gate",
    ) == "NEVER_AUDITED"


def test_end_not_final_reason_code():
    """audit=passed 但出点未定稿（next_prep/coarse/需复核）→ END_NOT_FINAL。"""
    for clip in (
        {"confirm_status": "pending", "broadcast_audit": "passed", "end_by": "next_prep", "end_quality": "coarse"},
        {"confirm_status": "vision_confirmed", "broadcast_audit": "passed", "end_by": "next_combat", "end_quality": "coarse"},
        {"confirm_status": "vision_confirmed", "broadcast_audit": "passed", "end_by": "broadcast_exclusion", "end_quality": "precise", "end_review_required": True},
    ):
        assert jianying_handlers._skip_reason_code(clip, "blocked_by_gate") == "END_NOT_FINAL"


def test_no_exclusion_evidence_reason_code():
    assert jianying_handlers._skip_reason_code(
        {"confirm_status": "pending", "broadcast_audit": "pending_no_exclusion", "end_by": "next_prep"},
        "blocked_by_gate",
    ) == "NO_EXCLUSION_EVIDENCE"


def test_superseded_split_fragment_has_its_own_reason_code():
    """2026-09-14 现场：超长分裂碎片被并入兄弟后，原因必须是"已被接管"，
    而不是"出点证据不足"——后者会让人去修一个不存在的 bug。

    不传 sibling_lookup 也成立（合并标记随切片透传），保持纯函数语义。
    """
    clip = {
        "round_key": "round-000070-s0",
        "confirm_status": "pending",
        "broadcast_audit": "pending_no_exclusion",
        "end_by": "next_combat",
        "superseded_by_round_key": "round-000070-s1",
    }
    assert jianying_handlers._skip_reason_code(clip, "blocked_by_gate") == (
        "SUPERSEDED_BY_SPLIT_MERGE"
    )


def test_sibling_owns_round_reason_code():
    """同族兄弟已用权威出点定稿 ⇒ 本碎片是回合外的残余，原因可辨（仍不导出）。"""
    tail = {"round_key": "round-000044-s0", "broadcast_audit": "passed",
            "end_by": "broadcast_exclusion", "end_quality": "precise"}

    def lookup(key):
        return tail if key == "round-000044-s0" else None

    clip = {
        "round_key": "round-000044-s1",
        "confirm_status": "pending",
        "broadcast_audit": "pending_no_exclusion",
        "end_by": "next_combat",
    }
    assert jianying_handlers._skip_reason_code(
        clip, "blocked_by_gate", sibling_lookup=lookup
    ) == "SIBLING_OWNS_ROUND"
    # 不传 lookup ⇒ 保持原判据（不引入新的放行/归类副作用）
    assert jianying_handlers._skip_reason_code(clip, "blocked_by_gate") == (
        "NO_EXCLUSION_EVIDENCE"
    )


def test_sibling_owns_round_needs_authoritative_sibling():
    """兄弟没定稿 / 出点非法 / 查不到 ⇒ 不得套用 SIBLING_OWNS_ROUND（防误判）。"""
    clip = {
        "round_key": "round-000044-s1",
        "confirm_status": "pending",
        "broadcast_audit": "pending_no_exclusion",
    }
    for sibling in (
        None,
        {"broadcast_audit": "pending_no_exclusion", "end_by": "broadcast_exclusion"},
        {"broadcast_audit": "passed", "end_by": "next_combat"},
        {"broadcast_audit": "passed"},
    ):
        assert jianying_handlers._skip_reason_code(
            clip, "blocked_by_gate", sibling_lookup=lambda _k, s=sibling: s
        ) == "NO_EXCLUSION_EVIDENCE"

    def boom(_key):
        raise RuntimeError("authority unavailable")

    assert jianying_handlers._skip_reason_code(
        clip, "blocked_by_gate", sibling_lookup=boom
    ) == "NO_EXCLUSION_EVIDENCE"
    # 非分裂切片不参与家族解释
    assert jianying_handlers._skip_reason_code(
        {**clip, "round_key": "round-000044"}, "blocked_by_gate",
        sibling_lookup=lambda _k: {"broadcast_audit": "passed", "end_by": "broadcast_exclusion"},
    ) == "NO_EXCLUSION_EVIDENCE"


def test_sibling_lookup_falls_back_to_parent_key():
    """2026-09-14 真实会话回归：真实回合是用**父键**定稿的（round-000071
    711.0-802.2 vision_confirmed），分裂碎片 s0 只是它的早期投影；残余碎片
    round-000071-s1(862.0-874.3) 必须报"兄弟已覆盖回合"，而不是 NEVER_AUDITED。"""
    parent = {"round_key": "round-000071", "broadcast_audit": "passed",
              "end_by": "broadcast_exclusion", "end_quality": "precise"}
    lookup = lambda key: parent if key == "round-000071" else None
    clip = {"round_key": "round-000071-s1", "confirm_status": "pending",
            "broadcast_audit": "pending_lookahead", "end_by": "next_prep"}
    assert jianying_handlers._skip_reason_code(
        clip, "blocked_by_gate", sibling_lookup=lookup
    ) == "SIBLING_OWNS_ROUND"
    # 不带 lookup 时仍是原码（纯函数语义不变）
    assert jianying_handlers._skip_reason_code(clip, "blocked_by_gate") == "NEVER_AUDITED"

    # -s0 碎片同理（父键已定稿 ⇒ 该碎片是同回合的早期投影）
    s0 = {"round_key": "round-000071-s0", "confirm_status": "pending",
          "broadcast_audit": "pending_no_exclusion"}
    assert jianying_handlers._skip_reason_code(
        s0, "blocked_by_gate", sibling_lookup=lookup
    ) == "SIBLING_OWNS_ROUND"


def test_sibling_does_not_override_rejection():
    """被拒就是被拒：不得被"兄弟已覆盖"文案盖掉。"""
    clip = {
        "round_key": "round-000044-s1",
        "confirm_status": "rejected",
        "broadcast_audit": "rejected_no_stable_combat",
    }
    assert jianying_handlers._skip_reason_code(
        clip, "blocked_by_gate",
        sibling_lookup=lambda _k: {"broadcast_audit": "passed", "end_by": "broadcast_exclusion"},
    ) == "REJECTED"


def test_split_family_key_parsing_matches_analyzer():
    """解析规则必须与分析器同源（合并侧与解释侧不能各写一套）。"""
    from lsc.analyzer.valorant_broadcast import _split_family_base_key as analyzer_base

    for key, expected in (
        ("round-000070-s1", "round-000070"),
        ("round-000070-s0", "round-000070"),
        ("round-000070-s12", "round-000070"),
        ("round-000070", ""),
        ("", ""),
        ("round-000070-sx", ""),
    ):
        assert jianying_handlers._split_family_base_key(key) == expected
        assert jianying_handlers._split_family_base_key(key) == analyzer_base(key)
    assert jianying_handlers._split_fragment_index("round-000070-s12") == 12
    assert jianying_handlers._split_fragment_index("round-000070") is None


def test_rejected_round_stays_rejected():
    assert jianying_handlers._skip_reason_code(
        {"confirm_status": "pending", "broadcast_audit": "rejected_no_stable_combat_start"},
        "blocked_by_gate",
    ) == "REJECTED"


def test_reconcile_rejected_text_maps_to_rejected_code():
    """L3 实测：权威校验阶段的拒绝文案是「非当前录制权威切片：已拒绝(...)」，
    而切片 dict 自身可能仍带陈旧的 pending_lookahead —— 必须判成 REJECTED。"""
    assert jianying_handlers._skip_reason_code(
        {"confirm_status": "pending", "broadcast_audit": "pending_lookahead",
         "round_key": "round-000007"},
        "非当前录制权威切片：已拒绝(no_stable_combat)",
    ) == "REJECTED"


def test_rejection_reason_prefers_audit_over_ok_gate():
    """入点门禁通过（"ok"）时不得把 "ok" 当拒绝原因（前端会显示「已拒绝(ok)」）。"""
    helper = getattr(room_handler, "_rejection_reason", None)
    if helper is None:
        pytest.skip("_rejection_reason 未实现")
    assert helper({"broadcast_start_gate": "ok", "broadcast_audit": "rejected_no_stable_combat"}) == (
        "rejected_no_stable_combat"
    )
    assert helper({"broadcast_start_gate": "no_stable_combat", "broadcast_audit": ""}) == "no_stable_combat"
    assert helper({}) == "rejected"


def test_not_in_authority_reason_code():
    assert jianying_handlers._skip_reason_code(
        {"round_key": "round-999999"},
        "旧分析会话遗留切片",
    ) == "NOT_IN_AUTHORITY"


def test_export_recovers_finalized_clip_and_reports_reason_codes(case):
    """核心回归：105 回归入列；其余四条继续被拒且原因可辨。"""
    room = _stub_room()
    included, skipped = _run_export(case.clips, room)

    assert set(included) == set(case.expected["included"]), (
        f"included 应为 {case.expected['included']}，实际 {included}；"
        "现场：105 已定稿却因权威不可达被跳过，135 被误报为旧会话遗留"
    )
    assert set(skipped) == set(case.expected["skipped"])

    # 每条跳过都能给出结构化原因（C4），不再是一句「未确认/近似定位/未通过赛事审计」
    for round_key, expect in case.expected["skipped"].items():
        assert skipped[round_key] == expect["reason_code"], (
            f"{round_key} 期望 {expect['reason_code']}，实得 {skipped[round_key]}"
        )


def test_scan_path_terminals_are_projected_into_durable_state():
    """C2：扫描通路定稿必须同时进 durable 账本（否则收尾 sidecar 缺条目）。"""
    projected = getattr(room_handler, "_project_scan_audit_terminals", None)
    if projected is None:
        pytest.skip("C2 未实现（夹具 A 的改前状态）")

    highlights = [
        {  # 现场 20:43:26 的 105：已定稿，却只进 listed
            "round_key": "round-000105", "start": 1050.2, "end": 1113.3,
            "confirm_status": "vision_confirmed", "broadcast_audit": "passed",
            "end_by": "broadcast_exclusion", "end_quality": "precise",
        },
        {  # 现场 20:44:09 的 123：审计跑完但无排除证据 → manual_review
            "round_key": "round-000123", "start": 1232.0, "end": 1346.0,
            "confirm_status": "pending", "broadcast_audit": "pending_no_exclusion",
            "end_by": "next_prep",
        },
        {  # 仍未定稿：不得进账本
            "round_key": "round-000135", "start": 1352.0, "end": 1445.0,
            "confirm_status": "pending", "broadcast_audit": "pending_lookahead",
        },
        {  # 已拒绝：进 rejected 账本 + tombstone
            "round_key": "round-000093", "start": 927.25, "end": 1050.25,
            "confirm_status": "pending", "broadcast_audit": "rejected_no_stable_combat_start",
        },
    ]
    state: dict = {"room_id": ROOM_ID, "recording_id": RECORDING_ID}

    first = projected(state, highlights)
    assert first == 3, f"应补投影 3 条（105/123/93），实得 {first}"
    # accepted / manual_review 同槽（既有 durable 语义），靠计数区分；
    # 123 的 broadcast_audit=pending_no_exclusion 会让门禁继续拒它（不放宽判据）。
    assert [c["round_key"] for c in state["accepted_candidates"]] == ["round-000105", "round-000123"]
    assert [c["round_key"] for c in state["rejected_candidates"]] == ["round-000093"]
    assert "round-000093" in state["rejected_round_keys"]
    assert state["audit_terminal_total"] == 3
    assert state["audit_accepted_count"] == 1
    assert state["audit_rejected_count"] == 1
    assert state["audit_manual_review_count"] == 1
    # 扫描通路的结果本来就已入列：不得因此制造 delivery_gap
    assert state["audit_delivered_total"] == 2
    assert int(state.get("audit_delivery_gap") or 0) == 0

    # 幂等：重复投影不重复计数
    assert projected(state, highlights) == 0
    assert state["audit_terminal_total"] == 3


def test_analysis_save_path_follows_finalize_rename(tmp_path):
    """C3：归档改名后必须写新名 sidecar，否则「至_」文件的分析快照永远停在改名那一刻。"""
    sync = getattr(room_handler, "_sync_analysis_save_path", None)
    if sync is None:
        pytest.skip("C3 未实现")

    old = tmp_path / "2026-09-11_20-10-29_录制中.mp4"
    new = tmp_path / "2026-09-11_20-10-29_至_2026-09-11_20-38-42.mp4"
    new.write_bytes(b"")

    class Manager:
        def __init__(self, path):
            self._path = path

        def get_room(self, _rid):
            return SimpleNamespace(room_id="r1", record_output_path=str(self._path))

    # 改名后：旧名已不存在、房间当前录像是新名 → 写新名
    assert sync(Manager(new), "r1", str(old)) == str(new)
    # 路径未变 → 原样
    assert sync(Manager(new), "r1", str(new)) == str(new)
    # 两个都不存在（probe 失败等）：退回入参，绝不把落盘变成空路径
    missing = tmp_path / "missing.mp4"
    assert sync(Manager(missing), "r1", str(old)) == str(old)
    # 房间当前录像不存在但入参存在：保留入参（不丢已有快照）
    old.write_bytes(b"")
    assert sync(Manager(missing), "r1", str(old)) == str(old)


def test_listed_without_terminal_detection():
    """C6：listed 里有、却无终态也不在待审计队列的回合必须被识别出来。"""
    helper = getattr(room_handler, "_listed_items_without_terminal", None)
    if helper is None:
        pytest.skip("C6 未实现")

    state = {
        "listed_clips": {
            f"{ROOM_ID}:round-000105": {"round_key": "round-000105", "broadcast_audit": "passed"},
            f"{ROOM_ID}:round-000135": {"round_key": "round-000135", "broadcast_audit": "passed"},
        },
        "accepted_candidates": [{"round_key": "round-000105"}],
        "rejected_round_keys": {},
        "ocr_runtime_state": {"broadcast_pending_rounds": []},
        "refine_result_queue": [],
    }
    assert [item["round_key"] for item in helper(state)] == ["round-000135"]

    # 队列里还有它 → 算"待审计"，不算缺归属
    state["ocr_runtime_state"]["broadcast_pending_rounds"] = [{"round_key": "round-000135"}]
    assert helper(state) == []

    # 落进 refine 结果队列（已定稿待交付）→ 同样算有归属
    state["ocr_runtime_state"]["broadcast_pending_rounds"] = []
    state["refine_result_queue"] = [{"candidate": {"round_key": "round-000135"}}]
    assert helper(state) == []


def test_finalize_completion_waits_for_listed_terminals():
    """收尾完成判定必须把「listed 无归属」计入未收尾（源码级守卫）。"""
    src = (ROOT / "python-backend" / "handlers" / "room_handler.py").read_text(encoding="utf-8")
    anchor = "coverage_complete = bool(state.get('coverage_complete'))"
    assert anchor in src
    window = src[src.index(anchor): src.index(anchor) + 1200]
    assert "_listed_items_without_terminal(state)" in window, (
        "收尾完成判定必须检查 listed 里是否还有无终态归属的切片（现场 20:45 的 135）"
    )


def test_authority_snapshot_survives_task_pop(case):
    """C1：任务态没了，权威快照仍必须能回答「这条 round_key 属于当前录制 epoch」。"""
    snapshots = getattr(room_handler, "_last_authority_snapshots", None)
    if snapshots is None:
        pytest.skip("C1 未实现（夹具 A 的改前状态）")
    snap = snapshots.get(ROOM_ID)
    assert snap is not None and snap["listed_clips"]
    # 105 的终态审计字段必须来自权威快照，而不是 20:37 的旧 sidecar
    key = f"{ROOM_ID}:round-000105"
    listed = snap["listed_clips"][key]
    assert listed["broadcast_audit"] == "passed"
    assert listed["end_by"] == "broadcast_exclusion"
    assert listed["end_quality"] == "precise"
