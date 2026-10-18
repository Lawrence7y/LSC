"""导出/对齐两个 handler 模块的纯逻辑单元覆盖（2026-09-18 债务收口 §9-P0.5）。

审计实测 `alignment_handlers.py` 16% / `export_handlers.py` 16%（报告 §2.3），
而这两个模块承载"切片入/出点"和"多房间时间轴锚点"—— 恰恰是错了用户直接看得见
的两条链。它们此前的"防线"只有源码文本守卫（§5），所以这里补的是**真断言**：

- `_resolve_export_range` 的 5 条分支（exact / approximate / 预览延迟兜底 /
  部分墙钟快照特例 / AI 高光直通）—— 决定导出到底切在哪；
- `_epoch_ms_to_mono` 的跨线程时钟换算 —— 现场"预览轴与导出轴错位 6~7 秒"的根因修复；
- 导出状态表LRU、批量计数、预设回退、fps/浮点解析、settings 输出目录兜底。
"""
from __future__ import annotations

import json
import os
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_backend_dir = os.path.join(ROOT, "python-backend")
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

from handlers import alignment_handlers as ah  # noqa: E402
from handlers import export_handlers as eh  # noqa: E402

LATENCY = eh._PREVIEW_LATENCY_FALLBACK  # noqa: SLF001


# ────────────────── _resolve_export_range：导出入/出点的五条分支 ──────────────────
def test_ai_highlight_passes_through_without_offset():
    """AI 高光的起止点已经是录制轴，不能再扣 content_offset / 预览延迟。"""
    start, end, precision = eh._resolve_export_range(  # noqa: SLF001
        12.0, 30.0, source='ai_highlight', content_offset=5.0,
        snap_in=1.0, snap_out=2.0, snap_rec=3.0,
    )
    assert (start, end, precision) == (12.0, 30.0, 'exact')
    # 负值仍被夹到 0（不产出负起点）
    assert eh._resolve_export_range(-5.0, -1.0, source='ai_highlight')[:2] == (0.0, 0.0)  # noqa: SLF001


def test_full_wall_clock_snapshot_is_exact_and_offset_anchored():
    """三件套快照齐全 ⇒ 用快照锚定，exact，且扣 content_offset。"""
    start, end, precision = eh._resolve_export_range(  # noqa: SLF001
        100.0, 200.0, content_offset=1.5, snap_in=60.0, snap_out=90.0, snap_rec=10.0,
    )
    assert precision == 'exact'
    assert (start, end) == (60.0 - 10.0 - 1.5, 90.0 - 10.0 - 1.5)


def test_room_marks_take_priority_when_complete():
    start, end, precision = eh._resolve_export_range(  # noqa: SLF001
        0.0, 0.0, source='timeline', content_offset=2.0, use_room_marks=True,
        room_mark_in=40.0, room_mark_out=55.0, room_rec_start=5.0,
    )
    assert precision == 'exact'
    assert (start, end) == (33.0, 48.0)


def test_room_marks_without_snapshot_fall_back_to_preview_latency():
    """房间标记缺失 ⇒ 只能近似，并且必须扣掉预览延迟兜底（§8.2）。"""
    start, end, precision = eh._resolve_export_range(  # noqa: SLF001
        50.0, 60.0, content_offset=1.0, use_room_marks=True,
    )
    assert precision == 'approximate'
    assert (start, end) == (50.0 - 1.0 - LATENCY, 60.0 - 1.0 - LATENCY)


def test_partial_snapshot_anchors_to_timeline_without_latency_guess():
    """只剩一件快照时仍按录制轴锚定，**不**再加预览延迟（代码里明确写着的特例）。"""
    start, end, precision = eh._resolve_export_range(  # noqa: SLF001
        50.0, 60.0, content_offset=1.0, snap_rec=9.0,
    )
    assert precision == 'approximate'
    assert (start, end) == (49.0, 59.0)


