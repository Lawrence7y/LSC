from __future__ import annotations

import math

import numpy as np

import lsc.analyzer.valorant_broadcast as broadcast
import lsc.analyzer.valorant_ocr_rounds as ocr_rounds
from lsc.analyzer.base import ScanWindow
from lsc.analyzer.valorant_broadcast import (
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
    assert second[0]["broadcast_audit"] == "passed"
    # 在线入点门禁只单独扫头部 [0,10]；尾部审计仍从候选尾窗开始 [0,20]，
    # 重试时从缓存末尾 [19,55]。next_prep 强出点缩短 lookahead 到 45s。
    assert calls == [(0.0, 10.0), (0.0, 20.0), (19.0, 55.0)]


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



def test_broadcast_audit_pass_keeps_next_prep_end_by(monkeypatch) -> None:
    """OCR 已给 next_prep（vision_confirmed）时，审计通过不得把 end_by 改坏。"""
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

    assert result[0]["broadcast_audit"] == "passed"
    assert result[0]["end_by"] == "next_prep"
    assert result[0]["confirm_status"] == "vision_confirmed"


def test_broadcast_fallback_rescans_only_unscanned_head(monkeypatch) -> None:
    """提速回归：尾窗无 combat 触发兜底全扫时，只补抽未扫描的头部
    [start, extract_start]，不重抽已在 samples 的尾窗（避免重复抽帧+推理）。"""
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
        start = float(kwargs["start_sec"])
        end = float(kwargs["end_sec"])
        calls.append((start, end))
        return [
            (float(ts), np.full((8, 8, 3), int(ts * 10) % 255, dtype=np.uint8))
            for ts in np.arange(start, end + 0.01, 1.0)
        ]

    monkeypatch.setattr(ocr_rounds, "extract_frames_cancellable", fake_extract)
    monkeypatch.setattr(ocr_rounds, "_read_top_anchors", lambda image: (None, None, None))

    audit_broadcast_rounds(
        [{
            "start": 0.0, "end": 50.0,
            # 已具备精修入点，跳过在线入点门禁，聚焦验证尾部兜底全扫
            "start_delta": 0.2, "start_confidence": 0.95, "start_refined": 0.0,
        }],
        "unused.mp4",
        classifier=FakeClassifier(),
        available_end=200.0,
    )

    # 主抽帧尾窗 [20, 140]；兜底只补抽头部 [0, 20]，而非重抽整段 [0, 140]
    assert (20.0, 140.0) in calls
    assert (0.0, 20.0) in calls
    assert (0.0, 140.0) not in calls


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
    assert audited_count == 1
    assert len(res) == 1
    assert len(state["runtime_state"]["broadcast_pending_rounds"]) == 2



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


def test_start_gate_moves_non_combat_start_to_stable_combat_on_finalize(monkeypatch) -> None:
    """收尾审计时，候选前 3s 是 replay/result 等非交战画面时，入点必须后移。"""
    _LABELS = ("non_game", "buy", "combat", "result", "replay")
    calls = []

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
        calls.append((start, end))
        frames = []
        for ts in np.arange(start, end + 0.01, 1.0):
            label = label_at(float(ts))
            img = np.full((8, 8, 3), int(float(ts) * 10) % 255, dtype=np.uint8)
            img[..., 0] = _LABELS.index(label)
            frames.append((float(ts), img))
        return frames

    monkeypatch.setattr(ocr_rounds, "extract_frames_cancellable", fake_extract)
    monkeypatch.setattr(ocr_rounds, "_read_top_anchors", lambda image: (None, None, None))

    res = audit_broadcast_rounds(
        [{"start": 0.0, "end": 20.0}],
        "unused.mp4",
        classifier=FakeClassifier(),
        finalize=True,
    )

    assert len(res) == 1
    assert res[0]["start"] == 3.0
    assert res[0]["start_refined"] == 3.0
    assert res[0]["broadcast_start_gate"] == "moved_from_non_combat"
    # 1fps 视觉门禁只负责后移，不伪造精修 delta，仍应要求人工复核
    assert res[0]["start_delta"] is None
    assert res[0]["broadcast_review_required"] is True


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


def test_online_start_gate_moves_replay_head_to_combat(monkeypatch) -> None:
    """Step 1：在线审计（available_end 已存在、finalize=False）也必须执行入点
    门禁——回放开头候选后移到首个稳定 combat，强停时不再放行回放入点切片。"""
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

    result = audit_broadcast_rounds(
        [{"start": 0.0, "end": 20.0}],
        "unused.mp4",
        classifier=FakeClassifier(),
        available_end=100.0,
    )

    assert len(result) == 1
    assert result[0]["start"] == 3.0
    assert result[0]["broadcast_start_gate"] == "moved_from_non_combat"
    assert result[0]["start_delta"] is None


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


def test_online_start_gate_persists_moved_start_across_pending_retry(monkeypatch) -> None:
    """Step 1：在线首轮后移入点后返回 pending_lookahead，重试时必须复用后移
    后的起点，不能退回原始错误起点。"""
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
    candidate = {"start": 0.0, "end": 20.0}
    first = audit_broadcast_rounds(
        [candidate],
        "unused.mp4",
        classifier=FakeClassifier(),
        available_end=30.0,  # 头部已覆盖、尾部未写满 → pending_lookahead
        audit_cache=cache,
    )
    assert len(first) == 1
    assert first[0]["broadcast_audit"] == "pending_lookahead"
    assert first[0]["start"] == 3.0
    assert first[0]["broadcast_start_gate"] == "moved_from_non_combat"

    second = audit_broadcast_rounds(
        [candidate],
        "unused.mp4",
        classifier=FakeClassifier(),
        available_end=200.0,
        audit_cache=cache,
    )
    assert len(second) == 1
    # 重试必须复用后移后的入点，而不是退回 0.0
    assert second[0]["start"] == 3.0
    assert second[0]["broadcast_start_gate"] == "moved_from_non_combat"
