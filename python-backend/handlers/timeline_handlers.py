"""TimelineContext / ClipSnapshot 相关 WS handlers（从 room_handler 抽离）。

仅搬迁，不重构业务逻辑。依赖通过 register_timeline_handlers 参数注入，
保持与原 room_handler 内闭包实现完全一致的行为。

注册形态：room_handler.register_room_handlers 末尾调用
    register_timeline_handlers(server, bridge=bridge, manager=manager, queue_export=queue_export)
"""
from __future__ import annotations

import logging
import os
from typing import Any
from uuid import uuid4

from lsc.core.services.timeline_service import get_timeline_service

_log = logging.getLogger('lsc.handlers')


def timeline_to_dict(ctx) -> dict[str, Any]:
    """将 TimelineContext 序列化为与 get_timeline 同构的 dict。"""
    return {
        'timeline_id': ctx.timeline_id,
        'reference_room_id': ctx.reference_room_id,
        'preview_ready': ctx.preview_ready,
        'clip_ready': ctx.clip_ready,
        'created_at': ctx.created_at,
        'room_snapshots': {
            rid: {
                'room_id': s.room_id,
                'preview_epoch_id': s.preview_epoch_id,
                'recording_id': s.recording_id,
                'preview_to_common_delta': s.preview_to_common_delta,
                'recording_to_common_delta': s.recording_to_common_delta,
                'align_confidence': s.align_confidence,
                'media_start_mono': s.media_start_mono,
            }
            for rid, s in ctx.room_snapshots.items()
        },
    }


def register_timeline_handlers(server, *, bridge, manager, queue_export) -> None:
    """注册 timeline / clip snapshot / export_clip_by_id / get_timeline handlers。

    Args:
        server: WebSocket server（提供 .on / .broadcast）。
        bridge: 跨线程消息桥（提供 .queue_broadcast）。
        manager: MultiRoomManager（用于 export_clip_by_id 读取房间录制文件）。
        queue_export: room_handler 内的统一导出入队协程。
    """
    timeline_svc = get_timeline_service()

    def _on_timeline_invalidated(timeline_id: str, reason: str) -> None:
        try:
            bridge.queue_broadcast({
                'type': 'timeline_invalidated',
                'data': {'timeline_id': timeline_id, 'reason': reason},
            })
        except Exception as exc:
            _log.debug("broadcast timeline_invalidated 失败: %s", exc)

    timeline_svc.add_invalidate_listener(_on_timeline_invalidated)

    # ── create_clip_snapshot handler ──
    @server.on('create_clip_snapshot')
    async def handle_create_clip_snapshot(data):
        """一次把公共入出点原子映射到全部目标房间。

        任一路越界或时钟不可用则整组返回 RANGE_UNAVAILABLE。
        clip_ready=false 时拒绝（CLIP_NOT_READY），禁止无录制 ID 宣称精确切片。
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

        ctx = timeline_svc.get_timeline(timeline_id)
        if ctx is None:
            return {'success': False, 'error': 'timeline not found or expired'}
        if not ctx.clip_ready:
            return {
                'success': False,
                'error': 'CLIP_NOT_READY',
                'message': '对齐可用但录制尚未就绪，请确认各房间正在录制后再添加精确切片',
            }

        shared_group_id = f"group_{timeline_id[:8]}_{uuid4().hex[:8]}"
        created_clip_ids: list[str] = []
        clips = []
        for room_id in target_room_ids:
            snap = timeline_svc.create_clip_snapshot(
                timeline_id, room_id, common_start, common_end,
                source=source, source_highlight_id=source_highlight_id,
                clip_group_id=shared_group_id,
            )
            if snap is None:
                for cid in created_clip_ids:
                    timeline_svc.delete_clip_snapshot(cid)
                return {
                    'success': False,
                    'error': 'RANGE_UNAVAILABLE',
                    'failed_room': room_id,
                }
            created_clip_ids.append(snap.clip_id)
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

    # ── export_clip 支持 clip_id 模式 ──
    @server.on('export_clip_by_id')
    async def handle_export_clip_by_id(data):
        """通过 clip_id 导出切片 — 后端通过 recording_id 找到受信任文件。"""
        clip_id = data.get('clip_id')
        if not clip_id:
            return {'success': False, 'error': 'clip_id is required'}

        snap = timeline_svc.get_clip_snapshot(clip_id)
        if snap is None:
            return {'success': False, 'error': 'clip not found or expired'}

        # 计算时间映射（需要 manager/room 访问，在当前线程执行）
        room = manager.get_room(snap.room_id)
        if room is None:
            return {'success': False, 'error': '房间不存在'}
        if room.recording_id != snap.recording_id:
            return {'success': False, 'error': '录制文件已变化，请重新创建切片'}
        if not room.record_output_path or not os.path.isfile(room.record_output_path):
            return {'success': False, 'error': '该房间没有录制文件'}

        content_offset = getattr(room, 'content_offset', 0.0)
        ctx = timeline_svc.get_timeline(snap.timeline_id)
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
            source=snap.source or data.get('source', 'manual'),
            job_id=jid,
        )

        if result.get('error'):
            return {'success': False, 'error': result['error']}

        return {'success': True, 'clip_id': clip_id, 'job_id': result['job_id'], 'queued': True}

    # ── get_timeline handler ──
    @server.on('get_timeline')
    async def handle_get_timeline(data):
        """返回当前 TimelineContext（按 timeline_id 或 room_id 查询）。"""
        timeline_id = data.get('timeline_id')
        room_id = data.get('room_id')
        if timeline_id:
            ctx = timeline_svc.get_timeline(timeline_id)
        elif room_id:
            ctx = timeline_svc.get_active_timeline_for_room(room_id)
        else:
            return {'success': False, 'error': 'timeline_id or room_id is required'}
        if ctx is None:
            return {'success': False, 'error': 'timeline not found or expired'}
        return {'success': True, 'timeline': timeline_to_dict(ctx)}
