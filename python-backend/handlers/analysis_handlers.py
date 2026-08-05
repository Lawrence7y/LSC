"""分析相关 WebSocket handlers（从 room_handler 抽离）。

仅搬迁，不重构业务逻辑。依赖通过 register_analysis_handlers 参数注入，
保持与原 room_handler 内闭包实现完全一致的行为。

注册形态：room_handler.register_room_handlers 内部调用
    register_analysis_handlers(server, bridge=bridge, manager=manager, ...)
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from typing import Any
from uuid import uuid4

from persistence import (
    is_analysis_stale,
    load_analysis_results,
    save_analysis_results,
)

_log = logging.getLogger('lsc.handlers')

# ── 分析任务状态（模块级） ──────────────────────────────────────────
_analysis_jobs: dict[str, dict[str, Any]] = {}
_analysis_jobs_lock = threading.RLock()
_ANALYSIS_JOB_TTL = 300.0

# 持续分析任务状态
_continuous_tasks: dict[str, dict[str, Any]] = {}

# 切片精修状态
_clip_refine_state: dict[str, dict[str, Any]] = {}
_exported_clip_ids: dict[str, None] = {}
_listed_clip_ids: dict[str, None] = {}
_listed_clip_bounds: dict[str, tuple[float, float, str]] = {}
_refined_round_keys: set[str] = set()
_refined_round_keys_lock = threading.Lock()

_CLIP_KEY_CACHE_MAX = 20000


def _bounded_clip_key_add(cache: dict, key: str, value: Any = None) -> None:
    """向保序 dict 缓存写入 key；超上限时裁掉最旧一半。"""
    if len(cache) >= _CLIP_KEY_CACHE_MAX:
        for _old_key in list(cache)[: _CLIP_KEY_CACHE_MAX // 2]:
            cache.pop(_old_key, None)
    cache[key] = value


def purge_stale_analysis_jobs() -> None:
    """TTL-based purge of completed analysis jobs."""
    now = time.time()
    with _analysis_jobs_lock:
        stale = [rid for rid, job in list(_analysis_jobs.items())
                 if job.get('completed_at') and now - job['completed_at'] > _ANALYSIS_JOB_TTL]
        for rid in stale:
            _analysis_jobs.pop(rid, None)
    if stale:
        _log.debug("purged %d stale analysis jobs", len(stale))


def register_analysis_handlers(
    server,
    *,
    bridge,
    manager,
    bridge_executor,
    ai_executor,
    load_settings,
    safe_float,
    analyze_scene_or_rounds,
    validate_synced_analysis_targets,
    continuous_analysis_loop,
    auto_export_highlights,
    build_continuous_status_payload,
    map_highlight_to_room,
    recording_media_start,
    min_highlight_duration_for_queue,
    valorant_round_key,
    should_broadcast_clip_list_update,
    # 共享状态（由 room_handler 传入，避免循环导入）
    analysis_jobs: dict | None = None,
    analysis_jobs_lock=None,
    continuous_tasks: dict | None = None,
    refined_round_keys: set | None = None,
    refined_round_keys_lock=None,
) -> None:
    """注册分析相关 handlers。

    Args:
        server: WebSocket server。
        bridge: 跨线程消息桥。
        manager: RoomOrchestrator。
        bridge_executor: 快操作线程池。
        ai_executor: AI 分析线程池。
        load_settings: 加载设置函数。
        safe_float: 安全浮点转换。
        analyze_scene_or_rounds: 场景/回合分析函数。
        validate_synced_analysis_targets: 校验同步分析目标。
        continuous_analysis_loop: 持续分析循环协程。
        auto_export_highlights: 高光自动入列/导出协程。
        build_continuous_status_payload: 构建持续分析状态载荷。
        map_highlight_to_room: 高光映射到目标房间。
        recording_media_start: 获取录制媒体起点。
        min_highlight_duration_for_queue: 入列最小时长。
        valorant_round_key: 生成回合 key。
        should_broadcast_clip_list_update: 判断是否广播切片列表更新。
    """

    # 使用外部传入的共享状态（room_handler 持有），回退到模块级默认值
    if analysis_jobs is not None:
        _analysis_jobs = analysis_jobs
    if analysis_jobs_lock is not None:
        _analysis_jobs_lock = analysis_jobs_lock
    if continuous_tasks is not None:
        _continuous_tasks = continuous_tasks
    if refined_round_keys is not None:
        _refined_round_keys = refined_round_keys
    if refined_round_keys_lock is not None:
        _refined_round_keys_lock = refined_round_keys_lock

    def _broadcast_analysis_progress(room_id: str, stage: str, progress: float, detail: str) -> None:
        """广播 AI 分析进度到前端。"""
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
        """启动场景分析/AI高光分析。"""
        room_id = data.get('room_id')
        threshold = safe_float(data.get('threshold', 0.3), 0.3)
        mode = data.get('mode', 'scene')
        game = data.get('game', 'valorant')

        if not room_id:
            return {'error': 'room_id is required'}
        with _analysis_jobs_lock:
            _existing_job = _analysis_jobs.get(room_id)
            if _existing_job and not _existing_job.get('completed_at') and not _existing_job.get('cancelled'):
                return {'success': False, 'error': '该房间已有分析任务进行中'}
            _continuous_conflict = any(
                room_id == (st.get('main_room_id') or '')
                or room_id in (st.get('target_room_ids') or [])
                for st in _continuous_tasks.values()
            )
        if _continuous_conflict:
            return {'success': False, 'error': '该房间正在持续分析中，请先停止持续分析'}
        # 在提交线程池前先登记任务，确保紧随其后的 cancel_analysis 一定能命中。
        with _analysis_jobs_lock:
            _analysis_jobs[room_id] = {
                "progress": 0.0,
                "highlights": [],
                "mode": mode,
                "cancelled": False,
                "stage": "等待分析线程",
            }
        _log.info("启动分析: room_id=%s, mode=%s, threshold=%.2f", room_id, mode, threshold)

        def _do_analysis():
            with _analysis_jobs_lock:
                if _analysis_jobs.get(room_id, {}).get('cancelled'):
                    return {'success': False, 'error': '分析已取消', 'cancelled': True}
            room = manager.get_room(room_id)
            if room is None:
                with _analysis_jobs_lock:
                    _analysis_jobs[room_id].update(completed_at=time.time(), error='房间不存在')
                return {'success': False, 'error': '房间不存在'}
            if not room.record_output_path or not os.path.isfile(room.record_output_path):
                with _analysis_jobs_lock:
                    _analysis_jobs[room_id].update(completed_at=time.time(), error='录制文件不存在')
                return {'success': False, 'error': '录制文件不存在'}

            video_path = room.record_output_path
            t0 = time.monotonic()

            def _progress_cb(stage, progress, detail):
                with _analysis_jobs_lock:
                    if _analysis_jobs.get(room_id, {}).get('cancelled'):
                        return
                    _analysis_jobs[room_id]['progress'] = progress / 100.0
                    _analysis_jobs[room_id]['stage'] = stage
                _broadcast_analysis_progress(room_id, stage, progress, detail)

            def _cancel_check():
                with _analysis_jobs_lock:
                    return _analysis_jobs.get(room_id, {}).get('cancelled', False)

            highlights = analyze_scene_or_rounds(
                video_path, game=game, threshold=threshold,
                progress_callback=_progress_cb, cancel_check=_cancel_check,
            )
            if highlights is None:
                return {'success': False, 'error': '分析已取消', 'cancelled': True}
            with _analysis_jobs_lock:
                if _analysis_jobs.get(room_id, {}).get('cancelled'):
                    return {'success': False, 'error': '分析已取消', 'cancelled': True}

            for _h in highlights:
                _h.setdefault("reason", "场景切换频繁")
                _h.setdefault("speech_score", 0.0)
                _h.setdefault("visual_score", 0.0)
                _h.setdefault("transcript", "")
            with _analysis_jobs_lock:
                _analysis_jobs[room_id] = {
                    "progress": 1.0, "highlights": highlights, "mode": mode,
                    "completed_at": time.time(),
                }
            analysis_time = time.monotonic() - t0
            save_analysis_results(video_path, room_id, mode, highlights, analysis_time_sec=analysis_time)
            _log.info("分析完成: room_id=%s, mode=%s, highlights=%d", room_id, mode, len(highlights))
            return {'success': True, 'mode': mode, 'highlights': highlights}

        executor = ai_executor if mode in ('ai', 'combined') else bridge_executor
        _timeout = 120
        try:
            result = await asyncio.wait_for(
                asyncio.get_running_loop().run_in_executor(executor, _do_analysis),
                timeout=_timeout,
            )
        except asyncio.TimeoutError:
            with _analysis_jobs_lock:
                _analysis_jobs.setdefault(room_id, {})['cancelled'] = True
            _log.error("分析超时（%ss），room_id=%s, mode=%s", _timeout, room_id, mode)
            return {
                'success': False,
                'error': f'分析超时（{_timeout}s），可能模型下载卡住或视频过长。请检查网络后重试。',
            }
        except Exception as exc:
            with _analysis_jobs_lock:
                _analysis_jobs.setdefault(room_id, {}).update(
                    completed_at=time.time(),
                    error=str(exc),
                )
            raise
        return result

    @server.on('start_analysis_export')
    async def handle_start_analysis_export(data):
        """高光分析并自动导出（单房间 / 多房间同步）。"""
        main_room_id = data.get('main_room_id')
        target_room_ids = data.get('target_room_ids') or ([main_room_id] if main_room_id else [])
        mode = data.get('mode', 'scene')
        weights = data.get('weights', {})
        threshold = safe_float(data.get('threshold', 0.3), 0.3)
        game = data.get('game', 'valorant')
        preset_id = data.get('preset_id', '')
        job_prefix = data.get('job_prefix', f'hlexport-{int(time.time() * 1000)}')

        if not main_room_id:
            return {'error': 'main_room_id is required'}
        if not target_room_ids:
            target_room_ids = [main_room_id]

        with _analysis_jobs_lock:
            _existing_job = _analysis_jobs.get(main_room_id)
            if _existing_job and not _existing_job.get('completed_at') and not _existing_job.get('cancelled'):
                return {'success': False, 'error': '该房间已有分析任务进行中'}
            _target_id_set = set(target_room_ids)
            _continuous_conflict = any(
                (st.get('main_room_id') or '') in _target_id_set
                or bool(_target_id_set.intersection(st.get('target_room_ids') or []))
                for st in _continuous_tasks.values()
            )
        if _continuous_conflict:
            return {'success': False, 'error': '目标房间正在持续分析中，请先停止持续分析'}

        with _analysis_jobs_lock:
            _analysis_jobs[main_room_id] = {
                "progress": 0.0,
                "highlights": [],
                "mode": mode,
                "cancelled": False,
                "stage": "等待分析线程",
                "target_room_ids": target_room_ids,
            }

        _log.info("分析并导出: main=%s, targets=%s, mode=%s", main_room_id, target_room_ids, mode)
        loop = asyncio.get_running_loop()

        def _do_analysis_and_export():
            with _analysis_jobs_lock:
                if _analysis_jobs.get(main_room_id, {}).get('cancelled'):
                    return {'success': False, 'error': '分析已取消', 'cancelled': True}
            ok, error, main_room, target_rooms = validate_synced_analysis_targets(
                manager, main_room_id, target_room_ids, wait_for_file=True,
            )
            if not ok:
                with _analysis_jobs_lock:
                    _analysis_jobs[main_room_id].update(
                        completed_at=time.time(),
                        error=error,
                    )
                return {'success': False, 'error': error}

            video_path = main_room.record_output_path
            t0 = time.monotonic()

            def _progress_cb(stage, progress, detail):
                with _analysis_jobs_lock:
                    if _analysis_jobs.get(main_room_id, {}).get('cancelled'):
                        return
                    _analysis_jobs[main_room_id]['progress'] = progress / 100.0
                    _analysis_jobs[main_room_id]['stage'] = stage
                _broadcast_analysis_progress(main_room_id, stage, progress, detail)

            def _cancel_check():
                with _analysis_jobs_lock:
                    return _analysis_jobs.get(main_room_id, {}).get('cancelled', False)

            highlights = analyze_scene_or_rounds(
                video_path, game=game, threshold=threshold,
                progress_callback=_progress_cb, cancel_check=_cancel_check,
            )
            if highlights is None:
                return {'success': False, 'error': '分析已取消', 'cancelled': True}
            with _analysis_jobs_lock:
                if _analysis_jobs.get(main_room_id, {}).get('cancelled'):
                    return {'success': False, 'error': '分析已取消', 'cancelled': True}

            for _h in highlights:
                _h.setdefault("reason", "场景切换频繁")
                _h.setdefault("speech_score", 0.0)
                _h.setdefault("visual_score", 0.0)
                _h.setdefault("transcript", "")

            analysis_time = time.monotonic() - t0
            save_analysis_results(
                video_path, main_room_id, mode, highlights,
                analysis_time_sec=analysis_time, weights=weights if weights else None,
            )
            with _analysis_jobs_lock:
                _analysis_jobs[main_room_id] = {
                    "progress": 1.0, "highlights": highlights, "mode": mode, "completed_at": time.time(),
                    "target_room_ids": _analysis_jobs[main_room_id].get("target_room_ids") or target_room_ids,
                }

            if not highlights:
                return {'success': False, 'error': '未检测到高光片段', 'highlights': []}

            async def _submit_list_only():
                return await auto_export_highlights(
                    main_room, target_rooms, highlights,
                    job_prefix=job_prefix, preset_id=preset_id,
                    defer_export=True, confirm_status='pending', list_only=True,
                )

            submitted_rounds = asyncio.run_coroutine_threadsafe(
                _submit_list_only(), loop
            ).result(timeout=60)
            submitted_list = list(submitted_rounds)
            _log.info("分析导出已入列: main=%s, 高光=%d, 房间=%d, 入列=%d",
                      main_room_id, len(highlights), len(target_rooms), len(submitted_list))
            if not submitted_list:
                return {'success': False, 'error': '未能入列任何切片', 'highlights': highlights}
            with _analysis_jobs_lock:
                _listed = list(
                    (_analysis_jobs.get(main_room_id, {}) or {}).get("listed_clips", {}).values()
                )
            return {
                'success': True, 'highlights': highlights,
                'submitted_count': len(submitted_list), 'job_ids': [],
                'listed_clips': _listed,
            }

        executor = ai_executor if mode in ('ai', 'combined') else bridge_executor
        _timeout = 120
        try:
            result = await asyncio.wait_for(
                loop.run_in_executor(executor, _do_analysis_and_export),
                timeout=_timeout,
            )
        except asyncio.TimeoutError:
            with _analysis_jobs_lock:
                _analysis_jobs.setdefault(main_room_id, {})['cancelled'] = True
            _log.error("分析导出超时（%ss），main_room=%s, mode=%s", _timeout, main_room_id, mode)
            return {
                'success': False,
                'error': f'分析超时（{_timeout}s），可能模型下载卡住或视频过长。请检查网络后重试。',
            }
        except Exception as exc:
            with _analysis_jobs_lock:
                _analysis_jobs.setdefault(main_room_id, {}).update(
                    completed_at=time.time(),
                    error=str(exc),
                )
            raise
        return result

    @server.on('cancel_analysis')
    async def handle_cancel_analysis(data):
        """取消正在进行的 AI 分析。"""
        room_id = data.get('room_id')
        if not room_id:
            return {'error': 'room_id is required'}
        with _analysis_jobs_lock:
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
        now = time.time()
        with _analysis_jobs_lock:
            stale_keys = [
                rid for rid, job in _analysis_jobs.items()
                if job.get('completed_at') and now - job['completed_at'] > _ANALYSIS_JOB_TTL
            ]
            for rid in stale_keys:
                _analysis_jobs.pop(rid, None)
            job = _analysis_jobs.get(room_id)
            if job is not None:
                job = dict(job)
        if job is None:
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

    # ── 持续分析 handlers ──────────────────────────────────────────

    @server.on('start_continuous_analysis')
    async def handle_start_continuous_analysis(data):
        """启动持续分析（边录边分析）。"""
        data = data or {}
        main_room_id = data.get('main_room_id') or data.get('room_id')
        target_room_ids = data.get('target_room_ids') or [main_room_id]
        mode = data.get('mode', 'scene')
        interval = int(data.get('interval', 60))
        threshold = safe_float(data.get('threshold', 0.3), 0.3)
        game = data.get('game', 'valorant')
        _start_valorant_profile = (data.get('valorant_profile') or 'valorant')
        if not main_room_id:
            return {'error': 'room_id is required'}

        requested_target_ids = [str(room_id) for room_id in target_room_ids if room_id]
        if main_room_id not in requested_target_ids:
            requested_target_ids.insert(0, main_room_id)

        def _recording_state(room_id: str) -> dict[str, Any]:
            get_status = getattr(manager, 'get_recording_status', None)
            if callable(get_status):
                return get_status(room_id)  # type: ignore[no-any-return]
            room = manager.get_room(room_id)
            return {
                'exists': room is not None,
                'is_recording': bool(room is not None and getattr(room, 'is_recording', False)),
            }

        recording_states = await asyncio.get_running_loop().run_in_executor(
            bridge_executor,
            lambda: {
                room_id: _recording_state(room_id)
                for room_id in requested_target_ids
            },
        )
        inactive_room_ids = [
            room_id for room_id, status in recording_states.items()
            if not status.get('exists') or not status.get('is_recording')
        ]
        if inactive_room_ids:
            if main_room_id in inactive_room_ids:
                return {'success': False, 'error': '主直播间尚未开始录制，无法启动持续分析'}
            return {
                'success': False,
                'error': '目标房间尚未开始录制，无法启动持续分析',
                'inactive_room_ids': inactive_room_ids,
            }
        target_room_ids = requested_target_ids
        with _analysis_jobs_lock:
            if _continuous_tasks:
                active_room_id = next(iter(_continuous_tasks))
                active_state = _continuous_tasks.get(active_room_id) or {}
                if active_state.get('status') == 'stopping' or active_state.get('cancelled'):
                    return {
                        'success': False, 'error': '持续分析正在停止，请稍后再试',
                        'active_room_id': active_room_id, 'phase': 'stopping',
                    }
                return {'success': False, 'error': '已有持续分析任务正在运行', 'active_room_id': active_room_id}
            _continuous_tasks[main_room_id] = {
                'status': 'starting', 'cancelled': False,
                'main_room_id': main_room_id, 'target_room_ids': list(target_room_ids or []),
            }
        if mode == 'valorant_round' and game == 'valorant':
            interval = 5
        elif interval < 10:
            interval = 10

        def _discard_starting_placeholder() -> None:
            with _analysis_jobs_lock:
                if (_continuous_tasks.get(main_room_id) or {}).get('status') == 'starting':
                    _continuous_tasks.pop(main_room_id, None)

        try:
            ok, error, main_room, target_rooms = await asyncio.get_running_loop().run_in_executor(
                bridge_executor,
                lambda: validate_synced_analysis_targets(
                    manager, main_room_id, target_room_ids, wait_for_file=True,
                ),
            )
        except Exception:
            _discard_starting_placeholder()
            raise
        if not ok:
            _discard_starting_placeholder()
            return {'success': False, 'error': error}
        with _analysis_jobs_lock:
            _placeholder = _continuous_tasks.get(main_room_id) or {}
            _start_aborted = bool(
                _placeholder.get('cancelled') or _placeholder.get('status') == 'stopping'
            )
        if _start_aborted:
            with _analysis_jobs_lock:
                _continuous_tasks.pop(main_room_id, None)
            _log.info("持续分析启动被中止: main_room_id=%s", main_room_id)
            return {'success': False, 'error': '持续分析已在启动前被取消', 'cancelled': True}
        resolved_target_room_ids = [
            getattr(room, "room_id", "") for room in target_rooms if getattr(room, "room_id", "")
        ]

        task = asyncio.create_task(continuous_analysis_loop(
            main_room_id, resolved_target_room_ids, interval, threshold, mode, game,
            valorant_profile=_start_valorant_profile,
        ))
        with _analysis_jobs_lock:
            _continuous_tasks[main_room_id] = {
                'task': task, 'last_analyzed': 0.0, 'highlights': [],
                'cancelled': False, 'completed': False, 'finalizing': False,
                'mode': mode, 'main_room_id': main_room_id,
                'target_room_ids': resolved_target_room_ids,
                'recorded_duration': 0.0, 'confirmed_rounds': 0, 'pending_rounds': 0,
                'listed_clips': {},
                'analysis_stage': '等待新录制', 'session_id': uuid4().hex,
                '_session_t0': time.monotonic(),
            }
        _log.info("持续分析已启动: main_room_id=%s, targets=%s, mode=%s, interval=%ds",
                  main_room_id, resolved_target_room_ids, mode, interval)
        bridge.queue_broadcast({
            'type': 'continuous_analysis_status',
            'data': {
                'running': True, 'room_id': main_room_id,
                'target_room_ids': resolved_target_room_ids, 'mode': mode,
                'analyzed_duration': 0.0, 'total_highlights': 0,
                'recorded_duration': 0.0, 'confirmed_rounds': 0, 'pending_rounds': 0,
                'analysis_stage': '等待新录制', 'phase': 'running',
                'updated_at': time.time(), 'scan_mode': 'full',
                'scan_range': [0.0, 0.0], 'scan_timeout': 120,
                'full_rescan': True, 'refine_with_ocr': False,
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
        with _analysis_jobs_lock:
            if not room_id and len(_continuous_tasks) == 1:
                room_id = next(iter(_continuous_tasks))
            if room_id and room_id not in _continuous_tasks:
                for active_room_id, active_state in list(_continuous_tasks.items()):
                    active_targets = active_state.get('target_room_ids') or []
                    if room_id == active_state.get('main_room_id') or room_id in active_targets:
                        room_id = active_room_id
                        break
            state = _continuous_tasks.get(room_id)
            if state is not None:
                room = manager.get_room(room_id)
                is_recording = bool(room is not None and getattr(room, 'is_recording', False))
                finalizing = bool(state.get('finalizing'))
                ever_recorded = float(state.get('recorded_duration') or 0.0) > 0.0
                if finalizing or (not is_recording and ever_recorded):
                    state['stop_requested'] = True
                    state['status'] = 'stopping'
                    state['analysis_stage'] = '停止中（等待收尾）'
                else:
                    state['cancelled'] = True
                    state['scan_abort'] = True
                    state['status'] = 'stopping'
                    state['analysis_stage'] = '停止中'
                done_event = state.get('scan_done_event')
            else:
                done_event = None
        if not room_id:
            return {'error': 'room_id is required'}
        if not state:
            return {'success': False, 'error': '该房间没有持续分析任务'}
        if done_event is not None and not state.get('scan_running'):
            done_event.set()
        bridge.queue_broadcast({
            'type': 'continuous_analysis_status',
            'data': {
                'running': False, 'phase': 'stopping', 'status': 'stopping',
                'room_id': room_id, 'analysis_stage': '停止中', 'updated_at': time.time(),
            },
        })
        _log.info("持续分析停止请求：room_id=%s", room_id)
        return {
            'success': True, 'status': 'stopping', 'phase': 'stopping',
            'room_id': room_id, 'requested_room_id': requested_room_id,
        }

    @server.on('get_continuous_analysis_status')
    async def handle_get_continuous_analysis_status(data):
        """查询当前是否有正在运行的持续分析任务。"""
        with _analysis_jobs_lock:
            _ct_items = list(_continuous_tasks.items())
        if _ct_items:
            active_room_id, task = _ct_items[0]
            room = manager.get_room(active_room_id)
            recorded_duration = float(task.get('recorded_duration', task.get('last_analyzed', 0.0)) or 0.0)
            if room is not None and getattr(room, 'is_recording', False):
                started = float(getattr(room, 'recording_start_mono', 0.0) or 0.0)
                if started:
                    recorded_duration = max(recorded_duration, time.monotonic() - started)
            analysis_stage = task.get('analysis_stage', '分析中')
            if room is not None and getattr(room, 'is_recording', False) and analysis_stage == '等待新录制':
                analysis_stage = '等待可分析片段'
            if task.get('status') == 'stopping' or (
                task.get('cancelled') and not task.get('completed') and not task.get('finalizing')
            ):
                phase = 'stopping'
            elif task.get('completed'):
                phase = 'completed'
            elif task.get('finalizing'):
                phase = 'finalizing'
            else:
                phase = 'running'
            return build_continuous_status_payload(
                task, room_id=active_room_id,
                recorded_duration=recorded_duration,
                analysis_stage=analysis_stage, phase=phase,
            )
        return {'running': False, 'phase': 'idle', 'updated_at': time.time()}

    # ── 切片精修 handlers ──────────────────────────────────────────

    @server.on('begin_refine_clip')
    async def handle_begin_refine_clip(data):
        """用户点击 pending 切片进入精修。"""
        room_id = data.get('room_id', '')
        round_key = data.get('round_key', '') or data.get('clip_id', '')
        if not round_key:
            _log.warning("begin_refine_clip: 缺少 round_key")
            return {'success': False, 'error': 'missing round_key'}
        with _refined_round_keys_lock:
            _refined_round_keys.add(round_key)
        _clip_refine_state[round_key] = {
            'status': 'refining', 'room_id': room_id,
            'start': float(data.get('start', 0)), 'end': float(data.get('end', 0)),
        }
        bridge.queue_broadcast({
            'type': 'clip_confirm_status',
            'data': {
                'room_id': room_id, 'round_key': round_key,
                'confirm_status': 'refining',
                'start': round(float(data.get('start', 0)), 1),
                'end': round(float(data.get('end', 0)), 1),
            },
        })
        _log.info("精修开始: room=%s, round_key=%s", room_id, round_key)
        return {'success': True, 'round_key': round_key, 'status': 'refining'}

    @server.on('confirm_highlight_clip')
    async def handle_confirm_highlight_clip(data):
        """用户确认精修结果。"""
        room_id = data.get('room_id', '')
        round_key = data.get('round_key', '') or data.get('clip_id', '')
        start = float(data.get('start', 0))
        end = float(data.get('end', 0))
        target_room_ids = data.get('target_room_ids', [])
        if not round_key:
            _log.warning("confirm_highlight_clip: 缺少 round_key")
            return {'success': False, 'error': 'missing round_key'}
        with _refined_round_keys_lock:
            _refined_round_keys.add(round_key)
        _clip_refine_state[round_key] = {
            'status': 'user_confirmed', 'room_id': room_id,
            'start': start, 'end': end, 'target_room_ids': target_room_ids,
        }
        bridge.queue_broadcast({
            'type': 'clip_confirm_status',
            'data': {
                'room_id': room_id, 'round_key': round_key,
                'confirm_status': 'user_confirmed',
                'start': round(start, 1), 'end': round(end, 1),
            },
        })
        main_room = manager.get_room(room_id) if room_id else None
        main_group = (getattr(main_room, 'align_group_id', '') or '') if main_room else ''
        for target_rid in target_room_ids:
            if not target_rid or target_rid == room_id:
                continue
            t_start, t_end = start, end
            target_room = manager.get_room(target_rid)
            if main_room is not None and target_room is not None:
                # §8.6 epoch 失效保护：录制重连后 align_group_id 被清空，副房映射
                # 必须暂停（不得复用旧 content_offset），与持续分析路径一致
                target_group = getattr(target_room, 'align_group_id', '') or ''
                if target_group != main_group:
                    _log.warning(
                        "confirm_highlight_clip: 对齐组不一致，跳过副房映射 "
                        "(main=%s target=%s room=%s)",
                        main_group, target_group, target_rid,
                    )
                    continue
                mapped = map_highlight_to_room(
                    {'start': start, 'end': end}, main_room, target_room,
                )
                t_start = float(mapped.get('start', start))
                t_end = float(mapped.get('end', end))
            if t_end <= t_start:
                continue
            bridge.queue_broadcast({
                'type': 'clip_confirm_status',
                'data': {
                    'room_id': target_rid, 'round_key': round_key,
                    'confirm_status': 'user_confirmed',
                    'start': round(t_start, 1), 'end': round(t_end, 1),
                },
            })
        _log.info("精修确认: room=%s, round_key=%s, targets=%d, %.1f-%.1f",
                  room_id, round_key, len(target_room_ids), start, end)
        return {
            'success': True, 'round_key': round_key,
            'status': 'user_confirmed', 'target_room_ids': target_room_ids,
        }

    @server.on('cancel_refine_clip')
    async def handle_cancel_refine_clip(data):
        """取消精修：恢复 pending，解除 OCR 冻结。"""
        room_id = data.get('room_id', '')
        round_key = data.get('round_key', '') or data.get('clip_id', '')
        if not round_key:
            _log.warning("cancel_refine_clip: 缺少 round_key")
            return {'success': False, 'error': 'missing round_key'}
        saved = _clip_refine_state.pop(round_key, None)
        if saved and not room_id:
            room_id = saved.get('room_id', '')
        with _refined_round_keys_lock:
            _refined_round_keys.discard(round_key)
        broadcast_data: dict = {
            'room_id': room_id, 'round_key': round_key, 'confirm_status': 'pending',
        }
        start = saved.get('start') if saved else None
        end = saved.get('end') if saved else None
        if start is None and data.get('start') is not None:
            start = float(data['start'])
        if end is None and data.get('end') is not None:
            end = float(data['end'])
        if start is not None:
            broadcast_data['start'] = round(float(start), 1)
        if end is not None:
            broadcast_data['end'] = round(float(end), 1)
        bridge.queue_broadcast({'type': 'clip_confirm_status', 'data': broadcast_data})
        _log.info("精修取消: room=%s, round_key=%s", room_id, round_key)
        return {'success': True, 'round_key': round_key, 'status': 'pending'}
