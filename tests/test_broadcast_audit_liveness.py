"""官方解说（broadcast）在线审计存活性守卫（2026-09-11）。

现场（2026-09-11 两次真实会话）：
  * 32 分钟会话里 `audit_terminal_total = 0`，`边界审计超过预算` 12 次、每次交付 0 条；
  * 切片列表长期停留未审计的粗边界（纯回放片段 / 跨回合超长片段 / 半路截断）；
  * 实测单步 41.8s（冷）/21.4s（热）> 20s 墙钟预算，其中超长候选的门禁预取一次性
    解码 ≈514 帧 ≈23s。

本文件钉住修复后的三条性质：
  1. 取消（超预算/被抢占）时，已定稿结论留在 sink、未判定候选补发 pending ⇒ 批次完整；
  2. 在线微步骤一轮只推进一个分裂子块（单轮工作量落回预算内）；
  3. room_handler 在取消路径上交付拒绝结论（`_deliver_audit_outcomes_on_cancel`）。
"""

from __future__ import annotations

import numpy as np
import pytest

import lsc.analyzer.valorant_broadcast as broadcast
import lsc.analyzer.valorant_ocr_rounds as ocr_rounds
from lsc.analyzer.valorant_broadcast import BroadcastAuditOutcome
from lsc.utils.cancellable_ffmpeg import FFmpegCancelled

_LABELS = ("non_game", "buy", "combat", "result", "replay")


class _LivenessClassifier:
    """8x8 假帧分类器：像素首字节编码标签索引（沿用既有 fake 风格）。"""

    thresholds = {"stable_prob": 0.55}
    model_version = "test"
    provider = "cpu"

    def load(self) -> None:
        return None

    def predict_batch(self, images):
        rows = []
        for image in images:
            label = _LABELS[int(image[0, 0, 0])]
            row = np.full(len(_LABELS), 0.01, dtype=np.float32)
            row[_LABELS.index(label)] = 0.97
            rows.append(row)
        return np.array(rows, dtype=np.float32)


def _install_ocr(monkeypatch, label_at) -> None:
    def fake_extract(*args, **kwargs):
        start = float(kwargs["start_sec"])
        end = float(kwargs["end_sec"])
        return [
            (
                float(ts),
                np.full((8, 8, 3), _LABELS.index(label_at(float(ts))), dtype=np.uint8),
            )
            for ts in np.arange(start, end + 0.01, 1.0)
        ]

    monkeypatch.setattr(ocr_rounds, "extract_frames_cancellable", fake_extract)
    monkeypatch.setattr(ocr_rounds, "_read_top_anchors", lambda image: (None, None, None))


def test_cancelled_audit_batch_stays_complete(monkeypatch) -> None:
    """取消时已定稿结论不得丢，未判定候选必须补发 pending 保持批次完整。"""
    _install_ocr(monkeypatch, lambda ts: "replay")  # 整段回放 → 门禁拒绝
    sink: list = []
    candidates = [
        {"start": 0.0, "end": 20.0, "round_key": "round-live-1"},
        {"start": 100.0, "end": 120.0, "round_key": "round-live-2"},
    ]

    with pytest.raises(FFmpegCancelled):
        broadcast.audit_broadcast_rounds_with_outcomes(
            candidates,
            "unused.mp4",
            classifier=_LivenessClassifier(),
            available_end=200.0,
            outcome_sink=sink,
            # 第一个候选定稿后立即取消（等价于粗扫抢占 / 墙钟到期）
            cancel_check=lambda: bool(sink),
        )

    statuses = [o.status for o in sink]
    assert "rejected" in statuses, "已定稿的拒绝结论不得随取消丢弃"
    assert "pending" in statuses, "未判定候选必须补发 pending"
    assert {o.candidate.get("round_key") for o in sink} == {
        "round-live-1",
        "round-live-2",
    }
    assert next(o for o in sink if o.status == "pending").reason == (
        "cancelled_before_decision"
    )


def test_online_step_audits_one_oversize_child_per_call(monkeypatch) -> None:
    """在线微步骤一轮只推进一个分裂子块，其余以 pending 交回队列。"""
    _install_ocr(monkeypatch, lambda ts: "combat")
    sink: list = []
    outcomes = broadcast.audit_broadcast_rounds_with_outcomes(
        [{"start": 0.0, "end": 300.0, "round_key": "round-big"}],
        "unused.mp4",
        classifier=_LivenessClassifier(),
        available_end=400.0,
        max_media_step_sec=18.0,
        outcome_sink=sink,
    )
    assert outcomes is sink
    assert [o.candidate.get("round_key") for o in sink] == ["round-big-s0", "round-big-s1"]
    deferred = [o for o in sink if o.reason == "deferred_oversize_sibling"]
    assert [o.candidate.get("round_key") for o in deferred] == ["round-big-s1"]
    assert all(o.status == "pending" for o in deferred)


