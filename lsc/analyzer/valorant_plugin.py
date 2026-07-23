"""Valorant analyzer plugin: hybrid rounds + continuous scan budget."""
from __future__ import annotations

import logging
import os
import tempfile
import wave
from collections.abc import Callable
from typing import Any

import numpy as np

from lsc.analyzer.base import AnalyzerCapabilities, ScanWindow
from lsc.utils.process_launcher import run_hidden

_log = logging.getLogger(__name__)

VALORANT_INCREMENTAL_LOOKBACK_SEC = 360.0  # 6 分钟增量回看（质量优先）
VALORANT_MAX_CATCHUP_SEC = 480.0  # 单次 tick 最多向前追赶的新内容时长


def valorant_refine_with_ocr(
    mode: str,
    pressure: dict[str, Any] | None = None,
) -> bool:
    """Return whether continuous Valorant analysis may use legacy OCR refine.

    valorant_round 已切换为混合视觉边界，不再通过 refine_with_ocr 升格旧 OCR 路径。
    """
    return False


def window_scan_timeout(scan_duration_sec: float, *, use_ocr: bool) -> int:
    """单窗扫描超时（秒）。

    纯音频可近实时；OCR 帧抽检在负载下常需 1.5–2× 窗长。
    对照实测：相位短窗旧公式 ``dur/180*12+45`` 对 80–117s 窗只给 ~49–52s，
    OCR TimeoutError 后降级纯音频，待确认永远无法升格。
    """
    dur = max(1.0, float(scan_duration_sec))
    if not use_ocr:
        return int(max(45, int(dur / 180.0 * 12) + 45))
    # 2× 窗长 + 90s 余量；夹在 2–15 分钟（收尾另走 _finalize_scan_timeout）
    return int(min(900, max(120, int(dur * 2.0) + 90)))


def compute_valorant_scan_budget(
    mode: str,
    last_analyzed: float,
    current_dur: float,
    pressure: dict[str, Any] | None = None,
    tick_count: int = 0,
    round_phase: str | None = None,
    valorant_profile: str | None = None,
    pending_start: float | None = None,
    prediction=None,
) -> tuple[tuple[float, float], bool, int, bool]:
    """Return scan range, OCR flag, timeout, and whether this is the first scan.

    Incremental scans catch up from last_analyzed (with lookback overlap), never
    jump to a trailing tip window that would skip the middle of the recording.

    当 mode == "valorant_round" 且 round_phase 提供时，走相位调度器的短窗预算；
    否则保留旧的 lookback 追赶行为（向后兼容）。
    prediction 为可选 RoundClockPrediction，仅影响 OCR 密度，不改变确认门。
    """
    pressure = pressure or {}

    # 相位调度路径（新主路径）
    if mode == "valorant_round" and round_phase is not None:
        from lsc.analyzer.phase_scheduler import (
            RoundPhase,
            get_profile,
            scan_budget_for_phase,
        )
        cfg = get_profile(valorant_profile)
        try:
            phase = RoundPhase(round_phase)
        except ValueError:
            phase = RoundPhase.UNKNOWN
        budget = scan_budget_for_phase(
            phase, cfg,
            last_analyzed=last_analyzed,
            current_dur=current_dur,
            pending_start=pending_start,
            prediction=prediction,
        )
        # OCR 还要过压力门控
        use_ocr = budget.need_ocr and valorant_refine_with_ocr(mode, pressure)
        scan_range = (budget.scan_start, budget.scan_end)
        scan_duration = max(1.0, scan_range[1] - scan_range[0])
        timeout = window_scan_timeout(scan_duration, use_ocr=use_ocr)
        full_rescan = last_analyzed <= 0.0
        return scan_range, use_ocr, timeout, full_rescan

    # 旧路径（向后兼容：未传 round_phase 时保留 240s lookback 追赶行为）
    try:
        lookback = float(pressure.get("analysis_window_sec", VALORANT_INCREMENTAL_LOOKBACK_SEC))
    except (TypeError, ValueError):
        lookback = VALORANT_INCREMENTAL_LOOKBACK_SEC
    lookback = max(20.0, lookback)

    full_rescan = last_analyzed <= 0.0
    if full_rescan:
        scan_start = 0.0
        scan_end = float(current_dur)
    else:
        # 从已分析点回看 lookback，再向前追赶；禁止 current_dur - lookback 跳窗漏扫
        scan_start = max(0.0, float(last_analyzed) - lookback)
        scan_end = min(float(current_dur), float(last_analyzed) + VALORANT_MAX_CATCHUP_SEC)
        if scan_end < scan_start:
            scan_end = float(current_dur)
    use_ocr = valorant_refine_with_ocr(mode, pressure)

    scan_range = (round(scan_start, 3), round(float(scan_end), 3))
    scan_duration = max(1.0, scan_range[1] - scan_range[0])
    timeout = window_scan_timeout(scan_duration, use_ocr=use_ocr)
    return scan_range, use_ocr, timeout, full_rescan




