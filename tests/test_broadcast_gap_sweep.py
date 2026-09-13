"""收尾缺口补扫回归测试（2026-09-12 现场）。

现场：在线增量扫描漏掉"画面静止"区间里的回合——15:16 会话录像里 1530–1632 与
1740–1802 两段真实交战（抽帧顶中计时器 1:09 / 1:18 清晰可读）在 live 扫描里 0 候选，
而同一段用 finalize 重扫能检出。

修复：收尾时对「连续 ≥60s 无候选」的区间做 0.5fps 视觉巡检，把交战段合成候选
（``boundary_source=valorant_vision_sweep_v1``），交给同一套审计/门禁。
"""
from __future__ import annotations

import numpy as np

import lsc.analyzer.valorant_broadcast as broadcast
import lsc.analyzer.valorant_ocr_rounds as ocr_rounds

_LABELS = ("non_game", "buy", "combat", "result", "replay")


class _FakeClassifier:
    thresholds = {"stable_prob": 0.55}
    class_stable_prob = {}
    model_version = "test"
    provider = "cpu"

    def predict_batch(self, images):
        rows = []
        for image in images:
            label = _LABELS[int(image[0, 0, 0])]
            row = np.full(len(_LABELS), 0.01, dtype=np.float32)
            row[_LABELS.index(label)] = 0.97
            rows.append(row)
        return np.array(rows, dtype=np.float32)


def _extract_with(label_of):
    def fake_extract(*args, **kwargs):
        start = float(kwargs["start_sec"])
        end = float(kwargs["end_sec"])
        fps = float(kwargs.get("fps") or 1.0)
        step = 1.0 / max(0.05, fps)
        out = []
        ts = start
        while ts <= end + 1e-6:
            out.append((ts, np.full((8, 8, 3), _LABELS.index(label_of(round(ts, 2))),
                                    dtype=np.uint8)))
            ts += step
        return out

    return fake_extract


def test_merged_span_gaps_basic() -> None:
    """缺口 = [0, duration] 减去候选覆盖，只留 ≥min_gap_sec 的。"""
    gaps = broadcast._merged_span_gaps(
        [(100.0, 200.0), (190.0, 250.0), (900.0, 950.0)],
        duration=1050.0, min_gap_sec=60.0,
    )
    assert gaps == [(0.0, 100.0), (250.0, 900.0), (950.0, 1050.0)]


def test_gap_sweep_synthesises_candidate_for_missed_round(monkeypatch) -> None:
    """无候选区间里存在真实交战段 ⇒ 合成候选（带 padding，交给审计定稿）。"""
    # 候选只覆盖 0-500；缺口 500-1000 里 700-800 是交战，其余为回放/非游戏
    monkeypatch.setattr(
        ocr_rounds, "extract_frames_cancellable",
        _extract_with(lambda ts: "combat" if 700.0 <= ts <= 800.0 else "replay"),
    )
    items = broadcast.sweep_gap_rounds(
        "unused.mp4",
        [{"start": 0.0, "end": 500.0}],
        duration=1000.0,
        classifier=_FakeClassifier(),
    )
    assert len(items) == 1, items
    item = items[0]
    assert item["boundary_source"] == broadcast.GAP_SWEEP_BOUNDARY_SOURCE
    # round_key 自带（10s 桶约定）：审计缓存/分裂后缀/去重/前端身份都要用它
    assert item["round_key"] == f"round-{int(round(item['start'] / 10.0)):06d}"
    assert item["end_by"] == "next_combat" and item["confirm_status"] == "pending"
    assert item["start_by"] == "vision_gap_sweep"
    # 入点必须落在 combat 首帧附近（pad ≤ 起点门禁 onset 容差 2.5s），出点后留结算/回放
    assert 698.0 <= item["start"] <= 700.0
    assert 800.0 <= item["end"] <= 816.0
    assert item["start"] >= 700.0 - broadcast.START_GATE_ONSET_TOLERANCE_SEC


def test_gap_sweep_skips_gap_without_combat(monkeypatch) -> None:
    """缺口内无交战 ⇒ 不合成候选（不得凭空造回合）。"""
    monkeypatch.setattr(ocr_rounds, "extract_frames_cancellable",
                        _extract_with(lambda ts: "non_game"))
    assert broadcast.sweep_gap_rounds(
        "unused.mp4", [{"start": 0.0, "end": 500.0}], duration=1000.0,
        classifier=_FakeClassifier(),
    ) == []


