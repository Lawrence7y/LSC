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
import shutil
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any

import numpy as np

# 添加 lsc 到 Python 路径
import sys
_LSC_ROOT = os.path.join(os.path.dirname(__file__), '..', '..')
if _LSC_ROOT not in sys.path:
    sys.path.insert(0, _LSC_ROOT)

from lsc.config import ExportProfile
from lsc.gui.multi_room.manager import MultiRoomManager
from lsc.utils.error_messages import humanize_error
from lsc.core.services.resource_monitor import collect_system_stats

from persistence import load_rooms, save_rooms

_log = logging.getLogger('lsc.handlers')


SETTINGS_FILE = os.path.join(os.path.dirname(__file__), '..', 'settings.json')
RECORDING_HISTORY_FILE = os.path.join(os.path.dirname(__file__), '..', 'recording_history.json')


def _load_recording_history() -> list[dict[str, Any]]:
    """从文件加载录制历史，失败返回空列表。"""
    try:
        with open(RECORDING_HISTORY_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
    except Exception as exc:
        _log.warning("加载录制历史失败，使用空列表: %s", exc)
    return []


def _save_recording_history(history: list[dict[str, Any]]) -> None:
    """持久化录制历史到文件，失败时打印日志。"""
    try:
        with open(RECORDING_HISTORY_FILE, 'w', encoding='utf-8') as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
    except Exception as exc:
        _log.error("保存录制历史失败: %s", exc)


recording_history: list[dict[str, Any]] = _load_recording_history()

# Analytics jobs in progress: {room_id: {"progress": 0.0, "highlights": [...], "completed_at": float}}
_analysis_jobs: dict[str, dict[str, Any]] = {}
_ANALYSIS_JOB_TTL = 300.0  # 5 分钟后自动清理已完成的分析结果

# 导出任务映射：前端 job_id -> 后端 clip_id，用于取消导出时定位 FFmpeg 进程
export_jobs: dict[str, str] = {}

# MSE streamer instances keyed by room_id
_mse_streamers: dict[str, Any] = {}
# 保护 _mse_streamers 的锁：asyncio 线程与 run_in_executor 线程池均会并发访问
_mse_streamers_lock = threading.Lock()
# 正在启动 MSE 的 room_id 集合，防止启动过程中重复请求
_mse_starting: set[str] = set()

# MSE 预览自动重连状态: {room_id: {"attempts": int}}
_mse_reconnect_state: dict[str, dict[str, Any]] = {}
_MSE_MAX_RECONNECT = 3
_MSE_RECONNECT_BASE_DELAY = 2.0
_MSE_RECONNECT_MAX_DELAY = 30.0

# 专用线程池：录制操作（HTTP 刷新 + FFmpeg 启动）可阻塞 30s+，独立线程池避免饿死快操作
_recording_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix='rec')
# 快操作线程池：disconnect/mute/seek 等 bridge.call 操作，预期 <1s 完成
_bridge_executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix='bridge')

# 录制并发限流：最多同时启动 2 路录制，避免 6 路同时 HTTP 刷新 + FFmpeg 启动耗尽线程和 CPU
_recording_semaphore = asyncio.Semaphore(2)
# 正在提交录制启动的 room_id 集合，防止同一房间重复提交
_recording_starting: set[str] = set()


