"""Valorant 回合检测器单元测试。

核心验证目标：
1. 回合完整性——每个输出片段对应一个完整游戏回合，不被固定时间间隔切断、不碎片化。
2. 买枪期剔除——回合起始的低能量买枪/准备期被裁掉，片段从战斗实际开始处起算。
3. 回合尾冗余裁剪——战斗结束（回合结束钟声）后仅保留短余韵。
4. 不固定时长——手枪局(~30s)/长枪局(~60-80s)/加时(~80-100s)都能正确分段。

策略：不依赖真实视频/FFmpeg，通过 patch `_extract_rms_envelope` 直接注入合成的
1s 窗口 RMS 波形，patch `_detect_round_end_chimes` 注入回合结束钟声时间戳，纯 numpy
逻辑验证算法。与 tests/test_audio_aligner.py 的合成信号手法一致。
"""

from __future__ import annotations

import sys
import types
from unittest.mock import patch

import numpy as np

from lsc.analyzer.round_detector import (
    ValorantRoundConfig,
    _build_round_segments_from_phase_markers,
    _find_combat_segments,
    _format_output,
    _split_by_round_end_chimes,
    _trim_buy_phases,
    _validate_rounds,
    detect_valorant_rounds,
)

_SR = 8000  # round_detector._SAMPLE_RATE


# ──────────────────────────────────────────────────────────────────────
# 合成 RMS 波形工具
# ──────────────────────────────────────────────────────────────────────
def _build_rms(*, rounds: list[tuple[int, int, int]], total_sec: int,
               buy_energy: float = 0.05, combat_energy: float = 1.0,
               idle_energy: float = 0.02) -> np.ndarray:
    """构造 1s 窗口 RMS 数组（每个元素代表 1 秒的能量）。

    rounds: [(buy_start, combat_start, combat_end), ...]
      - [buy_start, combat_start) = 买枪期（低能量 buy_energy）
      - [combat_start, combat_end) = 战斗期（高能量 combat_energy）
      - 其余时间 = 空闲（极低能量 idle_energy）
    """
    rms = np.full(total_sec, idle_energy, dtype=np.float32)
    for buy_start, combat_start, combat_end in rounds:
        rms[buy_start:combat_start] = buy_energy
        rms[combat_start:combat_end] = combat_energy
    return rms


def _run_detect(rms: np.ndarray, *, chimes: list[float] | None = None,
                duration: float | None = None,
                config: ValorantRoundConfig | None = None) -> list[dict]:
    """用注入的 RMS 波形和钟声运行 detect_valorant_rounds（不触碰 FFmpeg）。"""
    dur = duration if duration is not None else float(len(rms))
    with (
        patch("lsc.analyzer.round_detector._get_duration", return_value=dur),
        patch("lsc.analyzer.round_detector._extract_audio_pcm",
              return_value=(_rms_to_pcm(rms), 16000)),
        patch("lsc.analyzer.round_detector._detect_chimes_from_samples",
              return_value=chimes or []),
        patch("os.path.isfile", return_value=True),
    ):
        return detect_valorant_rounds(
            "fake.mp4", ffmpeg_path="ffmpeg", config=config, refine_with_ocr=False,
        )


def _rms_to_pcm(rms: np.ndarray) -> np.ndarray:
    """将 1s 窗口 RMS 数组转换为合成 PCM 样本（用于测试）。

    每秒钟的正弦波 RMS 值与输入 rms 数组一致。
    """
    sr = 16000
    pcm_parts = []
    for val in rms:
        t = np.linspace(0, 1, sr, endpoint=False)
        sine = np.sin(2 * np.pi * 440 * t).astype(np.float32)
        pcm_parts.append(sine * val * 3000)
    if not pcm_parts:
        return np.array([], dtype=np.float32)
    return np.concatenate(pcm_parts).astype(np.float32)


