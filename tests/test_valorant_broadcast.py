from __future__ import annotations

import math

import numpy as np

import lsc.analyzer.valorant_broadcast as broadcast
import lsc.analyzer.valorant_ocr_rounds as ocr_rounds
from lsc.analyzer.base import ScanWindow
from lsc.analyzer.valorant_broadcast import (
    BroadcastAuditOutcome,
    _merge_split_family_fragments,
    audit_broadcast_phase_sequence,
    audit_broadcast_rounds,
)
from lsc.analyzer.valorant_plugin import ValorantAnalyzerPlugin
from lsc.analyzer.valorant_profile import resolve_valorant_profile


def test_broadcast_audit_failure_keeps_file_ocr_candidates(monkeypatch, tmp_path) -> None:
    video = tmp_path / "broadcast.mp4"
    video.write_bytes(b"placeholder")
    candidate = {"start": 10.0, "end": 80.0, "phase": "combat"}
    monkeypatch.setattr(
        ocr_rounds,
        "detect_valorant_rounds_ocr",
        lambda *_args, **_kwargs: [candidate],
    )
    monkeypatch.setattr(
        broadcast,
        "audit_broadcast_rounds",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("model unavailable")),
    )

    result = ValorantAnalyzerPlugin().analyze_file(
        str(video),
        options={"valorant_profile": "broadcast"},
    )

    assert result and result[0]["start"] == 10.0
    assert result[0]["broadcast_audit"] == "skipped"
    assert result[0]["broadcast_review_required"] is True


def test_broadcast_audit_failure_keeps_incremental_candidates(monkeypatch, tmp_path) -> None:
    video = tmp_path / "broadcast.mp4"
    video.write_bytes(b"placeholder")
    candidate = {"start": 10.0, "end": 80.0, "phase": "combat"}
    monkeypatch.setattr(
        ocr_rounds,
        "detect_valorant_rounds_ocr",
        lambda *_args, **_kwargs: [candidate],
    )
    monkeypatch.setattr(
        broadcast,
        "audit_broadcast_rounds",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("model unavailable")),
    )

    result = ValorantAnalyzerPlugin().scan_window(
        str(video),
        ScanWindow(0.0, 90.0, timeout_sec=30.0, use_ocr=True),
        {"valorant_profile": "broadcast", "finalize": False},
    )

    assert result and result[0]["broadcast_audit"] == "skipped"
    assert result[0]["broadcast_review_required"] is True


def test_long_broadcast_candidate_gets_split_instead_of_wholesale_rejected(monkeypatch) -> None:
    """2026-09-08 修复：超长结构无效候选不再整条拒绝，按 ≤150s 切块重审计，
    避免合并回合内部真实回合（回放过滤后）全部丢失。"""

    class FakeClassifier:
        thresholds = {"stable_prob": 0.55}
        model_version = "test"
        provider = "cpu"

        def load(self) -> None:
            return None

    # 纯函数层面：430s 候选切成 3 块，边界连续、round_key 后缀去重、
    # end_coarse 重置为块尾（截断后的 end_delta 保持小量级）。
    chunks = broadcast._expand_oversize_candidates([
        {
            "start": 32.0,
            "end": 462.0,
            "source_profile": "broadcast",
            "round_key": "round-000003",
        }
    ])
    assert len(chunks) == 3
    assert [c["start"] for c in chunks] == [32.0, 182.0, 332.0]
    assert [c["end"] for c in chunks] == [182.0, 332.0, 462.0]
    assert all(c["split_from_oversize"] for c in chunks)
    assert [c["round_key"] for c in chunks] == [
        "round-000003-s0",
        "round-000003-s1",
        "round-000003-s2",
    ]
    assert all(c["end_coarse"] == c["end"] for c in chunks)
    # 非超长候选原样通过
    small = broadcast._expand_oversize_candidates([{"start": 1.0, "end": 100.0}])
    assert len(small) == 1
    assert small[0]["start"] == 1.0 and small[0]["end"] == 100.0

    # 审计入口：超长候选分裂后每个子块获得独立 outcome，不再出现整条
    # long_or_invalid 拒绝。
    monkeypatch.setattr(
        ocr_rounds, "extract_frames_cancellable", lambda *_args, **_kwargs: []
    )
    monkeypatch.setattr(ocr_rounds, "_read_top_anchors", lambda image: (None, None, None))
    outcomes = broadcast.audit_broadcast_rounds_with_outcomes(
        [{"start": 32.0, "end": 462.0, "source_profile": "broadcast"}],
        "unused.mp4",
        classifier=FakeClassifier(),
        available_end=636.0,
    )
    assert len(outcomes) == 3
    assert all(o.status == "rejected" for o in outcomes)
    assert all(o.candidate.get("split_from_oversize") for o in outcomes)


def test_replay_stable_run_cuts_before_replay() -> None:
    samples = [
        (float(i), "combat", 0.95) for i in range(10)
    ] + [
        (10.0, "replay", 0.96),
        (10.5, "replay", 0.96),
        (11.0, "replay", 0.96),
    ]

    cutoff, reason = audit_broadcast_phase_sequence(
        samples, start=0.0, end=20.0,
    )

    assert cutoff == 9.75
    assert reason == "broadcast_replay_or_non_game"


def test_single_replay_prediction_does_not_cut() -> None:
    samples = [
        (float(i), "combat", 0.95) for i in range(10)
    ] + [(10.0, "replay", 0.96), (10.5, "combat", 0.95)]

    assert audit_broadcast_phase_sequence(samples, start=0.0, end=20.0) == (None, None)


def test_replay_evidence_survives_short_unknown_gap() -> None:
    """转场产生短暂 unknown 时，前后 Replay 证据仍应形成稳定排除。"""
    samples = [
        (float(i), "combat", 0.95) for i in range(10)
    ] + [
        (10.0, "replay", 0.80),
        (11.0, "unknown", 0.62),
        (12.0, "replay", 0.78),
    ]

    cutoff, reason = audit_broadcast_phase_sequence(
        samples,
        start=0.0,
        end=20.0,
    )

    assert cutoff == 9.75
    assert reason == "broadcast_replay_or_non_game"


def test_unknown_or_non_game_blip_with_active_timer_does_not_cut() -> None:
    samples = [
        (float(i), "combat", 0.95) for i in range(10)
    ] + [
        (10.0, "unknown", 0.50),
        (10.5, "non_game", 0.70),
        (11.0, "unknown", 0.51),
        (11.5, "combat", 0.90),
        (12.0, "combat", 0.90),
    ]
    timer_samples = [
        (10.0, 70.0, "unknown"),
        (11.0, 69.0, "non_game"),
        (12.0, 68.0, "combat"),
    ]

    assert audit_broadcast_phase_sequence(
        samples,
        timer_samples,
        start=0.0,
        end=12.0,
    ) == (None, None)


def test_broadcast_audit_can_extend_past_ocr_end() -> None:
    samples = [
        (float(i), "combat", 0.95) for i in range(10)
    ] + [
        (10.0, "combat", 0.95),
        (10.5, "combat", 0.95),
        (11.0, "replay", 0.96),
        (11.5, "replay", 0.96),
        (12.0, "replay", 0.96),
    ]

    cutoff, reason = audit_broadcast_phase_sequence(
        samples,
        start=0.0,
        end=10.0,
        scan_end=20.0,
    )

    assert cutoff == 10.75
    assert reason == "broadcast_replay_or_non_game"


def test_broadcast_audit_does_not_extend_without_post_end_combat() -> None:
    samples = [
        (float(i), "combat", 0.95) for i in range(10)
    ] + [
        (10.5, "non_game", 0.90),
        (11.0, "non_game", 0.90),
        (11.5, "non_game", 0.90),
        (12.0, "non_game", 0.90),
        (12.5, "replay", 0.96),
    ]

    assert audit_broadcast_phase_sequence(
        samples,
        start=0.0,
        end=10.0,
        scan_end=20.0,
    ) == (None, None)


def test_live_broadcast_audit_waits_for_unwritten_lookahead(monkeypatch) -> None:
    class FakeClassifier:
        thresholds = {"stable_prob": 0.55}
        model_version = "test"
        provider = "cpu"

        def load(self) -> None:
            return None

        def predict_batch(self, images):
            return np.tile(
                np.array([[0.01, 0.01, 0.97, 0.005, 0.005]], dtype=np.float32),
                (len(images), 1),
            )

    def fake_extract(*args, **kwargs):
        available = min(float(kwargs["end_sec"]), 20.0)
        return [
            (
                float(ts),
                np.full((8, 8, 3), int(ts * 10) % 255, dtype=np.uint8),
            )
            for ts in np.arange(0.0, available + 0.01, 1.0)
        ]

    monkeypatch.setattr(ocr_rounds, "extract_frames_cancellable", fake_extract)
    monkeypatch.setattr(ocr_rounds, "_read_top_anchors", lambda image: (None, None, None))

    result = audit_broadcast_rounds(
        [{"start": 0.0, "end": 10.0}],
        "unused.mp4",
        classifier=FakeClassifier(),
        available_end=20.0,
    )

    assert len(result) == 1
    assert result[0]["broadcast_audit"] == "pending_lookahead"


def test_broadcast_audit_reuses_cached_pending_tail(monkeypatch) -> None:
    calls = []

    class FakeClassifier:
        thresholds = {"stable_prob": 0.55}
        model_version = "test"
        provider = "cpu"

        def load(self) -> None:
            return None

        def predict_batch(self, images):
            return np.tile(
                np.array([[0.01, 0.01, 0.97, 0.005, 0.005]], dtype=np.float32),
                (len(images), 1),
            )

    def fake_extract(*args, **kwargs):
        start = float(kwargs["start_sec"])
        end = float(kwargs["end_sec"])
        calls.append((start, end))
        return [
            (
                float(ts),
                np.full((8, 8, 3), int(ts * 10) % 255, dtype=np.uint8),
            )
            for ts in np.arange(start, end + 0.01, 1.0)
        ]

    monkeypatch.setattr(ocr_rounds, "extract_frames_cancellable", fake_extract)
    monkeypatch.setattr(ocr_rounds, "_read_top_anchors", lambda image: (None, None, None))

    cache = {}
    first = audit_broadcast_rounds(
        [{"start": 0.0, "end": 10.0, "end_by": "next_prep"}],
        "unused.mp4",
        classifier=FakeClassifier(),
        available_end=20.0,
        audit_cache=cache,
    )
    second = audit_broadcast_rounds(
        [{"start": 0.0, "end": 10.0, "end_by": "next_prep"}],
        "unused.mp4",
        classifier=FakeClassifier(),
        available_end=100.0,
        audit_cache=cache,
    )

    assert first[0]["broadcast_audit"] == "pending_lookahead"
    assert second[0]["broadcast_audit"] == "pending_no_exclusion"
    assert second[0]["broadcast_next_prep_invalidated"] is True
    # 在线入点门禁只单独扫头部 [0,10]；尾部审计仍从候选尾窗开始 [0,20]，
    # 首轮已否决 next_prep，重试必须从缓存末尾扩展到完整 90s 后视窗，
    # 不再停在原“强出点”的 45s 窗。
    assert calls == [(0.0, 10.0), (0.0, 20.0), (19.0, 100.0)]


def test_broadcast_audit_without_exclusion_keeps_pending(monkeypatch) -> None:
    """B-01/B-02: OCR 以 next_combat 闭合且未发现真实排除证据（reason=none）时，
    严禁伪造 broadcast_exclusion 或提升为 vision_confirmed，必须保持 pending。
    """
    class FakeClassifier:
        thresholds = {"stable_prob": 0.55}
        model_version = "test"
        provider = "cpu"

        def load(self) -> None:
            return None

        def predict_batch(self, images):
            # 全 combat（索引 2）：无排除段 -> cutoff=None
            return np.tile(
                np.array([[0.01, 0.01, 0.97, 0.005, 0.005]], dtype=np.float32),
                (len(images), 1),
            )

    def fake_extract(*args, **kwargs):
        start = float(kwargs["start_sec"])
        end = float(kwargs["end_sec"])
        return [
            (float(ts), np.full((8, 8, 3), int(ts * 10) % 255, dtype=np.uint8))
            for ts in np.arange(start, end + 0.01, 1.0)
        ]

    monkeypatch.setattr(ocr_rounds, "extract_frames_cancellable", fake_extract)
    monkeypatch.setattr(ocr_rounds, "_read_top_anchors", lambda image: (None, None, None))

    candidate = {
        "start": 0.0,
        "end": 10.0,
        "phase": "combat",
        "boundary_source": "valorant_ocr_v1",
        "source_profile": "broadcast",
        "start_by": "ocr_combat",
        "end_by": "next_combat",
        "confirm_status": "pending",
    }
    result = audit_broadcast_rounds(
        [candidate],
        "unused.mp4",
        classifier=FakeClassifier(),
        available_end=100.0,
    )

    assert len(result) == 1
    # 核心修复：无排除证据不能标 passed，不能伪造 broadcast_exclusion
    assert result[0]["broadcast_audit"] == "pending_no_exclusion"
    assert result[0]["confirm_status"] == "pending"
    assert result[0]["end_by"] == "next_combat"
    assert result[0]["boundary_refined"] is False
    assert result[0]["broadcast_review_required"] is True


def test_broadcast_audit_with_replay_promotes_to_exclusion(monkeypatch) -> None:
    """B-01/B-02: 当检测到真实 replay 画面时，精确截断并提升为 vision_confirmed 与 broadcast_exclusion。"""
    class ReplayClassifier:
        thresholds = {"stable_prob": 0.55}
        model_version = "test"
        provider = "cpu"

        def load(self) -> None:
            return None

        def predict_batch(self, images):
            # 模拟：前 10 帧是 combat (idx 2)，之后是 replay (idx 3)
            preds = []
            for _ in images:
                preds.append([0.01, 0.01, 0.01, 0.96, 0.01])
            return np.array(preds, dtype=np.float32)

    def fake_extract(*args, **kwargs):
        start = float(kwargs["start_sec"])
        end = float(kwargs["end_sec"])
        return [
            (float(ts), np.full((8, 8, 3), int(ts * 10) % 255, dtype=np.uint8))
            for ts in np.arange(start, end + 0.01, 1.0)
        ]

    monkeypatch.setattr(ocr_rounds, "extract_frames_cancellable", fake_extract)
    monkeypatch.setattr(ocr_rounds, "_read_top_anchors", lambda image: (None, None, None))

    candidate = {
        "start": 0.0,
        "end": 20.0,
        "phase": "combat",
        "boundary_source": "valorant_ocr_v1",
        "source_profile": "broadcast",
        "start_by": "ocr_combat",
        "end_by": "next_combat",
        "confirm_status": "pending",
        # 模拟已完成的入点物理精修：双向证据完整才允许 boundary_refined=True
        "start_delta": 0.2,
        "start_confidence": 0.95,
    }
    # 让前 20s 为 combat，20s 以后为 replay
    class HybridClassifier:
        thresholds = {"stable_prob": 0.55}
        model_version = "test"
        provider = "cpu"

        def load(self) -> None:
            return None

        def predict_batch(self, images):
            preds = []
            for img in images:
                ts_val = int(img[0, 0, 0]) / 10.0
                if ts_val < 20.0:
                    preds.append([0.01, 0.01, 0.96, 0.01, 0.01])
                else:
                    preds.append([0.01, 0.01, 0.01, 0.96, 0.01])
            return np.array(preds, dtype=np.float32)

    result = audit_broadcast_rounds(
        [candidate],
        "unused.mp4",
        classifier=HybridClassifier(),
        available_end=100.0,
    )

    assert len(result) == 1
    assert result[0]["broadcast_audit"] == "passed"
    assert result[0]["confirm_status"] == "vision_confirmed"
    assert result[0]["end_by"] == "broadcast_exclusion"
    assert result[0]["broadcast_excluded_reason"] == "broadcast_replay_or_non_game"
    assert result[0]["boundary_refined"] is True
    assert result[0]["boundary_refined_by"] == "broadcast_audit_v2"



def test_broadcast_audit_vetoes_false_next_prep_when_combat_continues(monkeypatch) -> None:
    """next_prep 后立即持续 combat 证明 OCR 出点为假，不得盖章 passed。"""
    class FakeClassifier:
        thresholds = {"stable_prob": 0.55}
        model_version = "test"
        provider = "cpu"

        def load(self) -> None:
            return None

        def predict_batch(self, images):
            return np.tile(
                np.array([[0.01, 0.01, 0.97, 0.005, 0.005]], dtype=np.float32),
                (len(images), 1),
            )

    def fake_extract(*args, **kwargs):
        start = float(kwargs["start_sec"])
        end = float(kwargs["end_sec"])
        return [
            (float(ts), np.full((8, 8, 3), int(ts * 10) % 255, dtype=np.uint8))
            for ts in np.arange(start, end + 0.01, 1.0)
        ]

    monkeypatch.setattr(ocr_rounds, "extract_frames_cancellable", fake_extract)
    monkeypatch.setattr(ocr_rounds, "_read_top_anchors", lambda image: (None, None, None))

    result = audit_broadcast_rounds(
        [{
            "start": 0.0, "end": 10.0, "phase": "combat",
            "boundary_source": "valorant_ocr_v1", "source_profile": "broadcast",
            "start_by": "ocr_combat", "end_by": "next_prep",
            "confirm_status": "vision_confirmed",
        }],
        "unused.mp4",
        classifier=FakeClassifier(),
        available_end=100.0,
    )

    assert result[0]["broadcast_audit"] == "pending_lookahead"
    assert result[0]["broadcast_next_prep_invalidated"] is True
    assert result[0]["_audit_continue_ready"] is True


def test_broadcast_backward_cut_keeps_short_result_presentation_tail(monkeypatch) -> None:
    """常规赛事审计向前截回粗出点时，保留结算横幅的展示尾巴。"""
    class FakeClassifier:
        thresholds = {"stable_prob": 0.55}
        model_version = "test"
        provider = "cpu"

        def load(self) -> None:
            return None

        def predict_batch(self, images):
            rows = []
            for image in images:
                ts = float(image[0, 0, 0]) / 10.0
                row = [0.01] * 5
                row[2 if ts < 20.0 else 4] = 0.96
                rows.append(row)
            return np.asarray(rows, dtype=np.float32)

    def fake_extract(*args, **kwargs):
        start = float(kwargs["start_sec"])
        end = float(kwargs["end_sec"])
        return [
            (float(ts), np.full((8, 8, 3), float(ts) * 10.0, dtype=np.float32))
            for ts in np.arange(start, end + 0.01, 1.0)
        ]

    monkeypatch.setattr(ocr_rounds, "extract_frames_cancellable", fake_extract)
    monkeypatch.setattr(ocr_rounds, "_read_top_anchors", lambda image: (None, None, None))

    result = audit_broadcast_rounds(
        [{
            "start": 0.0,
            "end": 40.0,
            "end_coarse": 40.0,
            "start_by": "ocr_combat",
            "end_by": "next_combat",
            "source_profile": "broadcast",
            "start_delta": 0.2,
            "start_confidence": 0.95,
        }],
        "unused.mp4",
        classifier=FakeClassifier(),
        available_end=100.0,
    )

    assert result[0]["broadcast_audit"] == "passed"
    assert result[0]["broadcast_result_tail_sec"] == 2.5
    assert 21.5 <= result[0]["end"] <= 23.0
    assert result[0]["end_quality"] == "precise"
    assert result[0]["end_review_required"] is False