def _run_scene_analysis(video_path: str, threshold: float = 0.3, min_duration: float = 3.0) -> list[dict[str, Any]]:
    """Run FFmpeg scene detection on a video file.

    Returns list of highlight segments: [{"start": float, "end": float, "score": float}, ...]
    """
    import re
    from lsc.config import load_config as _load_cfg
    _cfg = _load_cfg()
    _ffmpeg = _cfg.ffmpeg_path or shutil.which("ffmpeg") or "ffmpeg"

    cmd = [
        _ffmpeg,
        "-i", video_path,
        "-vf", f"select='gt(scene\\\\,{threshold})',showinfo",
        "-vsync", "vfr",
        "-f", "null",
        "-",
    ]

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        return []
    except FileNotFoundError:
        return []

    # Parse showinfo output for pts_time
    # Format: [Parsed_showinfo_1 @ 0x...] n:123 pts:456 pts_time:12.34 ...
    pattern = re.compile(r"pts_time:(\d+\.?\d*)")
    timestamps: list[float] = []
    for line in proc.stderr.split("\n"):
        match = pattern.search(line)
        if match:
            timestamps.append(float(match.group(1)))

    if not timestamps:
        return []

    # Also get video duration from ffprobe
    duration = _get_video_duration(video_path)
    if duration <= 0:
        return []

    # Group consecutive scene changes into highlight segments
    highlights: list[dict[str, Any]] = []
    segment_start = timestamps[0]
    prev_ts = timestamps[0]

    for ts in timestamps[1:]:
        gap = ts - prev_ts
        if gap > 15:  # More than 15s gap → new segment
            highlights.append({
                "start": max(0, segment_start - 2),  # Pad start 2s
                "end": min(duration, prev_ts + 5),     # Pad end 5s
                "score": min(1.0, 1.0 / max(gap, 1)),
            })
            segment_start = ts
        prev_ts = ts

    # Final segment
    highlights.append({
        "start": max(0, segment_start - 2),
        "end": min(duration, prev_ts + 5),
        "score": 0.8,
    })

    # Filter by minimum duration and deduplicate overlapping
    result = []
    last_end = 0.0
    for h in highlights:
        if h["end"] - h["start"] >= min_duration:
            h["start"] = max(h["start"], last_end)
            if h["end"] > h["start"]:
                result.append(h)
                last_end = h["end"]

    return result


