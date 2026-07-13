"""WebSocket 消息路由器：处理前端请求并与 Qt 主线程的 MultiRoomManager 交互。

将前端的房间管理、录制、导出、预览、分析等操作路由到 Qt 主线程执行，
并通过广播将房间状态变更实时推送给前端。
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import shutil
import subprocess

# 添加 lsc 到 Python 路径
import sys
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any

import numpy as np

_LSC_ROOT = os.path.join(os.path.dirname(__file__), '..', '..')
if _LSC_ROOT not in sys.path:
    sys.path.insert(0, _LSC_ROOT)

from persistence import (
    is_analysis_stale,
    load_analysis_results,
    load_rooms,
    save_analysis_results,
    save_rooms,
)

from lsc.config import ExportProfile, load_config
from lsc.core.services.ingest_registry import PreviewStreamRegistry, get_shared_ingest_registry
from lsc.core.services.resource_monitor import collect_system_stats, get_resource_pressure
from lsc.core.services.timeline_service import get_timeline_service
from lsc.gui.multi_room.manager import MultiRoomManager
from lsc.platforms.registry import select_quality
from lsc.utils.error_messages import humanize_error
from lsc.utils.process_launcher import prepare_launch, set_stream_nonblocking

_log = logging.getLogger('lsc.handlers')


SETTINGS_FILE = os.path.join(os.path.dirname(__file__), '..', 'settings.json')
RECORDING_HISTORY_FILE = os.path.join(os.path.dirname(__file__), '..', 'recording_history.json')


def _load_recording_history() -> list[dict[str, Any]]:
    """从文件加载录制历史，失败返回空列表。"""
    try:
        with open(RECORDING_HISTORY_FILE, encoding='utf-8') as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
    except Exception as exc:
        _log.warning("加载录制历史失败，使用空列表: %s", exc)
    return []


def _atomic_write_json(file_path: str, data: Any) -> None:
    """原子写入 JSON 文件：先写 .tmp 再 replace，防止断电损坏。"""
    tmp_path = file_path + '.tmp'
    with open(tmp_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, file_path)


def _save_recording_history(history: list[dict[str, Any]]) -> None:
    """持久化录制历史到文件（原子写入），失败时打印日志。"""
    try:
        _atomic_write_json(RECORDING_HISTORY_FILE, history)
    except Exception as exc:
        _log.error("保存录制历史失败: %s", exc)


recording_history: list[dict[str, Any]] = _load_recording_history()

# Analytics jobs in progress: {room_id: {"progress": 0.0, "highlights": [...], "completed_at": float}}
_analysis_jobs: dict[str, dict[str, Any]] = {}
_ANALYSIS_JOB_TTL = 300.0  # 5 分钟后自动清理已完成的分析结果

# 持续分析任务状态：room_id -> {task, last_analyzed, highlights, cancelled}
# 边录边分析：后台 asyncio 任务定期对录制文件新增段做增量场景检测
_continuous_tasks: dict[str, dict[str, Any]] = {}
_VALORANT_INCREMENTAL_LOOKBACK_SEC = 240.0  # 4 分钟增量回看窗口
_VALORANT_MAX_CATCHUP_SEC = 480.0  # 单次 tick 最多向前追赶的新内容时长
_VALORANT_MIN_EXPORT_DURATION_SEC = 35.0  # 短于此的 OCR 段视为假买枪/准备期
_deferred_export_jobs: list[dict[str, Any]] = []  # 延后导出队列（先入列，压力缓解后再导出）

def _clip_id(room_id: str, start: float, end: float) -> str:
    """生成稳定的切片 ID（前后端同算法独立计算，用于去重）。"""
    return f"{room_id}_{int(round(start * 10))}_{int(round(end * 10))}"


# 导出任务映射：前端 job_id -> 后端 clip_id，用于取消导出时定位 FFmpeg 进程
export_jobs: dict[str, str] = {}

# 分析 FFmpeg 串行化：确保同时只有 1 个分析任务跑 FFmpeg（音频提取+OCR），
# 避免与录制/预览/导出 FFmpeg 竞争导致 8+ 进程同时运行
_analysis_semaphore = asyncio.Semaphore(1)

# 全局导出队列：所有导出任务（手动/自动/分析）统一入队，worker 池并行消费
_EXPORT_MAX_CONCURRENT = 2  # 全局最大并行导出进程数
_export_queue: asyncio.Queue | None = None
_EXPORT_WORKERS: list[asyncio.Task] = []  # worker 池（长度 ≤ _EXPORT_MAX_CONCURRENT）
_export_queue_lock = asyncio.Lock()
_export_cancelled_jobs: set[str] = set()  # 已取消的 job_id 集合（含排队中）

# MSE streamer instances keyed by room_id
_mse_streamers: dict[str, Any] = {}
# 保护 _mse_streamers 的锁：asyncio 线程与 run_in_executor 线程池均会并发访问
_mse_streamers_lock = threading.Lock()


def _preview_stream_registry() -> PreviewStreamRegistry:
    return PreviewStreamRegistry(backing=_mse_streamers, lock=_mse_streamers_lock)


_shared_ingests = get_shared_ingest_registry()


def _ingest_diagnostics() -> dict[str, int]:
    try:
        stats = _shared_ingests.snapshot_counts()
    except Exception as exc:
        _log.debug("shared ingest diagnostics failed: %s", exc)
        stats = {
            "shared_ingests": 0,
            "recording_sinks": 0,
            "preview_subscribers": 0,
            "preview_dropped_bytes": 0,
            "preview_dropped_batches": 0,
        }
    stats["legacy_mse_streamers"] = _preview_stream_registry().active_count()
    return stats


def _stop_idle_shared_ingest(room_id: str, reason: str) -> bool:
    try:
        ingest = _shared_ingests.get(room_id)
    except Exception as exc:
        _log.debug("shared ingest lookup failed during cleanup room_id=%s: %s", room_id, exc)
        return False
    if ingest is None:
        return False
    if getattr(ingest, "recording_active", False):
        return False
    if getattr(ingest, "preview_subscribers", 0) > 0:
        return False
    try:
        _shared_ingests.stop_room(room_id, reason=reason)
        return True
    except Exception as exc:
        _log.warning("shared ingest cleanup failed room_id=%s: %s", room_id, exc)
        return False


def _compute_preview_quality_params(data: dict | None = None) -> dict[str, Any]:
    """从 settings 和消息数据计算预览画质参数，含压力感知降级。

    降级策略：
    - 3 路以上新建/重建的非放大预览上限 854×480@20fps
    - 4 路或 pressure 时 640×360@15fps
    - critical 时拒绝新增高成本任务（调用方应检查 pressure_reject）
    """
    settings = load_settings()
    preview_quality = (data or {}).get('preview_quality') or settings.get('preview_quality', '高清')
    preset = _get_preview_quality_preset(preview_quality)
    from lsc.core.services.mse_streamer import _check_nvenc
    use_nvenc = _check_nvenc()
    width = preset['width']
    height = preset['height']
    target_fps = 0  # 0 表示保持原画帧率
    active_mse_count = _preview_stream_registry().active_count()

    # 压力感知降级
    pressure = get_resource_pressure()
    pressure_level = pressure.get('level', 'normal')

    # 默认不限制（使用 preset 原始分辨率）
    max_w, max_h = 0, 0

    if pressure_level == 'critical' or active_mse_count >= 4:
        max_w, max_h, target_fps = 640, 360, 15
    elif pressure_level == 'pressure' or active_mse_count >= 3:
        max_w, max_h, target_fps = 854, 480, 20
    elif active_mse_count >= 8:
        max_w, max_h, target_fps = 640, 360, 15
    elif active_mse_count >= 6:
        max_w, max_h = 854, 480

    # 仅在设置了限制时才降分辨率
    if max_w > 0 and max_h > 0:
        if width == 0 or height == 0:
            width, height = max_w, max_h
        elif width > max_w or height > max_h:
            ratio = min(max_w / width, max_h / height)
            width = int(width * ratio)
            height = int(height * ratio)

    video_bitrate = preset['nvenc_bitrate'] if use_nvenc else preset['x264_bitrate']
    crf_value = preset['x264_crf']
    return {
        'width': width,
        'height': height,
        'use_nvenc': use_nvenc,
        'video_bitrate': video_bitrate,
        'crf_value': crf_value,
        'fps': target_fps,
        'pressure_level': pressure_level,
        'pressure_reject': pressure_level == 'critical',
    }


def _configure_shared_preview_quality(shared_ingest, data: dict | None = None) -> None:
    """Compute preview quality params and configure them on the shared ingest."""
    params = _compute_preview_quality_params(data)
    # 过滤掉 configure_preview 不接受的参数
    valid_keys = {'width', 'height', 'use_nvenc', 'video_bitrate', 'crf_value', 'fps'}
    filtered = {k: v for k, v in params.items() if k in valid_keys}
    shared_ingest.configure_preview(**filtered)


# 正在启动 MSE 的 room_id 集合，防止启动过程中重复请求
_mse_starting: set[str] = set()
_mse_starting_lock = threading.Lock()

# MSE 预览自动重连状态: {room_id: {"attempts": int}}
_mse_reconnect_state: dict[str, dict[str, Any]] = {}
_MSE_MAX_RECONNECT = 3
_MSE_RECONNECT_BASE_DELAY = 2.0
_MSE_RECONNECT_MAX_DELAY = 30.0

# 专用线程池：录制操作（HTTP 刷新 + FFmpeg 启动）可阻塞 30s+，独立线程池避免饿死快操作
_recording_executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix='rec')
# 快操作线程池：disconnect/mute/seek 等 bridge.call 操作，预期 <1s 完成
_bridge_executor = ThreadPoolExecutor(max_workers=16, thread_name_prefix='bridge')
# AI 分析专用线程池：CPU/GPU 密集型，独立线程池避免与录制/导出竞争
_ai_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix='ai')

# 录制并发限流：最多同时启动 2 路录制，避免 6 路同时 HTTP 刷新 + FFmpeg 启动耗尽线程和 CPU
_recording_semaphore = asyncio.Semaphore(2)
# 正在提交录制启动的 room_id 集合，防止同一房间重复提交
_recording_starting: set[str] = set()


def shutdown_room_handlers(timeout_sec: float = 10.0) -> dict[str, int]:
    """Stop handler-owned background work before backend process exit."""
    stats = {
        "continuous_tasks_cancelled": 0,
        "mse_streamers_stopped": 0,
        "shared_ingests_stopped": 0,
        "executors_shutdown": 0,
    }

    for room_id, state in list(_continuous_tasks.items()):
        state["cancelled"] = True
        task = state.get("task")
        cancel = getattr(task, "cancel", None)
        if callable(cancel):
            try:
                cancel()
            except Exception as exc:
                _log.debug("cancel continuous task failed room_id=%s: %s", room_id, exc)
        stats["continuous_tasks_cancelled"] += 1
    _continuous_tasks.clear()

    with _mse_starting_lock:
        _mse_starting.clear()
    _mse_reconnect_state.clear()
    _recording_starting.clear()
    _analysis_jobs.clear()
    export_jobs.clear()

    streamers = _preview_stream_registry().clear_items()
    for room_id, streamer in streamers:
        stop = getattr(streamer, "stop", None)
        if callable(stop):
            try:
                stop()
                stats["mse_streamers_stopped"] += 1
            except Exception as exc:
                _log.warning("stop MSE streamer failed room_id=%s: %s", room_id, exc)

    stop_all_shared = getattr(_shared_ingests, "stop_all", None)
    if callable(stop_all_shared):
        try:
            stats["shared_ingests_stopped"] = int(stop_all_shared(reason="handler shutdown") or 0)
        except Exception as exc:
            _log.warning("stop shared ingests failed during shutdown: %s", exc)

    for name, executor in (
        ("recording", _recording_executor),
        ("bridge", _bridge_executor),
        ("ai", _ai_executor),
    ):
        shutdown = getattr(executor, "shutdown", None)
        if callable(shutdown):
            try:
                shutdown(wait=False, cancel_futures=True)
                stats["executors_shutdown"] += 1
            except TypeError:
                shutdown(wait=False)
                stats["executors_shutdown"] += 1
            except Exception as exc:
                _log.warning("shutdown %s executor failed: %s", name, exc)

    _log.info("room handlers shutdown complete timeout_sec=%.1f stats=%s", timeout_sec, stats)
    return stats


def _safe_terminate(proc: subprocess.Popen) -> None:
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


def _wait_for_recording_file(room, timeout_sec: float = 8.0) -> bool:
    """等待录制文件物理创建（FFmpeg 启动延迟）。

    录制启动后 record_output_path 会立即设置，但 FFmpeg 子进程需要
    2-5 秒才真正创建文件。此函数在超时内轮询等待文件出现。
    若录制进程已退出但文件仍未出现，立即返回 False 避免无效等待。
    """
    path = getattr(room, "record_output_path", "")
    if not path:
        return False
    if os.path.isfile(path):
        return True
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        time.sleep(0.5)
        if not getattr(room, "is_recording", False):
            _log.warning("等待录制文件时录制已停止: room=%s", getattr(room, "room_id", "?"))
            return os.path.isfile(getattr(room, "record_output_path", ""))
        room_refreshed = getattr(room, "record_output_path", "")
        if room_refreshed and os.path.isfile(room_refreshed):
            return True
    return False


def _validate_synced_analysis_targets(
    manager,
    main_room_id,
    target_room_ids,
    wait_for_file: bool = False,
) -> tuple[bool, str, Any | None, list[Any]]:
    main_room = manager.get_room(main_room_id)
    if main_room is None:
        return False, "主房间不存在", None, []

    main_record_path = getattr(main_room, "record_output_path", "")
    if not main_record_path:
        return False, "主房间录制文件不存在", None, []
    if not os.path.isfile(main_record_path) and (not wait_for_file or not _wait_for_recording_file(main_room)):
        return False, "主房间录制文件不存在", None, []

    seen: set[str] = set()
    unique_target_ids: list[str] = []
    if main_room_id:
        seen.add(main_room_id)
        unique_target_ids.append(main_room_id)
    for room_id in target_room_ids or []:
        if not room_id or room_id in seen:
            continue
        seen.add(room_id)
        unique_target_ids.append(room_id)

    multi_room = len(unique_target_ids) > 1
    main_group = getattr(main_room, "align_group_id", "") or ""
    if multi_room and not main_group:
        return False, "主房间未对齐，请先一键对齐", None, []

    target_rooms: list[Any] = []
    for room_id in unique_target_ids:
        room = manager.get_room(room_id)
        if room is None:
            return False, f"目标房间不存在: {room_id}", None, []
        record_path = getattr(room, "record_output_path", "")
        if not record_path:
            return False, f"目标房间录制文件不存在: {room_id}", None, []
        if not os.path.isfile(record_path) and (not wait_for_file or not _wait_for_recording_file(room)):
            return False, f"目标房间录制文件不存在: {room_id}", None, []
        if multi_room and (getattr(room, "align_group_id", "") or "") != main_group:
            return False, f"房间 {room_id} 与主房间不在同一对齐组，请重新一键对齐", None, []
        target_rooms.append(room)

    return True, "", main_room, target_rooms


def _map_highlight_to_room(highlight, main_room, target_room) -> dict[str, Any]:
    source_start = float(highlight.get("start", 0) or 0)
    source_end = float(highlight.get("end", 0) or 0)
    main_rec = float(getattr(main_room, "recording_start_mono", 0.0) or 0.0)
    target_rec = float(getattr(target_room, "recording_start_mono", 0.0) or 0.0)
    delta = (main_rec - target_rec) + (
        float(getattr(main_room, "content_offset", 0.0) or 0.0)
        - float(getattr(target_room, "content_offset", 0.0) or 0.0)
    )
    mapped = dict(highlight)
    mapped.update({
        "start": max(0.0, source_start + delta),
        "end": max(0.0, source_end + delta),
        "room_id": getattr(target_room, "room_id", ""),
        "source_room_id": getattr(main_room, "room_id", ""),
        "source_start": source_start,
        "source_end": source_end,
        "offset_delta": delta,
    })
    return mapped


def _map_highlights_by_room(highlights, main_room, target_rooms) -> dict[str, list[dict[str, Any]]]:
    mapped_by_room: dict[str, list[dict[str, Any]]] = {
        getattr(room, "room_id", ""): [] for room in target_rooms
    }
    for highlight in highlights:
        source_start = float(highlight.get("start", 0) or 0)
        source_end = float(highlight.get("end", 0) or 0)
        if source_start >= source_end:
            continue
        for room in target_rooms:
            room_id = getattr(room, "room_id", "")
            mapped = _map_highlight_to_room(highlight, main_room, room)
            if float(mapped.get("start", 0) or 0) < float(mapped.get("end", 0) or 0):
                mapped_by_room[room_id].append(mapped)
    return mapped_by_room


def _detect_audio_energy_peaks(
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
        subprocess.run(cmd, capture_output=True, timeout=60)
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


def _detect_rounds_by_audio_rhythm(
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
        subprocess.run(cmd, capture_output=True, timeout=120)
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


def _new_rounds(
    prev: list[dict[str, Any]],
    current: list[dict[str, Any]],
    overlap_tol: float = 5.0,
) -> list[dict[str, Any]]:
    """全量重扫下的回合级去重：返回 current 中与 prev 无时间重叠的全新回合。

    持续分析每轮 detect_valorant_rounds 全量重扫产出从头到当前的完整回合集，
    大部分回合与上一轮重复（边界可能微调）。本函数按时间区间重叠判定：current
    中某回合若与 prev 任一回合有实质重叠（重叠 > overlap_tol 秒），视为已存在，
    否则视为新回合。仅用于前端"新增 N 个高光"增量提示，不影响累计集本身。
    """
    if not prev:
        return list(current)
    fresh: list[dict[str, Any]] = []
    for cur in current:
        cs, ce = cur.get('start', 0.0), cur.get('end', 0.0)
        overlaps = False
        for p in prev:
            ps, pe = p.get('start', 0.0), p.get('end', 0.0)
            inter = min(ce, pe) - max(cs, ps)
            if inter > overlap_tol:
                # A pending round may receive a confirmed OCR end on a later
                # trailing scan; let that update become a new export candidate.
                if (
                    not _is_auto_exportable_valorant_round(p)
                    and _is_auto_exportable_valorant_round(cur)
                ):
                    if p.get("round_key") and not cur.get("round_key"):
                        cur = dict(cur)
                        cur["round_key"] = p["round_key"]
                    continue
                overlaps = True
                break
        if not overlaps:
            fresh.append(cur)
    return fresh


def _drop_open_tail_rounds(
    rounds: list[dict[str, Any]],
    current_dur: float,
    tail_margin: float = 5.0,
) -> list[dict[str, Any]]:
    """持续分析只发布已闭合回合：过滤仍贴着录制尾部的未闭合回合。

    使用两段式 margin 判定：
    - end >= current_dur - 3s：尾部数据不完整（_margin），直接丢弃
    - end >= current_dur - 20s：回合可能仍在进行中（pending_margin），标记为 phase="pending" 保留
    """
    if not rounds:
        return []
    cleaned = list(rounds)
    last = cleaned[-1]
    try:
        end = float(last.get("end", 0.0))
    except (TypeError, ValueError):
        end = 0.0
    # 明确的 open_tail 需要保留，等待下一次回看确认结束边界。
    if last.get("tail_by") == "open_tail":
        last["phase"] = "pending"
        return cleaned
    # 3s 内：数据本身不完整，丢弃
    if end >= current_dur - tail_margin:
        return cleaned[:-1]
    # 20s 内：回合可能仍在进行中，标记为 pending 保留（前端可展示"进行中"）
    pending_margin = 20.0
    if end >= current_dur - pending_margin:
        last["phase"] = "pending"
    return cleaned


def _is_auto_exportable_valorant_round(round_data: dict[str, Any]) -> bool:
    """Return whether a round has confirmed OCR boundaries for auto-export.

    Only OCR-confirmed rounds (start_by=ocr_buy_exit, end_by=ocr_result/next_buy)
    are auto-exported. Audio-only / full_round fuzzy boundaries must wait for OCR.
    Segments shorter than _VALORANT_MIN_EXPORT_DURATION_SEC are treated as false
    buy-phase clips (e.g. 回合3_218s ≈ 27s) and rejected.
    """
    try:
        start = float(round_data.get("start", 0.0))
        end = float(round_data.get("end", 0.0))
    except (TypeError, ValueError):
        return False
    if end - start < _VALORANT_MIN_EXPORT_DURATION_SEC:
        return False
    return (
        end > start
        and round_data.get("phase") != "pending"
        and round_data.get("start_by") == "ocr_buy_exit"
        and round_data.get("end_by") in {"ocr_result", "next_buy"}
    )


def _valorant_round_key(round_data: dict[str, Any]) -> str:
    """Return a boundary-stable key for one Valorant round."""
    existing = str(round_data.get("round_key") or "").strip()
    if existing:
        return existing
    try:
        start = float(round_data.get("start", 0.0))
    except (TypeError, ValueError):
        start = 0.0
    # ponytail: quantize the start only to absorb small OCR boundary drift.
    return f"round-{int(round(start / 10.0)):06d}"


def _merge_round_windows(
    existing: list[dict[str, Any]],
    window_rounds: list[dict[str, Any]],
    overlap_tol: float = 5.0,
) -> list[dict[str, Any]]:
    """Merge Valorant incremental window rounds with stable prior rounds.

    Window analysis may shift the same round by several seconds as the local
    audio threshold changes. If a new window round substantially overlaps an
    existing one, treat it as the newer boundary for that same round rather
    than keeping both.
    """
    if not existing:
        merged = [dict(item) for item in window_rounds]
        for item in merged:
            item.setdefault("round_key", _valorant_round_key(item))
        return merged
    if not window_rounds:
        return [dict(item) for item in existing]

    def _span(item: dict[str, Any]) -> tuple[float, float]:
        try:
            return float(item.get("start", 0.0)), float(item.get("end", 0.0))
        except (TypeError, ValueError):
            return 0.0, 0.0

    kept: list[dict[str, Any]] = []
    for old in existing:
        old_start, old_end = _span(old)
        overlaps_window = False
        for new in window_rounds:
            new_start, new_end = _span(new)
            if min(old_end, new_end) - max(old_start, new_start) > overlap_tol:
                overlaps_window = True
                if old.get("round_key") and not new.get("round_key"):
                    new["round_key"] = old["round_key"]
                break
        if not overlaps_window:
            kept.append(dict(old))

    merged = sorted(
        kept + [dict(item) for item in window_rounds],
        key=lambda item: _span(item)[0],
    )
    for item in merged:
        item.setdefault("round_key", _valorant_round_key(item))
    cleaned: list[dict[str, Any]] = []
    for item in merged:
        start, end = _span(item)
        if end - start < 5.0:
            continue
        if cleaned:
            prev_start, prev_end = _span(cleaned[-1])
            if start < prev_end:
                if end > prev_end:
                    cleaned[-1]["end"] = round(start, 3)
                    if float(cleaned[-1].get("end", 0.0)) - prev_start < 5.0:
                        cleaned.pop()
                    cleaned.append(dict(item))
                continue
        cleaned.append(dict(item))
    return cleaned


def _round_lists_changed(
    prev: list[dict[str, Any]],
    current: list[dict[str, Any]],
    tol: float = 0.5,
) -> bool:
    if len(prev) != len(current):
        return True
    for old, new in zip(prev, current, strict=False):
        try:
            old_start = float(old.get("start", 0.0))
            old_end = float(old.get("end", 0.0))
            new_start = float(new.get("start", 0.0))
            new_end = float(new.get("end", 0.0))
        except (TypeError, ValueError):
            return True
        if abs(old_start - new_start) > tol or abs(old_end - new_end) > tol:
            return True
    return False


def _continuous_valorant_refine_with_ocr(
    mode: str,
    pressure: dict[str, Any] | None = None,
) -> bool:
    """Return whether continuous Valorant analysis may use OCR at all.

    Soft pressure / critical-without-pause keep OCR enabled but with a longer
    sample interval (see ocr_sample_interval). Only extreme pause_analysis
    disables OCR for this tick (audio-first catch-up).
    """
    if mode != "valorant_round":
        return False
    pressure = pressure or {}
    if pressure.get("pause_analysis"):
        return False
    return True


def _continuous_valorant_scan_budget(
    mode: str,
    last_analyzed: float,
    current_dur: float,
    pressure: dict[str, Any] | None = None,
    tick_count: int = 0,
) -> tuple[tuple[float, float], bool, int, bool]:
    """Return scan range, OCR flag, timeout, and whether this is the first scan.

    Incremental scans catch up from last_analyzed (with lookback overlap), never
    jump to a trailing tip window that would skip the middle of the recording.
    """
    pressure = pressure or {}
    try:
        lookback = float(pressure.get("analysis_window_sec", _VALORANT_INCREMENTAL_LOOKBACK_SEC))
    except (TypeError, ValueError):
        lookback = _VALORANT_INCREMENTAL_LOOKBACK_SEC
    lookback = max(20.0, lookback)

    full_rescan = last_analyzed <= 0.0
    if full_rescan:
        scan_start = 0.0
        scan_end = float(current_dur)
    else:
        # 从已分析点回看 lookback，再向前追赶；禁止 current_dur - lookback 跳窗漏扫
        scan_start = max(0.0, float(last_analyzed) - lookback)
        scan_end = min(float(current_dur), float(last_analyzed) + _VALORANT_MAX_CATCHUP_SEC)
        if scan_end < scan_start:
            scan_end = float(current_dur)
    use_ocr = _continuous_valorant_refine_with_ocr(mode, pressure)

    scan_range = (round(scan_start, 3), round(float(scan_end), 3))
    scan_duration = max(1.0, scan_range[1] - scan_range[0])
    timeout = max(45, int(scan_duration / 180.0 * 12) + 45)
    return scan_range, use_ocr, timeout, full_rescan


def _continuous_effective_interval(
    interval: int,
    last_analyzed: float,
    valorant_incremental: bool,
    pressure: dict[str, Any] | None,
) -> tuple[int, bool]:
    """Return continuous-analysis delay and whether this pass should skip."""
    base_interval = max(10, int(interval))
    effective_interval = base_interval

    pressure = pressure or {}
    multiplier = pressure.get("analysis_interval_multiplier", 1)
    try:
        multiplier = max(1, int(multiplier))
    except (TypeError, ValueError):
        multiplier = 1

    if pressure.get("pause_analysis"):
        retry_after = pressure.get("retry_after_sec", effective_interval)
        try:
            return max(10, int(retry_after)), True
        except (TypeError, ValueError):
            return effective_interval * multiplier, True

    return effective_interval * multiplier, False


def _cleanup_segments(segments: list[dict[str, Any]], min_duration: float = 5.0) -> list[dict[str, Any]]:
    """清理片段列表：过滤过短段、移除重叠、按时间排序。"""
    if not segments:
        return []

    # 过滤 < 5s 的垃圾片段
    filtered = [s for s in segments if s.get('end', 0) - s.get('start', 0) >= 5]
    if not filtered:
        return []

    # 按开始时间排序
    filtered.sort(key=lambda s: s.get('start', 0.0))

    # 移除重叠：前一片段 end 裁剪到后一片段 start - 1
    cleaned: list[dict[str, Any]] = [dict(filtered[0])]
    for seg in filtered[1:]:
        if seg['start'] < cleaned[-1]['end']:
            cleaned[-1]['end'] = seg['start'] - 1.0
            if cleaned[-1]['end'] - cleaned[-1]['start'] < min_duration:
                cleaned.pop()
        cleaned.append(dict(seg))

    return cleaned


def _scene_ocr_detection(
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


def _merge_scene_and_ocr(
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


def _run_scene_analysis(
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

    duration = _get_video_duration(video_path)
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
        if time_range is not None:
            tr_start, tr_end = time_range
            # -ss input seek（快速）+ -t duration，限定增量分析范围
            cmd += ["-ss", f"{tr_start:.3f}", "-t", f"{tr_end - tr_start:.3f}"]
        cmd += [
            "-i", video_path,
            "-vf", f"select='gt(scene\\\\,{ts_threshold})',showinfo",
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
                    _safe_terminate(proc)
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
            _safe_terminate(proc)
            return []
        finally:
            if proc.poll() is None:
                _safe_terminate(proc)
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
        audio_highlights = _detect_audio_energy_peaks(
            video_path, duration, ffmpeg_path=_ffmpeg,
            time_range=time_range, cancel_check=cancel_check,
        )
        if audio_highlights:
            _log.info("音频能量检测: 发现 %d 段高光 (path=%s)", len(audio_highlights), video_path)
        else:
            _log.warning("音频能量检测也无结果 (path=%s)", video_path)

        # 尝试 OCR 检测补充信号
        ocr_highlights = _scene_ocr_detection(
            video_path, _ffmpeg, duration, progress_callback, cancel_check, time_range,
            enabled=enable_ocr,
        )
        if ocr_highlights:
            _log.info("OCR 检测补充: 发现 %d 段高光 (path=%s)", len(ocr_highlights), video_path)
            if progress_callback:
                progress_callback("scene", 100.0, f"检测完成：{len(audio_highlights) + len(ocr_highlights)} 段")
            return _merge_scene_and_ocr(audio_highlights, ocr_highlights)

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
    ocr_highlights = _scene_ocr_detection(
        video_path, _ffmpeg, duration, progress_callback, cancel_check, time_range,
        enabled=enable_ocr,
    )
    if ocr_highlights:
        _log.info("OCR 补充检测: %d 段高光 (path=%s)", len(ocr_highlights), video_path)
        result = _merge_scene_and_ocr(result, ocr_highlights)

    _log.info("场景检测完成: %d 段高光 (from %d 切换点, threshold=%.2f)",
              len(result), len(timestamps), threshold)
    if progress_callback:
        progress_callback("scene", 100.0, f"场景检测完成：{len(result)} 段")
    return result


def _analyze_scene_or_rounds(
    video_path: str,
    game: str,
    threshold: float,
    progress_callback: Callable[[str, float, str], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> list[dict[str, Any]] | None:
    """scene 模式的统一分析入口：Valorant 优先回合分割，其余走场景检测。

    对一次性分析（录制已结束的完整文件）：
    - game="valorant" 时先用 detect_valorant_rounds 全量回合分割（按完整回合切，
      不受固定时间间隔切断），并启用 OCR 边界校正（refine_with_ocr=True）；
    - 无结果或非 valorant，回退到 _run_scene_analysis 场景检测。

    统一 handle_start_analysis / handle_start_analysis_export 两个 handler 的 scene
    行为，消除重复的快速路径代码。

    Returns:
        高光段列表；被取消时返回 None（与 _run_scene_analysis 语义一致）。
    """
    if game == 'valorant':
        try:
            from lsc.analyzer.round_detector import detect_valorant_rounds
            from lsc.config import load_config as _load_cfg_r
            _cfg = _load_cfg_r()
            _ffmpeg = _cfg.ffmpeg_path or shutil.which("ffmpeg") or "ffmpeg"
            if progress_callback:
                progress_callback("round_detect", 0.0, "Valorant 回合检测中...")
            highlights = detect_valorant_rounds(
                video_path,
                ffmpeg_path=_ffmpeg,
                progress_callback=progress_callback,
                cancel_check=cancel_check,
                refine_with_ocr=True,  # 一次性分析文件已完整，可用 OCR 校正
            )
            if cancel_check and cancel_check():
                return None
            if highlights:
                _log.info("Valorant 回合检测: %d 回合 (path=%s)",
                          len(highlights), os.path.basename(video_path))
                return highlights
            _log.info("Valorant 回合检测无结果，回退到场景检测")
        except Exception as exc:
            _log.warning("Valorant 回合检测失败，回退到场景检测: %s", exc)

    return _run_scene_analysis(
        video_path, threshold=threshold,
        progress_callback=progress_callback, cancel_check=cancel_check,
    )


def _get_video_duration(video_path: str) -> float:
    """Get video duration in seconds using ffprobe."""
    import json as _json

    from lsc.config import load_config as _load_cfg2
    _cfg2 = _load_cfg2()
    _ffprobe = _cfg2.ffprobe_path or shutil.which("ffprobe") or "ffprobe"

    try:
        result = subprocess.run(
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


def _expand_user_path(path: str) -> str:
    if path.startswith('~'):
        return os.path.expanduser(path)
    return path


def _parse_fps(framerate: str) -> float:
    """Parse framerate string to float. Returns 0.0 for 原画/auto."""
    if not framerate or framerate == '原画':
        return 0.0
    try:
        return float(framerate)
    except (ValueError, TypeError):
        return 0.0


def _safe_float(value, default: float = 0.0) -> float:
    """安全地将值转换为 float，转换失败时返回默认值。"""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _get_export_preset(preset_id: str) -> dict[str, Any] | None:
    """Get export preset configuration by ID."""
    presets = {
        'douyin_vertical': {
            'codec': 'h264_nvenc',
            'crf': 23,
            'resolution': '1080:1920',
            'framerate': '30',
            'audio_bitrate': '128k',
            'vertical_crop': True,
        },
        'bilibili_horizontal': {
            'codec': 'h264_nvenc',
            'crf': 23,
            'resolution': '1920:1080',
            'framerate': '30',
            'audio_bitrate': '128k',
            'vertical_crop': False,
        },
        'original': {
            'codec': 'copy',
            'crf': 0,
            'resolution': '',
            'framerate': '原画',
            'audio_bitrate': '128k',
            'vertical_crop': False,
        },
        'high_quality': {
            'codec': 'h264_nvenc',
            'crf': 18,
            'resolution': '',
            'framerate': '60',
            'audio_bitrate': '256k',
            'vertical_crop': False,
        },
        'small_file': {
            'codec': 'hevc_nvenc',
            'crf': 28,
            'resolution': '1280:720',
            'framerate': '24',
            'audio_bitrate': '96k',
            'vertical_crop': False,
        },
    }
    return presets.get(preset_id)


_settings_cache: dict[str, Any] | None = None
_settings_cache_mtime: float = 0.0
_settings_cache_ttl: float = 5.0
_settings_cache_time: float = 0.0


def load_settings():
    """从 settings.json 加载应用设置，失败时返回默认值。

    带文件修改时间缓存：5 秒内重复调用且文件未修改时直接返回缓存，
    减少批量导出等场景的冗余磁盘 IO。
    """
    global _settings_cache, _settings_cache_mtime, _settings_cache_time
    now = time.time()
    if _settings_cache is not None and (now - _settings_cache_time) < _settings_cache_ttl:
        return _settings_cache
    if os.path.exists(SETTINGS_FILE):
        try:
            mtime = os.path.getmtime(SETTINGS_FILE)
            if mtime == _settings_cache_mtime:
                _settings_cache_time = now
                return _settings_cache
            with open(SETTINGS_FILE, encoding='utf-8') as f:
                _settings_cache = json.load(f)
            _settings_cache_mtime = mtime
            _settings_cache_time = now
            return _settings_cache
        except Exception as exc:
            _log.warning("加载设置文件失败，使用默认值: %s", exc)
    _settings_cache = {
        'output_dir': os.path.join(os.path.expanduser('~'), 'LSC', 'output'),
        'theme': 'dark',
        'encoder': 'h264_nvenc',
        'quality': '原画',
        'param_mode': 'CRF 质量',
        'crf': 23,
        'bitrate': 8000,
        'bitrate_unit': 'kbps',
        'resolution': '原画',
        'framerate': '原画',
        'audio_bitrate': '128k',
        'preview_quality': '高清',
        'default_export_preset': 'douyin_vertical',
    }
    _settings_cache_time = now
    return _settings_cache


# 预览画质预设：分辨率 + NVENC 码率 + libx264 CRF/码率
_PREVIEW_QUALITY_PRESETS: dict[str, dict[str, Any]] = {
    '原画': {'width': 0, 'height': 0, 'nvenc_bitrate': '8000k', 'x264_crf': 20, 'x264_bitrate': '6000k'},
    '高清': {'width': 1280, 'height': 720, 'nvenc_bitrate': '2500k', 'x264_crf': 26, 'x264_bitrate': '1800k'},
    '标清': {'width': 854, 'height': 480, 'nvenc_bitrate': '1500k', 'x264_crf': 30, 'x264_bitrate': '1000k'},
    '流畅': {'width': 640, 'height': 360, 'nvenc_bitrate': '800k', 'x264_crf': 32, 'x264_bitrate': '600k'},
}


def _get_preview_quality_preset(quality: str) -> dict[str, Any]:
    """返回预览画质预设参数，未知值回退到 '高清'。"""
    return _PREVIEW_QUALITY_PRESETS.get(quality, _PREVIEW_QUALITY_PRESETS['高清'])


def save_settings(settings: dict):
    try:
        os.makedirs(os.path.dirname(SETTINGS_FILE), exist_ok=True)
        _atomic_write_json(SETTINGS_FILE, settings)
    except OSError as exc:
        _log.error("保存设置失败: %s", exc)
        raise


def get_storage_info():
    """获取输出目录存储信息（总大小、磁盘总量、切片数量）。"""
    settings = load_settings()
    output_dir = _expand_user_path(settings.get('output_dir', os.path.join(os.path.expanduser('~'), 'LSC', 'output')))
    if not os.path.exists(output_dir):
        os.makedirs(output_dir, exist_ok=True)

    total_size = 0
    clip_count = 0
    for dirpath, _dirnames, filenames in os.walk(output_dir):
        for f in filenames:
            fp = os.path.join(dirpath, f)
            if os.path.exists(fp):
                total_size += os.path.getsize(fp)
                if f.endswith(('.mp4', '.mkv', '.flv', '.mov', '.ts')):
                    clip_count += 1

    try:
        disk_usage = shutil.disk_usage(output_dir)
        total_disk = disk_usage.total / (1024 ** 3)
    except Exception:
        total_disk = 50

    return {
        'used': total_size / (1024 ** 3),
        'total': total_disk,
        'clip_count': clip_count,
    }


def get_disk_usage_info():
    """获取输出目录磁盘使用情况（总容量、已用、可用）。"""
    settings = load_settings()
    output_dir = _expand_user_path(settings.get('output_dir', os.path.join(os.path.expanduser('~'), 'LSC', 'output')))
    if not os.path.exists(output_dir):
        os.makedirs(output_dir, exist_ok=True)

    try:
        total, used, free = shutil.disk_usage(output_dir)
    except Exception:
        return {'total': 0, 'used': 0, 'free': 0}

    return {'total': total, 'used': used, 'free': free}


def _room_to_dict(room: Any) -> dict[str, Any]:
    """将 Room 对象序列化为前端可消费的字典。"""
    stream_url = ''
    if room.stream_info and room.stream_info.stream_url:
        stream_url = room.stream_info.stream_url

    started_at = None
    if room.record_started_at is not None:
        if isinstance(room.record_started_at, datetime):
            started_at = room.record_started_at.isoformat()
        else:
            started_at = datetime.fromtimestamp(float(room.record_started_at)).isoformat()

    room_id = room.room_id
    return {
        'room_id': room_id,
        'room_url': room.room_url,
        'platform': room.platform,
        'platform_name': room.platform_name,
        'streamer_name': room.streamer_name,
        'stream_title': room.stream_title,
        'stream_url': stream_url,
        'is_connecting': room.is_connecting,
        'is_connected': room.is_connected,
        'is_recording': room.is_recording,
        'is_recording_starting': room_id in _recording_starting,
        'is_reconnecting': getattr(room, 'is_reconnecting', False),
        'record_output_path': room.record_output_path or "",
        'record_started_at': started_at,
        'record_size_mb': room.record_size_mb,
        'last_error': room.last_error,
        'preview_enabled': room.preview_enabled,
        'preview_paused': room.preview_paused,
        'preview_muted': room.preview_muted,
        'preview_quality': getattr(room, 'preview_quality', '') or '',
        'mark_in': room.mark_in,
        'mark_out': room.mark_out,
        'mark_in_wallclock': room.mark_in_wallclock,
        'mark_out_wallclock': room.mark_out_wallclock,
        'recording_start_mono': room.recording_start_mono,
        'recording_media_start_mono': getattr(room, 'recording_media_start_mono', None),
        'preview_latency': room.preview_latency,
        'content_offset': getattr(room, 'content_offset', 0.0),
        'align_group_id': getattr(room, 'align_group_id', '') or '',
        'category': getattr(room, 'category', '') or '',
        'preview_epoch_id': getattr(room, 'preview_epoch_id', '') or '',
        'recording_id': getattr(room, 'recording_id', '') or '',
    }


def _rooms_list(manager: MultiRoomManager):
    """将 manager 中的所有房间序列化为字典列表。"""
    return [_room_to_dict(r) for r in manager.list_rooms()]


def _persist_current_rooms(manager: MultiRoomManager) -> bool:
    """将当前 manager 中的房间列表持久化到 JSON。"""
    return save_rooms(_rooms_list(manager))


def _get_current_pos(room: Any) -> float:
    """获取当前播放/录制位置（秒）。"""
    if room.controller is not None:
        pos = getattr(room.controller, 'current_sec', 0)
        # Electron 模式下 current_sec 可能恒为 0，回退到录制时长
        if pos is not None and pos > 0:
            return float(pos)
    if room.is_recording and room.record_started_at is not None:
        if isinstance(room.record_started_at, datetime):
            return (datetime.now() - room.record_started_at).total_seconds()
        return 0.0
    return 0.0


class _RoomsThrottle:
    """rooms_updated 广播节流：首次立即发送，300ms 内合并后续更新。"""
    _MERGE_WINDOW_SEC = 0.3

    def __init__(self) -> None:
        self._last_send_time = 0.0
        self._pending = False

    def should_send_immediate(self) -> bool:
        """首次立即发送；后续在合并窗口外也立即发送。"""
        now = time.monotonic()
        if self._last_send_time == 0.0:
            self._last_send_time = now
            return True
        if now - self._last_send_time >= self._MERGE_WINDOW_SEC:
            self._last_send_time = now
            return True
        self._pending = True
        return False

    def mark_pending(self) -> None:
        """标记有未发送的更新。"""
        self._pending = True

    @property
    def has_pending(self) -> bool:
        return self._pending


def register_room_handlers(server, bridge):
    manager: MultiRoomManager = bridge.manager

    # rooms_updated 广播节流：首次立即发送，300ms 内合并后续更新
    _rooms_throttle = _RoomsThrottle()
    _rooms_throttle_task: asyncio.Task | None = None

    def _do_broadcast_rooms():
        nonlocal _rooms_throttle_task
        msg = {
            'type': 'rooms_updated',
            'data': {'rooms': _rooms_list(manager)},
        }
        _rooms_throttle_task = asyncio.create_task(server.broadcast(msg['type'], msg['data']))
        _rooms_throttle_task.add_done_callback(lambda t: t.exception() if not t.cancelled() and t.exception() else None)

    def _broadcast_rooms(*, force: bool = False):
        """广播 rooms_updated。force=True 时跳过 300ms 合并，用于用户操作即时反馈。"""
        nonlocal _rooms_throttle_task
        if force:
            _rooms_throttle._pending = False
            _rooms_throttle._last_send_time = time.monotonic()
            _do_broadcast_rooms()
            return
        if _rooms_throttle.should_send_immediate():
            _do_broadcast_rooms()
            return
        # 合并窗口内的后续更新：启动延迟发送任务
        if _rooms_throttle_task is not None and not _rooms_throttle_task.done():
            return
        async def _flush():
            await asyncio.sleep(_RoomsThrottle._MERGE_WINDOW_SEC)
            if _rooms_throttle.has_pending:
                _rooms_throttle._pending = False
                _rooms_throttle._last_send_time = time.monotonic()
                _do_broadcast_rooms()
            _rooms_throttle_task = None
        _rooms_throttle_task = asyncio.create_task(_flush())

    def _attach_shared_preview_handle(room_id: str, shared_ingest, loop):
        return _preview_stream_registry().attach_shared(
            room_id,
            shared_ingest,
            on_init_segment=lambda seg: asyncio.run_coroutine_threadsafe(
                server.broadcast('mse_init', {
                    'room_id': room_id,
                    'data': base64.b64encode(seg).decode('ascii'),
                }),
                loop,
            ),
            on_media_segment=lambda seg: asyncio.run_coroutine_threadsafe(
                server.broadcast('mse_segment', {
                    'room_id': room_id,
                    'data': base64.b64encode(seg).decode('ascii'),
                }),
                loop,
            ),
            on_error=lambda err: (
                asyncio.run_coroutine_threadsafe(
                    server.broadcast('mse_error', {
                        'room_id': room_id,
                        'error': err,
                    }),
                    loop,
                ),
                _stop_idle_shared_ingest(room_id, reason="shared preview error"),
            ),
        )

    async def _reattach_shared_preview_after_recording_start(room_id: str, room) -> bool:
        try:
            shared_enabled = bool(getattr(load_config(), 'shared_ingest_enabled', False))
        except Exception as exc:
            _log.debug("shared ingest config check failed during preview reattach: %s", exc)
            return False
        if not shared_enabled or room is None or not getattr(room, 'preview_enabled', False):
            return False

        shared_ingest = _shared_ingests.get(room_id)
        if (
            shared_ingest is None
            or not getattr(shared_ingest, 'recording_active', False)
            or getattr(shared_ingest, 'is_stopped', True)
        ):
            return False

        existing = _preview_stream_registry().get(room_id)
        if existing is not None and getattr(existing, '_ingest', None) is shared_ingest:
            try:
                existing.replay_init()
            except Exception as exc:
                _log.debug("shared preview init replay failed during recording start: %s", exc)
            return True

        loop = asyncio.get_running_loop()
        shared_handle = None
        try:
            _configure_shared_preview_quality(shared_ingest)
            shared_handle = _attach_shared_preview_handle(room_id, shared_ingest, loop)
            shared_handle.replay_init()
        except Exception as exc:
            _log.warning(
                "shared preview reattach after recording start failed: room_id=%s, error=%s",
                room_id,
                exc,
            )
            if shared_handle is not None:
                try:
                    shared_handle.stop()
                except Exception as stop_exc:
                    _log.debug("shared preview cleanup failed after reattach error: %s", stop_exc)
            return False

        if existing is not None and existing is not shared_handle:
            def _stop_existing_preview():
                try:
                    existing.stop()
                except Exception as exc:
                    _log.debug("legacy preview stop failed after shared reattach: %s", exc)
                return True

            await asyncio.get_running_loop().run_in_executor(_bridge_executor, _stop_existing_preview)
            _stop_idle_shared_ingest(room_id, reason="preview reattached to shared recording")

        def _set_preview_on():
            current = manager.get_room(room_id)
            if current is not None:
                current.preview_enabled = True
            return True

        try:
            await asyncio.get_running_loop().run_in_executor(
                _bridge_executor, lambda: bridge.call(_set_preview_on)
            )
        except Exception as exc:
            _log.debug("preview state sync failed after shared reattach: %s", exc)
        return True

    def _queue_rooms_update(*_args, **_kwargs):
        """Qt 主线程槽：异步操作（连接/录制）完成后通过线程安全队列广播 rooms_updated。

        此时 manager 的房间状态已更新（is_connected/is_recording/stream_url 等），
        借助 bridge.queue_broadcast 推送给前端，避免 asyncio 事件循环不可用的限制。
        """
        bridge.queue_broadcast({
            'type': 'rooms_updated',
            'data': {'rooms': _rooms_list(manager)},
        })

    # 连接/录制均通过后台 worker 异步完成，状态变更发生在信号发射时，
    # 必须在此补充 rooms_updated 广播，否则前端房间卡片状态永远停留在旧值。
    # 注意：MultiRoomManager 只有 room_connect_finished / batch_record_progress /
    # batch_record_finished / global_tick 信号，没有 recording_started/recording_stopped。
    # room_connect_finished 覆盖单房间连接完成；batch_record_progress 覆盖批量录制启动结果。
    # 单房间 start_recording / stop_recording 是同步调用，调用方 handler 会直接广播 rooms_updated。
    manager.room_connect_finished.connect(_queue_rooms_update)
    manager.batch_record_progress.connect(_queue_rooms_update)
    manager.batch_record_finished.connect(_queue_rooms_update)
    # 每 5 秒中频 tick 时广播 rooms_updated，让前端刷新录制文件大小
    manager.medium_tick.connect(_queue_rooms_update)

    def _broadcast_system_stats():
        """广播系统资源快照到前端。"""
        try:
            settings = load_settings()
            output_dir = _expand_user_path(settings.get('output_dir', ''))
            stats = collect_system_stats(output_dir, extra=_ingest_diagnostics())
            bridge.queue_broadcast({'type': 'system_stats', 'data': stats})
        except Exception as exc:
            _log.debug("System stats broadcast failed: %s", exc)

    manager.low_tick.connect(lambda: _broadcast_system_stats())

    @server.on_connect
    async def handle_connect(websocket):
        """新客户端连接时推送已保存的房间列表。

        优先使用 MultiRoomManager.load_rooms() 恢复房间（含 mark_in /
        mark_out / preview_muted / include_in_cut 等用户偏好），避免
        旧实现仅用 URL 重新 add_room 导致偏好丢失。
        仅在 manager 当前无房间时恢复，避免重复加载。
        """
        def _restore():
            existing = manager.list_rooms()
            if existing:
                _log.info("已有 %d 个房间，跳过恢复", len(existing))
                return len(existing)
            try:
                count = manager.load_rooms()
                _log.info("从持久化恢复 %d 个房间", count)
                return count
            except Exception as exc:
                _log.error("manager.load_rooms failed: %s", exc)
                # 回退到旧持久化路径（仅恢复 URL，无偏好）
                rooms = load_rooms()
                if not rooms:
                    return 0
                restored = 0
                for r in rooms:
                    url = r.get('room_url')
                    if not url:
                        continue
                    if manager.add_room(url) is not None:
                        restored += 1
                _log.info("从旧持久化恢复 %d 个房间（仅URL）", restored)
                return restored
        try:
            await asyncio.get_running_loop().run_in_executor(
                _bridge_executor, lambda: bridge.call(_restore)
            )
        except Exception as exc:
            _log.error("Restore rooms failed: %s", exc)
        # 推送 manager 中的房间（room_id 与 manager 一致，含用户偏好）
        await websocket.send(json.dumps({
            'type': 'rooms_loaded',
            'data': {'rooms': _rooms_list(manager)},
        }))
        # 推送已保存的设置（含 appSettings 主题/语言等），确保前端启动时恢复记忆
        try:
            saved = load_settings()
            await websocket.send(json.dumps({
                'type': 'settings_loaded',
                'data': saved,
            }))
        except Exception as exc:
            _log.error("Push settings on connect failed: %s", exc)

        # 推送当前持续分析状态（如果有）
        if _continuous_tasks:
            active_room_id = next(iter(_continuous_tasks))
            task = _continuous_tasks[active_room_id]
            phase = 'finalizing' if task.get('finalizing') else ('completed' if task.get('completed') else 'running')
            scan_range = task.get('scan_range', (0.0, 0.0))
            await websocket.send(json.dumps({
                'type': 'continuous_analysis_status',
                'data': {
                    'running': phase != 'completed',
                    'room_id': active_room_id,
                    'target_room_ids': task.get('target_room_ids', []),
                    'mode': task.get('mode', 'scene'),
                    'analyzed_duration': task.get('last_analyzed', 0.0),
                    'total_highlights': len(task.get('highlights', [])),
                    'phase': phase,
                    'updated_at': time.time(),
                    'scan_mode': 'full' if task.get('full_rescan') else 'incremental',
                    'scan_range': list(scan_range) if isinstance(scan_range, tuple) else scan_range,
                    'scan_timeout': task.get('scan_timeout', 120),
                    'full_rescan': bool(task.get('full_rescan', False)),
                    'refine_with_ocr': bool(task.get('refine_with_ocr', False)),
                    'progress': min(100.0, max(0.0, (task.get('last_analyzed', 0.0) / max(float(scan_range[1]) if isinstance(scan_range, tuple) and len(scan_range) > 1 else 1.0, 1.0)) * 100.0)),
                },
            }))

        # 预检测 NVENC 可用性（在后台线程中执行，不阻塞连接流程）
        # 首次预览时无需再等待 NVENC 检测，减少 1-3 秒延迟
        def _precheck_nvenc():
            try:
                from lsc.core.services.mse_streamer import _check_nvenc
                _check_nvenc()
            except Exception as exc:
                _log.debug("操作异常（已忽略）: %s", exc)
        asyncio.get_running_loop().run_in_executor(None, _precheck_nvenc)

    @server.on('get_rooms')
    async def handle_get_rooms(data):
        """获取当前所有房间列表。"""
        _log.debug("获取房间列表: %d 个", len(manager.list_rooms()))
        return {'rooms': _rooms_list(manager)}

    @server.on('refresh_room_status')
    async def handle_refresh_room_status(data):
        """刷新房间状态：清除错误标记，不阻断正在进行的录制/预览/分析。

        只清除 last_error / preview_error 等瞬态错误字段，
        不触碰 is_recording / is_reconnecting / is_connecting 等运行状态。
        正在重连的房间保留错误信息（用户需要看到重连进度）。
        """
        room_id = data.get('room_id')
        rooms_to_refresh = []
        if room_id:
            room = manager.get_room(room_id)
            if room:
                rooms_to_refresh = [room]
        else:
            rooms_to_refresh = list(manager._rooms.values())

        refreshed = 0
        for room in rooms_to_refresh:
            # 正在重连的房间跳过（保留错误信息供用户查看进度）
            if getattr(room, 'is_reconnecting', False):
                continue
            changed = False
            if getattr(room, 'last_error', None):
                room.last_error = None
                changed = True
            if getattr(room, 'preview_error', None):
                room.preview_error = None
                changed = True
            if changed:
                refreshed += 1

        await _broadcast_rooms()
        _log.info("刷新房间状态: %d 个房间错误已清除", refreshed)
        return {'success': True, 'refreshed': refreshed}

    @server.on('save_rooms')
    async def handle_save_rooms(data):
        """保存前端传入的房间列表。"""
        rooms = data.get('rooms', [])
        if not isinstance(rooms, list):
            _log.warning("save_rooms 校验失败: rooms 不是列表")
            return {'success': False, 'error': 'rooms 必须是列表'}
        for room in rooms:
            if not isinstance(room, dict):
                _log.warning("save_rooms 校验失败: 房间数据不是对象")
                return {'success': False, 'error': '房间数据必须是对象'}
            if not isinstance(room.get('room_id'), str):
                _log.warning("save_rooms 校验失败: room_id 不是字符串")
                return {'success': False, 'error': 'room_id 必须是字符串'}
            if not isinstance(room.get('room_url'), str):
                _log.warning("save_rooms 校验失败: room_url 不是字符串")
                return {'success': False, 'error': 'room_url 必须是字符串'}
        success = save_rooms(rooms)
        _log.info("save_rooms: 保存 %d 个房间, success=%s", len(rooms), success)
        return {'success': success}

    @server.on('load_rooms')
    async def handle_load_rooms(data):
        """从持久化文件加载房间列表并广播。"""
        rooms = load_rooms()
        _log.info("加载房间列表: %d 个", len(rooms))
        await server.broadcast('rooms_loaded', {'rooms': rooms})
        return {'success': True, 'rooms': rooms}

    @server.on('add_room')
    async def handle_add_room(data):
        """添加新房间（通过直播间 URL）。"""
        url = data.get('url', '').strip()
        if not url:
            return {'success': False, 'error': '请输入直播间链接'}
        _log.info("添加房间: url=%s", url)

        def _add():
            return manager.add_room(url)

        try:
            room = await asyncio.get_running_loop().run_in_executor(
                _bridge_executor, lambda: bridge.call(_add, timeout=30.0)
            )
        except TimeoutError:
            _log.warning("添加房间超时: url=%s", url)
            return {'success': False, 'error': '添加房间超时，请重试'}
        except Exception as exc:
            _log.error("添加房间异常: url=%s, error=%s", url, exc)
            return {'success': False, 'error': humanize_error(str(exc))}

        if room is None:
            _log.warning("添加房间失败（达上限）: url=%s", url)
            return {'success': False, 'error': '房间数已达上限'}

        _broadcast_rooms()
        _persist_current_rooms(manager)
        _log.info("房间添加成功: room_id=%s, platform=%s, streamer=%s", room.room_id, room.platform, room.streamer_name)
        return {'success': True, 'room_id': room.room_id}

    @server.on('connect_room')
    async def handle_connect_room(data):
        """连接指定房间的直播间。"""
        room_id = data.get('room_id')
        if not room_id:
            return {'error': 'room_id is required'}
        _log.info("连接房间: room_id=%s", room_id)

        def _connect():
            settings = load_settings()
            return manager.connect_room(room_id, async_mode=True, quality_preset=settings.get('quality', '原画'))

        try:
            success = await asyncio.get_running_loop().run_in_executor(_bridge_executor, lambda: bridge.call(_connect))
        except Exception as exc:
            _log.error("连接房间异常: room_id=%s, error=%s", room_id, exc)
            return {'success': False, 'error': humanize_error(str(exc)), 'room_id': room_id}
        _broadcast_rooms(force=True)
        _log.info("连接房间完成: room_id=%s, success=%s", room_id, success)
        return {'success': bool(success), 'room_id': room_id}

    @server.on('disconnect_room')
    async def handle_disconnect_room(data):
        """断开指定房间的连接。"""
        room_id = data.get('room_id')
        if not room_id:
            return {'error': 'room_id is required'}
        _log.info("断开房间连接: room_id=%s", room_id)

        # 清理 MSE streamer，防止僵尸 streamer 阻止后续预览启动
        stale_streamer = _preview_stream_registry().pop(room_id)
        if stale_streamer is not None:
            _log.info("清理断开房间的 MSE streamer: room_id=%s", room_id)
            def _stop_streamer():
                try:
                    stale_streamer.stop()
                except Exception as exc:
                    _log.debug("停止 streamer 失败 (disconnect): %s", exc)
            await asyncio.get_running_loop().run_in_executor(_bridge_executor, _stop_streamer)
            _stop_idle_shared_ingest(room_id, reason="room disconnected")

        bridge.submit(manager.disconnect_room, room_id)
        _broadcast_rooms(force=True)
        _log.info("断开连接指令已提交: room_id=%s", room_id)
        return {'success': True}

    @server.on('set_preview_muted')
    async def handle_set_preview_muted(data):
        """设置房间预览静音状态。"""
        room_id = data.get('room_id')
        muted = bool(data.get('muted', False))
        if not room_id:
            return {'error': 'room_id is required'}
        _log.debug("设置静音: room_id=%s, muted=%s", room_id, muted)

        # 必须等 Qt 写完 preview_muted 再广播，否则会用旧值覆盖前端乐观更新
        try:
            await asyncio.get_running_loop().run_in_executor(
                _bridge_executor,
                lambda: bridge.call(manager.set_preview_muted, room_id, muted),
            )
        except Exception as exc:
            _log.warning("设置静音失败: room_id=%s, error=%s", room_id, exc)
            return {'success': False, 'error': humanize_error(str(exc)), 'room_id': room_id}
        _broadcast_rooms(force=True)
        return {'success': True, 'room_id': room_id}

    @server.on('set_preview_quality')
    async def handle_set_preview_quality(data):
        """保存预览画质并立即重启预览以生效（支持录制中切换）。"""
        room_id = data.get('room_id')
        quality = data.get('quality')
        if not room_id or not quality:
            return {'success': False, 'error': 'room_id and quality are required'}
        _quality_map = {
            'original': '原画', 'hd': '高清', 'sd': '标清', 'ld': '流畅',
            'high': '高清', 'medium': '标清', 'low': '流畅',
            '原画': '原画', '高清': '高清', '标清': '标清', '流畅': '流畅',
        }
        quality = _quality_map.get(quality, quality)
        _log.info("保存预览画质: room_id=%s, quality=%s", room_id, quality)

        # 1. 保存到全局 settings
        settings = load_settings()
        settings['preview_quality'] = quality
        save_settings(settings)

        # 2. 更新 room.preview_quality（前端可感知当前画质）
        def _set_room_quality():
            room = manager.get_room(room_id)
            if room is not None:
                room.preview_quality = quality
            return room is not None
        try:
            await asyncio.get_running_loop().run_in_executor(
                _bridge_executor, lambda: bridge.call(_set_room_quality)
            )
        except Exception as exc:
            _log.warning("更新 room.preview_quality 失败: room_id=%s, error=%s", room_id, exc)

        # 3. 如果预览正在运行，重启预览以应用新画质
        def _check_preview():
            room = manager.get_room(room_id)
            if room is not None:
                return room.preview_enabled and room.is_connected
            return False
        try:
            was_preview_enabled = await asyncio.get_running_loop().run_in_executor(
                _bridge_executor, lambda: bridge.call(_check_preview)
            )
        except Exception:
            was_preview_enabled = False

        if was_preview_enabled:
            _log.info("重启预览以应用新画质: room_id=%s, quality=%s", room_id, quality)
            # 停止预览（等 stop 完成再启动，消除竞态）
            try:
                await _handle_mse_preview(server, manager, room_id, False, {'mode': 'mse'})
            except Exception as exc:
                _log.warning("停止预览失败(画质切换): room_id=%s, error=%s", room_id, exc)
            # 重新启动预览（force_restart=True 确保录制中也重启 FFmpeg）
            try:
                await _handle_mse_preview(server, manager, room_id, True, {'mode': 'mse'}, force_restart=True)
            except Exception as exc:
                _log.error("重启预览失败: room_id=%s, error=%s", room_id, exc)

        _broadcast_rooms()
        return {'success': True}

    @server.on('start_recording')
    async def handle_start_recording(data):
        """开始录制指定房间（支持并发限流，最多同时 2 路）。"""
        _rec_log = logging.getLogger('lsc.recording')
        room_id = data.get('room_id')
        _rec_log.info("[录制] start_recording request for room_id=%s", room_id)
        if not room_id:
            return {'error': 'room_id is required'}

        settings = load_settings()
        output_dir = _expand_user_path(settings.get('output_dir', os.path.join(os.path.expanduser('~'), 'LSC', 'output')))
        encoder = settings.get('encoder', 'H.264 NVENC')
        crf = int(settings.get('crf', 23))
        param_mode = settings.get('param_mode', 'CRF 质量')
        bitrate = str(settings.get('bitrate', 8000))
        bitrate_unit = settings.get('bitrate_unit', 'kbps')
        resolution = settings.get('resolution', '原画')
        framerate = settings.get('framerate', '原画')
        audio_bitrate = settings.get('audio_bitrate', '128k')

        def _start():
            # 注意：不通过 bridge.call 切回 Qt 主线程，而是在 executor 线程中
            # 直接调用 manager.start_recording。这与 _BatchRecordWorker 的模式
            # 一致（manager 仅做属性读写 + subprocess 启动，不依赖 Qt 事件循环），
            # 避免刷新流 URL（HTTP 请求最长 36s）和 FFmpeg 首帧探测（最长 5s）
            # 阻塞 Qt 主线程导致预览/心跳冻结。项目记忆硬约束：
            # "Recording start/preview reconnect operations must be executed in
            #  background threads to prevent main thread blocking"。
            return manager.start_recording(
                room_id, output_dir, encoder, crf,
                param_mode=param_mode, bitrate=bitrate, bitrate_unit=bitrate_unit,
                resolution=resolution, framerate=framerate, audio_bitrate=audio_bitrate,
            )

        # 防重复提交：同一房间正在启动录制时拒绝重复请求
        if room_id in _recording_starting:
            _rec_log.warning("[录制] room %s already starting", room_id)
            return {
                'success': False,
                'error': '该房间正在启动录制中，请稍候',
                'room_id': room_id,
            }
        _recording_starting.add(room_id)
        # 立即广播 is_recording_starting，让前端按钮立刻进入 loading
        _broadcast_rooms(force=True)
        success = False
        error_msg: str | None = None
        try:
            # 并发限流：最多 2 路同时启动录制（Semaphore 在 asyncio 上下文 await，不占 executor 线程）
            _rec_log.info("[录制] acquiring semaphore for room %s", room_id)
            async with _recording_semaphore:
                _rec_log.info("[录制] semaphore acquired, submitting to executor for room %s", room_id)
                success = await asyncio.get_running_loop().run_in_executor(_recording_executor, _start)
            _rec_log.info("[录制] executor returned success=%s for room %s", success, room_id)
        except Exception as exc:
            _rec_log.error("[录制] exception for room %s: %s", room_id, exc, exc_info=True)
            error_msg = humanize_error(str(exc))
            success = False
        finally:
            _recording_starting.discard(room_id)

        room = await asyncio.get_running_loop().run_in_executor(_bridge_executor, lambda: bridge.call(manager.get_room, room_id))
        if room and room.is_recording:
            recording_history.append({
                'title': room.streamer_name or '未知主播',
                'platform': room.platform_name,
                'start_time': datetime.now().isoformat(),
                'room_id': room_id,
            })
            _save_recording_history(recording_history)
            if success:
                await _reattach_shared_preview_after_recording_start(room_id, room)

        _broadcast_rooms(force=True)
        if error_msg is not None:
            return {'success': False, 'error': error_msg, 'room_id': room_id}
        if not success:
            # 获取房间的具体错误信息，避免前端显示"未知错误"
            room = manager.get_room(room_id)
            fail_msg = (room.last_error if room else None) or '录制启动失败，请检查房间状态'
            _rec_log.warning("[录制] failed for room %s, last_error=%s", room_id, fail_msg)
            return {'success': False, 'error': humanize_error(fail_msg), 'room_id': room_id}
        return {'success': True, 'room_id': room_id}

    @server.on('stop_recording')
    async def handle_stop_recording(data):
        """停止录制指定房间。"""
        room_id = data.get('room_id')
        if not room_id:
            return {'error': 'room_id is required'}
        _log.info("停止录制: room_id=%s", room_id)

        def _stop_async():
            return manager.stop_recording_async(room_id)

        try:
            success = await asyncio.get_running_loop().run_in_executor(
                _bridge_executor, lambda: bridge.call(_stop_async, timeout=5.0)
            )
        except Exception as exc:
            _log.error("停止录制异常: room_id=%s, error=%s", room_id, exc)
            _broadcast_rooms()
            return {'success': False, 'error': humanize_error(str(exc))}
        _log.info("停止录制完成: room_id=%s, success=%s", room_id, success)

        for record in reversed(recording_history):
            if record.get('room_id') == room_id and 'end_time' not in record:
                record['end_time'] = datetime.now().isoformat()
                start = datetime.fromisoformat(record['start_time'])
                end = datetime.fromisoformat(record['end_time'])
                duration = (end - start).total_seconds()
                record['duration'] = f"{int(duration // 3600):02d}:{int((duration % 3600) // 60):02d}:{int(duration % 60):02d}"
                break
        _save_recording_history(recording_history)

        _broadcast_rooms()
        return {'success': bool(success)}

    @server.on('remove_room')
    async def handle_remove_room(data):
        """移除指定房间。"""
        room_id = data.get('room_id')
        if not room_id:
            return {'error': 'room_id is required'}
        _log.info("移除房间: room_id=%s", room_id)

        stale_streamer = _preview_stream_registry().pop(room_id)
        if stale_streamer is not None:
            _log.info("清理移除房间的 MSE streamer: room_id=%s", room_id)
            def _stop_streamer():
                try:
                    stale_streamer.stop()
                except Exception as exc:
                    _log.debug("停止 streamer 失败 (remove): %s", exc)
            await asyncio.get_running_loop().run_in_executor(_bridge_executor, _stop_streamer)
            _stop_idle_shared_ingest(room_id, reason="room removed")

        bridge.submit(manager.remove_room, room_id)
        _broadcast_rooms()
        _persist_current_rooms(manager)
        _log.info("房间已移除: room_id=%s", room_id)
        return {'success': True}

    @server.on('seek')
    async def handle_seek(data):
        """跳转到指定时间位置。"""
        room_id = data.get('room_id')
        time_pos = _safe_float(data.get('time', 0))
        if not room_id:
            return {'error': 'room_id is required'}
        _log.debug("seek: room_id=%s, time=%.2f", room_id, time_pos)

        def _seek():
            room = manager.get_room(room_id)
            if room is None or room.controller is None:
                return False
            controller = room.controller
            controller.current_sec = time_pos
            widget = room.preview_widget
            if widget is not None:
                seek_fn = getattr(widget, 'seek', None)
                if callable(seek_fn):
                    try:
                        seek_fn(time_pos)
                        return True
                    except Exception as exc:
                        _log.debug("操作异常（已忽略）: %s", exc)
            return True

        success = await asyncio.get_running_loop().run_in_executor(_bridge_executor, lambda: bridge.call(_seek))
        return {'success': bool(success)}

    @server.on('set_mark_in')
    async def handle_set_mark_in(data):
        """设置入点（剪辑起始标记）。

        live=True（默认）：实时按键标记，捕获 wallclock 用于精确导出映射。
        live=False：时间线拖动标记，不捕获 wallclock，导出时走降级路径。
        """
        room_id = data.get('room_id')
        if not room_id:
            return {'error': 'room_id is required'}
        time_value = data.get('time')
        live = data.get('live', True)
        _log.debug("设置入点: room_id=%s, time=%s, live=%s", room_id, time_value, live)

        # 实时标记才捕获 wallclock；拖动标记不捕获（wallclock 不代表内容时刻）
        import time as _time
        captured_wallclock = _time.monotonic() if live else None
        # 删除入点 (time: null) 场景不需要 wallclock
        if time_value is None and 'time' in data:
            captured_wallclock = None

        def _mark():
            room = manager.get_room(room_id)
            if room is None:
                return None
            if time_value is None and 'time' in data:
                # time: null → 删除入点
                room.mark_in = None
                room.mark_in_wallclock = None
                return None
            if time_value is not None:
                room.mark_in = float(time_value)
            else:
                room.mark_in = _get_current_pos(room)
            room.mark_in_wallclock = captured_wallclock
            return room.mark_in

        value = await asyncio.get_running_loop().run_in_executor(_bridge_executor, lambda: bridge.call(_mark))
        _broadcast_rooms()
        return {'success': True, 'mark_in': value}

    @server.on('set_mark_out')
    async def handle_set_mark_out(data):
        """设置出点（剪辑结束标记）。

        live=True（默认）：实时按键标记，捕获 wallclock 用于精确导出映射。
        live=False：时间线拖动标记，不捕获 wallclock，导出时走降级路径。
        """
        room_id = data.get('room_id')
        if not room_id:
            return {'error': 'room_id is required'}
        time_value = data.get('time')
        live = data.get('live', True)
        _log.debug("设置出点: room_id=%s, time=%s, live=%s", room_id, time_value, live)

        import time as _time
        captured_wallclock = _time.monotonic() if live else None
        if time_value is None and 'time' in data:
            captured_wallclock = None

        def _mark():
            room = manager.get_room(room_id)
            if room is None:
                return None
            if time_value is None and 'time' in data:
                # time: null → 删除出点
                room.mark_out = None
                room.mark_out_wallclock = None
                return None
            if time_value is not None:
                room.mark_out = float(time_value)
            else:
                room.mark_out = _get_current_pos(room)
            room.mark_out_wallclock = captured_wallclock
            return room.mark_out

        value = await asyncio.get_running_loop().run_in_executor(_bridge_executor, lambda: bridge.call(_mark))
        _broadcast_rooms()
        return {'success': True, 'mark_out': value}

    @server.on('toggle_play_pause')
    async def handle_toggle_play_pause(data):
        """切换预览播放/暂停。"""
        room_id = data.get('room_id')
        if not room_id:
            return {'error': 'room_id is required'}
        _log.debug("切换播放/暂停: room_id=%s", room_id)

        def _toggle():
            room = manager.get_room(room_id)
            if room is None:
                return False
            room.preview_paused = not room.preview_paused
            _log.debug("toggle_play_pause: room_id=%s, paused=%s", room_id, room.preview_paused)
            widget = room.preview_widget
            if widget is not None:
                pause_fn = getattr(widget, 'pause', None)
                if callable(pause_fn):
                    try:
                        pause_fn(room.preview_paused)
                        return True
                    except Exception as exc:
                        _log.debug("操作异常（已忽略）: %s", exc)
            return True

        success = await asyncio.get_running_loop().run_in_executor(_bridge_executor, lambda: bridge.call(_toggle))
        _broadcast_rooms()
        return {'success': bool(success)}

    @server.on('seek_relative')
    async def handle_seek_relative(data):
        """相对跳转（在当前时间上增加偏移）。"""
        room_id = data.get('room_id')
        offset = _safe_float(data.get('offset', 0))
        if not room_id:
            return {'error': 'room_id is required'}
        _log.debug("相对跳转: room_id=%s, offset=%.2f", room_id, offset)

        def _seek():
            room = manager.get_room(room_id)
            if room is None or room.controller is None:
                return False
            controller = room.controller
            new_pos = max(0, (controller.current_sec or 0) + offset)
            controller.current_sec = new_pos
            widget = room.preview_widget
            if widget is not None:
                seek_fn = getattr(widget, 'seek', None)
                if callable(seek_fn):
                    try:
                        seek_fn(new_pos)
                        return True
                    except Exception as exc:
                        _log.debug("操作异常（已忽略）: %s", exc)
            return True

        success = await asyncio.get_running_loop().run_in_executor(_bridge_executor, lambda: bridge.call(_seek))
        return {'success': bool(success)}

    @server.on('fullscreen')
    async def handle_fullscreen(data):
        """全屏切换（由前端直接处理）。"""
        room_id = data.get('room_id')
        if not room_id:
            return {'error': 'room_id is required'}
        _log.debug("全屏切换: room_id=%s", room_id)
        return {'success': True, 'note': 'fullscreen should be handled by frontend'}

    @server.on('get_history')
    async def handle_get_history(data):
        """获取最近录制历史（最多 20 条）。"""
        _log.debug("获取录制历史: %d 条", len(recording_history))
        formatted = []
        for record in reversed(recording_history[-20:]):
            formatted.append({
                'title': record.get('title', '未知'),
                'platform': record.get('platform', '未知'),
                'duration': record.get('duration', '--:--:--'),
                'size': '0 MB',
                'time': record.get('start_time', ''),
            })
        return {'history': formatted}

    @server.on('get_storage')
    async def handle_get_storage(data):
        """获取存储信息。"""
        _log.debug("获取存储信息")
        return get_storage_info()

    @server.on('get_disk_usage')
    async def handle_get_disk_usage(data):
        """获取磁盘使用情况。"""
        _log.debug("获取磁盘使用情况")
        return get_disk_usage_info()

    @server.on('get_system_stats')
    async def handle_get_system_stats(data):
        """获取系统资源快照。"""
        _log.debug("获取系统资源快照")
        settings = load_settings()
        output_dir = _expand_user_path(settings.get('output_dir', ''))
        stats = collect_system_stats(output_dir, extra=_ingest_diagnostics())
        return {'type': 'system_stats', 'data': stats}

    @server.on('get_settings')
    async def handle_get_settings(data):
        """获取应用设置。"""
        _log.debug("获取应用设置")
        return load_settings()

    @server.on('save_settings')
    async def handle_save_settings(data):
        """保存应用设置。"""
        if not isinstance(data, dict):
            _log.warning("save_settings 校验失败: data 不是对象")
            return {'success': False, 'error': '设置数据必须是对象'}
        if not isinstance(data.get('output_dir'), str):
            _log.warning("save_settings 校验失败: output_dir 不是字符串")
            return {'success': False, 'error': 'output_dir 必须是字符串'}
        try:
            save_settings(data)
        except OSError as exc:
            from lsc.utils.error_messages import humanize_error
            _log.error("保存设置失败: %s", exc)
            return {'success': False, 'error': humanize_error(str(exc))}
        _log.info("设置已保存: output_dir=%s", data.get('output_dir', ''))
        return {'success': True}

    @server.on('get_douyin_cookie_status')
    async def handle_get_douyin_cookie_status(data):
        """查询抖音 Cookie 是否已配置。"""
        from lsc.platforms.cookie_helper import get_douyin_cookie_status
        try:
            return {'success': True, **get_douyin_cookie_status()}
        except Exception as exc:
            _log.warning("get_douyin_cookie_status failed: %s", exc)
            return {'success': False, 'error': str(exc), 'configured': False, 'count': 0}

    @server.on('save_douyin_cookies')
    async def handle_save_douyin_cookies(data):
        """保存用户粘贴的抖音 Cookie（JSON / Cookie 头）。"""
        from lsc.platforms.cookie_helper import save_douyin_cookies_from_text
        raw = ''
        if isinstance(data, dict):
            raw = str(data.get('cookies') or data.get('text') or '')
        if not raw.strip():
            return {'success': False, 'error': '请粘贴 Cookie 内容'}
        try:
            status = save_douyin_cookies_from_text(raw)
            _log.info("抖音 Cookie 已保存: count=%s", status.get('count'))
            return {'success': True, **status}
        except (ValueError, json.JSONDecodeError) as exc:
            return {'success': False, 'error': str(exc)}
        except OSError as exc:
            from lsc.utils.error_messages import humanize_error
            _log.error("保存抖音 Cookie 失败: %s", exc)
            return {'success': False, 'error': humanize_error(str(exc))}

    @server.on('set_content_offset')
    async def handle_set_content_offset(data):
        """设置房间的音频互相关内容偏移量（由前端音频对齐后回传）。"""
        room_id = data.get('room_id')
        offset = float(data.get('offset', 0.0))
        if not room_id:
            return {'error': 'room_id is required'}
        _log.info("设置 content_offset: room_id=%s, offset=%.4fs", room_id, offset)
        def _set():
            room = manager.get_room(room_id)
            if room is not None:
                room.content_offset = offset
        await asyncio.get_running_loop().run_in_executor(_bridge_executor, lambda: bridge.call(_set))
        return {'success': True}

    @server.on('align_previews')
    async def handle_align_previews(data):
        """对齐所有预览到直播流（仅非 Electron 模式）。"""
        _log.info("对齐预览（非Electron模式）")
        try:
            count = await asyncio.get_running_loop().run_in_executor(
                _bridge_executor, lambda: bridge.call(manager.align_previews_to_live)
            )
            _log.info("对齐预览完成: %d 个房间", count)
            return {'success': True, 'aligned': count}
        except Exception as exc:
            _log.error("对齐预览失败: %s", exc)
            return {'success': False, 'error': str(exc)}

    @server.on('align_audio')
    async def handle_align_audio(data):
        """启动多房间音频互相关对齐（后台线程执行，基于录制文件）。

        参数:
            data: 需包含 room_ids 列表，至少 2 个房间才可执行。

        返回:
            成功时返回 {'success': True, 'message': '音频对齐已启动'}，
            失败时返回 {'success': False, 'error': 错误信息}。

        对齐结果通过 align_audio_result 广播异步返回前端。
        """
        room_ids = data.get('room_ids', [])
        _align_log = logging.getLogger('lsc.align')
        _align_log.info("收到音频对齐请求: room_ids=%s", room_ids)
        if len(room_ids) < 2:
            _align_log.warning("音频对齐请求房间数不足: %d", len(room_ids))
            return {'success': False, 'error': '至少需要 2 个房间'}
        try:
            _align_log.info("调用 start_audio_align: rooms=%s", room_ids)
            bridge.call(manager.start_audio_align, room_ids, timeout=5.0)
            _align_log.info("音频对齐已启动: rooms=%s", room_ids)
            return {'success': True, 'message': '音频对齐已启动'}
        except Exception as exc:
            _align_log.error("音频对齐启动失败: %s", exc, exc_info=True)
            return {'success': False, 'error': str(exc)}

    @server.on('align_preview_audio')
    async def handle_align_preview_audio(data):
        """多房间预览音频互相关对齐（同步，基于前端发送的 PCM 数据）。

        前端通过 Web Audio API 从 <video> 元素捕获音频 PCM，base64 编码后
        发送到后端。后端解码后直接运行互相关计算，返回偏移量。

        参数:
            data: 需包含 rooms 列表，每项包含 room_id, sample_rate, pcm_base64。

        返回:
            {'success': True, 'offsets': {...}, 'reference_room_id': '...', 'scores': {...}}
            或 {'success': False, 'error': '错误信息'}
        """
        rooms_data = data.get('rooms', [])
        _align_log = logging.getLogger('lsc.align')
        _align_log.info("收到预览音频对齐请求: rooms=%d", len(rooms_data))
        if len(rooms_data) < 2:
            _align_log.warning("预览音频对齐请求房间数不足: %d", len(rooms_data))
            return {'success': False, 'error': '至少需要 2 个房间'}
        try:
            import base64

            from lsc.editor.audio_aligner import align_audio_map

            # 解码 PCM 数据
            audio_map: dict[str, np.ndarray] = {}
            for rd in rooms_data:
                room_id = rd.get('room_id', '')
                sample_rate = int(rd.get('sample_rate', 16000))
                pcm_b64 = rd.get('pcm_base64', '')
                diagnostics = rd.get('diagnostics') or {}
                _align_log.info(
                    "预览音频诊断: room_id=%s, current_time=%s, buffer=%s-%s, ingest_mode=%s, "
                    "ready_state=%s, has_audio_track=%s, rms=%s, sample_count=%s, capture_reason=%s",
                    room_id,
                    diagnostics.get('current_time'),
                    diagnostics.get('buffer_start'),
                    diagnostics.get('buffer_end'),
                    diagnostics.get('ingest_mode'),
                    diagnostics.get('ready_state'),
                    diagnostics.get('has_audio_track'),
                    diagnostics.get('rms'),
                    diagnostics.get('sample_count'),
                    diagnostics.get('capture_reason'),
                )
                if not room_id or not pcm_b64:
                    _align_log.warning("预览音频对齐跳过: room_id=%s, 缺少数据", room_id)
                    continue
                try:
                    raw = base64.b64decode(pcm_b64)
                    samples = np.frombuffer(raw, dtype=np.float32)
                    if samples.size < sample_rate:  # 至少1秒
                        _align_log.warning("预览音频对齐跳过: room_id=%s, 样本过少=%d", room_id, samples.size)
                        continue
                    audio_map[room_id] = samples
                    _align_log.info("解码预览音频: room_id=%s, samples=%d (%.2fs), rate=%d",
                                    room_id, samples.size, samples.size / sample_rate, sample_rate)
                except Exception as exc:
                    _align_log.warning("预览音频解码失败: room_id=%s, error=%s", room_id, exc)

            valid_ids = list(audio_map.keys())
            if len(valid_ids) < 2:
                _align_log.warning("有效预览音频不足 2 路: %s", valid_ids)
                return {'success': False, 'error': '有效音频不足 2 路，无法互相关对齐'}

            result = align_audio_map(audio_map, sample_rate, method='preview_audio')
            if not result.success:
                return {'success': False, 'error': result.error, 'precision': 'buffer_only'}

            _align_log.info(
                "预览音频对齐完成: reference=%s, offsets=%s, scores=%s",
                result.reference_room_id,
                {k: f"{v:.4f}" for k, v in result.offsets.items()},
                {k: f"{v:.3f}" for k, v in result.correlation_scores.items()},
            )

            # 仅对置信度 ≥ 0.3 的房间写入 offset/group；可信不足 2 路则不建组
            _ALIGN_TRUST_THRESHOLD = 0.3
            offsets = result.offsets
            scores = result.correlation_scores
            trusted = {
                rid: float(offset)
                for rid, offset in offsets.items()
                if float(scores.get(rid, 0.0) or 0.0) >= _ALIGN_TRUST_THRESHOLD
            }
            if len(trusted) < 2:
                _align_log.warning(
                    "可信对齐房间不足 %d/2，不写入 align_group_id: trusted=%s",
                    len(trusted),
                    list(trusted.keys()),
                )
                return {
                    'success': False,
                    'error': '可信对齐不足，无法建立对齐组',
                    'offsets': result.offsets,
                    'reference_room_id': result.reference_room_id,
                    'method': result.method,
                    'scores': result.correlation_scores,
                    'precision': 'buffer_only',
                }

            import time as _align_time
            group_id = f"align_{int(_align_time.time())}"

            def _apply_alignment():
                for rid, offset in offsets.items():
                    room = manager.get_room(rid)
                    if room is None:
                        continue
                    score = float(scores.get(rid, 0.0) or 0.0)
                    if score < _ALIGN_TRUST_THRESHOLD:
                        # 低置信：强制 0，不写入 align_group_id
                        room.content_offset = 0.0
                        continue
                    room.content_offset = float(offset)
                    room.align_group_id = group_id
                return True

            try:
                await asyncio.get_running_loop().run_in_executor(
                    _bridge_executor, lambda: bridge.call(_apply_alignment)
                )
            except Exception as exc:
                _align_log.warning("写入对齐组失败: %s", exc)

            return {
                'success': True,
                'offsets': result.offsets,
                'reference_room_id': result.reference_room_id,
                'method': result.method,
                'scores': result.correlation_scores,
                'align_group_id': group_id,
            }
        except Exception as exc:
            _align_log.error("预览音频对齐失败: %s", exc, exc_info=True)
            return {'success': False, 'error': str(exc)}

    @server.on('check_dependencies')
    async def handle_check_dependencies(data):
        """检测系统依赖状态：FFmpeg / FFprobe / NVENC / Python"""
        from lsc.config import load_config as _load_config
        from lsc.core.services.mse_streamer import _check_nvenc
        from lsc.utils.process_launcher import prepare_launch as _prepare_launch

        cfg = _load_config()
        _log.info("检测依赖: ffmpeg=%s, ffprobe=%s, nvenc=%s",
                  cfg.ffmpeg_path or shutil.which("ffmpeg"),
                  cfg.ffprobe_path or shutil.which("ffprobe"),
                  _check_nvenc() if (cfg.ffmpeg_path or shutil.which("ffmpeg")) else False)
        results = {}

        # FFmpeg
        ffmpeg_path = cfg.ffmpeg_path or shutil.which("ffmpeg") or ""
        ffmpeg_ok = bool(ffmpeg_path) and os.path.isfile(ffmpeg_path)
        ffmpeg_version = ""
        if ffmpeg_ok:
            try:
                env, cflags, cwd = _prepare_launch(ffmpeg_path)
                rkw = {"capture_output": True, "text": True, "timeout": 5, "env": env}
                if cwd:
                    rkw["cwd"] = cwd
                if cflags:
                    rkw["creationflags"] = cflags
                r = subprocess.run([ffmpeg_path, "-version"], **rkw)
                if r.returncode == 0:
                    ffmpeg_version = r.stdout.split('\n')[0].strip()
            except Exception as exc:
                _log.debug("检测 FFmpeg 版本失败: %s", exc)
        results['ffmpeg'] = {'available': ffmpeg_ok, 'path': ffmpeg_path, 'version': ffmpeg_version}

        # FFprobe
        ffprobe_path = cfg.ffprobe_path or shutil.which("ffprobe") or ""
        ffprobe_ok = bool(ffprobe_path) and os.path.isfile(ffprobe_path)
        ffprobe_version = ""
        if ffprobe_ok:
            try:
                env, cflags, cwd = _prepare_launch(ffprobe_path)
                rkw = {"capture_output": True, "text": True, "timeout": 5, "env": env}
                if cwd:
                    rkw["cwd"] = cwd
                if cflags:
                    rkw["creationflags"] = cflags
                r = subprocess.run([ffprobe_path, "-version"], **rkw)
                if r.returncode == 0:
                    ffprobe_version = r.stdout.split('\n')[0].strip()
            except Exception as exc:
                _log.debug("检测 FFprobe 版本失败: %s", exc)
        results['ffprobe'] = {'available': ffprobe_ok, 'path': ffprobe_path, 'version': ffprobe_version}

        # NVENC
        nvenc_ok = _check_nvenc() if ffmpeg_ok else False
        results['nvenc'] = {'available': nvenc_ok, 'path': '', 'version': 'h264_nvenc' if nvenc_ok else ''}

        # Python
        py_version = f"Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        results['python'] = {'available': True, 'path': sys.executable, 'version': py_version}

        return {'success': True, 'dependencies': results}

    @server.on('export_clip')
    async def handle_export_clip(data):
        """导出视频切片 — 统一入队到全局导出队列。"""
        room_id = data.get('room_id')
        start_sec = _safe_float(data.get('start', 0))
        end_sec = _safe_float(data.get('end', 0))
        label = data.get('label', 'clip')
        preset_id = data.get('preset_id', '')
        job_id = data.get('job_id', '')
        source = data.get('source', '')

        # 列表导出携带的墙钟快照（优先于房间当前 mark_*_wallclock）
        mark_in_wallclock = data.get('mark_in_wallclock')
        mark_out_wallclock = data.get('mark_out_wallclock')
        recording_start_mono = data.get('recording_start_mono')
        recording_media_start_mono = data.get('recording_media_start_mono')
        use_room_marks = bool(data.get('use_room_marks', False))

        _log.info("导出切片: room_id=%s, start=%.2f, end=%.2f, label=%s, preset=%s, job_id=%s",
                  room_id, start_sec, end_sec, label, preset_id, job_id)

        result = await queue_export(
            room_id, start_sec, end_sec, label, preset_id, source, job_id,
            mark_in_wallclock=mark_in_wallclock,
            mark_out_wallclock=mark_out_wallclock,
            recording_start_mono=recording_start_mono,
            recording_media_start_mono=recording_media_start_mono,
            use_room_marks=use_room_marks,
        )

        if result.get('error'):
            return {'success': False, 'error': result['error']}
        return {
            'success': True,
            'job_id': result['job_id'],
            'queued': True,
            'precision': result.get('precision'),
        }

    @server.on('cancel_export')
    async def handle_cancel_export(data):
        """取消导出任务 — 支持取消排队中和进行中的任务。"""
        job_id = data.get('job_id', '')
        if not job_id:
            return {'success': False, 'error': 'job_id is required'}

        # 情况 1：任务正在执行（已经注册了 clip_id）
        clip_id = export_jobs.get(job_id)
        if clip_id:
            _log.info("取消导出(执行中): job_id=%s, clip_id=%s", job_id, clip_id)
            def _cancel():
                return manager.cancel_export(clip_id)
            try:
                cancelled = await asyncio.get_running_loop().run_in_executor(
                    _bridge_executor, lambda: bridge.call(_cancel)
                )
            except Exception as exc:
                _log.error("取消导出异常: job_id=%s, error=%s", job_id, exc)
                return {'success': False, 'error': humanize_error(str(exc))}
            if cancelled:
                export_jobs.pop(job_id, None)
                _log.info("导出已取消: job_id=%s", job_id)
                return {'success': True}
            return {'success': False, 'error': 'job not found'}

        # 情况 2：任务还在排队中（尚未注册 clip_id）— 标记为取消
        _export_cancelled_jobs.add(job_id)
        _log.info("取消导出(排队中): job_id=%s", job_id)
        return {'success': True, 'note': 'queued job marked as cancelled'}

    @server.on('enable_preview')
    async def handle_enable_preview(data):
        """开启/关闭房间预览（支持 qt / electron / mse 模式）。"""
        room_id = data.get('room_id')
        enabled = bool(data.get('enabled', True))
        mode = data.get('mode', 'qt')  # 'qt' | 'electron' | 'mse'
        if not room_id:
            return {'error': 'room_id is required'}
        _log.info("预览切换: room_id=%s, enabled=%s, mode=%s", room_id, enabled, mode)

        # MSE 模式：在 handler 层管理 MseStreamer（需要 WebSocket 推送）
        if mode == 'mse':
            return await _handle_mse_preview(server, manager, room_id, enabled, data)

        def _preview():
            if enabled:
                return manager.start_preview(room_id, mode=mode)
            manager.stop_preview(room_id)
            return True

        success = await asyncio.get_running_loop().run_in_executor(_bridge_executor, lambda: bridge.call(_preview))
        _broadcast_rooms()
        return {'success': bool(success)}

    # MSE preview handler
    async def _handle_mse_preview(srv, mgr, room_id: str, enabled: bool, data: dict | None = None, *, force_restart: bool = False) -> dict[str, Any]:
        """Handle MSE (Media Source Extensions) preview mode.

        Creates an MseStreamer that transcodes the live stream to fragmented MP4
        and pushes segments via WebSocket for browser-native <video> playback.
        """
        if enabled:
            # Check if already streaming / starting
            existing = _preview_stream_registry().get(room_id)
            if existing is not None and existing.is_running:
                    # Streamer 仍在运行：设置 preview_enabled=True 并重发 init 段
                    _log.info("预览已在运行: room_id=%s, 重发 init 段", room_id)
                    def _set_preview_on():
                        room = mgr.get_room(room_id)
                        if room is not None:
                            room.preview_enabled = True
                        return True
                    try:
                        await asyncio.get_running_loop().run_in_executor(
                            _bridge_executor, lambda: bridge.call(_set_preview_on)
                        )
                    except Exception as exc:
                        _log.error("设置 preview_enabled 失败: room_id=%s, error=%s", room_id, exc)
                    existing.replay_init()
                    _broadcast_rooms()
                    return {'success': True, 'room_id': room_id, 'note': 'already streaming, init replayed'}
            try:
                shared_enabled = bool(getattr(load_config(), 'shared_ingest_enabled', False))
            except Exception as exc:
                _log.debug("shared ingest config check failed: %s", exc)
                shared_enabled = False
            if shared_enabled:
                shared_ingest = _shared_ingests.get(room_id)
                if (
                    shared_ingest is not None
                    and getattr(shared_ingest, 'recording_active', False)
                    and not getattr(shared_ingest, 'is_stopped', True)
                ):
                    loop = asyncio.get_running_loop()
                    shared_handle = None
                    try:
                        if force_restart:
                            # 停止旧预览 FFmpeg 以应用新画质参数
                            def _stop_preview_sink():
                                try:
                                    shared_ingest.stop_preview_sink()
                                except Exception as exc:
                                    _log.debug("stop_preview_sink 失败: %s", exc)
                            await asyncio.get_running_loop().run_in_executor(_bridge_executor, _stop_preview_sink)

                        _configure_shared_preview_quality(shared_ingest, data)
                        shared_handle = _preview_stream_registry().attach_shared(
                            room_id,
                            shared_ingest,
                            on_init_segment=lambda seg: asyncio.run_coroutine_threadsafe(
                                srv.broadcast('mse_init', {
                                    'room_id': room_id,
                                    'data': base64.b64encode(seg).decode('ascii'),
                                }),
                                loop,
                            ),
                            on_media_segment=lambda seg: asyncio.run_coroutine_threadsafe(
                                srv.broadcast('mse_segment', {
                                    'room_id': room_id,
                                    'data': base64.b64encode(seg).decode('ascii'),
                                }),
                                loop,
                            ),
                            on_error=lambda err: (
                                asyncio.run_coroutine_threadsafe(
                                    srv.broadcast('mse_error', {
                                        'room_id': room_id,
                                        'error': err,
                                    }),
                                    loop,
                                ),
                                _stop_idle_shared_ingest(room_id, reason="shared preview error"),
                            ),
                        )

                        if force_restart:
                            # 启动新预览 FFmpeg（subscriber 已存在，新参数已配置）
                            def _start_preview_sink():
                                preview_params = _compute_preview_quality_params(data)
                                valid_keys = {'width', 'height', 'use_nvenc', 'video_bitrate', 'crf_value', 'fps'}
                                filtered = {k: v for k, v in preview_params.items() if k in valid_keys}
                                shared_ingest.configure_preview(**filtered)
                                return shared_ingest.start_preview(**filtered)
                            try:
                                result = await asyncio.get_running_loop().run_in_executor(_bridge_executor, _start_preview_sink)
                                if not getattr(result, 'ok', False):
                                    _log.warning("预览重启失败: room_id=%s, error=%s", room_id, getattr(result, 'error', ''))
                            except Exception as exc:
                                _log.warning("预览重启异常: room_id=%s, error=%s", room_id, exc)

                        shared_handle.replay_init()

                        def _set_shared_preview_on():
                            room = mgr.get_room(room_id)
                            if room is not None:
                                room.preview_enabled = True
                            return True

                        await asyncio.get_running_loop().run_in_executor(
                            _bridge_executor, lambda: bridge.call(_set_shared_preview_on)
                        )
                        _mse_reconnect_state.pop(room_id, None)
                        _broadcast_rooms()
                        return {'success': True, 'room_id': room_id, 'note': 'shared ingest preview attached'}
                    except Exception as exc:
                        _log.warning(
                            "shared ingest preview attach failed: room_id=%s, error=%s",
                            room_id,
                            exc,
                        )
                        if shared_handle is not None:
                            try:
                                shared_handle.stop()
                            except Exception as stop_exc:
                                _log.debug("shared preview cleanup failed: %s", stop_exc)
                if shared_ingest is None or not getattr(shared_ingest, 'recording_active', False):
                    loop = asyncio.get_running_loop()
                    shared_handle = None
                    try:
                        if shared_ingest is None or getattr(shared_ingest, 'is_stopped', True):
                            refresh_ok = await asyncio.get_running_loop().run_in_executor(
                                _recording_executor, lambda: mgr.refresh_stream_url(room_id, force=False)
                            )
                            if not refresh_ok:
                                raise RuntimeError("stream url refresh failed")

                            def _read_shared_snapshot():
                                room = mgr.get_room(room_id)
                                if room is None:
                                    return None
                                return {
                                    'is_connected': room.is_connected,
                                    'stream_url': room.stream_info.stream_url if room.stream_info else '',
                                    'headers': (room.stream_info.headers if room.stream_info else None) or {},
                                    'quality_urls': (room.stream_info.quality_urls if room.stream_info else {}),
                                }

                            snapshot = await asyncio.get_running_loop().run_in_executor(
                                _bridge_executor, lambda: bridge.call(_read_shared_snapshot)
                            )
                            if snapshot is None or not snapshot['is_connected'] or not snapshot['stream_url']:
                                raise RuntimeError("room is not connected or has no stream url")

                            stream_url = snapshot['stream_url']
                            settings = load_settings()
                            preview_quality = (data or {}).get('preview_quality') or settings.get('preview_quality', '高清')
                            quality_urls = snapshot.get('quality_urls') or {}
                            if quality_urls:
                                selected_url, _selected_key = select_quality(
                                    {'qualityUrls': quality_urls, 'streamUrl': stream_url, 'selectedQuality': ''},
                                    preview_quality,
                                )
                                if selected_url:
                                    stream_url = selected_url
                            shared_ingest = _shared_ingests.get_or_create(
                                room_id,
                                url=stream_url,
                                headers=snapshot.get('headers') or {},
                            )

                        if getattr(shared_ingest, 'process_id', None) is None or getattr(shared_ingest, 'is_stopped', True):
                            preview_params = _compute_preview_quality_params(data)
                            # 过滤掉不接受的参数
                            valid_keys = {'width', 'height', 'use_nvenc', 'video_bitrate', 'crf_value', 'fps'}
                            filtered = {k: v for k, v in preview_params.items() if k in valid_keys}
                            shared_ingest.configure_preview(**filtered)
                            result = shared_ingest.start_preview(**filtered)
                            if not getattr(result, 'ok', False):
                                raise RuntimeError(getattr(result, 'error', '') or "shared preview start failed")

                        _configure_shared_preview_quality(shared_ingest, data)
                        shared_handle = _attach_shared_preview_handle(room_id, shared_ingest, loop)
                        shared_handle.replay_init()

                        def _set_shared_preview_on():
                            room = mgr.get_room(room_id)
                            if room is not None:
                                room.preview_enabled = True
                            return True

                        await asyncio.get_running_loop().run_in_executor(
                            _bridge_executor, lambda: bridge.call(_set_shared_preview_on)
                        )
                        _mse_reconnect_state.pop(room_id, None)
                        _broadcast_rooms()
                        return {'success': True, 'room_id': room_id, 'note': 'shared ingest preview-only started'}
                    except Exception as exc:
                        _log.warning(
                            "shared ingest preview-only failed: room_id=%s, error=%s",
                            room_id,
                            exc,
                        )
                        if shared_handle is not None:
                            try:
                                shared_handle.stop()
                            except Exception as stop_exc:
                                _log.debug("shared preview-only handle cleanup failed: %s", stop_exc)
                        if shared_ingest is not None and not getattr(shared_ingest, 'recording_active', False):
                            _stop_idle_shared_ingest(room_id, reason="shared preview-only failed")
                        return {'success': False, 'room_id': room_id, 'error': f'共享预览启动失败：{exc}'}
                if shared_enabled:
                    return {'success': False, 'room_id': room_id, 'error': '共享预览启动失败，请检查直播流状态'}
            with _mse_starting_lock:
                    if room_id in _mse_starting:
                        return {'success': False, 'room_id': room_id, 'error': 'MSE 正在启动中，请稍候'}
                    _mse_starting.add(room_id)

            try:
                # 先在后台线程刷新流 URL，避免阻塞 Qt 主线程（B站等平台耗时 10+ 秒）
                # force=False：连接后 120s 内复用房间流缓存，显著加快预览启动
                refresh_ok = await asyncio.get_running_loop().run_in_executor(
                    _recording_executor, lambda: mgr.refresh_stream_url(room_id, force=False)
                )
                if not refresh_ok:
                    # 仅在确实没有可用流时才断开；保留缓存，避免误报「房间未连接」
                    def _mark_disconnected_if_no_stream():
                        room = mgr.get_room(room_id)
                        if room is None:
                            return
                        has_url = bool(
                            (room.stream_info and room.stream_info.stream_url)
                            or room.stream_url_cached
                            or (room.controller and getattr(room.controller, "stream_url", ""))
                        )
                        if has_url:
                            _log.warning(
                                "preview refresh failed but keep connection for %s (cached stream present)",
                                room_id,
                            )
                            return
                        room.is_connected = False
                        room.stream_info = None
                    await asyncio.get_running_loop().run_in_executor(
                        _bridge_executor, lambda: bridge.call(_mark_disconnected_if_no_stream)
                    )

                # 在 Qt 主线程读取刷新后的房间状态
                def _read_snapshot():
                    room = mgr.get_room(room_id)
                    if room is None:
                        return None
                    stream_url = ''
                    if room.stream_info and room.stream_info.stream_url:
                        stream_url = room.stream_info.stream_url
                    elif room.stream_url_cached:
                        stream_url = room.stream_url_cached
                    return {
                        'is_connected': room.is_connected or bool(stream_url),
                        'stream_url': stream_url,
                        'platform': room.platform,
                        'headers': (room.stream_info.headers if room.stream_info else None) or {},
                        'quality_urls': (room.stream_info.quality_urls if room.stream_info else {}),
                    }

                snapshot = await asyncio.get_running_loop().run_in_executor(
                    _bridge_executor, lambda: bridge.call(_read_snapshot)
                )
                if snapshot is None:
                    return {'success': False, 'room_id': room_id, 'error': '房间不存在'}
                if not snapshot['is_connected'] or not snapshot['stream_url']:
                    return {'success': False, 'room_id': room_id, 'error': '房间未连接或无流信息（直播可能已结束）'}

                stream_url = snapshot['stream_url']

                # 读取预览画质预设（优先消息传入的 preview_quality，回退到全局设置）
                settings = load_settings()
                preview_quality = data.get('preview_quality') or settings.get('preview_quality', '高清')
                preset = _get_preview_quality_preset(preview_quality)
                width = preset['width']
                height = preset['height']

                # 根据用户选择的预览画质，从 quality_urls 中挑选对应画质的流地址
                quality_urls = snapshot.get('quality_urls') or {}
                if quality_urls:
                    selected_url, selected_key = select_quality(
                        {'qualityUrls': quality_urls, 'streamUrl': stream_url, 'selectedQuality': ''},
                        preview_quality,
                    )
                    if selected_url:
                        stream_url = selected_url
                        _log.info("预览画质选择: preset=%s, quality_key=%s, url=%s", preview_quality, selected_key, stream_url[:80])
                # B站等平台首次刷新 URL 耗时较长，延长 FFmpeg 启动探测超时
                platform = snapshot.get('platform', '')
                probe_timeout = 8.0 if platform in ('bilibili', 'bilibili_bangumi') else 3.0

                # 动态降分辨率：活跃 MSE ≥6 路时强制限制分辨率 ≤ 854x480
                # S7: ≥8 路时进一步降级到 640x360，防止内存/CPU 过载
                active_mse_count = _preview_stream_registry().active_count()
                if active_mse_count >= 8:
                    max_w, max_h = 640, 360
                    if width == 0 or height == 0:
                        width, height = max_w, max_h
                    elif width > max_w or height > max_h:
                        ratio = min(max_w / width, max_h / height)
                        width = int(width * ratio)
                        height = int(height * ratio)
                elif active_mse_count >= 6:
                    max_w, max_h = 854, 480
                    if width == 0 or height == 0:
                        width, height = max_w, max_h
                    elif width > max_w or height > max_h:
                        ratio = min(max_w / width, max_h / height)
                        width = int(width * ratio)
                        height = int(height * ratio)

                # 提取流 headers（B站/虎牙/斗鱼 CDN 强制检查 Referer）
                preview_headers = snapshot.get('headers') or {}
                # 确定编码码率/CRF
                from lsc.core.services.mse_streamer import _check_nvenc
                use_nvenc = _check_nvenc()
                video_bitrate = preset['nvenc_bitrate'] if use_nvenc else preset['x264_bitrate']
                crf_value = preset['x264_crf']

                loop = asyncio.get_running_loop()

                async def _on_mse_error(room_id: str, err: str, loop):
                    """MSE 流错误处理：尝试自动重连，超限后清理预览状态。

                    使用 while 循环替代递归，避免异步递归控制流难以推理的问题。
                    on_error 回调仍可调用本函数，但首次进入即开始循环，不形成递归链。
                    """
                    _log.info("MSE error for room %s: %s", room_id, err)

                    # 1. 从 _mse_streamers 移除并停止已失效的 streamer（仅首次执行）
                    old_streamer = _preview_stream_registry().pop(room_id)
                    if old_streamer is not None:
                        try:
                            old_streamer.stop()
                        except Exception as exc:
                            _log.debug("停止旧 MSE streamer 失败: %s", exc)
                        _stop_idle_shared_ingest(room_id, reason="mse error cleanup")

                    current_error = err

                    while True:
                        # 2. 检查是否仍需预览（用户可能已手动关闭）
                        def _check_preview():
                            room = mgr.get_room(room_id)
                            if room is None:
                                return False
                            return room.preview_enabled

                        try:
                            still_previewing = await loop.run_in_executor(
                                _bridge_executor, lambda: bridge.call(_check_preview)
                            )
                        except Exception:
                            still_previewing = False

                        if not still_previewing:
                            _mse_reconnect_state.pop(room_id, None)
                            return

                        # 3. 检查重连次数
                        state = _mse_reconnect_state.get(room_id, {'attempts': 0})
                        if state['attempts'] >= _MSE_MAX_RECONNECT:
                            _log.warning(
                                "MSE reconnect exhausted for room %s (%d attempts)",
                                room_id, state['attempts'],
                            )
                            _mse_reconnect_state.pop(room_id, None)
                            await srv.broadcast('mse_error', {
                                'room_id': room_id,
                                'error': '预览重连失败，已达到最大重试次数，请手动重新开启预览',
                            })

                            def _clear_preview():
                                room = mgr.get_room(room_id)
                                if room is not None:
                                    room.preview_enabled = False

                            try:
                                await loop.run_in_executor(
                                    _bridge_executor, lambda: bridge.call(_clear_preview)
                                )
                            except Exception as exc:
                                _log.error("MSE error cleanup failed: %s", exc)
                            bridge.queue_broadcast({
                                'type': 'rooms_updated',
                                'data': {'rooms': _rooms_list(mgr)},
                            })
                            return

                        # 4. 计算指数退避延迟
                        delay = min(
                            _MSE_RECONNECT_BASE_DELAY * (2 ** state['attempts']),
                            _MSE_RECONNECT_MAX_DELAY,
                        )
                        state['attempts'] += 1
                        _mse_reconnect_state[room_id] = state

                        _log.info(
                            "MSE reconnect attempt %d/%d for room %s (delay=%.1fs, error=%s)",
                            state['attempts'], _MSE_MAX_RECONNECT, room_id, delay, current_error,
                        )

                        # 5. 广播重连中
                        await srv.broadcast('mse_reconnecting', {
                            'room_id': room_id,
                            'attempt': state['attempts'],
                            'max_attempts': _MSE_MAX_RECONNECT,
                            'delay': delay,
                        })

                        # 6. 等待退避延迟
                        await asyncio.sleep(delay)

                        # 7. 再次检查是否仍在预览
                        try:
                            still_previewing = await loop.run_in_executor(
                                _bridge_executor, lambda: bridge.call(_check_preview)
                            )
                        except Exception:
                            still_previewing = False
                        if not still_previewing:
                            _mse_reconnect_state.pop(room_id, None)
                            return

                        # 8. 刷新流 URL（优先缓存，失败回退强制刷新）
                        try:
                            refresh_ok = await loop.run_in_executor(
                                _recording_executor, lambda: mgr.refresh_stream_url(room_id, force=False)
                            )
                            if not refresh_ok:
                                refresh_ok = await loop.run_in_executor(
                                    _recording_executor, lambda: mgr.refresh_stream_url(room_id, force=True)
                                )
                        except Exception as exc:
                            _log.error("MSE reconnect URL refresh failed: %s", exc)
                            refresh_ok = False

                        if not refresh_ok:
                            current_error = '流地址刷新失败'
                            continue  # 进入下一次循环重试

                        # 9. 读取刷新后的房间状态
                        def _read_snapshot():
                            room = mgr.get_room(room_id)
                            if room is None:
                                return None
                            return {
                                'is_connected': room.is_connected,
                                'stream_url': room.stream_info.stream_url if room.stream_info else '',
                                'platform': room.platform,
                                'headers': (room.stream_info.headers if room.stream_info else None) or {},
                                'quality_urls': (room.stream_info.quality_urls if room.stream_info else {}),
                            }

                        try:
                            snapshot = await loop.run_in_executor(
                                _bridge_executor, lambda: bridge.call(_read_snapshot)
                            )
                        except Exception:
                            snapshot = None

                        if snapshot is None or not snapshot['is_connected'] or not snapshot['stream_url']:
                            current_error = '房间未连接或无流信息'
                            continue  # 进入下一次循环重试

                        # 10. 获取预览画质预设（与初始启动逻辑一致）
                        settings = load_settings()
                        preview_quality = settings.get('preview_quality', '高清')
                        preset = _get_preview_quality_preset(preview_quality)
                        r_width = preset['width']
                        r_height = preset['height']

                        active_mse_count = _preview_stream_registry().active_count()
                        if active_mse_count >= 8:
                            max_w, max_h = 640, 360
                            if r_width == 0 or r_height == 0:
                                r_width, r_height = max_w, max_h
                            elif r_width > max_w or r_height > max_h:
                                ratio = min(max_w / r_width, max_h / r_height)
                                r_width = int(r_width * ratio)
                                r_height = int(r_height * ratio)
                        elif active_mse_count >= 6:
                            max_w, max_h = 854, 480
                            if r_width == 0 or r_height == 0:
                                r_width, r_height = max_w, max_h
                            elif r_width > max_w or r_height > max_h:
                                ratio = min(max_w / r_width, max_h / r_height)
                                r_width = int(r_width * ratio)
                                r_height = int(r_height * ratio)

                        r_headers = snapshot.get('headers') or {}
                        r_quality_urls = snapshot.get('quality_urls') or {}
                        r_stream_url = snapshot['stream_url']
                        if r_quality_urls:
                            selected_url, _ = select_quality(
                                {'qualityUrls': r_quality_urls, 'streamUrl': r_stream_url, 'selectedQuality': ''},
                                preview_quality,
                            )
                            if selected_url:
                                r_stream_url = selected_url
                        from lsc.core.services.mse_streamer import _check_nvenc
                        use_nvenc = _check_nvenc()
                        r_bitrate = preset['nvenc_bitrate'] if use_nvenc else preset['x264_bitrate']
                        r_crf = preset['x264_crf']
                        r_probe = 8.0 if snapshot.get('platform', '') in ('bilibili', 'bilibili_bangumi') else 3.0

                        # 11. 创建并启动新的 MseStreamer
                        def _restart():
                            from lsc.core.services.mse_streamer import MseStreamer
                            try:
                                streamer = MseStreamer(
                                    url=r_stream_url,
                                    width=r_width,
                                    height=r_height,
                                    headers=r_headers or None,
                                    video_bitrate=r_bitrate,
                                    crf_value=r_crf,
                                    on_init_segment=lambda seg, _room_id=room_id: asyncio.run_coroutine_threadsafe(
                                        srv.broadcast('mse_init', {
                                            'room_id': _room_id,
                                            'data': base64.b64encode(seg).decode('ascii'),
                                        }),
                                        loop,
                                    ),
                                    on_media_segment=lambda seg, _room_id=room_id: asyncio.run_coroutine_threadsafe(
                                        srv.broadcast('mse_segment', {
                                            'room_id': _room_id,
                                            'data': base64.b64encode(seg).decode('ascii'),
                                        }),
                                        loop,
                                    ),
                                    on_error=lambda e, _room_id=room_id: asyncio.run_coroutine_threadsafe(
                                        _on_mse_error(_room_id, e, loop), loop
                                    ),
                                )
                                ok = streamer.start(startup_probe_timeout=r_probe)
                                if ok:
                                    _preview_stream_registry().set_legacy(room_id, streamer)
                                    return True, ''
                                stderr_tail = ''
                                try:
                                    stderr_tail = (streamer._last_stderr or '').strip()[:300]
                                except AttributeError:
                                    pass
                                try:
                                    streamer.stop()
                                except Exception as exc:
                                    _log.debug("停止启动失败的 streamer 失败: %s", exc)
                                return False, stderr_tail
                            except Exception as exc:
                                _log.error("MSE reconnect start failed: %s", exc)
                                return False, str(exc)

                        try:
                            success, error_detail = await loop.run_in_executor(
                                _recording_executor, _restart
                            )
                        except Exception as exc:
                            success, error_detail = False, str(exc)

                        if success:
                            _mse_reconnect_state.pop(room_id, None)
                            _log.info("MSE reconnect succeeded for room %s", room_id)
                            await srv.broadcast('mse_reconnected', {'room_id': room_id})
                            _broadcast_rooms()
                            return
                        else:
                            _log.warning(
                                "MSE reconnect failed for room %s: %s",
                                room_id, error_detail,
                            )
                            current_error = f'重连失败：{error_detail}'
                            continue  # 进入下一次循环重试

                def _start():
                    """启动 MseStreamer。返回 (ok, error_detail)。

                    error_detail 在 ok=False 时携带具体失败原因（FFmpeg stderr
                    尾部或异常消息），供前端精确显示，避免笼统的"请检查 FFmpeg"
                    误导用户。
                    """
                    try:
                        from lsc.core.services.mse_streamer import MseStreamer

                        streamer = MseStreamer(
                            url=stream_url,
                            width=width,
                            height=height,
                            headers=preview_headers or None,
                            video_bitrate=video_bitrate,
                            crf_value=crf_value,
                            on_init_segment=lambda seg: asyncio.run_coroutine_threadsafe(
                                srv.broadcast('mse_init', {
                                    'room_id': room_id,
                                    'data': base64.b64encode(seg).decode('ascii'),
                                }),
                                loop,
                            ),
                            on_media_segment=lambda seg: asyncio.run_coroutine_threadsafe(
                                srv.broadcast('mse_segment', {
                                    'room_id': room_id,
                                    'data': base64.b64encode(seg).decode('ascii'),
                                }),
                                loop,
                            ),
                            on_error=lambda err: asyncio.run_coroutine_threadsafe(
                                _on_mse_error(room_id, err, loop), loop
                            ),
                        )
                        ok = streamer.start(startup_probe_timeout=probe_timeout)
                        if ok:
                            _preview_stream_registry().set_legacy(room_id, streamer)
                            return True, ''
                        # 启动失败：从 streamer 提取 stderr 详情
                        stderr_tail = ''
                        try:
                            stderr_tail = (streamer._last_stderr or '').strip()[:300]
                        except AttributeError:
                            pass
                        # 清理已启动的 FFmpeg 进程和管道，防止资源泄漏
                        try:
                            streamer.stop()
                        except Exception as exc:
                            _log.debug("停止启动失败的 streamer 失败 (start): %s", exc)
                        return False, stderr_tail
                    except FileNotFoundError:
                        # FFmpeg 可执行文件未找到
                        return False, 'FFmpeg 未找到，请在设置中配置 FFmpeg 路径或将其加入 PATH'
                    except Exception as exc:
                        _log.error("MSE streamer start failed: %s", exc)
                        return False, str(exc)

                success, error_detail = await asyncio.get_running_loop().run_in_executor(_recording_executor, _start)

                if not success:
                    # MSE 启动失败：不设置 preview_enabled，避免前端渲染 VideoPreview 导致反复重试。
                    # 根据 error_detail 区分失败原因：
                    # - FFmpeg 未找到 → 提示安装/配置
                    # - 有 stderr → 直播流连接失败（地址过期/主播下播/CDN 拒绝等）
                    # - 无 stderr → 未知原因
                    if not error_detail:
                        error_msg = 'MSE 流启动失败，请检查直播流是否在线'
                    elif 'FFmpeg 未找到' in error_detail:
                        error_msg = error_detail
                    else:
                        error_msg = f'直播流连接失败：{error_detail}'
                    return {'success': False, 'room_id': room_id, 'error': error_msg}

                # 启动成功：通过 bridge.call 在 Qt 主线程更新 preview_enabled
                # 若此处抛异常，streamer 已在 _mse_streamers 中但前端不知需要 stop，
                # 需主动清理避免进程泄漏
                def _set_preview_enabled():
                    room = mgr.get_room(room_id)
                    if room is not None:
                        room.preview_enabled = True
                    return True

                try:
                    await asyncio.get_running_loop().run_in_executor(
                        _bridge_executor, lambda: bridge.call(_set_preview_enabled)
                    )
                except Exception as exc:
                    # bridge.call 失败：清理已注册的 streamer，避免进程泄漏
                    _log.error("MSE preview_enabled 设置失败，清理 streamer: %s", exc)
                    leak_streamer = _preview_stream_registry().pop(room_id)
                    if leak_streamer is not None:
                        try:
                            leak_streamer.stop()
                        except Exception as exc:
                            _log.debug("停止泄漏 streamer 失败 (cleanup): %s", exc)
                        _stop_idle_shared_ingest(room_id, reason="preview state sync failed")
                    return {'success': False, 'room_id': room_id, 'error': f'预览状态同步失败：{exc}'}

                _mse_reconnect_state.pop(room_id, None)
                _broadcast_rooms()
                return {'success': True, 'note': 'mse streaming started'}
            finally:
                with _mse_starting_lock:
                    _mse_starting.discard(room_id)

        else:
            # Stop MSE streaming
            streamer = _preview_stream_registry().pop(room_id)
            if streamer is not None:
                def _stop():
                    streamer.stop()
                await asyncio.get_running_loop().run_in_executor(_bridge_executor, _stop)
                _stop_idle_shared_ingest(room_id, reason="preview stopped")

            def _disable():
                room = mgr.get_room(room_id)
                if room:
                    room.preview_enabled = False
                return True

            await asyncio.get_running_loop().run_in_executor(_bridge_executor, lambda: bridge.call(_disable))
            _mse_reconnect_state.pop(room_id, None)
            _broadcast_rooms()
            _log.info("MSE 预览已停止: room_id=%s", room_id)
            return {'success': True, 'note': 'mse streaming stopped'}

    @server.on('request_mse_init')
    async def handle_request_mse_init(data):
        """前端挂载 VideoPreview 后主动请求补发 init 段。

        消除 mse_init 早于 rooms_updated 到达前端导致的竞态：前端收到
        rooms_updated 后才挂载 VideoPreview 并注册 player，此时可能已
        错过后端首次广播的 mse_init。本 handler 从缓存的 init 段重发。
        """
        room_id = data.get('room_id')
        if not room_id:
            return {'success': False, 'error': 'room_id is required'}
        _log.debug("请求 MSE init 重发: room_id=%s", room_id)
        streamer = _preview_stream_registry().get(room_id)
        if streamer is None:
            _log.debug("request_mse_init: room_id=%s 流未启动", room_id)
            return {'success': False, 'error': 'MSE 流未启动'}
        ok = streamer.replay_init()
        _log.debug("request_mse_init: room_id=%s, ok=%s", room_id, ok)
        return {'success': ok, 'room_id': room_id, 'note': 'init replayed' if ok else 'init not ready yet'}

    def _broadcast_analysis_progress(room_id: str, stage: str, progress: float, detail: str) -> None:
        """广播 AI 分析进度到前端。

        使用 bridge.queue_broadcast 线程安全地投递消息，
        与 _queue_rooms_update / _broadcast_system_stats 采用相同的广播模式。
        广播失败只记日志，不中断分析流程。
        """
        try:
            bridge.queue_broadcast({
                'type': 'analysis_progress',
                'data': {
                    'room_id': room_id,
                    'stage': stage,
                    'progress': progress,
                    'detail': detail,
                },
            })
        except Exception as exc:
            _log.warning("广播分析进度失败: %s", exc)

    @server.on('start_analysis')
    async def handle_start_analysis(data):
        """启动场景分析/AI高光分析。

        参数:
            mode: 'scene'（场景检测，默认）| 'ai'（仅AI分析）| 'combined'（AI+场景融合）
            whisper_model: 'auto'/'tiny'/'base'/'small'/'medium'，仅 AI/combined 模式
            weights: 融合权重 {'audio': float, 'visual': float, 'scene': float}，仅 AI/combined 模式
        """
        room_id = data.get('room_id')
        threshold = _safe_float(data.get('threshold', 0.3), 0.3)
        mode = data.get('mode', 'scene')  # 'scene' | 'ai' | 'combined'
        whisper_model = data.get('whisper_model', 'auto')  # 'auto'/'tiny'/'base'/'small'/'medium'
        weights = data.get('weights', {})  # {'audio': 0.45, 'visual': 0.35, 'scene': 0.20}
        absolute_threshold = _safe_float(data.get('absolute_threshold', 0.15), 0.15)
        game = data.get('game', 'valorant')  # 'valorant' | 'generic'

        if not room_id:
            return {'error': 'room_id is required'}
        _log.info("启动分析: room_id=%s, mode=%s, threshold=%.2f", room_id, mode, threshold)

        def _do_analysis():
            room = manager.get_room(room_id)
            if room is None:
                return {'success': False, 'error': '房间不存在'}
            if not room.record_output_path or not os.path.isfile(room.record_output_path):
                return {'success': False, 'error': '录制文件不存在'}

            video_path = room.record_output_path
            _analysis_jobs[room_id] = {"progress": 0.0, "highlights": [], "mode": mode, "cancelled": False}

            # 进度回调与取消检查（scene 和 AI 模式共用，P0-4）
            def _progress_cb(stage, progress, detail):
                if _analysis_jobs.get(room_id, {}).get('cancelled'):
                    return
                _analysis_jobs[room_id]['progress'] = progress / 100.0
                _analysis_jobs[room_id]['stage'] = stage
                _broadcast_analysis_progress(room_id, stage, progress, detail)

            def _cancel_check():
                return _analysis_jobs.get(room_id, {}).get('cancelled', False)

            highlights = _analyze_scene_or_rounds(
                video_path, game=game, threshold=threshold,
                progress_callback=_progress_cb, cancel_check=_cancel_check,
            )
            if highlights is None:
                return {'success': False, 'error': '分析已取消', 'cancelled': True}

            # P0-5: 补齐 scene 模式字段，与 AI 模式格式统一（前端 Modal 渲染需要）
            for _h in highlights:
                _h.setdefault("reason", "场景切换频繁")
                _h.setdefault("speech_score", 0.0)
                _h.setdefault("visual_score", 0.0)
                _h.setdefault("transcript", "")
            _analysis_jobs[room_id] = {
                "progress": 1.0,
                "highlights": highlights,
                "mode": mode,
                "completed_at": time.time(),
            }
            # P0-3: 落盘到录制文件同目录 {basename}.analysis.json（重启不丢失）
            save_analysis_results(video_path, room_id, mode, highlights)
            _log.info("分析完成: room_id=%s, mode=%s, highlights=%d", room_id, mode, len(highlights))
            return {'success': True, 'mode': mode, 'highlights': highlights}

        executor = _bridge_executor
        _timeout = 120
        try:
            result = await asyncio.wait_for(
                asyncio.get_running_loop().run_in_executor(executor, _do_analysis),
                timeout=_timeout,
            )
        except asyncio.TimeoutError:
            _analysis_jobs.setdefault(room_id, {})['cancelled'] = True
            _log.error("分析超时（%ss），room_id=%s, mode=%s", _timeout, room_id, mode)
            return {
                'success': False,
                'error': f'分析超时（{_timeout}s），可能模型下载卡住或视频过长。请检查网络后重试。',
            }
        return result

    @server.on('start_analysis_export')
    async def handle_start_analysis_export(data):
        """高光分析并自动导出（单房间 / 多房间同步）。

        参数:
            main_room_id: str         — 做高光分析的主直播间
            target_room_ids: [str]    — 要导出的所有房间（含 main；单房间时=[main]）
            mode: 'scene'|'ai'|'combined'（默认 scene）
            whisper_model / weights / threshold — 仅 AI/combined
            preset_id: str            — 导出预设
            job_prefix: str           — 前端关联进度用

        流程: 校验对齐组 → 分析主房间 → 高光按 content_offset 映射到每个目标房间
              → 批量导出。复用 export_progress/clip_completed/clip_failed 事件。
        """
        main_room_id = data.get('main_room_id')
        target_room_ids = data.get('target_room_ids') or ([main_room_id] if main_room_id else [])
        mode = data.get('mode', 'scene')
        whisper_model = data.get('whisper_model', 'auto')
        weights = data.get('weights', {})
        threshold = _safe_float(data.get('threshold', 0.3), 0.3)
        game = data.get('game', 'valorant')  # 'valorant' | 'generic'
        preset_id = data.get('preset_id', '')
        job_prefix = data.get('job_prefix', f'hlexport-{int(time.time() * 1000)}')

        if not main_room_id:
            return {'error': 'main_room_id is required'}
        if not target_room_ids:
            target_room_ids = [main_room_id]

        _log.info("分析并导出: main=%s, targets=%s, mode=%s", main_room_id, target_room_ids, mode)
        loop = asyncio.get_running_loop()

        def _do_analysis_and_export():
            ok, error, main_room, target_rooms = _validate_synced_analysis_targets(
                manager, main_room_id, target_room_ids, wait_for_file=True,
            )
            if not ok:
                return {'success': False, 'error': error}

            # 3. 高光分析主房间（复用 scene/AI 分析逻辑）
            video_path = main_room.record_output_path
            _analysis_jobs[main_room_id] = {"progress": 0.0, "highlights": [], "mode": mode, "cancelled": False}

            def _progress_cb(stage, progress, detail):
                if _analysis_jobs.get(main_room_id, {}).get('cancelled'):
                    return
                _analysis_jobs[main_room_id]['progress'] = progress / 100.0
                _analysis_jobs[main_room_id]['stage'] = stage
                _broadcast_analysis_progress(main_room_id, stage, progress, detail)

            def _cancel_check():
                return _analysis_jobs.get(main_room_id, {}).get('cancelled', False)

            highlights = _analyze_scene_or_rounds(
                video_path, game=game, threshold=threshold,
                progress_callback=_progress_cb, cancel_check=_cancel_check,
            )
            if highlights is None:
                return {'success': False, 'error': '分析已取消', 'cancelled': True}

            # 补齐字段
            for _h in highlights:
                _h.setdefault("reason", "场景切换频繁")
                _h.setdefault("speech_score", 0.0)
                _h.setdefault("visual_score", 0.0)
                _h.setdefault("transcript", "")

            # 落盘分析结果（P0-3）
            save_analysis_results(
                video_path, main_room_id, mode, highlights,
                analysis_time_sec=analysis_time,
                whisper_model=whisper_model if mode != 'scene' else '',
                weights=weights if weights else None,
            )
            _analysis_jobs[main_room_id] = {
                "progress": 1.0, "highlights": highlights, "mode": mode, "completed_at": time.time(),
            }

            if not highlights:
                return {'success': False, 'error': '未检测到高光片段', 'highlights': []}

            # 4. 批量导出（切到 Qt 主线程创建 QThread）
            # 注意：profile 构建在 queue_export 内部完成，此处无需预构建

            async def _submit_exports():
                """提交所有导出任务到全局导出队列."""
                jobs: list[str] = []
                for i, hl in enumerate(highlights):
                    t1 = float(hl.get('start', 0))
                    t2 = float(hl.get('end', 0))
                    if t1 >= t2:
                        continue
                    for r in target_rooms:
                        rid = r.room_id
                        mapped = _map_highlight_to_room(hl, main_room, r)
                        mapped_start = mapped["start"]
                        mapped_end = mapped["end"]
                        if mapped_start >= mapped_end:
                            continue
                        room_name = r.streamer_name or rid
                        title = f"{room_name}_高光{i + 1}_s{int(t1)}"
                        jid = f"{job_prefix}-{i}-{rid}"

                        result = await queue_export(
                            rid, mapped_start, mapped_end,
                            label=title, preset_id=preset_id,
                            source='ai_highlight', job_id=jid,
                        )
                        if result.get('success'):
                            jobs.append(result['job_id'])
                        else:
                            _log.warning("导出入队失败: room=%s, error=%s", rid, result.get('error'))
                return jobs

            # 在 server 事件循环上安全执行（线程不在 asyncio loop 内）
            submitted = asyncio.run_coroutine_threadsafe(_submit_exports(), loop).result(timeout=60)
            _log.info("分析并导出已提交: main=%s, 高光=%d, 房间=%d, 任务=%d",
                      main_room_id, len(highlights), len(target_rooms), len(submitted))
            if not submitted:
                return {
                    'success': False,
                    'error': '未能提交任何导出任务',
                    'highlights': highlights,
                }
            return {
                'success': True,
                'highlights': highlights,
                'submitted_count': len(submitted),
                'job_ids': submitted,
            }

        executor = _bridge_executor
        _timeout = 120
        try:
            result = await asyncio.wait_for(
                loop.run_in_executor(executor, _do_analysis_and_export),
                timeout=_timeout,
            )
        except asyncio.TimeoutError:
            _analysis_jobs.setdefault(main_room_id, {})['cancelled'] = True
            _log.error("分析导出超时（%ss），main_room=%s, mode=%s", _timeout, main_room_id, mode)
            return {
                'success': False,
                'error': f'分析超时（{_timeout}s），可能模型下载卡住或视频过长。请检查网络后重试。',
            }
        return result

    @server.on('cancel_analysis')
    async def handle_cancel_analysis(data):
        """取消正在进行的 AI 分析。"""
        room_id = data.get('room_id')
        if not room_id:
            return {'error': 'room_id is required'}
        if room_id in _analysis_jobs:
            _analysis_jobs[room_id]['cancelled'] = True
            _log.info("取消分析: room_id=%s", room_id)
            return {'success': True, 'room_id': room_id}
        return {'success': False, 'error': '没有正在进行的分析任务'}

    @server.on('get_analysis_results')
    async def handle_get_analysis_results(data):
        """获取场景分析结果（自动清理 5 分钟前的过期任务）。"""
        room_id = data.get('room_id')
        if not room_id:
            return {'error': 'room_id is required'}
        _log.debug("获取分析结果: room_id=%s", room_id)
        # 清理过期的已完成分析任务，防止 _analysis_jobs 字典无限增长
        now = time.time()
        stale_keys = [
            rid for rid, job in _analysis_jobs.items()
            if job.get('completed_at') and now - job['completed_at'] > _ANALYSIS_JOB_TTL
        ]
        for rid in stale_keys:
            _analysis_jobs.pop(rid, None)
        job = _analysis_jobs.get(room_id)
        if job is None:
            # P0-3: 内存未命中（重启/TTL 过期），回退读录制文件同目录的分析结果 JSON
            room = manager.get_room(room_id)
            video_path = getattr(room, 'record_output_path', '') if room else ''
            if video_path and os.path.isfile(video_path):
                stored = load_analysis_results(video_path)
                if stored and not is_analysis_stale(video_path, stored):
                    return {
                        'progress': 1.0,
                        'highlights': stored.get('highlights', []),
                        'mode': stored.get('mode', 'scene'),
                        'stage': '',
                        'done': True,
                        'persisted': True,
                    }
            return {'progress': 0, 'highlights': [], 'done': False}
        return {
            'progress': job.get('progress', 0),
            'highlights': job.get('highlights', []),
            'mode': job.get('mode', 'scene'),
            'stage': job.get('stage', ''),
            'done': job.get('progress', 0) >= 1.0,
        }

    def _merge_highlights(existing: list[dict[str, Any]], new_hl: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """合并历史高光与新增高光, 基于 start/end 去重 (IoU >= 0.6 视为重复, 保留分数高的)。

        用于 scene 模式增量分析的累积, 避免全量替换丢失历史高光。
        """
        from lsc.analyzer.pipeline import _deduplicate_highlights
        return _deduplicate_highlights(existing + new_hl, iou_threshold=0.6)

    _exported_clip_ids: set[str] = set()  # 已导出的切片 ID（防止重复导出同一回合，跨房间共享）

    async def _auto_export_highlights(main_room, target_rooms, highlights, job_prefix, preset_id='',
                                      defer_export: bool = True):
        """确认高光先入切片列表；默认延后导出，压力缓解后再真正 queue_export。

        defer_export=True（持续分析默认）:
          - 仅广播 clip_queued（export_deferred），不启动 FFmpeg
          - 任务写入 _deferred_export_jobs，由 _flush_deferred_exports 消费
        defer_export=False:
          - 立即 queue_export（手动/一次性同步分析路径）
        """
        if not highlights or not target_rooms:
            return []

        main_rec = float(getattr(main_room, 'recording_start_mono', 0.0) or 0.0)
        main_offset = float(getattr(main_room, 'content_offset', 0.0) or 0.0)

        submitted_jobs = set()

        for idx, hl in enumerate(highlights):
            source_start = float(hl.get('start', 0) or 0)
            source_end = float(hl.get('end', 0) or 0)
            if source_start >= source_end:
                continue
            if source_end - source_start < _VALORANT_MIN_EXPORT_DURATION_SEC:
                _log.info("跳过过短高光 %.1f-%.1f", source_start, source_end)
                continue

            round_idx = int(hl.get('round_index', idx + 1))
            round_key = _valorant_round_key(hl)

            for target_room in target_rooms:
                rid = getattr(target_room, 'room_id', '')
                if not rid:
                    continue

                target_rec = float(getattr(target_room, 'recording_start_mono', 0.0) or 0.0)
                target_offset = float(getattr(target_room, 'content_offset', 0.0) or 0.0)
                delta = (main_rec - target_rec) + (main_offset - target_offset)

                export_start = max(0.0, source_start + delta)
                export_end = max(0.0, source_end + delta)
                if export_start >= export_end:
                    continue
                if export_end - export_start < _VALORANT_MIN_EXPORT_DURATION_SEC:
                    continue

                room_name = getattr(target_room, 'streamer_name', '') or rid
                listed_key = f"{rid}:{round_key}"
                if listed_key in _exported_clip_ids:
                    continue
                label = f"{room_name}_回合{round_idx}_{int(export_start)}s"
                job_id = f"auto-{job_prefix}-{round_key}-{rid}"
                clip_id = _clip_id(rid, export_start, export_end)

                if defer_export:
                    _deferred_export_jobs.append({
                        'room_id': rid,
                        'start': export_start,
                        'end': export_end,
                        'label': label,
                        'preset_id': preset_id,
                        'job_id': job_id,
                        'round_key': round_key,
                        'clip_id': clip_id,
                        'room_name': room_name,
                    })
                    _exported_clip_ids.add(listed_key)
                    submitted_jobs.add((round_key, rid))
                    bridge.queue_broadcast({
                        'type': 'clip_queued',
                        'data': {
                            'clip_id': clip_id,
                            'job_id': job_id,
                            'room_id': rid,
                            'room_name': room_name,
                            'label': label,
                            'start': round(export_start, 1),
                            'end': round(export_end, 1),
                            'export_deferred': True,
                        },
                    })
                    _log.info("延后导出仅入列: room=%s, job_id=%s, %.1f-%.1f", rid, job_id, export_start, export_end)
                    continue

                result = await queue_export(
                    rid, export_start, export_end,
                    label=label, preset_id=preset_id,
                    source='ai_highlight', job_id=job_id,
                )
                if result.get('success'):
                    submitted_jobs.add((round_key, rid))
                    bridge.queue_broadcast({
                        'type': 'clip_queued',
                        'data': {
                            'clip_id': clip_id,
                            'job_id': job_id,
                            'room_id': rid,
                            'room_name': room_name,
                            'label': label,
                            'start': round(export_start, 1),
                            'end': round(export_end, 1),
                        },
                    })
                    _log.info("自动导出入队: room=%s, job_id=%s, %.1f-%.1f", rid, job_id, export_start, export_end)
                else:
                    _log.warning("自动导出入队失败: room=%s, error=%s", rid, result.get('error'))

            if idx < len(highlights) - 1:
                try:
                    await asyncio.sleep(0.5)
                except asyncio.CancelledError:
                    break

        return submitted_jobs

    async def _flush_deferred_exports(force: bool = False) -> int:
        """压力缓解或收尾时，把延后队列真正送进导出 worker。"""
        if not _deferred_export_jobs:
            return 0
        pressure = get_resource_pressure()
        if not force and (
            pressure.get('pause_analysis')
            or pressure.get('level') == 'critical'
        ):
            return 0
        jobs = list(_deferred_export_jobs)
        _deferred_export_jobs.clear()
        flushed = 0
        for job in jobs:
            result = await queue_export(
                job['room_id'], job['start'], job['end'],
                label=job['label'], preset_id=job.get('preset_id', ''),
                source='ai_highlight', job_id=job['job_id'],
            )
            if result.get('success'):
                flushed += 1
                bridge.queue_broadcast({
                    'type': 'clip_export_started',
                    'data': {
                        'clip_id': job.get('clip_id'),
                        'job_id': job['job_id'],
                        'room_id': job['room_id'],
                    },
                })
                _log.info("延后导出入队: room=%s, job_id=%s", job['room_id'], job['job_id'])
            else:
                _deferred_export_jobs.append(job)
                _log.warning("延后导出入队失败: %s", result.get('error'))
        return flushed

    def _build_export_profile(settings, preset_id=None):
        """全系统唯一的 ExportProfile 构建入口。

        从 settings + preset_id 构造导出配置。preset_id 优先于 settings 全局值，
        供 queue_export（手动/自动导出统一入口）和 handle_export_clip 复用，
        消除 profile 构造重复。

        preset_id 为空或找不到时回退到全局 settings。
        """
        encoder = settings.get('encoder', 'h264_nvenc')
        crf_val = int(settings.get('crf', 23))
        resolution = settings.get('resolution', '')
        framerate = settings.get('framerate', '原画')
        audio_br = settings.get('audio_bitrate', '128k')
        vertical_crop = False

        if preset_id:
            preset = _get_export_preset(preset_id)
            if preset:
                encoder = preset.get('codec', encoder)
                crf_val = preset.get('crf', crf_val)
                resolution = preset.get('resolution', resolution)
                framerate = preset.get('framerate', framerate)
                audio_br = preset.get('audio_bitrate', audio_br)
                vertical_crop = preset.get('vertical_crop', vertical_crop)

        codec_map = {
            'H.264 NVENC': 'h264_nvenc', 'H.264 CPU': 'libx264',
            'H.265 NVENC': 'hevc_nvenc', 'H.265 CPU': 'libx265',
            'Copy': 'copy', 'h264_nvenc': 'h264_nvenc',
            'libx264': 'libx264', 'hevc_nvenc': 'hevc_nvenc',
            'libx265': 'libx265', 'copy': 'copy',
        }
        rate_mode_map = {'CRF 质量': 'crf', '码率限制': 'bitrate', '不限制': 'unrestricted'}
        bitrate = str(settings.get('bitrate', 8000))
        video_bitrate = f"{bitrate}k" if not bitrate.endswith(('k', 'M')) else bitrate
        if resolution and ":" in resolution:
            resolution = resolution.replace(":", "x")
        enc_preset = settings.get('preset', 'medium')

        return ExportProfile(
            codec=codec_map.get(encoder, 'libx264'),
            crf=crf_val, preset=enc_preset,
            rate_mode=rate_mode_map.get(settings.get('param_mode', 'CRF 质量'), 'crf'),
            video_bitrate=video_bitrate, audio_bitrate=audio_br,
            resolution=resolution, fps=_parse_fps(framerate),
            vertical_crop=vertical_crop,
        )

    async def _process_export_job(job):
        """处理单个导出任务 — bridge.call 提交到 Qt 主线程，等待 FFmpeg 完成后才返回。

        done_event 在两种情况下被 set：
        1. manager.start_export 启动失败（无 clip_id）→ 立即 set，不启动 FFmpeg
        2. FFmpeg 导出完成（on_done 回调）→ 异步 set
        这保证队列中下一个任务必须等上一个 FFmpeg 完成后才开始。
        """
        room_id = job['room_id']
        export_start = job['start']
        export_end = job['end']
        label = job['label']
        output_dir = job['output_dir']
        profile = job['profile']
        job_id = job['job_id']

        loop = asyncio.get_running_loop()
        done_event = asyncio.Event()
        result = {'success': False, 'clip_id': '', 'error': ''}

        def on_done(success, output_path, error, size_mb, thumbnail_path):
            """FFmpeg 导出完成的回调（Qt 主线程）。"""
            export_jobs.pop(job_id, None)
            if success:
                asyncio.run_coroutine_threadsafe(server.broadcast('clip_completed', {
                    'room_id': room_id, 'start': export_start, 'end': export_end,
                    'label': label, 'room_name': job.get('room_name', ''),
                    'thumbnail_path': thumbnail_path or '', 'output_path': output_path,
                    'job_id': job_id,
                }), loop)
            else:
                asyncio.run_coroutine_threadsafe(server.broadcast('clip_failed', {
                    'room_id': room_id, 'job_id': job_id,
                    'error': error or '导出失败',
                }), loop)
            loop.call_soon_threadsafe(done_event.set)

        def on_progress(percent, elapsed, total):
            asyncio.run_coroutine_threadsafe(server.broadcast('export_progress', {
                'room_id': room_id, 'job_id': job_id,
                'percent': float(percent), 'elapsed': float(elapsed), 'total': float(total),
            }), loop)

        def _run_export():
            """在 Qt 主线程提交导出（通过 bridge.call 调用）。"""
            try:
                clip_id = manager.start_export(
                    room_id, export_start, export_end,
                    output_dir=output_dir, title=label,
                    profile=profile, on_done=on_done, on_progress=on_progress,
                )
                result['clip_id'] = clip_id or ''
                if not clip_id:
                    # 启动失败：立即结束，不等待 FFmpeg
                    room = manager.get_room(room_id)
                    controller = None if room is None else room.controller
                    result['error'] = getattr(controller, '_last_export_error', '') or '导出启动失败'
                    loop.call_soon_threadsafe(done_event.set)
                else:
                    result['success'] = True
                    # 成功启动：等待 on_done 回调中 set event
            except Exception as exc:
                result['error'] = str(exc)
                loop.call_soon_threadsafe(done_event.set)

        await loop.run_in_executor(_bridge_executor, lambda: bridge.call(_run_export))

        if result['success'] and result['clip_id']:
            # 立即注册，使 cancel 可以在 FFmpeg 执行期间定位进程
            export_jobs[job_id] = result['clip_id']
        elif result['error']:
            _log.error("导出任务失败: room=%s, job=%s, error=%s", room_id, job_id, result['error'])

        # 等待 FFmpeg 实际完成（通过 on_done 回调 set event）
        await done_event.wait()

    async def _export_queue_worker():
        """Worker 队列消费循环：池内 worker 并行处理（全局同时 ≤ _EXPORT_MAX_CONCURRENT 个导出）。"""
        while True:
            job = await _export_queue.get()
            job_id = job.get('job_id', '')
            # 检查是否已被取消（在排队期间被前端取消）
            if job_id and job_id in _export_cancelled_jobs:
                _export_cancelled_jobs.discard(job_id)
                _export_queue.task_done()
                room_id = job.get('room_id', '')
                asyncio.run_coroutine_threadsafe(server.broadcast('clip_failed', {
                    'room_id': room_id, 'job_id': job_id, 'error': '导出已取消',
                }), asyncio.get_running_loop())
                continue
            try:
                await _process_export_job(job)
            except Exception as exc:
                _log.error("导出队列异常: %s", exc, exc_info=True)
                if job_id:
                    export_jobs.pop(job_id, None)
                    room_id = job.get('room_id', '')
                    asyncio.run_coroutine_threadsafe(server.broadcast('clip_failed', {
                        'room_id': room_id, 'job_id': job_id, 'error': f'导出异常: {exc}',
                    }), asyncio.get_running_loop())
            finally:
                _export_queue.task_done()

    async def _ensure_export_queue():
        """确保全局导出队列和 worker 池已初始化（池大小 = _EXPORT_MAX_CONCURRENT）。"""
        global _export_queue, _EXPORT_WORKERS
        async with _export_queue_lock:
            if _export_queue is None:
                _export_queue = asyncio.Queue()
            # 清理已结束的 worker，补充到池大小
            _EXPORT_WORKERS[:] = [t for t in _EXPORT_WORKERS if not t.done()]
            while len(_EXPORT_WORKERS) < _EXPORT_MAX_CONCURRENT:
                _EXPORT_WORKERS.append(asyncio.create_task(_export_queue_worker()))
            if len(_EXPORT_WORKERS) == _EXPORT_MAX_CONCURRENT:
                _log.debug("导出队列 worker 池已就绪: %d 个 worker", len(_EXPORT_WORKERS))

    async def queue_export(room_id, start_sec, end_sec, label='clip', preset_id='',
                          source='', job_id='',
                          mark_in_wallclock=None, mark_out_wallclock=None,
                          recording_start_mono=None, recording_media_start_mono=None,
                          use_room_marks=False):
        """统一导出入口：校验参数、计算时间映射、构建 profile、入队。

        所有导出路径（手动/自动/分析/clip_id）均通过此函数入队，
        保证全局同时最多 _EXPORT_MAX_CONCURRENT 个 FFmpeg 导出进程。

        时间映射优先级（§2.1）：
        1. 请求携带 mark_in/out_wallclock + recording_*_mono 快照 → 精确映射
        2. source == 'ai_highlight' → 直接使用传入 start/end
        3. use_room_marks=True → 使用房间当前 mark_*_wallclock（仅「导当前选区」）
        4. 否则 → start/end + content_offset，precision=approximate
        """
        if not room_id:
            return {'error': 'room_id is required'}
        if start_sec >= end_sec:
            return {'error': '入点必须早于出点'}

        await _ensure_export_queue()

        room = manager.get_room(room_id)
        if room is None:
            return {'error': '房间不存在'}

        room_name = getattr(room, 'streamer_name', '') or room_id
        precision = 'exact'
        content_offset = float(getattr(room, 'content_offset', 0.0) or 0.0)

        # 请求快照字段（列表导出时由前端 handleAddClip 快照写入）
        # NOTE: content_offset is NOT snapshotted; still read from live room state.
        snap_in = mark_in_wallclock
        snap_out = mark_out_wallclock
        snap_rec = recording_media_start_mono if recording_media_start_mono is not None else recording_start_mono
        # 兼容字符串/None：_safe_float 风格的宽松转换
        if snap_in is not None:
            try:
                snap_in = float(snap_in)
            except (TypeError, ValueError):
                snap_in = None
        if snap_out is not None:
            try:
                snap_out = float(snap_out)
            except (TypeError, ValueError):
                snap_out = None
        if snap_rec is not None:
            try:
                snap_rec = float(snap_rec)
            except (TypeError, ValueError):
                snap_rec = None

        if snap_in is not None and snap_out is not None and snap_rec is not None:
            export_start = max(0.0, snap_in - snap_rec - content_offset)
            export_end = max(0.0, snap_out - snap_rec - content_offset)
            precision = 'exact'
        elif source == 'ai_highlight':
            export_start = max(0.0, start_sec)
            export_end = max(0.0, end_sec)
            precision = 'exact'
        elif use_room_marks:
            mark_in_wc = getattr(room, 'mark_in_wallclock', None)
            mark_out_wc = getattr(room, 'mark_out_wallclock', None)
            rec_start = getattr(room, 'recording_media_start_mono', None) or getattr(room, 'recording_start_mono', None)
            if mark_in_wc is not None and mark_out_wc is not None and rec_start is not None:
                export_start = max(0.0, mark_in_wc - rec_start - content_offset)
                export_end = max(0.0, mark_out_wc - rec_start - content_offset)
                precision = 'exact'
            else:
                export_start = max(0.0, start_sec - content_offset)
                export_end = max(0.0, end_sec - content_offset)
                precision = 'approximate'
                _log.warning(
                    "导出降级：use_room_marks 但墙钟不可用，使用 start/end "
                    "(room=%s, start=%.2f, end=%.2f)",
                    room_id, export_start, export_end,
                )
        else:
            export_start = max(0.0, start_sec - content_offset)
            export_end = max(0.0, end_sec - content_offset)
            precision = 'approximate'
            _log.warning(
                "导出降级：无墙钟快照，使用 start/end "
                "(room=%s, start=%.2f, end=%.2f)",
                room_id, export_start, export_end,
            )

        if export_start >= export_end:
            return {'error': '导出时间范围无效（入点>=出点）'}

        settings = load_settings()
        profile = _build_export_profile(settings, preset_id)
        output_dir = _expand_user_path(
            settings.get('output_dir', os.path.join(os.path.expanduser('~'), 'LSC', 'output'))
        )

        if not job_id:
            job_id = f"q-{int(time.time() * 1000)}-{room_id[:6]}"

        job = {
            'room_id': room_id, 'start': export_start, 'end': export_end,
            'label': label, 'output_dir': output_dir, 'profile': profile,
            'job_id': job_id, 'room_name': room_name,
        }
        await _export_queue.put(job)
        _log.debug("导出已入队: room=%s, job=%s, %.1f-%.1f, precision=%s, queue_size=%d",
                   room_id, job_id, export_start, export_end, precision, _export_queue.qsize())
        return {'success': True, 'queued': True, 'job_id': job_id, 'precision': precision}

    async def _continuous_valorant_worker(
        room_id, mode, game, threshold,
        _continuous_tasks, _analysis_semaphore, _bridge_executor,
        scan_result_container: dict,
    ) -> None:
        """后台 Worker：连续执行 detect_valorant_rounds / detect_rounds_by_audio_rhythm。

        持有 _analysis_semaphore 确保同时只有 1 个 FFmpeg。
        主循环通过 scan_result_container['video_path'] / ['current_dur'] / ['refine_with_ocr']
        传入最新参数，通过 ['done_event'] 获知何时消费完毕、何时需重启分析。
        """
        loop = asyncio.get_running_loop()
        _log.info(f"持续分析 Worker 启动: room_id={room_id}, mode={mode}")
        try:
            while not _continuous_tasks.get(room_id, {}).get('cancelled'):
                # 等待主循环 kick 或上一次结果被消费
                await asyncio.sleep(0.5)
                task_state = _continuous_tasks.get(room_id)
                if not task_state or task_state.get('cancelled'):
                    break
                if not task_state.get('scan_requested'):
                    continue

                video_path = task_state.get('video_path')
                current_dur = task_state.get('current_dur', 0.0)
                refine_with_ocr = task_state.get('refine_with_ocr', False)
                scan_range = task_state.get('scan_range', (0.0, current_dur))
                scan_timeout = int(task_state.get('scan_timeout', 120))

                if not video_path or current_dur <= 3.0:
                    task_state['scan_requested'] = False
                    continue

                _vp = video_path  # capture for closure
                def _scan_cancel_check():
                    return _continuous_tasks.get(room_id, {}).get('cancelled', False)

                def _do_scan(_vp=_vp, _dur=current_dur, _ocr=refine_with_ocr, _range=scan_range):
                    from lsc.config import load_config as _load_cfg_r
                    _cfg = _load_cfg_r()
                    _ffmpeg = _cfg.ffmpeg_path or shutil.which("ffmpeg") or "ffmpeg"
                    _cancel = _scan_cancel_check
                    if game == 'valorant':
                        from lsc.analyzer.round_detector import (
                            ValorantRoundConfig, detect_valorant_rounds,
                        )
                        try:
                            _ocr_iv = float(task_state.get('ocr_sample_interval', 2.0) or 2.0)
                        except (TypeError, ValueError):
                            _ocr_iv = 2.0
                        _round_config = ValorantRoundConfig(
                            full_round=True,
                            phase_sample_interval=max(2.0, _ocr_iv),
                        )
                        return detect_valorant_rounds(
                            _vp, ffmpeg_path=_ffmpeg, duration=_dur,
                            cancel_check=_cancel,
                            refine_with_ocr=_ocr,
                            time_range=_range,
                            config=_round_config,
                        )
                    return _detect_rounds_by_audio_rhythm(
                        _vp, duration=_dur, ffmpeg_path=_ffmpeg,
                        time_range=_range,
                        cancel_check=_cancel,
                    )

                task_state['scan_requested'] = False
                task_state['scan_running'] = True
                try:
                    async with _analysis_semaphore:
                        result = await asyncio.wait_for(
                            loop.run_in_executor(_bridge_executor, _do_scan),
                            timeout=scan_timeout,
                        )
                    scan_result_container['result'] = result or []
                    scan_result_container['video_path'] = video_path
                    scan_result_container['current_dur'] = current_dur
                    scan_result_container['completed_at'] = time.time()
                    _log.info(f"持续分析 Worker 完成: room_id={room_id}, {len(result or [])} 回合")
                except Exception as exc:
                    _log.warning(f"持续分析 Worker 异常: room_id={room_id}, {exc}")
                    scan_result_container['result'] = []
                finally:
                    task_state['scan_running'] = False
                    done_ev = task_state.get('scan_done_event')
                    if done_ev is not None:
                        try:
                            done_ev.set()
                        except Exception:
                            _log.debug("scan_done_event.set 失败", exc_info=True)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            _log.error(f"持续分析 Worker 异常退出: room_id={room_id}, {exc}", exc_info=True)

    async def _continuous_analysis_loop(
        main_room_id: str,
        target_room_ids: list[str],
        interval: int,
        threshold: float,
        mode: str = 'scene', game: str = 'valorant',
    ) -> None:
        """持续分析后台循环（生产者-消费者模式）。

        Worker (_continuous_valorant_worker) 在后台独立执行 OCR/音频分析。
        主循环每 interval 秒：
          1. 更新任务状态 (video_path/dur/refine_with_ocr/scan_range)
          2. Kick worker (scan_requested=True)
          3. 消费上一轮结果（如果已被 worker 写入 scan_result_container）
          4. 导出 + 广播

        关键改进：主循环不再被 detect_valorant_rounds 阻塞。
        """
        room_id = main_room_id
        last_analyzed = 0.0
        last_consumed_at = 0.0
        all_highlights: list[dict[str, Any]] = []
        # 收尾分析状态：录制停止后强制再扫一次完整文件，不丢弃尾部回合
        _finalize_pending = False        # 是否有待完成的收尾扫描
        _finalize_started = False        # 收尾扫描是否已启动
        _recording_was_active = False    # 录制是否曾经处于活跃状态
        _recording_stop_ticks = 0        # 录制停止后经过的 tick 数（延迟确认防抖）
        video_path = ''
        current_dur = 0.0
        loop = asyncio.get_running_loop()
        _valorant_incremental_rounds = mode == "valorant_round" and game == "valorant"

        # Worker 共享状态
        scan_result: dict[str, Any] = {'result': [], 'video_path': '', 'current_dur': 0.0, 'completed_at': 0.0}
        scan_done_event = asyncio.Event()

        def _get_recording_file_info():
            room = manager.get_room(room_id)
            if room is None or not room.record_output_path:
                return None, 0.0
            path = room.record_output_path
            if os.path.isfile(path):
                dur = _get_video_duration(path)
                if dur > 0:
                    return path, dur
            base, ext = os.path.splitext(path)
            for candidate in (path + '.tmp', base + '.tmp', path + '.tmp' + ext):
                if os.path.isfile(candidate):
                    dur = _get_video_duration(candidate)
                    if dur > 0:
                        return candidate, dur
            return None, 0.0

        # 初始化任务状态
        if room_id not in _continuous_tasks:
            _continuous_tasks[room_id] = {}
        _continuous_tasks[room_id].update({
            'cancelled': False,
            'scan_requested': False,
            'scan_running': False,
            'scan_done_event': scan_done_event,
            'video_path': '',
            'current_dur': 0.0,
            'refine_with_ocr': False,
            'scan_range': (0.0, 0.0),
            'scan_timeout': 120,
            'full_rescan': True,
            'last_analyzed': 0.0,
            'highlights': [],
            'result_ready': False,
        })

        # 启动后台 Worker
        _worker_task = asyncio.create_task(
            _continuous_valorant_worker(
                room_id, mode, game, threshold,
                _continuous_tasks, _analysis_semaphore, _bridge_executor,
                scan_result,
            ),
            name=f"continuous-worker-{room_id[:8]}",
        )

        _log.info("持续分析启动: room_id=%s, mode=%s, game=%s, interval=%ds, 增量回合窗口=%s",
                  room_id, mode, game, interval, _valorant_incremental_rounds)

        _scan_counter = 0  # tick 计数器，用于状态机与局部 OCR 预算
        try:
            while not _continuous_tasks.get(room_id, {}).get('cancelled'):
                pressure = get_resource_pressure()
                effective_interval, skip_for_pressure = _continuous_effective_interval(
                    max(interval, 20), last_analyzed, _valorant_incremental_rounds, pressure,
                )
                state = _continuous_tasks.get(room_id)
                if state:
                    state['resource_pressure'] = pressure
                    state['effective_interval'] = effective_interval

                # Fix: 首次 tick 加速；扫描中短轮询；Worker 完成立即唤醒
                _sleep_time = float(effective_interval)
                if last_analyzed <= 0.0:
                    _sleep_time = min(_sleep_time, 10.0)
                state = _continuous_tasks.get(room_id)
                if state and (state.get('scan_running') or state.get('scan_requested')):
                    _sleep_time = min(_sleep_time, 2.0)

                _pending_result = float(scan_result.get('completed_at', 0.0) or 0.0) > last_consumed_at
                if not _pending_result:
                    try:
                        scan_done_event.clear()
                        await asyncio.wait_for(scan_done_event.wait(), timeout=_sleep_time)
                    except asyncio.TimeoutError:
                        pass
                    except asyncio.CancelledError:
                        break

                state = _continuous_tasks.get(room_id)
                if not state or state.get('cancelled'):
                    break
                video_path, current_dur = await loop.run_in_executor(
                    _bridge_executor, _get_recording_file_info,
                )
                room_obj = manager.get_room(room_id)
                is_still_recording = bool(room_obj and getattr(room_obj, 'is_recording', False))
                recording_start = float(getattr(room_obj, 'recording_start_mono', 0.0) or 0.0)
                recorded_duration = max(
                    current_dur,
                    time.monotonic() - recording_start if is_still_recording and recording_start else 0.0,
                )
                state['video_path'] = video_path or ''
                state['current_dur'] = current_dur
                state['recorded_duration'] = recorded_duration
                state['analysis_stage'] = (
                    '扫描中' if state.get('scan_running')
                    else '等待可分析片段' if is_still_recording and not video_path
                    else '等待新片段' if is_still_recording
                    else '等待新录制'
                )

                if is_still_recording:
                    _recording_was_active = True
                    _recording_stop_ticks = 0
                elif False and _recording_was_active:
                    _recording_stop_ticks += 1
                    if _recording_stop_ticks >= 2 and not _finalize_started:
                        _finalize_pending = True
                        _log.info("持续分析收尾: 录制已停止，触发最终完整扫描 room_id=%s", room_id)
                if skip_for_pressure:
                    bridge.queue_broadcast({
                        'type': 'continuous_analysis_status',
                        'data': {
                            'running': True,
                            'room_id': room_id,
                            'target_room_ids': target_room_ids,
                            'mode': mode,
                            'analyzed_duration': last_analyzed,
                            'recorded_duration': state.get('recorded_duration', current_dur),
                            'confirmed_rounds': state.get('confirmed_rounds', 0),
                            'pending_rounds': state.get('pending_rounds', 0),
                            'analysis_stage': state.get('analysis_stage', '分析中'),
                            'total_highlights': len(all_highlights),
                            'phase': 'finalizing' if _finalize_started else 'running',
                            'updated_at': time.time(),
                            'scan_mode': 'incremental' if _valorant_incremental_rounds else 'full',
                            'scan_range': [max(0.0, current_dur - 1.0), current_dur] if current_dur else [0.0, 0.0],
                            'scan_timeout': state.get('scan_timeout', 120),
                            'full_rescan': bool(state.get('full_rescan', False)),
                            'refine_with_ocr': bool(state.get('refine_with_ocr', False)),
                            'progress': min(100.0, max(0.0, (last_analyzed / max(current_dur, 1.0)) * 100.0)) if current_dur else 0.0,
                            'scan_phase': state.get('scan_phase'),
                            'scan_reason': state.get('scan_reason'),
                            'effective_interval': effective_interval,
                        },
                    })
                    _log.info("持续分析让路: room_id=%s, pressure=%s", room_id, pressure.get("level"))
                    continue

                worker_completed_at = scan_result.get('completed_at', 0.0)
                worker_dur = scan_result.get('current_dur', 0.0)
                worker_result = scan_result.get('result', [])
                _time_since_last_consume = time.time() - last_consumed_at
                _min_consume_interval = 30.0 if _valorant_incremental_rounds else 0.0
                can_consume = (
                    worker_completed_at > last_consumed_at
                    and (
                        worker_dur > last_analyzed + 5.0
                        or _finalize_started
                        or not _valorant_incremental_rounds
                        or _time_since_last_consume > _min_consume_interval
                    )
                )
                if can_consume:
                    last_consumed_at = worker_completed_at
                    new_hl = _cleanup_segments(list(worker_result))
                    for h in new_hl:
                        h.setdefault("reason", "回合战斗阶段")
                        h.setdefault("speech_score", 0.0)
                        h.setdefault("visual_score", 0.0)
                        h.setdefault("transcript", "")

                    publish_update = False
                    if _valorant_incremental_rounds:
                        window_rounds = (
                            list(new_hl)
                            if _finalize_started
                            else _drop_open_tail_rounds(new_hl, worker_dur)
                        )
                        full_rounds = _merge_round_windows(all_highlights, window_rounds)
                        publish_update = _round_lists_changed(all_highlights, full_rounds) or bool(window_rounds)
                        new_hl = _new_rounds(all_highlights, full_rounds)
                        all_highlights = full_rounds
                    elif mode == 'scene':
                        all_highlights = _merge_highlights(all_highlights, new_hl)
                        publish_update = bool(new_hl)
                    else:
                        all_highlights = new_hl
                        publish_update = bool(new_hl)

                    retry_pending_exports = _valorant_incremental_rounds and any(
                        _is_auto_exportable_valorant_round(h)
                        and any(
                            f"{rid}:{_valorant_round_key(h)}" not in _exported_clip_ids
                            for rid in target_room_ids
                        )
                        for h in all_highlights
                    )
                    if (publish_update and new_hl) or retry_pending_exports:
                        await _export_and_broadcast(
                            room_id, main_room_id, target_room_ids, manager,
                            bridge, all_highlights, new_hl,
                            scan_result.get('video_path', ''), mode,
                        )
                        ok, _, main_room_for_map, target_rooms_for_map = _validate_synced_analysis_targets(
                            manager, main_room_id, target_room_ids, wait_for_file=True,
                        )
                        if _valorant_incremental_rounds:
                            confirmed_hl = [
                                h for h in all_highlights
                                if _is_auto_exportable_valorant_round(h)
                                and any(
                                    f"{rid}:{_valorant_round_key(h)}" not in _exported_clip_ids
                                    for rid in target_room_ids
                                )
                            ]
                        else:
                            confirmed_hl = list(new_hl)
                        if confirmed_hl and ok and main_room_for_map is not None and target_rooms_for_map:
                            # 从 settings 读取用户默认导出预设，保证持续分析与手动导出使用同一配置
                            _auto_preset = load_settings().get('appSettings', {}).get('default_export_preset', '')
                            submitted_round_rooms = await _auto_export_highlights(
                                main_room_for_map, target_rooms_for_map, confirmed_hl,
                                job_prefix=f"{int(time.time() * 1000)}",
                                preset_id=_auto_preset,
                                defer_export=True,
                            )
                            for h in confirmed_hl:
                                key = _valorant_round_key(h)
                                for target in target_rooms_for_map:
                                    rid = getattr(target, 'room_id', '')
                                    if (key, rid) in submitted_round_rooms:
                                        _exported_clip_ids.add(f"{rid}:{key}")
                            _log.info("持续分析入列(延后导出): room_id=%s, 确认 %d 段 × %d 房间",
                                      room_id, len(confirmed_hl), len(target_rooms_for_map))

                    # 压力缓解或收尾时冲刷延后导出队列
                    await _flush_deferred_exports(force=_finalize_started)

                    last_analyzed = worker_dur
                    if room_id in _continuous_tasks:
                        _continuous_tasks[room_id]['last_analyzed'] = last_analyzed
                        _continuous_tasks[room_id]['recorded_duration'] = max(recorded_duration, worker_dur)
                        confirmed_total = sum(
                            1 for item in all_highlights
                            if _is_auto_exportable_valorant_round(item)
                        )
                        _continuous_tasks[room_id]['confirmed_rounds'] = confirmed_total
                        _continuous_tasks[room_id]['pending_rounds'] = max(0, len(all_highlights) - confirmed_total)
                        _continuous_tasks[room_id]['analysis_stage'] = '收尾中' if _finalize_started else '分析中'
                        _continuous_tasks[room_id]['highlights'] = all_highlights
                        _continuous_tasks[room_id]['result_ready'] = False
                        _continuous_tasks[room_id]['full_rescan'] = False

                    _log.info("持续分析增量: room_id=%s, mode=%s, 新增 %d 段, 累计 %d 段 (已分析到 %.1fs)",
                              room_id, mode, len(new_hl), len(all_highlights), worker_dur)

                    if _finalize_started and _finalize_pending and worker_dur <= last_analyzed + 5.0:
                        _finalize_pending = False
                        _log.info("持续分析收尾完成: room_id=%s, 累计 %d 段", room_id, len(all_highlights))
                        bridge.queue_broadcast({
                            'type': 'continuous_analysis_complete',
                            'data': {
                                'room_id': room_id,
                                'total_highlights': len(all_highlights),
                            },
                        })
                        if room_id in _continuous_tasks:
                            _continuous_tasks[room_id]['completed'] = True
                            _continuous_tasks[room_id]['finalizing'] = False
                            _continuous_tasks[room_id]['cancelled'] = True

                state = _continuous_tasks.get(room_id)
                if not state or state.get('cancelled'):
                    break
                if video_path:
                    should_kick = False
                    if _finalize_pending and not _finalize_started:
                        should_kick = True
                        _finalize_started = True
                        if room_id in _continuous_tasks:
                            _continuous_tasks[room_id]['finalizing'] = True
                    elif _valorant_incremental_rounds:
                        # 文件时长可能滞后于墙钟录制时长；用二者较大值决定是否 kick
                        kick_dur = max(current_dur, float(state.get('recorded_duration', 0.0) or 0.0))
                        should_kick = kick_dur > last_analyzed + 15.0
                    elif current_dur > last_analyzed + 12.0:
                        should_kick = True

                    if should_kick and not state.get('scan_running'):
                        state['last_progress_broadcast_at'] = time.time()
                        _scan_counter += 1
                        scan_range, use_ocr_this_tick, _scan_timeout, full_rescan = _continuous_valorant_scan_budget(
                            mode, last_analyzed, current_dur, pressure, _scan_counter
                        )
                        if state.get('scan_range') == scan_range and state.get('scan_phase') == ('full' if full_rescan else 'incremental'):
                            continue
                        state['video_path'] = video_path
                        state['current_dur'] = current_dur
                        state['refine_with_ocr'] = use_ocr_this_tick
                        try:
                            state['ocr_sample_interval'] = float(
                                pressure.get('ocr_sample_interval', 2.0) or 2.0
                            )
                        except (TypeError, ValueError):
                            state['ocr_sample_interval'] = 2.0
                        # critical 且未 pause：奇数 tick 纯音频先追赶，偶数 tick 再 OCR（降载）
                        if (
                            pressure.get('level') == 'critical'
                            and not pressure.get('pause_analysis')
                            and _scan_counter % 2 == 1
                        ):
                            state['refine_with_ocr'] = False
                        state['scan_range'] = scan_range
                        state['full_rescan'] = full_rescan
                        state['scan_timeout'] = _scan_timeout
                        state['scan_requested'] = True
                        state['scan_phase'] = 'full' if full_rescan else 'incremental'
                        state['scan_reason'] = 'finalize' if _finalize_started else 'audio_increment'

                        bridge.queue_broadcast({
                            'type': 'continuous_analysis_status',
                            'data': {
                                'running': True,
                                'room_id': room_id,
                                'target_room_ids': target_room_ids,
                                'mode': mode,
                                'analyzed_duration': last_analyzed,
                                'recorded_duration': state.get('recorded_duration', current_dur),
                                'confirmed_rounds': state.get('confirmed_rounds', 0),
                                'pending_rounds': state.get('pending_rounds', 0),
                                'analysis_stage': state.get('analysis_stage', '分析中'),
                                'total_highlights': len(all_highlights),
                                'phase': 'finalizing' if _finalize_started else 'running',
                                'updated_at': time.time(),
                                'scan_mode': 'full' if full_rescan else 'incremental',
                                'scan_range': [scan_range[0], scan_range[1]],
                                'scan_timeout': _scan_timeout,
                                'full_rescan': full_rescan,
                                'refine_with_ocr': use_ocr_this_tick,
                                'progress': min(100.0, max(0.0, (last_analyzed / max(scan_range[1], 1.0)) * 100.0)),
                                'scan_phase': 'full' if full_rescan else 'incremental',
                                'scan_reason': 'finalize' if _finalize_started else 'audio_increment',
                                'effective_interval': effective_interval,
                            },
                        })

                        if not state.get('scan_running'):
                            _log.info(f"持续分析 kick worker: room_id={room_id}, dur={current_dur:.0f}s, range={scan_range[0]:.0f}-{scan_range[1]:.0f}, OCR={use_ocr_this_tick}, full={full_rescan}, finalize={_finalize_started}")

                # 每 tick 广播状态（含等待中），避免 UI 卡在 analyzed_duration /「等待新片段」
                bridge.queue_broadcast({
                    'type': 'continuous_analysis_status',
                    'data': {
                        'running': True,
                        'room_id': room_id,
                        'target_room_ids': target_room_ids,
                        'mode': mode,
                        'analyzed_duration': last_analyzed,
                        'recorded_duration': state.get('recorded_duration', current_dur),
                        'confirmed_rounds': state.get('confirmed_rounds', 0),
                        'pending_rounds': state.get('pending_rounds', 0),
                        'analysis_stage': state.get('analysis_stage', '分析中'),
                        'total_highlights': len(all_highlights),
                        'phase': 'finalizing' if _finalize_started else 'running',
                        'updated_at': time.time(),
                        'scan_mode': state.get('scan_phase', 'incremental'),
                        'scan_range': list(state.get('scan_range', (0.0, 0.0))) if isinstance(state.get('scan_range'), (list, tuple)) else [0.0, 0.0],
                        'scan_timeout': state.get('scan_timeout', 120),
                        'full_rescan': bool(state.get('full_rescan', False)),
                        'refine_with_ocr': bool(state.get('refine_with_ocr', False)),
                        'progress': min(100.0, max(0.0, (last_analyzed / max(current_dur, 1.0)) * 100.0)) if current_dur else 0.0,
                        'scan_phase': 'running' if state.get('scan_running') else state.get('scan_phase'),
                        'scan_reason': 'scanning' if state.get('scan_running') else state.get('scan_reason'),
                        'effective_interval': effective_interval,
                    },
                })

                # 检测录制停止，触发收尾分析
                room_obj = manager.get_room(room_id)
                is_still_recording = bool(room_obj and getattr(room_obj, 'is_recording', False))
                if is_still_recording:
                    _recording_was_active = True
                    _recording_stop_ticks = 0
                elif _recording_was_active:
                    _recording_stop_ticks += 1
                    # 延迟 2 个 tick 确认录制真的停止了（防网络抖动误触发）
                    if _recording_stop_ticks >= 2 and not _finalize_started:
                        _finalize_pending = True
                        _log.info("持续分析收尾: 录制已停止，触发最终完整扫描 room_id=%s", room_id)
                        await _flush_deferred_exports(force=True)
                    # 如果录制恢复（重新开始录制），取消收尾
                    if _finalize_pending and is_still_recording:
                        _finalize_pending = False
                        _finalize_started = False
                        _log.info("持续分析收尾取消: 录制已恢复 room_id=%s", room_id)


        except asyncio.CancelledError:
            pass
        except Exception as exc:
            _log.error("持续分析异常: room_id=%s, %s", room_id, exc, exc_info=True)
        finally:
            # 取消 worker
            if _worker_task and not _worker_task.done():
                _worker_task.cancel()
                try:
                    await asyncio.wait_for(_worker_task, timeout=5.0)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass
            _continuous_tasks.pop(room_id, None)
            _log.info("持续分析已停止: room_id=%s, 累计 %d 段高光", room_id, len(all_highlights))

    async def _export_and_broadcast(
        room_id, main_room_id, target_room_ids, manager, bridge,
        all_highlights, new_hl, video_path, mode,
    ) -> None:
        """导出 + 广播（公共逻辑，从原 loop 提取）"""
        mapped_highlights_by_room: dict[str, list[dict[str, Any]]] = {}
        ok, error, main_room_for_map, target_rooms_for_map = _validate_synced_analysis_targets(
            manager, main_room_id, target_room_ids, wait_for_file=True,
        )
        if ok and main_room_for_map is not None:
            mapped_highlights_by_room = _map_highlights_by_room(
                all_highlights, main_room_for_map, target_rooms_for_map,
            )
        elif main_room_for_map is not None:
            _log.warning("持续分析同步映射回退到主房间: %s", error)
            mapped_highlights_by_room = _map_highlights_by_room(
                all_highlights, main_room_for_map, [main_room_for_map],
            )
        for idx, hl in enumerate(new_hl):
            bridge.queue_broadcast({
                'type': 'highlight_stream',
                'data': {
                    'room_id': room_id, 'main_room_id': main_room_id,
                    'highlight': hl, 'index': idx,
                    'total_in_round': len(new_hl), 'round_total': len(all_highlights),
                },
            })
        try:
            bridge.queue_broadcast({
                'type': 'continuous_highlights',
                'data': {
                    'room_id': room_id, 'main_room_id': main_room_id,
                    'target_room_ids': target_room_ids,
                    'highlights': all_highlights, 'new_count': len(new_hl),
                    'total': len(all_highlights),
                    'mapped_highlights_by_room': mapped_highlights_by_room,
                },
            })
        except Exception as exc:
            _log.warning("广播持续分析高光失败: %s", exc)

        # 对于非 Valorant 回合模式（scene/generic），在此自动导出高光到各目标房间
        # Valorant 模式在主循环中已调用 _auto_export_highlights 完成导出
        if mode != 'valorant_round':
            try:
                if mapped_highlights_by_room and main_room_for_map is not None and target_rooms_for_map:
                    # 从 settings 读取用户默认导出预设，保证持续分析与手动导出使用同一配置
                    _auto_preset_generic = load_settings().get('appSettings', {}).get('default_export_preset', '')
                    for target_rid, hls in mapped_highlights_by_room.items():
                        if not hls:
                            continue
                        target_room = next(
                            (r for r in target_rooms_for_map if getattr(r, 'room_id', '') == target_rid),
                            None,
                        )
                        if target_room:
                            await _auto_export_highlights(
                                main_room_for_map,
                                [target_room],
                                hls,
                                job_prefix=f"auto-{int(time.time() * 1000)}",
                                preset_id=_auto_preset_generic,
                            )
            except Exception as exc:
                _log.warning("持续分析自动导出失败: %s", exc)

        if video_path:
            save_analysis_results(video_path, room_id, mode, all_highlights)

    @server.on('start_continuous_analysis')
    async def handle_start_continuous_analysis(data):
        """启动持续分析（边录边分析）。

        参数:
            room_id: 房间 ID
            mode: 分析模式 'fast'（快速回合检测）| 'scene'（场景+音频检测）|
                'ai'（仅语音）| 'combined'（AI深度）
            interval: 增量分析间隔（秒，默认 60，AI 模式建议 300）
            threshold: 场景检测阈值（默认 0.3，会自适应降低）
        """
        data = data or {}
        main_room_id = data.get('main_room_id') or data.get('room_id')
        target_room_ids = data.get('target_room_ids') or [main_room_id]
        mode = data.get('mode', 'scene')
        interval = int(data.get('interval', 60))
        threshold = _safe_float(data.get('threshold', 0.3), 0.3)
        game = data.get('game', 'valorant')  # 'valorant' | 'generic'
        if not main_room_id:
            return {'error': 'room_id is required'}
        if _continuous_tasks:
            active_room_id = next(iter(_continuous_tasks))
            return {
                'success': False,
                'error': '已有持续分析任务正在运行',
                'active_room_id': active_room_id,
            }
        if interval < 10:
            interval = 10  # 最小 10s，避免过于频繁

        ok, error, main_room, target_rooms = _validate_synced_analysis_targets(
            manager,
            main_room_id,
            target_room_ids,
            wait_for_file=True,
        )
        if not ok:
            return {'success': False, 'error': error}
        resolved_target_room_ids = [
            getattr(room, "room_id", "") for room in target_rooms if getattr(room, "room_id", "")
        ]

        task = asyncio.create_task(_continuous_analysis_loop(main_room_id, resolved_target_room_ids, interval, threshold, mode, game))
        _continuous_tasks[main_room_id] = {
            'task': task,
            'last_analyzed': 0.0,
            'highlights': [],
            'cancelled': False,
            'completed': False,
            'finalizing': False,
            'mode': mode,
            'main_room_id': main_room_id,
            'target_room_ids': resolved_target_room_ids,
            'recorded_duration': 0.0,
            'confirmed_rounds': 0,
            'pending_rounds': 0,
            'analysis_stage': '等待新录制',
        }
        _log.info(
            "持续分析已启动: main_room_id=%s, targets=%s, mode=%s, interval=%ds, threshold=%.2f",
            main_room_id,
            resolved_target_room_ids,
            mode,
            interval,
            threshold,
        )
        bridge.queue_broadcast({
            'type': 'continuous_analysis_status',
            'data': {
                'running': True,
                'room_id': main_room_id,
                'target_room_ids': resolved_target_room_ids,
                'mode': mode,
                'analyzed_duration': 0.0,
                'total_highlights': 0,
                'recorded_duration': 0.0,
                'confirmed_rounds': 0,
                'pending_rounds': 0,
                'analysis_stage': '等待新录制',
                'phase': 'running',
                'updated_at': time.time(),
                'scan_mode': 'full',
                'scan_range': [0.0, 0.0],
                'scan_timeout': 120,
                'full_rescan': True,
                'refine_with_ocr': False,
            },
        })
        return {
            'success': True,
            'message': f'持续分析已启动（{mode} 模式，间隔 {interval}s）',
            'main_room_id': main_room_id,
            'target_room_ids': resolved_target_room_ids,
            'mode': mode,
        }

    @server.on('stop_continuous_analysis')
    async def handle_stop_continuous_analysis(data):
        """停止持续分析。"""
        data = data or {}
        requested_room_id = data.get('main_room_id') or data.get('room_id')
        room_id = requested_room_id
        if not room_id and len(_continuous_tasks) == 1:
            room_id = next(iter(_continuous_tasks))
        if room_id and room_id not in _continuous_tasks:
            for active_room_id, active_state in _continuous_tasks.items():
                active_targets = active_state.get('target_room_ids') or []
                if room_id == active_state.get('main_room_id') or room_id in active_targets:
                    room_id = active_room_id
                    break
        if not room_id:
            return {'error': 'room_id is required'}
        state = _continuous_tasks.get(room_id)
        if not state:
            return {'success': False, 'error': '该房间没有持续分析任务'}
        state['cancelled'] = True
        state['task'].cancel()
        _log.info("持续分析停止请求: room_id=%s", room_id)
        return {'success': True, 'room_id': room_id, 'requested_room_id': requested_room_id}

    @server.on('get_continuous_analysis_status')
    async def handle_get_continuous_analysis_status(data):
        """查询当前是否有正在运行的持续分析任务。"""
        if _continuous_tasks:
            active_room_id = next(iter(_continuous_tasks))
            task = _continuous_tasks[active_room_id]
            room = manager.get_room(active_room_id)
            recorded_duration = float(task.get('recorded_duration', task.get('last_analyzed', 0.0)) or 0.0)
            if room is not None and getattr(room, 'is_recording', False):
                started = float(getattr(room, 'recording_start_mono', 0.0) or 0.0)
                if started:
                    recorded_duration = max(recorded_duration, time.monotonic() - started)
            analysis_stage = task.get('analysis_stage', '分析中')
            if room is not None and getattr(room, 'is_recording', False) and analysis_stage == '等待新录制':
                analysis_stage = '等待可分析片段'
            return {
                'running': True,
                'room_id': active_room_id,
                'target_room_ids': task.get('target_room_ids', []),
                'mode': task.get('mode', 'scene'),
                'analyzed_duration': task.get('last_analyzed', 0.0),
                'total_highlights': len(task.get('highlights', [])),
                'recorded_duration': recorded_duration,
                'confirmed_rounds': task.get('confirmed_rounds', len(task.get('highlights', []))),
                'pending_rounds': task.get('pending_rounds', 0),
                'analysis_stage': analysis_stage,
                'phase': 'running',
                'updated_at': time.time(),
            }
        return {'running': False, 'phase': 'idle', 'updated_at': time.time()}


    # ── TimelineContext 集成 ──
    _timeline_svc = get_timeline_service()

    # ── create_clip_snapshot handler ──
    @server.on('create_clip_snapshot')
    async def handle_create_clip_snapshot(data):
        """一次把公共入出点原子映射到全部目标房间。

        任一路越界或时钟不可用则整组返回 RANGE_UNAVAILABLE。
        """
        timeline_id = data.get('timeline_id')
        common_start = float(data.get('common_start', 0))
        common_end = float(data.get('common_end', 0))
        target_room_ids = data.get('target_room_ids', [])
        source = data.get('source', 'manual')
        source_highlight_id = data.get('source_highlight_id', '')

        if not timeline_id:
            return {'success': False, 'error': 'timeline_id is required'}
        if not target_room_ids:
            return {'success': False, 'error': 'target_room_ids is required'}

        ctx = _timeline_svc.get_timeline(timeline_id)
        if ctx is None:
            return {'success': False, 'error': 'timeline not found or expired'}

        clips = []
        for room_id in target_room_ids:
            snap = _timeline_svc.create_clip_snapshot(
                timeline_id, room_id, common_start, common_end,
                source=source, source_highlight_id=source_highlight_id,
            )
            if snap is None:
                return {
                    'success': False,
                    'error': 'RANGE_UNAVAILABLE',
                    'failed_room': room_id,
                }
            clips.append({
                'clip_id': snap.clip_id,
                'clip_group_id': snap.clip_group_id,
                'room_id': snap.room_id,
                'recording_id': snap.recording_id,
                'common_start': snap.common_start,
                'common_end': snap.common_end,
                'source': snap.source,
            })

        return {'success': True, 'clips': clips}


    @server.on('get_clip_snapshot')
    async def handle_get_clip_snapshot(data):
        """通过 clip_id 获取 ClipSnapshot。"""
        clip_id = data.get('clip_id')
        if not clip_id:
            return {'error': 'clip_id is required'}
        snap = _timeline_svc.get_clip_snapshot(clip_id)
        if snap is None:
            return {'error': 'clip not found'}
        return {
            'success': True,
            'clip': {
                'clip_id': snap.clip_id,
                'clip_group_id': snap.clip_group_id,
                'timeline_id': snap.timeline_id,
                'recording_id': snap.recording_id,
                'common_start': snap.common_start,
                'common_end': snap.common_end,
                'room_id': snap.room_id,
                'source': snap.source,
                'output_path': snap.output_path,
                'exported': snap.exported,
                'error': snap.error,
            },
        }

    # ── export_clip 支持 clip_id 模式 ──
    # 原有 export_clip handler 仍保留（向后兼容），新增 clip_id 分支
    @server.on('export_clip_by_id')
    async def handle_export_clip_by_id(data):
        """通过 clip_id 导出切片 — 后端通过 recording_id 找到受信任文件。"""
        clip_id = data.get('clip_id')
        if not clip_id:
            return {'error': 'clip_id is required'}

        snap = _timeline_svc.get_clip_snapshot(clip_id)
        if snap is None:
            return {'error': 'clip not found or expired'}

        # 计算时间映射（需要 manager/room 访问，在当前线程执行）
        room = manager.get_room(snap.room_id)
        if room is None:
            return {'success': False, 'error': '房间不存在'}
        if room.recording_id != snap.recording_id:
            return {'success': False, 'error': '录制文件已变化，请重新创建切片'}
        if not room.record_output_path or not os.path.isfile(room.record_output_path):
            return {'success': False, 'error': '该房间没有录制文件'}

        content_offset = getattr(room, 'content_offset', 0.0)
        ctx = _timeline_svc.get_timeline(snap.timeline_id)
        if ctx is not None and snap.room_id in ctx.room_snapshots:
            rec_delta = ctx.room_snapshots[snap.room_id].recording_to_common_delta
            export_start = max(0.0, snap.common_start - rec_delta)
            export_end = max(0.0, snap.common_end - rec_delta)
        else:
            rec_start = getattr(room, 'recording_media_start_mono', None) or getattr(room, 'recording_start_mono', None)
            mark_in_wc = getattr(room, 'mark_in_wallclock', None)
            mark_out_wc = getattr(room, 'mark_out_wallclock', None)
            if mark_in_wc is not None and mark_out_wc is not None and rec_start is not None:
                export_start = max(0.0, mark_in_wc - rec_start - content_offset)
                export_end = max(0.0, mark_out_wc - rec_start - content_offset)
            else:
                export_start = max(0.0, snap.common_start - content_offset)
                export_end = max(0.0, snap.common_end - content_offset)

        jid = f"clip-{clip_id[:8]}"

        result = await queue_export(
            snap.room_id, export_start, export_end,
            label=data.get('label', 'clip'),
            preset_id=data.get('preset_id', ''),
            source='ai_highlight', job_id=jid,
        )

        if result.get('error'):
            return {'success': False, 'error': result['error']}

        return {'success': True, 'clip_id': clip_id, 'job_id': result['job_id'], 'queued': True}

    # ── timeline 查询 handler ──
    @server.on('get_timeline')
    async def handle_get_timeline(data):
        """获取指定房间的当前 TimelineContext。"""
        room_id = data.get('room_id')
        if not room_id:
            return {'error': 'room_id is required'}
        ctx = _timeline_svc.get_active_timeline_for_room(room_id)
        if ctx is None:
            return {'error': 'no active timeline'}
        return {
            'success': True,
            'timeline': {
                'timeline_id': ctx.timeline_id,
                'reference_room_id': ctx.reference_room_id,
                'preview_ready': ctx.preview_ready,
                'clip_ready': ctx.clip_ready,
                'created_at': ctx.created_at,
                'room_snapshots': {
                    rid: {
                        'preview_epoch_id': s.preview_epoch_id,
                        'recording_id': s.recording_id,
                        'preview_to_common_delta': s.preview_to_common_delta,
                        'recording_to_common_delta': s.recording_to_common_delta,
                        'align_confidence': s.align_confidence,
                    }
                    for rid, s in ctx.room_snapshots.items()
                },
            },
        }

    # 已保存的房间列表会在新客户端连接时由 on_connect 推送