def test_false_next_prep_extends_to_real_replay_boundary(monkeypatch) -> None:
    """粗出点后仍交战时，应延长到真实 replay，而不是保留过早 next_prep。"""
    class FakeClassifier:
        thresholds = {"stable_prob": 0.55}
        model_version = "test"
        provider = "cpu"

        def load(self) -> None:
            return None

        def predict_batch(self, images):
            rows = []
            for image in images:
                ts = float(image[0, 0, 0]) / 10.0
                label = 2 if ts < 31.0 else 4
                row = [0.01] * 5
                row[label] = 0.96
                rows.append(row)
            return np.asarray(rows, dtype=np.float32)

    def fake_extract(*args, **kwargs):
        start = float(kwargs["start_sec"])
        end = float(kwargs["end_sec"])
        return [
            (
                float(ts),
                np.full((8, 8, 3), float(ts) * 10.0, dtype=np.float32),
            )
            for ts in np.arange(start, end + 0.01, 1.0)
        ]

    monkeypatch.setattr(ocr_rounds, "extract_frames_cancellable", fake_extract)
    monkeypatch.setattr(ocr_rounds, "_read_top_anchors", lambda image: (None, None, None))

    result = audit_broadcast_rounds(
        [{
            "start": 0.0,
            "end": 10.0,
            "phase": "combat",
            "boundary_source": "valorant_ocr_v1",
            "source_profile": "broadcast",
            "start_by": "ocr_combat",
            "end_by": "next_prep",
            "confirm_status": "vision_confirmed",
            "start_delta": 0.1,
            "start_confidence": 0.95,
        }],
        "unused.mp4",
        classifier=FakeClassifier(),
        available_end=100.0,
    )

    assert result[0]["broadcast_audit"] == "passed"
    assert result[0]["end_by"] == "broadcast_exclusion"
    assert result[0]["broadcast_next_prep_invalidated"] is True
    assert 29.0 <= result[0]["end"] <= 31.0


def test_broadcast_fallback_rescans_only_unscanned_head(monkeypatch) -> None:
    """尾窗无 combat 触发兜底全扫时，只补抽未扫描的头部，不重抽已在 samples 的尾窗。

    注：回看窗口已从 30s 放宽到 60s（见 BROADCAST_AUDIT_TAIL_LOOKBACK_SEC 注释，
    现场 round-000071 因窗口太短丢回合）。短候选因此一次就覆盖到头部，
    长候选仍必须只补抽头部。
    """
    calls: list[tuple[float, float]] = []

    class FakeClassifier:
        thresholds = {"stable_prob": 0.55}
        model_version = "test"
        provider = "cpu"

        def load(self) -> None:
            return None

        def predict_batch(self, images):
            # 全 replay（索引 4）：尾窗无 combat → 触发兜底全扫
            return np.tile(
                np.array([[0.01, 0.01, 0.01, 0.01, 0.96]], dtype=np.float32),
                (len(images), 1),
            )

    def fake_extract(*args, **kwargs):
        start_ts = float(kwargs["start_sec"])
        end_ts = float(kwargs["end_sec"])
        calls.append((start_ts, end_ts))
        return [
            (float(ts), np.full((8, 8, 3), int(ts * 10) % 255, dtype=np.uint8))
            for ts in np.arange(start_ts, end_ts + 0.01, 1.0)
        ]

    monkeypatch.setattr(ocr_rounds, "extract_frames_cancellable", fake_extract)
    monkeypatch.setattr(ocr_rounds, "_read_top_anchors", lambda image: (None, None, None))

    lookback = broadcast.BROADCAST_AUDIT_TAIL_LOOKBACK_SEC

    # ① 短候选（短于回看窗）：一次抽帧就覆盖头部，不得再重复补抽头部
    audit_broadcast_rounds(
        [{
            "start": 0.0, "end": 50.0,
            "start_delta": 0.2, "start_confidence": 0.95, "start_refined": 0.0,
        }],
        "unused.mp4",
        classifier=FakeClassifier(),
        available_end=200.0,
    )
    assert calls, "至少要有一次抽帧"
    assert calls[0][0] == 0.0, f"回看窗 {lookback}s 应已覆盖头部：{calls}"
    assert len(calls) == 1, f"头部已在窗内，不应重复补抽：{calls}"

    # ② 长候选（远长于回看窗）：兜底只补抽未扫描的头部 [start, extract_start]
    calls.clear()
    audit_broadcast_rounds(
        [{
            "start": 0.0, "end": 400.0,
            "start_delta": 0.2, "start_confidence": 0.95, "start_refined": 0.0,
        }],
        "unused.mp4",
        classifier=FakeClassifier(),
        available_end=600.0,
    )
    heads = [c for c in calls if c[0] == 0.0]
    assert heads, f"长候选必须补抽头部：{calls}"
    assert heads[0][1] <= 400.0 - lookback + 1.0, f"补抽范围应止于尾窗起点：{calls}"
    assert len(calls) >= 2


def test_broadcast_timer_ocr_skips_unknown_and_replay_frames(monkeypatch) -> None:
    """提速回归：计时器 OCR 只在结果被消费的帧触发——combat@stride 与
    non_game/buy/result；unknown/replay 帧的计时器从不被读取，必须跳过。"""
    seq_labels = [
        "combat", "combat", "unknown", "replay", "combat", "non_game",
        "buy", "result", "combat", "unknown", "replay", "combat",
    ]
    label_to_idx = {"non_game": 0, "buy": 1, "combat": 2, "result": 3, "replay": 4}

    def _row(lb: str) -> list[float]:
        # unknown 不是类别，而是置信度 < stable_prob(0.55) 时的降级标签：
        # 用全 0.2 行使 argmax 置信 0.2 < 0.55 → 审计归为 unknown。
        if lb == "unknown":
            return [0.2] * 5
        row = [0.01] * 5
        row[label_to_idx[lb]] = 0.96
        return row

    probs_arr = np.array([_row(lb) for lb in seq_labels], dtype=np.float32)

    class FakeClassifier:
        thresholds = {"stable_prob": 0.55}
        model_version = "test"
        provider = "cpu"

        def load(self) -> None:
            return None

        def predict_batch(self, images):
            return probs_arr[: len(images)]

    def fake_extract(*args, **kwargs):
        # 返回 12 帧，像素值 = 局部 index，供 OCR 记录器识别是哪一帧
        return [
            (float(i), np.full((8, 8, 3), i, dtype=np.uint8))
            for i in range(len(seq_labels))
        ]

    ocr_called_idx: list[int] = []

    def fake_read_top_anchors(image, *_a, **_k):
        ocr_called_idx.append(int(image[0, 0, 0]))
        return None, None, None

    monkeypatch.setattr(ocr_rounds, "extract_frames_cancellable", fake_extract)
    monkeypatch.setattr(ocr_rounds, "_read_top_anchors", fake_read_top_anchors)

    audit_broadcast_rounds(
        [{
            "start": 0.0, "end": 2.0,
            # 该用例聚焦计时器 OCR 触发规则；入点已精修，跳过在线入点门禁，
            # 避免 2 秒合成窗口因“稳定 combat 不足 2s”被门禁提前拒绝。
            "start_delta": 0.1, "start_confidence": 0.95, "start_refined": 0.0,
        }],
        "unused.mp4",
        classifier=FakeClassifier(),
        available_end=200.0,
    )

    called = set(ocr_called_idx)
    # combat@stride(0,4,8) + non_game(5)/buy(6)/result(7)
    assert called == {0, 4, 5, 6, 7, 8}
    # unknown(2,9) 与 replay(3,10) 绝不触发 OCR；非 stride 的 combat(1,11) 也不触发
    assert not (called & {1, 2, 3, 9, 10, 11})

    # 持续分析的有界微步骤不再对 combat 帧周期跑通用
    # OCR；只保留首个排除帧读数用于否决“交战钟仍在走”。
    # 这保证 DirectML 上一个 18s 视觉步骤不会叠加 4–9 次
    # 耗时数十秒的计时器 OCR。
    ocr_called_idx.clear()
    audit_broadcast_rounds(
        [{
            "start": 0.0,
            "end": 2.0,
            "start_delta": 0.1,
            "start_confidence": 0.95,
            "start_refined": 0.0,
        }],
        "unused.mp4",
        classifier=FakeClassifier(),
        available_end=200.0,
        max_media_step_sec=18.0,
    )
    assert len(ocr_called_idx) <= 1


def test_realtime_fast_mode_defers_broadcast_audit(monkeypatch, tmp_path) -> None:
    """实时追赶时先保存候选，不能让 90s broadcast lookahead 阻塞 OCR。"""
    video = tmp_path / "broadcast.mp4"
    video.write_bytes(b"placeholder")
    candidate = {"start": 10.0, "end": 80.0, "phase": "combat"}
    audit_called = False

    monkeypatch.setattr(
        ocr_rounds,
        "detect_valorant_rounds_ocr",
        lambda *_args, **_kwargs: [candidate],
    )

    def fail_audit(*_args, **_kwargs):
        nonlocal audit_called
        audit_called = True
        raise AssertionError("realtime fast mode must defer broadcast audit")

    monkeypatch.setattr(broadcast, "audit_broadcast_rounds", fail_audit)
    state = {
        "valorant_profile": "broadcast",
        "runtime_state": {},
        "current_dur": 100.0,
        "finalize": False,
        "realtime_fast_mode": True,
    }

    result = ValorantAnalyzerPlugin().scan_window(
        str(video),
        ScanWindow(0.0, 90.0, timeout_sec=30.0, use_ocr=True),
        state,
    )

    assert result == []
    assert audit_called is False
    pending = state["runtime_state"]["broadcast_pending_rounds"]
    assert pending and pending[0]["broadcast_audit"] == "pending_lookahead"
    assert pending[0]["broadcast_review_required"] is True


def test_pending_broadcast_round_is_reaudited_without_new_ocr_round(monkeypatch, tmp_path) -> None:
    import lsc.analyzer.valorant_broadcast as broadcast_module
    import lsc.analyzer.valorant_frame_classifier as classifier_module
    import lsc.analyzer.valorant_ocr_rounds as ocr_module
    from lsc.analyzer.base import ScanWindow
    from lsc.analyzer.valorant_plugin import ValorantAnalyzerPlugin

    video_path = tmp_path / "recording.mp4"
    video_path.write_bytes(b"test")
    ocr_results = [[{"start": 10.0, "end": 20.0}], []]

    def fake_detect(*args, **kwargs):
        return ocr_results.pop(0)

    class FakeClassifier:
        thresholds = {"stable_prob": 0.55}
        model_version = "test"
        provider = "cpu"

        def __init__(self, *args, **kwargs) -> None:
            pass

        def load(self) -> None:
            return None

    def fake_audit(rounds, video_path, **kwargs):
        audit = "pending_lookahead" if kwargs["available_end"] < 50.0 else "passed"
        return [{**round_data, "broadcast_audit": audit} for round_data in rounds]

    monkeypatch.setattr(ocr_module, "detect_valorant_rounds_ocr", fake_detect)
    monkeypatch.setattr(classifier_module, "ValorantFrameClassifier", FakeClassifier)
    monkeypatch.setattr(broadcast_module, "audit_broadcast_rounds", fake_audit)

    state = {
        "valorant_profile": "broadcast",
        "runtime_state": {},
        "current_dur": 20.0,
        "finalize": False,
        "ffmpeg_path": "ffmpeg",
    }
    plugin = ValorantAnalyzerPlugin()
    first = plugin.scan_window(
        str(video_path),
        ScanWindow(start_sec=0.0, end_sec=20.0, timeout_sec=120.0, use_ocr=True),
        state,
    )
    assert first == []
    assert state["runtime_state"]["broadcast_pending_rounds"]

    state["current_dur"] = 100.0
    second = plugin.scan_window(
        str(video_path),
        ScanWindow(start_sec=20.0, end_sec=40.0, timeout_sec=120.0, use_ocr=True),
        state,
    )
    assert second and second[0]["broadcast_audit"] == "passed"
    assert state["runtime_state"]["broadcast_pending_rounds"] == []


def _cancel_audit_plugin(monkeypatch, tmp_path, exc_factory, *, emit_windows: int = 1):
    """搭一个「OCR 出候选 → 审计抛异常」的 scan_window 现场。

    ``emit_windows``：OCR 只在最初这么多轮窗口里产出该回合（真实语义——同一回合
    不会在每个增量窗口里反复出现；后续窗口只扫新媒体）。
    """
    import lsc.analyzer.valorant_frame_classifier as classifier_module

    video_path = tmp_path / "recording.mp4"
    video_path.write_bytes(b"test")
    emissions = {"left": emit_windows}

    class FakeClassifier:
        thresholds = {"stable_prob": 0.55}
        model_version = "test"
        provider = "cpu"

        def __init__(self, *args, **kwargs) -> None:
            pass

        def load(self) -> None:
            return None

    def fake_detect(*_args, **_kwargs):
        if emissions["left"] <= 0:
            return []
        emissions["left"] -= 1
        return [
            {
                "start": 969.6,
                "end": 1078.6,
                "round_key": "round-000097",
                "end_by": "next_combat",
            }
        ]

    monkeypatch.setattr(ocr_rounds, "detect_valorant_rounds_ocr", fake_detect)
    monkeypatch.setattr(classifier_module, "ValorantFrameClassifier", FakeClassifier)
    monkeypatch.setattr(
        broadcast,
        "audit_broadcast_rounds",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(exc_factory()),
    )
    state = {
        "valorant_profile": "broadcast",
        "runtime_state": {},
        "current_dur": 1381.8,
        "finalize": False,
        "ffmpeg_path": "ffmpeg",
    }
    return ValorantAnalyzerPlugin(), state, video_path


def test_cancelled_broadcast_audit_requeues_candidate(monkeypatch, tmp_path) -> None:
    """2026-09-14 现场回归：审计被「取消」不得把候选静默丢出待审队列。

    现场：10:34:43 收尾尾部扫描 360s 超时 → 正在跑的 broadcast 审计被取消 →
    插件异常分支把整批盖成 ``broadcast_audit="skipped"``，而回写队列的条件只认
    ``pending_lookahead`` ⇒ 候选既无终态、又不在队列（``pending_queue_depth=0``），
    收尾补扫 5 轮全空转，round-000097 落 manual_review、导出侧报 NEVER_AUDITED。

    取消 ≠ 结构性无解：文件还在，下一轮理应接着审。故必须重新排队。
    """
    from lsc.utils.cancellable_ffmpeg import FFmpegCancelled

    plugin, state, video_path = _cancel_audit_plugin(
        monkeypatch, tmp_path, lambda: FFmpegCancelled("cancelled during broadcast audit")
    )
    plugin.scan_window(
        str(video_path),
        ScanWindow(start_sec=1315.6, end_sec=1381.8, timeout_sec=360.0, use_ocr=True),
        state,
    )

    pending = state["runtime_state"]["broadcast_pending_rounds"]
    assert pending, "被取消的候选必须留在待审队列里续审"
    assert pending[0]["round_key"] == "round-000097"
    # 重新排队不等于"已审"：列表侧仍须显示未审计
    assert pending[0]["broadcast_audit"] == "skipped"
    assert pending[0]["broadcast_review_required"] is True


def test_cancelled_audit_requeue_is_bounded(monkeypatch, tmp_path) -> None:
    """有界重试：真·无解不能无限占用审计槽位（超过上限即不再排队）。"""
    from lsc.analyzer.valorant_plugin import _BROADCAST_AUDIT_CANCEL_RETRY_MAX
    from lsc.utils.cancellable_ffmpeg import FFmpegCancelled

    plugin, state, video_path = _cancel_audit_plugin(
        monkeypatch, tmp_path, lambda: FFmpegCancelled("cancelled")
    )
    window = ScanWindow(start_sec=1315.6, end_sec=1381.8, timeout_sec=360.0, use_ocr=True)
    for _ in range(_BROADCAST_AUDIT_CANCEL_RETRY_MAX + 2):
        plugin.scan_window(str(video_path), window, state)

    pending = state["runtime_state"]["broadcast_pending_rounds"]
    assert pending == [], "超过重试上限后不得再排队"


def test_unavailable_broadcast_audit_does_not_requeue(monkeypatch, tmp_path) -> None:
    """审计「不可用」（非取消，如模型缺失）不重排：重试无意义，避免空转。"""
    plugin, state, video_path = _cancel_audit_plugin(
        monkeypatch, tmp_path, lambda: RuntimeError("model unavailable")
    )
    plugin.scan_window(
        str(video_path),
        ScanWindow(start_sec=1315.6, end_sec=1381.8, timeout_sec=360.0, use_ocr=True),
        state,
    )
    assert state["runtime_state"]["broadcast_pending_rounds"] == []



def test_frozen_combat_timer_is_pause_boundary() -> None:
    timer_samples = [
        (float(i), 90.0 - i, "combat") for i in range(10)
    ] + [
        (10.0, 80.0, "combat"),
        (10.5, 80.0, "combat"),
        (11.0, 80.0, "combat"),
        (11.5, 80.0, "combat"),
        (12.0, 80.0, "combat"),
        (12.5, 80.0, "combat"),
        (13.0, 80.0, "combat"),
    ]
    samples = [(ts, "combat", 0.95) for ts, _, _ in timer_samples]

    cutoff, reason = audit_broadcast_phase_sequence(
        samples, timer_samples, start=0.0, end=10.0,
    )

    assert cutoff == 9.75
    assert reason == "broadcast_pause"


def test_frozen_combat_frames_are_pause_boundary_without_timer() -> None:
    samples = [(float(i), "combat", 0.95) for i in range(16)]
    freeze_samples = [
        (float(i), "combat", 10.0 if i <= 10 else 0.0)
        for i in range(16)
    ]

    cutoff, reason = audit_broadcast_phase_sequence(
        samples,
        freeze_samples=freeze_samples,
        start=0.0,
        end=20.0,
    )

    assert cutoff == 9.75
    assert reason == "broadcast_pause"