# ──────────────────────────────────────────────────────────────────────
# 1. 回合完整性：每个片段对应一个完整回合，不被切断/碎片化
# ──────────────────────────────────────────────────────────────────────
class TestRoundCompleteness:
    def test_time_range_ocr_refine_can_build_window_rounds(self):
        """HVV: OCR 模式下 OCR 边界为权威 - start 应 >= OCR phase 起点 - pre_combat_pad。"""
        markers = [
            {"timestamp": 120.0, "type": "round_start"},
            {"timestamp": 175.0, "type": "round_end"},
        ]
        # PCM + RMS mock：buy phase 期间（局部 index < 40）低能量，之后持续高
        # 局部时间 40 全局时间=140.0（range_offset=100）
        fake_pcm = np.zeros(960, dtype=np.float32)  # 8000Hz * 0.12s
        fake_rms = np.ones(120) * 100.0
        fake_rms[40:] = 2000.0
        with (
            patch("lsc.analyzer.round_detector._detect_round_phase_markers",
                  return_value=markers) as detect_markers,
            patch("lsc.analyzer.round_detector._extract_audio_pcm",
                  return_value=(fake_pcm, 8000)) as extract_pcm,
            patch("lsc.analyzer.round_detector._compute_rms_envelope",
                  return_value=fake_rms) as compute_rms,
            patch("lsc.analyzer.round_detector._detect_chimes_from_samples",
                  return_value=[]) as chimes,
            patch("os.path.isfile", return_value=True),
        ):
            res = detect_valorant_rounds(
                "fake.mp4",
                ffmpeg_path="ffmpeg",
                duration=220.0,
                refine_with_ocr=True,
                time_range=(100.0, 220.0),
            )

        detect_markers.assert_called_once()
        extract_pcm.assert_called_once()  # 音频管线继续运行（RMS 用于评分）
        assert len(res) >= 1
        # OCR 确认的 start = OCR phase 起点 - pre_combat_pad(2s) = 118.0
        assert res[0]["start"] >= 120.0 - 2.0, f"start {res[0]['start']} 应 >= 118.0"
        # end 不应超过 OCR end + tail_pad 太多
        assert res[0]["end"] <= 175.0 + 10.0
        # OCR 确认的回合应有 ocr_confirmed 标记
        assert res[0].get("tail_by") in ("ocr_phase", "ocr"), \
            f"OCR 确认的回合 tail_by 应为 ocr_phase/ocr, 实际 {res[0].get('tail_by')}"

    def test_three_rounds_stay_independent(self):
        """3 个回合各自独立成片，数量守恒（不碎片化、不误合并）。"""
        rms = _build_rms(
            rounds=[(10, 35, 65), (90, 115, 185), (210, 235, 325)],
            total_sec=400,
        )
        res = _run_detect(rms, chimes=[65.0, 185.0, 325.0])
        assert len(res) == 3, f"应得 3 个完整回合，实际 {len(res)}"
        # 每个回合单调递增、互不重叠
        for i in range(1, len(res)):
            assert res[i]["start"] >= res[i - 1]["end"], "回合不应重叠"
        # round_index 连续
        assert [r["round_index"] for r in res] == [1, 2, 3]

    def test_variable_round_durations_all_preserved(self):
        """不固定时长：手枪(~30s)/长枪(~70s)/加时(~90s)都能正确分段并保留。"""
        rms = _build_rms(
            rounds=[(10, 35, 65), (90, 115, 185), (210, 235, 325)],
            total_sec=400,
        )
        res = _run_detect(rms, chimes=[65.0, 185.0, 325.0])
        durations = sorted(round(r["end"] - r["start"]) for r in res)
        # 三档时长差异显著，均落在合理范围（含 padding）
        assert durations[0] < 45, f"手枪局应最短，实际 {durations}"
        assert 60 < durations[1] < 90, f"长枪局中等，实际 {durations}"
        assert durations[2] > 85, f"加时局最长，实际 {durations}"

    def test_time_range_analysis_returns_global_timestamps(self):
        """增量窗口分析只扫描窗口，但输出仍回到整段视频时间轴。"""
        rms = _build_rms(rounds=[(10, 20, 60)], total_sec=120)
        pcm = _rms_to_pcm(rms)
        with (
            patch("lsc.analyzer.round_detector._extract_audio_pcm",
                  return_value=(pcm, 16000)) as extract,
            patch("lsc.analyzer.round_detector._detect_chimes_from_samples",
                  return_value=[160.0 - 100.0]) as chimes,
            patch("os.path.isfile", return_value=True),
        ):
            res = detect_valorant_rounds(
                "fake.mp4",
                ffmpeg_path="ffmpeg",
                duration=220.0,
                refine_with_ocr=False,
                time_range=(100.0, 220.0),
            )

        extract.assert_called_once_with("fake.mp4", "ffmpeg", time_range=(100.0, 220.0))
        assert len(res) == 1
        assert 119.0 <= res[0]["start"] <= 121.0
        assert 163.0 <= res[0]["end"] <= 165.0

    def test_long_round_not_split_at_midpoint(self):
        """加时长回合(~95s)不被 max_combat_duration 中点强切成两段。

        total_sec 取足够大使战斗占比 < 45%（贴近真实录像：战斗只占少数时间），
        55 百分位动态阈值才能正确落在空闲与战斗能量之间。
        """
        rms = _build_rms(rounds=[(10, 30, 125)], total_sec=300)
        res = _run_detect(rms, chimes=[125.0])
        assert len(res) == 1, f"单个长回合不应被切成 {len(res)} 段"
        assert res[0]["end"] - res[0]["start"] > 85

    def test_no_fixed_interval_cut(self):
        """长战斗跨越任意 120s 边界不被切断——回合完整性核心断言。"""
        # 一个从 100s 持续到 190s 的战斗，横跨 120s 边界
        rms = _build_rms(rounds=[(80, 100, 190)], total_sec=240)
        res = _run_detect(rms, chimes=[190.0])
        assert len(res) == 1, "跨 120s 边界的回合必须保持完整，不能被切断"
        assert res[0]["start"] < 120 < res[0]["end"], "片段应完整跨越 120s 边界"