def test_oversize_slab_truncates_at_inner_replay(monkeypatch) -> None:
    """分裂块必须整段起扫：块内回放处截断，不得跨回合通过。

    现场（2026-09-11，真实录像离线复现）：203s 候选被切成两块，块内含
    "回合A → result → 非游戏 → 22s 回放 → 回合B满钟"，修复前 s0 只扫尾部
    30s 看不到块中回放，整块以 end_by=next_prep 通过（切片跨回合且带回放）。
    """
    import handlers.room_handler as room_handler

    # 0-120 交战（回合A），120-150 回放，之后新回合满钟交战
    def _label(ts: float) -> str:
        if ts < 120.0:
            return "combat"
        if ts < 150.0:
            return "replay"
        return "combat"

    _install_ocr(monkeypatch, _label)

    def _timer_at(ts: float) -> float | None:
        if 100.0 <= ts < 120.0:
            return max(0.0, 120.0 - ts)
        if ts >= 150.0:
            return max(0.0, 100.0 - (ts - 150.0))
        return None

    monkeypatch.setattr(
        ocr_rounds,
        "_read_top_anchors",
        lambda image, *_a, **_kw: (_timer_at(float(image[0, 0, 1])), 9, 8),
    )

    cache: dict = {}
    task_state = {"room_id": "room-1", "recording_id": "rec-1"}
    pending = [{"start": 0.0, "end": 300.0, "round_key": "round-two"}]
    produced: list = []
    for _turn in range(8):
        if not pending:
            break
        pending.sort(key=lambda item: float(item.get("start", 0.0)))
        candidate = dict(pending[0])
        sink: list = []
        outcomes = broadcast.audit_broadcast_rounds_with_outcomes(
            [candidate],
            "unused.mp4",
            classifier=_LivenessClassifier(),
            available_end=400.0,
            audit_cache=cache,
            max_media_step_sec=18.0,
            outcome_sink=sink,
        )
        room_handler._consume_broadcast_audit_outcome_batch(
            pending, 0, outcomes, produced, task_state,
            current_duration=400.0, broadcast=None,
        )

    slab0 = next(item for item in produced if item.get("round_key") == "round-two-s0")
    assert slab0.get("end_by") == "broadcast_exclusion", (
        f"块内回放必须截断，实际 end={slab0.get('end')} end_by={slab0.get('end_by')}"
    )
    assert 118.0 <= float(slab0["end"]) <= 128.0, f"截断点应落在回放处（含 2.5s 结算尾巴），实际 {slab0['end']}"


def test_split_slab_ignores_frame_freeze_fallback(monkeypatch) -> None:
    """分裂块整段起扫时不得用逐帧冻结兜底（块头静态画面 != 技术暂停）。

    否则块头静态画面会被判成持续 ≥2.5s 的冻结并给出贴头 cutoff，整块被
    no_active_span 拒绝——这正是整段起扫引入的新风险。
    """
    _install_ocr(monkeypatch, lambda ts: "combat")  # 全交战、像素完全一致
    outcomes = broadcast.audit_broadcast_rounds_with_outcomes(
        [{"start": 100.0, "end": 240.0, "split_from_oversize": True, "round_key": "round-slab"}],
        "unused.mp4",
        classifier=_LivenessClassifier(),
        available_end=300.0,
    )
    assert outcomes
    assert all(o.reason != "no_stable_combat" for o in outcomes), (
        f"块头静态画面不得触发 no_active_span 拒绝: {[o.reason for o in outcomes]}"
    )


def test_online_prefetch_respects_media_step_budget(monkeypatch) -> None:
    """在线预取必须与抽取同受媒体预算约束（旧实现一次预取 150s 门禁窗）。"""
    _install_ocr(monkeypatch, lambda ts: "combat")

    class SpyProvider:
        def __init__(self) -> None:
            self.ranges: list[tuple[float, float]] = []

        def prefetch_ranges(self, _video, ranges, **_kwargs) -> int:
            self.ranges.extend((float(s), float(e)) for s, e in ranges)
            return len(self.ranges)

        def get_frames(self, *_args, **kwargs):
            return ocr_rounds.extract_frames_cancellable(
                "unused.mp4",
                start_sec=float(kwargs["start_sec"]),
                end_sec=float(kwargs["end_sec"]),
                fps=float(kwargs.get("fps", 1.0)),
                ffmpeg_path="ffmpeg",
            )

    provider = SpyProvider()
    broadcast.audit_broadcast_rounds(
        [
            {"start": 0.0, "end": 150.0, "round_key": "round-s0", "split_from_oversize": True},
            {"start": 150.0, "end": 300.0, "round_key": "round-s1", "split_from_oversize": True},
        ],
        "unused.mp4",
        classifier=_LivenessClassifier(),
        available_end=400.0,
        frame_provider=provider,
        max_media_step_sec=18.0,
    )
    assert provider.ranges, "多候选在线调用应走预取路径"
    for start, end in provider.ranges:
        assert end - start <= 18.0 + 0.001, f"预取窗口 {end - start:.1f}s 超媒体预算"