def test_auto_profile_routes_match_title_to_broadcast() -> None:
    assert resolve_valorant_profile(
        "auto",
        streamer_name="月子（无畏契约解说）",
        stream_title="太平洋+进化者联赛",
    ) == "broadcast"


def test_auto_profile_keeps_ordinary_live_on_pov() -> None:
    assert resolve_valorant_profile(
        "auto",
        streamer_name="普通主播",
        stream_title="排位上分",
    ) == "pov"


def test_inspect_valorant_profile_diagnostics() -> None:
    """B-03: inspect_valorant_profile 返回策略判定原因与冲突警告。"""
    from lsc.analyzer.valorant_profile import inspect_valorant_profile

    # auto 命中官方赛事关键词 -> broadcast, title_hint, no mismatch warning
    dec1 = inspect_valorant_profile("auto", streamer_name="解说", stream_title="VCT 太平洋联赛")
    assert dec1.resolved_profile == "broadcast"
    assert dec1.profile_reason == "title_hint"
    assert dec1.profile_mismatch_warning is False

    # auto 普通个人直播 -> pov, fallback_pov, no mismatch warning
    dec2 = inspect_valorant_profile("auto", streamer_name="普通主播", stream_title="排位上分")
    assert dec2.resolved_profile == "pov"
    assert dec2.profile_reason == "fallback_pov"
    assert dec2.profile_mismatch_warning is False

    # 显式 broadcast 但直播间无赛事特征 -> broadcast, explicit, mismatch warning=True
    dec3 = inspect_valorant_profile("broadcast", streamer_name="普通主播", stream_title="排位上分")
    assert dec3.resolved_profile == "broadcast"
    assert dec3.profile_reason == "explicit"
    assert dec3.profile_mismatch_warning is True
    assert "可能导致分析滞后" in dec3.warning_message


def test_decide_backlog_policy_tiers() -> None:
    """A-02: 测试 backlog 分层控制器阶梯。"""
    from lsc.analyzer.valorant_plugin import decide_backlog_policy

    # <=30s: realtime, quota=2, 中央哨兵 4s
    m1, p1 = decide_backlog_policy(15.0, 1.5)
    assert m1 == "realtime"
    assert p1["audit_quota"] == 2
    assert p1["ocr_sample_interval"] == 1.0
    assert p1["center_sentinel_sec"] == 4.0

    # 30-60s: catchup, quota=1
    m2, p2 = decide_backlog_policy(45.0, 1.5)
    assert m2 == "catchup"
    assert p2["audit_quota"] == 1
    assert p2["ocr_sample_interval"] == 1.0
    assert p2["center_sentinel_sec"] == 4.0

    # 60-180s: priority-catchup, quota=1
    m3, p3 = decide_backlog_policy(120.0, 1.5)
    assert m3 == "priority-catchup"
    assert p3["audit_quota"] == 1
    assert p3["ocr_sample_interval"] == 1.0
    assert p3["center_sentinel_sec"] == 6.0

    # >180s: degraded-catchup, quota=1；顶部恒 1fps（ocr_sample_interval 不降频），
    # 降本只放大中央哨兵间隔
    m4, p4 = decide_backlog_policy(240.0, 0.8)
    assert m4 == "degraded-catchup"
    assert p4["audit_quota"] == 1
    assert p4["ocr_sample_interval"] == 1.0
    assert p4["center_sentinel_sec"] == 8.0

    # 官方解说 / broadcast：即使落后 >180s 也保持高质量采样与审计配额
    m5, p5 = decide_backlog_policy(240.0, 0.8, source_profile="broadcast")
    assert m5 == "degraded-catchup"
    assert p5["audit_quota"] == 2
    assert p5["center_sentinel_sec"] == 4.0


def test_scan_window_decouples_and_throttles_audit_quota(monkeypatch, tmp_path) -> None:
    """A-03: 粗扫与审计解耦，单次 kick 最多消费 quota 个候选，其余保留在 pending 队列。"""
    import lsc.analyzer.valorant_broadcast as broadcast_module
    import lsc.analyzer.valorant_frame_classifier as classifier_module
    import lsc.analyzer.valorant_ocr_rounds as ocr_module
    from lsc.analyzer.base import ScanWindow
    from lsc.analyzer.valorant_plugin import ValorantAnalyzerPlugin

    video_path = tmp_path / "recording.mp4"
    video_path.write_bytes(b"test")
    cands = [
        {"start": 10.0, "end": 20.0, "end_by": "next_prep"},
        {"start": 30.0, "end": 40.0, "end_by": "next_prep"},
        {"start": 50.0, "end": 60.0, "end_by": "next_prep"},
    ]
    monkeypatch.setattr(ocr_module, "detect_valorant_rounds_ocr", lambda *a, **k: cands)

    class FakeClassifier:
        thresholds = {"stable_prob": 0.55}
        model_version = "test"
        provider = "cpu"

        def __init__(self, *args, **kwargs) -> None:
            pass

        def load(self): return None

    audited_count = 0
    def fake_audit(rounds, video_path, **kwargs):
        nonlocal audited_count
        audited_count += len(rounds)
        return [{**r, "broadcast_audit": "passed"} for r in rounds]

    monkeypatch.setattr(classifier_module, "ValorantFrameClassifier", FakeClassifier)
    monkeypatch.setattr(broadcast_module, "audit_broadcast_rounds", fake_audit)

    state = {
        "valorant_profile": "broadcast",
        "runtime_state": {},
        "current_dur": 120.0,
        "finalize": False,
        "ffmpeg_path": "ffmpeg",
    }
    plugin = ValorantAnalyzerPlugin()
    res = plugin.scan_window(
        str(video_path),
        ScanWindow(start_sec=0.0, end_sec=70.0, timeout_sec=120.0, use_ocr=True),
        state,
    )
    assert audited_count == 2
    assert len(res) == 2
    assert len(state["runtime_state"]["broadcast_pending_rounds"]) == 1



def test_cap_broadcast_audit_cache_evicts_completed_first() -> None:
    """缓存超限时优先淘汰已完成条目；未完成条目尽量保留跨窗口复用语义。"""
    from lsc.analyzer.valorant_plugin import (
        _BROADCAST_AUDIT_CACHE_MAX,
        _cap_broadcast_audit_cache,
    )

    cache: dict = {}
    for i in range(_BROADCAST_AUDIT_CACHE_MAX):
        cache[f"{i:.1f}"] = {"completed": True, "samples": []}
    # 再塞一条未完成条目，触发超限
    cache["999.0"] = {"samples": [("s", 1, 2)]}
    assert len(cache) == _BROADCAST_AUDIT_CACHE_MAX + 1

    _cap_broadcast_audit_cache(cache)

    assert len(cache) <= _BROADCAST_AUDIT_CACHE_MAX
    assert "999.0" in cache  # 未完成条目存活

    # 已完成条目不足时按插入序兜底淘汰，保证总量受限
    cache2: dict = {f"{i:.1f}": {"samples": []} for i in range(_BROADCAST_AUDIT_CACHE_MAX + 5)}
    _cap_broadcast_audit_cache(cache2)
    assert len(cache2) <= _BROADCAST_AUDIT_CACHE_MAX


def test_broadcast_audit_finalize_or_strong_ocr_reduces_lookahead(monkeypatch) -> None:
    """收尾阶段或具备 strong OCR 证据（如 result_ts）时，lookahead 降到 45s 以节约开销。"""
    calls = []

    class FakeClassifier:
        thresholds = {"stable_prob": 0.55}
        model_version = "test"
        provider = "cpu"

        def load(self) -> None:
            return None

        def predict_batch(self, images):
            return np.tile(
                np.array([[0.01, 0.01, 0.97, 0.005, 0.005]], dtype=np.float32),
                (len(images), 1),
            )

    def fake_extract(*args, **kwargs):
        calls.append((float(kwargs["start_sec"]), float(kwargs["end_sec"])))
        return [
            (
                float(ts),
                np.full((8, 8, 3), int(ts * 10) % 255, dtype=np.uint8),
            )
            for ts in np.arange(float(kwargs["start_sec"]), float(kwargs["end_sec"]) + 0.01, 1.0)
        ]

    monkeypatch.setattr(ocr_rounds, "extract_frames_cancellable", fake_extract)
    monkeypatch.setattr(ocr_rounds, "_read_top_anchors", lambda image: (None, None, None))

    # finalize=True: lookahead 应为 45s (scan_end = min(0 + 150, max(20, 20 + 45)) = 65s)
    calls.clear()
    res1 = audit_broadcast_rounds(
        [{"start": 0.0, "end": 20.0}],
        "unused.mp4",
        classifier=FakeClassifier(),
        finalize=True,
    )
    assert res1 and res1[0]["broadcast_audit_scan_end"] == 65.0
    # 入点门禁先扫候选开头 0-15s，确认起点是真实 combat 后再扫尾部到 65s
    assert calls[0] == (0.0, 15.0)
    assert any(math.isclose(end, 65.0) for _, end in calls)

    # 强证据 result_ts 存在时，即使 finalize=False，lookahead 也应收窄至 45s
    calls.clear()
    res2 = audit_broadcast_rounds(
        [{"start": 0.0, "end": 20.0, "result_ts": 19.5}],
        "unused.mp4",
        classifier=FakeClassifier(),
        available_end=100.0,
    )
    assert res2 and res2[0]["broadcast_audit_scan_end"] == 65.0


def test_broadcast_audit_fallback_full_scan_short_circuit(monkeypatch) -> None:
    """尾部无 combat 兜底全扫应有短路缓存，避免跨增量窗口或重试时重复全扫。"""
    extract_calls = []

    class NonCombatClassifier:
        thresholds = {"stable_prob": 0.55}
        model_version = "test"
        provider = "cpu"

        def load(self) -> None:
            return None

        def predict_batch(self, images):
            # 全是 unknown，没有 combat
            return np.tile(
                np.array([[0.2, 0.2, 0.2, 0.2, 0.2]], dtype=np.float32),
                (len(images), 1),
            )

    def fake_extract(*args, **kwargs):
        extract_calls.append((float(kwargs["start_sec"]), float(kwargs["end_sec"])))
        return [
            (float(ts), np.full((8, 8, 3), int(ts * 10) % 255, dtype=np.uint8))
            for ts in np.arange(float(kwargs["start_sec"]), float(kwargs["end_sec"]) + 0.01, 1.0)
        ]

    monkeypatch.setattr(ocr_rounds, "extract_frames_cancellable", fake_extract)
    monkeypatch.setattr(ocr_rounds, "_read_top_anchors", lambda image: (None, None, None))

    cache = {}
    candidate = {
        "start": 0.0, "end": 100.0,
        # 已具备精修入点，跳过在线入点门禁，聚焦验证尾部兜底全扫的缓存短路
        "start_delta": 0.2, "start_confidence": 0.95, "start_refined": 0.0,
    }
    audit_broadcast_rounds(
        [candidate],
        "unused.mp4",
        classifier=NonCombatClassifier(),
        available_end=150.0,
        audit_cache=cache,
    )
    first_calls_count = len(extract_calls)
    assert cache["0.0"].get("fallback_full_scanned") is True

    # 再次重试，兜底全扫应该被短路跳过
    audit_broadcast_rounds(
        [candidate],
        "unused.mp4",
        classifier=NonCombatClassifier(),
        available_end=150.0,
        audit_cache=cache,
    )
    # 因为 fallback_full_scanned 已为 True，第二次不应该再次触发全区间 extract_frames_cancellable
    assert len(extract_calls) == first_calls_count


def test_phase1_broadcast_pending_round_is_listable_but_not_auto_exportable() -> None:
    """阶段一（Task 1.1）核心门禁：broadcast 粗筛 pending 候选允许入列前端展示，
    但严禁直接自动导出，必须等待后台审计通过为 vision_confirmed。"""
    from handlers.room_handler import (
        _is_auto_exportable_valorant_round,
        _is_listable_ocr_round,
    )

    candidate = {
        "start": 10.0,
        "end": 60.0,
        "phase": "combat",
        "boundary_source": "valorant_ocr_v1",
        "source_profile": "broadcast",
        "start_by": "ocr_combat",
        "end_by": "next_combat",
        "confirm_status": "pending",
        "broadcast_audit": "pending_lookahead",
        "broadcast_review_required": True,
    }

    # 允许入列展示给用户
    assert _is_listable_ocr_round(candidate) is True
    # 严禁自动导出
    assert _is_auto_exportable_valorant_round(candidate) is False


def test_phase1_scan_window_deferred_audit_immediately_returns_candidates(monkeypatch, tmp_path) -> None:
    """阶段一（Task 1.3）核心解耦：粗扫在 deferred_audit 模式下即刻返回候选，
    消除前端盲等空白，同时异步进入 pending 队列等待后台 worker 定稿。"""
    import lsc.analyzer.valorant_ocr_rounds as ocr_module
    from lsc.analyzer.base import ScanWindow
    from lsc.analyzer.valorant_plugin import ValorantAnalyzerPlugin

    video_path = tmp_path / "recording.mp4"
    video_path.write_bytes(b"test")
    cands = [{"start": 15.0, "end": 45.0, "start_by": "ocr_combat", "end_by": "next_combat"}]
    monkeypatch.setattr(ocr_module, "detect_valorant_rounds_ocr", lambda *a, **k: cands)

    state = {
        "valorant_profile": "broadcast",
        "runtime_state": {},
        "current_dur": 50.0,
        "finalize": False,
        "deferred_audit": True,
        "ffmpeg_path": "ffmpeg",
    }
    plugin = ValorantAnalyzerPlugin()
    results = plugin.scan_window(
        str(video_path),
        ScanWindow(start_sec=0.0, end_sec=50.0, timeout_sec=60.0, use_ocr=True),
        state,
    )

    # 阶段一优化：即刻返回粗筛结果，不再返回空列表
    assert len(results) == 1
    assert results[0]["start"] == 15.0
    assert results[0]["confirm_status"] == "pending"
    assert results[0]["broadcast_audit"] == "pending_lookahead"
    # 同时在 runtime_state 登记，供后台 worker 消费
    assert len(state["runtime_state"]["broadcast_pending_rounds"]) == 1


def test_phase1_audit_broadcast_rounds_custom_lookahead_sec(monkeypatch) -> None:
    """阶段一（Task 1.2）动态 Lookahead：支持由运行时调度传入更短的 lookahead_sec（如 45s），
    显著压缩现实等待时长。"""
    calls = []

    class FakeClassifier:
        thresholds = {"stable_prob": 0.55}
        model_version = "test"
        provider = "cpu"
        def load(self): return None
        def predict_batch(self, images):
            return np.tile(np.array([[0.01, 0.01, 0.97, 0.005, 0.005]], dtype=np.float32), (len(images), 1))

    def fake_extract(*args, **kwargs):
        calls.append((float(kwargs["start_sec"]), float(kwargs["end_sec"])))
        return [
            (float(ts), np.full((8, 8, 3), int(ts * 10) % 255, dtype=np.uint8))
            for ts in np.arange(float(kwargs["start_sec"]), float(kwargs["end_sec"]) + 0.01, 1.0)
        ]

    monkeypatch.setattr(ocr_rounds, "extract_frames_cancellable", fake_extract)
    monkeypatch.setattr(ocr_rounds, "_read_top_anchors", lambda image: (None, None, None))

    # 传入 lookahead_sec=30.0，scan_end 应为 min(10 + 150, 40 + 30) = 70.0s
    res = audit_broadcast_rounds(
        [{"start": 10.0, "end": 40.0}],
        "unused.mp4",
        classifier=FakeClassifier(),
        available_end=100.0,
        lookahead_sec=30.0,
    )
    assert res and res[0]["broadcast_audit_scan_end"] == 70.0
    # 在线入点门禁先扫头部 [10, 25]，再增量扫尾到 70.0s
    assert calls[0] == (10.0, 25.0)
    assert any(math.isclose(end, 70.0) for _, end in calls)


def test_phase2_observer_score_delta_alone_confirms_round() -> None:
    """阶段二（Task 2.4）：当模型在后视窗未发现明显回放，但存在 Observer 比分跳变点时，
    比分跳变时间确凿作为截断出点，理由为 broadcast_observer_score_delta。"""
    # 模拟全是 combat，模型未给出排除段
    samples = [(float(i), "combat", 0.95) for i in range(30)]

    cutoff, reason = audit_broadcast_phase_sequence(
        samples,
        start=0.0,
        end=20.0,
        score_cutoff=18.5,
    )

    assert cutoff == 18.25  # 18.5 - 0.25 容差
    assert reason == "broadcast_observer_score_delta"


def test_phase2_observer_score_with_earlier_replay_prefers_replay_cut() -> None:
    """阶段二（Task 2.4）：当比分跳变前 1s 导播已切入 Replay 慢动作时，
    优先采用较早的 Replay 截断点，切除慢动作回放杂质。"""
    samples = [
        (float(i), "combat", 0.95) for i in range(16)
    ] + [
        (16.0, "replay", 0.96),
        (16.5, "replay", 0.96),
        (17.0, "replay", 0.96),
    ]

    # 比分跳变记录在 17.5s，但 16.0s 已进入 Replay
    cutoff, reason = audit_broadcast_phase_sequence(
        samples,
        start=0.0,
        end=20.0,
        score_cutoff=17.5,
    )

    # 应在 Replay 起点前 15.75s 截断，切除 Replay
    assert cutoff == 15.75
    assert reason == "broadcast_replay_or_non_game"


def test_phase2_audit_broadcast_rounds_with_score_cutoff(monkeypatch) -> None:
    """阶段二（Task 2.4）：端到端验证候选携带 score_end_ts 时顺利定稿为 vision_confirmed。"""
    class FakeClassifier:
        thresholds = {"stable_prob": 0.55}
        model_version = "test"
        provider = "cpu"
        def load(self): return None
        def predict_batch(self, images):
            # 模型全输出 combat，无截断
            return np.tile(np.array([[0.01, 0.01, 0.97, 0.005, 0.005]], dtype=np.float32), (len(images), 1))

    def fake_extract(*args, **kwargs):
        start = float(kwargs["start_sec"])
        end = float(kwargs["end_sec"])
        return [
            (float(ts), np.full((8, 8, 3), int(ts * 10) % 255, dtype=np.uint8))
            for ts in np.arange(start, end + 0.01, 1.0)
        ]

    monkeypatch.setattr(ocr_rounds, "extract_frames_cancellable", fake_extract)
    monkeypatch.setattr(ocr_rounds, "_read_top_anchors", lambda image: (None, None, None))

    candidate = {
        "start": 0.0,
        "end": 20.0,
        "start_by": "ocr_combat",
        "end_by": "next_combat",
        "score_end_ts": 19.0,
        "start_delta": 0.2,
        "start_confidence": 0.95,
    }

    res = audit_broadcast_rounds(
        [candidate],
        "unused.mp4",
        classifier=FakeClassifier(),
        available_end=100.0,
    )

    assert len(res) == 1
    assert res[0]["broadcast_audit"] == "passed"
    assert res[0]["confirm_status"] == "vision_confirmed"
    assert res[0]["end_by"] == "broadcast_exclusion"
    assert res[0]["broadcast_excluded_reason"] == "broadcast_observer_score_delta"
    assert res[0]["end"] == 18.75