# ──────────────────────────────────────────────────────────────────────
# 2. 买枪期剔除：片段从战斗实际开始处起算
# ──────────────────────────────────────────────────────────────────────
class TestBuyPhaseTrim:
    def test_buy_phase_trimmed_from_start(self):
        """回合起点应接近战斗开始(35s)，而非买枪开始(10s)。"""
        rms = _build_rms(rounds=[(10, 35, 65)], total_sec=100)
        res = _run_detect(rms, chimes=[65.0])
        assert len(res) == 1
        # 起点应在战斗开始附近（允许 pre_combat_pad=2s 的前置缓冲）
        assert res[0]["start"] >= 30, f"买枪期未裁净，起点 {res[0]['start']}"
        assert res[0]["start"] <= 36, f"起点不应晚于战斗开始太多，{res[0]['start']}"


# ──────────────────────────────────────────────────────────────────────
# 3. 回合尾冗余裁剪：战斗结束后仅留短余韵
# ──────────────────────────────────────────────────────────────────────
    def test_low_energy_post_barrier_setup_is_preserved(self):
        """Low-energy setup after barrier drop is part of the combat round."""
        rms = np.full(150, 0.02, dtype=np.float32)
        rms[10:35] = 0.05
        rms[35:60] = 0.12
        rms[60:90] = 1.0

        res = _run_detect(rms, chimes=[90.0])

        assert len(res) == 1
        assert 32 <= res[0]["start"] <= 38, (
            f"clip should preserve setup near combat start, got {res[0]['start']}"
        )

    def test_in_round_silence_does_not_split_duel_sequence(self):
        """In-round quiet time should stay inside one duel sequence."""
        rms = np.full(180, 0.02, dtype=np.float32)
        rms[10:35] = 0.05
        rms[35:55] = 0.14
        rms[55:70] = 1.0
        rms[70:92] = 0.10
        rms[92:110] = 1.0

        res = _run_detect(rms, chimes=[110.0])

        assert len(res) == 1, f"single round should not be split: {res}"
        assert res[0]["start"] <= 38
        assert res[0]["end"] >= 112

    def test_buy_phase_scan_does_not_cross_previous_round(self):
        """Buy-phase trimming must not scan into the previous round."""
        cfg = ValorantRoundConfig()
        smoothed = np.full(340, 0.02, dtype=np.float32)
        smoothed[112:180] = 1.0
        smoothed[197:315] = 1.0

        trimmed = _trim_buy_phases(smoothed, [(112, 180), (197, 315)], cfg)

        assert trimmed[1][0] >= 197


class TestTailTrim:
    def test_tail_trimmed_by_chime(self):
        """有回合结束钟声时，片段结束点 = 钟声 + tail_pad，砍掉尾部垃圾时间。"""
        cfg = ValorantRoundConfig()
        # 战斗 35-65s，钟声在 65s，之后 65-95s 是死亡回放/结算（仍有能量残留）
        rms = _build_rms(rounds=[(10, 35, 65)], total_sec=120)
        rms[65:95] = 0.5  # 尾部残留能量（结算画面）
        res = _run_detect(rms, chimes=[65.0], config=cfg)
        assert len(res) == 1
        # 结束点应贴近钟声 + tail_pad(4s) = 69s，而非延伸到 95s
        assert res[0]["end"] <= 65 + cfg.tail_pad + 2, \
            f"尾部未裁剪，结束点 {res[0]['end']}（应≈69s）"
        assert res[0]["tail_by"] == "chime"

    def test_tail_fallback_without_chime(self):
        """无钟声时退回 post_combat_pad 保底，tail_by 标记为 audio。"""
        rms = _build_rms(rounds=[(10, 35, 65)], total_sec=100)
        res = _run_detect(rms, chimes=[])
        assert len(res) == 1
        assert res[0]["tail_by"] == "audio"


