"""音频频谱事件检测器。

通过 FFT 分析音频频谱，检测击杀枪声（高频突增）、
回合结束音效（低频钟声）和主播反应（人声突增）。
"""
from __future__ import annotations

import logging
import os
import tempfile
import wave
from collections.abc import Callable
from typing import Any

import numpy as np

from lsc.utils.process_launcher import run_hidden

_log = logging.getLogger(__name__)

_WINDOW_SECONDS = 0.25
_SAMPLE_RATE = 16000

_FREQ_BANDS = {
    "low": (200, 500),
    "mid": (300, 3400),
    "high": (2000, 8000),
}

_BAND_LABELS = {
    "low": "round_end",
    "mid": "voice_burst",
    "high": "gunfire",
}

_SPIKE_RATIO = 5.0


def detect_sound_events(
    video_path: str,
    ffmpeg_path: str = "ffmpeg",
    duration: float = 0.0,
    progress_callback: Callable[[str, float, str], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
    time_range: tuple[float, float] | None = None,
) -> list[dict[str, Any]]:
    """检测音频事件（击杀枪声、回合结束、主播反应）。

    Args:
        time_range: 可选 ``(start_sec, end_sec)``，仅分析该时间段（增量分析）。

    Returns:
        ``[{"timestamp": float, "type": str, "score": float}, ...]``
    """
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".wav")
    os.close(tmp_fd)

    cmd = [ffmpeg_path, "-y", "-loglevel", "error"]
    if time_range is not None:
        cmd += ["-ss", f"{time_range[0]:.3f}", "-t", f"{time_range[1] - time_range[0]:.3f}"]
    cmd += ["-i", video_path, "-ar", str(_SAMPLE_RATE), "-ac", "1", "-f", "wav", tmp_path]

    try:
        run_hidden(cmd, capture_output=True, timeout=300)
        if cancel_check and cancel_check():
            return []

        with wave.open(tmp_path, "rb") as wf:
            n_frames = wf.getnframes()
            framerate = wf.getframerate()
            raw = wf.readframes(n_frames)

        samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
        if len(samples) == 0:
            return []

        window_size = int(framerate * _WINDOW_SECONDS)
        n_windows = len(samples) // window_size
        if n_windows < 4:
            return []

        trimmed = samples[: n_windows * window_size].reshape(n_windows, window_size)
        freqs = np.fft.rfftfreq(window_size, 1.0 / framerate)
        band_masks = {
            name: (freqs >= f_low) & (freqs <= f_high)
            for name, (f_low, f_high) in _FREQ_BANDS.items()
        }
        band_counts = {name: max(int(np.sum(mask)), 1) for name, mask in band_masks.items()}

        # 批量 FFT：一次性计算所有窗口的频谱
        all_spectra = np.abs(np.fft.rfft(trimmed, axis=1))  # shape: (n_windows, n_freqs)

        # 向量化计算各频段能量
        all_energies: dict[str, np.ndarray] = {}
        for name, mask in band_masks.items():
            all_energies[name] = np.sum(all_spectra[:, mask] ** 2, axis=1) / band_counts[name]

        # 预计算 sub-sample 精确定位所需的数据
        half_size = window_size // 2
        freqs_half = np.fft.rfftfreq(half_size, 1.0 / framerate)
        half_band_masks = {
            name: (freqs_half >= f_low) & (freqs_half <= f_high)
            for name, (f_low, f_high) in _FREQ_BANDS.items()
        }
        half_band_counts = {name: max(int(np.sum(mask)), 1) for name, mask in half_band_masks.items()}

        events: list[dict[str, Any]] = []
        sub_sample_ratio = 2
        seg_offset = time_range[0] if time_range else 0.0

        for name in _FREQ_BANDS:
            if cancel_check and cancel_check():
                break

            energies = all_energies[name]
            # 构建前向能量数组（第 0 个窗口无前值，设为 0）
            prev_energies_arr = np.zeros_like(energies)
            prev_energies_arr[1:] = energies[:-1]

            # 向量化 spike 检测：energy > prev * _SPIKE_RATIO 且 prev > 0
            spike_mask = (prev_energies_arr > 0) & (energies > prev_energies_arr * _SPIKE_RATIO)
            spike_indices = np.where(spike_mask)[0]

            for i in spike_indices:
                if cancel_check and cancel_check():
                    break

                prev = float(prev_energies_arr[i])
                energy = float(energies[i])

                # Sub-sample 精确定位
                window_start = i * window_size
                first_half = samples[window_start:window_start + half_size].astype(np.float32)
                second_half = samples[window_start + half_size:window_start + window_size].astype(np.float32)

                spec_first = np.abs(np.fft.rfft(first_half))
                spec_second = np.abs(np.fft.rfft(second_half))
                h_mask = half_band_masks[name]
                h_count = half_band_counts[name]
                energy_first = float(np.sum(spec_first[h_mask] ** 2) / h_count)
                energy_second = float(np.sum(spec_second[h_mask] ** 2) / h_count)

                if energy_second > energy_first * 1.5:
                    timestamp = i * _WINDOW_SECONDS + _WINDOW_SECONDS / sub_sample_ratio + seg_offset
                else:
                    timestamp = i * _WINDOW_SECONDS + seg_offset

                score = min(1.0, energy / (prev * 5.0))
                events.append({
                    "timestamp": round(timestamp, 3),
                    "type": _BAND_LABELS.get(name, name),
                    "score": max(0.3, score),
                })

            if progress_callback and duration > 0:
                pct = min(90.0, (list(_FREQ_BANDS).index(name) + 1) / len(_FREQ_BANDS) * 90.0)
                progress_callback("sound", pct, f"音频频谱分析中... {name}")

        merged = _merge_events(events)

        if progress_callback:
            progress_callback("sound", 100.0, f"音频事件检测完成：{len(merged)} 个")

        _log.info("音频事件检测: %d 个事件 (path=%s)", len(merged), os.path.basename(video_path))
        return merged
    except Exception as exc:
        _log.warning("音频事件检测失败: %s", exc)
        return []
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def _merge_events(
    events: list[dict[str, Any]], merge_window: float = 5.0
) -> list[dict[str, Any]]:
    """合并相近的音频事件（同一时间多频段突增只保留最高分）。

    merge_window=5.0s: 激烈交火时枪声频繁，2s 窗口会产生过多事件。
    5s 窗口将同一波交火的多个事件合并为一个。
    """
    if not events:
        return []
    events.sort(key=lambda e: e["timestamp"])
    merged = [events[0]]
    for e in events[1:]:
        if e["timestamp"] - merged[-1]["timestamp"] <= merge_window:
            if e["score"] > merged[-1]["score"]:
                merged[-1] = e
        else:
            merged.append(e)
    return merged


