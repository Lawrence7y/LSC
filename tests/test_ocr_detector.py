"""OCR 检测器单元测试。

覆盖 _BUY_PHASE_PATTERNS 常量的正确性。
"""
from __future__ import annotations

import sys
import os

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from lsc.analyzer.ocr_detector import _BUY_PHASE_PATTERNS


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
