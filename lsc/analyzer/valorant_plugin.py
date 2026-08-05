"""Valorant analyzer plugin: 纯 OCR 回合检测（持续分析 + 文件分析统一）。"""
from __future__ import annotations

import logging
import os
from collections.abc import Callable
from typing import Any

from lsc.analyzer.base import AnalyzerCapabilities, ScanWindow

_log = logging.getLogger(__name__)

# 增量回看：FSM/锚点跨窗口持久化 + last_processed_ts 去重，回看窗只承担 seek 稳定性缓冲
INCREMENTAL_LOOKBACK_SEC = 30.0
MAX_CATCHUP_SEC = 480.0


def window_scan_timeout(scan_duration_sec: float, *, use_ocr: bool) -> int:
    """单窗扫描超时（秒）：OCR 双区域抽检在负载下常需 1.5–2× 窗长。"""
    dur = max(1.0, float(scan_duration_sec))
    if not use_ocr:
        return int(max(45, int(dur / 180.0 * 12) + 45))
    return int(min(900, max(120, int(dur * 2.0) + 90)))


def compute_valorant_scan_budget(
    mode: str,
    last_analyzed: float,
    current_dur: float,
    pressure: dict[str, Any] | None = None,
) -> tuple[tuple[float, float], bool, int, bool]:
    """增量扫描预算：从已分析点回看 lookback 再向前追赶，绝不跳窗漏扫。"""
    del pressure
    full_rescan = last_analyzed <= 0.0
    if full_rescan:
        scan_start = 0.0
        scan_end = float(current_dur)
    else:
        scan_start = max(0.0, float(last_analyzed) - INCREMENTAL_LOOKBACK_SEC)
        scan_end = min(float(current_dur), float(last_analyzed) + MAX_CATCHUP_SEC)
        if scan_end < scan_start:
            scan_end = float(current_dur)
    scan_range = (round(scan_start, 3), round(float(scan_end), 3))
    scan_duration = max(1.0, scan_range[1] - scan_range[0])
    timeout = window_scan_timeout(scan_duration, use_ocr=True)
    return scan_range, True, timeout, full_rescan


class ValorantAnalyzerPlugin:
    game = "valorant"
    display_name = "Valorant"

    def capabilities(self) -> AnalyzerCapabilities:
        return AnalyzerCapabilities(
            realtime_continuous=True,
            posthoc_file=True,
            needs_ocr=True,
            needs_audio=False,
            game_specific=True,
        )

    def analyze_file(
        self,
        video_path: str,
        *,
        progress_callback: Callable[[str, float, str], None] | None = None,
        cancel_check: Callable[[], bool] | None = None,
        options: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]] | None:
        """录制完成后的全文件分析（与持续分析共用纯 OCR 检测器）。"""
        if cancel_check and cancel_check():
            return None
        options = options or {}
        from lsc.analyzer.valorant_ocr_rounds import detect_valorant_rounds_ocr

        try:
            if progress_callback:
                progress_callback("round_detect", 0.0, "OCR 回合检测中...")
            return detect_valorant_rounds_ocr(
                video_path,
                ffmpeg_path=options.get("ffmpeg_path") or "ffmpeg",
                cancel_check=cancel_check,
                progress_callback=progress_callback,
                finalize=True,
            )
        except Exception as exc:
            _log.warning("Valorant OCR analyze_file failed: %s", exc)
            return None

    def plan_scan_window(
        self,
        state: dict[str, Any],
        current_dur: float,
        pressure: dict[str, Any],
    ) -> ScanWindow:
        scan_range, use_ocr, timeout, full_rescan = compute_valorant_scan_budget(
            mode=state.get("mode", "valorant_round"),
            last_analyzed=float(state.get("last_analyzed", 0.0) or 0.0),
            current_dur=current_dur,
            pressure=pressure,
        )
        state["full_rescan"] = full_rescan
        start, end = scan_range
        return ScanWindow(
            start_sec=float(start),
            end_sec=float(end),
            timeout_sec=float(timeout),
            use_ocr=bool(use_ocr),
        )

    def scan_window(
        self,
        video_path: str,
        window: ScanWindow,
        state: dict[str, Any],
        *,
        cancel_check: Callable[[], bool] | None = None,
    ) -> list[dict[str, Any]]:
        """持续分析增量扫描：调纯 OCR 检测器。"""
        # Contract / placeholder paths: skip on empty/missing files.
        try:
            if not os.path.isfile(video_path) or os.path.getsize(video_path) <= 0:
                state["last_analyzed"] = window.end_sec
                return []
        except OSError:
            state["last_analyzed"] = window.end_sec
            return []
        from lsc.analyzer.valorant_ocr_rounds import detect_valorant_rounds_ocr

        try:
            rounds = detect_valorant_rounds_ocr(
                video_path,
                time_range=(window.start_sec, window.end_sec),
                ffmpeg_path=state.get("ffmpeg_path") or "ffmpeg",
                cancel_check=cancel_check,
                progress_callback=state.get("progress_callback"),
                runtime_state=state.get("runtime_state"),
                finalize=bool(state.get("finalize", False)),
                source_profile=state.get("valorant_profile"),
            )
        except Exception as exc:
            _log.warning("Valorant OCR scan_window failed: %s", exc)
            rounds = []
        state["last_analyzed"] = window.end_sec
        return rounds or []
