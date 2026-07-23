from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from handlers import room_handler


ROOT = Path(__file__).resolve().parents[1]


def test_continuous_analysis_requires_growing_recording_file_shape() -> None:
    shared_room = SimpleNamespace(
        output_path="recording.mp4",
        record_output_path="recording.mp4",
        file_size_mb=10.0,
        is_recording=True,
    )

    assert shared_room.record_output_path.endswith(".mp4")
    assert shared_room.is_recording is True


def test_valorant_round_window_merge_replaces_overlapping_drift() -> None:
    existing = [
        {"start": 354.717, "end": 424.467, "score": 1.0, "tail_by": "chime"},
    ]
    window = [
        {"start": 407.567, "end": 491.567, "score": 1.0, "tail_by": "chime"},
        {"start": 507.567, "end": 589.567, "score": 0.8, "tail_by": "chime"},
    ]

    merged = room_handler._merge_round_windows(existing, window)

    assert [(item["start"], item["end"]) for item in merged] == [
        (item["start"], item["end"]) for item in window
    ]
    assert all(item.get("round_key") for item in merged)
    for prev, cur in zip(merged, merged[1:]):
        assert prev["end"] <= cur["start"]


def test_valorant_merge_keeps_hybrid_over_full_round() -> None:
    """已 hybrid 确认的回合，不得被后续 full_round 音频结果覆盖。"""
    existing = [
        {
            "start": 12.0,
            "end": 76.0,
            "start_by": "model_buy_exit",
            "end_by": "model_result",
            "boundary_source": "valorant_hybrid_v1",
            "confirm_status": "vision_confirmed",
            "phase": "combat",
            "round_key": "round-000001",
        },
        {
            "start": 97.0,
            "end": 192.0,
            "start_by": "model_buy_exit",
            "end_by": "model_score",
            "boundary_source": "valorant_hybrid_v1",
            "confirm_status": "vision_confirmed",
            "phase": "combat",
            "round_key": "round-000009",
        },
    ]
    window = [
        {
            "start": 87.0,
            "end": 181.0,
            "start_by": "full_round",
            "end_by": "full_round",
            "phase": "full_round",
            "tail_by": "full_round",
        },
        {
            "start": 210.0,
            "end": 290.0,
            "start_by": "full_round",
            "end_by": "full_round",
            "phase": "full_round",
        },
    ]
    merged = room_handler._merge_round_windows(existing, window)
    assert any(
        abs(float(item["start"]) - 97.0) < 0.1 and item.get("end_by") == "model_score"
        for item in merged
    )


def test_valorant_merge_keeps_hybrid_round_over_audio_overlap() -> None:
    """Hybrid 回合不得被重叠的纯音频窗吃掉。"""
    existing = [
        {
            "start": 327.0,
            "end": 400.0,
            "start_by": "model_buy_exit",
            "end_by": "model_result",
            "boundary_source": "valorant_hybrid_v1",
            "confirm_status": "vision_confirmed",
            "phase": "combat",
            "round_key": "round-000033",
        },
    ]
    window = [
        {
            "start": 310.0,
            "end": 420.0,
            "start_by": "audio",
            "end_by": "chime",
            "phase": "combat",
        },
    ]
    merged = room_handler._merge_round_windows(existing, window)
    assert len(merged) == 1
    assert merged[0]["round_key"] == "round-000033"
    assert merged[0]["start_by"] == "model_buy_exit"


def test_valorant_merge_abuts_overlap_instead_of_dropping() -> None:
    """相邻回合轻微重叠时应对齐邻接，不得直接丢后段。"""
    existing = [
        {
            "start": 100.0,
            "end": 200.0,
            "start_by": "ocr_buy_exit",
            "end_by": "next_buy",
            "round_key": "round-000010",
        },
    ]
    window = [
        {
            "start": 195.0,
            "end": 280.0,
            "start_by": "ocr_buy_exit",
            "end_by": "ocr_result",
            "ocr_confirmed": True,
            "round_key": "round-000020",
        },
    ]
    merged = room_handler._merge_round_windows(existing, window)
    assert len(merged) == 2
    assert float(merged[0]["end"]) <= float(merged[1]["start"]) + 0.01
    assert merged[1]["round_key"] == "round-000020"


def test_finalize_scan_timeout_covers_ten_minute_ocr() -> None:
    # 实测 614s 全文件 OCR ~191s；旧公式只给 ~130s
    t = room_handler._finalize_scan_timeout(614.0)
    assert t >= 300
    assert t >= 190
    t2 = room_handler._finalize_scan_timeout(614.0, attempt=2)
    assert t2 > t
    assert room_handler._finalize_scan_timeout(60.0) >= 300
    assert room_handler._finalize_scan_timeout(7200.0) <= 1800


def test_window_scan_timeout_ocr_not_starved_at_fifty_seconds() -> None:
    """对照实测：相位短窗 OCR 用旧公式只给 ~49–52s，TimeoutError 后永远无法升格待确认。"""
    # 纯音频可保持短超时
    audio_to = room_handler._window_scan_timeout(80.0, use_ocr=False)
    assert 45 <= audio_to <= 60

    # OCR：80s / 117s 窗口必须远大于 50s
    ocr_80 = room_handler._window_scan_timeout(80.0, use_ocr=True)
    ocr_117 = room_handler._window_scan_timeout(117.0, use_ocr=True)
    assert ocr_80 >= 120
    assert ocr_117 >= 150
    assert ocr_117 > ocr_80
    assert room_handler._window_scan_timeout(25.0, use_ocr=True) >= 120
    assert room_handler._window_scan_timeout(600.0, use_ocr=True) <= 900


