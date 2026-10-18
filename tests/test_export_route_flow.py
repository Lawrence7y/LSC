"""`export_clip` / `cancel_export` / `get_export_job_status` 的路由级用例（§9-P0.5）。

`export_handlers.py` 此前 33% 只覆盖到纯函数（`_resolve_export_range` 等）。剩下没人守的
正是"导出到底成没成"那条链：`queue_export` 校验 → `_build_export_profile` → 入队 →
worker 取号持 semaphore → `manager.start_export` → `on_done` 回调 →
`clip_completed`/`clip_failed` 广播 + 任务状态表。这些分支写反时用户侧表现是
"点了导出没反应"或"进度条永远不到 100%"，而源码文本守卫完全看不见。

两个实现要点（都是踩出来的）：
- **一个用例一个事件循环**：worker 池是 `_ensure_export_queue_impl` 在**当前**循环里
  `create_task` 出来的，`asyncio.run` 结束时会把它们取消。若提交与等待分在两次
  `asyncio.run` 里，任务会随第一个循环一起死掉（表现为"状态永远不出现"）。
- FFmpeg 不会被启动：`manager.start_export` 是假件，按真实现的回调契约同步收口。
"""
from __future__ import annotations

import asyncio
import os
import sys
from types import SimpleNamespace

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_backend_dir = os.path.join(ROOT, "python-backend")
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

import handlers.export_handlers as eh  # noqa: E402


class _FakeServer:
    def __init__(self) -> None:
        self.handlers: dict = {}
        self.broadcasts: list[tuple[str, dict]] = []

    def on(self, name, *args, **kwargs):
        def _deco(fn):
            self.handlers[name] = fn
            return fn
        return _deco

    async def broadcast(self, type_, data):
        self.broadcasts.append((type_, data))

    def types(self) -> list[str]:
        return [t for t, _ in self.broadcasts]


class _FakeManager:
    """只实现导出链用到的接口；`start_export` 按真实现的回调契约同步收口。"""

    def __init__(self, room, behavior='ok'):
        self.room = room
        self.behavior = behavior
        self.started: list[dict] = []
        self.cancelled: list[str] = []

    def get_room(self, room_id):
        return self.room if room_id == self.room.room_id else None

    def call(self, fn):
        return fn()

    def cancel_export(self, clip_id):
        self.cancelled.append(clip_id)
        return True

    def start_export(self, room_id, start, end, **kwargs):
        self.started.append({'room_id': room_id, 'start': start, 'end': end, **kwargs})
        if self.behavior == 'interface_missing':
            raise AttributeError("'FakeController' object has no attribute 'export_segment'")
        if self.behavior == 'no_clip_id':
            return ''
        on_done = kwargs['on_done']
        if self.behavior == 'progress':
            # 真实现：进度回调会先于完成回调多次触发
            kwargs['on_progress'](42.0, 3.0, 10.0)
        if self.behavior == 'fail':
            on_done(False, '', 'FFmpeg 退出码 1', 0.0, '')
        else:
            on_done(True, 'D:/out/clip.mp4', '', 12.5, 'D:/out/clip.jpg')
        return 'clip-1'


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """注册 handlers，并在用例前后把模块级全局状态复位（worker 池/队列/计数器/状态表）。"""
    room = SimpleNamespace(
        room_id='r1',
        streamer_name='主播',
        stream_title='',
        content_offset=1.0,
        mark_in_wallclock=None,
        mark_out_wallclock=None,
        recording_start_mono=1000.0,
        recording_media_start_mono=1000.0,
        output_bundle_dir='',
        controller=None,
    )
    server = _FakeServer()
    state = {'manager': None}

    class _Proxy:
        def __getattr__(self, name):
            return getattr(state['manager'], name)

    state['manager'] = _FakeManager(room)
    settings = {'output_dir': str(tmp_path), 'encoder': 'H.264 CPU', 'crf': 21,
                'framerate': '30', 'resolution': '', 'bitrate': 8000, 'audio_bitrate': '128k'}
    eh.register_export_handlers(
        server,
        bridge=SimpleNamespace(manager=_Proxy(), queue_broadcast=lambda msg: None),
        manager=_Proxy(),
        bridge_executor=None,
        load_settings=lambda: dict(settings),
        expand_user_path=lambda p: p,
    )
    monkeypatch.setattr(eh, '_check_nvenc', lambda: False)
    yield {'server': server, 'room': room, 'settings': settings, 'state': state, 'tmp': tmp_path}

    for worker in list(eh._EXPORT_WORKERS):
        worker.cancel()
    eh._EXPORT_WORKERS.clear()
    eh._export_queue = None
    # 与模块导入时的初态一致（`_export_semaphore = asyncio.Semaphore(2)`）：
    # 置 None 会让下一个用例的 worker 撞上 `async with None` —— 那是夹具错，不是产品缺陷。
    eh._export_semaphore = asyncio.Semaphore(2)
    eh._export_semaphore_limit = 2
    eh._export_job_states.clear()
    eh.export_jobs.clear()
    eh._export_cancelled_jobs.clear()
    eh._reset_export_batch(0, '')
    eh._ctx = None
    eh._queue_export_fn = None