def test_cancel_delivery_keeps_non_rejected_pending(monkeypatch, tmp_path) -> None:
    """取消路径只交付拒绝；accepted/manual_review 降级留队（保住"必带密扫"不变量）。"""
    import handlers.room_handler as room_handler

    pending = [{"start": 10.0, "end": 200.0, "round_key": "round-x"}]
    produced: list = []
    task_state = {"room_id": "room-1", "recording_id": "rec-1"}
    sink = [
        BroadcastAuditOutcome("rejected", {"round_key": "round-x", "start": 10.0, "end": 150.0}, "no_stable_combat_start"),
        BroadcastAuditOutcome("accepted", {"round_key": "round-y", "start": 200.0, "end": 260.0}, "ok"),
    ]
    delivered = room_handler._deliver_audit_outcomes_on_cancel(
        sink,
        pending,
        0,
        produced,
        task_state,
        current_duration=300.0,
        broadcast=None,
    )
    assert delivered == 2
    assert produced == [], "accepted 不得在取消路径直接产出（须下一轮带密扫）"
    # 拒绝已计入终态；accepted 以 pending 回写队列，槽位不得被弹掉
    assert task_state.get("audit_rejected_count") == 1
    assert pending, "批次含非终态项时不得弹出候选槽位"
    assert any(
        str(item.get("round_key") or "").startswith("round-")
        for item in pending
    )


def test_oversize_converges_to_all_terminal_under_wall_budget(monkeypatch) -> None:
    """超长候选在 20s 墙钟预算下逐轮推进，有限轮次内全部定稿且不丢子块。

    内容对齐现场：整段无可信交战锚点（现场那条 551.8s 候选 95% 为非游戏画面），
    每个子块都应在门禁处被拒 —— 关键是**每轮只推进一块、且结论不丢**。
    """
    import handlers.room_handler as room_handler

    _install_ocr(monkeypatch, lambda ts: "non_game")
    import lsc.analyzer.valorant_frame_classifier  # noqa: F401  (契约：分类器由调用方注入)

    cache: dict = {}
    task_state = {"room_id": "room-1", "recording_id": "rec-1"}
    pending = [{"start": 0.0, "end": 320.0, "round_key": "round-mega"}]
    produced: list = []
    children = {"round-mega-s0", "round-mega-s1", "round-mega-s2"}

    for _turn in range(6):
        if not pending:
            break
        pending.sort(key=lambda item: float(item.get("start", 0.0)))
        candidate = dict(pending[0])
        sink: list = []
        outcomes = broadcast.audit_broadcast_rounds_with_outcomes(
            [candidate],
            "unused.mp4",
            classifier=_LivenessClassifier(),
            available_end=400.0,
            audit_cache=cache,
            max_media_step_sec=18.0,
            outcome_sink=sink,
        )
        room_handler._consume_broadcast_audit_outcome_batch(
            pending,
            0,
            outcomes,
            produced,
            task_state,
            current_duration=400.0,
            broadcast=None,
        )

    tombstones = set((task_state.get("rejected_round_keys") or {}).keys())
    assert not pending, "所有子块必须收敛为终态，槽位应被弹出"
    assert children <= tombstones, "每个子块都要有终态（不得静默丢弃）"
    assert len(tombstones) == 3, "不得出现重复计数/重复定稿"


def test_room_handler_cancel_path_is_wired() -> None:
    """源守卫：room_handler 取消路径必须消费已定稿结论（而不是丢弃）。"""
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "python-backend/handlers/room_handler.py").read_text(
        encoding="utf-8"
    )
    assert "from lsc.utils.cancellable_ffmpeg import FFmpegCancelled" in src
    assert "outcome_sink=_audit_outcome_sink" in src
    assert "except FFmpegCancelled:" in src
    assert "_deliver_audit_outcomes_on_cancel(" in src
    # 既有硬预算契约不得被改动
    assert "_BCAST_REFINE_STEP_MAX_SEC = 20.0" in src
    assert "_BCAST_REFINE_STEP_MEDIA_SEC = 18.0" in src