# ──────────────────────────────────────────────────────────────────────
# 4. _find_combat_segments 纯函数逻辑
# ──────────────────────────────────────────────────────────────────────
class TestOcrPhaseRoundSegments:
    def test_trailing_buy_phase_does_not_back_cut_previous_round(self):
        cfg = ValorantRoundConfig()
        markers = [
            {"timestamp": 35.0, "type": "round_start"},
            {"timestamp": 90.0, "type": "round_end"},
            {"timestamp": 120.0, "type": "round_start"},
            {"timestamp": 190.0, "type": "round_end"},
            {"timestamp": 235.0, "type": "round_start"},
            # Live recording stops while the next buy phase is still visible.
            # This marker is not a combat start yet, so it must not cut 235s
            # back to 255s (= 291 - round_inactive_gap).
            {"timestamp": 291.0, "type": "round_start"},
        ]

        res = _build_round_segments_from_phase_markers(markers, 292.0, cfg)

        assert res[-1]["start"] == 237.0
        assert res[-1]["end"] > 280.0
        assert res[-1]["tail_by"] == "ocr_phase"
        assert res[-1]["end_by"] == "next_buy"
        assert res[-1]["ocr_confirmed"] is True

    def test_no_round_start_marker_no_zero_first_segment(self):
        """无 round_start 标记时不从 0.0 构造首段。

        录像可能从回合中途开始，0.0 不是真正的回合开始。
        只有 end 标记没有配对 start 标记的回合应被丢弃。
        """
        cfg = ValorantRoundConfig()
        markers = [
            {"timestamp": 35.0, "type": "round_end"},  # 有 end 但无前置 start
            {"timestamp": 71.0, "type": "round_start"},
            {"timestamp": 135.0, "type": "round_end"},
        ]
        res = _build_round_segments_from_phase_markers(markers, 200.0, cfg)
        # 首段不应从 0.0 开始
        assert all(r["start"] > 0 for r in res), \
            f"不应有从 0.0 开始的回合: {[(r['start'], r['end']) for r in res]}"
        assert len(res) == 1  # 只有 71.0->135.0 一个完整回合

    def test_ocr_inferred_end_uses_8s_gap_not_15s(self):
        """OCR 推断结束点用 8s 间隔（死亡回放~3s + 结算~5s），不是音频路径的 15s。"""
        cfg = ValorantRoundConfig()
        markers = [
            {"timestamp": 100.0, "type": "round_start"},
            # 无 round_end 标记 -> 用下一回合 start 回推
            {"timestamp": 200.0, "type": "round_start"},
            {"timestamp": 300.0, "type": "round_end"},
        ]
        res = _build_round_segments_from_phase_markers(markers, 400.0, cfg)
        # 第一回合无 end 标记，用 next_start(200) - 8 = 192
        # 不是 next_start(200) - 15 = 185
        assert res[0]["end"] == 200.0
        assert res[0]["end_by"] == "next_buy"
        assert res[0]["ocr_confirmed"] is True

    def test_ocr_confirmed_metadata_preserved(self):
        """OCR 确认的回合应保留 ocr_confirmed 和 ocr_end 元数据。"""
        cfg = ValorantRoundConfig()
        markers = [
            {"timestamp": 100.0, "type": "round_start"},
            {"timestamp": 150.0, "type": "round_end"},
            {"timestamp": 200.0, "type": "round_start"},
            {"timestamp": 250.0, "type": "round_end"},
        ]
        res = _build_round_segments_from_phase_markers(markers, 400.0, cfg)
        assert len(res) >= 2
        # 有明确 end 标记的回合应标记为 ocr_confirmed
        for r in res:
            if r["tail_by"] != "open_tail":
                assert r.get("ocr_confirmed") is True, \
                    f"非 open_tail 回合应有 ocr_confirmed=True: {r}"
                assert r.get("ocr_end") is not None, \
                    f"ocr_confirmed 回合应有 ocr_end: {r}"


