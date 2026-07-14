"""Onset early-return must OCR-refine when refine_with_ocr=True even if time_range set.

回归测试：收尾路径（time_range 非 None）在 Onset fallback 时仍应调用
_refine_rounds_with_ocr，否则收尾回合永远无法 OCR 精修。
"""
from __future__ import annotations

import sys
import types
from unittest.mock import patch, MagicMock

import numpy as np
import pytest

from lsc.analyzer.round_detector import detect_valorant_rounds, ValorantRoundConfig


def _make_fake_onset_module():
    """构造 fake onset_detector 模块，返回 2 个战斗段。"""
    mod = types.ModuleType("lsc.analyzer.onset_detector")
    mod.compute_spectral_flux = lambda samples, rate: (np.ones(10), 1)
    mod.detect_onset_events = lambda flux, rate: [
        {"timestamp": 10.0},
        {"timestamp": 80.0},
    ]
    mod.aggregate_onsets_to_combat_segments = lambda events, duration: [
        {"start": 5, "end": 70},
        {"start": 80, "end": 140},
    ]
    return mod


def test_onset_path_refines_with_ocr_when_time_range_set():
    """Onset 路径在有 time_range 时仍应调用 OCR refine（scan_range is None 守卫已删）。"""
    fake_onset = _make_fake_onset_module()
    total_sec = 250

    # 1 个战斗段覆盖 220/250 = 88% → 触发 onset fallback
    onset_combat = [(30, 250)]

    mock_refine = MagicMock(return_value=[
        {"start": 105.0, "end": 170.0, "score": 0.8, "reason": "R1",
         "phase": "combat", "round_index": 1, "tail_by": "ocr"},
        {"start": 180.0, "end": 240.0, "score": 0.7, "reason": "R2",
         "phase": "combat", "round_index": 2, "tail_by": "ocr"},
    ])

    fake_pcm = np.ones(total_sec * 16000, dtype=np.float32) * 100

    with (
        patch.dict(sys.modules, {"lsc.analyzer.onset_detector": fake_onset}),
        patch("lsc.analyzer.round_detector._get_duration", return_value=400.0),
        patch("lsc.analyzer.round_detector._extract_audio_pcm",
              return_value=(fake_pcm, 16000)),
        patch("lsc.analyzer.round_detector._compute_rms_envelope",
              return_value=np.ones(total_sec, dtype=np.float32)),
        patch("lsc.analyzer.round_detector._find_combat_segments",
              return_value=onset_combat),
        patch("lsc.analyzer.round_detector._detect_chimes_from_samples",
              return_value=[]),
        patch("lsc.analyzer.round_detector._refine_rounds_with_ocr", mock_refine),
        patch("os.path.isfile", return_value=True),
    ):
        result = detect_valorant_rounds(
            "fake.mp4",
            ffmpeg_path="ffmpeg",
            duration=400.0,
            refine_with_ocr=True,
            time_range=(100.0, 350.0),
        )

    # 核心断言：_refine_rounds_with_ocr 必须被调用
    mock_refine.assert_called_once()
    assert len(result) >= 1, "Onset + OCR refine 应产出至少 1 个回合"


def test_onset_path_still_refines_without_time_range():
    """无 time_range 时 onset 路径的 OCR refine 应继续工作（回归保护）。"""
    fake_onset = _make_fake_onset_module()
    total_sec = 250

    onset_combat = [(30, 250)]

    mock_refine = MagicMock(return_value=[
        {"start": 5.0, "end": 70.0, "score": 0.8, "reason": "R1",
         "phase": "combat", "round_index": 1, "tail_by": "ocr"},
    ])

    fake_pcm = np.ones(total_sec * 16000, dtype=np.float32) * 100

    with (
        patch.dict(sys.modules, {"lsc.analyzer.onset_detector": fake_onset}),
        patch("lsc.analyzer.round_detector._get_duration", return_value=250.0),
        patch("lsc.analyzer.round_detector._extract_audio_pcm",
              return_value=(fake_pcm, 16000)),
        patch("lsc.analyzer.round_detector._compute_rms_envelope",
              return_value=np.ones(total_sec, dtype=np.float32)),
        patch("lsc.analyzer.round_detector._find_combat_segments",
              return_value=onset_combat),
        patch("lsc.analyzer.round_detector._detect_chimes_from_samples",
              return_value=[]),
        patch("lsc.analyzer.round_detector._refine_rounds_with_ocr", mock_refine),
        patch("os.path.isfile", return_value=True),
    ):
        result = detect_valorant_rounds(
            "fake.mp4",
            ffmpeg_path="ffmpeg",
            duration=250.0,
            refine_with_ocr=True,
        )

    mock_refine.assert_called_once()
    assert len(result) >= 1


def test_source_no_scan_range_guard_on_onset_ocr_path():
    """源码不应再包含 'scan_range is None' 守卫阻止 onset OCR refine。"""
    import inspect
    from lsc.analyzer import round_detector
    source = inspect.getsource(round_detector.detect_valorant_rounds)
    # 旧代码模式: results0 and scan_range is None
    # 修复后不应出现
    assert "results0 and scan_range is None" not in source, \
        "onset OCR refine 不应受 scan_range is None 守卫限制"