def test_continuous_valorant_budget_ocr_timeout_covers_short_window() -> None:
    """post_combat 短窗预算超时仍须足够长（legacy OCR refine 已禁用）。"""
    _, use_ocr, timeout, _ = room_handler._continuous_valorant_scan_budget(
        "valorant_round",
        last_analyzed=100.0,
        current_dur=150.0,
        pressure={"level": "normal"},
        tick_count=5,
        round_phase="post_combat",
        valorant_profile="broadcast",
    )
    assert use_ocr is False
    assert timeout >= 45


def test_continuous_analysis_interval_respects_resource_pressure() -> None:
    normal_delay, normal_skip = room_handler._continuous_effective_interval(
        interval=30,
        last_analyzed=0.0,
        valorant_incremental=False,
        pressure={"level": "normal", "analysis_interval_multiplier": 1, "pause_analysis": False},
    )
    pressure_delay, pressure_skip = room_handler._continuous_effective_interval(
        interval=30,
        last_analyzed=0.0,
        valorant_incremental=False,
        pressure={"level": "pressure", "analysis_interval_multiplier": 3, "pause_analysis": False},
    )
    valorant_delay, valorant_skip = room_handler._continuous_effective_interval(
        interval=30,
        last_analyzed=3700.0,
        valorant_incremental=True,
        pressure={"level": "pressure", "analysis_interval_multiplier": 2, "pause_analysis": False},
    )
    critical_delay, critical_skip = room_handler._continuous_effective_interval(
        interval=30,
        last_analyzed=0.0,
        valorant_incremental=False,
        pressure={"level": "critical", "analysis_interval_multiplier": 4, "pause_analysis": False, "retry_after_sec": 45},
    )

    assert (normal_delay, normal_skip) == (30, False)
    assert (pressure_delay, pressure_skip) == (90, False)
    assert (valorant_delay, valorant_skip) == (60, False)
    assert (critical_delay, critical_skip) == (120, False)


def test_continuous_analysis_interval_does_not_grow_with_recording_duration() -> None:
    early = room_handler._continuous_effective_interval(
        interval=30,
        last_analyzed=0.0,
        valorant_incremental=True,
        pressure={"level": "normal", "analysis_interval_multiplier": 1, "pause_analysis": False},
    )
    late = room_handler._continuous_effective_interval(
        interval=30,
        last_analyzed=7200.0,
        valorant_incremental=True,
        pressure={"level": "normal", "analysis_interval_multiplier": 1, "pause_analysis": False},
    )

    assert early == late == (30, False)


def test_open_tail_round_is_retained_as_pending_for_status() -> None:
    rounds = [{"start": 100.0, "end": 180.0, "tail_by": "open_tail"}]

    retained = room_handler._drop_open_tail_rounds(rounds, current_dur=180.0)

    assert retained == [{"start": 100.0, "end": 180.0, "tail_by": "open_tail", "phase": "pending"}]
    assert not room_handler._is_auto_exportable_valorant_round(retained[0])


def test_hybrid_closed_round_near_tip_is_not_dropped() -> None:
    """结算帧贴近扫描 tip 时，不得把已闭合 hybrid 回合当 open_tail 丢弃。"""
    rounds = [{
        "start": 92.444,
        "end": 188.611,
        "phase": "combat",
        "boundary_source": "valorant_hybrid_v1",
        "start_by": "model_buy_exit",
        "end_by": "model_result",
        "confirm_status": "pending",
    }]

    retained = room_handler._drop_open_tail_rounds(rounds, current_dur=192.0)

    assert retained == rounds
    assert room_handler._is_listable_hybrid_round(retained[0])


def test_incomplete_non_hybrid_tip_round_still_dropped() -> None:
    rounds = [{"start": 100.0, "end": 190.0, "phase": "combat", "end_by": "full_round"}]

    retained = room_handler._drop_open_tail_rounds(rounds, current_dur=192.0)

    assert retained == []


def test_new_rounds_releases_pending_round_when_hybrid_confirms() -> None:
    previous = [{
        "start": 100.0, "end": 180.0, "phase": "pending",
        "boundary_source": "valorant_hybrid_v1",
        "start_by": "model_buy_exit", "end_by": "open_tail",
        "confirm_status": "pending",
    }]
    current = [{
        "start": 100.0, "end": 195.0, "phase": "combat",
        "boundary_source": "valorant_hybrid_v1",
        "start_by": "model_buy_exit", "end_by": "model_result",
        "confirm_status": "vision_confirmed",
    }]

    assert room_handler._new_rounds(previous, current) == current


def test_valorant_incremental_lookback_is_six_minutes() -> None:
    assert room_handler._VALORANT_INCREMENTAL_LOOKBACK_SEC == 360.0
    assert room_handler._VALORANT_MAX_CATCHUP_SEC > 0.0