def test_hard_veto_decreasing_combat_after_cutoff() -> None:
    """截断点之后交战计时器仍连续递减时，不得定稿 broadcast_exclusion。"""
    assert broadcast._has_decreasing_combat_after(
        10.0,
        [
            (9.0, 90.0, "combat"),
            (10.5, 80.0, "combat"),
            (11.5, 70.0, "combat"),
            (12.5, "replay", "replay"),
        ],
    ) is True
    # 截断后是 replay/非 combat：没有持续递减的交战计时器，允许正常截断。
    assert broadcast._has_decreasing_combat_after(
        10.0,
        [
            (9.0, 90.0, "combat"),
            (10.5, 90.0, "combat"),
            (11.5, 90.0, "combat"),
            (12.5, None, "replay"),
        ],
    ) is False


def test_start_gate_rejects_replay_headed_ordinary_candidate_on_finalize(monkeypatch) -> None:
    """收尾审计时，普通候选前 3s 是 replay 画面、随后才进入 combat，应直接拒绝。
    回放开头属于上一回合尾段；只有 split_from_oversize 固定块才允许块内后移。"""
    _LABELS = ("non_game", "buy", "combat", "result", "replay")

    class FakeClassifier:
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

    def label_at(ts: float) -> str:
        return "replay" if ts < 3.0 else "combat"

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

    outcomes = broadcast.audit_broadcast_rounds_with_outcomes(
        [{"start": 0.0, "end": 20.0}],
        "unused.mp4",
        classifier=FakeClassifier(),
        finalize=True,
    )

    assert len(outcomes) == 1
    assert outcomes[0].status == "rejected"
    assert outcomes[0].reason == "no_stable_combat_start"
    assert outcomes[0].candidate["broadcast_start_gate"] == "no_stable_combat"


def test_start_gate_rejects_candidate_whose_whole_head_is_non_combat(monkeypatch) -> None:
    """候选开头没有稳定 combat 时（例如整段都是 replay/non_game），收尾应拒绝。"""
    _LABELS = ("non_game", "buy", "combat", "result", "replay")

    class FakeClassifier:
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

    def label_at(ts: float) -> str:
        return "replay" if ts < 15.0 else "combat"

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

    cache = {}
    outcomes = broadcast.audit_broadcast_rounds_with_outcomes(
        [{"start": 0.0, "end": 20.0}],
        "unused.mp4",
        classifier=FakeClassifier(),
        finalize=True,
        audit_cache=cache,
    )

    assert len(outcomes) == 1
    assert outcomes[0].status == "rejected"
    assert outcomes[0].reason == "no_stable_combat_start"
    assert outcomes[0].candidate["broadcast_start_gate"] == "no_stable_combat"

    # 拒绝结论跨缓存重试保持，防止尾部样本的后续调用把同一伪回合复活
    outcomes2 = broadcast.audit_broadcast_rounds_with_outcomes(
        [{"start": 0.0, "end": 20.0}],
        "unused.mp4",
        classifier=FakeClassifier(),
        finalize=True,
        audit_cache=cache,
    )
    assert len(outcomes2) == 1
    assert outcomes2[0].status == "rejected"
    assert outcomes2[0].reason == "no_stable_combat_start"


def test_start_gate_split_chunk_must_find_real_combat_onset(monkeypatch) -> None:
    """split_from_oversize 固定块头不能直接当入点：前段 replay 后必须后移，
    找不到任何真实 combat 锚点时整块拒绝。"""
    _LABELS = ("non_game", "buy", "combat", "result", "replay")

    class FakeClassifier:
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

    def fake_extract_with(combat_start: float):
        def fake_extract(*args, **kwargs):
            start = float(kwargs["start_sec"])
            end = float(kwargs["end_sec"])
            return [
                (
                    float(ts),
                    np.full(
                        (8, 8, 3),
                        _LABELS.index(
                            "combat" if float(ts) >= combat_start else "replay"
                        ),
                        dtype=np.uint8,
                    ),
                )
                for ts in np.arange(start, end + 0.01, 1.0)
            ]

        return fake_extract

    monkeypatch.setattr(ocr_rounds, "_read_top_anchors", lambda image: (None, None, None))

    # 块内存在真实 combat：把入点从固定块头 150s 后移到 154s
    monkeypatch.setattr(
        ocr_rounds,
        "extract_frames_cancellable",
        fake_extract_with(combat_start=154.0),
    )
    moved = audit_broadcast_rounds(
        [{"start": 150.0, "end": 300.0, "split_from_oversize": True}],
        "unused.mp4",
        classifier=FakeClassifier(),
        finalize=True,
    )
    assert len(moved) == 1
    assert moved[0]["start"] == 154.0
    assert moved[0]["broadcast_start_gate"] == "moved_from_non_combat"
    assert moved[0]["start_delta"] is None

    # 块头本身就是真实 combat（固定边界恰逢回合起点）时允许通过
    monkeypatch.setattr(
        ocr_rounds,
        "extract_frames_cancellable",
        fake_extract_with(combat_start=150.0),
    )
    direct = audit_broadcast_rounds(
        [{"start": 150.0, "end": 300.0, "split_from_oversize": True}],
        "unused.mp4",
        classifier=FakeClassifier(),
        finalize=True,
    )
    assert len(direct) == 1
    assert direct[0]["start"] == 150.0
    assert direct[0]["broadcast_start_gate"] == "ok"

    # 块内找不到任何真实 combat：整块拒绝，不产出固定切块伪回合
    monkeypatch.setattr(
        ocr_rounds,
        "extract_frames_cancellable",
        fake_extract_with(combat_start=9999.0),
    )
    outcomes = broadcast.audit_broadcast_rounds_with_outcomes(
        [{"start": 150.0, "end": 300.0, "split_from_oversize": True}],
        "unused.mp4",
        classifier=FakeClassifier(),
        finalize=True,
    )
    assert len(outcomes) == 1
    assert outcomes[0].status == "rejected"
    assert outcomes[0].reason == "no_stable_combat_start"


def test_online_start_gate_rejects_replay_headed_ordinary_candidate(monkeypatch) -> None:
    """Step 1+：在线审计也必须执行入点门禁；普通候选回放开头直接拒绝（删除），
    不再后移成“下一条真回合”，避免强停/收尾产出回放入点切片。"""
    _LABELS = ("non_game", "buy", "combat", "result", "replay")

    class FakeClassifier:
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

    def fake_extract(*args, **kwargs):
        start = float(kwargs["start_sec"])
        end = float(kwargs["end_sec"])
        frames = []
        for ts in np.arange(start, end + 0.01, 1.0):
            label = "replay" if float(ts) < 3.0 else "combat"
            img = np.full((8, 8, 3), int(float(ts) * 10) % 255, dtype=np.uint8)
            img[..., 0] = _LABELS.index(label)
            frames.append((float(ts), img))
        return frames

    monkeypatch.setattr(ocr_rounds, "extract_frames_cancellable", fake_extract)
    monkeypatch.setattr(ocr_rounds, "_read_top_anchors", lambda image: (None, None, None))

    outcomes = broadcast.audit_broadcast_rounds_with_outcomes(
        [{"start": 0.0, "end": 20.0}],
        "unused.mp4",
        classifier=FakeClassifier(),
        available_end=100.0,
    )

    assert len(outcomes) == 1
    assert outcomes[0].status == "rejected"
    assert outcomes[0].reason == "no_stable_combat_start"
    assert outcomes[0].candidate["broadcast_start_gate"] == "no_stable_combat"


def test_online_start_gate_split_chunk_scans_past_standard_prefix(monkeypatch) -> None:
    """超长候选子块在线审计不能只看普通 15s 头部。

    真实强停验收曾出现固定块头落在回放、20s 后才进入稳定交战的场景；
    split 子块应扫描整块并把入点后移，不能误判为无 combat 后直接删除。
    """
    labels = ("non_game", "buy", "combat", "result", "replay")
    extract_ranges: list[tuple[float, float]] = []

    class FakeClassifier:
        thresholds = {"stable_prob": 0.55}
        model_version = "test"
        provider = "cpu"

        def load(self) -> None:
            return None

        def predict_batch(self, images):
            rows = []
            for image in images:
                label = labels[int(image[0, 0, 0])]
                row = np.full(len(labels), 0.01, dtype=np.float32)
                row[labels.index(label)] = 0.97
                rows.append(row)
            return np.array(rows, dtype=np.float32)

    def fake_extract(*args, **kwargs):
        start = float(kwargs["start_sec"])
        end = float(kwargs["end_sec"])
        extract_ranges.append((start, end))
        frames = []
        for ts in np.arange(start, end + 0.01, 1.0):
            label = "replay" if float(ts) < 20.0 else "combat"
            img = np.full((8, 8, 3), int(float(ts) * 10) % 255, dtype=np.uint8)
            img[..., 0] = labels.index(label)
            frames.append((float(ts), img))
        return frames

    monkeypatch.setattr(ocr_rounds, "extract_frames_cancellable", fake_extract)
    monkeypatch.setattr(ocr_rounds, "_read_top_anchors", lambda image: (None, None, None))

    result = audit_broadcast_rounds(
        [{
            "start": 0.0,
            "end": 40.0,
            "end_by": "next_prep",
            "split_from_oversize": True,
        }],
        "unused.mp4",
        classifier=FakeClassifier(),
        available_end=100.0,
    )

    assert len(result) == 1
    assert result[0]["start"] == 20.0
    assert result[0]["broadcast_start_gate"] == "moved_from_non_combat"
    assert any(
        end > broadcast.START_GATE_SCAN_LIMIT_SEC
        for _start, end in extract_ranges
    )


def test_online_start_gate_rejects_no_combat_head(monkeypatch) -> None:
    """Step 1：在线审计对“头部 15s 找不到稳定 combat”的候选直接拒绝（删除），
    前一回合保持不变，不再生成回放开头的新切片。"""
    _LABELS = ("non_game", "buy", "combat", "result", "replay")

    class FakeClassifier:
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

    def fake_extract(*args, **kwargs):
        start = float(kwargs["start_sec"])
        end = float(kwargs["end_sec"])
        frames = []
        for ts in np.arange(start, end + 0.01, 1.0):
            label = "replay" if float(ts) < 16.0 else "combat"
            img = np.full((8, 8, 3), int(float(ts) * 10) % 255, dtype=np.uint8)
            img[..., 0] = _LABELS.index(label)
            frames.append((float(ts), img))
        return frames

    monkeypatch.setattr(ocr_rounds, "extract_frames_cancellable", fake_extract)
    monkeypatch.setattr(ocr_rounds, "_read_top_anchors", lambda image: (None, None, None))

    outcomes = broadcast.audit_broadcast_rounds_with_outcomes(
        [{"start": 0.0, "end": 20.0}],
        "unused.mp4",
        classifier=FakeClassifier(),
        available_end=100.0,
    )

    assert len(outcomes) == 1
    assert outcomes[0].status == "rejected"
    assert outcomes[0].reason == "no_stable_combat_start"
    assert outcomes[0].candidate["broadcast_start_gate"] == "no_stable_combat"


def test_start_gate_rejects_replay_headed_long_ordinary_candidate_without_extension(monkeypatch) -> None:
    """回放占满普通 15s 门禁时，普通长候选也必须直接拒绝，不再扩展 35s 后移。

    这样既删除“回放开头伪回合”，又减少解说流多出的 20s 抽帧/推理，缓解滞后。
    """
    _LABELS = ("non_game", "buy", "combat", "result", "replay")
    extract_ranges: list[tuple[float, float]] = []

    class FakeClassifier:
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

    def fake_extract(*args, **kwargs):
        start = float(kwargs["start_sec"])
        end = float(kwargs["end_sec"])
        extract_ranges.append((start, end))
        frames = []
        for ts in np.arange(start, end + 0.01, 1.0):
            label = "replay" if float(ts) < 20.0 else "combat"
            img = np.full((8, 8, 3), int(float(ts) * 10) % 255, dtype=np.uint8)
            img[..., 0] = _LABELS.index(label)
            frames.append((float(ts), img))
        return frames

    monkeypatch.setattr(ocr_rounds, "extract_frames_cancellable", fake_extract)
    monkeypatch.setattr(ocr_rounds, "_read_top_anchors", lambda image: (None, None, None))

    outcomes = broadcast.audit_broadcast_rounds_with_outcomes(
        [{
            "start": 0.0,
            "end": 40.0,
            "end_by": "next_prep",
            "confirm_status": "vision_confirmed",
        }],
        "unused.mp4",
        classifier=FakeClassifier(),
        finalize=True,
    )

    assert len(outcomes) == 1
    assert outcomes[0].status == "rejected"
    assert outcomes[0].reason == "no_stable_combat_start"
    # 普通候选不做 15→35s 扩展：只扫描 0-15s 门禁窗后立即拒绝
    assert extract_ranges and all(end <= broadcast.START_GATE_SCAN_LIMIT_SEC + 1.0 for _, end in extract_ranges)
    assert not any(end > broadcast.START_GATE_SCAN_LIMIT_SEC for _, end in extract_ranges)


def test_audit_micro_steps_resume_from_cache_without_large_extract(monkeypatch) -> None:
    """后台审计按媒体时间分步，并能从 audit_cache 继续直到终态。"""
    extract_ranges: list[tuple[float, float, float]] = []

    class FakeClassifier:
        thresholds = {"stable_prob": 0.55}
        model_version = "test"
        provider = "cpu"

        def load(self) -> None:
            return None

        def predict_batch(self, images):
            rows = []
            for image in images:
                ts = float(image[0, 0, 0]) / 10.0
                row = [0.01] * 5
                row[2 if ts < 50.0 else 4] = 0.96
                rows.append(row)
            return np.asarray(rows, dtype=np.float32)

    def fake_extract(*args, **kwargs):
        start = float(kwargs["start_sec"])
        end = float(kwargs["end_sec"])
        fps = float(kwargs["fps"])
        extract_ranges.append((start, end, fps))
        return [
            (
                float(ts),
                np.full((8, 8, 3), float(ts) * 10.0, dtype=np.float32),
            )
            for ts in np.arange(start, end + 0.01, 1.0 / fps)
        ]

    monkeypatch.setattr(ocr_rounds, "extract_frames_cancellable", fake_extract)
    monkeypatch.setattr(ocr_rounds, "_read_top_anchors", lambda _image: (None, None, None))

    candidate = {
        "start": 0.0,
        "end": 20.0,
        "end_by": "next_prep",
        "confirm_status": "vision_confirmed",
        "start_delta": 0.0,
        "start_confidence": 0.95,
    }
    cache: dict = {}
    statuses: list[str] = []
    for _ in range(10):
        outcomes = broadcast.audit_broadcast_rounds_with_outcomes(
            [candidate],
            "unused.mp4",
            classifier=FakeClassifier(),
            available_end=120.0,
            audit_cache=cache,
            max_media_step_sec=18.0,
        )
        assert len(outcomes) == 1
        statuses.append(outcomes[0].status)
        candidate = outcomes[0].candidate
        if outcomes[0].status == "accepted":
            break

    assert statuses[0] == "pending"
    assert statuses[-1] == "accepted"
    assert len(statuses) > 1
    # Subsequent steps intentionally re-read one second at the seam for stable
    # phase runs, so physical extraction is at most budget + 1s overlap.
    assert all(end - start <= 19.001 for start, end, _fps in extract_ranges)


def test_audit_fallback_full_scan_respects_micro_step_budget(monkeypatch) -> None:
    """尾窗无 combat 时的头部回补也必须遵守媒体微步骤预算。

    旧逻辑只限制了尾窗，却一次性抽取 [start, tail_start]，
    真实官方解说候选会因此单次持锁 90s+。
    """
    extract_ranges: list[tuple[float, float, float]] = []

    class FakeClassifier:
        thresholds = {"stable_prob": 0.55}
        model_version = "test"
        provider = "cpu"

        def load(self) -> None:
            return None

        def predict_batch(self, images):
            rows = np.full((len(images), 5), 0.01, dtype=np.float32)
            rows[:, 4] = 0.96  # replay：尾窗不存在 combat，触发头部回补
            return rows

    def fake_extract(*args, **kwargs):
        start = float(kwargs["start_sec"])
        end = float(kwargs["end_sec"])
        fps = float(kwargs["fps"])
        extract_ranges.append((start, end, fps))
        return [
            (float(ts), np.zeros((8, 8, 3), dtype=np.uint8))
            for ts in np.arange(start, end + 0.01, 1.0 / fps)
        ]

    monkeypatch.setattr(ocr_rounds, "extract_frames_cancellable", fake_extract)
    monkeypatch.setattr(ocr_rounds, "_read_top_anchors", lambda _image: (None, None, None))

    outcomes = broadcast.audit_broadcast_rounds_with_outcomes(
        [{
            "start": 0.0,
            "end": 100.0,
            "end_by": "next_prep",
            "start_delta": 0.1,
            "start_confidence": 0.95,
        }],
        "unused.mp4",
        classifier=FakeClassifier(),
        available_end=200.0,
        audit_cache={},
        max_media_step_sec=18.0,
    )

    assert outcomes and outcomes[0].status == "pending"
    assert len(extract_ranges) == 2  # 有界尾窗 + 有界头部回补
    assert all(end - start <= 18.001 for start, end, _fps in extract_ranges)
    # 回补从尾窗向前搜索，而非从候选 0s 开始慢慢追。
    assert extract_ranges[1][1] == extract_ranges[0][0]