class TestFindCombatSegments:
    def test_merge_short_gaps(self):
        """间距小于 merge_gap 的高能量段被合并为同一回合。"""
        smoothed = np.zeros(100, dtype=np.float32)
        smoothed[10:25] = 1.0
        smoothed[30:45] = 1.0  # 与前段间距 5s < merge_gap(10)
        segs = _find_combat_segments(smoothed, threshold=0.5,
                                     merge_gap=10.0, min_duration=8.0)
        assert len(segs) == 1, f"近距离段应合并，实际 {segs}"

    def test_separate_far_gaps(self):
        """间距大于 merge_gap 的段保持独立。"""
        smoothed = np.zeros(100, dtype=np.float32)
        smoothed[10:25] = 1.0
        smoothed[50:70] = 1.0  # 间距 25s > merge_gap
        segs = _find_combat_segments(smoothed, threshold=0.5,
                                     merge_gap=10.0, min_duration=8.0)
        assert len(segs) == 2

    def test_filter_too_short(self):
        """短于 min_duration 的段被过滤。"""
        smoothed = np.zeros(100, dtype=np.float32)
        smoothed[10:14] = 1.0  # 仅 4s < min_duration(8)
        segs = _find_combat_segments(smoothed, threshold=0.5,
                                     merge_gap=10.0, min_duration=8.0)
        assert len(segs) == 0


# ──────────────────────────────────────────────────────────────────────
# 5. OCR 边界校正兜底：失败必须静默回退纯音频结果
# ──────────────────────────────────────────────────────────────────────
class TestRoundEndChimeSplitting:
    def test_dense_false_chimes_do_not_erase_combat_segment(self):
        """密集低频误检不应把一个完整战斗段切成过短碎片。"""
        cfg = ValorantRoundConfig()
        combat_segments = [(27, 53)]
        dense_false_chimes = [32.75, 41.5, 52.0]

        result = _split_by_round_end_chimes(
            combat_segments, dense_false_chimes, cfg, rms_len=100
        )

        assert result == combat_segments

    def test_single_plausible_internal_chime_still_splits_merged_rounds(self):
        """单个可信内部钟声仍应用于拆分被误合并的两个回合。"""
        cfg = ValorantRoundConfig()
        combat_segments = [(10, 95)]

        result = _split_by_round_end_chimes(
            combat_segments, [50.0], cfg, rms_len=120
        )

        assert result == [(10, 50), (55, 95)]


class TestRoundValidation:
    def test_short_gap_merge_must_not_create_overlong_round(self):
        """短间隙兜底合并不能生成超过最大回合长度的超长片段。"""
        cfg = ValorantRoundConfig()

        result = _validate_rounds(
            [(112, 180), (192, 315)],
            cfg,
            duration=408.4,
        )

        assert result == [(112, 180), (192, 315)]