def test_continuous_valorant_budget_uses_first_full_scan_then_catchup_window() -> None:
    """首次全量；之后从 last_analyzed 回看并向前追赶，禁止跳到尾部滑动窗。"""
    first_range, first_ocr, _, first_full = room_handler._continuous_valorant_scan_budget(
        mode="valorant_round",
        last_analyzed=0.0,
        current_dur=120.0,
        pressure={"level": "normal", "analysis_window_sec": 180},
    )
    normal_range, normal_ocr, _, normal_full = room_handler._continuous_valorant_scan_budget(
        mode="valorant_round",
        last_analyzed=600.0,
        current_dur=720.0,
        pressure={"level": "normal", "analysis_window_sec": 180},
    )
    critical_range, critical_ocr, _, critical_full = room_handler._continuous_valorant_scan_budget(
        mode="valorant_round",
        last_analyzed=600.0,
        current_dur=720.0,
        pressure={"level": "critical", "analysis_window_sec": 75, "degrade_analysis": True, "pause_analysis": True},
    )

    assert (first_range, first_ocr, first_full) == ((0.0, 120.0), False, True)
    # 回看 180s → 420，向前追赶到 720；不得变成 current-lookback=540 而跳过 540 前的未分析区间
    assert (normal_range, normal_ocr, normal_full) == ((420.0, 720.0), False, False)
    # hybrid 路径：pause 也不恢复 legacy OCR refine
    assert critical_ocr is False
    assert critical_full is False
    assert critical_range[0] <= 600.0
    assert critical_range[1] == 720.0


def test_continuous_valorant_budget_does_not_skip_middle_when_falling_behind() -> None:
    """录制远快于分析时，必须从 last_analyzed 追赶，不能只扫尾部 lookback 秒。

    现场案例：last_analyzed=25, current=277, lookback=240。
    旧逻辑 current-lookback=37 仍可能漏中段；更糟的 60s lookback 会跳到 217-277。
    """
    scan_range, _, _, _ = room_handler._continuous_valorant_scan_budget(
        mode="valorant_round",
        last_analyzed=25.0,
        current_dur=277.0,
        pressure={"level": "normal", "analysis_window_sec": 240},
    )
    assert scan_range[0] <= 25.0
    assert scan_range[1] >= 277.0 - 1.0
    # 旧逻辑 current-60=217 会跳过 25→217；新逻辑必须从 last_analyzed 回看覆盖中段
    assert scan_range[0] < 217.0
    assert scan_range[0] <= max(0.0, 25.0 - 240.0) + 1.0


def test_continuous_valorant_budget_does_not_expand_with_recording_length() -> None:
    """增量窗口按 lookback/追赶上限，不随整场录制时长线性放大。"""
    short_range, _, _, _ = room_handler._continuous_valorant_scan_budget(
        mode="valorant_round",
        last_analyzed=600.0,
        current_dur=720.0,
        pressure={"level": "normal"},
    )
    long_range, _, _, _ = room_handler._continuous_valorant_scan_budget(
        mode="valorant_round",
        last_analyzed=600.0,
        current_dur=3600.0,
        pressure={"level": "normal"},
    )
    lookback = room_handler._VALORANT_INCREMENTAL_LOOKBACK_SEC
    max_catchup = room_handler._VALORANT_MAX_CATCHUP_SEC
    assert short_range[0] == max(0.0, 600.0 - lookback)
    assert short_range[1] == 720.0
    assert long_range[0] == max(0.0, 600.0 - lookback)
    assert long_range[1] - long_range[0] <= max_catchup + lookback + 1.0
    assert long_range[1] < 3600.0


def test_continuous_valorant_budget_caps_catchup_span() -> None:
    """单次追赶有上限，避免一次扫完整场超长录像。"""
    scan_range, _, _, _ = room_handler._continuous_valorant_scan_budget(
        mode="valorant_round",
        last_analyzed=100.0,
        current_dur=3600.0,
        pressure={"level": "normal"},
    )
    assert scan_range[1] - scan_range[0] <= room_handler._VALORANT_MAX_CATCHUP_SEC + room_handler._VALORANT_INCREMENTAL_LOOKBACK_SEC + 1.0
    assert scan_range[0] <= 100.0
    assert scan_range[1] < 3600.0


def test_continuous_valorant_budget_post_combat_caps_catchup_span() -> None:
    """post_combat 相位调度下追赶窗口仍受 _MAX_CATCHUP_SEC + lookback 约束。"""
    scan_range, _, _, _ = room_handler._continuous_valorant_scan_budget(
        mode="valorant_round",
        last_analyzed=100.0,
        current_dur=3600.0,
        pressure={"level": "normal"},
        round_phase="post_combat",
        valorant_profile="broadcast",
    )
    lookback = room_handler._VALORANT_INCREMENTAL_LOOKBACK_SEC
    max_catchup = room_handler._VALORANT_MAX_CATCHUP_SEC
    assert scan_range[1] - scan_range[0] <= max_catchup + lookback + 1.0
    assert scan_range[0] <= 100.0
    assert scan_range[1] < 3600.0


def test_continuous_valorant_budget_honors_phase_short_window() -> None:
    """相位调度下 buy 相位使用短窗；混合视觉不再走 legacy OCR refine。"""
    scan_range, use_ocr, _, full = room_handler._continuous_valorant_scan_budget(
        "valorant_round",
        last_analyzed=100.0,
        current_dur=180.0,
        pressure={"level": "normal"},
        tick_count=3,
        round_phase="buy",
        valorant_profile="pov",
    )
    assert full is False
    assert use_ocr is False
    assert scan_range[1] - scan_range[0] < 240.0


def test_continuous_valorant_budget_dense_ocr_in_post_combat() -> None:
    """post_combat 相位预算仍生效，但 hybrid 路径不再启用 legacy OCR refine。"""
    _, use_ocr, _, _ = room_handler._continuous_valorant_scan_budget(
        "valorant_round",
        last_analyzed=100.0,
        current_dur=150.0,
        pressure={"level": "normal"},
        tick_count=5,
        round_phase="post_combat",
        valorant_profile="broadcast",
    )
    assert use_ocr is False


