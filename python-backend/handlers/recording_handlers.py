"""录制相关 WebSocket handlers（从 room_handler 抽离）。

仅搬迁，不重构业务逻辑。依赖通过 register_recording_handlers 参数注入，
保持与原 room_handler 内闭包实现完全一致的行为。

注册形态：room_handler.register_room_handlers 内部调用
    register_recording_handlers(server, bridge=bridge, manager=manager, ...)
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime
from typing import Any

from lsc.utils.error_messages import humanize_error

_log = logging.getLogger('lsc.handlers')


def register_recording_handlers(
    server,
    *,
    bridge,
    manager,
    broadcast_rooms,
    bridge_executor,
    recording_executor,
    recording_semaphore,
    recording_starting: set,
    recording_wait_queue: list,
    recording_history: list,
    recording_history_lock,
    max_recording_history: int,
    save_recording_history,
    load_settings,
    expand_user_path,
    reattach_shared_preview,
) -> None:
    """注册录制相关 handlers。

    Args:
        server: WebSocket server（提供 .on / .broadcast）。
        bridge: 跨线程消息桥（提供 .queue_broadcast / .manager）。
        manager: RoomOrchestrator。
        broadcast_rooms: 广播 rooms_updated 的回调。
        bridge_executor: 快操作线程池。
        recording_executor: 录制操作线程池。
        recording_semaphore: 录制并发限流 Semaphore。
        recording_starting: 正在启动录制的 room_id 集合。
        recording_wait_queue: 等待录制槽位的 room_id 队列。
        recording_history: 录制历史列表（可变引用）。
        recording_history_lock: 保护 recording_history 的锁。
        max_recording_history: 录制历史上限。
        save_recording_history: 持久化录制历史的回调。
        load_settings: 加载设置函数。
        expand_user_path: 路径展开函数。
        reattach_shared_preview: 录制启动后重挂共享预览的协程。
    """

    @server.on('start_recording')
    async def handle_start_recording(data):
        """开始录制指定房间（支持并发限流，最多同时 2 路）。"""
        _rec_log = logging.getLogger('lsc.recording')
        room_id = data.get('room_id')
        _rec_log.info("[录制] start_recording request for room_id=%s", room_id)
        if not room_id:
            return {'error': 'room_id is required'}

        settings = load_settings()
        requested_spec = data.get('recording_spec')
        if not isinstance(requested_spec, dict):
            requested_spec = {}

        def _spec_choice(name, default, allowed):
            value = requested_spec.get(name, default)
            return value if value in allowed else default

        output_dir = expand_user_path(settings.get('output_dir', os.path.join(os.path.expanduser('~'), 'LSC', 'output')))
        encoder = _spec_choice(
            'encoder',
            settings.get('encoder', 'h264_nvenc'),
            {
                'h264_nvenc', 'hevc_nvenc', 'h264_qsv', 'h264_amf',
                'libx264', 'libx265', 'copy',
                'H.264 NVENC', 'H.265 NVENC', 'H.264 CPU', 'H.265 CPU', 'Copy',
            },
        )
        try:
            crf = max(0, min(51, int(requested_spec.get('crf', settings.get('crf', 23)))))
        except (TypeError, ValueError):
            crf = 23
        param_mode = _spec_choice(
            'param_mode',
            settings.get('param_mode', 'CRF 质量'),
            {'CRF 质量', '自定义码率', '码率限制', '不限制'},
        )
        raw_bitrate = str(requested_spec.get('bitrate', settings.get('bitrate', 8000)))
        bitrate = raw_bitrate if raw_bitrate.replace('.', '', 1).isdigit() else '8000'
        bitrate_unit = _spec_choice(
            'bitrate_unit', settings.get('bitrate_unit', 'kbps'), {'kbps', 'Mbps'},
        )
        resolution = _spec_choice(
            'resolution',
            settings.get('resolution', '原画'),
            {'原画', '1920:1080', '1280:720', '854:480'},
        )
        framerate = _spec_choice(
            'framerate', settings.get('framerate', '原画'), {'原画', '60', '30', '24'},
        )
        audio_bitrate = _spec_choice(
            'audio_bitrate', settings.get('audio_bitrate', '128k'), {'128k', '192k', '256k'},
        )
        _rec_log.info(
            "[录制] effective spec room=%s encoder=%s mode=%s crf=%s bitrate=%s%s resolution=%s fps=%s audio=%s",
            room_id, encoder, param_mode, crf, bitrate, bitrate_unit,
            resolution, framerate, audio_bitrate,
        )

        def _start():
            return manager.start_recording(
                room_id, output_dir, encoder, crf,
                param_mode=param_mode, bitrate=bitrate, bitrate_unit=bitrate_unit,
                resolution=resolution, framerate=framerate, audio_bitrate=audio_bitrate,
                _run_in_background=True,
            )

        # 防重复提交：同一房间正在启动录制时拒绝重复请求
        if room_id in recording_starting:
            _rec_log.warning("[录制] room %s already starting", room_id)
            return {
                'success': False,
                'error': '该房间正在启动录制中，请稍候',
                'room_id': room_id,
            }
        recording_starting.add(room_id)
        # 立即广播 is_recording_starting，让前端按钮立刻进入 loading
        broadcast_rooms(force=True)
        success = False
        error_msg: str | None = None
        try:
            should_queue = recording_semaphore.locked() or len(recording_starting) > 12
            if should_queue:
                if room_id not in recording_wait_queue:
                    recording_wait_queue.append(room_id)
                position = recording_wait_queue.index(room_id) + 1
                bridge.queue_broadcast({
                    'type': 'recording_queue',
                    'data': {'room_id': room_id, 'position': position, 'waiting': True},
                })
                broadcast_rooms(force=True)

            _rec_log.info("[录制] acquiring semaphore for room %s", room_id)
            await recording_semaphore.acquire()
            try:
                if room_id in recording_wait_queue:
                    recording_wait_queue.remove(room_id)
                broadcast_rooms(force=True)
                _rec_log.info("[录制] semaphore acquired, submitting to executor for room %s", room_id)
                success = await asyncio.get_running_loop().run_in_executor(recording_executor, _start)
                _rec_log.info("[录制] executor returned success=%s for room %s", success, room_id)
            finally:
                recording_semaphore.release()
        except Exception as exc:
            _rec_log.error("[录制] exception for room %s: %s", room_id, exc, exc_info=True)
            error_msg = humanize_error(str(exc))
            success = False
        finally:
            recording_starting.discard(room_id)
            if room_id in recording_wait_queue:
                recording_wait_queue.remove(room_id)

        def _read_recording_status() -> dict[str, Any]:
            status_reader = getattr(manager, 'get_recording_status', None)
            if callable(status_reader):
                try:
                    status = status_reader(room_id)
                    if isinstance(status, dict):
                        return status
                except Exception as exc:
                    _rec_log.warning(
                        "[录制] get_recording_status failed, fallback to room snapshot: room=%s, error=%s",
                        room_id,
                        exc,
                    )

            room = manager.get_room(room_id)
            if room is None:
                return {}
            return {
                'is_recording': bool(getattr(room, 'is_recording', False)),
                'last_error': str(getattr(room, 'last_error', '') or ''),
                'streamer_name': str(getattr(room, 'streamer_name', '') or ''),
                'platform_name': str(getattr(room, 'platform_name', '') or ''),
                'preview_enabled': bool(getattr(room, 'preview_enabled', False)),
            }

        recording_status = await asyncio.get_running_loop().run_in_executor(
            recording_executor, _read_recording_status
        )
        is_recording = bool(recording_status.get('is_recording'))
        last_err = str(recording_status.get('last_error') or '')
        if success and not is_recording:
            success = False
            error_msg = last_err or '录制进程未保持运行，未写入有效录像文件'
            _rec_log.warning("[录制] startup reported success but recording is inactive: room=%s, error=%s", room_id, error_msg)
        if is_recording:
            with recording_history_lock:
                recording_history.append({
                    'title': recording_status.get('streamer_name') or '未知主播',
                    'platform': recording_status.get('platform_name') or '',
                    'start_time': datetime.now().isoformat(),
                    'room_id': room_id,
                })
                if len(recording_history) > max_recording_history:
                    del recording_history[:len(recording_history) - max_recording_history]
                save_recording_history(recording_history)
            if success:
                await reattach_shared_preview(
                    room_id, bool(recording_status.get('preview_enabled'))
                )

        broadcast_rooms(force=True)
        if error_msg is not None:
            return {'success': False, 'error': error_msg, 'room_id': room_id}
        if not success:
            fail_msg = last_err or '录制启动失败，请检查房间状态'
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
                bridge_executor, lambda: bridge.manager.call(_stop_async, timeout=5.0)
            )
        except Exception as exc:
            _log.error("停止录制异常: room_id=%s, error=%s", room_id, exc)
            broadcast_rooms()
            return {'success': False, 'error': humanize_error(str(exc))}
        _log.info("停止录制完成: room_id=%s, success=%s", room_id, success)

        with recording_history_lock:
            for record in reversed(recording_history):
                if record.get('room_id') == room_id and 'end_time' not in record:
                    record['end_time'] = datetime.now().isoformat()
                    start = datetime.fromisoformat(record['start_time'])
                    end = datetime.fromisoformat(record['end_time'])
                    duration = (end - start).total_seconds()
                    record['duration'] = f"{int(duration // 3600):02d}:{int((duration % 3600) // 60):02d}:{int(duration % 60):02d}"
                    break
            save_recording_history(recording_history)

        broadcast_rooms()
        return {'success': bool(success)}

    @server.on('repair_recording')
    async def handle_repair_recording(data):
        """修复录制文件（moov atom 不完整）。"""
        room_id = data.get('room_id')
        if not room_id:
            return {'success': False, 'error': 'room_id is required'}

        room = manager.get_room(room_id)
        if room is None:
            return {'success': False, 'error': '房间不存在'}

        path = getattr(room, 'record_output_path', '')
        if not path or not os.path.isfile(path):
            return {'success': False, 'error': '录制文件不存在'}

        from lsc.utils.recording_repair import repair_recording
        repaired = await asyncio.get_running_loop().run_in_executor(
            recording_executor, lambda: repair_recording(path)
        )
        if repaired:
            return {'success': True, 'path': repaired}
        return {'success': False, 'error': '修复失败，文件可能严重损坏'}