def _payload(**overrides):
    payload = {
        'room_id': 'r1', 'start': 10.0, 'end': 20.0, 'label': 'clip',
        'mark_in_wallclock': 1010.0, 'mark_out_wallclock': 1020.0,
        'recording_media_start_mono': 1000.0, 'job_id': 'j-ok',
    }
    payload.update(overrides)
    return payload


async def _drive(ctx, *, expect_status, job_id='j-ok', **overrides):
    """同一个事件循环里：提交 → 等 worker 收口 → 返回 (响应, 任务状态)。"""
    if overrides.get('_behavior'):
        ctx['state']['manager'] = _FakeManager(ctx['room'], behavior=overrides.pop('_behavior'))
    out = await ctx['server'].handlers['export_clip'](_payload(job_id=job_id, **overrides))
    final = {}
    for _ in range(600):
        states = eh._get_export_job_states([job_id])
        if states and states[0].get('status') == expect_status:
            final = states[0]
            break
        await asyncio.sleep(0.005)
    else:
        raise AssertionError(f'{job_id} 未进入 {expect_status}: {eh._get_export_job_states([job_id])}')
    return out, final


def test_happy_path_completes_job_and_broadcasts_terminal(env):
    ctx = env
    out, state = asyncio.run(_drive(ctx, expect_status='completed'))
    assert out['success'] is True, out
    assert out['precision'] == 'exact'
    assert state['output_path'] == 'D:/out/clip.mp4'
    assert state['percent'] == 100.0
    assert state['size_mb'] == 12.5
    assert ctx['server'].types().count('clip_completed') == 1
    assert 'clip_export_started' in ctx['server'].types()
    assert 'clip_failed' not in ctx['server'].types()


def test_route_applies_wallclock_range_and_content_offset(env):
    """快照齐全 ⇒ exact，且真的扣了 content_offset=1.0（unit 层只测了纯函数，这里测接线）。"""
    out, _ = asyncio.run(_drive(env, expect_status='completed'))
    assert out['export_start'] == pytest.approx(9.0)
    assert out['export_end'] == pytest.approx(19.0)


def test_route_coerces_string_timestamps_instead_of_raising(env):
    """WS payload 里 start/end 可能是字符串：按共享口径解析后照常入队。

    2026-09-18 之前这里各处手写 `float(data.get(...))` + try/except，同一个坏字段
    在不同入口表现不一致（有的回退 0、有的整条请求抛 ValueError）。
    """
    out, _ = asyncio.run(_drive(env, expect_status='completed', job_id='j-str',
                                start='10.0', end='20.0'))
    assert out['export_start'] == pytest.approx(9.0)
    # 解析不出来的值回退 0.0 ⇒ 空区间被业务校验拒绝，而不是把异常抛给前端
    rejected = asyncio.run(env['server'].handlers['export_clip'](_payload(start='', end='')))
    assert rejected['success'] is False
    assert '入点必须早于出点' in rejected['error']


def test_rejects_inverted_range(env):
    out = asyncio.run(env['server'].handlers['export_clip'](_payload(start=30.0, end=20.0)))
    assert out['success'] is False
    assert '入点必须早于出点' in out['error']


def test_rejects_unknown_room(env):
    out = asyncio.run(env['server'].handlers['export_clip'](_payload(room_id='nope')))
    assert out['success'] is False
    assert '房间不存在' in out['error']