def detect_rounds_by_audio_rhythm(
    video_path: str,
    duration: float,
    ffmpeg_path: str = "ffmpeg",
    time_range: tuple[float, float] | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> list[dict[str, Any]]:
    """音频回合节奏检测：通过 RMS 能量包络识别 Valorant 回合边界。

    Valorant 回合的音频特征：
    - 买枪阶段 (~20-30s)：中低能量（语音、商店音效）
    - 战斗阶段 (~20-50s)：高能量（枪声、技能）
    - 回合过渡 (~3-5s)：能量回落

    算法：找高能量段（战斗）→ 合并间距 < 10s 的段 → 每段前后加短暂 padding（剔除买枪期）→ 过滤

    持续分析专用：录制中文件只能可靠提取音频，视频方法全部失效。
    """
    import tempfile
    import wave

    import numpy as np

    tmp_fd, tmp_path = tempfile.mkstemp(suffix='.wav')
    os.close(tmp_fd)

    seg_offset = time_range[0] if time_range else 0.0
    seg_end = time_range[1] if time_range else duration

    cmd = [ffmpeg_path, '-y', '-loglevel', 'error']
    if time_range:
        cmd += ['-ss', f'{time_range[0]:.3f}', '-t', f'{time_range[1] - time_range[0]:.3f}']
    cmd += ['-i', video_path, '-ar', '8000', '-ac', '1', '-f', 'wav', tmp_path]

    try:
        run_hidden(cmd, capture_output=True, timeout=120)
        if cancel_check and cancel_check():
            return []

        with wave.open(tmp_path, 'rb') as wf:
            n_frames = wf.getnframes()
            framerate = wf.getframerate()
            raw = wf.readframes(n_frames)

        samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
        if len(samples) == 0:
            return []

        # 1s 窗口 RMS
        window = framerate  # 1 秒
        n_windows = len(samples) // window
        if n_windows < 10:
            return []
        trimmed = samples[:n_windows * window].reshape(n_windows, window)
        rms = np.sqrt(np.mean(trimmed ** 2, axis=1))

        if rms.max() == 0:
            return []

        # 7s 居中移动平均平滑
        kernel = np.ones(7) / 7.0
        smoothed = np.convolve(rms, kernel, mode='same')

        # 动态阈值：顶部 45% = 高能量（战斗阶段）
        threshold = float(np.percentile(smoothed, 55))
        if threshold == 0:
            threshold = float(np.mean(smoothed))
        if threshold == 0:
            return []

        is_high = smoothed > threshold

        # 找连续高能量段
        combat_periods: list[tuple[int, int]] = []
        i = 0
        while i < n_windows:
            if is_high[i]:
                start = i
                while i < n_windows and is_high[i]:
                    i += 1
                end = i
                combat_periods.append((start, end))
            else:
                i += 1

        if not combat_periods:
            return []

        # 合并间距 < 10s 的高能量段（同一回合内的短暂安静）
        merged: list[tuple[int, int]] = [combat_periods[0]]
        for s, e in combat_periods[1:]:
            if s - merged[-1][1] < 10:
                merged[-1] = (merged[-1][0], e)
            else:
                merged.append((s, e))

        # 过滤：合并后高能量段 >= 5s（真实战斗，非噪声）
        merged = [(s, e) for s, e in merged if e - s >= 5]

        if not merged:
            return []

        # 每个战斗段 → 回合片段
        highlights: list[dict[str, Any]] = []
        for combat_start, combat_end in merged:
            # 起点: 战斗段开始处（剔除买枪期，仅保留 2s 安全缓冲），后 5s（回合结束反应）
            round_start = max(0.0, combat_start - 2 + seg_offset)
            round_end = min(duration, combat_end + 5 + seg_offset)
            seg_duration = round_end - round_start

            # 过滤：回合片段时长 15-150s
            if seg_duration < 15 or seg_duration > 150:  # 去掉买枪期后，纯战斗段可能更短
                continue

            # score: 战斗峰值强度 / 阈值（越高=越激烈）
            peak_rms = float(np.max(smoothed[combat_start:combat_end]))
            score = min(1.0, peak_rms / (threshold * 2.0))
            score = max(0.3, score)

            highlights.append({
                'start': round(round_start, 3),
                'end': round(round_end, 3),
                'score': round(score, 3),
                'reason': '回合战斗阶段',
                'phase': 'combat',
            })

        if not highlights:
            return []

        # 移除重叠
        highlights.sort(key=lambda h: h['start'])
        cleaned: list[dict[str, Any]] = []
        for h in highlights:
            if cleaned and h['start'] < cleaned[-1]['end']:
                # 裁剪前一片段
                cleaned[-1]['end'] = h['start']
                if cleaned[-1]['end'] - cleaned[-1]['start'] < 10:
                    cleaned.pop()
            cleaned.append(h)

        _log.info(
            "音频回合检测: %d 回合 (duration=%.0fs, threshold=%.1f, combat_periods=%d→%d)",
            len(cleaned), seg_end - seg_offset, threshold,
            len(combat_periods), len(merged),
        )
        return cleaned

    except Exception as exc:
        _log.warning("音频回合检测失败: %s", exc)
        return []
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass





class ValorantAnalyzerPlugin:
    game = "valorant"
    display_name = "Valorant"

    def capabilities(self) -> AnalyzerCapabilities:
        return AnalyzerCapabilities(
            realtime_continuous=True,
            posthoc_file=True,
            needs_ocr=True,
            needs_audio=True,
            game_specific=True,
        )

    def analyze_file(
        self,
        video_path: str,
        *,
        progress_callback=None,
        cancel_check=None,
        options: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]] | None:
        """对齐 _analyze_scene_or_rounds 的 valorant 分支（hybrid）。"""
        if cancel_check and cancel_check():
            return None
        options = options or {}
        from lsc.analyzer.round_detector import detect_valorant_rounds_hybrid
        from lsc.analyzer.valorant_frame_classifier import ModelContractError

        try:
            return detect_valorant_rounds_hybrid(
                video_path,
                ffmpeg_path=options.get("ffmpeg_path") or "ffmpeg",
                model_dir=options.get("model_dir"),
                cancel_check=cancel_check,
                progress_callback=progress_callback,
                session_id=str(options.get("session_id") or ""),
            )
        except ModelContractError:
            raise
        except Exception as exc:
            _log.warning("Valorant hybrid analyze_file failed: %s", exc)
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
            tick_count=int(state.get("tick_count", 0) or 0),
            round_phase=state.get("round_phase"),
            valorant_profile=state.get("valorant_profile"),
            pending_start=state.get("pending_start"),
            prediction=state.get("prediction"),
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
        mode = state.get("mode", "valorant_round")
        game = state.get("game", "valorant")
        ffmpeg_path = state.get("ffmpeg_path") or "ffmpeg"
        # Contract / placeholder paths: skip model load on empty/missing files.
        try:
            if not os.path.isfile(video_path) or os.path.getsize(video_path) <= 0:
                state["last_analyzed"] = window.end_sec
                return []
        except OSError:
            state["last_analyzed"] = window.end_sec
            return []
        if game == "valorant" and mode == "valorant_round":
            from lsc.analyzer.round_detector import detect_valorant_rounds_hybrid
            from lsc.analyzer.valorant_frame_classifier import ModelContractError

            try:
                rounds = detect_valorant_rounds_hybrid(
                    video_path,
                    time_range=(window.start_sec, window.end_sec),
                    ffmpeg_path=ffmpeg_path,
                    model_dir=state.get("model_dir"),
                    cancel_check=cancel_check,
                    progress_callback=state.get("progress_callback"),
                    session_id=str(state.get("session_id") or ""),
                    classifier=state.get("classifier"),
                    runtime_state=state.get("runtime_state"),
                )
            except ModelContractError:
                raise
            state["last_analyzed"] = window.end_sec
            return rounds or []

        duration = float(state.get("current_dur") or window.end_sec or 0.0)
        rounds = detect_rounds_by_audio_rhythm(
            video_path,
            duration=duration,
            ffmpeg_path=ffmpeg_path,
            time_range=(window.start_sec, window.end_sec),
            cancel_check=cancel_check,
        )
        state["last_analyzed"] = window.end_sec
        return rounds or []