def test_no_anchor_information_uses_the_generic_approximate_path():
    start, end, precision = eh._resolve_export_range(70.0, 80.0, content_offset='0.5')  # noqa: SLF001
    assert precision == 'approximate'
    assert (start, end) == (70.0 - 0.5 - LATENCY, 80.0 - 0.5 - LATENCY)


def test_result_never_goes_negative_even_with_big_offset():
    start, end, _ = eh._resolve_export_range(1.0, 2.0, content_offset=99.0)  # noqa: SLF001
    assert (start, end) == (0.0, 0.0)


# ────────────────── 导出状态表与批量计数 ──────────────────
def test_export_job_state_merges_and_ignores_empty_job_id():
    eh._export_job_states.clear()
    try:
        eh._set_export_job_state('j1', 'queued', room_id='r1')  # noqa: SLF001
        eh._set_export_job_state('j1', 'done', output_path='/tmp/a.mp4')  # noqa: SLF001
        eh._set_export_job_state('', 'done', room_id='r2')  # 空 id 直接忽略

        states = eh._get_export_job_states(['j1', 'missing'])  # noqa: SLF001
        assert len(states) == 1
        assert states[0]['status'] == 'done'
        assert states[0]['room_id'] == 'r1', "后续更新必须合并而不是覆盖旧字段"
        assert states[0]['output_path'] == '/tmp/a.mp4'
    finally:
        eh._export_job_states.clear()  # noqa: SLF001


def test_export_job_state_table_evicts_oldest_only(monkeypatch):
    eh._export_job_states.clear()
    monkeypatch.setattr(eh, '_MAX_EXPORT_JOB_STATES', 3)
    try:
        for i in range(6):
            monkeypatch.setattr(time, 'time', lambda base=1000.0, i=i: base + i)
            eh._set_export_job_state(f'j{i}', 'done')  # noqa: SLF001
        kept = sorted(eh._export_job_states)  # noqa: SLF001
        assert kept == ['j3', 'j4', 'j5'], "超上限时按 updated_at 淘汰最旧的"
    finally:
        eh._export_job_states.clear()  # noqa: SLF001


class _Bridge:
    def __init__(self):
        self.msgs = []

    def queue_broadcast(self, msg):
        self.msgs.append(msg)


def test_notify_export_overall_counts_and_carries_batch_id():
    bridge = _Bridge()
    eh._reset_export_batch(3, 'batch-7')  # noqa: SLF001
    try:
        eh._notify_export_overall(bridge)  # noqa: SLF001
        eh._notify_export_overall(bridge)  # noqa: SLF001
        payload = [m for m in bridge.msgs if m['type'] == 'export_overall_progress']
        assert len(payload) == 2
        assert payload[-1]['data'] == {
            'total': 3, 'completed': 2, 'percent': pytest.approx(200 / 3), 'batch_id': 'batch-7',
        }
    finally:
        eh._reset_export_batch(0, '')  # noqa: SLF001


def test_notify_export_overall_without_batch_does_not_broadcast():
    """total=0（非批量入口）时不得凭空造一条"0/0"进度广播。"""
    bridge = _Bridge()
    eh._reset_export_batch(0, '')  # noqa: SLF001
    try:
        eh._notify_export_overall(bridge)  # noqa: SLF001
        assert bridge.msgs == []
    finally:
        eh._reset_export_batch(0, '')  # noqa: SLF001


# ────────────────── 预设 / 数值解析 / 并发上限 ──────────────────
def test_get_export_preset_custom_wins_and_broken_settings_fall_back():
    builtin = eh._get_export_preset('original', lambda: {})  # noqa: SLF001
    assert builtin is not None and builtin['codec'] == 'copy'

    def load_custom():
        return {'appSettings': {'custom_export_presets': [
            {'id': 'original', 'codec': 'libx264', 'crf': 20},
            'not-a-dict-should-be-skipped',
        ]}}

    custom = eh._get_export_preset('original', load_custom)  # noqa: SLF001
    assert custom is not None
    assert custom['codec'] == 'libx264' and custom['crf'] == 20
    # 未给的字段回落到内置默认值（不是 KeyError）
    assert custom['audio_bitrate'] == '128k'

    assert eh._get_export_preset('nope', lambda: {}) is None  # noqa: SLF001

    def boom():
        raise RuntimeError('settings unreadable')

    assert eh._get_export_preset('high_quality', boom)['crf'] == 18  # noqa: SLF001


