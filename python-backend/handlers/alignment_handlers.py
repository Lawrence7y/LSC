"""多房间音频对齐 WebSocket handlers（从 room_handler 抽离）。

仅搬迁，不重构业务逻辑。依赖通过 register_alignment_handlers 参数注入，
保持与原 room_handler 内闭包实现完全一致的行为。

注册形态：room_handler.register_room_handlers 内部调用
    register_alignment_handlers(server, bridge=bridge, manager=manager, broadcast_rooms=..., ...)
"""
from __future__ import annotations

import asyncio
import logging
import time

import numpy as np
from handlers.timeline_handlers import timeline_to_dict

from lsc.core.services.timeline_service import (
    build_room_snapshots_from_align,
    get_timeline_service,
)

_log = logging.getLogger('lsc.handlers')
# 前端响应 watchdog 为 30 秒，后端必须提前返回明确错误，不能与前端同时超时。
_ALIGN_COMPUTE_TIMEOUT_SEC = 20.0
_MIN_ALIGN_SAMPLE_RATE = 8_000
_MAX_ALIGN_SAMPLE_RATE = 48_000
_MAX_ALIGN_DURATION_SEC = 15


def register_alignment_handlers(
    server,
    *,
    bridge,
    manager,
    broadcast_rooms,
    bridge_executor,
    recording_executor,
) -> None:
    """注册音频对齐相关 handlers。

    Args:
        server: WebSocket server（提供 .on / .broadcast）。
        bridge: 跨线程消息桥（提供 .queue_broadcast / .manager）。
        manager: RoomOrchestrator。
        broadcast_rooms: 广播 rooms_updated 的回调。
        bridge_executor: 快操作线程池。
        recording_executor: 录制/密集操作线程池。
    """

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
        await asyncio.get_running_loop().run_in_executor(bridge_executor, lambda: bridge.manager.call(_set))
        return {'success': True}

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
        # 限制房间数与单路 PCM 大小，防止超大 payload 导致 OOM
        _MAX_ALIGN_ROOMS = 64
        _MAX_PCM_BASE64_BYTES = 20 * 1024 * 1024  # 20 MB per room
        if len(rooms_data) > _MAX_ALIGN_ROOMS:
            _align_log.warning("预览音频对齐房间数过多: %d (limit %d)", len(rooms_data), _MAX_ALIGN_ROOMS)
            return {'success': False, 'error': f'房间数过多（{len(rooms_data)}），上限 {_MAX_ALIGN_ROOMS}'}
        try:
            import base64

            from lsc.editor.audio_aligner import align_audio_map

            # 解码 PCM 数据
            audio_map: dict[str, np.ndarray] = {}
            align_sample_rate: int | None = None
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
                if room_id in audio_map:
                    return {'success': False, 'error': f'房间 {room_id} 的音频数据重复'}
                if not (_MIN_ALIGN_SAMPLE_RATE <= sample_rate <= _MAX_ALIGN_SAMPLE_RATE):
                    return {
                        'success': False,
                        'error': f'房间 {room_id} 的采样率无效：{sample_rate}',
                    }
                # 限制单路 PCM 大小，防止超大 base64 解码导致 OOM
                if len(pcm_b64) > _MAX_PCM_BASE64_BYTES:
                    _align_log.warning("预览音频对齐跳过: room_id=%s, PCM 过大=%d bytes (limit %d)",
                                       room_id, len(pcm_b64), _MAX_PCM_BASE64_BYTES)
                    continue
                try:
                    raw = base64.b64decode(pcm_b64, validate=True)
                    if len(raw) % np.dtype(np.float32).itemsize != 0:
                        raise ValueError('PCM 字节数不是 float32 的整数倍')
                    samples = np.frombuffer(raw, dtype=np.float32)
                    if samples.size < sample_rate:  # 至少1秒
                        _align_log.warning("预览音频对齐跳过: room_id=%s, 样本过少=%d", room_id, samples.size)
                        continue
                    if samples.size > sample_rate * _MAX_ALIGN_DURATION_SEC:
                        return {
                            'success': False,
                            'error': f'房间 {room_id} 的音频超过 {_MAX_ALIGN_DURATION_SEC} 秒上限',
                        }
                    if align_sample_rate is None:
                        align_sample_rate = sample_rate
                    elif sample_rate != align_sample_rate:
                        return {
                            'success': False,
                            'error': '各房间音频采样率不一致，请重新采集后再对齐',
                        }
                    audio_map[room_id] = samples
                    _align_log.info("解码预览音频: room_id=%s, samples=%d (%.2fs), rate=%d",
                                    room_id, samples.size, samples.size / sample_rate, sample_rate)
                except Exception as exc:
                    _align_log.warning("预览音频解码失败: room_id=%s, error=%s", room_id, exc)

            valid_ids = list(audio_map.keys())
            if len(valid_ids) < 2:
                _align_log.warning("有效预览音频不足 2 路: %s", valid_ids)
                return {'success': False, 'error': '有效音频不足 2 路，无法互相关对齐'}

            # 互相关 FFT 可能阻塞数百 ms～数秒，卸载到线程池避免堵死 WS 事件循环
            try:
                result = await asyncio.wait_for(
                    asyncio.get_running_loop().run_in_executor(
                        recording_executor,
                        lambda: align_audio_map(
                            audio_map,
                            int(align_sample_rate or 16000),
                            method='preview_audio',
                        ),
                    ),
                    timeout=_ALIGN_COMPUTE_TIMEOUT_SEC,
                )
            except asyncio.TimeoutError:
                _align_log.error(
                    "预览音频对齐计算超时: rooms=%d timeout=%.0fs",
                    len(audio_map),
                    _ALIGN_COMPUTE_TIMEOUT_SEC,
                )
                return {
                    'success': False,
                    'error': '音频对齐计算超时，请减少房间数量后重试',
                }
            if not result.success:
                def _clear_on_align_fail():
                    for rid in audio_map:
                        room = manager.get_room(rid)
                        if room is None:
                            continue
                        room.align_group_id = ''
                    return True

                try:
                    await asyncio.get_running_loop().run_in_executor(
                        bridge_executor, lambda: bridge.manager.call(_clear_on_align_fail)
                    )
                    broadcast_rooms(force=True)
                except Exception as exc:
                    _align_log.warning("对齐失败后清除对齐组失败: %s", exc)
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
                    "可信对齐房间不足 %d/2，清除 align_group_id: trusted=%s",
                    len(trusted),
                    list(trusted.keys()),
                )

                def _clear_stale_align_groups():
                    for rid in offsets:
                        room = manager.get_room(rid)
                        if room is None:
                            continue
                        room.content_offset = 0.0
                        room.align_group_id = ''
                    return True

                try:
                    await asyncio.get_running_loop().run_in_executor(
                        bridge_executor, lambda: bridge.manager.call(_clear_stale_align_groups)
                    )
                    broadcast_rooms(force=True)
                except Exception as exc:
                    _align_log.warning("清除对齐组失败: %s", exc)
                return {
                    'success': False,
                    'error': '可信对齐不足，无法建立对齐组',
                    'offsets': result.offsets,
                    'reference_room_id': result.reference_room_id,
                    'method': result.method,
                    'scores': result.correlation_scores,
                    'precision': 'buffer_only',
                }

            group_id = f"align_{int(time.time())}"
            reference_room_id = result.reference_room_id

            def _apply_alignment_and_create_timeline():
                timeline_svc = get_timeline_service()
                # 重新对齐前先失效旧 timeline
                seen_tids: set[str] = set()
                for rid in trusted:
                    old = timeline_svc.get_active_timeline_for_room(rid)
                    if old is not None and old.timeline_id not in seen_tids:
                        seen_tids.add(old.timeline_id)
                        timeline_svc.invalidate_timeline(
                            old.timeline_id, f"realign:{group_id}",
                        )

                room_meta: dict[str, dict] = {}
                for rid, offset in offsets.items():
                    room = manager.get_room(rid)
                    if room is None:
                        continue
                    score = float(scores.get(rid, 0.0) or 0.0)
                    if score < _ALIGN_TRUST_THRESHOLD:
                        room.content_offset = 0.0
                        room.align_group_id = ''
                        continue
                    room.content_offset = float(offset)
                    room.align_group_id = group_id
                    media_start = (
                        getattr(room, 'recording_media_start_mono', None)
                        or getattr(room, 'recording_start_mono', None)
                        or 0.0
                    )
                    room_meta[rid] = {
                        'preview_epoch_id': getattr(room, 'preview_epoch_id', '') or '',
                        'recording_id': getattr(room, 'recording_id', '') or '',
                        'media_start_mono': float(media_start or 0.0),
                    }

                snapshots = build_room_snapshots_from_align(
                    reference_room_id,
                    offsets=trusted,
                    scores=scores,
                    room_meta=room_meta,
                    confidence_threshold=_ALIGN_TRUST_THRESHOLD,
                )
                if len(snapshots) < 2:
                    _align_log.warning(
                        "对齐快照不足 2 路，跳过 create_timeline: %s",
                        list(snapshots.keys()),
                    )
                    return None
                return timeline_svc.create_timeline(
                    reference_room_id,
                    snapshots,
                    required_room_ids=list(trusted.keys()),
                )

            timeline_payload = None
            try:
                ctx = await asyncio.get_running_loop().run_in_executor(
                    bridge_executor, lambda: bridge.manager.call(_apply_alignment_and_create_timeline)
                )
                if ctx is not None:
                    timeline_payload = timeline_to_dict(ctx)
                    bridge.queue_broadcast({
                        'type': 'timeline_ready',
                        'data': {'timeline': timeline_payload},
                    })
                else:
                    _align_log.warning(
                        "create_timeline 未创建（offset 已保留）: reference=%s trusted=%s",
                        reference_room_id, list(trusted.keys()),
                    )

                    def _clear_on_timeline_fail():
                        for rid in trusted:
                            room = manager.get_room(rid)
                            if room is None:
                                continue
                            room.align_group_id = ''
                            room.content_offset = 0.0
                        return True

                    try:
                        await asyncio.get_running_loop().run_in_executor(
                            bridge_executor, lambda: bridge.manager.call(_clear_on_timeline_fail)
                        )
                        broadcast_rooms(force=True)
                    except Exception as clear_exc:
                        _align_log.warning("公共时间轴创建失败后清除对齐组失败: %s", clear_exc)
                    return {
                        'success': False,
                        'error': '公共时间轴创建失败',
                        'offsets': result.offsets,
                        'reference_room_id': result.reference_room_id,
                        'method': result.method,
                        'scores': result.correlation_scores,
                        'precision': 'buffer_only',
                    }
            except Exception as exc:
                _align_log.warning("写入对齐组/创建 TimelineContext 失败: %s", exc)
                return {
                    'success': False,
                    'error': '公共时间轴创建失败',
                    'detail': str(exc),
                    'offsets': result.offsets,
                    'reference_room_id': result.reference_room_id,
                    'method': result.method,
                    'scores': result.correlation_scores,
                    'precision': 'buffer_only',
                }

            response = {
                'success': True,
                'offsets': result.offsets,
                'reference_room_id': result.reference_room_id,
                'method': result.method,
                'scores': result.correlation_scores,
                'align_group_id': group_id,
            }
            if timeline_payload is not None:
                response['timeline'] = timeline_payload
            return response
        except Exception as exc:
            _align_log.error("预览音频对齐失败: %s", exc, exc_info=True)
            return {'success': False, 'error': str(exc)}
