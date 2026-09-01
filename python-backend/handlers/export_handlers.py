"""导出相关 WebSocket handlers 与导出队列基础设施（从 room_handler 抽离）。

仅搬迁，不重构业务逻辑。依赖通过 register_export_handlers 参数注入，
保持与原 room_handler 内闭包实现完全一致的行为。

注册形态：room_handler.register_room_handlers 内部调用
    register_export_handlers(server, bridge=bridge, manager=manager, ...)

外部导入兼容：
    from handlers.export_handlers import ensure_export_queue, queue_export_ref
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from typing import Any

from lsc.config import ExportProfile
from lsc.core.services.mse_streamer import _check_nvenc
from lsc.core.services.resource_monitor import get_resource_pressure
from lsc.utils.error_messages import humanize_error

_log = logging.getLogger('lsc.handlers')

# ── 导出任务状态（模块级，跨 register 调用持久） ──────────────────────
export_jobs: dict[str, str] = {}  # job_id -> clip_id
_export_jobs_lock = threading.Lock()
_export_job_states: dict[str, dict[str, Any]] = {}
_MAX_EXPORT_JOB_STATES = 512
_export_cancelled_jobs: set[str] = set()

# 全局导出队列
_export_queue: asyncio.Queue | None = None
_EXPORT_WORKERS: list[asyncio.Task] = []
_MAX_EXPORT_WORKERS = 4
_export_semaphore = asyncio.Semaphore(2)
_export_semaphore_limit = 2
_export_in_flight = 0
_export_queue_lock = asyncio.Lock()
_export_stats_lock = threading.Lock()

# 批量导出进度
_export_total = 0
_export_completed = 0
_export_batch_id = ""

# 延后导出队列（持续分析先入列，压力缓解后再导出）
_deferred_export_jobs: list[dict[str, Any]] = []


def _set_export_job_state(job_id: str, status: str, **fields: Any) -> None:
    """记录可查询的导出状态，补偿 WebSocket 单次终态广播丢失。"""
    if not job_id:
        return
    with _export_jobs_lock:
        current = dict(_export_job_states.get(job_id) or {})
        current.update(fields)
        current.update({
            'job_id': job_id,
            'status': status,
            'updated_at': time.time(),
        })
        _export_job_states[job_id] = current
        if len(_export_job_states) > _MAX_EXPORT_JOB_STATES:
            stale = sorted(
                _export_job_states,
                key=lambda key: float(_export_job_states[key].get('updated_at', 0.0)),
            )[:len(_export_job_states) - _MAX_EXPORT_JOB_STATES]
            for key in stale:
                _export_job_states.pop(key, None)


def _get_export_job_states(job_ids: list[str]) -> list[dict[str, Any]]:
    with _export_jobs_lock:
        return [
            dict(_export_job_states[job_id])
            for job_id in job_ids
            if job_id in _export_job_states
        ]


def _notify_export_overall(bridge, success: bool = True) -> None:
    """广播批量导出总体进度。"""
    global _export_completed
    with _export_stats_lock:
        _export_completed += 1
        total = _export_total
        completed = _export_completed
    if total > 0:
        bridge.queue_broadcast({
            'type': 'export_overall_progress',
            'data': {
                'total': total,
                'completed': completed,
                'percent': (completed / total * 100),
                'batch_id': _export_batch_id,
            },
        })


def _reset_export_batch(total: int, batch_id: str) -> None:
    """重置批量导出计数器。"""
    global _export_total, _export_completed, _export_batch_id
    with _export_stats_lock:
        _export_total = total
        _export_completed = 0

# §8.2 降级补偿：无墙钟快照时用固定 preview_latency 近似预览流相对录制流的延迟
_PREVIEW_LATENCY_FALLBACK = 2.0


def _resolve_export_range(
    start_sec,
    end_sec,
    *,
    source='',
    content_offset=0.0,
    snap_in=None,
    snap_out=None,
    snap_rec=None,
    use_room_marks=False,
    room_mark_in=None,
    room_mark_out=None,
    room_rec_start=None,
):
    """解析导出入/出点与精度。"""
    content_offset = float(content_offset or 0.0)
    start_sec = float(start_sec)
    end_sec = float(end_sec)

    if source == 'ai_highlight':
        return max(0.0, start_sec), max(0.0, end_sec), 'exact'

    if snap_in is not None and snap_out is not None and snap_rec is not None:
        return (
            max(0.0, float(snap_in) - float(snap_rec) - content_offset),
            max(0.0, float(snap_out) - float(snap_rec) - content_offset),
            'exact',
        )

    if use_room_marks:
        if (
            room_mark_in is not None
            and room_mark_out is not None
            and room_rec_start is not None
        ):
            return (
                max(0.0, float(room_mark_in) - float(room_rec_start) - content_offset),
                max(0.0, float(room_mark_out) - float(room_rec_start) - content_offset),
                'exact',
            )
        return (
            max(0.0, start_sec - content_offset - _PREVIEW_LATENCY_FALLBACK),
            max(0.0, end_sec - content_offset - _PREVIEW_LATENCY_FALLBACK),
            'approximate',
        )

    # A partial wall-clock snapshot still anchors the request to the current
    # recording timeline; do not apply the generic preview-latency fallback.
    if snap_in is not None or snap_out is not None or snap_rec is not None:
        return (
            max(0.0, start_sec - content_offset),
            max(0.0, end_sec - content_offset),
            'approximate',
        )

    return (
        max(0.0, start_sec - content_offset - _PREVIEW_LATENCY_FALLBACK),
        max(0.0, end_sec - content_offset - _PREVIEW_LATENCY_FALLBACK),
        'approximate',
    )


# ── 模块级引用（由 register 设置，供 ensure_export_queue 外部调用） ──
_queue_export_fn = None


async def ensure_export_queue():
    """模块级入口：确保导出队列已初始化（兼容 server.py 的 import）。"""
    if _queue_export_fn is not None:
        # 触发一次 _ensure 以初始化 worker
        await _ensure_export_queue_impl()
    # 如果尚未 register，静默跳过


async def _ensure_export_queue_impl():
    """内部实现（需要 register 已设置 _ctx）。"""
    global _export_queue, _EXPORT_WORKERS, _export_semaphore, _export_semaphore_limit
    if _ctx is None:
        return
    async with _export_queue_lock:
        if _export_queue is None:
            _export_queue = asyncio.Queue(maxsize=100)
        desired = _get_export_max_concurrent()
        if _export_semaphore_limit != desired:
            if _export_queue.empty() and _export_in_flight == 0:
                _export_semaphore = asyncio.Semaphore(desired)
                _export_semaphore_limit = desired
                _log.info("导出并发上限已更新: %d", desired)
            else:
                _log.warning(
                    "导出并发上限变更(%d->%d)延迟生效：队列非空或有在途任务(in_flight=%d)",
                    _export_semaphore_limit, desired, _export_in_flight,
                )
        _EXPORT_WORKERS[:] = [t for t in _EXPORT_WORKERS if not t.done()]
        _purge_stale_fn = _ctx.get('purge_stale_analysis_jobs')
        if callable(_purge_stale_fn):
            _purge_stale_fn()
        while len(_EXPORT_WORKERS) < _MAX_EXPORT_WORKERS:
            _EXPORT_WORKERS.append(asyncio.create_task(_export_queue_worker_impl()))
        if len(_EXPORT_WORKERS) == _MAX_EXPORT_WORKERS:
            _log.debug("导出队列 worker 池已就绪: %d 个 worker, 并发=%d",
                       len(_EXPORT_WORKERS), desired)


# register 上下文（保存 server/bridge/manager 等引用供 worker 使用）
_ctx: dict[str, Any] | None = None


def _get_export_max_concurrent() -> int:
    """从 settings 读取 export_max_concurrent，默认 2，合法值仅 1 或 2。"""
    if _ctx is None:
        return 2
    try:
        load_settings = _ctx['load_settings']
        val = int(load_settings().get('export_max_concurrent', 2))
        if val not in (1, 2):
            return 2
        return val
    except (TypeError, ValueError):
        return 2


async def _export_queue_worker_impl():
    """常驻 worker 消费循环。"""
    global _export_in_flight
    if _export_queue is None:  # 由 _ensure_export_queue 保证；显式守卫替代 assert
        _log.error("export queue worker aborted: _export_queue is None")
        return
    while True:
        job = await _export_queue.get()
        job_id = job.get('job_id', '')
        if job_id and job_id in _export_cancelled_jobs:
            _export_cancelled_jobs.discard(job_id)
            _export_queue.task_done()
            room_id = job.get('room_id', '')
            _set_export_job_state(job_id, 'cancelled', room_id=room_id, error='导出已取消')
            server = _ctx['server']  # type: ignore[index]
            asyncio.run_coroutine_threadsafe(server.broadcast('clip_failed', {
                'room_id': room_id, 'job_id': job_id, 'error': '导出已取消',
            }), asyncio.get_running_loop())
            continue
        try:
            async with _export_semaphore:
                with _export_stats_lock:
                    _export_in_flight += 1
                try:
                    await _process_export_job_impl(job)
                finally:
                    with _export_stats_lock:
                        _export_in_flight -= 1
        except Exception as exc:
            _log.error("导出队列异常：%s", exc, exc_info=True)
            if job_id:
                with _export_jobs_lock:
                    export_jobs.pop(job_id, None)
                room_id = job.get('room_id', '')
                _set_export_job_state(job_id, 'failed', room_id=room_id, error=f'导出异常：{exc}')
                server = _ctx['server']  # type: ignore[index]
                asyncio.run_coroutine_threadsafe(server.broadcast('clip_failed', {
                    'room_id': room_id, 'job_id': job_id, 'error': f'导出异常：{exc}',
                }), asyncio.get_running_loop())
        finally:
            _export_queue.task_done()


async def _process_export_job_impl(job):
    """处理单个导出任务。"""
    server = _ctx['server']  # type: ignore[index]
    bridge = _ctx['bridge']  # type: ignore[index]
    manager = _ctx['manager']  # type: ignore[index]
    bridge_executor = _ctx['bridge_executor']  # type: ignore[index]

    room_id = job['room_id']
    export_start = job['start']
    export_end = job['end']
    label = job['label']
    output_dir = job['output_dir']
    profile = job['profile']
    job_id = job['job_id']
    _set_export_job_state(
        job_id,
        'exporting',
        room_id=room_id,
        label=label,
        percent=0.0,
        export_start=export_start,
        export_end=export_end,
        precision=job.get('precision', 'exact'),
    )

    await server.broadcast('clip_export_started', {'room_id': room_id, 'job_id': job_id})

    loop = asyncio.get_running_loop()
    done_event = asyncio.Event()
    result = {'success': False, 'clip_id': '', 'error': ''}

    def on_done(success, output_path, error, size_mb, thumbnail_path):
        with _export_jobs_lock:
            export_jobs.pop(job_id, None)
        if success:
            _set_export_job_state(
                job_id, 'completed', room_id=room_id, label=label,
                percent=100.0, output_path=output_path,
                thumbnail_path=thumbnail_path or '', size_mb=float(size_mb or 0.0), error='',
                export_start=export_start,
                export_end=export_end,
                precision=job.get('precision', 'exact'),
            )
            asyncio.run_coroutine_threadsafe(server.broadcast('clip_completed', {
                'room_id': room_id, 'start': export_start, 'end': export_end,
                'export_start': export_start, 'export_end': export_end,
                'precision': job.get('precision', 'exact'),
                'label': label, 'room_name': job.get('room_name', ''),
                'thumbnail_path': thumbnail_path or '', 'output_path': output_path,
                'job_id': job_id,
            }), loop)
            _notify_export_overall(bridge, success=True)
        else:
            _set_export_job_state(job_id, 'failed', room_id=room_id, label=label, error=error or '导出失败')
            asyncio.run_coroutine_threadsafe(server.broadcast('clip_failed', {
                'room_id': room_id, 'job_id': job_id, 'error': error or '导出失败',
            }), loop)
            _notify_export_overall(bridge, success=False)
        loop.call_soon_threadsafe(done_event.set)

    def on_progress(percent, elapsed, total):
        _set_export_job_state(
            job_id, 'exporting', room_id=room_id, label=label,
            percent=float(percent), elapsed=float(elapsed), total=float(total),
        )
        asyncio.run_coroutine_threadsafe(server.broadcast('export_progress', {
            'room_id': room_id, 'job_id': job_id,
            'percent': float(percent), 'elapsed': float(elapsed), 'total': float(total),
        }), loop)

    def _run_export():
        try:
            clip_id = manager.start_export(
                room_id, export_start, export_end,
                output_dir=output_dir, title=label,
                profile=profile, on_done=on_done, on_progress=on_progress,
            )
            result['clip_id'] = clip_id or ''
            if not clip_id:
                room = manager.get_room(room_id)
                controller = None if room is None else room.controller
                result['error'] = getattr(controller, '_last_export_error', '') or '导出启动失败'
                loop.call_soon_threadsafe(done_event.set)
            else:
                result['success'] = True
                with _export_jobs_lock:
                    state = _export_job_states.get(job_id) or {}
                    if state.get('status') not in {'completed', 'failed', 'cancelled'}:
                        export_jobs[job_id] = clip_id
        except Exception as exc:
            result['error'] = str(exc)
            loop.call_soon_threadsafe(done_event.set)

    await loop.run_in_executor(bridge_executor, lambda: bridge.manager.call(_run_export))

    if result['error']:
        _set_export_job_state(job_id, 'failed', room_id=room_id, label=label, error=result['error'] or '导出启动失败')
        _log.error("导出任务失败: room=%s, job=%s, error=%s", room_id, job_id, result['error'])
        await server.broadcast('clip_failed', {
            'room_id': room_id, 'job_id': job_id, 'error': result['error'] or '导出启动失败',
        })

    # 兜底超时：on_done 理论上必被调用（ExportWorker.run 已保证 finally 语义），
    # 但任何意外路径都不得让 worker 在 semaphore 槽位内永久挂起——
    # 2 次卡死即占满全部导出槽位，其余导出全部永久失败。
    try:
        await asyncio.wait_for(done_event.wait(), timeout=6 * 3600)
    except asyncio.TimeoutError:
        _log.error(
            "导出任务超时放弃: room=%s, job=%s（6h 无完成回调）",
            room_id, job_id,
        )
        with _export_jobs_lock:
            export_jobs.pop(job_id, None)
        _set_export_job_state(job_id, 'failed', room_id=room_id, label=label, error='导出超时（6 小时无完成回调）')
        await server.broadcast('clip_failed', {
            'room_id': room_id, 'job_id': job_id, 'error': '导出超时（6 小时无完成回调）',
        })


def _get_export_preset(preset_id: str, load_settings) -> dict[str, Any] | None:
    """Get export preset configuration by ID."""
    try:
        custom_presets = load_settings().get('appSettings', {}).get('custom_export_presets', [])
        if isinstance(custom_presets, list):
            for cp in custom_presets:
                if isinstance(cp, dict) and cp.get('id') == preset_id:
                    return {
                        'codec': cp.get('codec', 'h264_nvenc'),
                        'crf': cp.get('crf', 23),
                        'resolution': cp.get('resolution', ''),
                        'framerate': cp.get('framerate', '原画'),
                        'audio_bitrate': cp.get('audio_bitrate', '128k'),
                        'vertical_crop': bool(cp.get('vertical_crop', False)),
                    }
    except Exception as exc:
        _log.warning("读取自定义导出预设失败，回退内置预设: %s", exc)

    presets = {
        'douyin_vertical': {
            'codec': 'h264_nvenc', 'crf': 23, 'resolution': '1080:1920',
            'framerate': '30', 'audio_bitrate': '128k', 'vertical_crop': True,
        },
        'bilibili_horizontal': {
            'codec': 'h264_nvenc', 'crf': 23, 'resolution': '1920:1080',
            'framerate': '30', 'audio_bitrate': '128k', 'vertical_crop': False,
        },
        'original': {
            'codec': 'copy', 'crf': 0, 'resolution': '',
            'framerate': '原画', 'audio_bitrate': '128k', 'vertical_crop': False,
        },
        'high_quality': {
            'codec': 'h264_nvenc', 'crf': 18, 'resolution': '',
            'framerate': '60', 'audio_bitrate': '256k', 'vertical_crop': False,
        },
        'small_file': {
            'codec': 'hevc_nvenc', 'crf': 28, 'resolution': '1280:720',
            'framerate': '24', 'audio_bitrate': '96k', 'vertical_crop': False,
        },
    }
    return presets.get(preset_id)


def _parse_fps(framerate: str) -> float:
    if not framerate or framerate == '原画':
        return 0.0
    try:
        return float(framerate)
    except (TypeError, ValueError):
        return 0.0


def register_export_handlers(
    server,
    *,
    bridge,
    manager,
    bridge_executor,
    load_settings,
    expand_user_path,
    purge_stale_analysis_jobs=None,
    # 共享状态（由 room_handler 传入，避免状态分裂）
    ext_export_jobs: dict | None = None,
    ext_export_jobs_lock=None,
    ext_export_cancelled_jobs: set | None = None,
) -> None:
    """注册导出相关 handlers 并初始化导出队列基础设施。

    Args:
        server: WebSocket server。
        bridge: 跨线程消息桥。
        manager: RoomOrchestrator。
        bridge_executor: 快操作线程池。
        load_settings: 加载设置函数。
        expand_user_path: 路径展开函数。
        purge_stale_analysis_jobs: 清理过期分析任务的回调。
        ext_export_jobs: 外部 export_jobs dict（room_handler 持有）。
        ext_export_jobs_lock: 外部 export_jobs 锁。
        ext_export_cancelled_jobs: 外部已取消 job 集合。
    """
    global _ctx, _queue_export_fn

    # 如果传入了外部状态，替换模块级引用
    if ext_export_jobs is not None:
        globals()['export_jobs'] = ext_export_jobs
    if ext_export_jobs_lock is not None:
        globals()['_export_jobs_lock'] = ext_export_jobs_lock
    if ext_export_cancelled_jobs is not None:
        globals()['_export_cancelled_jobs'] = ext_export_cancelled_jobs

    _ctx = {
        'server': server,
        'bridge': bridge,
        'manager': manager,
        'bridge_executor': bridge_executor,
        'load_settings': load_settings,
        'expand_user_path': expand_user_path,
        'purge_stale_analysis_jobs': purge_stale_analysis_jobs,
    }

    def _build_export_profile(settings, preset_id=None):
        """全系统唯一的 ExportProfile 构建入口。"""
        encoder = settings.get('encoder', 'h264_nvenc')
        crf_val = int(settings.get('crf', 23))
        resolution = settings.get('resolution', '')
        framerate = settings.get('framerate', '原画')
        audio_br = settings.get('audio_bitrate', '128k')
        vertical_crop = False

        if preset_id:
            preset = _get_export_preset(preset_id, load_settings)
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

        codec = codec_map.get(encoder, 'libx264')
        if codec in {'h264_nvenc', 'hevc_nvenc'} and not _check_nvenc():
            fallback_codec = 'libx265' if codec == 'hevc_nvenc' else 'libx264'
            _log.warning("导出 NVENC 不可用，已自动回退到 %s", fallback_codec)
            codec = fallback_codec

        return ExportProfile(
            codec=codec,
            crf=crf_val, preset=enc_preset,
            rate_mode=rate_mode_map.get(settings.get('param_mode', 'CRF 质量'), 'crf'),
            video_bitrate=video_bitrate, audio_bitrate=audio_br,
            resolution=resolution, fps=_parse_fps(framerate),
            vertical_crop=vertical_crop,
        )

    async def queue_export(room_id, start_sec, end_sec, label='clip', preset_id='',
                          source='', job_id='',
                          mark_in_wallclock=None, mark_out_wallclock=None,
                          recording_start_mono=None, recording_media_start_mono=None,
                          use_room_marks=False, content_offset=None, pre_mapped=False):
        """统一导出入口：校验参数、计算时间映射、构建 profile、入队。

        pre_mapped=True 时 start/end 已是录制文件时间轴最终值（调用方已按
        墙钟映射公式完成换算），跳过 _resolve_export_range，避免二次扣除 content_offset。
        """
        if not room_id:
            return {'error': 'room_id is required'}
        if start_sec >= end_sec:
            return {'error': '入点必须早于出点'}

        await _ensure_export_queue_impl()

        room = manager.get_room(room_id)
        if room is None:
            return {'error': '房间不存在'}

        room_name = getattr(room, 'streamer_name', '') or room_id
        if content_offset is not None:
            try:
                content_offset = float(content_offset)
            except (TypeError, ValueError):
                content_offset = float(getattr(room, 'content_offset', 0.0) or 0.0)
        else:
            content_offset = float(getattr(room, 'content_offset', 0.0) or 0.0)

        snap_in = mark_in_wallclock
        snap_out = mark_out_wallclock
        snap_rec = recording_media_start_mono if recording_media_start_mono is not None else recording_start_mono
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

        room_mark_in = getattr(room, 'mark_in_wallclock', None)
        room_mark_out = getattr(room, 'mark_out_wallclock', None)
        room_rec_start = (
            getattr(room, 'recording_media_start_mono', None)
            or getattr(room, 'recording_start_mono', None)
        )

        if pre_mapped:
            export_start, export_end, precision = max(0.0, float(start_sec)), max(0.0, float(end_sec)), 'exact'
        else:
            export_start, export_end, precision = _resolve_export_range(
                start_sec, end_sec,
                source=source, content_offset=content_offset,
                snap_in=snap_in, snap_out=snap_out, snap_rec=snap_rec,
                use_room_marks=use_room_marks,
                room_mark_in=room_mark_in, room_mark_out=room_mark_out,
                room_rec_start=room_rec_start,
            )

        if precision == 'approximate':
            if use_room_marks:
                _log.warning(
                    "导出降级：use_room_marks 但墙钟不可用 (room=%s, start=%.2f, end=%.2f)",
                    room_id, export_start, export_end,
                )
            else:
                _log.warning(
                    "导出降级：无墙钟快照，使用 start/end (room=%s, start=%.2f, end=%.2f)",
                    room_id, export_start, export_end,
                )

        if export_start >= export_end:
            return {'error': '导出时间范围无效（入点>=出点）'}

        settings = load_settings()
        profile = _build_export_profile(settings, preset_id)
        output_dir = expand_user_path(
            settings.get('output_dir', os.path.join(os.path.expanduser('~'), 'LSC', 'output'))
        )

        if not job_id:
            job_id = f"q-{int(time.time() * 1000)}-{room_id[:6]}"

        job = {
            'room_id': room_id, 'start': export_start, 'end': export_end,
            'label': label, 'output_dir': output_dir, 'profile': profile,
            'job_id': job_id, 'room_name': room_name, 'precision': precision,
        }
        try:
            _export_queue.put_nowait(job)  # type: ignore[union-attr]
        except asyncio.QueueFull:
            _log.warning("导出队列已满: room=%s, job=%s, qsize=%d",
                         room_id, job_id, _export_queue.qsize())  # type: ignore[union-attr]
            return {'success': False, 'error': '导出队列已满，请稍后重试'}
        _set_export_job_state(
            job_id,
            'queued',
            room_id=room_id,
            label=label,
            percent=0.0,
            export_start=export_start,
            export_end=export_end,
            precision=precision,
        )
        _log.debug("导出已入队: room=%s, job=%s, %.1f-%.1f, precision=%s, queue_size=%d",
                   room_id, job_id, export_start, export_end, precision, _export_queue.qsize())  # type: ignore[union-attr]
        return {
            'success': True,
            'queued': True,
            'job_id': job_id,
            'precision': precision,
            'export_start': export_start,
            'export_end': export_end,
        }

    # 暴露 queue_export 供外部模块使用
    _queue_export_fn = queue_export

    async def flush_deferred_exports(force: bool = False) -> int:
        """压力缓解或收尾时，把延后队列真正送进导出 worker。

        ⚠️ 死代码：消费的是本模块的 ``_deferred_export_jobs``（无人 append），
        实际延后列表在 room_handler 内（由持续分析写入，经其 _flush_deferred_exports
        消费）。保留仅为兼容旧调用方；若需统一延后导出请接线 room_handler 的列表。
        """
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

    # 将 flush_deferred_exports 挂到模块级供 analysis_handlers 使用
    global _flush_deferred_exports_fn
    _flush_deferred_exports_fn = flush_deferred_exports

    # ── WebSocket handlers ──────────────────────────────────────────

    @server.on('export_clip')
    async def handle_export_clip(data):
        """导出视频切片 — 统一入队到全局导出队列。"""
        room_id = data.get('room_id')
        start_sec = _safe_float(data.get('start', 0))
        end_sec = _safe_float(data.get('end', 0))
        label = data.get('label', 'clip')
        preset_id = data.get('preset_id', '')
        job_id = data.get('job_id', '')
        operation_id = data.get('operation_id', '')
        source = data.get('source', '')

        mark_in_wallclock = data.get('mark_in_wallclock')
        mark_out_wallclock = data.get('mark_out_wallclock')
        recording_start_mono = data.get('recording_start_mono')
        recording_media_start_mono = data.get('recording_media_start_mono')
        use_room_marks = bool(data.get('use_room_marks', False))
        content_offset = data.get('content_offset', None)

        _log.info("导出切片: room_id=%s, start=%.2f, end=%.2f, label=%s, preset=%s, job_id=%s, operation_id=%s",
                  room_id, start_sec, end_sec, label, preset_id, job_id, operation_id)

        result = await queue_export(
            room_id, start_sec, end_sec, label, preset_id, source, job_id,
            mark_in_wallclock=mark_in_wallclock,
            mark_out_wallclock=mark_out_wallclock,
            recording_start_mono=recording_start_mono,
            recording_media_start_mono=recording_media_start_mono,
            use_room_marks=use_room_marks,
            content_offset=content_offset,
        )

        if result.get('error'):
            return {'success': False, 'error': result['error'], 'operation_id': operation_id}
        return {
            'success': True,
            'job_id': result['job_id'],
            'operation_id': operation_id,
            'queued': True,
            'precision': result.get('precision'),
            'export_start': result.get('export_start'),
            'export_end': result.get('export_end'),
        }

    @server.on('cancel_export')
    async def handle_cancel_export(data):
        """取消导出任务 — 支持取消排队中和进行中的任务。"""
        job_id = data.get('job_id', '')
        if not job_id:
            return {'success': False, 'error': 'job_id is required'}

        with _export_jobs_lock:
            clip_id = export_jobs.get(job_id)
        if clip_id:
            _log.info("取消导出(执行中): job_id=%s, clip_id=%s", job_id, clip_id)
            def _cancel():
                return manager.cancel_export(clip_id)
            try:
                cancelled = await asyncio.get_running_loop().run_in_executor(
                    bridge_executor, lambda: bridge.manager.call(_cancel)
                )
            except Exception as exc:
                _log.error("取消导出异常: job_id=%s, error=%s", job_id, exc)
                return {'success': False, 'error': humanize_error(str(exc))}
            if cancelled:
                with _export_jobs_lock:
                    export_jobs.pop(job_id, None)
                _set_export_job_state(job_id, 'cancelled', error='导出已取消')
                _log.info("导出已取消: job_id=%s", job_id)
                return {'success': True}
            return {'success': False, 'error': 'job not found'}

        _export_cancelled_jobs.add(job_id)
        _set_export_job_state(job_id, 'cancelled', error='导出已取消')
        _log.info("取消导出(排队中): job_id=%s", job_id)
        return {'success': True, 'note': 'queued job marked as cancelled'}

    @server.on('get_export_job_status')
    async def handle_get_export_job_status(data):
        """返回导出任务快照，供前端补偿丢失的进度/完成广播。"""
        raw_ids = (data or {}).get('job_ids') or []
        if not isinstance(raw_ids, list):
            return {'success': False, 'error': 'job_ids must be a list', 'jobs': []}
        job_ids = [str(job_id) for job_id in raw_ids[:100] if str(job_id)]
        return {'success': True, 'jobs': _get_export_job_states(job_ids)}


def _safe_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


# 模块级引用：flush_deferred_exports（由 register 设置）
_flush_deferred_exports_fn = None


def get_queue_export():
    """获取 queue_export 函数引用（供 timeline_handlers / analysis_handlers 使用）。"""
    return _queue_export_fn


def get_flush_deferred_exports():
    """获取 flush_deferred_exports 函数引用（供 analysis_handlers 使用）。"""
    return _flush_deferred_exports_fn
