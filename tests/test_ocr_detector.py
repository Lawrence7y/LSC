"""OCR 检测器单元测试。

覆盖 _BUY_PHASE_PATTERNS 常量的正确性。
"""
from __future__ import annotations

import os
import sys
import types

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from lsc.analyzer.ocr_detector import _BUY_PHASE_PATTERNS, _get_video_resolution


def test_ocr_detector_module_imports_with_threading_lock() -> None:
    """回归：_ocr_lock 依赖 threading；缺 import 会导致 OCR 全线回退纯音频。"""
    import threading

    from lsc.analyzer import ocr_detector as od

    assert isinstance(od._ocr_lock, type(threading.Lock()))


# ──────────────────────────────────────────────────────────────────────
# _BUY_PHASE_PATTERNS 常量测试
# ──────────────────────────────────────────────────────────────────────


class TestBuyPhasePatterns:
    """测试买枪期关键词模式是否正确定义。"""

    def test_patterns_exist_and_nonempty(self) -> None:
        """_BUY_PHASE_PATTERNS 应存在且非空。"""
        assert _BUY_PHASE_PATTERNS is not None
        assert len(_BUY_PHASE_PATTERNS) > 0

    def test_patterns_are_compiled_regex(self) -> None:
        """每个元素应为编译后的正则表达式。"""
        for pattern in _BUY_PHASE_PATTERNS:
            assert hasattr(pattern, "search"), "pattern 应有 search 方法"
            assert hasattr(pattern, "match"), "pattern 应有 match 方法"

    @pytest.mark.parametrize("text", [
        "buy", "Buy Phase", "BUY",
        "equip", "Equip Weapon",
        "preparation", "preparing",
    ])
    def test_patterns_match_expected_keywords(self, text: str) -> None:
        """验证关键英文词 buy/equip/prepar 能被匹配。"""
        matched = any(p.search(text) for p in _BUY_PHASE_PATTERNS)
        assert matched, f"'{text}' 应被 _BUY_PHASE_PATTERNS 匹配"

    @pytest.mark.parametrize("text", [
        "Round 5", "Phase 3", "eliminated", "headshot", "12 vs 10",
    ])
    def test_patterns_do_not_match_unrelated_text(self, text: str) -> None:
        """验证无关文字不会被误匹配。"""
        matched = any(p.search(text) for p in _BUY_PHASE_PATTERNS)
        assert not matched, f"'{text}' 不应被 _BUY_PHASE_PATTERNS 匹配"


class TestVideoResolutionProbe:
    def test_get_video_resolution_returns_none_on_probe_failure(self, monkeypatch) -> None:
        """探测失败时返回 None，禁止静默回退 1080p。"""

        def fake_run_hidden(*_args, **_kwargs):
            class _Result:
                stderr = "no video stream\n"
            return _Result()

        monkeypatch.setattr("lsc.analyzer.ocr_detector.run_hidden", fake_run_hidden)
        assert _get_video_resolution("fake.mp4", "ffmpeg") is None

    def test_detect_kill_events_skips_when_resolution_unavailable(
        self, monkeypatch, caplog
    ) -> None:
        """分辨率不可用时跳过 OCR，不调用裁剪/OCR 管线。"""
        import logging

        from lsc.analyzer import ocr_detector as od

        fake_rapidocr = types.ModuleType("rapidocr_onnxruntime")
        fake_rapidocr.RapidOCR = object
        monkeypatch.setitem(sys.modules, "rapidocr_onnxruntime", fake_rapidocr)
        monkeypatch.setattr(od, "_get_video_resolution", lambda *_a, **_k: None)
        ocr_called: list[bool] = []
        monkeypatch.setattr(
            od, "_get_ocr", lambda: ocr_called.append(True) or None
        )

        with caplog.at_level(logging.WARNING):
            result = od.detect_kill_events("fake.mp4", ffmpeg_path="ffmpeg")

        assert result == []
        assert not ocr_called
        assert any("ocr_unavailable" in r.message for r in caplog.records)


class TestDetectKillEventsCancel:
    def test_cancel_check_exits_on_second_call(self, monkeypatch, tmp_path) -> None:
        """cancel_check 第二次返回 True 时主循环提前结束。"""
        import types

        from lsc.analyzer import ocr_detector as od

        fake_rapidocr = types.ModuleType("rapidocr_onnxruntime")
        fake_rapidocr.RapidOCR = object
        monkeypatch.setitem(sys.modules, "rapidocr_onnxruntime", fake_rapidocr)
        monkeypatch.setattr(od, "_get_video_resolution", lambda *_a, **_k: (1920, 1080))

        class _FakeResult:
            stderr = "pts_time:0.0\npts_time:0.5\npts_time:1.0\n"

        monkeypatch.setattr(
            od, "run_ffmpeg_with_hwaccel_fallback",
            lambda *_a, **_k: _FakeResult(),
        )

        ocr_calls: list[str] = []

        class _FakeOcr:
            def __call__(self, fpath: str):
                ocr_calls.append(fpath)
                return ([], None)

        monkeypatch.setattr(od, "_get_ocr", lambda: _FakeOcr())

        from PIL import Image

        real_mkdtemp = od.tempfile.mkdtemp

        def fake_mkdtemp(prefix=""):
            d = real_mkdtemp(prefix=prefix)
            for i in range(3):
                Image.new("L", (10, 10), color=128).save(
                    os.path.join(d, f"frame_{i:05d}.jpg"), format="JPEG",
                )
            return d

        monkeypatch.setattr(od.tempfile, "mkdtemp", fake_mkdtemp)

        cancel_calls = {"n": 0}

        def cancel_check() -> bool:
            cancel_calls["n"] += 1
            return cancel_calls["n"] >= 2

        result = od.detect_kill_events(
            "fake.mp4",
            ffmpeg_path="ffmpeg",
            cancel_check=cancel_check,
            game="apex",  # 无 round_marker 第二遍，仅测主循环
        )

        assert result == []
        assert cancel_calls["n"] == 2
        assert len(ocr_calls) == 1  # 仅处理第 1 帧后于第 2 次迭代取消