def test_batch_audit_prefetches_overlapping_candidates_once(monkeypatch) -> None:
    """重叠候选的 1fps gate/tail 范围应合并成一次 FFmpeg 抽帧。"""
    from lsc.analyzer.frame_provider import FrameProvider

    extract_ranges: list[tuple[float, float, float]] = []

    class FakeClassifier:
        thresholds = {"stable_prob": 0.55}
        model_version = "test"
        provider = "cpu"

        def load(self) -> None:
            return None

        def predict_batch(self, images):
            rows = np.full((len(images), 5), 0.01, dtype=np.float32)
            rows[:, 2] = 0.96
            return rows

    def fake_extract(*args, **kwargs):
        start = float(kwargs["start_sec"])
        end = float(kwargs["end_sec"])
        fps = float(kwargs["fps"])
        extract_ranges.append((start, end, fps))
        return [
            (
                float(ts),
                np.full((8, 8, 3), int(float(ts) * 10) % 255, dtype=np.uint8),
            )
            for ts in np.arange(start, end + 0.01, 1.0 / fps)
        ]

    monkeypatch.setattr(ocr_rounds, "extract_frames_cancellable", fake_extract)
    monkeypatch.setattr(ocr_rounds, "_read_top_anchors", lambda _image: (None, None, None))
    candidates = [
        {
            "start": 0.0, "end": 40.0, "end_by": "next_prep",
            "confirm_status": "vision_confirmed", "start_delta": 0.0,
            "start_confidence": 0.95,
        },
        {
            "start": 20.0, "end": 60.0, "end_by": "next_prep",
            "confirm_status": "vision_confirmed", "start_delta": 0.0,
            "start_confidence": 0.95,
        },
    ]

    result = broadcast.audit_broadcast_rounds(
        candidates,
        "unused.mp4",
        classifier=FakeClassifier(),
        available_end=120.0,
        frame_provider=FrameProvider(max_frames=500),
    )

    assert len(result) == 2
    one_fps_calls = [row for row in extract_ranges if row[2] == 1.0]
    assert one_fps_calls == [(0.0, 105.0, 1.0)]


def test_online_start_gate_defers_when_head_not_fully_written(monkeypatch) -> None:
    """Step 1 安全边界：在线阶段若头部 15s 尚未被当前录制覆盖，不能拿截断头部
    下结论，应跳过入点门禁并返回 pending_lookahead 等待重试，避免误拒。"""
    _LABELS = ("non_game", "buy", "combat", "result", "replay")

    class FakeClassifier:
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

    def fake_extract(*args, **kwargs):
        start = float(kwargs["start_sec"])
        end = float(kwargs["end_sec"])
        # 前 15s 全是 replay：若门禁误跑，会拒绝；此处头部未写满，必须等待
        frames = []
        for ts in np.arange(start, end + 0.01, 1.0):
            img = np.full((8, 8, 3), int(float(ts) * 10) % 255, dtype=np.uint8)
            img[..., 0] = _LABELS.index("replay")
            frames.append((float(ts), img))
        return frames

    monkeypatch.setattr(ocr_rounds, "extract_frames_cancellable", fake_extract)
    monkeypatch.setattr(ocr_rounds, "_read_top_anchors", lambda image: (None, None, None))

    cache: dict[str, object] = {}
    result = audit_broadcast_rounds(
        [{"start": 0.0, "end": 20.0}],
        "unused.mp4",
        classifier=FakeClassifier(),
        available_end=10.0,  # 只覆盖到 10s，头部 15s 未写满
        audit_cache=cache,
    )

    assert len(result) == 1
    assert result[0]["broadcast_audit"] == "pending_lookahead"
    assert result[0].get("broadcast_start_gate") is None
    assert "0.0" in cache
    assert cache["0.0"].get("start_gate_rejected") is not True


def test_online_start_gate_rejects_replay_headed_ordinary_and_persists_rejection(monkeypatch) -> None:
    """在线首轮判定普通回放开头候选应拒绝后，重试必须保持拒绝结论，不复活。"""
    _LABELS = ("non_game", "buy", "combat", "result", "replay")

    class FakeClassifier:
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

    def fake_extract(*args, **kwargs):
        start = float(kwargs["start_sec"])
        end = float(kwargs["end_sec"])
        frames = []
        for ts in np.arange(start, end + 0.01, 1.0):
            label = "replay" if float(ts) < 3.0 else "combat"
            img = np.full((8, 8, 3), int(float(ts) * 10) % 255, dtype=np.uint8)
            img[..., 0] = _LABELS.index(label)
            frames.append((float(ts), img))
        return frames

    monkeypatch.setattr(ocr_rounds, "extract_frames_cancellable", fake_extract)
    monkeypatch.setattr(ocr_rounds, "_read_top_anchors", lambda image: (None, None, None))

    cache: dict[str, object] = {}
    candidate = {"start": 0.0, "end": 20.0, "round_key": "round-stable"}
    first = broadcast.audit_broadcast_rounds_with_outcomes(
        [candidate],
        "unused.mp4",
        classifier=FakeClassifier(),
        available_end=30.0,  # 头部已覆盖，门禁可判定
        audit_cache=cache,
    )
    assert len(first) == 1
    assert first[0].status == "rejected"
    assert first[0].reason == "no_stable_combat_start"
    assert cache["round-stable"].get("start_gate_rejected") is True

    second = broadcast.audit_broadcast_rounds_with_outcomes(
        [dict(first[0].candidate)],
        "unused.mp4",
        classifier=FakeClassifier(),
        available_end=200.0,
        audit_cache=cache,
    )
    assert len(second) == 1
    # 重试必须保持拒绝结论，不允许复活
    assert second[0].status == "rejected"
    assert second[0].reason == "no_stable_combat_start"


# ── A4（2026-09-10）：start_confidence 由二值代理改为实测视觉一致性 ──────────


def test_start_visual_combat_ratio_measures_window() -> None:
    import lsc.analyzer.valorant_broadcast as mod

    samples = [
        (8.0, "combat", 0.9),     # 起点之前：**不计入**（前视窗口，见函数注释）
        (10.0, "combat", 0.9),    # 窗口下界 [10.0, 12.0]
        (11.9, "buy", 0.9),
        (12.1, "combat", 0.9),    # 超出窗口，不得参与
        (20.0, "non_game", 0.9),  # 超出窗口，不得参与
    ]
    # 前视窗口内 2 个样本、1 个 combat
    assert mod._start_visual_combat_ratio(samples, start=10.0) == 0.5


def test_start_visual_combat_ratio_ignores_pre_start_samples() -> None:
    """前视语义：起点之前的样本不参与（否则正常的"购买→交战"过渡会被误判低分）。

    实测依据：真实录像里健康入点的样本形如 `buy,unknown,combat,combat`，
    起点前本就该是购买阶段；对称窗口会把它算成 0.5 而导致批量降级。
    """
    import lsc.analyzer.valorant_broadcast as mod

    samples = [
        (9.0, "buy", 0.9),
        (10.0, "combat", 0.9),
        (11.0, "combat", 0.9),
    ]
    assert mod._start_visual_combat_ratio(samples, start=10.0) == 1.0


def test_start_visual_combat_ratio_returns_none_without_samples() -> None:
    """窗口内无样本必须返回 None（而不是 0.0），否则会把回合批量降级 coarse。"""
    import lsc.analyzer.valorant_broadcast as mod

    assert mod._start_visual_combat_ratio([], start=10.0) is None
    assert mod._start_visual_combat_ratio(None, start=10.0) is None
    assert mod._start_visual_combat_ratio([(100.0, "combat", 0.9)], start=10.0) is None


def test_apply_start_visual_confidence_overwrites_binary_proxy() -> None:
    import lsc.analyzer.valorant_broadcast as mod

    samples = [(20.0, "combat", 0.9), (20.9, "combat", 0.9), (21.8, "replay", 0.9)]
    item = {"start": 20.0, "start_confidence": 0.7}  # 旧二值代理
    ratio = mod._apply_start_visual_confidence(item, samples)
    assert ratio == 0.667 or ratio == 0.666 or ratio == 0.667
    assert item["start_confidence"] == ratio
    assert item["start_confidence_source"] == "visual_combat_ratio"


def test_apply_start_visual_confidence_keeps_fallback_without_samples() -> None:
    """验收要求：缺样本时不得写值，保留兜底 —— 广播回合不得因此批量降级 coarse。"""
    import lsc.analyzer.valorant_broadcast as mod

    item = {"start": 20.0, "start_confidence": 0.95}
    assert mod._apply_start_visual_confidence(item, []) is None
    assert item["start_confidence"] == 0.95
    assert "start_confidence_source" not in item


# ── A2（2026-09-10）：起点落在回放/非游戏帧上的显式标注 ──────────────────


def test_start_window_replay_evidence_detects_replay_at_start() -> None:
    import lsc.analyzer.valorant_broadcast as mod

    # 起点窗口内出现 replay（回放转场/水印）→ 命中
    assert mod._start_window_replay_evidence(
        [(10.0, "unknown", 0.9), (11.0, "replay", 0.9), (12.0, "combat", 0.9)], start=10.0
    )
    # non_game（非游戏画面）同样视为不可作为入点锚点
    assert mod._start_window_replay_evidence(
        [(10.0, "non_game", 0.9), (11.0, "combat", 0.9)], start=10.0
    )
    # 起点之前出现的回放不计入（前视窗口）
    assert not mod._start_window_replay_evidence(
        [(8.0, "replay", 0.9), (10.0, "combat", 0.9)], start=10.0
    )
    # 超出窗口不计入
    assert not mod._start_window_replay_evidence(
        [(10.0, "combat", 0.9), (13.5, "replay", 0.9)], start=10.0
    )
    # 纯交战 → 无回放证据
    assert not mod._start_window_replay_evidence(
        [(10.0, "combat", 0.9), (11.0, "combat", 0.9)], start=10.0
    )
    # 空/None 安全
    assert not mod._start_window_replay_evidence(None, start=10.0)
    assert not mod._start_window_replay_evidence([], start=10.0)


def test_replay_at_start_is_marked_without_changing_rejection_path() -> None:
    """A2 必须**纯附加**：拒绝链路与既有 reason 串不得改动（零消费方风险）。"""
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "lsc/analyzer/valorant_broadcast.py").read_text(
        encoding="utf-8"
    )
    block = src.split('if gate_reason == "no_stable_combat":', 1)[1].split("_record_audit_outcome", 1)[0]
    assert 'item["broadcast_start_gate_detail"] = "replay_at_start"' in block
    # 既有字段与 outcome reason 保持不变
    assert 'item["broadcast_start_gate"] = gate_reason' in block
    assert 'reason="no_stable_combat_start"' in src


def test_veto_ignores_decreasing_clock_of_next_round() -> None:
    """截断点之后出现「满钟」= 新回合：不得据此否决（回合化取证）。

    形态复刻 2026-09-11 现场 round-000135：真实出点 1423.2 之后是回放/非游戏，
    紧接着下一回合满钟并继续递减；旧逻辑（普通候选 fresh_clock_min=None）据此
    否决 → 出点退回 next_prep/coarse（导出被判「出点未定稿」）。
    """
    timer_samples = [
        (1352.0, 100.0, "combat"),
        (1400.0, 52.0, "combat"),
        (1420.0, 32.0, "combat"),
        (1424.0, None, "non_game"),  # 回放/非游戏
        (1450.0, 100.0, "combat"),  # 下一回合满钟
        (1460.0, 90.0, "combat"),
        (1470.0, 80.0, "combat"),
    ]
    # 旧语义：跨回合钟表递减 → 误否决（锁住"改之前是坏的"）
    assert broadcast._has_decreasing_combat_after(1423.2, timer_samples) is True
    # 回合化：识别到满钟=新回合 → 不否决
    assert (
        broadcast._has_decreasing_combat_after(
            1423.2,
            timer_samples,
            fresh_clock_min=broadcast.FRESH_ROUND_CLOCK_MIN,
        )
        is False
    )


def test_veto_fresh_clock_is_round_scoped_for_all_candidates() -> None:
    """源码守卫：硬否决的 fresh-clock 逃逸不得再按 split_from_oversize 分叉。

    现场教训：跨回合的钟表证据会误否决正确截断（135 的 1423.2；宽窗口下 045 的
    514.0 会被下一回合的钟表误否决）。若有人把条件改回「仅分裂块」，本用例必须红。
    """
    from pathlib import Path

    src = (
        Path(__file__).resolve().parents[1] / "lsc/analyzer/valorant_broadcast.py"
    ).read_text(encoding="utf-8")
    anchor = "and _has_decreasing_combat_after("
    window = src[src.index(anchor) : src.index(anchor) + 700]
    assert "fresh_clock_min=FRESH_ROUND_CLOCK_MIN," in window
    assert "split_from_oversize" not in window, (
        "硬否决的回合化逃逸不得再按 split_from_oversize 分叉（跨回合钟表会误否决正确截断）"
    )


def test_interior_round_boundary_detected_for_cross_round_candidate() -> None:
    """L1：区间内部含「回合结束后重新开战」= 跨回合候选。

    形态复刻 2026-09-11 现场 round-000123：`[1232, 1420.75]` 内含回合 A 的结束
    （1315→1349：result/non_game/replay + buy），随后回合 B 重开战（1350），
    出点却是回合 B 的结束 ⇒ 与 round-000135 认领同一条回合。
    """
    samples = [
        *[(float(t), "combat", 0.95) for t in range(0, 10)],
        (10.0, "result", 0.95),
        *[(float(t), "non_game", 0.95) for t in range(11, 15)],
        *[(float(t), "replay", 0.95) for t in range(15, 18)],
        *[(float(t), "buy", 0.95) for t in range(18, 20)],
        *[(float(t), "combat", 0.95) for t in range(20, 39)],
    ]
    assert broadcast._interior_round_boundary(samples, start=0.0, end=38.0) == 20.0


def test_interior_round_boundary_ignores_tail_exclusion_and_next_round() -> None:
    """L1 不得误伤：出点就是排除点、且下一回合在区间**之后**（round-000135 形态）。

    `[0, 33.5]` 内只有末尾的 result/non_game/replay（没有再次开战），
    区间之后的 combat（>= end）不参与判定 ⇒ 必须返回 None。
    """
    samples = [
        *[(float(t), "combat", 0.95) for t in range(0, 29)],
        (29.0, "result", 0.95),
        *[(float(t), "non_game", 0.95) for t in range(30, 33)],
        (33.0, "replay", 0.95),
        *[(float(t), "combat", 0.95) for t in range(34, 41)],  # 下一回合：在 end 之后
    ]
    assert broadcast._interior_round_boundary(samples, start=0.0, end=33.5) is None


def test_interior_boundary_verdict_policy() -> None:
    """裁决：小幅前缀裁剪 vs 大面积错位拒绝（避免"为凑切片而编造起点"）。"""
    # 现场 123：总长 188.75s、前缀 118s、剩余 70.75s ⇒ 拒绝
    assert broadcast._interior_boundary_verdict(
        start=1232.0, end=1420.75, resume=1350.0
    ) == ("reject", 118.0, 70.75)
    # 小前缀（15s ≤ INTERIOR_TRIM_MAX_SEC）⇒ 裁剪
    assert broadcast._interior_boundary_verdict(start=0.0, end=120.0, resume=15.0)[0] == "trim"
    # 前缀既超过 20s 又超过总时长一半 ⇒ 拒绝
    assert broadcast._interior_boundary_verdict(start=0.0, end=60.0, resume=40.0)[0] == "reject"
    # 裁剪后剩余不足 MIN_ACTIVE_SEC ⇒ 拒绝
    assert broadcast._interior_boundary_verdict(start=0.0, end=12.0, resume=5.0)[0] == "reject"


def test_interior_boundary_check_is_wired_into_audit_emit() -> None:
    """源码守卫：区间内边界自检必须挂在「盖章之后、写缓存之前」的产出路径上。"""
    from pathlib import Path

    src = (
        Path(__file__).resolve().parents[1] / "lsc/analyzer/valorant_broadcast.py"
    ).read_text(encoding="utf-8")
    anchor = "_stamp_broadcast_decision(item, clf, cutoff=cutoff, reason=reason)"
    window = src[src.index(anchor) : src.index(anchor) + 3000]
    assert "_interior_round_boundary(" in window
    assert "rejected_interior_boundary" in window


def test_interior_round_boundary_detects_when_window_starts_at_terminal_run() -> None:
    """尾部窗口回归：区间内可能**没有**前缀 combat（现场 123 的样本从回合 A 的终态游程开始）。

    首版实现要求「先见到 combat」，于是 1350 的重开战没被记上、L1 不触发
    （真实样本：SPAN=[1232,1420.8] 从 1316:result 起）。本用例锁住该形态。
    """
    samples = [
        *[(float(t), "result", 0.95) for t in range(0, 5)],
        *[(float(t), "non_game", 0.95) for t in range(5, 16)],
        *[(float(t), "replay", 0.95) for t in range(16, 28)],
        (28.0, "unknown", 0.95),
        *[(float(t), "buy", 0.95) for t in range(29, 34)],
        *[(float(t), "combat", 0.95) for t in range(34, 55)],
    ]
    assert broadcast._interior_round_boundary(samples, start=-84.0, end=104.5) == 34.0


def test_interior_boundary_trim_invalidates_old_start_evidence() -> None:
    """L1 裁剪必须作废原起点的密扫证据（否则下游会误判"起点证据强"）。"""
    item = {
        "start": 100.0,
        "end": 200.0,
        "start_delta": 0.4,
        "start_confidence": 0.95,
        "start_quality": "precise",
        "boundary_refined": True,
        "broadcast_review_required": False,
    }
    broadcast._apply_interior_boundary_trim(item, resume=150.0, trimmed=50.0)
    assert item["start"] == 150.0
    assert item["start_refined"] == 150.0
    assert item["start_by"] == "interior_boundary_trim"
    assert item["interior_boundary_trim_sec"] == 50.0
    assert item["start_quality"] == "coarse"
    assert item["start_review_required"] is True
    assert item["start_delta"] is None
    assert item["start_confidence"] == 0.70
    assert item["boundary_refined"] is False
    assert item["broadcast_review_required"] is True


def test_interior_boundary_rejection_is_not_cached_completed() -> None:
    """L1 拒绝路径不得写 cache completed：缓存命中分支会绕过自检重新盖章成 accepted。

    事故形态：拒绝后若标记 completed，下一轮审计命中缓存分支
    （`item.update(stamped_decision)` 或重新 `_stamp_broadcast_decision()` 后直接
    append 到 output）就会把"跨回合拒绝"悄悄翻成接受。既有拒绝路径
    （no_stable_combat_start / long_or_invalid）都不写 completed，保持一致。
    """
    from pathlib import Path

    src = (
        Path(__file__).resolve().parents[1] / "lsc/analyzer/valorant_broadcast.py"
    ).read_text(encoding="utf-8")
    index = src.index('item["broadcast_audit"] = "rejected_interior_boundary"')
    branch = src[index: src.index("continue", index)]
    assert 'cache_item["completed"]' not in branch, (
        "拒绝路径写 cache completed 会让下一轮缓存命中把拒绝翻成接受"
    )
    assert "interior_round_boundary" in branch