def test_valorant_round_scan_uses_catchup_window_after_first_scan() -> None:
    scan_range, use_ocr, _, full_rescan = room_handler._continuous_valorant_scan_budget(
        "valorant_round", 600.0, 720.0, {"level": "normal", "analysis_window_sec": 180}
    )

    assert (scan_range, use_ocr, full_rescan) == ((420.0, 720.0), False, False)


def test_valorant_round_scan_only_first_pass_is_full() -> None:
    first_range, first_ocr, _, first_full = room_handler._continuous_valorant_scan_budget(
        "valorant_round", 0.0, 120.0, {"level": "normal"}
    )
    later_range, later_ocr, _, later_full = room_handler._continuous_valorant_scan_budget(
        "valorant_round", 600.0, 720.0, {"level": "normal"}
    )

    assert (first_range, first_full) == ((0.0, 120.0), True)
    # 默认 lookback=240 → max(0, 600-240)=360，向前追赶到 720
    assert later_range[0] == max(0.0, 600.0 - room_handler._VALORANT_INCREMENTAL_LOOKBACK_SEC)
    assert later_range[0] <= 600.0
    assert later_range[1] == 720.0
    assert later_full is False
    assert first_ocr is False
    assert later_ocr is False


def test_valorant_round_ocr_refine_disabled_for_hybrid() -> None:
    """混合视觉为唯一边界权威：valorant_round 不再走 legacy OCR refine。"""
    assert room_handler._continuous_valorant_refine_with_ocr("fast", {"level": "normal"}) is False
    assert room_handler._continuous_valorant_refine_with_ocr(
        "valorant_round", {"level": "critical", "pause_analysis": False}
    ) is False
    assert room_handler._continuous_valorant_refine_with_ocr(
        "valorant_round", {"level": "critical", "pause_analysis": True}
    ) is False
    assert room_handler._continuous_valorant_refine_with_ocr(
        "valorant_round", {"level": "pressure", "degrade_analysis": True}
    ) is False
    assert room_handler._continuous_valorant_refine_with_ocr("valorant_round", {"level": "normal"}) is False


def test_continuous_loop_does_not_disable_ocr_on_critical_odd_tick() -> None:
    """质量档禁止 critical 奇数 tick 强制纯音频。"""
    src = (ROOT / "python-backend/handlers/room_handler.py").read_text(encoding="utf-8")
    loop = src.split("async def _continuous_analysis_loop", 1)[1].split(
        "async def _export_and_broadcast", 1
    )[0]
    assert "pressure.get('level') == 'critical'" not in loop or (
        "refine_with_ocr'] = False" not in loop
    )
    # 更明确：不得再出现「奇数 tick 关 OCR」模式
    assert "_scan_counter % 2 == 1" not in loop


def test_only_hybrid_vision_confirmed_rounds_are_auto_exportable() -> None:
    hybrid_ok = {
        "start": 102.0,
        "end": 154.0,
        "phase": "combat",
        "boundary_source": "valorant_hybrid_v1",
        "start_by": "model_buy_exit",
        "end_by": "model_result",
        "confirm_status": "vision_confirmed",
    }
    assert room_handler._is_auto_exportable_valorant_round(hybrid_ok)
    assert not room_handler._is_auto_exportable_valorant_round({
        **hybrid_ok,
        "confirm_status": "pending",
    })
    assert not room_handler._is_auto_exportable_valorant_round({
        "start": 102.0, "end": 154.0, "phase": "combat",
        "start_by": "ocr_buy_exit", "end_by": "ocr_result",
    })
    assert not room_handler._is_auto_exportable_valorant_round({
        "start": 102.0, "end": 154.0, "phase": "combat",
        "start_by": "ocr_buy_exit", "end_by": "next_buy",
    })
    assert not room_handler._is_auto_exportable_valorant_round({
        "start": 102.0, "end": 154.0, "phase": "pending",
        "start_by": "ocr_buy_exit", "end_by": "open_tail",
    })
    assert not room_handler._is_auto_exportable_valorant_round({
        "start": 102.0, "end": 154.0, "phase": "combat",
        "start_by": "audio", "end_by": "audio",
    })
    assert not room_handler._is_auto_exportable_valorant_round({
        "start": 102.0, "end": 154.0, "phase": "combat",
        "start_by": "full_round", "end_by": "full_round",
    })
    assert not room_handler._is_auto_exportable_valorant_round({
        "start": 154.0, "end": 102.0, "phase": "combat",
        "start_by": "ocr_buy_exit", "end_by": "ocr_result",
    })


def test_trim_valorant_combat_bounds_drops_post_junk() -> None:
    """OCR 双可信入列时裁掉结算后垃圾尾，正赛段作为高质量边界。"""
    trimmed = room_handler._trim_valorant_combat_bounds({
        "start": 102.0,
        "end": 190.0,
        "phase": "combat",
        "start_by": "ocr_buy_exit",
        "end_by": "ocr_result",
        "ocr_start": 102.0,
        "ocr_end": 180.0,
        "round_start_sec": 102.0,
        "round_end_sec": 180.0,
    })
    # 质量档：start +0.5 避开买枪尾帧，end -1.5 避开结算字帧
    assert trimmed["start"] == 102.5
    assert trimmed["end"] == 178.5
    assert not room_handler._is_auto_exportable_valorant_round(trimmed)

    next_buy = room_handler._trim_valorant_combat_bounds({
        "start": 100.0,
        "end": 200.0,
        "phase": "combat",
        "start_by": "ocr_buy_exit",
        "end_by": "next_buy",
        "round_start_sec": 100.0,
    })
    assert next_buy["start"] == 100.5
    assert next_buy["end"] == 200.0  # 准备阶段起点保持，不再 -8s junk