def _get_video_duration(video_path: str) -> float:
    """Get video duration in seconds using ffprobe."""
    import json as _json
    import re
    from lsc.config import load_config as _load_cfg2
    _cfg2 = _load_cfg2()
    _ffprobe = _cfg2.ffprobe_path or shutil.which("ffprobe") or "ffprobe"

    try:
        result = subprocess.run(
            [
                _ffprobe,
                "-v", "error",
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


def load_settings():
    """从 settings.json 加载应用设置，失败时返回默认值。"""
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as exc:
            _log.warning("加载设置文件失败，使用默认值: %s", exc)
    return {
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
    }


# 预览画质预设：分辨率 + NVENC 码率 + libx264 CRF/码率
_PREVIEW_QUALITY_PRESETS: dict[str, dict[str, Any]] = {
    '原画': {'width': 0, 'height': 0, 'nvenc_bitrate': '4000k', 'x264_crf': 23, 'x264_bitrate': '3000k'},
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
        with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
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

    return {
        'room_id': room.room_id,
        'room_url': room.room_url,
        'platform': room.platform,
        'platform_name': room.platform_name,
        'streamer_name': room.streamer_name,
        'stream_title': room.stream_title,
        'stream_url': stream_url,
        'is_connecting': room.is_connecting,
        'is_connected': room.is_connected,
        'is_recording': room.is_recording,
        'is_reconnecting': getattr(room, 'is_reconnecting', False),
        'record_output_path': room.record_output_path,
        'record_started_at': started_at,
        'record_size_mb': room.record_size_mb,
        'last_error': room.last_error,
        'preview_enabled': room.preview_enabled,
        'preview_paused': room.preview_paused,
        'preview_muted': room.preview_muted,
        'mark_in': room.mark_in,
        'mark_out': room.mark_out,
        'mark_in_wallclock': room.mark_in_wallclock,
        'mark_out_wallclock': room.mark_out_wallclock,
        'recording_start_mono': room.recording_start_mono,
        'preview_latency': room.preview_latency,
        'content_offset': getattr(room, 'content_offset', 0.0),
        'category': getattr(room, 'category', '') or '',
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


def register_room_handlers(server, bridge):
    manager: MultiRoomManager = bridge.manager

    # rooms_updated 广播节流：多房间同时活跃时，状态变更会频繁触发广播，
    # 每次都序列化全部房间为 JSON 并发送，前端 JSON.parse + 状态更新造成主线程压力。
    # 节流为最多 300ms 一次，合并多次变更为一次广播。
    _rooms_throttle = {'dirty': False, 'task': None}

    def _do_broadcast_rooms():
        msg = {
            'type': 'rooms_updated',
            'data': {'rooms': _rooms_list(manager)},
        }
        task = asyncio.create_task(server.broadcast(msg['type'], msg['data']))
        task.add_done_callback(lambda t: t.exception() if not t.cancelled() and t.exception() else None)

    def _broadcast_rooms():
        _rooms_throttle['dirty'] = True
        if _rooms_throttle['task'] is not None and not _rooms_throttle['task'].done():
            return
        async def _flush():
            await asyncio.sleep(0.3)
            if _rooms_throttle['dirty']:
                _rooms_throttle['dirty'] = False
                _rooms_throttle['task'] = None
                _do_broadcast_rooms()
            else:
                _rooms_throttle['task'] = None
        _rooms_throttle['task'] = asyncio.create_task(_flush())

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
            stats = collect_system_stats(output_dir)
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

        # 预检测 NVENC 可用性（在后台线程中执行，不阻塞连接流程）
        # 首次预览时无需再等待 NVENC 检测，减少 1-3 秒延迟
        def _precheck_nvenc():
            try:
                from lsc.core.services.mse_streamer import _check_nvenc
                _check_nvenc()
            except Exception:
                pass
        asyncio.get_running_loop().run_in_executor(None, _precheck_nvenc)

    @server.on('get_rooms')
    async def handle_get_rooms(data):
        """获取当前所有房间列表。"""
        _log.debug("获取房间列表: %d 个", len(manager.list_rooms()))
        return {'rooms': _rooms_list(manager)}

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
            return {'success': False, 'error': humanize_error(str(exc))}
        _broadcast_rooms()
        _log.info("连接房间完成: room_id=%s, success=%s", room_id, success)
        return {'success': bool(success)}

    @server.on('disconnect_room')
    async def handle_disconnect_room(data):
        """断开指定房间的连接。"""
        room_id = data.get('room_id')
        if not room_id:
            return {'error': 'room_id is required'}
        _log.info("断开房间连接: room_id=%s", room_id)

        # 清理 MSE streamer，防止僵尸 streamer 阻止后续预览启动
        with _mse_streamers_lock:
            stale_streamer = _mse_streamers.pop(room_id, None)
        if stale_streamer is not None:
            _log.info("清理断开房间的 MSE streamer: room_id=%s", room_id)
            def _stop_streamer():
                try:
                    stale_streamer.stop()
                except Exception as exc:
                    _log.debug("停止 streamer 失败 (disconnect): %s", exc)
            await asyncio.get_running_loop().run_in_executor(_bridge_executor, _stop_streamer)

        def _disconnect():
            manager.disconnect_room(room_id)
            return True

        try:
            await asyncio.get_running_loop().run_in_executor(_bridge_executor, lambda: bridge.call(_disconnect, timeout=30.0))
        except Exception as exc:
            _log.error("断开连接异常: room_id=%s, error=%s", room_id, exc)
            return {'success': False, 'error': humanize_error(str(exc))}
        _broadcast_rooms()
        _log.info("断开连接完成: room_id=%s", room_id)
        return {'success': True}

    @server.on('set_preview_muted')
    async def handle_set_preview_muted(data):
        """设置房间预览静音状态。"""
        room_id = data.get('room_id')
        muted = bool(data.get('muted', False))
        if not room_id:
            return {'error': 'room_id is required'}
        _log.debug("设置静音: room_id=%s, muted=%s", room_id, muted)

        def _set_muted():
            manager.set_preview_muted(room_id, muted)
            return True

        await asyncio.get_running_loop().run_in_executor(_bridge_executor, lambda: bridge.call(_set_muted))
        _broadcast_rooms()
        return {'success': True}

    @server.on('set_preview_quality')
    async def handle_set_preview_quality(data):
        """切换预览画质并立即生效。

        保存画质到 settings.json，如果该房间有活跃的 MSE streamer，
        则停止旧的 streamer 并用新画质重新启动。
        """
        room_id = data.get('room_id')
        quality = data.get('quality')
        if not room_id or not quality:
            return {'success': False, 'error': 'room_id and quality are required'}
        # 兼容英文值映射到中文预设名
        _quality_map = {
            'original': '原画', 'hd': '高清', 'sd': '标清', 'ld': '流畅',
        }
        quality = _quality_map.get(quality, quality)
        _log.info("切换预览画质: room_id=%s, quality=%s", room_id, quality)

        # 保存到 settings.json
        settings = load_settings()
        settings['preview_quality'] = quality
        save_settings(settings)

        # 检查是否有活跃的 MSE streamer
        with _mse_streamers_lock:
            existing = _mse_streamers.get(room_id)
        if existing is not None and existing.is_running:
            # 停止旧 streamer
            with _mse_streamers_lock:
                _mse_streamers.pop(room_id, None)
            def _stop_old():
                try:
                    existing.stop()
                except Exception as exc:
                    _log.debug("停止旧 streamer 失败 (quality change): %s", exc)
            await asyncio.get_running_loop().run_in_executor(_bridge_executor, _stop_old)

            # 用新画质重新启动
            return await _handle_mse_preview(server, manager, room_id, True, {'preview_quality': quality})

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
            return {'success': False, 'error': '该房间正在启动录制中，请稍候'}
        _recording_starting.add(room_id)
        try:
            # 并发限流：最多 2 路同时启动录制（Semaphore 在 asyncio 上下文 await，不占 executor 线程）
            _rec_log.info("[录制] acquiring semaphore for room %s", room_id)
            async with _recording_semaphore:
                _rec_log.info("[录制] semaphore acquired, submitting to executor for room %s", room_id)
                success = await asyncio.get_running_loop().run_in_executor(_recording_executor, _start)
            _rec_log.info("[录制] executor returned success=%s for room %s", success, room_id)
        except Exception as exc:
            _rec_log.error("[录制] exception for room %s: %s", room_id, exc, exc_info=True)
            _broadcast_rooms()
            return {'success': False, 'error': humanize_error(str(exc))}
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

        _broadcast_rooms()
        if not success:
            # 获取房间的具体错误信息，避免前端显示"未知错误"
            room = manager.get_room(room_id)
            error_msg = (room.last_error if room else None) or '录制启动失败，请检查房间状态'
            _rec_log.warning("[录制] failed for room %s, last_error=%s", room_id, error_msg)
            return {'success': False, 'error': humanize_error(error_msg)}
        return {'success': True}

    @server.on('stop_recording')
    async def handle_stop_recording(data):
        """停止录制指定房间。"""
        room_id = data.get('room_id')
        if not room_id:
            return {'error': 'room_id is required'}
        _log.info("停止录制: room_id=%s", room_id)

        def _stop():
            return manager.stop_recording(room_id)

        try:
            success = await asyncio.get_running_loop().run_in_executor(
                _bridge_executor, lambda: bridge.call(_stop, timeout=30.0)
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

        # S3: 清理 MSE streamer，防止 FFmpeg 进程泄漏
        with _mse_streamers_lock:
            stale_streamer = _mse_streamers.pop(room_id, None)
        if stale_streamer is not None:
            _log.info("清理移除房间的 MSE streamer: room_id=%s", room_id)
            def _stop_streamer():
                try:
                    stale_streamer.stop()
                except Exception as exc:
                    _log.debug("停止 streamer 失败 (remove): %s", exc)
            await asyncio.get_running_loop().run_in_executor(_bridge_executor, _stop_streamer)

        def _remove():
            return manager.remove_room(room_id)

        try:
            await asyncio.get_running_loop().run_in_executor(_bridge_executor, lambda: bridge.call(_remove, timeout=30.0))
        except Exception as exc:
            _log.error("移除房间异常: room_id=%s, error=%s", room_id, exc)
            return {'success': False, 'error': humanize_error(str(exc))}
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
                    except Exception:
                        pass
            return True

        success = await asyncio.get_running_loop().run_in_executor(_bridge_executor, lambda: bridge.call(_seek))
        return {'success': bool(success)}

    @server.on('set_mark_in')
    async def handle_set_mark_in(data):
        """设置入点（剪辑起始标记）。"""
        room_id = data.get('room_id')
        if not room_id:
            return {'error': 'room_id is required'}
        time_value = data.get('time')
        _log.debug("设置入点: room_id=%s, time=%s", room_id, time_value)

        def _mark():
            import time as _time
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
            room.mark_in_wallclock = _time.monotonic()
            return room.mark_in

        value = await asyncio.get_running_loop().run_in_executor(_bridge_executor, lambda: bridge.call(_mark))
        _broadcast_rooms()
        return {'success': True, 'mark_in': value}

    @server.on('set_mark_out')
    async def handle_set_mark_out(data):
        """设置出点（剪辑结束标记）。"""
        room_id = data.get('room_id')
        if not room_id:
            return {'error': 'room_id is required'}
        time_value = data.get('time')
        _log.debug("设置出点: room_id=%s, time=%s", room_id, time_value)

        def _mark():
            import time as _time
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
            room.mark_out_wallclock = _time.monotonic()
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
                    except Exception:
                        pass
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
                    except Exception:
                        pass
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
        stats = collect_system_stats(output_dir)
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
            from lsc.editor.audio_aligner import compute_offset
            import base64

            # 解码 PCM 数据
            audio_map: dict[str, np.ndarray] = {}
            for rd in rooms_data:
                room_id = rd.get('room_id', '')
                sample_rate = int(rd.get('sample_rate', 16000))
                pcm_b64 = rd.get('pcm_base64', '')
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

            # 互相关计算
            ref_id = valid_ids[0]
            ref_audio = audio_map[ref_id]
            raw_offsets: dict[str, float] = {ref_id: 0.0}
            scores: dict[str, float] = {ref_id: 1.0}

            _align_log.info("开始预览音频互相关: reference=%s, rooms=%s", ref_id, valid_ids[1:])
            for rid in valid_ids[1:]:
                offset, score = compute_offset(ref_audio, audio_map[rid], sample_rate)
                raw_offsets[rid] = float(offset)
                scores[rid] = float(score)
                _align_log.info("互相关: %s vs %s → offset=%.4fs (%.1fms), score=%.3f",
                                ref_id, rid, offset, offset * 1000, score)

            # 以最慢房间为基准（raw_offset 最小）
            slowest_id = min(valid_ids, key=lambda rid: raw_offsets[rid])
            slowest_offset = raw_offsets[slowest_id]
            offsets: dict[str, float] = {}
            for rid in valid_ids:
                offsets[rid] = float(max(0.0, raw_offsets[rid] - slowest_offset))

            _align_log.info(
                "预览音频对齐完成: reference=%s, offsets=%s, scores=%s",
                slowest_id,
                {k: f"{v:.4f}" for k, v in offsets.items()},
                {k: f"{v:.3f}" for k, v in scores.items()},
            )
            return {
                'success': True,
                'offsets': offsets,
                'reference_room_id': slowest_id,
                'method': 'preview_audio',
                'scores': scores,
            }
        except Exception as exc:
            _align_log.error("预览音频对齐失败: %s", exc, exc_info=True)
            return {'success': False, 'error': str(exc)}

    @server.on('check_dependencies')
    async def handle_check_dependencies(data):
        """检测系统依赖状态：FFmpeg / FFprobe / NVENC / Python"""
        import platform as _platform
        import subprocess
        from lsc.config import load_config as _load_config
        from lsc.utils.process_launcher import prepare_launch as _prepare_launch
        from lsc.core.services.mse_streamer import _check_nvenc

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
                if cwd: rkw["cwd"] = cwd
                if cflags: rkw["creationflags"] = cflags
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
                if cwd: rkw["cwd"] = cwd
                if cflags: rkw["creationflags"] = cflags
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
        """导出视频切片。

        使用 MSE currentTime 作为时间基准，减去 preview_latency 补偿预览延迟，
        再减去 content_offset 补偿多房间内容延迟（由音频互相关计算得出）。
        不使用墙钟时间差，避免 seek 标记时墙钟差与内容时长不一致导致导出时长错误。
        """
        room_id = data.get('room_id')
        start = _safe_float(data.get('start', 0))
        end = _safe_float(data.get('end', 0))
        label = data.get('label', 'clip')
        preset_id = data.get('preset_id', '')
        job_id = data.get('job_id', '')
        if not room_id:
            return {'error': 'room_id is required'}
        if start >= end:
            return {'error': '入点必须早于出点'}
        _log.info("导出切片: room_id=%s, start=%.2f, end=%.2f, label=%s, preset=%s, job_id=%s",
                  room_id, start, end, label, preset_id, job_id)

        loop = asyncio.get_running_loop()

        def _export():
            room = manager.get_room(room_id)
            if room is None:
                return False, '房间不存在'
            if not room.record_output_path:
                return False, '该房间没有录制文件（请先开始录制再导出切片）'
            if not os.path.isfile(room.record_output_path):
                return False, f'录制文件不存在: {room.record_output_path}'
            if room.mark_in is None or room.mark_out is None:
                return False, '请先设置入点和出点'

            # 使用墙钟差映射预览标记到录制文件位置：
            # mark_in_wallclock - recording_start_mono = 标记时刻在录制文件中的秒位置
            # 该方案绕过了 MSE 延迟和预览/录制启动时间差，精确映射两条独立时间轴。
            # content_offset 由音频互相关计算得出，补偿多房间内容偏移。
            content_offset = getattr(room, 'content_offset', 0.0)
            mark_in_wc = getattr(room, 'mark_in_wallclock', None)
            mark_out_wc = getattr(room, 'mark_out_wallclock', None)
            rec_start = getattr(room, 'recording_start_mono', None)

            if mark_in_wc is not None and mark_out_wc is not None and rec_start is not None:
                export_start = max(0.0, mark_in_wc - rec_start - content_offset)
                export_end = max(0.0, mark_out_wc - rec_start - content_offset)
            else:
                # 回退：墙钟不可用时用旧的固定延迟方案
                latency = getattr(room, 'preview_latency', 2.0)
                export_start = max(0.0, start - latency - content_offset)
                export_end = max(0.0, end - latency - content_offset)
            _log.info("导出时间映射: room_id=%s, wallclock_mode=%s, export_start=%.2f, export_end=%.2f, content_offset=%.4f",
                      room_id, mark_in_wc is not None and rec_start is not None,
                      export_start, export_end, content_offset)

            settings = load_settings()

            # Apply preset overrides if specified
            if preset_id:
                preset = _get_export_preset(preset_id)
                if preset:
                    encoder = preset.get('codec', settings.get('encoder', 'H.264 NVENC'))
                    crf_val = preset.get('crf', int(settings.get('crf', 23)))
                    resolution = preset.get('resolution', settings.get('resolution', ''))
                    framerate = preset.get('framerate', settings.get('framerate', '原画'))
                    audio_br = preset.get('audio_bitrate', settings.get('audio_bitrate', '128k'))
                    vertical_crop = preset.get('vertical_crop', False)
                else:
                    encoder = settings.get('encoder', 'H.264 NVENC')
                    crf_val = int(settings.get('crf', 23))
                    resolution = settings.get('resolution', '')
                    framerate = settings.get('framerate', '原画')
                    audio_br = settings.get('audio_bitrate', '128k')
                    vertical_crop = False
            else:
                encoder = settings.get('encoder', 'H.264 NVENC')
                crf_val = int(settings.get('crf', 23))
                resolution = settings.get('resolution', '')
                framerate = settings.get('framerate', '原画')
                audio_br = settings.get('audio_bitrate', '128k')
                vertical_crop = False
            codec_map = {
                'H.264 NVENC': 'h264_nvenc',
                'H.264 CPU': 'libx264',
                'H.265 NVENC': 'hevc_nvenc',
                'H.265 CPU': 'libx265',
                'Copy': 'copy',
                'h264_nvenc': 'h264_nvenc',
                'libx264': 'libx264',
                'hevc_nvenc': 'hevc_nvenc',
                'libx265': 'libx265',
                'copy': 'copy',
            }
            param_mode = settings.get('param_mode', 'CRF 质量')
            rate_mode_map = {
                'CRF 质量': 'crf',
                '码率限制': 'bitrate',
                '不限制': 'unrestricted',
            }
            bitrate = str(settings.get('bitrate', 8000))
            video_bitrate = f"{bitrate}k" if not bitrate.endswith(('k', 'M')) else bitrate

            # 分辨率格式归一化：将 "1920:1080" 转为 "1920x1080"
            if resolution and ":" in resolution:
                resolution = resolution.replace(":", "x")

            # 从设置中读取编码预设（默认 medium）
            preset = settings.get('preset', 'medium')
            profile = ExportProfile(
                codec=codec_map.get(encoder, 'libx264'),
                crf=crf_val,
                preset=preset,
                rate_mode=rate_mode_map.get(param_mode, 'crf'),
                video_bitrate=video_bitrate,
                audio_bitrate=audio_br,
                resolution=resolution,
                fps=_parse_fps(framerate),
                vertical_crop=vertical_crop,
            )

            output_dir = _expand_user_path(settings.get('output_dir', os.path.join(os.path.expanduser('~'), 'LSC', 'output')))
            room_name = room.streamer_name or room_id

            def on_done(success, output_path, error, size_mb, thumbnail_path):
                # 导出结束（成功/失败/取消）后清理 job 映射
                export_jobs.pop(job_id, None)
                if not success:
                    # 失败/取消时通知前端，避免队列永久 running
                    asyncio.run_coroutine_threadsafe(
                        server.broadcast('clip_failed', {
                            'room_id': room_id,
                            'job_id': job_id,
                            'error': error or '导出失败',
                        }),
                        loop,
                    )
                    return
                asyncio.run_coroutine_threadsafe(
                    server.broadcast('clip_completed', {
                        'room_id': room_id,
                        'start': export_start,
                        'end': export_end,
                        'label': label,
                        'room_name': room_name,
                        'thumbnail_path': thumbnail_path or '',
                        'output_path': output_path,
                        'job_id': job_id,
                    }),
                    loop,
                )

            def on_progress(percent, elapsed, total):
                asyncio.run_coroutine_threadsafe(
                    server.broadcast('export_progress', {
                        'room_id': room_id,
                        'job_id': job_id,
                        'percent': float(percent),
                        'elapsed': float(elapsed),
                        'total': float(total),
                    }),
                    loop,
                )

            clip_id = manager.start_export(
                room_id, export_start, export_end,
                output_dir=output_dir,
                title=label,
                profile=profile,
                on_done=on_done,
                on_progress=on_progress,
            )
            return clip_id, ''

        clip_id, error = await asyncio.get_running_loop().run_in_executor(_bridge_executor, lambda: bridge.call(_export))
        if clip_id and job_id:
            export_jobs[job_id] = clip_id
            _log.info("导出已提交: room_id=%s, job_id=%s, clip_id=%s", room_id, job_id, clip_id)
        else:
            _log.warning("导出失败: room_id=%s, error=%s", room_id, error)
        return {'success': bool(clip_id), 'error': error, 'job_id': job_id}

    @server.on('cancel_export')
    async def handle_cancel_export(data):
        """取消导出任务，终止对应的后端 FFmpeg 进程。"""
        job_id = data.get('job_id', '')
        if not job_id:
            return {'success': False, 'error': 'job_id is required'}
        clip_id = export_jobs.get(job_id)
        if not clip_id:
            _log.warning("取消导出: job_id=%s 未找到对应任务", job_id)
            return {'success': False, 'error': 'job not found'}
        _log.info("取消导出: job_id=%s, clip_id=%s", job_id, clip_id)

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
    async def _handle_mse_preview(srv, mgr, room_id: str, enabled: bool, data: dict | None = None) -> dict[str, Any]:
        """Handle MSE (Media Source Extensions) preview mode.

        Creates an MseStreamer that transcodes the live stream to fragmented MP4
        and pushes segments via WebSocket for browser-native <video> playback.
        """
        if enabled:
            # Check if already streaming / starting
            with _mse_streamers_lock:
                existing = _mse_streamers.get(room_id)
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
                if room_id in _mse_starting:
                    return {'success': False, 'room_id': room_id, 'error': 'MSE 正在启动中，请稍候'}
                _mse_starting.add(room_id)

            try:
                # 先在后台线程刷新流 URL，避免阻塞 Qt 主线程（B站等平台耗时 10+ 秒）
                refresh_ok = await asyncio.get_running_loop().run_in_executor(
                    _recording_executor, mgr.refresh_stream_url, room_id
                )
                if not refresh_ok:
                    # 刷新失败（流已下线），在 Qt 主线程更新连接状态
                    def _mark_disconnected():
                        room = mgr.get_room(room_id)
                        if room is not None:
                            room.is_connected = False
                            room.stream_info = None
                    await asyncio.get_running_loop().run_in_executor(
                        _bridge_executor, lambda: bridge.call(_mark_disconnected)
                    )

                # 在 Qt 主线程读取刷新后的房间状态
                def _read_snapshot():
                    room = mgr.get_room(room_id)
                    if room is None:
                        return None
                    return {
                        'is_connected': room.is_connected,
                        'stream_url': room.stream_info.stream_url if room.stream_info else '',
                        'platform': room.platform,
                        'headers': (room.stream_info.headers if room.stream_info else None) or {},
                    }

                snapshot = await asyncio.get_running_loop().run_in_executor(
                    _bridge_executor, lambda: bridge.call(_read_snapshot)
                )
                if snapshot is None:
                    return {'success': False, 'room_id': room_id, 'error': '房间不存在'}
                if not snapshot['is_connected'] or not snapshot['stream_url']:
                    return {'success': False, 'room_id': room_id, 'error': '房间未连接或无流信息（直播可能已结束）'}

                stream_url = snapshot['stream_url']
                # B站等平台首次刷新 URL 耗时较长，延长 FFmpeg 启动探测超时
                platform = snapshot.get('platform', '')
                probe_timeout = 8.0 if platform in ('bilibili', 'bilibili_bangumi') else 3.0

                # 读取预览画质预设（优先消息传入的 preview_quality，回退到全局设置）
                settings = load_settings()
                preview_quality = data.get('preview_quality') or settings.get('preview_quality', '高清')
                preset = _get_preview_quality_preset(preview_quality)
                width = preset['width']
                height = preset['height']

                # 动态降分辨率：活跃 MSE ≥6 路时强制限制分辨率 ≤ 854x480
                # S7: ≥8 路时进一步降级到 640x360，防止内存/CPU 过载
                with _mse_streamers_lock:
                    active_mse_count = sum(
                        1 for s in _mse_streamers.values() if s.is_running
                    )
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
                    with _mse_streamers_lock:
                        old_streamer = _mse_streamers.pop(room_id, None)
                    if old_streamer is not None:
                        try:
                            old_streamer.stop()
                        except Exception as exc:
                            _log.debug("停止旧 MSE streamer 失败: %s", exc)

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

                        # 8. 刷新流 URL（强制刷新，绕过缓存以避免拿到过期的流地址）
                        try:
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

                        with _mse_streamers_lock:
                            active_mse_count = sum(
                                1 for s in _mse_streamers.values() if s.is_running
                            )
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
                                    url=snapshot['stream_url'],
                                    width=r_width,
                                    height=r_height,
                                    headers=r_headers or None,
                                    video_bitrate=r_bitrate,
                                    crf_value=r_crf,
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
                                    on_error=lambda e: asyncio.run_coroutine_threadsafe(
                                        _on_mse_error(room_id, e, loop), loop
                                    ),
                                )
                                ok = streamer.start(startup_probe_timeout=r_probe)
                                if ok:
                                    with _mse_streamers_lock:
                                        _mse_streamers[room_id] = streamer
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
                            with _mse_streamers_lock:
                                _mse_streamers[room_id] = streamer
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
                    with _mse_streamers_lock:
                        leak_streamer = _mse_streamers.pop(room_id, None)
                    if leak_streamer is not None:
                        try:
                            leak_streamer.stop()
                        except Exception as exc:
                            _log.debug("停止泄漏 streamer 失败 (cleanup): %s", exc)
                    return {'success': False, 'room_id': room_id, 'error': f'预览状态同步失败：{exc}'}

                _mse_reconnect_state.pop(room_id, None)
                _broadcast_rooms()
                return {'success': True, 'note': 'mse streaming started'}
            finally:
                with _mse_streamers_lock:
                    _mse_starting.discard(room_id)

        else:
            # Stop MSE streaming
            with _mse_streamers_lock:
                streamer = _mse_streamers.pop(room_id, None)
            if streamer is not None:
                def _stop():
                    streamer.stop()
                await asyncio.get_running_loop().run_in_executor(_bridge_executor, _stop)

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
        with _mse_streamers_lock:
            streamer = _mse_streamers.get(room_id)
        if streamer is None:
            _log.debug("request_mse_init: room_id=%s 流未启动", room_id)
            return {'success': False, 'error': 'MSE 流未启动'}
        ok = streamer.replay_init()
        _log.debug("request_mse_init: room_id=%s, ok=%s", room_id, ok)
        return {'success': ok, 'room_id': room_id, 'note': 'init replayed' if ok else 'init not ready yet'}

    @server.on('start_analysis')
    async def handle_start_analysis(data):
        """启动场景分析，高光片段检测。"""
        room_id = data.get('room_id')
        threshold = _safe_float(data.get('threshold', 0.3), 0.3)
        if not room_id:
            return {'error': 'room_id is required'}
        _log.info("启动场景分析: room_id=%s, threshold=%.2f", room_id, threshold)

        def _do_analysis():
            room = manager.get_room(room_id)
            if room is None:
                return {'success': False, 'error': '房间不存在'}
            if not room.record_output_path or not os.path.isfile(room.record_output_path):
                return {'success': False, 'error': '录制文件不存在'}

            video_path = room.record_output_path
            _analysis_jobs[room_id] = {"progress": 0.0, "highlights": []}

            highlights = _run_scene_analysis(video_path, threshold=threshold)

            _analysis_jobs[room_id] = {
                "progress": 1.0,
                "highlights": highlights,
                "completed_at": time.time(),
            }
            _log.info("场景分析完成: room_id=%s, highlights=%d", room_id, len(highlights))
            return {'success': True, 'highlights': highlights}

        result = await asyncio.get_running_loop().run_in_executor(_bridge_executor, _do_analysis)
        return result

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
            return {'progress': 0, 'highlights': [], 'done': False}
        return {
            'progress': job.get('progress', 0),
            'highlights': job.get('highlights', []),
            'done': job.get('progress', 0) >= 1.0,
        }

    # 已保存的房间列表会在新客户端连接时由 on_connect 推送
