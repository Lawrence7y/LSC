"""AI 高光分析模块。

提供基于多模态（语音 + 视觉 + 场景）的直播高光片段自动识别能力。
通过 faster-whisper 语音转录、open-clip-torch 视觉嵌入和规则评分，
融合多维度信号检测直播中的精彩瞬间。

主要入口::

    from lsc.analyzer import HighlightAnalyzer

    analyzer = HighlightAnalyzer()
    highlights = analyzer.analyze("recording.mp4")
"""
from __future__ import annotations

from lsc.analyzer.pipeline import HighlightAnalyzer

__all__ = ["HighlightAnalyzer"]
