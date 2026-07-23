"""Scene / generic highlight analysis (FFmpeg scene + audio/OCR fallbacks)."""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
from collections.abc import Callable
from typing import Any

from lsc.utils.process_launcher import prepare_launch, run_hidden, set_stream_nonblocking

_log = logging.getLogger(__name__)


def safe_terminate(proc: subprocess.Popen) -> None:
    """安全终止子进程：terminate → 等 5s → kill 兜底。"""
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except Exception:
        try:
            proc.kill()
            proc.wait(timeout=3)
        except Exception as exc:
            _log.debug("操作异常（已忽略）: %s", exc)


def get_video_duration(video_path: str) -> float:
    """Get video duration in seconds using ffprobe."""
    import json as _json

    from lsc.config import load_config as _load_cfg2
    _cfg2 = _load_cfg2()
    _ffprobe = _cfg2.ffprobe_path or shutil.which("ffprobe") or "ffprobe"

    try:
        result = run_hidden(
            [
                _ffprobe,
                "-v", "error",
                "-probesize", "50M",
                "-analyzeduration", "10M",
                "-show_entries", "format=duration",
                "-of", "json",
                video_path,
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        data = _json.loads(result.stdout)
        return float(data.get("format", {}).get("duration", 0))
    except Exception as exc:
        _log.debug("获取视频时长失败 (%s): %s", video_path, exc)
        return 0.0



def detect_audio_energy_peaks(
    video_path: str,
    duration: float,
    ffmpeg_path: str = "ffmpeg",
    time_range: tuple[float, float] | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> list[dict[str, Any]]:
    """音频 RMS 能量峰值检测，作为 scene 检测的回退方案。

    当 FFmpeg scene 检测对所有阈值都返回 0 结果时（如游戏直播画面过于连续），
    提取音频 RMS 包络，找到音量峰值段作为高光候选。
    """
    import tempfile
    import wave

    import numpy as np

    tmp_fd, tmp_path = tempfile.mkstemp(suffix='.wav')
    os.close(tmp_fd)

    cmd = [ffmpeg_path, '-y', '-loglevel', 'error']
    if time_range:
        cmd += ['-ss', f'{time_range[0]:.3f}', '-t', f'{time_range[1] - time_range[0]:.3f}']
    cmd += ['-i', video_path, '-ar', '8000', '-ac', '1', '-f', 'wav', tmp_path]

    try:
        run_hidden(cmd, capture_output=True, timeout=60)
        if cancel_check and cancel_check():
            return []
        with wave.open(tmp_path, 'rb') as wf:
            n_frames = wf.getnframes()
            framerate = wf.getframerate()
            raw = wf.readframes(n_frames)
        samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
        if len(samples) == 0:
            return []
        window = framerate // 2
        n_windows = len(samples) // window
        if n_windows == 0:
            return []
        trimmed = samples[:n_windows * window].reshape(n_windows, window)
        rms = np.sqrt(np.mean(trimmed ** 2, axis=1))
        if rms.max() == 0:
            return []
        percentile_threshold = float(np.percentile(rms, 85))
        mean_rms = float(np.mean(rms))
        std_rms = float(np.std(rms))
        statistical_threshold = mean_rms + 2.0 * std_rms
        threshold = max(percentile_threshold, statistical_threshold)
        if threshold == 0:
            return []
        is_peak = rms > threshold
        seg_offset = time_range[0] if time_range else 0.0
        highlights: list[dict[str, Any]] = []
        i = 0
        while i < n_windows:
            if is_peak[i]:
                start = i
                while i < n_windows and is_peak[i]:
                    i += 1
                end = i
                start_sec = (start * 0.5) + seg_offset
                end_sec = (end * 0.5) + seg_offset
                if end_sec - start_sec >= 3.0:
                    score = min(1.0, float(np.mean(rms[start:end]) / threshold))
                    highlights.append({
                        'start': max(0, start_sec - 2),
                        'end': min(duration, end_sec + 5),
                        'score': max(0.3, score),
                        'reason': '音频能量峰值',
                        'phase': 'unknown',
                    })
            else:
                i += 1
        return highlights
    except Exception as exc:
        _log.warning("音频能量检测失败: %s", exc)
        return []
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def scene_ocr_detection(
    video_path: str,
    ffmpeg_path: str,
    duration: float,
    progress_callback: Callable[[str, float, str], None] | None,
    cancel_check: Callable[[], bool] | None,
    time_range: tuple[float, float] | None = None,
    enabled: bool = True,
) -> list[dict[str, Any]]:
    """Scene 模式的轻量 OCR 检测：仅 Kill Feed + 回合标记，不跑 Whisper/CLIP。

    rapidocr 未安装或 enabled=False 时返回空列表。
    持续分析时录制文件仍在写入，OCR 帧提取不完整，应设 enabled=False 跳过。
    """
    if not enabled:
        return []

    try:
        from lsc.analyzer.ocr_detector import detect_kill_events
    except ImportError:
        _log.debug("rapidocr 未安装，scene 模式跳过 OCR 检测")
        return []

    if progress_callback:
        progress_callback("scene", 85.0, "OCR 击杀检测中...")
    try:
        ocr_events = detect_kill_events(
            video_path, ffmpeg_path=ffmpeg_path,
            duration=duration,
            cancel_check=cancel_check,
            game="valorant",
        )
    except Exception as exc:
        _log.warning("scene 模式 OCR 检测失败: %s", exc)
        return []

    ocr_highlights: list[dict[str, Any]] = []
    for evt in ocr_events:
        if evt.get("type") == "kill":
            ts = evt.get("timestamp", 0.0)
            if time_range and (ts < time_range[0] or ts > time_range[1]):
                continue
            score = evt.get("score", 0.5)
            pre_pad = 1.0 if score >= 0.7 else 2.0
            post_pad = 4.0 if score >= 0.7 else 6.0
            ocr_highlights.append({
                "start": max(0.0, ts - pre_pad),
                "end": ts + post_pad,
                "score": score,
                "reason": f"击杀: {evt.get('text', '')[:30]}",
                "source": "ocr",
                "type": "kill",
                "timestamp": ts,
            })
        elif evt.get("type") == "round_marker":
            ts = evt.get("timestamp", 0.0)
            highlight = {
                "start": max(0.0, ts - 1.0),
                "end": ts + 1.0,
                "score": 0.5,
                "reason": "回合标记",
                "source": "ocr",
                "type": "round_marker",
                "timestamp": ts,
            }
            if evt.get("phase"):
                highlight["phase"] = evt["phase"]
            ocr_highlights.append(highlight)
    return ocr_highlights


def merge_scene_and_ocr(
    scene_highlights: list[dict[str, Any]],
    ocr_highlights: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """合并 scene 检测结果与 OCR 检测结果。

    OCR 击杀事件参与回合分组，与 scene 高光去重后合并。
    """
    from lsc.analyzer.pipeline import (
        _deduplicate_highlights,
        _group_events_by_round,
        _merge_close_segments,
    )

    all_highlights = list(scene_highlights) + list(ocr_highlights)

    if ocr_highlights:
        all_highlights = _group_events_by_round(all_highlights)

    all_highlights = _deduplicate_highlights(all_highlights, iou_threshold=0.5)
    all_highlights = _merge_close_segments(all_highlights, max_gap=15.0)
    return all_highlights


def run_scene_analysis(
    video_path: str,
    threshold: float = 0.3,
    min_duration: float = 3.0,
    progress_callback: Callable[[str, float, str], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
    time_range: tuple[float, float] | None = None,
    enable_ocr: bool = True,
) -> list[dict[str, Any]] | None:
    """FFmpeg 场景检测，支持流式进度回调、取消、自适应阈值与时间范围。

    游戏直播（如 Valorant 第一人称）画面连续，固定阈值 0.3 常检测不到足够
    场景切换点导致空高光。本函数在给定阈值无结果时，自动降低阈值重试
    （0.3 → 0.15 → 0.05），并输出诊断日志。

    参数:
        time_range: 可选 ``(start_sec, end_sec)``，仅分析该时间段（用于增量
            持续分析）。返回的高光时间戳已还原为视频全局时间轴。

    Returns:
        高光段列表 ``[{"start", "end", "score"}, ...]``；被取消时返回 None。
    """
    from lsc.config import load_config as _load_cfg
    _cfg = _load_cfg()
    _ffmpeg = _cfg.ffmpeg_path or shutil.which("ffmpeg") or "ffmpeg"

    duration = get_video_duration(video_path)
    if duration <= 0:
        _log.warning("场景检测: 无法获取视频时长 (path=%s)", video_path)
        return []

    env, creation_flags, cwd = prepare_launch(_ffmpeg)
    pattern = re.compile(r"pts_time:(\d+\.?\d*)")

    # 增量分析时的时间偏移：input seek 后 pts_time 是相对 seek 点的，需加回 seg_offset 还原全局
    seg_offset = time_range[0] if time_range else 0.0

    def _detect(ts_threshold: float) -> list[float] | None:
        """在给定阈值下跑 FFmpeg scene 检测，返回场景切换时间戳列表（已还原全局）。

        返回 None 表示被取消；返回 list（可能为空）表示正常完成。
        """
        cmd = [_ffmpeg, "-y", "-loglevel", "info"]
        # 硬解卸 CPU；select/scene 仍须 CPU，先降到 640 宽降低滤镜代价
        try:
            from lsc.utils.gpu_ffmpeg import nvenc_available
            if nvenc_available():
                cmd += ["-hwaccel", "cuda"]
        except Exception as exc:
            _log.debug("scene detect: nvenc probe skipped: %s", exc)
        if time_range is not None:
            tr_start, tr_end = time_range
            # -ss input seek（快速）+ -t duration，限定增量分析范围
            cmd += ["-ss", f"{tr_start:.3f}", "-t", f"{tr_end - tr_start:.3f}"]
        cmd += [
            "-i", video_path,
            "-vf", f"scale=640:-2,select='gt(scene\\\\,{ts_threshold})',showinfo",
            "-vsync", "vfr", "-f", "null", "-",
        ]
        popen_kwargs: dict[str, Any] = {
            "stdout": subprocess.DEVNULL, "stderr": subprocess.PIPE,
            "text": True, "bufsize": 0, "encoding": "utf-8",
            "errors": "replace", "env": env, "cwd": cwd,
        }
        if creation_flags:
            popen_kwargs["creationflags"] = creation_flags
        try:
            proc = subprocess.Popen(cmd, **popen_kwargs)  # noqa: S603
            set_stream_nonblocking(proc.stderr)
        except FileNotFoundError:
            _log.warning("场景检测: FFmpeg 未找到")
            return []
        ts_list: list[float] = []
        try:
            if proc.stderr is None:
                _log.error("FFmpeg 场景检测: stderr 管道未创建")
                return []
            for line in proc.stderr:
                if cancel_check and cancel_check():
                    _log.info("场景检测被取消")
                    safe_terminate(proc)
                    return None
                m = pattern.search(line)
                if m:
                    # input seek 后 pts_time 相对 seek 点，加 seg_offset 还原全局时间轴
                    ts_list.append(float(m.group(1)) + seg_offset)
                    if progress_callback and duration > 0:
                        pct = min(95.0, ts_list[-1] / duration * 100.0)
                        progress_callback("scene", pct, f"已检测 {len(ts_list)} 个场景切换")
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            _log.warning("场景检测: wait 超时 (threshold=%.2f)", ts_threshold)
            safe_terminate(proc)
            return []
        finally:
            if proc.poll() is None:
                safe_terminate(proc)
        return ts_list

    # 自适应阈值：游戏直播画面连续，高阈值可能 0 结果，逐步降低重试
    # 阈值下限收紧到 0.15 — 0.05 在 Valorant 第一人称视角下会把视角晃动误判为场景切换
    thresholds_to_try = [threshold]
    if threshold > 0.15:
        thresholds_to_try.append(max(0.15, threshold / 2))
    if threshold > 0.25:
        thresholds_to_try.append(0.15)

    timestamps: list[float] = []
    for th in thresholds_to_try:
        if cancel_check and cancel_check():
            return None
        detected = _detect(th)
        if detected is None:
            return None  # 取消
        _log.info("场景检测: threshold=%.2f → %d 个切换点 (duration=%.1fs)",
                  th, len(detected), duration)
        if detected:
            timestamps = detected
            break  # 检测到就不再降阈值

    if not timestamps:
        _log.info("场景检测: 所有阈值均未检测到场景切换，回退到音频能量检测 (path=%s)", video_path)
        if progress_callback:
            progress_callback("scene", 80.0, "场景检测无结果，尝试音频能量检测...")
        audio_highlights = detect_audio_energy_peaks(
            video_path, duration, ffmpeg_path=_ffmpeg,
            time_range=time_range, cancel_check=cancel_check,
        )
        if audio_highlights:
            _log.info("音频能量检测: 发现 %d 段高光 (path=%s)", len(audio_highlights), video_path)
        else:
            _log.warning("音频能量检测也无结果 (path=%s)", video_path)

        # 尝试 OCR 检测补充信号
        ocr_highlights = scene_ocr_detection(
            video_path, _ffmpeg, duration, progress_callback, cancel_check, time_range,
            enabled=enable_ocr,
        )
        if ocr_highlights:
            _log.info("OCR 检测补充: 发现 %d 段高光 (path=%s)", len(ocr_highlights), video_path)
            if progress_callback:
                progress_callback("scene", 100.0, f"检测完成：{len(audio_highlights) + len(ocr_highlights)} 段")
            return merge_scene_and_ocr(audio_highlights, ocr_highlights)

        if audio_highlights:
            if progress_callback:
                progress_callback("scene", 100.0, f"音频检测完成：{len(audio_highlights)} 段")
            return audio_highlights
        if progress_callback:
            progress_callback("scene", 100.0, "未检测到高光（画面和音频均无显著变化）")
        return []

    # 分组连续场景切换为高光段
    # 针对 Valorant 回合制游戏: 手枪局/eco 局回合 30-40s, 长枪局 60-80s, 加时赛 80-100s
    # 动态间隔：根据场景切换间距分布自适应确定回合边界
    if len(timestamps) > 1:
        gaps = [timestamps[i + 1] - timestamps[i] for i in range(len(timestamps) - 1)]
        median_gap = sorted(gaps)[len(gaps) // 2]
        _ROUND_MIN_GAP = min(max(35.0, median_gap * 2.0), 80.0)
    else:
        _ROUND_MIN_GAP = 35.0
    _log.info("场景分组: 动态回合间隔=%.1fs (中位间距=%.1fs)", _ROUND_MIN_GAP,
              median_gap if len(timestamps) > 1 else 0.0)
    _ROUND_PRE_PAD = 2.0   # 前缓冲: 战斗开始前短暂走位缓冲（不含买枪期）
    _ROUND_POST_PAD = 5.0  # 后缓冲: 保留击杀后的反应 / 回合结算
    highlights: list[dict[str, Any]] = []
    segment_start = timestamps[0]
    prev_ts = timestamps[0]
    for ts in timestamps[1:]:
        gap = ts - prev_ts
        if gap > _ROUND_MIN_GAP:  # 间隔 > 动态阈值 → 新回合边界
            highlights.append({
                "start": max(0.0, segment_start - _ROUND_PRE_PAD),
                "end": min(duration, prev_ts + _ROUND_POST_PAD),
                "score": max(0.3, min(1.0, 1.5 - gap / 60.0)),
                "reason": "场景切换检测",
                "phase": "unknown",
            })
            segment_start = ts
        prev_ts = ts
    last_gap = prev_ts - segment_start
    highlights.append({
        "start": max(0.0, segment_start - _ROUND_PRE_PAD),
        "end": min(duration, prev_ts + _ROUND_POST_PAD),
        "score": max(0.5, min(1.0, 1.5 - last_gap / 60.0)),
        "reason": "场景切换检测",
        "phase": "unknown",
    })

    # 按最短时长过滤 + 去重重叠（确保片段互不重叠）
    result: list[dict[str, Any]] = []
    last_end = 0.0
    for h in highlights:
        seg_len = h["end"] - h["start"]
        if seg_len >= min_duration or seg_len >= 15.0:
            h["start"] = max(h["start"], last_end)
            if h["end"] > h["start"]:
                result.append(h)
                last_end = h["end"]

    # OCR 检测补充信号
    ocr_highlights = scene_ocr_detection(
        video_path, _ffmpeg, duration, progress_callback, cancel_check, time_range,
        enabled=enable_ocr,
    )
    if ocr_highlights:
        _log.info("OCR 补充检测: %d 段高光 (path=%s)", len(ocr_highlights), video_path)
        result = merge_scene_and_ocr(result, ocr_highlights)

    _log.info("场景检测完成: %d 段高光 (from %d 切换点, threshold=%.2f)",
              len(result), len(timestamps), threshold)
    if progress_callback:
        progress_callback("scene", 100.0, f"场景检测完成：{len(result)} 段")
    return result