def test_gap_sweep_splits_two_rounds_inside_one_gap(monkeypatch) -> None:
    """一个缺口里跨两个回合（中间有终态游程）⇒ 切成两条候选，而不是合并成一条。"""
    def label_of(ts: float) -> str:
        if 600.0 <= ts <= 700.0 or 730.0 <= ts <= 830.0:
            return "combat"
        if 701.0 <= ts <= 729.0:
            return "replay"
        return "non_game"

    monkeypatch.setattr(ocr_rounds, "extract_frames_cancellable", _extract_with(label_of))
    items = broadcast.sweep_gap_rounds(
        "unused.mp4", [{"start": 0.0, "end": 500.0}], duration=1000.0,
        classifier=_FakeClassifier(),
    )
    assert len(items) == 2, [(i["start"], i["end"]) for i in items]
    assert 598.0 <= items[0]["start"] <= 600.0 and 700.0 <= items[0]["end"] <= 716.0
    assert 729.0 <= items[1]["start"] <= 731.0 and items[1]["end"] >= 830.0


def test_gap_sweep_skips_short_gap(monkeypatch) -> None:
    """缺口不足 min_gap_sec 不巡（避免把轮次间隔当缺口）。"""
    monkeypatch.setattr(ocr_rounds, "extract_frames_cancellable",
                        _extract_with(lambda ts: "combat"))
    assert broadcast.sweep_gap_rounds(
        "unused.mp4", [{"start": 0.0, "end": 500.0}], duration=530.0,
        classifier=_FakeClassifier(),
    ) == []


def test_gap_sweep_is_wired_into_finalize_scan() -> None:
    """接线守门：补扫必须在收尾扫描（is_final_scan）里、且在审计之前调用。"""
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "lsc/analyzer/valorant_plugin.py").read_text(
        encoding="utf-8",
    )
    sweep_at = src.index("else sweep_gap_rounds(")
    audit_at = src.index("audited_rounds = audit_broadcast_rounds(")
    assert sweep_at < audit_at, "补扫必须在审计之前合成候选"
    # 必须在收尾分支内调用，且合成候选会被并入待审批次
    window = src[src.index("if is_final_scan:"): sweep_at]
    assert "is_final_scan" in window
    after = src[sweep_at: audit_at]
    assert "merged_candidates" in after and "sorted_candidates" in after
    # 每个收尾任务只补扫一次（否则每轮重扫全片，挤爆审计预算）
    assert 'state.get("gap_sweep_done")' in src
    assert 'state["gap_sweep_done"] = True' in src


def test_sweep_candidates_are_listable_and_still_gated() -> None:
    """补扫候选可入列（来源标记独立），但草稿门禁依旧要求审计通过。"""
    import sys
    from pathlib import Path

    backend = Path(__file__).resolve().parents[1] / "python-backend"
    if str(backend) not in sys.path:
        sys.path.insert(0, str(backend))
    import handlers.room_handler as rh

    from lsc.exporter.jianying_draft import clip_allowed_for_draft

    base = {
        "start": 700.0, "end": 800.0, "boundary_source": rh._SWEEP_BOUNDARY_SOURCE,
        "start_by": "vision_gap_sweep", "end_by": "next_combat",
        "confirm_status": "pending", "source_profile": "broadcast",
        "broadcast_audit": "pending_lookahead",
    }
    assert rh._is_ocr_round(base) is True
    assert rh._is_listable_ocr_round(dict(base)) is True
    # 未定稿不得进草稿（失败关闭不放宽）
    assert clip_allowed_for_draft(dict(base), include_pending=False) is False
    # 审计定稿后才允许
    stamped = dict(base, broadcast_audit="passed", confirm_status="vision_confirmed",
                   end_by="broadcast_exclusion", end_quality="precise",
                   end_review_required=False)
    assert clip_allowed_for_draft(stamped, include_pending=False) is True


def test_gap_sweep_start_pad_within_gate_tolerance() -> None:
    """补扫入点 pad 必须 ≤ 起点门禁 onset 容差，否则整批被门禁拒绝。

    2026-09-12 19:14 现场：补扫检出全部 17 个回合，但 5s pad 让 15 条全被
    `rejected_no_stable_combat_start` 拒掉（日志 `入点门禁拒绝: 1619.0-1705.0`）。
    """
    assert broadcast.GAP_SWEEP_START_PAD_SEC <= broadcast.START_GATE_ONSET_TOLERANCE_SEC

    # 端到端：补扫候选必须能通过起点门禁（onset 就在起点上）
    from lsc.analyzer.valorant_broadcast import _start_gate_decision

    start = 1619.0  # 补扫给出的起点（combat 首帧 - pad）
    samples = [(start, "non_game", 0.9)] + [
        (start + 1.0 + i, "combat", 0.9) for i in range(14)
    ]
    assert _start_gate_decision(samples, start=start, split_from_oversize=False) == (start, None)