def test_start_gate_tolerates_one_sample_onset_jitter() -> None:
    """入点容差：候选起点那一帧的低置信抖动不得否决整条真实回合。

    2026-09-12 11:32 现场：start=230.187 首帧 unknown(0.3565)、其后 14 帧全 combat
    （视觉抽帧确认该回合真实存在：233s 计时钟 1:36、292s SPIKE PLANTED），旧实现要求
    onset 偏差 ≤1e-6s → rejected_no_stable_combat_start → 切片被删。同内容 start=230.200
    即通过。容差 2.5s 吸收抖动，同时不放行"先回放、后交战"的伪候选。
    """
    samples = [(230.187, "unknown", 0.3565)] + [
        (231.187 + i, "combat", 0.85) for i in range(14)
    ]
    assert broadcast._start_gate_decision(
        samples, start=230.187, split_from_oversize=False,
    ) == (230.187, None)

    # 纯 replay / 非游戏窗口：没有 combat 游程 → 仍拒绝（15% 案例属此类，不受影响）
    assert broadcast._start_gate_decision(
        [(100.0 + i, "replay", 1.0) for i in range(15)],
        start=100.0,
        split_from_oversize=False,
    ) == (None, "no_stable_combat")

    # 窗口内先回放、约 10s 后才交战：偏差远超容差 → 仍拒绝
    late_combat = [
        (100.0 + i, "replay", 1.0) for i in range(9)
    ] + [(109.0 + i, "combat", 0.9) for i in range(6)]
    assert broadcast._start_gate_decision(
        late_combat, start=100.0, split_from_oversize=False,
    ) == (None, "no_stable_combat")


def test_effective_lookahead_keeps_full_budget_when_ocr_end_vetoed() -> None:
    """视觉否决 OCR 出点后，即使收尾/离线也不得把后视窗口钉死在 +45s。

    否则 scan_end 恒 == end+45，而"否决后继续扩展"的判定条件恒成立 → 候选无限
    pending（实测 351.312 的假出点 403.125 连跑 8 轮停在 448.125，真出点 449.875
    在窗外）。给足 90s 后一次即定稿 broadcast_exclusion / precise。
    """
    resolve = broadcast._effective_lookahead_sec

    # 旧行为：收尾/离线一律 45s（无论是否被否决）
    assert resolve(
        finalize=True, available_end=None, has_strong_ocr_end=False,
        next_prep_invalidated=False,
    ) == 45.0
    # 新行为：被否决 → 给足 90s（收尾/离线同样适用）
    assert resolve(
        finalize=True, available_end=None, has_strong_ocr_end=False,
        next_prep_invalidated=True,
    ) == 90.0
    assert resolve(
        finalize=True, available_end=1000.0, has_strong_ocr_end=False,
        next_prep_invalidated=True,
    ) == 90.0
    assert resolve(
        finalize=False, available_end=None, has_strong_ocr_end=False,
        next_prep_invalidated=True,
    ) == 90.0
    # OCR 强证据出点仍走 45s 窄窗；显式 lookahead_sec 仍被尊重
    assert resolve(
        finalize=False, available_end=1000.0, has_strong_ocr_end=True,
        next_prep_invalidated=False,
    ) == 45.0
    assert resolve(
        finalize=False, available_end=1000.0, has_strong_ocr_end=False,
        next_prep_invalidated=True, lookahead_sec=120.0,
    ) == 120.0


def test_finalize_can_still_extend_after_next_prep_veto() -> None:
    """接线守门：调用点必须把 next_prep_invalidated 传给后视预算解析函数。

    只测纯函数不够——调用点漏传该标志时，收尾会退回"钉死 45s"的无限 pending。
    """
    from pathlib import Path

    src = (
        Path(__file__).resolve().parents[1] / "lsc/analyzer/valorant_broadcast.py"
    ).read_text(encoding="utf-8")
    index = src.index("effective_lookahead = _effective_lookahead_sec(")
    call = src[index: src.index(")", src.index("next_prep_invalidated=", index))]
    assert "next_prep_invalidated=next_prep_invalidated" in call
    assert "finalize=finalize" in call
    assert "available_end=available_end" in call


def test_interior_round_boundary_ignores_low_confidence_blip_after_result() -> None:
    """L1 不得把「回合结束后转场里的 2 帧低置信 combat」当成重新开战。

    2026-09-12 现场 round-000099（真实回合 990-1078）：审计样本为
    `result 1081-1083 → combat 1084(0.554)/1085(0.648) → non_game/replay 1086+`，
    随后整段回放。旧实现只要终态游程后出现**单帧** combat 就算跨回合 → 整条真实
    回合被 `rejected_interior_boundary` 丢掉。重新开战必须是稳定游程（≥4 帧）。
    """
    samples = [
        *[(float(t), "combat", 0.80) for t in range(0, 90)],      # 回合主体
        (90.0, "unknown", 0.374),
        (91.0, "non_game", 0.732),
        (92.0, "unknown", 0.383),
        *[(float(t), "result", 0.70) for t in range(93, 96)],      # 结算
        (96.0, "combat", 0.554),                                   # 转场抖动（低置信）
        (97.0, "combat", 0.648),
        (98.0, "non_game", 0.942),
        *[(float(t), "replay", 1.0) for t in range(99, 118)],       # 整段回放
        (118.0, "non_game", 0.922),
        *[(float(t), "buy", 0.76) for t in range(120, 123)],
    ]
    assert broadcast._interior_round_boundary(samples, start=0.0, end=99.75) is None

    # 反例：真实重开战（稳定 combat 游程）仍必须被识别
    with_real_resume = [
        *samples,
        *[(float(t), "combat", 0.80) for t in range(124, 160)],
    ]
    assert broadcast._interior_round_boundary(
        with_real_resume, start=0.0, end=150.0,
    ) == 124.0


def test_interior_round_boundary_requires_stable_resume_run() -> None:
    """阈值语义：重开战游程不足 ``min_resume_frames`` 帧时不算跨回合（可调）。"""
    samples = [
        *[(float(t), "result", 0.9) for t in range(0, 6)],
        (6.0, "combat", 0.9),          # 只有 2 帧
        (7.0, "combat", 0.9),
        (8.0, "non_game", 0.9),
    ]
    assert broadcast._interior_round_boundary(samples, start=-5.0, end=20.0) is None
    assert broadcast._interior_round_boundary(
        samples, start=-5.0, end=20.0, min_resume_frames=2,
    ) == 6.0


def _fake_frame_extract(label_of):
    """按 ts 决定像素值（十位=标签下标、个位随 ts 变化）的假抽帧器。

    每帧内容按 ts 奇偶摆动 +5（保持 value // 10 == label 不变）：避免"帧差分 ≈0"
    被冻结检测当成技术暂停（真实画面不会零差分）。
    """
    _LABELS = ("non_game", "buy", "combat", "result", "replay")

    def fake_extract(*args, **kwargs):
        start = float(kwargs["start_sec"])
        end = float(kwargs["end_sec"])
        out = []
        ts = start
        while ts <= end + 0.01:
            label = label_of(round(ts, 2))
            value = _LABELS.index(label) * 10 + (5 if int(round(ts)) % 2 else 0)
            out.append((ts, np.full((8, 8, 3), value, dtype=np.uint8)))
            ts += 1.0
        return out

    return fake_extract