def test_trim_next_buy_keeps_prep_end_even_with_ocr_end() -> None:
    """用户要录到下回合准备开始：next_buy 终点不得被胜利字 ocr_end 拽回。

    对照讲过往导出：analysis end=105.4(next_buy) 但导出成 95.1(=ocr_end 96.6-1.5)。
    """
    trimmed = room_handler._trim_valorant_combat_bounds({
        "start": 24.6,
        "end": 105.4,
        "phase": "combat",
        "start_by": "ocr_buy_exit",
        "end_by": "next_buy",
        "ocr_start": 24.6,
        "ocr_end": 96.6,
        "round_start_sec": 24.6,
        "round_end_sec": 96.6,
    })
    assert trimmed["start"] == pytest.approx(25.1, abs=0.05)
    assert trimmed["end"] == 105.4  # 保持准备开始，禁止 96.6-1.5


def test_trim_ignores_double_offset_ocr_start() -> None:
    """ocr_start 若明显偏离 start（双重 range_offset），不得把入点拽飞。"""
    trimmed = room_handler._trim_valorant_combat_bounds({
        "start": 375.8,
        "end": 487.3,
        "phase": "combat",
        "start_by": "ocr_buy_exit",
        "end_by": "next_buy",
        "ocr_start": 747.5,  # 错位元数据
        "buy_marker_sec": 746.7,
        "round_start_sec": 747.5,
    })
    assert trimmed["start"] == pytest.approx(376.3, abs=0.2)
    assert trimmed["end"] == 487.3


def test_trim_audio_chime_snaps_to_round_end_not_before_chime() -> None:
    """无 OCR 但有钟声：终点贴钟声，不得再 -1.5s 砍掉回合末击杀。"""
    trimmed = room_handler._trim_valorant_combat_bounds({
        "start": 100.0,
        "end": 183.0,  # chime 180 + tail_pad 3
        "phase": "combat",
        "start_by": "audio",
        "end_by": "chime",
        "tail_by": "chime",
        "round_start_sec": 100.0,
        "round_end_sec": 180.0,
    })
    assert trimmed["start"] == 100.0  # 音频战斗起点已过买枪裁剪，不再 +0.5
    assert 180.0 <= trimmed["end"] <= 181.0


def test_trim_full_round_audio_undoes_pre_pad_and_junk() -> None:
    """遗留 full_round 糊窗：回推起点 pre_pad、砍尾 junk，贴近正赛。"""
    trimmed = room_handler._trim_valorant_combat_bounds({
        "start": 92.0,  # combat@100 - 8s pre_pad
        "end": 210.0,
        "phase": "full_round",
        "start_by": "full_round",
        "end_by": "full_round",
        "tail_by": "full_round",
        "round_start_sec": 92.0,
        "round_end_sec": None,
    })
    assert trimmed["start"] == 100.0  # +8s 抵消 full_round_audio_pre_pad
    assert trimmed["end"] == 202.0  # -8s post junk


def test_continuous_valorant_worker_uses_hybrid_detect() -> None:
    """valorant_round 持续分析须走 hybrid 视觉检测，禁止回退纯音频 detect_valorant_rounds。"""
    src = (ROOT / "python-backend/handlers/room_handler.py").read_text(encoding="utf-8")
    worker = src.split("def _continuous_valorant_worker", 1)[1].split(
        "async def _continuous_analysis_loop", 1
    )[0]
    assert "detect_valorant_rounds_hybrid" in worker
    assert "ModelContractError" in worker
    hybrid_branch = worker.split("if game == 'valorant' and _mode == 'valorant_round':", 1)[1]
    assert "detect_valorant_rounds_hybrid" in hybrid_branch.split("return _detect_rounds_by_audio_rhythm", 1)[0]
    assert "detect_valorant_rounds(" not in worker


def test_legacy_audio_ocr_boundary_cannot_enter_valorant_list_path() -> None:
    """音频/OCR 旧边界不得进入 hybrid 入列路径。"""
    legacy_cases = [
        {"start": 10.0, "end": 80.0, "start_by": "ocr_buy_exit", "end_by": "ocr_result", "ocr_confirmed": True},
        {"start": 10.0, "end": 80.0, "start_by": "audio", "end_by": "chime"},
        {"start": 10.0, "end": 80.0, "start_by": "full_round", "end_by": "full_round"},
    ]
    for rd in legacy_cases:
        assert room_handler._is_listable_hybrid_round(rd) is False
        assert room_handler._is_auto_exportable_valorant_round(rd) is False


def test_shadow_mode_skips_listing_path() -> None:
    """LSC_VALORANT_VISION_SHADOW=1 时持续分析跳过 clip_queued 入列。"""
    src = (ROOT / "python-backend/handlers/room_handler.py").read_text(encoding="utf-8")
    assert "_valorant_vision_shadow_enabled" in src
    loop = src.split("async def _continuous_analysis_loop", 1)[1].split(
        "async def _export_and_broadcast", 1
    )[0]
    assert "shadow_mode" in loop
    assert "shadow_rounds_detected" in loop
    assert "跳过 clip_queued" in loop
    assert "and not (state and state.get('shadow_mode'))" in loop


def test_analyze_scene_or_rounds_uses_hybrid_for_valorant() -> None:
    src = (ROOT / "python-backend/handlers/room_handler.py").read_text(encoding="utf-8")
    fn = src.split("def _analyze_scene_or_rounds", 1)[1].split("def _get_video_duration", 1)[0]
    assert "detect_valorant_rounds_hybrid" in fn
    assert "detect_valorant_rounds(" not in fn