@pytest.mark.parametrize(
    ('raw', 'expected'),
    [('30', 30.0), ('原画', 0.0), ('', 0.0), (None, 0.0), ('abc', 0.0), ('59.94', 59.94)],
)
def test_parse_fps_never_raises(raw, expected):
    assert eh._parse_fps(raw) == expected  # noqa: SLF001


@pytest.mark.parametrize(('raw', 'expected'), [(1, 1), (2, 2), ('1', 1), (3, 2), (0, 2), (None, 2)])
def test_export_max_concurrent_only_accepts_one_or_two(monkeypatch, raw, expected):
    ctx = {'load_settings': lambda: {'export_max_concurrent': raw}}
    monkeypatch.setattr(eh, '_ctx', ctx)
    assert eh._get_export_max_concurrent() == expected  # noqa: SLF001


def test_export_max_concurrent_defaults_without_register(monkeypatch):
    monkeypatch.setattr(eh, '_ctx', None)
    assert eh._get_export_max_concurrent() == 2  # noqa: SLF001


# `safe_float` 已由 `lsc/utils/coerce.py` 统一提供（见 tests/test_coerce.py），
# 导出路由如何用它由 tests/test_export_route_flow.py 覆盖；这里不再重复测助手。


# ────────────────── 对齐侧：跨时钟换算与房间态 ──────────────────
def test_epoch_ms_to_mono_ignores_non_positive():
    assert ah._epoch_ms_to_mono(0) == 0.0  # noqa: SLF001
    assert ah._epoch_ms_to_mono(-5) == 0.0  # noqa: SLF001


def test_epoch_ms_to_mono_preserves_the_capture_gap():
    """两路采集相差 5s 结束 ⇒ 换算后的单调时刻也必须相差 5s。

    修前所有房间共用"后端收到请求"这一个锚点，先结束的房间被人为推迟，
    现场表现为预览轴与导出轴错位 6~7 秒。
    """
    now_epoch = time.time()
    late = ah._epoch_ms_to_mono(now_epoch * 1000.0)  # noqa: SLF001
    early = ah._epoch_ms_to_mono((now_epoch - 5.0) * 1000.0)  # noqa: SLF001
    assert (late - early) == pytest.approx(5.0, abs=0.5)
    assert late == pytest.approx(time.monotonic(), abs=1.0)


def test_clear_room_alignment_resets_all_three_fields():
    class _Room:
        align_group_id = 'g1'
        content_offset = 3.5
        output_bundle_dir = '/tmp/x'

    room = _Room()
    ah._clear_room_alignment(room)  # noqa: SLF001
    assert (room.align_group_id, room.content_offset, room.output_bundle_dir) == ('', 0.0, '')


def test_settings_output_dir_falls_back_to_default_on_bad_state(tmp_path, monkeypatch):
    good = tmp_path / 'settings.json'
    good.write_text(json.dumps({'output_dir': str(tmp_path / 'rec')}), encoding='utf-8')
    monkeypatch.setattr(ah, 'SETTINGS_FILE', good)
    monkeypatch.setattr(ah, 'expand_user_path', lambda p: p)
    assert ah._settings_output_dir().endswith('rec')  # noqa: SLF001

    broken = tmp_path / 'broken.json'
    broken.write_text('{not json', encoding='utf-8')
    monkeypatch.setattr(ah, 'SETTINGS_FILE', broken)
    assert ah._settings_output_dir().endswith(os.path.join('LSC', 'output'))  # noqa: SLF001

    monkeypatch.setattr(ah, 'SETTINGS_FILE', tmp_path / 'missing.json')
    assert ah._settings_output_dir().endswith(os.path.join('LSC', 'output'))  # noqa: SLF001
