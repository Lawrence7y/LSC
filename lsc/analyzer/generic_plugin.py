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
        if cancel_check and cancel_check():
            return None
        from lsc.analyzer.scene_analysis import run_scene_analysis

        opts = options or {}
        return run_scene_analysis(
            video_path,
            threshold=float(opts.get("threshold", 0.3)),
            min_duration=float(opts.get("min_duration", 3.0)),
            progress_callback=progress_callback,
            cancel_check=cancel_check,
            time_range=opts.get("time_range"),
            enable_ocr=bool(opts.get("enable_ocr", True)),
        )

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
        # Continuous scene still uses handler audio-rhythm path until explicitly migrated.
        state["last_analyzed"] = window.end_sec
        return []
