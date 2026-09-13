"""前端导出门禁与后端赛事草稿门禁的「出点定稿」判据必须同源。

背景：持续分析产出的赛事切片，出点由视觉审计定稿（audit passed + end precise +
broadcast_exclusion），入点仍是 coarse 的 OCR 战斗锚点，于是聚合标记
`broadcast_review_required` / `boundary_review_required` 为 true。后端草稿门禁
（`_broadcast_gate_passed`）明确允许这种切片进草稿；前端 `canExportClip` 若只认
聚合标记，就会把整条切片锁成「必须人工确认才能导出」——用户在真实环境里看到
「导出全部（0）」与「该赛事切片边界仍在审计/复核中」弹窗。

本测试把两端的判据钉在一起：任一端单独放宽/收紧都会在这里失败。
"""
from __future__ import annotations

import re
from pathlib import Path

from lsc.exporter.jianying_draft import (
    _BROADCAST_VALID_END_BY as DRAFT_VALID_END_BY,
    clip_allowed_for_draft,
)

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "lsc-electron/src/utils/clipExportPolicy.ts"


def _policy_text() -> str:
    return POLICY.read_text(encoding="utf-8")


def _ts_valid_end_by() -> set[str]:
    text = _policy_text()
    match = re.search(
        r"const BROADCAST_VALID_END_BY = new Set\(\[(.*?)\]\)",
        text,
        re.S,
    )
    assert match, "clipExportPolicy.ts 必须定义 BROADCAST_VALID_END_BY"
    return set(re.findall(r"'([^']+)'", match.group(1)))


def test_frontend_valid_end_by_matches_backend() -> None:
    from handlers.room_handler import _BROADCAST_VALID_END_BY as ROOM_VALID_END_BY

    ts_set = _ts_valid_end_by()
    assert ts_set == set(DRAFT_VALID_END_BY) == set(ROOM_VALID_END_BY)


def test_frontend_export_state_vocabulary_tracks_backend_reason_codes() -> None:
    """列表逐条标注的状态码必须与后端跳过原因码同族（2026-09-12 列表标注）。

    用户在列表里看到的「待审计/需确认/已排除」必须能对上导出侧日志里的
    NEVER_AUDITED / END_NOT_FINAL / REJECTED，否则又是两套说法。
    """
    from handlers import jianying_handlers as h

    text = _policy_text()
    for code in ("EXPORTABLE", "PENDING_AUDIT", "NEEDS_CONFIRM", "REJECTED", "BLOCKED"):
        assert f"'{code}'" in text, f"缺少导出状态 {code}"
    # 前端状态 → 后端原因码的对应关系必须写在文件里（防止只改一边）
    for backend_code in (
        h.SKIP_REASON_REJECTED,
        h.SKIP_REASON_NEVER_AUDITED,
        h.SKIP_REASON_END_NOT_FINAL,
        h.SKIP_REASON_NO_EXCLUSION_EVIDENCE,
    ):
        assert backend_code in text, f"前端未标注与后端 {backend_code} 的对应关系"


def test_frontend_mirrors_end_authoritative_conditions() -> None:
    text = _policy_text()
    start = text.find("export function hasAuthoritativeBroadcastEnd")
    assert start >= 0, "缺少 hasAuthoritativeBroadcastEnd"
    body = text[start : text.find("\n}", start)]
    for needle in (
        "clip.broadcast_audit === 'passed'",
        "clip.end_quality === 'precise'",
        "clip.end_review_required !== true",
        "clip.duration_anomaly !== true",
        "BROADCAST_VALID_END_BY.has(String(clip.end_by ?? ''))",
    ):
        assert needle in body, f"出点定稿判据缺少：{needle}"


def test_frontend_end_authoritative_shortcuts_manual_confirm() -> None:
    text = _policy_text()
    assert "hasAuthoritativeBroadcastEnd(clip)" in text
    # 定稿出点必须覆盖 pending / refining / vision_confirmed：
    # pending、vision_confirmed 是后端 listed 门禁的合法状态
    # （status in (pending, vision_confirmed)）；refining 是前端点了切片进入精修的
    # 会话态，出点已定稿时同样不得要求人工确认（否则「点开切片再导出」仍被拦）。
    assert (
        "status === 'pending' || status === 'refining' || status === 'vision_confirmed'"
        in text
    )
    # 拒绝候选人工确认也不得复活（与后端一致）。
    assert "audit.startsWith('rejected')" in text


def _real_run_clip(**overrides) -> dict:
    """2026-09-11 18:04 持续分析实跑 sidecar 的 R01 形状（字段取自产物）。"""
    base = {
        "start": 94.788,
        "end": 172.782,
        "boundary_source": "valorant_ocr_v1",
        "source_profile": "broadcast",
        "start_by": "ocr_combat",
        "end_by": "broadcast_exclusion",
        "confirm_status": "vision_confirmed",
        "broadcast_audit": "passed",
        "broadcast_audit_reason": "broadcast_replay_or_non_game",
        "broadcast_review_required": True,
        "boundary_quality": "precise",
        "boundary_review_required": False,
        "start_quality": "coarse",
        "end_quality": "precise",
        "start_review_required": True,
        "end_review_required": False,
        "duration_anomaly": False,
    }
    base.update(overrides)
    return base


def test_backend_accepts_end_authoritative_aggregate_review_clip() -> None:
    """后端草稿门禁已放行的实跑样本：前端不得再要求人工确认。"""
    assert clip_allowed_for_draft(_real_run_clip()) is True
    # 入点 coarse 把聚合边界质量降级到 coarse 的实跑样本（R03）同样放行
    assert clip_allowed_for_draft(
        _real_run_clip(boundary_quality="coarse", boundary_review_required=True)
    ) is True


def test_backend_rejects_unfinalized_end_clip() -> None:
    """未定稿出点（R04：pending_no_exclusion + open_tail）两端都必须拦。"""
    unsettled = _real_run_clip(
        confirm_status="pending",
        broadcast_audit="pending_no_exclusion",
        broadcast_audit_reason="none",
        end_by="open_tail",
        end_quality="coarse",
        boundary_quality="pending",
        boundary_review_required=True,
    )
    assert clip_allowed_for_draft(unsettled) is False
    # 出点精确但仍要求复核（审计异常放行）：不得进自动草稿
    assert clip_allowed_for_draft(
        _real_run_clip(end_review_required=True, end_quality="coarse")
    ) is False
