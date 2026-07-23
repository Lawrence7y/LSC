from __future__ import annotations

from typing import Any

from lsc.analyzer.base import AnalyzerCapabilities, ScanWindow


class GenericAnalyzerPlugin:
    game = "generic"
    display_name = "通用场景"

    def capabilities(self) -> AnalyzerCapabilities:
        return AnalyzerCapabilities(
            realtime_continuous=False,
            posthoc_file=True,
            needs_ocr=False,
            needs_audio=False,
            game_specific=False,
        )

    def analyze_file(
        self,
        video_path: str,
        *,
        progress_callback=None,
        cancel_check=None,
        options: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]] | None:
        # B1: 占位返回 []；B2 迁入 _run_scene_analysis
        if cancel_check and cancel_check():
            return None
        return []

    def plan_scan_window(
        self,
        state: dict[str, Any],
        current_dur: float,
        pressure: dict[str, Any],
    ) -> ScanWindow:
        last = float(state.get("last_analyzed", 0.0) or 0.0)
        lookback = float(pressure.get("lookback_sec", 240.0))
        if last <= 0.0:
            start, end = 0.0, float(current_dur)
        else:
            start = max(0.0, last - lookback)
            end = float(current_dur)
        return ScanWindow(start_sec=start, end_sec=end, timeout_sec=60.0, use_ocr=False)

    def scan_window(
        self,
        video_path: str,
        window: ScanWindow,
        state: dict[str, Any],
        *,
        cancel_check=None,
    ) -> list[dict[str, Any]]:
        # B1 占位；B2 接音频节奏 / scene 增量
        state["last_analyzed"] = window.end_sec
        return []