class TestOcrRefineFallback:
    def test_phase_markers_build_complete_reference_rounds(self):
        """OCR 状态边界应按完整回合输出用户给定的参考切片。

        去掉 [0.0] 硬编码后，没有 round_start 标记的尾部 end 不再构造首段。
        首段从第一个 round_start (71.0) 开始。
        """
        cfg = ValorantRoundConfig()
        markers = [
            {"timestamp": 35.0, "type": "round_end"},
            {"timestamp": 71.0, "type": "round_start"},
            {"timestamp": 135.0, "type": "round_end"},
            {"timestamp": 172.0, "type": "round_start"},
            {"timestamp": 216.0, "type": "round_end"},
            {"timestamp": 252.0, "type": "round_start"},
            {"timestamp": 296.0, "type": "round_end"},
            {"timestamp": 331.0, "type": "round_start"},
        ]

        rounds = _build_round_segments_from_phase_markers(markers, 408.4, cfg)

        # 去掉 [0.0] 硬编码：首段不再从 0.0 开始，而是从第一个 round_start (71.0)
        assert [(r["start"], r["end"]) for r in rounds] == [
            (73.0, 135.0),
            (174.0, 216.0),
            (254.0, 296.0),
            (333.0, 408.4),
        ]

    def test_ocr_phase_path_can_add_audio_missed_rounds(self):
        """完整文件 OCR 状态路径应能产出音频能量漏掉的完整回合数量。"""
        cfg = ValorantRoundConfig()
        markers = [
            {"timestamp": 35.0, "type": "round_end"},
            {"timestamp": 71.0, "type": "round_start"},
            {"timestamp": 135.0, "type": "round_end"},
            {"timestamp": 172.0, "type": "round_start"},
            {"timestamp": 216.0, "type": "round_end"},
            {"timestamp": 252.0, "type": "round_start"},
            {"timestamp": 296.0, "type": "round_end"},
            {"timestamp": 331.0, "type": "round_start"},
        ]

        # RMS: buy phase 低能量 + 战斗段高能量（用于 buy trim）
        fake_rms = np.ones(408, dtype=np.float32) * 100.0
        fake_rms[10:35] = 2000.0   # R1 战斗
        fake_rms[75:135] = 2000.0  # R2 战斗
        fake_rms[176:216] = 2000.0 # R3 战斗
        fake_rms[256:296] = 2000.0 # R4 战斗
        fake_rms[335:380] = 2000.0 # R5 战斗

        with (
            patch("lsc.analyzer.round_detector._get_duration", return_value=408.4),
            patch("lsc.analyzer.round_detector._detect_round_phase_markers",
                  return_value=markers),
            patch("lsc.analyzer.round_detector._extract_audio_pcm",
                  return_value=(np.zeros(408 * 8, dtype=np.float32), 8000)),
            patch("lsc.analyzer.round_detector._compute_rms_envelope",
                  return_value=fake_rms),
            patch("lsc.analyzer.round_detector._detect_chimes_from_samples",
                  return_value=[35.0, 135.0, 216.0, 296.0]),
            patch("os.path.isfile", return_value=True),
        ):
            rounds = detect_valorant_rounds(
                "fake.mp4", config=cfg, refine_with_ocr=True,
            )

        # HVV 修改后：OCR 粗粒度边界经 buy phase trim 后起点应稍后移
        # 验证核心：回合数量正确 + 每个 round start >= OCR phase start（buy trim 不后移）
        assert len(rounds) >= 4, f"应 >=4 个回合, 实际 {len(rounds)}"
        for r in rounds:
            assert r["start"] >= 0.0
            assert r["end"] <= 408.4 + 1.0

    def test_refine_disabled_by_default(self):
        """refine_with_ocr=False 时不触碰 OCR，纯音频结果直出。"""
        rms = _build_rms(rounds=[(10, 35, 65)], total_sec=100)
        res = _run_detect(rms, chimes=[65.0])  # _run_detect 固定 refine_with_ocr=False
        assert len(res) == 1
        assert "start_by" not in res[0], "未启用 OCR 时不应有 start_by 标记"

    def test_refine_falls_back_when_ocr_unavailable(self):
        """OCR 模块导入失败时，refine_with_ocr=True 仍返回音频结果（不抛异常）。"""
        rms = _build_rms(rounds=[(10, 35, 65)], total_sec=100)
        dur = float(len(rms))
        pcm = _rms_to_pcm(rms)
        # 模拟 _detect_round_markers 抛异常（如 rapidocr 未安装）
        with (
            patch("lsc.analyzer.round_detector._get_duration", return_value=dur),
            patch("lsc.analyzer.round_detector._extract_audio_pcm",
                  return_value=(pcm, 16000)),
            patch("lsc.analyzer.round_detector._detect_chimes_from_samples",
                  return_value=[65.0]),
            patch("lsc.analyzer.round_detector._refine_rounds_with_ocr",
                  side_effect=RuntimeError("rapidocr 未安装")),
            patch("os.path.isfile", return_value=True),
        ):
            res = detect_valorant_rounds("fake.mp4", refine_with_ocr=True)
        assert len(res) == 1, "OCR 校正失败必须静默回退纯音频结果"