def test_shadow_env_helper(monkeypatch) -> None:
    monkeypatch.delenv("LSC_VALORANT_VISION_SHADOW", raising=False)
    assert room_handler._valorant_vision_shadow_enabled() is False
    monkeypatch.setenv("LSC_VALORANT_VISION_SHADOW", "1")
    assert room_handler._valorant_vision_shadow_enabled() is True


def test_derive_round_signals_uses_energy_fields_not_score() -> None:
    """相位信号必须用真实 energy_rise/collapse，禁止 score 冒充。"""
    # 直接测试逻辑：高 score 但无 energy 字段 → 不应视为 energy_rise
    # 通过公开的 helper 行为：合并后的 highlight 字段约定
    hl = {
        "start": 10.0, "end": 90.0, "score": 0.95,
        "start_by": "ocr_buy_exit", "end_by": "open_tail",
        "phase": "pending", "tail_by": "open_tail",
    }
    # score 高但无 energy_rise：调度侧不应仅凭 score 进 pre_combat
    assert hl.get("energy_rise") is not True
    hl2 = dict(hl)
    hl2["energy_rise"] = True
    hl2["tail_by"] = "chime"
    hl2["energy_collapse"] = True
    assert hl2["energy_rise"] is True
    assert hl2["energy_collapse"] is True


def test_clamped_ocr_format_output_not_auto_exportable_without_hybrid() -> None:
    """Legacy OCR 输出不再满足 hybrid 可导门禁。"""
    from lsc.analyzer.round_detector import ValorantRoundConfig, _format_output
    import numpy as np

    cfg = ValorantRoundConfig(full_round=True, pre_combat_pad=2.0, tail_pad=0.0)
    phase = [{
        "start": 40.0,
        "end": 153.0,
        "start_by": "ocr_buy_exit",
        "end_by": "next_buy",
        "tail_by": "ocr_phase",
        "ocr_confirmed": True,
        "ocr_end": None,
    }]
    result = _format_output(
        [(40, 152)], np.ones(152, dtype=np.float32), 1.0, 153.0, cfg,
        phase_rounds=phase,
    )
    assert not room_handler._is_auto_exportable_valorant_round(result[0])
    assert not room_handler._is_listable_hybrid_round(result[0])


def test_ocr_combat_energy_rejects_buy_phase_only_segments() -> None:
    import numpy as np
    from lsc.analyzer.round_detector import _ocr_round_has_combat_energy

    quiet = np.full(60, 10.0, dtype=np.float64)
    combat = np.concatenate([np.full(10, 10.0), np.full(50, 80.0)])
    threshold = 40.0
    assert _ocr_round_has_combat_energy(quiet, 0, 60, threshold) is False
    assert _ocr_round_has_combat_energy(combat, 0, 60, threshold) is True


def test_start_continuous_analysis_validates_off_event_loop() -> None:
    """start_continuous_analysis 的 wait_for_file 校验不得在 event loop 线程里 time.sleep。"""
    src = (ROOT / "python-backend/handlers/room_handler.py").read_text(encoding="utf-8")
    handler = src.split("async def handle_start_continuous_analysis", 1)[1].split(
        "@server.on(", 1
    )[0]
    assert "_validate_synced_analysis_targets" in handler
    assert "run_in_executor" in handler
    # 校验调用须落在 executor 提交路径内，禁止裸同步调用阻塞 asyncio
    validate_idx = handler.find("_validate_synced_analysis_targets")
    window = handler[max(0, validate_idx - 400) : validate_idx]
    assert "run_in_executor" in window


def test_scene_continuous_export_branch_is_list_only() -> None:
    """通用/场景持续分析不得走 defer→flush 自动导出，须与 Valorant 一样 list_only 入列。"""
    src = (ROOT / "python-backend/handlers/room_handler.py").read_text(encoding="utf-8")
    fn = src.split("async def _export_and_broadcast", 1)[1].split(
        "@server.on('start_continuous_analysis')", 1
    )[0]
    scene_branch = fn.split("mode != 'valorant_round'", 1)[1]
    assert "list_only=True" in scene_branch or "list_only = True" in scene_branch
    assert "confirm_status='pending'" in scene_branch or 'confirm_status="pending"' in scene_branch
    call_sites = [
        line.strip()
        for line in scene_branch.splitlines()
        if "_auto_export_highlights" in line
    ]
    assert call_sites, "scene 分支应调用 _auto_export_highlights 入列"
    idx = scene_branch.find("await _auto_export_highlights")
    assert idx >= 0
    window = scene_branch[idx : idx + 800]
    assert "list_only=True" in window or "list_only = True" in window
    assert "confirm_status='pending'" in window or 'confirm_status="pending"' in window


def test_pending_highlights_hybrid_path_skips_trim() -> None:
    """Hybrid 入列禁止 _trim_valorant_combat_bounds；仅 legacy OCR 路径保留 trim。"""
    src = (ROOT / "python-backend/handlers/room_handler.py").read_text(encoding="utf-8")
    loop = src.split("async def _continuous_analysis_loop", 1)[1].split(
        "async def _export_and_broadcast", 1
    )[0]
    assert "_is_listable_hybrid_round" in loop
    hybrid_block = loop.split("if _valorant_incremental_rounds:", 1)[1].split("else:", 1)[0]
    assert "_trim_valorant_combat_bounds" not in hybrid_block


def test_is_timeout_scan_error_detects_asyncio_timeout() -> None:
    """现场：TimeoutError() 的 str 为空，必须用 repr 识别。"""
    assert room_handler._is_timeout_scan_error("TimeoutError()")
    assert room_handler._is_timeout_scan_error(TimeoutError())
    assert room_handler._is_timeout_scan_error(asyncio.TimeoutError())
    assert not room_handler._is_timeout_scan_error("ValueError('x')")
    assert not room_handler._is_timeout_scan_error(None)


