"""Room handler 纯工具函数。

本模块包含不依赖闭包外状态的纯工具函数和常量，可被 room_handler.py 导入使用。
"""

import json
import os
import subprocess
from typing import Any


def atomic_write_json(file_path: str, data: Any) -> None:
    tmp_path = file_path + '.tmp'
    with open(tmp_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, file_path)


def clip_id(room_id: str, start: float, end: float) -> str:
    return f"{room_id}_{int(round(start * 10))}_{int(round(end * 10))}"


def expand_user_path(path: str) -> str:
    return os.path.abspath(os.path.expanduser(path))


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def parse_fps(fps_str: str) -> float:
    if not fps_str or fps_str == '原画':
        return 0.0
    try:
        return float(fps_str)
    except (TypeError, ValueError):
        return 0.0


def safe_terminate(proc: subprocess.Popen, timeout_sec: float = 3.0) -> None:
    import time
    try:
        proc.terminate()
    except Exception:
        pass
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return
        time.sleep(0.1)
    try:
        proc.kill()
    except Exception:
        pass


def detect_audio_energy_peaks(
    pcm_data: list[float],
    sample_rate: int = 16000,
    window_ms: float = 50.0,
    threshold_factor: float = 1.5,
) -> list[tuple[float, float]]:
    if not pcm_data:
        return []
    window_size = int(sample_rate * window_ms / 1000)
    energies = []
    for i in range(0, len(pcm_data), window_size):
        window = pcm_data[i:i + window_size]
        energy = sum(x * x for x in window) / max(len(window), 1)
        energies.append((i / sample_rate, energy))
    if not energies:
        return []
    avg_energy = sum(e for _, e in energies) / len(energies)
    threshold = avg_energy * threshold_factor
    peaks = []
    in_peak = False
    peak_start = 0.0
    for t, e in energies:
        if e >= threshold:
            if not in_peak:
                in_peak = True
                peak_start = t
        else:
            if in_peak:
                peaks.append((peak_start, t))
                in_peak = False
    if in_peak:
        peaks.append((peak_start, energies[-1][0]))
    return peaks


def merge_round_windows(rounds: list[dict], max_gap_sec: float = 2.0) -> list[dict]:
    if not rounds:
        return []
    merged = [dict(rounds[0])]
    for r in rounds[1:]:
        prev = merged[-1]
        if r.get('start', 0) - prev.get('end', 0) <= max_gap_sec:
            prev['end'] = max(prev.get('end', 0), r.get('end', 0))
            prev['score'] = max(prev.get('score', 0), r.get('score', 0))
        else:
            merged.append(dict(r))
    return merged


def cleanup_segments(segments: list[dict], min_duration: float = 1.0) -> list[dict]:
    return sorted(
        [s for s in segments if (s.get('end', 0) - s.get('start', 0)) >= min_duration],
        key=lambda s: s.get('start', 0),
    )


def round_lists_changed(a: list[dict], b: list[dict]) -> bool:
    if len(a) != len(b):
        return True
    for ra, rb in zip(a, b):
        if ra.get('start') != rb.get('start') or ra.get('end') != rb.get('end'):
            return True
    return False


def valorant_round_key(hl: dict) -> str:
    return f"r_{int(round(hl.get('start', 0)))}_{int(round(hl.get('end', 0)))}"


def is_auto_exportable_valorant_round(hl: dict) -> bool:
    if hl.get('confirm_status') not in ('user_confirmed', 'ocr_confirmed'):
        return False
    if hl.get('is_buy_phase', False):
        return False
    duration = hl.get('end', 0) - hl.get('start', 0)
    return duration >= 2.0


def drop_open_tail_rounds(new_hl: list[dict], worker_dur: float) -> list[dict]:
    return [h for h in new_hl if h.get('end', 0) <= worker_dur - 1.0]


def continuous_effective_interval(base_interval: float, pressure_level: str) -> float:
    if pressure_level == 'critical':
        return base_interval * 3.0
    elif pressure_level == 'pressure':
        return base_interval * 1.5
    return base_interval


def finalize_scan_timeout(duration_sec: float) -> int:
    return int(min(600, max(60, duration_sec * 2.0) + 60))


def window_scan_timeout(scan_duration_sec: float, *, use_ocr: bool) -> int:
    dur = max(1.0, float(scan_duration_sec))
    if not use_ocr:
        return int(max(45, int(dur / 180.0 * 12) + 45))
    return int(min(900, max(120, int(dur * 2.0) + 90)))