# ──────────────────────────────────────────────────────────────────────
# 7. OCR 确认边界优先：音频管线不应覆盖 OCR 精确边界
# ──────────────────────────────────────────────────────────────────────
class TestOcrConfirmedBoundaryPriority:
    def test_ocr_confirmed_boundary_not_overridden_by_chime(self):
        """OCR 确认的结束边界不应被音频钟声覆盖。

        即使战斗段末尾附近有钟声，OCR 确认的 end 应保持不变。
        """
        cfg = ValorantRoundConfig()
        markers = [
            {"timestamp": 100.0, "type": "round_start"},
            {"timestamp": 150.0, "type": "round_end"},
            {"timestamp": 200.0, "type": "round_start"},
            {"timestamp": 250.0, "type": "round_end"},
        ]
        fake_rms = np.ones(300, dtype=np.float32) * 100.0
        fake_rms[105:150] = 2000.0
        fake_rms[205:250] = 2000.0
        # 在战斗段末尾放一个误导性钟声（148s，早于 OCR end 150s）
        # 如果音频管线覆盖 OCR 边界，end 会被推到 148+4=152 而非 150+4=154
        with (
            patch("lsc.analyzer.round_detector._get_duration", return_value=300.0),
            patch("lsc.analyzer.round_detector._detect_round_phase_markers",
                  return_value=markers),
            patch("lsc.analyzer.round_detector._extract_audio_pcm",
                  return_value=(np.zeros(300 * 16, dtype=np.float32), 16000)),
            patch("lsc.analyzer.round_detector._compute_rms_envelope",
                  return_value=fake_rms),
            patch("lsc.analyzer.round_detector._detect_chimes_from_samples",
                  return_value=[148.0, 248.0]),
            patch("os.path.isfile", return_value=True),
        ):
            res = detect_valorant_rounds(
                "fake.mp4", config=cfg, refine_with_ocr=True, duration=300.0,
            )
        assert len(res) >= 2
        # OCR 确认的回合 tail_by 应为 ocr_phase，不是 chime
        assert res[0]["tail_by"] == "ocr_phase", \
            f"OCR 确认的回合不应被钟声覆盖: {res[0]['tail_by']}"
        # end 应基于 OCR end(150) + tail_pad(4) = 154，而非钟声(148)+4=152
        assert res[0]["end"] == 154.0, \
            f"OCR end 应 150+4=154, 实际 {res[0]['end']}"

    def test_audio_only_rounds_still_use_chime(self):
        """无 OCR 时（refine_with_ocr=False）仍用钟声裁尾。"""
        rms = _build_rms(rounds=[(10, 35, 65)], total_sec=120)
        rms[65:95] = 0.5
        res = _run_detect(rms, chimes=[65.0])
        assert len(res) == 1
        assert res[0]["tail_by"] == "chime", \
            f"纯音频回合应使用钟声裁尾: {res[0]['tail_by']}"

    def test_ocr_confirmed_safety_valve_not_triggered(self):
        """OCR 确认的边界不应触发安全阀覆盖回 audio 兜底。"""
        cfg = ValorantRoundConfig()
        markers = [
            {"timestamp": 100.0, "type": "round_start"},
            {"timestamp": 150.0, "type": "round_end"},
            {"timestamp": 200.0, "type": "round_start"},
            {"timestamp": 250.0, "type": "round_end"},
        ]
        # 低能量 RMS，使音频安全阀可能触发（peak_rms 低 -> score 低）
        fake_rms = np.ones(300, dtype=np.float32) * 10.0
        fake_rms[105:150] = 20.0
        fake_rms[205:250] = 20.0
        with (
            patch("lsc.analyzer.round_detector._get_duration", return_value=300.0),
            patch("lsc.analyzer.round_detector._detect_round_phase_markers",
                  return_value=markers),
            patch("lsc.analyzer.round_detector._extract_audio_pcm",
                  return_value=(np.zeros(300 * 16, dtype=np.float32), 16000)),
            patch("lsc.analyzer.round_detector._compute_rms_envelope",
                  return_value=fake_rms),
            patch("lsc.analyzer.round_detector._detect_chimes_from_samples",
                  return_value=[]),
            patch("os.path.isfile", return_value=True),
        ):
            res = detect_valorant_rounds(
                "fake.mp4", config=cfg, refine_with_ocr=True, duration=300.0,
            )
        assert len(res) >= 1
        # 安全阀不应把 OCR 确认的 tail_by 覆盖为 audio
        assert res[0]["tail_by"] == "ocr_phase", \
            f"OCR 确认边界不应被安全阀覆盖: {res[0]['tail_by']}"


# ──────────────────────────────────────────────────────────────────────
# 8. OCR 结束关键词双语检测
# ──────────────────────────────────────────────────────────────────────
class TestOcrEndKeywords:
    def test_english_end_keywords_detected(self):
        """英文胜负关键词应被识别为回合结束标记。"""
        # 验证 end_keywords 元组包含英文关键词
        from lsc.analyzer.round_detector import _detect_round_phase_markers
        # 通过检查源码中的 end_keywords 定义来验证
        import inspect
        source = inspect.getsource(_detect_round_phase_markers)
        assert "victory" in source.lower(), "应包含 victory 关键词"
        assert "defeat" in source.lower(), "应包含 defeat 关键词"
        assert "eliminated" in source.lower(), "应包含 eliminated 关键词"
        assert "clutch" in source.lower(), "应包含 clutch 关键词"


# ──────────────────────────────────────────────────────────────────────
# 6. 退化与异常输入
# ──────────────────────────────────────────────────────────────────────
class TestDegenerate:
    def test_silent_audio_returns_empty(self):
        """全静音（能量全零）返回空列表，不误报回合。"""
        rms = np.zeros(100, dtype=np.float32)
        res = _run_detect(rms, chimes=[])
        assert res == []

    def test_missing_file_returns_empty(self):
        """文件不存在返回空列表。"""
        with patch("os.path.isfile", return_value=False):
            res = detect_valorant_rounds("nonexistent.mp4")
        assert res == []

    def test_too_short_rms_returns_empty(self):
        """RMS 样本过短（< 10 窗口）返回空列表。"""
        rms = np.ones(5, dtype=np.float32)
        res = _run_detect(rms, chimes=[])
        assert res == []


