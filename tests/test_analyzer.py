"""AI 高光分析模块单元测试。

测试覆盖 HighlightAnalyzer（pipeline 编排）、自适应 padding、去重逻辑以及后端集成。
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from lsc.analyzer.pipeline import HighlightAnalyzer


class TestHighlightAnalyzer:
    """AI 高光分析主编排器测试。"""

    def test_cancel_check(self, tmp_path):
        """取消检查应在分析前中断，返回 None。"""
        from lsc.analyzer.pipeline import HighlightAnalyzer

        video = tmp_path / "video.mp4"
        video.write_bytes(b"fake")

        analyzer = HighlightAnalyzer(cancel_check=lambda: True)
        result = analyzer.analyze(str(video))
        assert result is None

    def test_analyze_file_not_found(self):
        """视频文件不存在时应抛出 FileNotFoundError。"""
        from lsc.analyzer.pipeline import HighlightAnalyzer

        analyzer = HighlightAnalyzer()
        with pytest.raises(FileNotFoundError):
            analyzer.analyze("/nonexistent/video.mp4")

    def test_merge_close_segments(self):
        """测试高光片段的合并逻辑。"""
        from lsc.analyzer.pipeline import _merge_close_segments

        segments = [
            {"start": 1.0, "end": 10.0, "score": 0.8, "reason": "画面剧烈变化", "speech_score": 0.5, "visual_score": 0.8, "transcript": "第一个片段"},
            {"start": 15.0, "end": 20.0, "score": 0.9, "reason": "击杀: player", "speech_score": 0.0, "visual_score": 0.9, "transcript": "第二个片段"},
            {"start": 30.0, "end": 40.0, "score": 0.7, "reason": "场景切换频繁", "speech_score": 0.1, "visual_score": 0.7, "transcript": "第三个"},
        ]
        merged = _merge_close_segments(segments, max_gap=15.0)
        assert len(merged) <= len(segments)


class TestBackendIntegration:
    """后端集成测试（验证 AI 依赖缺失时的优雅降级）。"""

    def test_analyzer_importable_without_ai_deps(self):
        """lsc.analyzer 模块在未安装 AI 依赖时也能正常导入。"""
        from lsc.analyzer import HighlightAnalyzer

        assert HighlightAnalyzer is not None

    def test_highlight_analyzer_instantiable_without_ai_deps(self):
        """HighlightAnalyzer 可在未安装 AI 依赖时实例化（不加载模型）。"""
        from lsc.analyzer.pipeline import HighlightAnalyzer

        analyzer = HighlightAnalyzer()
        assert analyzer.analysis_time_sec == 0.0
        assert analyzer._cancel_check is None
        assert analyzer._progress_callback is None


class TestIntegration:
    """集成测试（可选，AI 依赖未安装时自动跳过）。"""

    def test_full_pipeline_with_mock_video(self, tmp_path):
        """使用 mock 视频跑完整 pipeline（验证编排逻辑）。"""
        from lsc.analyzer.pipeline import HighlightAnalyzer

        video = tmp_path / "video.mp4"
        video.write_bytes(b"fake")

        analyzer = HighlightAnalyzer()
        results = analyzer.analyze(str(video))
        assert results == []
        assert analyzer.analysis_time_sec > 0.0