def detect_round_end_events(
    video_path: str,
    ffmpeg_path: str = "ffmpeg",
    time_range: tuple[float, float] | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> list[dict[str, Any]]:
    """仅检测回合结束音效（低频钟声 spike）。

    只处理 200-500Hz 低频段，跳过 mid/high 频段检测，
    性能约为 detect_sound_events 的 1/3。

    Returns:
        ``[{"timestamp": float, "score": float}, ...]``
    """
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".wav")
    os.close(tmp_fd)

    cmd = [ffmpeg_path, "-y", "-loglevel", "error"]
    if time_range is not None:
        cmd += ["-ss", f"{time_range[0]:.3f}", "-t", f"{time_range[1] - time_range[0]:.3f}"]
    cmd += ["-i", video_path, "-ar", str(_SAMPLE_RATE), "-ac", "1", "-f", "wav", tmp_path]

    try:
        run_hidden(cmd, capture_output=True, timeout=300)
        if cancel_check and cancel_check():
            return []

        with wave.open(tmp_path, "rb") as wf:
            n_frames = wf.getnframes()
            framerate = wf.getframerate()
            raw = wf.readframes(n_frames)

        samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
        if len(samples) == 0:
            return []

        window_size = int(framerate * _WINDOW_SECONDS)
        n_windows = len(samples) // window_size
        if n_windows < 4:
            return []

        trimmed = samples[: n_windows * window_size].reshape(n_windows, window_size)
        freqs = np.fft.rfftfreq(window_size, 1.0 / framerate)

        # 仅低频段 (200-500Hz) — 回合结束钟声特征频段
        f_low, f_high = _FREQ_BANDS["low"]
        band_mask = (freqs >= f_low) & (freqs <= f_high)
        band_count = max(int(np.sum(band_mask)), 1)

        all_spectra = np.abs(np.fft.rfft(trimmed, axis=1))
        energies = np.sum(all_spectra[:, band_mask] ** 2, axis=1) / band_count

        # Spike 检测
        prev_energies = np.zeros_like(energies)
        prev_energies[1:] = energies[:-1]
        spike_mask = (prev_energies > 0) & (energies > prev_energies * _SPIKE_RATIO)
        spike_indices = np.where(spike_mask)[0]

        seg_offset = time_range[0] if time_range else 0.0
        events: list[dict[str, Any]] = []

        for i in spike_indices:
            if cancel_check and cancel_check():
                break
            prev = float(prev_energies[i])
            energy = float(energies[i])
            timestamp = i * _WINDOW_SECONDS + seg_offset
            score = min(1.0, energy / (prev * 5.0))
            events.append({
                "timestamp": round(timestamp, 3),
                "score": max(0.3, score),
            })

        merged_events = _merge_events(
            [{"timestamp": e["timestamp"], "type": "round_end", "score": e["score"]} for e in events],
            merge_window=8.0,
        )
        return [{"timestamp": e["timestamp"], "score": e["score"]} for e in merged_events]

    except Exception as exc:
        _log.warning("回合结束音效检测失败: %s", exc)
        return []
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