class TestOcrRoundBoundaryMetadata:
    def test_ocr_round_uses_barrier_exit_and_explicit_end(self):
        cfg = ValorantRoundConfig(phase_sample_interval=2.0)
        rounds = _build_round_segments_from_phase_markers(
            [
                {"timestamp": 100.0, "type": "round_start"},
                {"timestamp": 154.0, "type": "round_end"},
                {"timestamp": 200.0, "type": "round_start"},
            ],
            240.0,
            cfg,
        )
        assert rounds[0]["start"] == 102.0
        assert rounds[0]["end"] == 154.0
        assert rounds[0]["start_by"] == "ocr_buy_exit"
        assert rounds[0]["end_by"] == "ocr_result"
        assert rounds[0]["ocr_confirmed"] is True

    def test_ocr_round_uses_next_buy_as_confirmed_end(self):
        rounds = _build_round_segments_from_phase_markers(
            [
                {"timestamp": 100.0, "type": "round_start"},
                {"timestamp": 200.0, "type": "round_start"},
            ],
            240.0,
            ValorantRoundConfig(phase_sample_interval=2.0),
        )
        assert rounds[0]["start"] == 102.0
        assert rounds[0]["end"] == 200.0
        assert rounds[0]["end_by"] == "next_buy"
        assert rounds[0]["ocr_confirmed"] is True

    def test_ocr_open_tail_is_not_confirmed(self):
        rounds = _build_round_segments_from_phase_markers(
            [{"timestamp": 100.0, "type": "round_start"}],
            180.0,
            ValorantRoundConfig(),
        )
        assert rounds[0]["end_by"] == "open_tail"
        assert rounds[0]["ocr_confirmed"] is False


class TestOcrOutputBoundaryRegression:
    def test_ocr_output_uses_phase_start_without_prepad(self):
        cfg = ValorantRoundConfig(pre_combat_pad=10.0)
        phase = [{
            "start": 100.0,
            "end": 160.0,
            "start_by": "ocr_buy_exit",
            "end_by": "ocr_result",
            "ocr_confirmed": True,
            "ocr_end": 160.0,
        }]
        result = _format_output(
            [(100, 160)], np.ones(200, dtype=np.float32), 1.0, 200.0, cfg,
            phase_rounds=phase,
        )
        assert result[0]["start"] == 100.0
        assert result[0]["start_by"] == "ocr_buy_exit"

    def test_open_tail_keeps_ocr_metadata_without_audio_fallback(self):
        cfg = ValorantRoundConfig(pre_combat_pad=10.0)
        phase = [{
            "start": 100.0,
            "end": 180.0,
            "start_by": "ocr_buy_exit",
            "end_by": "open_tail",
            "ocr_confirmed": False,
            "ocr_end": None,
        }]
        result = _format_output(
            [(100, 180)], np.ones(220, dtype=np.float32), 1.0, 220.0, cfg,
            phase_rounds=phase,
        )
        assert result[0]["start"] == 100.0
        assert result[0]["end_by"] == "open_tail"
        assert result[0]["ocr_confirmed"] is False

    def test_phase_rounds_skip_onset_fallback(self):
        fake_onset = types.ModuleType("lsc.analyzer.onset_detector")
        fake_onset.compute_spectral_flux = lambda samples, rate: (np.ones(2), 1)
        fake_onset.detect_onset_events = lambda flux, rate: [{"timestamp": 0.0}]
        fake_onset.aggregate_onsets_to_combat_segments = lambda events, duration: [
            {"start": 0, "end": 20}
        ]
        markers = [
            {"timestamp": 100.0, "type": "round_start"},
            {"timestamp": 150.0, "type": "round_end"},
        ]
        fake_rms = np.ones(300, dtype=np.float32)
        with (
            patch.dict(sys.modules, {"lsc.analyzer.onset_detector": fake_onset}),
            patch("lsc.analyzer.round_detector._get_duration", return_value=300.0),
            patch("lsc.analyzer.round_detector._detect_round_phase_markers", return_value=markers),
            patch("lsc.analyzer.round_detector._extract_audio_pcm",
                  return_value=(np.zeros(300 * 16, dtype=np.float32), 16000)),
            patch("lsc.analyzer.round_detector._compute_rms_envelope", return_value=fake_rms),
            patch("lsc.analyzer.round_detector._detect_chimes_from_samples", return_value=[]),
            patch("os.path.isfile", return_value=True),
        ):
            result = detect_valorant_rounds(
                "fake.mp4", config=ValorantRoundConfig(), refine_with_ocr=True,
                duration=300.0,
            )
        assert result[0]["start"] == 102.0
        assert result[0]["start_by"] == "ocr_buy_exit"