def _fake_classifier():
    _LABELS = ("non_game", "buy", "combat", "result", "replay")

    class FakeClassifier:
        thresholds = {"stable_prob": 0.55}
        model_version = "test"
        provider = "cpu"

        def load(self) -> None:
            return None

        def predict_batch(self, images):
            rows = []
            for image in images:
                label = _LABELS[int(image[0, 0, 0]) // 10]
                row = np.full(len(_LABELS), 0.01, dtype=np.float32)
                row[_LABELS.index(label)] = 0.97
                rows.append(row)
            return np.array(rows, dtype=np.float32)

    return FakeClassifier()


def test_a5_window_cap_only_uses_inner_replay_blocks() -> None:
    """A5 缩窗：只认候选区间内的回放块起点，+8s 余量。"""
    from lsc.analyzer.valorant_broadcast import A5_WINDOW_CAP_MARGIN_SEC, _a5_window_cap

    item = {"replay_segments": [[20.0, 30.0], [200.0, 220.0]]}
    # 起点前的回放（属于上一回合尾段）忽略；区间内的取最小起点
    assert _a5_window_cap(item, start=100.0, end=400.0) == 200.0 + A5_WINDOW_CAP_MARGIN_SEC
    assert _a5_window_cap(item, start=250.0, end=400.0) is None      # 区间外
    assert _a5_window_cap({"replay_segments": []}, start=0.0, end=10.0) is None
    assert _a5_window_cap({}, start=0.0, end=10.0) is None


def test_a5_cap_shrinks_scan_and_finds_same_cutoff(monkeypatch) -> None:
    """缩窗路径：回放块为真时，审计仍能给出同一处截断（结论不变、成本更低）。"""
    monkeypatch.setattr(ocr_rounds, "extract_frames_cancellable",
                        _fake_frame_extract(lambda ts: "replay" if ts >= 215.0 else "combat"))
    monkeypatch.setattr(ocr_rounds, "_read_top_anchors", lambda image: (None, None, None))
    cand = {"round_key": "round-cap-1", "start": 100.0, "end": 240.0,
            "start_by": "ocr_combat", "end_by": "next_combat",
            "replay_segments": [[215.0, 235.0]]}
    outs = broadcast.audit_broadcast_rounds_with_outcomes(
        [dict(cand)], "unused.mp4", classifier=_fake_classifier(), finalize=True,
    )
    assert len(outs) == 1
    assert outs[0].status == "accepted", (outs[0].status, outs[0].reason)
    end = float(outs[0].candidate["end"])
    assert 210.0 <= end <= 218.0, outs[0].candidate
    # 扫描窗口被压到「回放起点 + 余量」以内（未压窗时应为 end+45=285）
    assert float(outs[0].candidate["broadcast_audit_scan_end"]) <= 224.0


def test_a5_cap_failure_retries_with_full_window(monkeypatch) -> None:
    """缩窗失败必须回退完整窗口（否则缩窗会把真出点挡在窗口外）。"""
    # A5 块是假的（该处仍是 combat）；真出点在 235 之后
    monkeypatch.setattr(ocr_rounds, "extract_frames_cancellable",
                        _fake_frame_extract(lambda ts: "replay" if ts >= 235.0 else "combat"))
    monkeypatch.setattr(ocr_rounds, "_read_top_anchors", lambda image: (None, None, None))
    cand = {"round_key": "round-cap-2", "start": 100.0, "end": 240.0,
            "start_by": "ocr_combat", "end_by": "next_combat",
            "replay_segments": [[215.0, 235.0]]}
    cache: dict = {}
    first = broadcast.audit_broadcast_rounds_with_outcomes(
        [dict(cand)], "unused.mp4", classifier=_fake_classifier(), finalize=True,
        audit_cache=cache,
    )
    assert len(first) == 1
    assert first[0].status == "pending", first[0]
    assert first[0].reason == "a5_cap_retry", first[0]

    # 第二轮（同 cache）不得再缩窗，必须拿到真出点
    second = broadcast.audit_broadcast_rounds_with_outcomes(
        [dict(first[0].candidate)], "unused.mp4", classifier=_fake_classifier(),
        finalize=True, audit_cache=cache,
    )
    assert len(second) == 1
    assert second[0].status == "accepted", (second[0].status, second[0].reason)
    end = float(second[0].candidate["end"])
    # 真出点 235（回放起点）+ 审计既定 2.5s 结算尾巴 ⇒ 约 237.25
    assert 236.5 <= end <= 238.0, second[0].candidate
    assert second[0].candidate["end_by"] == "broadcast_exclusion"
    assert second[0].candidate["end_quality"] == "precise"
    assert "broadcast_audit_window_cap" not in second[0].candidate


def test_tail_lookback_covers_round_end_before_late_ocr_anchor(monkeypatch) -> None:
    """回看窗口必须够长：OCR 粗出点落在"下回合买枪首帧"时，真实出点常早 30–60s。

    2026-09-12 19:14 现场 round-000071（真实 combat 712–798，粗出点 835）：
    回看 30s → 窗口从 805 开始（回合已结束）⇒ `_first_stable_exclusion` 认不出
    "combat → 终态"边界 ⇒ no_exclusion_evidence ⇒ 整条回合进不了草稿；
    回看 45/60s → accepted / broadcast_exclusion / precise。
    """
    def label_at(ts: float) -> str:
        if 100.0 <= ts <= 195.0:
            return "combat"
        if 196.0 <= ts <= 205.0:
            return "result"
        if 206.0 <= ts <= 232.0:
            return "replay"
        if 233.0 <= ts <= 255.0:
            return "buy"
        return "combat" if ts >= 256.0 else "non_game"

    monkeypatch.setattr(ocr_rounds, "extract_frames_cancellable", _fake_frame_extract(label_at))
    monkeypatch.setattr(ocr_rounds, "_read_top_anchors", lambda image: (None, None, None))
    cand = {"round_key": "round-late-anchor", "start": 100.0, "end": 240.0,
            "start_by": "ocr_combat", "end_by": "next_combat"}

    assert broadcast.BROADCAST_AUDIT_TAIL_LOOKBACK_SEC >= 45.0, (
        "回看过短会把回合自身的 combat 挡在窗口外（现场 round-000071）"
    )
    outs = broadcast.audit_broadcast_rounds_with_outcomes(
        [dict(cand)], "unused.mp4", classifier=_fake_classifier(), finalize=True,
    )
    assert len(outs) == 1
    assert outs[0].status == "accepted", (outs[0].status, outs[0].reason)
    assert outs[0].candidate["end_by"] == "broadcast_exclusion"
    assert outs[0].candidate["end_quality"] == "precise"
    # 出点必须落在终态游程内（≈195-205），不得停在粗出点 240
    assert 193.0 <= float(outs[0].candidate["end"]) <= 210.0, outs[0].candidate


# ---------------------------------------------------------------------------
# 同父分裂碎片头尾合并（2026-09-14 现场回归）
#
# 真实数据取自 `2026-09-14_09-59-12_至_2026-09-14_10-22-08.finalization.json`：
#   round-000070 父候选 695.609-963.609（268s）> MAX_BROADCAST_ROUND_SEC=150
#   ⇒ 切成 s0(695.6-845.6) / s1(845.6-963.6) 独立审计。
#   头碎片 s0：入点门禁后移 695.609→791.609，头内找不到出点证据 → pending_no_exclusion
#   尾碎片 s1：审计出真出点 930.75（result_ts=928.609 + 2.5s 结算尾）→ passed
#   实际回合 = 791.609-930.75（139s），但导出的是后半段 845.609-930.75（85s）。
# ---------------------------------------------------------------------------

def _split_head_070() -> dict:
    return {
        "round_key": "round-000070-s0",
        "split_from_oversize": True,
        "split_index": 0,
        "start": 791.609,
        "start_coarse": 695.609,
        "start_refined": 791.609,
        "start_delta": None,
        "start_confidence": 1.0,
        "start_confidence_source": "visual_combat_ratio",
        "start_by": "ocr_combat",
        "start_quality": "coarse",
        "start_review_required": True,
        "broadcast_start_gate": "moved_from_non_combat",
        "broadcast_start_gate_from": 695.609,
        "broadcast_start_gate_to": 791.609,
        "end": 845.609,
        "end_coarse": 845.609,
        "end_by": "next_combat",
        "end_confidence": 0.5,
        "end_quality": "coarse",
        "end_review_required": True,
        "broadcast_audit": "pending_no_exclusion",
        "broadcast_audit_reason": "none",
        "confirm_status": "pending",
        "result_ts": 928.609,
        "source_profile": "broadcast",
    }


def _split_tail_070() -> dict:
    return {
        "round_key": "round-000070-s1",
        "split_from_oversize": True,
        "split_index": 1,
        "start": 845.609,
        "start_coarse": 845.609,
        "start_refined": 845.609,
        "start_delta": None,
        "start_confidence": 1.0,
        "start_by": "ocr_combat",
        "start_quality": "coarse",
        "start_review_required": True,
        "broadcast_start_gate": "ok",
        "broadcast_start_gate_from": 845.609,
        "end": 930.75,
        "end_refined": 930.75,
        "end_coarse": 963.609,
        "end_delta": 32.859,
        "end_confidence": 0.92,
        "end_by": "broadcast_exclusion",
        "end_quality": "precise",
        "end_review_required": False,
        "broadcast_audit": "passed",
        "broadcast_audit_reason": "broadcast_replay_or_non_game",
        "broadcast_excluded_reason": "broadcast_replay_or_non_game",
        "broadcast_result_tail_sec": 2.5,
        "confirm_status": "vision_confirmed",
        "result_ts": 928.609,
        "source_profile": "broadcast",
    }


def test_split_family_merge_recovers_head_of_real_round() -> None:
    """头碎片（无出点证据）+ 尾碎片（有权威出点）⇒ 合并成完整回合。"""
    head, tail = _split_head_070(), _split_tail_070()
    items = [head, tail]

    assert _merge_split_family_fragments(items) == 1

    # 起点并回头的起点（已过入点门禁的 791.609），出点保持尾碎片的审计结论
    assert tail["start"] == 791.609
    assert tail["start_coarse"] == 695.609
    assert tail["end"] == 930.75
    assert tail["end_by"] == "broadcast_exclusion"
    assert tail["end_quality"] == "precise"
    assert tail["split_merged"] is True
    assert tail["split_merged_from"] == ["round-000070-s0"]
    assert tail["split_merged_original_start"] == 845.609
    assert tail["split_merged_gap_sec"] == 0.0
    # 起点属性随起点一起搬运，不得留下 start < start_coarse 之类自相矛盾
    assert tail["broadcast_start_gate"] == "moved_from_non_combat"
    assert tail["start_quality"] == "coarse"
    # 合并后是 139.1s 的完整回合，而不是 85s 的半截
    assert round(tail["end"] - tail["start"], 1) == 139.1

    # 头碎片仍留在列表（保留"入列但不导出"的可观测性），只标被接管；
    # broadcast_audit 不得篡改成 passed——失败关闭语义必须原样保留
    assert head["broadcast_audit"] == "pending_no_exclusion"
    assert head["superseded_by_round_key"] == "round-000070-s1"
    assert head["broadcast_audit_reason"] == "superseded_by_split_merge"


def test_split_family_merge_makes_clip_pass_export_gate() -> None:
    """合并的**目的**是让完整回合能进草稿，且不靠放宽门禁实现。"""
    from lsc.exporter.jianying_draft import _broadcast_gate_passed

    def gate(clip: dict, include_pending: bool) -> bool:
        return _broadcast_gate_passed(
            confirm_status=clip.get("confirm_status"),
            source_profile=clip.get("source_profile"),
            broadcast_audit=clip.get("broadcast_audit"),
            broadcast_review_required=bool(clip.get("broadcast_review_required", True)),
            start_quality=clip.get("start_quality"),
            end_quality=clip.get("end_quality"),
            start_review_required=bool(clip.get("start_review_required", False)),
            end_review_required=bool(clip.get("end_review_required", False)),
            duration_anomaly=False,
            end_by=clip.get("end_by"),
            include_pending=include_pending,
        )

    tail = _split_tail_070()
    # 未合并时：尾碎片本身也过门禁（出点权威），但它只是半截回合
    assert gate(tail, include_pending=False) is True
    # 头碎片单独看：过不了门禁（本就该被丢弃）
    assert gate(_split_head_070(), include_pending=False) is False
    # 合并后：仍过门禁，且跨度是完整回合
    _merge_split_family_fragments([_split_head_070(), _split_tail_070()])
    merged = _split_tail_070()
    _merge_split_family_fragments([_split_head_070(), merged])
    assert gate(merged, include_pending=False) is True
    assert merged["start"] == 791.609


def test_split_family_merge_tolerates_result_ts_outside_head_span() -> None:
    """现场头碎片的 result_ts(928.609) 落在自己区间之外——不影响判定。

    这正是"用 result_ts 升格 next_combat"方案不可行的原因：头碎片区间
    (791.6-845.6) 内没有结算证据，只有尾碎片才有。
    """
    head, tail = _split_head_070(), _split_tail_070()
    assert not (head["start"] <= head["result_ts"] <= head["end"])
    assert _merge_split_family_fragments([head, tail]) == 1
    assert tail["start"] == 791.609


def test_split_family_merge_skips_authoritative_head() -> None:
    """044 现场几何：权威出点在**头**碎片上（s0 440.5-557.25 passed，
    s1 590.5-604.95 是真实回合之后的残余）⇒ 不得反向合并。"""
    head = {
        "round_key": "round-000044-s0",
        "split_from_oversize": True,
        "split_index": 0,
        "start": 440.515,
        "end": 557.25,
        "end_by": "broadcast_exclusion",
        "end_quality": "precise",
        "end_review_required": False,
        "broadcast_audit": "passed",
        "confirm_status": "vision_confirmed",
        "result_ts": 590.953,
    }
    tail = {
        "round_key": "round-000044-s1",
        "split_from_oversize": True,
        "split_index": 1,
        "start": 590.515,
        "end": 604.953,
        "end_by": "next_combat",
        "end_quality": "coarse",
        "broadcast_audit": "pending_no_exclusion",
        "result_ts": 590.953,
    }

    assert _merge_split_family_fragments([head, tail]) == 0
    assert tail["start"] == 590.515
    assert "split_merged" not in tail
    assert "superseded_by_round_key" not in head


def test_split_family_merge_requires_contiguity() -> None:
    """头尾之间隔着内容（gap > 容差）不得拼接成假回合。"""
    head, tail = _split_head_070(), _split_tail_070()
    head["end"] = 700.0  # 与尾碎片起点 845.609 不再相接
    assert _merge_split_family_fragments([head, tail]) == 0
    assert tail["start"] == 845.609


def test_split_family_merge_respects_max_round_duration() -> None:
    """合并后越过"超长回合"红线（>150s）时不做合并——那是另一个异常形态。"""
    head, tail = _split_head_070(), _split_tail_070()
    head["start"] = 600.0  # 合并后 = 930.75-600.0 = 330.75s > 150
    head["end"] = 845.609
    assert _merge_split_family_fragments([head, tail]) == 0
    assert tail["start"] == 845.609


def test_split_family_merge_requires_inconclusive_head() -> None:
    """被拒的头碎片（入点/区间有问题）不得把它的起点并进别人的回合。"""
    head, tail = _split_head_070(), _split_tail_070()
    head["broadcast_audit"] = "rejected_interior_boundary"
    assert _merge_split_family_fragments([head, tail]) == 0
    assert tail["start"] == 845.609


def test_split_family_merge_syncs_outcome_sink() -> None:
    """outcome 记录时做了浅拷贝 ⇒ 合并必须同步回写，否则调用方拿到旧边界。"""
    head, tail = _split_head_070(), _split_tail_070()
    sink = [
        BroadcastAuditOutcome(
            status="manual_review", candidate=dict(head), reason="no_exclusion_evidence"
        ),
        BroadcastAuditOutcome(
            status="accepted", candidate=dict(tail), reason="broadcast_replay_or_non_game"
        ),
    ]

    assert _merge_split_family_fragments([head, tail], sink) == 1
    assert sink[1].candidate["start"] == 791.609
    assert sink[1].candidate["split_merged"] is True
    assert sink[0].candidate["superseded_by_round_key"] == "round-000070-s1"


def test_audit_wires_split_family_merge_before_return() -> None:
    """接线守卫：两个审计入口共用 audit_broadcast_rounds，合并必须在其出口。

    且必须在定稿循环**之后**——只有那时本批结论才都定稿。
    """
    from pathlib import Path

    src = (
        Path(__file__).resolve().parents[1] / "lsc/analyzer/valorant_broadcast.py"
    ).read_text(encoding="utf-8")
    assert "_reconcile_split_family_fragments(output, audit_cache, _outcome_sink)" in src
    merge_idx = src.find("_reconcile_split_family_fragments(output, audit_cache")
    assert merge_idx > src.find('"赛事回合审计完成:')


def test_split_family_merge_across_batches_via_ledger() -> None:
    """**主路径**回归：正常运行期 deferred_audit=True，审计一次只推进一个子块，
    头尾碎片不在同一批 ⇒ 必须靠 audit_cache 台账跨批补回前半段。

    现场（2026-09-14）：s0 在 10:27:54 定稿（未定论）、s1 在 10:28:39 定稿
    （passed），两次不同的审计调用；只在同批里合并等于在主路径上不生效。
    """
    cache: dict = {}

    # 第一批：只有头碎片（未定论）——只入台账，不改边界
    head = _split_head_070()
    assert broadcast._reconcile_split_family_fragments([head], cache) == 0
    assert head["start"] == 791.609
    assert "split_merged" not in head
    ledger = cache[broadcast._SPLIT_FAMILY_CACHE_KEY]["round-000070"]
    assert ledger[0]["round_key"] == "round-000070-s0"

    # 第二批：只有尾碎片（定稿）——从台账补回前半段
    tail = _split_tail_070()
    assert broadcast._reconcile_split_family_fragments([tail], cache) == 1
    assert tail["start"] == 791.609
    assert tail["end"] == 930.75
    assert tail["split_merged"] is True
    assert tail["split_merged_from"] == ["round-000070-s0"]
    assert tail["split_merged_original_start"] == 845.609
    assert round(tail["end"] - tail["start"], 1) == 139.1


def test_split_family_merge_across_batches_needs_inconclusive_head_in_ledger() -> None:
    """台账里的头碎片若已被拒（入点/区间有问题），跨批也不得合并。"""
    cache: dict = {}
    head = _split_head_070()
    head["broadcast_audit"] = "rejected_interior_boundary"
    broadcast._reconcile_split_family_fragments([head], cache)

    tail = _split_tail_070()
    assert broadcast._reconcile_split_family_fragments([tail], cache) == 0
    assert tail["start"] == 845.609


def test_split_family_merge_chains_three_fragments() -> None:
    """3 块以上的族：台账记录的是**合并后**的起点，故可逐级传递。"""
    cache: dict = {}
    head0 = _split_head_070()
    head1 = {
        **_split_head_070(),
        "round_key": "round-000070-s1",
        "split_index": 1,
        "start": 845.609,
        "end": 890.0,
    }
    # 两块都未定论，先入台账
    assert broadcast._reconcile_split_family_fragments([head0, head1], cache) == 0
    ledger = cache[broadcast._SPLIT_FAMILY_CACHE_KEY]["round-000070"]
    assert ledger[0]["start_fields"]["start"] == 791.609
    # 中段碎片自己虽然未定论，台账里记的是**有效起点**（承接 s0）
    assert ledger[1]["start_fields"]["start"] == 791.609
    assert ledger[1]["chained_from"] == ["round-000070-s0"]

    tail = {
        **_split_tail_070(),
        "round_key": "round-000070-s2",
        "split_index": 2,
        "start": 890.0,
    }
    assert broadcast._reconcile_split_family_fragments([tail], cache) == 1
    # 链式传递：s2 直接借到的是 s1 台账里的"有效起点"，而 s1 的起点已被 s0 承接
    # ⇒ 一次补回整族最前面的真实起点 791.609
    assert tail["start"] == 791.609
    assert tail["split_merged_from"] == ["round-000070-s1", "round-000070-s0"]
    assert round(tail["end"] - tail["start"], 1) == 139.1


def test_split_family_reconcile_is_idempotent() -> None:
    """重复 reconcil 不得二次吸收（否则 split_merged_original_start 会失真）。"""
    cache: dict = {}
    head, tail = _split_head_070(), _split_tail_070()
    items = [head, tail]
    assert broadcast._reconcile_split_family_fragments(items, cache) == 1
    assert broadcast._reconcile_split_family_fragments(items, cache) == 0
    assert tail["split_merged_original_start"] == 845.609
    assert tail["start"] == 791.609




def test_split_merge_provenance_is_forwarded_to_clip_metadata() -> None:
    """合并来源必须透传到 clip_queued 与草稿侧白名单，否则前端/导出看不到解释。"""
    from pathlib import Path

    room_src = (
        Path(__file__).resolve().parents[1] / "python-backend/handlers/room_handler.py"
    ).read_text(encoding="utf-8")
    draft_src = (
        Path(__file__).resolve().parents[1]
        / "python-backend/handlers/jianying_handlers.py"
    ).read_text(encoding="utf-8")
    for field in ("split_merged", "split_merged_from", "superseded_by_round_key"):
        assert f'"{field}"' in room_src, field
        assert f'"{field}"' in draft_src, field


def test_inline_broadcast_audit_is_step_bounded(monkeypatch, tmp_path) -> None:
    """收尾同步审计必须限步（D 修复）：不限步时审计与被超时包裹的扫描争抢
    ONNX/DirectML 锁，把整窗拖到扫描超时。

    现场实测（2026-09-14）：同窗口无争抢仅需 51.8s（66.2s 媒体），
    现场却烧满 360s 超时并取消了正在跑的审计（round-000097 因此丢失）。
    """
    import lsc.analyzer.valorant_frame_classifier as classifier_module
    from lsc.analyzer.valorant_plugin import _BROADCAST_INLINE_AUDIT_STEP_MEDIA_SEC

    video = tmp_path / "broadcast.mp4"
    video.write_bytes(b"placeholder")
    seen: dict = {}

    class FakeClassifier:
        thresholds = {"stable_prob": 0.55}
        model_version = "test"
        provider = "cpu"

        def __init__(self, *args, **kwargs) -> None:
            pass

        def load(self) -> None:
            return None

    monkeypatch.setattr(
        ocr_rounds,
        "detect_valorant_rounds_ocr",
        lambda *_a, **_k: [{"start": 10.0, "end": 80.0, "round_key": "round-000001"}],
    )
    monkeypatch.setattr(classifier_module, "ValorantFrameClassifier", FakeClassifier)

    def fake_audit(rounds, video_path, **kwargs):
        seen.update(kwargs)
        return [{**r, "broadcast_audit": "passed"} for r in rounds]

    monkeypatch.setattr(broadcast, "audit_broadcast_rounds", fake_audit)

    result = ValorantAnalyzerPlugin().scan_window(
        str(video),
        ScanWindow(start_sec=0.0, end_sec=90.0, timeout_sec=120.0, use_ocr=True),
        {
            "valorant_profile": "broadcast",
            "runtime_state": {},
            "current_dur": 90.0,
            "finalize": True,
            "ffmpeg_path": "ffmpeg",
        },
    )
    assert result and result[0]["broadcast_audit"] == "passed"
    assert seen.get("max_media_step_sec") == _BROADCAST_INLINE_AUDIT_STEP_MEDIA_SEC
    assert seen.get("finalize") is True


def test_slow_scan_logs_stage_breakdown(monkeypatch, tmp_path, caplog) -> None:
    """分段耗时打点：扫描逼近超时预算时必须打出 OCR/审计两段墙钟，
    否则下次现场仍只能看到"扫描超时"这一句，无法定位慢在哪一段。"""
    import logging as _logging
    import time as _time

    video = tmp_path / "broadcast.mp4"
    video.write_bytes(b"placeholder")

    def slow_ocr(*_a, **_k):
        # 必须真的耗时：Windows 上 monotonic 粒度约 15ms，瞬时返回会让
        # elapsed 恰好为 0，打点阈值永远不满足（测试会假绿/假红）。
        _time.sleep(0.05)
        return []

    monkeypatch.setattr(ocr_rounds, "detect_valorant_rounds_ocr", slow_ocr)

    with caplog.at_level(_logging.WARNING, logger="lsc.analyzer.valorant_plugin"):
        ValorantAnalyzerPlugin().scan_window(
            str(video),
            # 小超时预算（阈值 0.6*0.01=6ms）⇒ 必然跨过打点阈值
            ScanWindow(start_sec=0.0, end_sec=10.0, timeout_sec=0.01, use_ocr=True),
            {"valorant_profile": "pov", "runtime_state": {}, "current_dur": 10.0},
        )

    messages = [r.getMessage() for r in caplog.records]
    assert any("扫描耗时逼近超时预算" in m for m in messages), messages
    breakdown = next(m for m in messages if "扫描耗时逼近超时预算" in m)
    assert "ocr=" in breakdown and "audit=" in breakdown and "timeout=" in breakdown


# ---------------------------------------------------------------------------
# 收尾缺口补扫口径（2026-09-14 真实会话回归）
#   真实事实：round-000015-s0(154.1-246.7) 11:43:43 定稿、round-000071-s0
#   (712.2-802.2) 11:52:37 定稿；11:54:33 补扫仍把 153.0-257.0 / 711.0-813.0
#   判为「无候选」并合成新候选，最终以父键定稿 ⇒ 同回合两条重叠条目
#   （导出侧 R12/R13 靠 OVERLAP_DEDUP 兜住）。且 gap_sweep_done 写在了每轮
#   新建的局部 state 上，收尾 4 轮各补扫一次。
# ---------------------------------------------------------------------------

def _finalize_scan_state(runtime_state):
    return {
        "valorant_profile": "broadcast",
        "runtime_state": runtime_state,
        "current_dur": 1147.0,
        "finalize": True,
        "ffmpeg_path": "ffmpeg",
    }


def _sweep_harness(monkeypatch, tmp_path, *, ocr_rounds_per_call, sweep_calls):
    """搭一个收尾扫描现场：OCR 出候选、审计按脚本给结论、补扫被记录。"""
    import lsc.analyzer.valorant_frame_classifier as classifier_module

    video = tmp_path / "recording.mp4"
    video.write_bytes(b"placeholder")
    pending_ocr = list(ocr_rounds_per_call)

    class FakeClassifier:
        thresholds = {"stable_prob": 0.55}
        model_version = "test"
        provider = "cpu"

        def __init__(self, *args, **kwargs) -> None:
            pass

        def load(self) -> None:
            return None

    monkeypatch.setattr(
        ocr_rounds,
        "detect_valorant_rounds_ocr",
        lambda *_a, **_k: [dict(c) for c in (pending_ocr.pop(0) if pending_ocr else [])],
    )
    monkeypatch.setattr(classifier_module, "ValorantFrameClassifier", FakeClassifier)
    monkeypatch.setattr(
        broadcast,
        "audit_broadcast_rounds",
        lambda rounds, *_a, **_k: [
            {**r, "broadcast_audit": "passed", "end_by": "broadcast_exclusion",
             "end_quality": "precise", "confirm_status": "vision_confirmed"}
            for r in rounds
        ],
    )

    def fake_sweep(video_path, candidates, **_kwargs):
        sweep_calls.append([dict(c) for c in candidates])
        return []

    monkeypatch.setattr(broadcast, "sweep_gap_rounds", fake_sweep)
    return video


def test_gap_sweep_runs_once_per_finalize_task(monkeypatch, tmp_path) -> None:
    """补扫标记必须跨调用存活：state 是每轮新建的局部 dict，写它等于没写。

    现场：收尾 4 轮各补扫一次（4×全片巡检），把 20s 审计微步预算挤爆。
    """
    sweep_calls: list = []
    video = _sweep_harness(
        monkeypatch, tmp_path,
        ocr_rounds_per_call=[[{"start": 10.0, "end": 80.0, "round_key": "round-000001"}]] * 3,
        sweep_calls=sweep_calls,
    )
    runtime_state: dict = {}
    plugin = ValorantAnalyzerPlugin()
    window = ScanWindow(start_sec=0.0, end_sec=90.0, timeout_sec=120.0, use_ocr=True)

    for _ in range(3):
        # 每轮都传**新建**的外层 state（复刻 room_handler 的 _scan_state），
        # 只有 runtime_state 是跨轮共享的同一个 dict
        plugin.scan_window(str(video), window, _finalize_scan_state(runtime_state))

    assert runtime_state.get("gap_sweep_done") is True, "标记必须落到 runtime_state"
    assert len(sweep_calls) == 1, f"收尾多轮只应补扫一次，实际 {len(sweep_calls)} 次"


def test_gap_sweep_excludes_finalized_spans(monkeypatch, tmp_path) -> None:
    """已定稿真实回合覆盖过的区间不得再被当作"无候选区间"。"""
    sweep_calls: list = []
    video = _sweep_harness(
        monkeypatch, tmp_path,
        ocr_rounds_per_call=[
            [{"start": 900.0, "end": 1000.0, "round_key": "round-000090"}]
        ],
        sweep_calls=sweep_calls,
    )
    runtime_state: dict = {
        # 已定稿跨度台账在共享 audit_cache 里（两条审计路径同一份）
        "broadcast_audit_cache": {
            broadcast._FINALIZED_SPANS_CACHE_KEY: [[154.053, 246.703], [712.153, 802.203]]
        }
    }
    ValorantAnalyzerPlugin().scan_window(
        str(video),
        ScanWindow(start_sec=0.0, end_sec=90.0, timeout_sec=120.0, use_ocr=True),
        _finalize_scan_state(runtime_state),
    )

    assert sweep_calls, "补扫应被调用（候选列表非空才进缺口计算）"
    passed = sweep_calls[0]
    spans = sorted((float(c["start"]), float(c["end"])) for c in passed)
    assert (154.053, 246.703) in spans
    assert (712.153, 802.203) in spans


def test_finalized_span_ledger_only_records_passed() -> None:
    """台账只记 passed（确有回合），被拒区间保持可补扫——缺口补扫是安全网。"""
    cache: dict = {}
    broadcast._remember_finalized_spans(cache, [
        {"start": 10.0, "end": 80.0, "broadcast_audit": "passed"},
        {"start": 90.0, "end": 150.0, "broadcast_audit": "rejected_no_stable_combat"},
        {"start": 160.0, "end": 220.0, "broadcast_audit": "pending_lookahead"},
        {"start": 230.0, "end": 290.0, "broadcast_audit": "skipped"},
        {"start": 300.0, "end": 300.0, "broadcast_audit": "passed"},  # 零长不计
    ])
    assert broadcast.finalized_spans(cache) == [[10.0, 80.0]]

    # 幂等：重复记同一跨度不膨胀
    broadcast._remember_finalized_spans(cache, [
        {"start": 10.0, "end": 80.0, "broadcast_audit": "passed"}
    ])
    assert broadcast.finalized_spans(cache) == [[10.0, 80.0]]

    # 有界
    many = [
        {"start": float(i * 10), "end": float(i * 10 + 5), "broadcast_audit": "passed"}
        for i in range(broadcast._FINALIZED_SPANS_MAX + 10)
    ]
    broadcast._remember_finalized_spans(cache, many)
    assert len(broadcast.finalized_spans(cache)) == broadcast._FINALIZED_SPANS_MAX


def test_audit_records_finalized_spans_into_shared_cache(monkeypatch) -> None:
    """写入点必须在审计出口：两条审计路径（插件同步 / 后台微步）共用同一个
    audit_cache，只有写在那里，收尾补扫才看得见**另一条路径**定稿的回合。

    现场正是如此：s0 由后台路径在正常阶段定稿，收尾期的插件补扫看不见它。
    """
    def label_at(ts: float) -> str:
        if 100.0 <= ts <= 195.0:
            return "combat"
        if 196.0 <= ts <= 205.0:
            return "result"
        if 206.0 <= ts <= 232.0:
            return "replay"
        if 233.0 <= ts <= 255.0:
            return "buy"
        return "combat" if ts >= 256.0 else "non_game"

    monkeypatch.setattr(ocr_rounds, "extract_frames_cancellable", _fake_frame_extract(label_at))
    monkeypatch.setattr(ocr_rounds, "_read_top_anchors", lambda image: (None, None, None))

    cache: dict = {}
    out = audit_broadcast_rounds(
        [{"round_key": "round-late-anchor", "start": 100.0, "end": 240.0,
          "start_by": "ocr_combat", "end_by": "next_combat"}],
        "unused.mp4",
        classifier=_fake_classifier(),
        audit_cache=cache,
        finalize=True,
    )
    assert out and out[0]["broadcast_audit"] == "passed", out
    spans = broadcast.finalized_spans(cache)
    assert spans, "审计出口必须把已定稿跨度写进共享 audit_cache"
    assert spans[0][0] >= 100.0


def test_plugin_sweep_reads_finalized_spans_from_audit_cache() -> None:
    """插件读的必须是共享 audit_cache 台账（不是自己那份 runtime_state 副本）。"""
    from pathlib import Path

    src = (
        Path(__file__).resolve().parents[1] / "lsc/analyzer/valorant_plugin.py"
    ).read_text(encoding="utf-8")
    assert "_finalized_span_items(audit_cache)" in src
    assert "finalized_spans(audit_cache)" in src
    # 旧的 runtime_state 版台账必须彻底移除，避免两份台账各记一半
    assert "_BROADCAST_FINALIZED_SPANS_KEY" not in src


def test_gap_sweep_runs_even_with_empty_queue_and_no_new_rounds(monkeypatch, tmp_path) -> None:
    """收尾期「无新候选 + 队列已排空」也必须补扫。

    2026-09-14 12:40 会话现场：收尾各轮 OCR 恒为 0 回合、待审队列空 ⇒ 整个
    broadcast 分支被跳过，补扫一次没跑，最后 650.2-736.1（85.9s）无人巡检。
    补扫要抓的恰恰是这种"画面静止导致全程无候选"的漏检，故收尾期无条件进入。
    """
    sweep_calls: list = []
    video = _sweep_harness(
        monkeypatch, tmp_path,
        ocr_rounds_per_call=[[]],  # 本轮没有任何新 OCR 回合
        sweep_calls=sweep_calls,
    )
    runtime_state: dict = {
        # 队列已排空：只剩已定稿真实回合的跨度（这正是补扫需要的覆盖证据）
        "broadcast_pending_rounds": [],
        "broadcast_audit_cache": {
            broadcast._FINALIZED_SPANS_CACHE_KEY: [[0.2, 51.25], [585.03, 650.25]]
        },
    }
    ValorantAnalyzerPlugin().scan_window(
        str(video),
        ScanWindow(start_sec=728.1, end_sec=736.1, timeout_sec=120.0, use_ocr=True),
        _finalize_scan_state(runtime_state),
    )

    assert sweep_calls, "收尾期队列空、无新回合时补扫也必须运行"
    spans = sorted((float(c["start"]), float(c["end"])) for c in sweep_calls[0])
    assert (585.03, 650.25) in spans, "已定稿跨度必须作为覆盖证据传进去"
    # 650.25-1147.0 是真实缺口 ⇒ 补扫必须看到它（而不是被当成"全片已覆盖"）
    assert span_gap_visible(sweep_calls[0])


def span_gap_visible(candidates: list) -> bool:
    """候选覆盖集必须留出真实缺口（此处 duration=1147.0，缺口 650.25-1147.0）。"""
    from lsc.analyzer.valorant_broadcast import GAP_SWEEP_MIN_GAP_SEC, _merged_span_gaps

    spans = [(float(c["start"]), float(c["end"])) for c in candidates]
    gaps = _merged_span_gaps(spans, duration=1147.0, min_gap_sec=GAP_SWEEP_MIN_GAP_SEC)
    return any(a <= 650.3 <= b for a, b in gaps)


# ---------------------------------------------------------------------------
# 弱出点候选的「向前回扫」取证（2026-09-15）
#
# 现场形态：OCR 以 next_combat 闭合（漏检下一回合准备横幅，出点落在下一回合满钟
# 首帧，比真出点晚 30-80s）。旧实现只看 [end-60, end+90]：真出点所在的
# combat -> replay/result/non_game 转场若早于 end-60 就完全在窗外，
# _first_stable_exclusion 因为「样本里先有 replay、没有 combat 前缀」而认不出边界
# => reason=none => pending_no_exclusion，把过晚的粗出点冻结成终态（切片尾部带
# 整段回放与买枪，且此后无人再审）。修复：定稿前必须先做一次有界向前回扫取证。
# ---------------------------------------------------------------------------

_BROADCAST_LABELS = ("non_game", "buy", "combat", "result", "replay")
# 伪标签「unknown」的编码：分类器输出平坦概率（最高置信度 0.2 < stable_prob=0.55），
# 由 _stable_visual_label 归一成 unknown —— 与真实模型"看不清"的形态一致。
_UNKNOWN_LABEL_INDEX = len(_BROADCAST_LABELS)


class _LabelClassifier:
    """8x8 假帧：像素首字节编码标签索引（沿用 liveness 测试的假分类器风格）。"""

    thresholds = {"stable_prob": 0.55}
    model_version = "test"
    provider = "cpu"

    def load(self) -> None:
        return None

    def predict_batch(self, images):
        rows = []
        for image in images:
            index = int(image[0, 0, 0])
            if index == _UNKNOWN_LABEL_INDEX:
                rows.append(np.full(len(_BROADCAST_LABELS), 0.2, dtype=np.float32))
                continue
            row = np.full(len(_BROADCAST_LABELS), 0.01, dtype=np.float32)
            row[index] = 0.97
            rows.append(row)
        return np.array(rows, dtype=np.float32)


def _label_frame(ts: float, label: str) -> np.ndarray:
    """假帧：首像素编码标签，其余像素随 ts 变化。

    必须随 ts 变化：_first_frozen_frames 会把「连续两帧几乎一样」的 combat 判成
    技术暂停（真实交战帧不会一样），常量帧会让用例走成 broadcast_pause 而不是
    回放转场，掩盖被测的边界语义。
    """
    frame = np.zeros((8, 8, 3), dtype=np.uint8)
    frame[:, :, 1] = ((int(ts) * 37) % 200) + 20
    frame[0, 0, 0] = (
        _UNKNOWN_LABEL_INDEX
        if label == "unknown"
        else _BROADCAST_LABELS.index(label)
    )
    return frame


def _install_label_ocr(monkeypatch, label_at, calls: list | None = None) -> None:
    def fake_extract(*args, **kwargs):
        start = float(kwargs["start_sec"])
        end = float(kwargs["end_sec"])
        if calls is not None:
            calls.append((round(start, 3), round(end, 3)))
        return [
            (float(ts), _label_frame(float(ts), label_at(float(ts))))
            for ts in np.arange(start, end + 0.01, 1.0)
        ]

    monkeypatch.setattr(ocr_rounds, "extract_frames_cancellable", fake_extract)
    monkeypatch.setattr(ocr_rounds, "_read_top_anchors", lambda image: (None, None, None))


def _drive_audit_to_terminal(
    candidate: dict,
    cache: dict,
    *,
    available_end: float,
    limit: int = 30,
    sink: list | None = None,
) -> tuple[dict, int]:
    """按 room_handler 的微步骤调度反复调用审计，直到候选拿到终态。

    ``sink`` 传入时收集每轮的 outcome（交付态断言用）。
    """
    for round_index in range(1, limit + 1):
        out = audit_broadcast_rounds(
            [dict(candidate)],
            "unused.mp4",
            classifier=_LabelClassifier(),
            available_end=available_end,
            audit_cache=cache,
            max_media_step_sec=18.0,
            _outcome_sink=sink,
        )
        if not out:
            # 拒绝终态只进 outcome sink、不写 output（既有契约）：据此终止。
            if sink:
                rejected = sink[-1]
                if str(getattr(rejected, "status", "")) == "rejected":
                    return dict(getattr(rejected, "candidate", None) or candidate), round_index
            raise AssertionError("审计吞掉了候选：output 与 outcome sink 都为空")
        item = out[0]
        if str(item.get("broadcast_audit") or "") != "pending_lookahead":
            return item, round_index
    raise AssertionError(f"候选未在 {limit} 轮内定稿（回扫不收敛）")


def _weak_end_candidate(round_key: str) -> dict:
    return {
        "round_key": round_key,
        "start": 0.0,
        "end": 120.0,
        "start_by": "ocr_combat",
        "end_by": "next_combat",
        "confirm_status": "pending",
        "source_profile": "broadcast",
        "boundary_source": "valorant_ocr_v1",
    }


def test_weak_end_candidate_backward_sweeps_to_real_end(monkeypatch) -> None:
    """弱出点 + 尾窗无排除证据 => 向前回扫后截到真实出点（而不是冻结过晚粗出点）。"""

    def label_at(ts: float) -> str:
        if ts < 60.0:
            return "combat"      # 真实回合的交战尾段
        if ts < 70.0:
            return "replay"      # 赛后回放（真实出点在 60 附近）
        return "combat"          # 下一回合满钟（OCR 误判成新回合闭合点）

    calls: list = []
    _install_label_ocr(monkeypatch, label_at, calls)
    cache: dict = {}

    item, rounds = _drive_audit_to_terminal(
        _weak_end_candidate("round-000012"), cache, available_end=320.0,
    )

    assert item["end_by"] == "broadcast_exclusion"
    assert item["broadcast_audit"] == "passed"
    assert item["confirm_status"] == "vision_confirmed"
    assert item["end_quality"] == "precise"
    # 真实出点 60 + 结算展示尾巴(2.5s) - 边界微调(0.25s)
    assert 58.0 <= float(item["end"]) <= 65.0, item["end"]
    # 回扫必须真的走到尾窗左界之前，而不是停在尾窗内
    assert float(item["broadcast_backward_swept_to"]) < 60.0
    assert any(start < 60.0 for start, _end in calls), "必须发生尾窗之前的向前回扫"
    assert rounds <= 20, f"回扫应在有限微步骤内收敛（实际 {rounds} 轮）"


def test_weak_end_backward_sweep_terminates_without_exclusion(monkeypatch) -> None:
    """回扫必须有界：整段没有排除证据时仍收敛到 pending_no_exclusion，不得无限 pending。"""
    calls: list = []
    _install_label_ocr(monkeypatch, lambda _ts: "combat", calls)
    cache: dict = {}
    candidate = _weak_end_candidate("round-000013")

    item, rounds = _drive_audit_to_terminal(candidate, cache, available_end=320.0)

    assert item["broadcast_audit"] == "pending_no_exclusion"
    assert item["confirm_status"] == "pending"
    assert item["end_by"] == "next_combat"
    # 回扫扫到候选起点就停（有界），并把「已扫完」写进缓存，不再来一轮
    assert cache["round-000013"]["fallback_full_scanned"] is True
    assert float(cache["round-000013"]["backward_sweep_scanned_to"]) == 0.0
    assert rounds <= 20, f"回扫不得无限滞留（实际 {rounds} 轮）"
    # 定稿后必须命中缓存：重复调用不得再抽帧（否则审计预算被同一条候选吃光）
    before = len(calls)
    repeat = audit_broadcast_rounds(
        [dict(candidate)],
        "unused.mp4",
        classifier=_LabelClassifier(),
        available_end=320.0,
        audit_cache=cache,
        max_media_step_sec=18.0,
    )
    assert repeat[0]["broadcast_audit"] == "pending_no_exclusion"
    assert len(calls) == before, "已定稿结论必须命中缓存，不得重新抽帧"


# ---------------------------------------------------------------------------
# 入点门禁：区分「有证据的否定」与「模型不确定」（P1-4，2026-09-15）
#
# 旧实现把两者一起判成 rejected_no_stable_combat_start（终态、人工确认也不复活），
# 于是「交战在起点之后 15-35s 才开始」「前段画面模型读不准」的真实回合被永久丢弃。
# 现在：窗口里有连续 >=2 帧 replay/non_game/result 才算证据（拒绝）；只有
# unknown/buy 这类非终态标签时先把门禁窗 15s 扩到 35s 复判，仍无结论则保留候选
# 交人工确认，绝不静默删除。
# ---------------------------------------------------------------------------


def test_start_gate_uncertain_head_keeps_round_for_manual_review(monkeypatch) -> None:
    """前 30s 模型读不准（unknown）+ 出点在 60s 的真实回合：不得被门禁判死。"""

    def label_at(ts: float) -> str:
        if ts < 30.0:
            return "unknown"     # 模型看不清（不代表不是交战）
        if ts < 60.0:
            return "combat"
        if ts < 75.0:
            return "replay"      # 赛后回放（真出点在 60 附近）
        return "combat"

    calls: list = []
    _install_label_ocr(monkeypatch, label_at, calls)
    cache: dict = {}
    sink: list = []

    item, rounds = _drive_audit_to_terminal(
        _weak_end_candidate("round-000014"),
        cache,
        available_end=320.0,
        sink=sink,
    )

    # 入点：不确定 = 保留 + 待复核，而不是终态拒绝
    assert item["broadcast_start_gate"] == "inconclusive"
    assert item["start_quality"] == "coarse"
    assert item["start_review_required"] is True
    assert item["confirm_status"] == "pending", "入点不确定不得自动导出"
    # 出点仍然定稿：整条回合不因入点不确定而丢失
    assert item["end_by"] == "broadcast_exclusion"
    assert item["broadcast_audit"] == "passed"
    assert item["end_quality"] == "precise"
    assert 58.0 <= float(item["end"]) <= 65.0, item["end"]
    # 交付态是人工复核（不是被拒绝/删除）
    assert sink and sink[-1].status == "manual_review"
    assert sink[-1].reason == "start_gate_inconclusive"
    # 不确定时先扩窗复判一次：门禁窗必须真的扫到 35s
    assert (15.0, 35.0) in calls, calls
    assert rounds <= 25, f"门禁扩窗 + 回扫应在有限轮次内收敛（实际 {rounds} 轮）"


def test_start_gate_extends_window_then_rejects_on_replay_evidence(monkeypatch) -> None:
    """扩窗后拿到结构性否定证据（回放游程）时仍必须拒绝——证据与不确定分开。"""

    def label_at(ts: float) -> str:
        if ts < 20.0:
            return "unknown"     # 前 15s 窗口只有 unknown ⇒ 先扩窗
        if ts < 45.0:
            return "replay"      # 扩到 35s 后看到回放游程 ⇒ 有证据的否定
        return "combat"

    calls: list = []
    _install_label_ocr(monkeypatch, label_at, calls)
    cache: dict = {}
    sink: list = []

    item, rounds = _drive_audit_to_terminal(
        _weak_end_candidate("round-000015"),
        cache,
        available_end=320.0,
        sink=sink,
    )

    assert item["broadcast_audit"] == "rejected_no_stable_combat_start"
    assert item["broadcast_start_gate"] == "no_stable_combat"
    assert item["broadcast_start_gate_detail"] == "non_combat_in_gate_window"
    assert item.get("start_review_required") is not True or item.get("start_quality") != "precise"
    assert sink and sink[-1].status == "rejected"
    assert sink[-1].reason == "no_stable_combat_start"
    assert cache["round-000015"].get("start_gate_rejected") is True
    assert cache["round-000015"].get("start_gate_inconclusive") is not True
    assert (15.0, 35.0) in calls, calls
    assert rounds <= 6, f"扩窗后应立刻定稿（实际 {rounds} 轮）"


def test_finalize_scan_without_candidates_must_not_fail(monkeypatch, tmp_path, caplog) -> None:
    """收尾扫描窗 0 候选时不得把整窗判失败（2026-09-15 真机事故）。

    现场：收尾最后一个窗口 1970.1-1978.1 的 OCR 本就 0 回合（直播沿已被前瞻
    窗扫过），`_audit_t0` 只在 `if audit_batch:` 内赋值，却在外层无条件参与
    耗时打点 ⇒ UnboundLocalError 被兜底 except 吞成 "scan_window failed"、
    scan_succeeded 置 False ⇒ 上层按 `_ScanWindowRetryError` 重试 3 次后放弃
    收尾（phase=error，17 段停在待确认），「完成后生成剪映草稿」永不触发。

    空候选窗是常态（每轮扫描都可能没有新回合），必须判成功。
    """
    import logging as _logging

    sweep_calls: list = []
    video = _sweep_harness(
        monkeypatch, tmp_path,
        ocr_rounds_per_call=[[]],
        sweep_calls=sweep_calls,
    )
    state = _finalize_scan_state({})
    state["current_dur"] = 1978.1

    with caplog.at_level(_logging.WARNING, logger="lsc.analyzer.valorant_plugin"):
        result = ValorantAnalyzerPlugin().scan_window(
            str(video),
            ScanWindow(start_sec=1970.1, end_sec=1978.1, timeout_sec=360.0, use_ocr=True),
            state,
        )

    messages = [r.getMessage() for r in caplog.records]
    assert not any("scan_window failed" in m for m in messages), messages
    assert state["scan_succeeded"] is True, "空候选窗也是成功扫描，不得判失败"
    assert state["last_analyzed"] == 1978.1
    assert result == []