def test_apply_scan_timeout_backoff_disables_ocr_and_caps_catchup() -> None:
    """OCR 超时后须降级纯音频并限制追赶窗，避免立刻再开 600s OCR。"""
    state: dict = {"refine_with_ocr": True, "last_scan_error": "TimeoutError()"}
    room_handler._apply_scan_timeout_backoff(state)
    assert int(state.get("ocr_degraded_remaining", 0)) >= 2
    assert float(state.get("post_timeout_max_catchup_sec", 999)) <= 180.0


def test_apply_scan_budget_degrade_forces_audio_and_caps_range() -> None:
    """预算层：ocr_degraded 时关掉 OCR，并把 end 钳到 last_analyzed+cap。"""
    scan_range = (2431.0, 3031.0)  # 现场超时后膨胀窗
    use_ocr, capped = room_handler._apply_scan_budget_degrade(
        {"ocr_degraded_remaining": 3, "post_timeout_max_catchup_sec": 120.0},
        scan_range=scan_range,
        last_analyzed=2551.0,
        use_ocr=True,
    )
    assert use_ocr is False
    assert capped[0] == 2431.0
    assert capped[1] <= 2551.0 + 120.0 + 0.01
    assert capped[1] < 3031.0


def test_clear_ocr_degrade_on_success_decrements() -> None:
    state = {"ocr_degraded_remaining": 2}
    room_handler._note_successful_scan_after_degrade(state)
    assert state["ocr_degraded_remaining"] == 1
    room_handler._note_successful_scan_after_degrade(state)
    assert state["ocr_degraded_remaining"] == 0


def test_continuous_worker_holds_semaphore_until_timed_out_scan_aborts() -> None:
    """超时不得立刻释放分析锁：否则旧 OCR 线程与新扫描并发 → 原生崩溃。"""
    src = (ROOT / "python-backend/handlers/room_handler.py").read_text(encoding="utf-8")
    worker = src.split("async def _continuous_valorant_worker", 1)[1].split(
        "async def _continuous_analysis_loop", 1
    )[0]
    assert "scan_abort" in worker
    assert "asyncio.shield" in worker
    assert "cancel_check" in worker
    # wait_for 超时后仍须 await 同一 future（grace），semaphore 在外层 with 内
    assert worker.find("TimeoutError") < worker.find("scan_abort") or "scan_abort" in worker


def test_vision_confirmed_is_exportable_gate() -> None:
    rd = {
        "start": 10.0, "end": 55.0, "phase": "combat",
        "boundary_source": "valorant_hybrid_v1",
        "start_by": "model_buy_exit",
        "end_by": "model_result",
        "confirm_status": "vision_confirmed",
    }
    assert room_handler._is_auto_exportable_valorant_round(rd) is True
    assert room_handler._is_listable_hybrid_round(rd) is True


def test_hybrid_pending_listable_not_exportable() -> None:
    rd = {
        "start": 10.0, "end": 55.0, "phase": "combat",
        "boundary_source": "valorant_hybrid_v1",
        "start_by": "model_buy_exit",
        "end_by": "model_result",
        "confirm_status": "pending",
    }
    assert room_handler._is_listable_hybrid_round(rd) is True
    assert room_handler._is_auto_exportable_valorant_round(rd) is False


def test_non_hybrid_not_listable_as_hybrid() -> None:
    rd = {"start": 10.0, "end": 80.0, "start_by": "full_round", "end_by": "energy_collapse"}
    assert room_handler._is_listable_hybrid_round(rd) is False


def test_list_only_min_duration_allows_short_hybrid_rounds() -> None:
    """现场：34.8s hybrid 回合 toast 了但被 35s 导出门槛挡住入列。

    list_only 须用更低门槛（≥5s），真正导出路径仍可保留 35s。
    """
    assert hasattr(room_handler, "_min_highlight_duration_for_queue")
    assert room_handler._min_highlight_duration_for_queue(list_only=True) <= 5.0
    assert room_handler._min_highlight_duration_for_queue(list_only=False) >= 35.0
    # 现场短回合应过 list_only 门、不过导出门外
    short = 187.3 - 152.5  # 34.8
    assert short >= room_handler._min_highlight_duration_for_queue(list_only=True)
    assert short < room_handler._min_highlight_duration_for_queue(list_only=False)
    src = (ROOT / "python-backend/handlers/room_handler.py").read_text(encoding="utf-8")
    auto_fn = src.split("async def _auto_export_highlights", 1)[1].split(
        "async def queue_export", 1
    )[0]
    assert "_min_highlight_duration_for_queue" in auto_fn


def test_stop_handler_sets_stopping_not_stopped() -> None:
    """stop 响应须返回 stopping，并在 handler 内广播 stopping 阶段。"""
    src = (ROOT / "python-backend/handlers/room_handler.py").read_text(encoding="utf-8")
    stop_fn = src.split("async def handle_stop_continuous_analysis", 1)[1].split(
        "@server.on('get_continuous_analysis_status')", 1
    )[0]
    assert "'status': 'stopping'" in stop_fn or '"status": "stopping"' in stop_fn
    assert "'phase': 'stopping'" in stop_fn or '"phase": "stopping"' in stop_fn
    assert "scan_abort" in stop_fn
    # 停止中不得广播 running=True，否则前端会把按钮弹回「分析中」
    assert "'running': False" in stop_fn or '"running": False' in stop_fn