def test_precision_approximate_when_no_wallclock(env):
    ctx = env
    out, _ = asyncio.run(_drive(ctx, expect_status='completed', job_id='j-appr',
                                mark_in_wallclock=None, mark_out_wallclock=None,
                                recording_media_start_mono=None))
    assert out['precision'] == 'approximate'
    # 近似路径要扣预览延迟兜底（CLAUDE.md §8.2），与 exact 不同口径
    assert out['export_start'] == pytest.approx(10.0 - 1.0 - eh._PREVIEW_LATENCY_FALLBACK)


def test_ffmpeg_failure_marks_job_failed_and_broadcasts_clip_failed(env):
    ctx = env
    _, state = asyncio.run(_drive(ctx, expect_status='failed', job_id='j-fail', _behavior='fail'))
    assert 'FFmpeg 退出码 1' in state['error']
    assert [t for t, _ in ctx['server'].broadcasts].count('clip_failed') == 1
    assert 'clip_completed' not in ctx['server'].types()


def test_start_export_without_clip_id_reports_controller_error(env):
    ctx = env
    ctx['room'].controller = SimpleNamespace(_last_export_error='磁盘空间不足')
    _, state = asyncio.run(_drive(ctx, expect_status='failed', job_id='j-noclip',
                                  _behavior='no_clip_id'))
    assert state['error'] == '磁盘空间不足'


def test_missing_controller_interface_is_humanized(env):
    ctx = env
    _, state = asyncio.run(_drive(ctx, expect_status='failed', job_id='j-api',
                                  _behavior='interface_missing'))
    assert state['error'].startswith('导出接口缺失或不兼容')


def test_progress_callback_lands_in_job_state(env):
    """`on_progress` 写的 percent 是前端进度条的唯一依据（广播丢了也能靠状态表补）。"""
    ctx = env
    _, state = asyncio.run(_drive(ctx, expect_status='completed', job_id='j-pr',
                                  _behavior='progress'))
    # 中途写过 42，终态必须覆盖成 100 —— 否则进度条会永远停在中间值
    assert state['percent'] == 100.0


def test_cancel_of_queued_job_marks_cancelled(env):
    server = env['server']

    async def scenario():
        queued = await server.handlers['cancel_export']({'job_id': 'j-queued'})
        missing = await server.handlers['cancel_export']({})
        state = eh._get_export_job_states(['j-queued'])
        return queued, missing, state

    queued, missing, state = asyncio.run(scenario())
    assert queued['success'] is True
    assert 'j-queued' in eh._export_cancelled_jobs
    assert state and state[0]['status'] == 'cancelled'
    assert missing['success'] is False
    assert 'job_id is required' in missing['error']


def test_cancel_of_running_job_goes_through_manager(env):
    server, room = env['server'], env['room']

    async def scenario():
        mgr = _FakeManager(room)
        env['state']['manager'] = mgr
        eh.export_jobs['j-run'] = 'clip-x'
        out = await server.handlers['cancel_export']({'job_id': 'j-run'})
        return out, mgr.cancelled

    out, cancelled = asyncio.run(scenario())
    assert out['success'] is True
    assert cancelled == ['clip-x']
    assert 'export_cancelled' in server.types()


def test_job_status_endpoint_shape(env):
    server = env['server']

    async def scenario():
        eh._set_export_job_state('j1', 'completed', room_id='r1')
        bad = await server.handlers['get_export_job_status']({'job_ids': 'not-a-list'})
        ok = await server.handlers['get_export_job_status']({'job_ids': ['j1', 'unknown']})
        many = await server.handlers['get_export_job_status'](
            {'job_ids': [f'x{i}' for i in range(300)]})
        return bad, ok, many

    bad, ok, many = asyncio.run(scenario())
    assert bad['success'] is False and bad['jobs'] == []
    assert [j['job_id'] for j in ok['jobs']] == ['j1']
    assert many['jobs'] == []  # 未登记的 id 不得凭空造状态


def test_nvenc_preset_falls_back_to_cpu_encoder_when_nvenc_missing(env):
    """NVENC 不可用时预设必须回退 CPU 编码器，否则用户点导出直接失败。"""
    ctx = env

    async def scenario():
        out, _state = await _drive(ctx, expect_status='completed', job_id='j-pro',
                                    preset_id='douyin_vertical')
        return out

    asyncio.run(scenario())
    started = ctx['state']['manager'].started
    assert started, 'start_export 未被调用'
    profile = started[0]['profile']
    assert profile.codec == 'libx264'
    assert profile.vertical_crop is True  # 预设里的竖屏裁剪要带过来
