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
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

from continuous_finalization import FinalizationJob
from persistence import (
    is_analysis_stale,
    load_analysis_results,
    load_finalization_job,
    save_analysis_results,
    save_finalization_job,
)

from lsc.analyzer.valorant_profile import (
    inspect_valorant_profile,
    normalize_valorant_profile,
    resolve_valorant_profile,
)
from lsc.recorder.assets import RecordingAsset

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


@contextmanager
def _room_recording_input(room):
    """Yield a single media path for both legacy and manifest recordings."""
    manifest_path = getattr(room, 'record_manifest_path', '') or ''
    if manifest_path and os.path.isfile(manifest_path):
        from lsc.config import load_config

        cfg = load_config()
        asset = RecordingAsset.recover(manifest_path)
        with asset.materialized_input(
            ffmpeg_path=cfg.ffmpeg_path,
            ffprobe_path=cfg.ffprobe_path,
            cleanup=False,
        ) as media_path:
            yield media_path
        return

    media_path = getattr(room, 'record_output_path', '') or ''
    if not media_path or not os.path.isfile(media_path):
        raise FileNotFoundError('录制文件不存在')
    yield media_path


def _bounded_clip_key_add(cache: dict, key: str, value: Any = None) -> None:
    """向保序 dict 缓存写入 key；超上限时裁掉最旧一半。"""
    if len(cache) >= _CLIP_KEY_CACHE_MAX:
        for _old_key in list(cache)[: _CLIP_KEY_CACHE_MAX // 2]:
            cache.pop(_old_key, None)
    cache[key] = value


def _persist_finalization_checkpoint(
    state: dict[str, Any],
    room: Any,
    room_id: str,
) -> dict[str, Any] | None:
    """在 stop 请求返回前持久化可恢复的收尾任务。

    Electron 退出时后端可能在 fresh scan 启动前被强制终止，因此不能只依赖
    `_continuous_analysis_loop` 后续 tick 创建 job。这里仅写入新的 sidecar，
    不修改录制文件或已有分析结果。
    """
    source_path = str(
        getattr(room, "record_output_path", "")
        or getattr(room, "record_manifest_path", "")
        or ""
    )
    if not source_path:
        return None

    payload = state.get("finalization_job")
    if isinstance(payload, dict):
        try:
            job = FinalizationJob.from_dict(payload)
        except (TypeError, ValueError):
            job = None
    else:
        job = None
    if job is None:
        job = FinalizationJob.create(
            job_id=f"finalize-{room_id}-{int(time.time() * 1000)}",
            room_id=room_id,
            recording_id=str(getattr(room, "recording_id", "") or ""),
            source_path=source_path,
            final_duration=float(state.get("recorded_duration", 0.0) or 0.0),
        )
    job.source_path = source_path
    job.valorant_profile = str(
        state.get("valorant_profile") or job.valorant_profile or "pov"
    )
    job.final_duration = max(
        float(job.final_duration or 0.0),
        float(state.get("recorded_duration", 0.0) or 0.0),
    )
    job.phase = "pending"
    job.last_error = ""
    job.updated_at = time.time()
    cur_pending = (state.get("ocr_runtime_state") or {}).get("broadcast_pending_rounds") or []
    # checkpoint 中的 pending_candidates 是当前可恢复队列，不是历史全集；
    # accepted/rejected 候选必须从 sidecar 中移除，避免重启后重复审计。
    job.replace_pending_candidates(cur_pending)
    for item in state.get("refine_result_queue") or []:
        if not isinstance(item, dict):
            continue
        candidate = item.get("candidate")
        delivery_key = str(item.get("delivery_key") or "")
        if isinstance(candidate, dict) and delivery_key:
            job.enqueue_refine_result(
                candidate,
                delivery_key,
                outcome=str(item.get("outcome") or "accepted"),
            )
    for item in state.get("coverage_ranges") or []:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            try:
                job.add_coverage(float(item[0]), float(item[1]))
            except (TypeError, ValueError):
                continue
    serialized = job.to_dict()
    state["finalization_job_id"] = job.job_id
    state["finalization_job"] = serialized
    state["finalization_state"] = "checkpoint_saved"
    if not save_finalization_job(source_path, serialized):
        _log.warning("收尾 checkpoint 保存失败: room_id=%s", room_id)
    return serialized


def _load_finalization_checkpoint_for_room(room: Any, room_id: str) -> FinalizationJob | None:
    """按当前录制路径及同目录 sidecar 查找可恢复收尾任务。

    Only checkpoints whose source file and recording epoch are still valid are
    returned.  A stale in-progress sidecar must not silently win over the
    finalized recording after the recorder renames the file.
    """
    source_path = str(
        getattr(room, "record_output_path", "")
        or getattr(room, "record_manifest_path", "")
        or ""
    )
    candidates: list[str] = []
    if source_path:
        candidates.append(source_path)
        try:
            for suffix in ("*.mp4", "*.mkv", "*.flv"):
                candidates.extend(
                    str(path)
                    for path in Path(source_path).parent.glob(suffix)
                    if str(path) != source_path
                )
        except OSError as exc:
            _log.debug("扫描收尾 sidecar 目录失败: %s", exc)
    valid_jobs: list[FinalizationJob] = []
    current_recording_id = str(getattr(room, "recording_id", "") or "")
    for candidate in candidates:
        payload = load_finalization_job(candidate)
        if not isinstance(payload, dict):
            continue
        if str(payload.get("room_id") or "") != str(room_id):
            continue
        try:
            job = FinalizationJob.from_dict(payload)
        except (TypeError, ValueError) as exc:
            _log.warning("收尾 checkpoint 格式无效: room_id=%s, err=%s", room_id, exc)
            continue
        # 只跳过已完成的收尾任务。phase=="error" 是“收尾超时/扫描失败重试
        # 3 次后放弃”的标记（终端模型错误走另一分支不标 error），其
        # pending_candidates / coverage_ranges 仍然有效且正是需要恢复的对象；
        # 之前连 error 一起跳过，导致 fast_mode 期间延迟堆积、已落盘的解说
        # 候选被永久孤儿化（resume 返回“未找到可恢复的收尾任务”）。
        source = str(job.source_path or candidate)
        if job.phase == "completed":
            continue
        if not source or not os.path.isfile(source):
            # The recorder can rename ``*_录制中.mp4`` after the first
            # checkpoint. Recover the matching finalized path by filename
            # prefix and immediately rebind the sidecar to it.
            replacement = None
            try:
                old_stem = Path(source).stem.replace("_录制中", "").replace(
                    "_in_progress", ""
                )
                for path in Path(candidate).parent.glob("*.mp4"):
                    if (
                        path.is_file()
                        and "_录制中" not in path.name
                        and "_in_progress" not in path.name
                        and path.stem.startswith(old_stem)
                    ):
                        replacement = str(path)
                        break
            except OSError:
                replacement = None
            if replacement:
                replacement_payload = load_finalization_job(replacement)
                if isinstance(replacement_payload, dict):
                    try:
                        replacement_job = FinalizationJob.from_dict(replacement_payload)
                    except (TypeError, ValueError):
                        replacement_job = None
                    if replacement_job is not None and (
                        replacement_job.phase == "completed"
                        or replacement_job.updated_at >= job.updated_at
                    ):
                        if replacement_job.phase == "completed":
                            continue
                        valid_jobs.append(replacement_job)
                        continue
                job.source_path = replacement
                save_finalization_job(replacement, job.to_dict())
                source = replacement
                _log.info(
                    "收尾 checkpoint 已从录制中路径重绑定最终录像: room_id=%s, source=%s",
                    room_id,
                    source,
                )
            else:
                _log.warning(
                    "忽略收尾 checkpoint：源录像不存在 room_id=%s, source=%s",
                    room_id,
                    source,
                )
                continue
        job_recording_id = str(job.recording_id or "")
        if (
            job_recording_id
            and current_recording_id
            and job_recording_id != current_recording_id
        ):
            _log.warning(
                "忽略收尾 checkpoint：录制 epoch 不匹配 room_id=%s, job=%s, current=%s",
                room_id,
                job_recording_id,
                current_recording_id,
            )
            continue
        if job_recording_id and not current_recording_id and getattr(room, "is_recording", False):
            _log.warning(
                "忽略收尾 checkpoint：当前录制 epoch 缺失且房间仍在录制 room_id=%s",
                room_id,
            )
            continue
        # A recorder-provided duration hint is optional. When present, it is a
        # cheap guard against resuming a checkpoint against a truncated file.
        current_duration = getattr(room, "recording_duration_sec", None)
        if current_duration is not None:
            try:
                if float(current_duration) + 2.0 < float(job.final_duration):
                    _log.warning(
                        "忽略收尾 checkpoint：源录像时长回退 room_id=%s, job=%.3f, current=%.3f",
                        room_id,
                        job.final_duration,
                        float(current_duration),
                    )
                    continue
            except (TypeError, ValueError):
                pass
        job.source_path = source
        valid_jobs.append(job)
    if not valid_jobs:
        return None
    return max(valid_jobs, key=lambda item: float(item.updated_at or 0.0))


def _validate_finalization_model_contract(profile: str) -> str | None:
    """Validate the production audit model before starting recovery."""
    if str(profile or "").strip().lower() != "broadcast":
        return None
    try:
        from lsc.analyzer.valorant_frame_classifier import ValorantFrameClassifier

        ValorantFrameClassifier().load()
    except Exception as exc:  # noqa: BLE001 - converted to a user-facing reason
        return f"broadcast 视觉模型契约不可用: {exc}"
    return None


def _finalization_validation_error(room: Any, room_id: str) -> str | None:
    """Return a concrete recovery error when a matching sidecar is stale."""
    source_path = str(
        getattr(room, "record_output_path", "")
        or getattr(room, "record_manifest_path", "")
        or ""
    )
    candidates = [source_path] if source_path else []
    if source_path:
        try:
            for suffix in ("*.mp4", "*.mkv", "*.flv"):
                candidates.extend(str(path) for path in Path(source_path).parent.glob(suffix))
        except OSError:
            pass
    current_recording_id = str(getattr(room, "recording_id", "") or "")
    for candidate in dict.fromkeys(candidates):
        payload = load_finalization_job(candidate)
        if not isinstance(payload, dict) or str(payload.get("room_id") or "") != str(room_id):
            continue
        source = str(payload.get("source_path") or candidate)
        if not source or not os.path.isfile(source):
            return "收尾检查点指向的源录像不存在，无法恢复"
        job_recording_id = str(payload.get("recording_id") or "")
        if (
            job_recording_id
            and current_recording_id
            and job_recording_id != current_recording_id
        ):
            return "收尾检查点的 recording_id 与当前录制 epoch 不匹配，已拒绝恢复"
    return None


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
    analysis_thread_semaphore=None,
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
        requested_valorant_profile = normalize_valorant_profile(
            data.get('valorant_profile')
        )

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
        # token 世代守卫：超时后旧 executor 线程仍在跑，若用户立即重启，
        # 旧线程的完成回写/进度回写不得污染新任务的 job dict（历史竞态）。
        with _analysis_jobs_lock:
            _job_token = uuid4().hex
            _analysis_jobs[room_id] = {
                "progress": 0.0,
                "highlights": [],
                "mode": mode,
                "cancelled": False,
                "stage": "等待分析线程",
                "token": _job_token,
            }
        _log.info("启动分析: room_id=%s, mode=%s, threshold=%.2f", room_id, mode, threshold)

        def _do_analysis():
            with _analysis_jobs_lock:
                job = _analysis_jobs.get(room_id, {})
                if job.get('token') != _job_token or job.get('cancelled'):
                    return {'success': False, 'error': '分析已取消', 'cancelled': True}
            room = manager.get_room(room_id)
            if room is None:
                with _analysis_jobs_lock:
                    if _analysis_jobs.get(room_id, {}).get('token') == _job_token:
                        _analysis_jobs[room_id].update(completed_at=time.time(), error='房间不存在')
                return {'success': False, 'error': '房间不存在'}
            if not (
                (getattr(room, 'record_manifest_path', '') and os.path.isfile(room.record_manifest_path))
                or (getattr(room, 'record_output_path', '') and os.path.isfile(room.record_output_path))
            ):
                with _analysis_jobs_lock:
                    if _analysis_jobs.get(room_id, {}).get('token') == _job_token:
                        _analysis_jobs[room_id].update(completed_at=time.time(), error='录制文件不存在')
                return {'success': False, 'error': '录制文件不存在'}

            valorant_profile = (
                resolve_valorant_profile(
                    requested_valorant_profile,
                    streamer_name=getattr(room, 'streamer_name', ''),
                    stream_title=getattr(room, 'stream_title', ''),
                    room_url=getattr(room, 'room_url', ''),
                )
                if game == 'valorant'
                else 'pov'
            )

            t0 = time.monotonic()
            analysis_storage_path = (
                getattr(room, 'record_manifest_path', '')
                or getattr(room, 'record_output_path', '')
            )

            def _progress_cb(stage, progress, detail):
                with _analysis_jobs_lock:
                    job = _analysis_jobs.get(room_id, {})
                    if job.get('token') != _job_token or job.get('cancelled'):
                        return
                    job['progress'] = progress / 100.0
                    job['stage'] = stage
                _broadcast_analysis_progress(room_id, stage, progress, detail)

            def _cancel_check():
                with _analysis_jobs_lock:
                    job = _analysis_jobs.get(room_id, {})
                    # token 失配 = 本任务已被重启取代，视同取消，让旧扫描尽早退出
                    return job.get('token') != _job_token or bool(job.get('cancelled'))

            with _room_recording_input(room) as video_path:
                highlights = analyze_scene_or_rounds(
                    video_path, game=game, threshold=threshold,
                    progress_callback=_progress_cb, cancel_check=_cancel_check,
                    valorant_profile=valorant_profile,
                )
            if highlights is None:
                return {'success': False, 'error': '分析已取消', 'cancelled': True}
            with _analysis_jobs_lock:
                job = _analysis_jobs.get(room_id, {})
                if job.get('token') != _job_token or job.get('cancelled'):
                    return {'success': False, 'error': '分析已取消', 'cancelled': True}

            for _h in highlights:
                _h.setdefault("reason", "场景切换频繁")
                _h.setdefault("speech_score", 0.0)
                _h.setdefault("visual_score", 0.0)
                _h.setdefault("transcript", "")
            with _analysis_jobs_lock:
                if _analysis_jobs.get(room_id, {}).get('token') != _job_token:
                    _log.warning("旧分析结果丢弃（任务已被重启取代）: room_id=%s", room_id)
                    return {'success': False, 'error': '分析已取消', 'cancelled': True}
                _analysis_jobs[room_id] = {
                    "progress": 1.0, "highlights": highlights, "mode": mode,
                    "completed_at": time.time(),
                }
            analysis_time = time.monotonic() - t0
            save_analysis_results(
                analysis_storage_path,
                room_id,
                mode,
                highlights,
                analysis_time_sec=analysis_time,
            )
            _log.info("分析完成: room_id=%s, mode=%s, highlights=%d", room_id, mode, len(highlights))
            return {'success': True, 'mode': mode, 'highlights': highlights}

        executor = ai_executor if mode in ('ai', 'combined') else bridge_executor
        _timeout = 120

        def _run_analysis_guarded():
            if game == 'valorant' and analysis_thread_semaphore is not None:
                with analysis_thread_semaphore:
                    return _do_analysis()
            return _do_analysis()

        try:
            result = await asyncio.wait_for(
                asyncio.get_running_loop().run_in_executor(executor, _run_analysis_guarded),
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
                # 仅当 job 未被重启取代时才写错误（token 世代守卫）
                if _analysis_jobs.get(room_id, {}).get('token') == _job_token:
                    _analysis_jobs[room_id].update(
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
        requested_valorant_profile = normalize_valorant_profile(
            data.get('valorant_profile')
        )
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

            t0 = time.monotonic()
            analysis_storage_path = (
                getattr(main_room, 'record_manifest_path', '')
                or getattr(main_room, 'record_output_path', '')
            )

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

            with _room_recording_input(main_room) as video_path:
                valorant_profile = (
                    resolve_valorant_profile(
                        requested_valorant_profile,
                        streamer_name=getattr(main_room, 'streamer_name', ''),
                        stream_title=getattr(main_room, 'stream_title', ''),
                        room_url=getattr(main_room, 'room_url', ''),
                    )
                    if game == 'valorant'
                    else 'pov'
                )
                highlights = analyze_scene_or_rounds(
                    video_path, game=game, threshold=threshold,
                    progress_callback=_progress_cb, cancel_check=_cancel_check,
                    valorant_profile=valorant_profile,
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
                    analysis_storage_path, main_room_id, mode, highlights,
                    analysis_time_sec=analysis_time, weights=weights if weights else None,
                )
            if highlights is None:
                return {'success': False, 'error': '分析已取消', 'cancelled': True}
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

        def _run_analysis_export_guarded():
            if game == 'valorant' and analysis_thread_semaphore is not None:
                with analysis_thread_semaphore:
                    return _do_analysis_and_export()
            return _do_analysis_and_export()

        try:
            result = await asyncio.wait_for(
                loop.run_in_executor(executor, _run_analysis_export_guarded),
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
            storage_path = ""
            if room is not None:
                storage_path = (
                    getattr(room, 'record_manifest_path', '')
                    or getattr(room, 'record_output_path', '')
                )
            if storage_path and os.path.isfile(storage_path):
                stored = load_analysis_results(storage_path)
                if stored and not is_analysis_stale(storage_path, stored):
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
        _requested_valorant_profile = normalize_valorant_profile(
            data.get('valorant_profile')
        )
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
        _profile_decision = (
            inspect_valorant_profile(
                _requested_valorant_profile,
                streamer_name=getattr(main_room, 'streamer_name', ''),
                stream_title=getattr(main_room, 'stream_title', ''),
                room_url=getattr(main_room, 'room_url', ''),
            )
            if game == 'valorant'
            else None
        )
        _resolved_valorant_profile = (
            _profile_decision.resolved_profile if _profile_decision else 'pov'
        )

        task = asyncio.create_task(continuous_analysis_loop(
            main_room_id, resolved_target_room_ids, interval, threshold, mode, game,
            valorant_profile=_resolved_valorant_profile,
        ))
        with _analysis_jobs_lock:
            _continuous_tasks[main_room_id] = {
                'task': task, 'last_analyzed': 0.0, 'highlights': [],
                'cancelled': False, 'completed': False, 'finalizing': False,
                'mode': mode, 'main_room_id': main_room_id,
                'target_room_ids': resolved_target_room_ids,
                'valorant_profile': _resolved_valorant_profile,
                'requested_valorant_profile': _requested_valorant_profile,
                'profile_reason': _profile_decision.profile_reason if _profile_decision else 'default',
                'profile_mismatch_warning': _profile_decision.profile_mismatch_warning if _profile_decision else False,
                'profile_warning_message': _profile_decision.warning_message if _profile_decision else '',
                'recorded_duration': 0.0, 'confirmed_rounds': 0, 'pending_rounds': 0,
                'listed_clips': {},
                'analysis_stage': '等待新录制', 'session_id': uuid4().hex,
                '_session_t0': time.monotonic(),
            }
        _log.info(
            "持续分析已启动: main_room_id=%s, targets=%s, mode=%s, profile=%s, interval=%ds",
            main_room_id, resolved_target_room_ids, mode,
            _resolved_valorant_profile, interval,
        )
        bridge.queue_broadcast({
            'type': 'continuous_analysis_status',
            'data': {
                'running': True, 'room_id': main_room_id,
                'target_room_ids': resolved_target_room_ids, 'mode': mode,
                'valorant_profile': _resolved_valorant_profile,
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
            'valorant_profile': _resolved_valorant_profile,
            'requested_valorant_profile': _requested_valorant_profile,
            'profile_reason': _profile_decision.profile_reason if _profile_decision else 'default',
            'profile_mismatch_warning': _profile_decision.profile_mismatch_warning if _profile_decision else False,
            'profile_warning_message': _profile_decision.warning_message if _profile_decision else '',
        }

    @server.on('resume_continuous_finalization')
    async def handle_resume_continuous_finalization(data):
        """从录制旁的 finalization sidecar 恢复收尾扫描。"""
        data = data or {}
        main_room_id = data.get('main_room_id') or data.get('room_id')
        if not main_room_id:
            return {'success': False, 'error': 'room_id is required'}
        with _analysis_jobs_lock:
            if _continuous_tasks:
                active_room_id = next(iter(_continuous_tasks))
                active_state = _continuous_tasks.get(active_room_id) or {}
                requested_job_id = str(data.get('finalization_job_id') or '')
                active_job_id = str(
                    active_state.get('finalization_job_id')
                    or active_state.get('resume_finalization_job', {}).get('job_id')
                    or ''
                )
                if (
                    str(main_room_id) == str(active_room_id)
                    and active_state.get('finalizing')
                    and (not requested_job_id or requested_job_id == active_job_id)
                ):
                    # Repeated UI clicks/reconnect retries are safe and do not
                    # create a second worker for the same finalization job.
                    return {
                        'success': True,
                        'status': 'finalizing',
                        'phase': 'finalizing',
                        'finalization_state': 'finalizing',
                        'room_id': active_room_id,
                        'finalization_job_id': active_job_id or None,
                        'idempotent': True,
                    }
                return {
                    'success': False,
                    'error': '已有持续分析任务正在运行',
                    'active_room_id': active_room_id,
                }
        room = manager.get_room(main_room_id)
        if room is None:
            return {'success': False, 'error': '房间不存在'}
        job = await asyncio.get_running_loop().run_in_executor(
            bridge_executor,
            lambda: _load_finalization_checkpoint_for_room(room, main_room_id),
        )
        if job is None:
            validation_error = await asyncio.get_running_loop().run_in_executor(
                bridge_executor,
                lambda: _finalization_validation_error(room, main_room_id),
            )
            return {
                'success': False,
                'error': validation_error or '未找到可恢复的收尾任务',
                'finalization_state': 'error' if validation_error else 'idle',
            }
        mode = data.get('mode', 'valorant_round')
        game = data.get('game', 'valorant')
        requested_profile = normalize_valorant_profile(
            data.get('valorant_profile') or job.valorant_profile
        )
        profile = (
            resolve_valorant_profile(
                requested_profile,
                streamer_name=getattr(room, 'streamer_name', ''),
                stream_title=getattr(room, 'stream_title', ''),
                room_url=getattr(room, 'room_url', ''),
            )
            if game == 'valorant'
            else 'pov'
        )
        model_error = await asyncio.get_running_loop().run_in_executor(
            bridge_executor,
            lambda: _validate_finalization_model_contract(profile),
        )
        if model_error:
            return {
                'success': False,
                'error': model_error,
                'finalization_state': 'checkpoint_saved',
                'finalization_job_id': job.job_id,
            }
        target_room_ids = [str(item) for item in (data.get('target_room_ids') or [main_room_id]) if item]
        if main_room_id not in target_room_ids:
            target_room_ids.insert(0, main_room_id)
        with _analysis_jobs_lock:
            _continuous_tasks[main_room_id] = {
                'status': 'starting',
                'cancelled': False,
                'main_room_id': main_room_id,
                'target_room_ids': target_room_ids,
                'resume_finalization_job': job.to_dict(),
                'finalization_job_id': job.job_id,
                'finalization_state': 'finalizing',
                'finalizing': True,
            }
        task = asyncio.create_task(
            continuous_analysis_loop(
                main_room_id,
                target_room_ids,
                5 if mode == 'valorant_round' and game == 'valorant' else 60,
                safe_float(data.get('threshold', 0.3), 0.3),
                mode,
                game,
                valorant_profile=profile,
                resume_job=job,
            )
        )
        with _analysis_jobs_lock:
            state = _continuous_tasks.get(main_room_id)
            if state is not None:
                state.update({
                    'task': task,
                    'mode': mode,
                    'game': game,
                    'valorant_profile': profile,
                })
        bridge.queue_broadcast({
            'type': 'continuous_analysis_status',
            'data': {
                'running': True,
                'room_id': main_room_id,
                'target_room_ids': target_room_ids,
                'mode': mode,
                'phase': 'finalizing',
                'finalization_state': 'finalizing',
                'analysis_stage': '恢复收尾中',
                'finalization_job_id': job.job_id,
                'updated_at': time.time(),
            },
        })
        return {
            'success': True,
            'status': 'finalizing',
            'phase': 'finalizing',
            'finalization_state': 'finalizing',
            'room_id': main_room_id,
            'finalization_job_id': job.job_id,
            'target_room_ids': target_room_ids,
            'valorant_profile': profile,
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
                stop_with_finalize = bool(data.get('stop_with_finalize'))
                if finalizing or (not is_recording and ever_recorded) or stop_with_finalize:
                    state['stop_requested'] = True
                    state['status'] = 'stopping'
                    state['analysis_stage'] = '停止中（等待收尾）'
                else:
                    # 录制仍在进行且不要求收尾：先补扫尾部再停止（有界，见
                    # _STOP_TAIL_MAX_WINDOWS），避免最后几分钟的回合永久丢失。
                    # 主循环在补扫窗口完成后自行置 cancelled 退出。
                    state['stop_tail_scan'] = True
                    state['stop_tail_windows'] = 0
                    # 后台审计先行中止（其工作已按候选缓存，重启后可续扫），
                    # 避免审计批次占着线程 semaphore 拖延尾部补扫。
                    state['refine_abort'] = True
                    try:
                        _live_dur = float(state.get('recorded_duration') or 0.0)
                        if room is not None:
                            _started = float(getattr(room, 'recording_start_mono', 0.0) or 0.0)
                            if _started > 0.0:
                                _live_dur = max(_live_dur, time.monotonic() - _started)
                    except (TypeError, ValueError):
                        _live_dur = float(state.get('recorded_duration') or 0.0)
                    state['stop_tail_target'] = max(0.0, _live_dur)
                    state['status'] = 'stopping'
                    state['analysis_stage'] = '停止中（补扫尾部）'
                done_event = state.get('scan_done_event')
            else:
                done_event = None
        if not room_id:
            return {'error': 'room_id is required'}
        if not state:
            return {'success': False, 'error': '该房间没有持续分析任务'}
        checkpoint = None
        if state.get('stop_requested'):
            checkpoint = _persist_finalization_checkpoint(state, room, room_id)
        if done_event is not None and not state.get('scan_running'):
            done_event.set()
        bridge.queue_broadcast({
            'type': 'continuous_analysis_status',
            'data': {
                'running': False, 'phase': 'stopping', 'status': 'stopping',
                'room_id': room_id, 'analysis_stage': '停止中', 'updated_at': time.time(),
                'finalization_state': (
                    'finalizing' if state.get('stop_requested') else 'idle'
                ),
                'finalization_job_id': (checkpoint or {}).get('job_id'),
            },
        })
        _log.info("持续分析停止请求：room_id=%s", room_id)
        return {
            'success': True, 'status': 'stopping', 'phase': 'stopping',
            'room_id': room_id, 'requested_room_id': requested_room_id,
            'finalization_job_id': (checkpoint or {}).get('job_id'),
            'finalization_state': (
                'finalizing' if state.get('stop_requested') else 'idle'
            ),
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
        # Startup/reconnect path: a checkpoint can outlive the in-memory task
        # and therefore must be discoverable from the room list.  Do not make
        # the UI wait for a previous ``phase=error`` event that can never be
        # replayed after an application restart.
        def _discover_recovery() -> tuple[Any, FinalizationJob] | None:
            list_rooms = getattr(manager, 'list_rooms', None)
            if not callable(list_rooms):
                return None
            rooms = list(list_rooms() or [])
            for candidate_room in rooms:
                candidate_id = str(getattr(candidate_room, 'room_id', '') or '')
                if not candidate_id:
                    continue
                candidate_job = _load_finalization_checkpoint_for_room(
                    candidate_room, candidate_id
                )
                if candidate_job is not None:
                    return candidate_room, candidate_job
            # Room preferences intentionally do not persist the active output
            # path. Discover sidecars under the configured output root as well,
            # otherwise a restart cannot find a checkpoint created for the
            # finalized filename.
            try:
                settings = load_settings()
                output_dir = os.path.expanduser(str(settings.get('output_dir') or ''))
                sidecars = (
                    Path(output_dir).rglob('*.finalization.json')
                    if output_dir and Path(output_dir).is_dir()
                    else ()
                )
                room_by_id = {
                    str(getattr(item, 'room_id', '') or ''): item for item in rooms
                }
                suffix = '.finalization.json'
                for sidecar in sidecars:
                    payload = load_finalization_job(
                        str(sidecar.with_name(sidecar.name[:-len(suffix)]))
                    )
                    if not isinstance(payload, dict):
                        continue
                    candidate_id = str(payload.get('room_id') or '')
                    candidate_room = room_by_id.get(candidate_id)
                    if candidate_room is None:
                        continue
                    try:
                        candidate_job = FinalizationJob.from_dict(payload)
                    except (TypeError, ValueError):
                        continue
                    if candidate_job.phase == 'completed':
                        continue
                    if not candidate_job.source_path or not os.path.isfile(candidate_job.source_path):
                        replacement = None
                        try:
                            old_stem = Path(candidate_job.source_path).stem.replace(
                                '_录制中', ''
                            ).replace('_in_progress', '')
                            for media in sidecar.parent.glob('*.mp4'):
                                if (
                                    media.is_file()
                                    and '_录制中' not in media.name
                                    and '_in_progress' not in media.name
                                    and media.stem.startswith(old_stem)
                                ):
                                    replacement = str(media)
                                    break
                        except OSError:
                            replacement = None
                        if replacement:
                            replacement_payload = load_finalization_job(replacement)
                            if isinstance(replacement_payload, dict):
                                try:
                                    replacement_job = FinalizationJob.from_dict(
                                        replacement_payload
                                    )
                                except (TypeError, ValueError):
                                    replacement_job = None
                                if replacement_job is not None and (
                                    replacement_job.phase == 'completed'
                                    or replacement_job.updated_at >= candidate_job.updated_at
                                ):
                                    if replacement_job.phase == 'completed':
                                        continue
                                    candidate_job = replacement_job
                                else:
                                    candidate_job.source_path = replacement
                                    save_finalization_job(
                                        replacement, candidate_job.to_dict()
                                    )
                            else:
                                candidate_job.source_path = replacement
                                save_finalization_job(
                                    replacement, candidate_job.to_dict()
                                )
                        else:
                            continue
                    current_id = str(getattr(candidate_room, 'recording_id', '') or '')
                    if (
                        candidate_job.recording_id
                        and current_id
                        and candidate_job.recording_id != current_id
                    ):
                        continue
                    if (
                        candidate_job.recording_id
                        and not current_id
                        and getattr(candidate_room, 'is_recording', False)
                    ):
                        continue
                    return candidate_room, candidate_job
            except (OSError, TypeError, ValueError) as exc:
                _log.debug('扫描输出目录收尾 sidecar 失败: %s', exc)
            return None

        discovered = await asyncio.get_running_loop().run_in_executor(
            bridge_executor, _discover_recovery
        )
        if discovered is not None:
            recovered_room, recovered_job = discovered
            return {
                'running': False,
                'phase': 'checkpoint_saved',
                'status': 'checkpoint_saved',
                'finalization_state': 'checkpoint_saved',
                'finalization_recoverable': True,
                'room_id': str(getattr(recovered_room, 'room_id', '') or ''),
                'finalization_job_id': recovered_job.job_id,
                'recording_id': recovered_job.recording_id,
                'valorant_profile': recovered_job.valorant_profile,
                'source_path': recovered_job.source_path,
                'recorded_duration': recovered_job.final_duration,
                'analyzed_duration': recovered_job.scan_cursor,
                'pending_rounds': len(recovered_job.pending_candidates),
                'finalization_pending_jobs': 1,
                'audit_terminal_total': len(recovered_job.refine_result_queue),
                'audit_accepted_count': sum(
                    1 for item in recovered_job.refine_result_queue
                    if str(item.get('outcome') or 'accepted') == 'accepted'
                ),
                'audit_delivered_total': 0,
                'audit_delivery_gap': sum(
                    1 for item in recovered_job.refine_result_queue
                    if str(item.get('outcome') or 'accepted') == 'accepted'
                ),
                'refine_result_queue_depth': len(recovered_job.refine_result_queue),
                'listed_clip_count': 0,
                'total_highlights': recovered_job.final_round_count,
                'analysis_stage': '已保存收尾检查点，可恢复',
                'updated_at': time.time(),
            }
        return {'running': False, 'phase': 'idle', 'finalization_state': 'idle', 'updated_at': time.time()}

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
