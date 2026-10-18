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
import time
from datetime import datetime
from typing import Any

from lsc.utils.error_messages import humanize_error

_log = logging.getLogger('lsc.handlers')

_STOP_CONFIRM_POLL_SEC = 1.0
# 现场证据（2026-09-19 16:17）：16:17:22 下发停止 → 16:17:32 跨线程调用超时
# → 16:17:34 才真正合段定稿。给 8s 回查窗口，留 4 倍余量。
_STOP_CONFIRM_MAX_WAIT_SEC = 8.0


def read_room_recording_status(manager, room_id: str) -> dict[str, Any]:
    """录制状态快照：优先走**不经 ``call()``** 的 ``get_recording_status``。

    编排线程忙的时候，任何经 ``call()`` 的读法（含 ``get_room``）都会跟着超时，
    于是"读结果"本身变成第二个故障点 —— 启动侧已经因为这个改用快照读取
    （见 ``get_recording_status`` 的 docstring），停止侧此前没有。
    """
    status_reader = getattr(manager, 'get_recording_status', None)
    if callable(status_reader):
        try:
            status = status_reader(room_id)
            if isinstance(status, dict):
                return status
        except Exception as exc:  # noqa: BLE001 - 读状态失败要退回房间快照
            _log.warning(
                "[录制] get_recording_status failed, fallback to room snapshot: room=%s, error=%s",
                room_id,
                exc,
            )

    room = manager.get_room(room_id)
    if room is None:
        return {}
    return {
        'exists': True,
        'is_recording': bool(getattr(room, 'is_recording', False)),
        'last_error': str(getattr(room, 'last_error', '') or ''),
        'streamer_name': str(getattr(room, 'streamer_name', '') or ''),
        'platform_name': str(getattr(room, 'platform_name', '') or ''),
        'preview_enabled': bool(getattr(room, 'preview_enabled', False)),
    }


async def wait_recording_stopped(
    status_fn,
    room_id: str,
    *,
    executor=None,
    poll_sec: float = _STOP_CONFIRM_POLL_SEC,
    max_wait_sec: float = _STOP_CONFIRM_MAX_WAIT_SEC,
) -> bool:
    """跨线程停止调用超时后，回查录制**是否真的停了**。

    `manager.call(...)` 超时只表示"没在预算内拿到返回值"，停止动作仍在编排线程上
    继续跑完。把它直接答成失败，用户就会在录像其实已完好定稿时看到"停止录制失败"。
    房间被移除（`exists` 为假）同样按"已停"处理 —— 录制不可能还在进行。
    """
    loop = asyncio.get_running_loop()
    deadline = time.monotonic() + max_wait_sec
    while True:
        status = await loop.run_in_executor(executor, status_fn, room_id)
        if not isinstance(status, dict):
            return False
        if not status.get('exists', True):
            return True
        if not status.get('is_recording', False):
            return True
        if time.monotonic() >= deadline:
            return False
        await asyncio.sleep(poll_sec)


def register_recording_handlers(
    server,
    *,
    bridge,
    manager,
    broadcast_rooms,
    bridge_executor,
    recording_executor,
    repair_executor,
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
            return read_room_recording_status(manager, room_id)

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
        wait_for_finalize = bool(data.get('wait_for_finalize'))
        if not room_id:
            return {'error': 'room_id is required'}
        _log.info(
            "停止录制: room_id=%s, wait_for_finalize=%s",
            room_id,
            wait_for_finalize,
        )

        def _stop_async():
            if wait_for_finalize:
                return manager.stop_recording(room_id)
            return manager.stop_recording_async(room_id)

        try:
            success = await asyncio.get_running_loop().run_in_executor(
                bridge_executor,
                lambda: bridge.manager.call(
                    _stop_async,
                    timeout=25.0 if wait_for_finalize else 10.0,
                ),
            )
        except Exception as exc:
            _log.error("停止录制异常: room_id=%s, error=%s", room_id, exc)
            # 超时 ≠ 失败：停止仍在编排线程上继续跑完（实测超时后 2s 才定稿）。
            # 先回查真实状态，只有"确实还在录"才答失败，否则把这次操作按成功回。
            stopped = await wait_recording_stopped(
                lambda rid: read_room_recording_status(manager, rid),
                room_id,
                executor=bridge_executor,
            )
            if not stopped:
                broadcast_rooms()
                return {'success': False, 'error': humanize_error(str(exc))}
            _log.warning("停止录制调用超时但回查已停止: room_id=%s", room_id)
            success = True
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
        # 取路径也要经线程池：`get_room` 走 ``call()``，编排线程仍忙时会在这里再超时
        # 一次，把已经停成功的请求整个打成异常。读不到就如实回空路径，不影响 success。
        try:
            room = await asyncio.get_running_loop().run_in_executor(
                bridge_executor, manager.get_room, room_id
            )
        except Exception as exc:  # noqa: BLE001 - 路径读不到不改变"已停止"这一结论
            _log.warning("停止录制后读取房间快照失败: room_id=%s, error=%s", room_id, exc)
            room = None
        return {
            'success': bool(success),
            'room_id': room_id,
            'output_path': getattr(room, 'record_output_path', '') if room else '',
            'dvr_output_path': getattr(room, 'dvr_output_path', '') if room else '',
            'recording_id': getattr(room, 'recording_id', '') if room else '',
            'finalization_state': 'completed' if success else 'error',
        }

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
        # 走专用 repair 池：同步修复可长达 600s，绝不能占住录制启动用的 rec 池
        repaired = await asyncio.get_running_loop().run_in_executor(
            repair_executor, lambda: repair_recording(path)
        )
        if repaired:
            return {'success': True, 'path': repaired}
        return {'success': False, 'error': '修复失败，文件可能严重损坏'}