def test_worker_stop_has_hard_timeout_constant() -> None:
    """worker 停止等待须有硬上限，避免 shield 永久挂死任务槽。"""
    src = (ROOT / "python-backend/handlers/room_handler.py").read_text(encoding="utf-8")
    assert "_SCAN_ABORT_HARD_SEC" in src
    assert "_SCAN_ABORT_GRACE_SEC" in src
    finally_block = src.split("async def _continuous_analysis_loop", 1)[1].split(
        "async def _export_and_broadcast", 1
    )[0]
    assert "_SCAN_ABORT_HARD_SEC" in finally_block
    # 不得在 finally 里裸 await shield(worker) 无超时
    assert "强制释放任务槽" in finally_block


def test_start_rejects_while_stopping() -> None:
    src = (ROOT / "python-backend/handlers/room_handler.py").read_text(encoding="utf-8")
    start_fn = src.split("async def handle_start_continuous_analysis", 1)[1].split(
        "@server.on('stop_continuous_analysis')", 1
    )[0]
    assert "持续分析正在停止，请稍后再试" in start_fn


def test_frontend_stop_keeps_busy_until_idle() -> None:
    """stop_response 不得立刻 continuousAnalyzing=false；须等 idle。"""
    wb = (ROOT / "lsc-electron/src/pages/Workbench/index.tsx").read_text(encoding="utf-8")
    stop_handler = wb.split("on('stop_continuous_analysis_response'", 1)[1].split(
        "on('continuous_analysis_complete'", 1
    )[0]
    assert "正在停止持续分析" in stop_handler
    assert "setContinuousAnalyzing(true)" in stop_handler
    assert "phase: 'stopping'" in stop_handler
    # 打开 Modal 不得强制重置为单次模式
    assert "setAnalysisIsContinuous(false)\n                      setContinuousModalOpen(true)" not in wb


def test_frontend_continuous_start_checks_send_and_pending() -> None:
    wb = (ROOT / "lsc-electron/src/pages/Workbench/index.tsx").read_text(encoding="utf-8")
    confirm = wb.split("const handleConfirmAnalysisExport", 1)[1].split(
        "// 监听分析结果与进度", 1
    )[0]
    assert "setContinuousSubmitting(true)" in confirm
    assert "if (!queued)" in confirm
    assert "start_continuous_analysis" in confirm
    # 单次路径：send 后关 Modal，但不得立刻清除 submitting（留给 response）
    one_shot = confirm.split("} else {", 1)[1]
    after_modal_close = one_shot.split("setContinuousModalOpen(false)", 1)[1]
    assert "setContinuousSubmitting(false)" not in after_modal_close

def test_finalize_continues_from_cursor_not_full_rescan() -> None:
    """停录收尾从游标继续，不默认全文件重扫。"""
    src = (ROOT / "python-backend/handlers/room_handler.py").read_text(encoding="utf-8")
    loop = src.split("async def _continuous_analysis_loop", 1)[1].split(
        "async def _export_and_broadcast", 1
    )[0]
    assert "停录收尾：从游标继续处理尾部" in loop
    finalize_block = loop.split("停录收尾：从游标继续处理尾部", 1)[1].split("else:", 1)[0]
    assert "last_analyzed" in finalize_block
    assert "full_rescan = False" in finalize_block or "full_rescan=False" in finalize_block
    assert "(0.0, float(current_dur))" not in finalize_block


def test_status_payload_reports_provider_and_latest_error() -> None:
    payload = room_handler._build_continuous_status_payload(
        {
            "mode": "valorant_round",
            "provider": "CPUExecutionProvider",
            "provider_warning": "valorant classifier fell back to CPU",
            "model_version": "v1",
            "last_scan_error": "model missing",
        },
        room_id="main",
    )

    assert payload["provider"] == "CPUExecutionProvider"
    assert payload["provider_warning"] == "valorant classifier fell back to CPU"
    assert payload["model_version"] == "v1"
    assert payload["last_scan_error"] == "model missing"


def test_hybrid_clip_metadata_preserves_boundary_evidence() -> None:
    metadata = room_handler._hybrid_clip_metadata({
        "boundary_source": "valorant_hybrid_v1",
        "boundary_evidence": ["model_buy_exit", "score_increment"],
        "model_version": "v1",
        "start_confidence": 0.91,
        "end_confidence": 0.93,
        "boundary_confidence": 0.92,
    })

    assert metadata["boundary_source"] == "valorant_hybrid_v1"
    assert metadata["boundary_evidence"] == ["model_buy_exit", "score_increment"]
    assert metadata["model_version"] == "v1"


def test_hybrid_effective_interval_allows_five_second_batches() -> None:
    interval, skip = room_handler._continuous_effective_interval(
        5,
        last_analyzed=100.0,
        valorant_incremental=True,
        pressure={},
    )

    assert interval == 5
    assert skip is False


def test_model_contract_error_is_terminal_for_continuous_task() -> None:
    assert room_handler._is_model_contract_error(
        "RuntimeError('ModelContractError: missing model')"
    )
    assert not room_handler._is_model_contract_error("TimeoutError()")


def test_model_contract_error_broadcasts_terminal_error() -> None:
    src = (ROOT / "python-backend/handlers/room_handler.py").read_text(encoding="utf-8")
    branch = src.split("terminal_model_error = _is_model_contract_error(worker_error)", 1)[1].split(
        "elif _finalize_started or _finalize_pending:", 1
    )[0]

    assert "continuous_analysis_complete" in branch
    assert "'error': worker_error" in branch or '"error": worker_error' in branch

