"""赛事（broadcast）切片「失败关闭」门禁回归测试。

覆盖用户五条修复中与门禁/元数据/收尾完成相关的关键不变量：
- 自动导出/自动草稿必须等待 broadcast_audit == passed；
- 元数据端到端携带审计字段；
- 收尾完成需要 coverage_complete + 审计队列清空；
- 自动草稿 includePending=false。
"""
from __future__ import annotations

from pathlib import Path

from lsc.exporter.jianying_draft import clip_allowed_for_draft

ROOT = Path(__file__).resolve().parents[1]
WORKBENCH = ROOT / "lsc-electron/src/pages/Workbench/index.tsx"


def _broadcast_candidate(**overrides) -> dict:
    base = {
        "start": 10.0,
        "end": 60.0,
        "boundary_source": "valorant_ocr_v1",
        "source_profile": "broadcast",
        "start_by": "ocr_combat",
        "end_by": "next_prep",
        "confirm_status": "vision_confirmed",
        "broadcast_audit": "passed",
        "broadcast_review_required": False,
        "duration_anomaly": False,
    }
    base.update(overrides)
    return base


def test_hybrid_clip_metadata_carries_broadcast_audit_fields() -> None:
    from handlers.room_handler import _hybrid_clip_metadata

    meta = _hybrid_clip_metadata(_broadcast_candidate())
    assert meta["source_profile"] == "broadcast"
    assert meta["broadcast_audit"] == "passed"
    assert meta["end_by"] == "next_prep"
    assert "broadcast_review_required" in meta
    assert "duration_anomaly" in meta


def test_auto_exportable_broadcast_requires_audit() -> None:
    from handlers.room_handler import _is_auto_exportable_valorant_round

    passed = _broadcast_candidate(end_by="broadcast_exclusion")
    assert _is_auto_exportable_valorant_round(passed) is True

    # 未经回放/暂停审计（策略被覆盖成 pov/valorant 时的典型缺失）→ 禁止
    missing_audit = _broadcast_candidate()
    missing_audit.pop("broadcast_audit", None)
    assert _is_auto_exportable_valorant_round(missing_audit) is False

    # pending_lookahead / 未定稿出点 → 禁止
    pending_lookahead = _broadcast_candidate(
        broadcast_audit="pending_lookahead",
        confirm_status="pending",
        end_by="next_combat",
    )
    assert _is_auto_exportable_valorant_round(pending_lookahead) is False

    # 需要人工复核 → 禁止
    review_required = _broadcast_candidate(broadcast_review_required=True)
    assert _is_auto_exportable_valorant_round(review_required) is False

    # 时长异常 → 禁止
    anomaly = _broadcast_candidate(duration_anomaly=True)
    assert _is_auto_exportable_valorant_round(anomaly) is False


def test_clip_allowed_for_draft_broadcast_fail_closed() -> None:
    passed = _broadcast_candidate(end_by="broadcast_exclusion")
    passed["mark_precision"] = "exact"
    assert clip_allowed_for_draft(passed) is True

    # 未审计 → 禁止（即使 confirm_status=vision_confirmed）
    unaudited = _broadcast_candidate()
    unaudited.pop("broadcast_audit", None)
    unaudited["mark_precision"] = "exact"
    assert clip_allowed_for_draft(unaudited) is False

    # 出点非法（next_combat/open_tail）→ 禁止
    bad_end = _broadcast_candidate(end_by="next_combat")
    bad_end["mark_precision"] = "exact"
    assert clip_allowed_for_draft(bad_end) is False

    # 手动确认可绕过审计缺失
    manual = _broadcast_candidate(confirm_status="user_confirmed")
    manual.pop("broadcast_audit", None)
    manual["mark_precision"] = "exact"
    assert clip_allowed_for_draft(manual) is True

    # 勾选「包含待确认切片」时允许 pending（仍受 approx 拦截）
    pending = _broadcast_candidate(confirm_status="pending", broadcast_audit="pending_lookahead")
    pending["mark_precision"] = "exact"
    assert clip_allowed_for_draft(pending, include_pending=True) is True
    assert clip_allowed_for_draft(pending, include_pending=False) is False


def test_auto_draft_uses_include_pending_false() -> None:
    text = WORKBENCH.read_text(encoding="utf-8")
    idx = text.find("includePending: false")
    assert idx >= 0, "自动草稿必须使用 includePending: false"


def test_auto_draft_waits_for_finalization_gate() -> None:
    text = WORKBENCH.read_text(encoding="utf-8")
    idx = text.find("finalizedOk")
    assert idx >= 0, "自动草稿必须等待收尾完成门禁"
    window = text[idx : idx + 600]
    assert "finalization_state === 'completed'" in window
    assert "coverage_complete === true" in window
    assert "audit_delivery_gap" in window
    assert "pending_queue_depth" in window


def test_coverage_gate_requires_full_scan() -> None:
    from handlers.room_handler import (
        _record_continuous_scan_coverage,
        _refresh_audit_queue_state,
    )

    state = {"coverage_ranges": [], "finalization_job": None}
    _record_continuous_scan_coverage(state, (0.0, 50.0), final_duration=100.0)
    assert state["coverage_complete"] is False
    _record_continuous_scan_coverage(state, (50.0, 100.0), final_duration=100.0)
    assert state["coverage_complete"] is True

    state["ocr_runtime_state"] = {"broadcast_pending_rounds": [{"start": 1.0}]}
    _refresh_audit_queue_state(state)
    assert state["pending_queue_depth"] == 1
    assert state["audit_delivery_gap"] == 0


def test_jianying_authoritative_merge_overrides_audit_fields() -> None:
    from handlers import jianying_handlers as h

    h._continuous_tasks = {
        "room-1": {
            "listed_clips": {
                "room-1:round-1": _broadcast_candidate(end_by="broadcast_exclusion"),
            },
        },
    }
    try:
        merged = h._merge_authoritative_clip({
            "room_id": "room-1",
            "round_key": "round-1",
            "confirm_status": "user_confirmed",
            "source_profile": "pov",  # 前端伪造 → 后端必须用权威 broadcast 覆盖
        })
        assert merged["source_profile"] == "broadcast"
        assert merged["broadcast_audit"] == "passed"
        assert merged["end_by"] == "broadcast_exclusion"
    finally:
        h._continuous_tasks = {}
