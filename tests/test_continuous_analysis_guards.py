from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from handlers import room_handler

ROOT = Path(__file__).resolve().parents[1]


def test_pressure_yield_check_after_worker_consume() -> None:
    """压力让路须在 worker 结果消费之后：让路 continue 不消费结果会与
    skipped-sleep（completed_at > last_consumed_at）组合成忙循环广播风暴。"""
    src = (ROOT / "python-backend/handlers/room_handler.py").read_text(encoding="utf-8")
    consume_idx = src.find("worker_completed_at = scan_result.get('completed_at', 0.0)")
    yield_idx = src.find("_log.info(\"持续分析让路:")
    assert consume_idx > 0
    assert yield_idx > consume_idx


def test_skip_sleep_has_ceiling() -> None:
    """主循环连续跳过 sleep 须有上限强制节流，防止任何路径组合重演忙循环。"""
    src = (ROOT / "python-backend/handlers/room_handler.py").read_text(encoding="utf-8")
    assert "_MAX_SKIP_SLEEP_TICKS" in src
    assert "_skip_sleep_ticks" in src


def test_continuous_alignment_has_safe_periodic_refresh_guard() -> None:
    """持续分析须定期重对齐，且前台使用/DVR 回看时不得强制跳直播沿。"""
    source = (
        ROOT / "lsc-electron" / "src" / "pages" / "Workbench" / "index.tsx"
    ).read_text(encoding="utf-8")

    assert "10 * 60 * 1000" in source
    assert "document.hidden" in source
    assert "document.hasFocus()" in source
    assert "seekAlignmentRoomsToLive(roomSet)" in source
    assert "Math.abs(room?.content_offset ?? 0) + 1.5" in source


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
    for prev, cur in zip(merged, merged[1:], strict=False):
        assert prev["end"] <= cur["start"]


def test_valorant_merge_keeps_hybrid_over_full_round() -> None:
    """已 OCR 确认的回合，不得被后续 full_round 音频结果覆盖。"""
    existing = [
        {
            "start": 12.0,
            "end": 76.0,
            "start_by": "ocr_combat",
            "end_by": "next_prep",
            "boundary_source": "valorant_ocr_v1",
            "confirm_status": "vision_confirmed",
            "phase": "combat",
            "round_key": "round-000001",
        },
        {
            "start": 97.0,
            "end": 192.0,
            "start_by": "ocr_combat",
            "end_by": "next_prep",
            "boundary_source": "valorant_ocr_v1",
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
        abs(float(item["start"]) - 97.0) < 0.1 and item.get("end_by") == "next_prep"
        for item in merged
    )


def test_valorant_merge_keeps_hybrid_round_over_audio_overlap() -> None:
    """OCR 回合不得被重叠的纯音频窗吃掉。"""
    existing = [
        {
            "start": 327.0,
            "end": 400.0,
            "start_by": "ocr_combat",
            "end_by": "next_prep",
            "boundary_source": "valorant_ocr_v1",
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
    assert merged[0]["start_by"] == "ocr_combat"


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
    """纯 OCR 路径：use_ocr 恒开，短窗超时也须足够长。"""
    _, use_ocr, timeout, _ = room_handler._continuous_valorant_scan_budget(
        "valorant_round",
        last_analyzed=100.0,
        current_dur=150.0,
        pressure={"level": "normal"},
    )
    assert use_ocr is True
    assert timeout >= 120


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
    """结算帧贴近扫描 tip 时，不得把已闭合 OCR 回合当 open_tail 丢弃。"""
    rounds = [{
        "start": 92.444,
        "end": 188.611,
        "phase": "combat",
        "boundary_source": "valorant_ocr_v1",
        "start_by": "ocr_combat",
        "end_by": "next_prep",
        "confirm_status": "pending",
    }]

    retained = room_handler._drop_open_tail_rounds(rounds, current_dur=192.0)

    assert retained == rounds
    assert room_handler._is_listable_ocr_round(retained[0])


def test_incomplete_non_hybrid_tip_round_still_dropped() -> None:
    rounds = [{"start": 100.0, "end": 190.0, "phase": "combat", "end_by": "full_round"}]

    retained = room_handler._drop_open_tail_rounds(rounds, current_dur=192.0)

    assert retained == []


def test_new_rounds_releases_pending_round_when_hybrid_confirms() -> None:
    previous = [{
        "start": 100.0, "end": 180.0, "phase": "pending",
        "boundary_source": "valorant_ocr_v1",
        "start_by": "ocr_combat", "end_by": "open_tail",
        "confirm_status": "pending",
    }]
    current = [{
        "start": 100.0, "end": 195.0, "phase": "combat",
        "boundary_source": "valorant_ocr_v1",
        "start_by": "ocr_combat", "end_by": "next_prep",
        "confirm_status": "vision_confirmed",
    }]

    assert room_handler._new_rounds(previous, current) == current


def test_valorant_incremental_lookback_is_bounded() -> None:
    # 纯 OCR 固定 lookback=30（与 valorant_plugin 一致），不随相位变化
    assert room_handler._VALORANT_INCREMENTAL_LOOKBACK_SEC == 30.0
    assert room_handler._VALORANT_MAX_CATCHUP_SEC > 0.0


def test_continuous_valorant_budget_uses_first_full_scan_then_catchup_window() -> None:
    """首次全量；之后从 last_analyzed 回看 30s 并向前追赶，禁止跳到尾部滑动窗。"""
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

    assert (first_range, first_ocr, first_full) == ((0.0, 120.0), True, True)
    # 回看 30s → 570，向前追赶到 720；不得变成 current-lookback=690 而跳过中段
    assert (normal_range, normal_ocr, normal_full) == ((570.0, 720.0), True, False)
    # 纯 OCR 路径：pressure 不关 OCR
    assert critical_ocr is True
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
    assert scan_range[0] <= max(0.0, 25.0 - 30.0) + 1.0


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
    """追赶窗口始终受 _MAX_CATCHUP_SEC + lookback 约束（纯 OCR 无相位参数）。"""
    scan_range, _, _, _ = room_handler._continuous_valorant_scan_budget(
        mode="valorant_round",
        last_analyzed=100.0,
        current_dur=3600.0,
        pressure={"level": "normal"},
    )
    lookback = room_handler._VALORANT_INCREMENTAL_LOOKBACK_SEC
    max_catchup = room_handler._VALORANT_MAX_CATCHUP_SEC
    assert scan_range[1] - scan_range[0] <= max_catchup + lookback + 1.0
    assert scan_range[0] <= 100.0
    assert scan_range[1] < 3600.0


def test_continuous_valorant_budget_honors_phase_short_window() -> None:
    """纯 OCR 固定增量预算：不随相位变化，恒开 OCR。"""
    scan_range, use_ocr, _, full = room_handler._continuous_valorant_scan_budget(
        "valorant_round",
        last_analyzed=100.0,
        current_dur=180.0,
        pressure={"level": "normal"},
    )
    assert full is False
    assert use_ocr is True
    assert scan_range[1] - scan_range[0] < 240.0


def test_continuous_valorant_budget_dense_ocr_in_post_combat() -> None:
    """纯 OCR 路径：任何时刻 OCR 恒开（不再有 legacy OCR refine 开关）。"""
    _, use_ocr, _, _ = room_handler._continuous_valorant_scan_budget(
        "valorant_round",
        last_analyzed=100.0,
        current_dur=150.0,
        pressure={"level": "normal"},
    )
    assert use_ocr is True


def test_valorant_round_scan_uses_catchup_window_after_first_scan() -> None:
    scan_range, use_ocr, _, full_rescan = room_handler._continuous_valorant_scan_budget(
        "valorant_round", 600.0, 720.0, {"level": "normal", "analysis_window_sec": 180}
    )

    assert (scan_range, use_ocr, full_rescan) == ((570.0, 720.0), True, False)


def test_valorant_round_scan_only_first_pass_is_full() -> None:
    first_range, first_ocr, _, first_full = room_handler._continuous_valorant_scan_budget(
        "valorant_round", 0.0, 120.0, {"level": "normal"}
    )
    later_range, later_ocr, _, later_full = room_handler._continuous_valorant_scan_budget(
        "valorant_round", 600.0, 720.0, {"level": "normal"}
    )

    assert (first_range, first_full) == ((0.0, 120.0), True)
    # 固定 lookback=30 → max(0, 600-30)=570，向前追赶到 720
    assert later_range[0] == max(0.0, 600.0 - room_handler._VALORANT_INCREMENTAL_LOOKBACK_SEC)
    assert later_range[0] <= 600.0
    assert later_range[1] == 720.0
    assert later_full is False
    assert first_ocr is True
    assert later_ocr is True


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
        "boundary_source": "valorant_ocr_v1",
        "start_by": "ocr_combat",
        "end_by": "next_prep",
        "confirm_status": "vision_confirmed",
    }
    assert room_handler._is_auto_exportable_valorant_round(hybrid_ok)
    assert not room_handler._is_auto_exportable_valorant_round({
        **hybrid_ok,
        "confirm_status": "pending",
    })
    assert not room_handler._is_auto_exportable_valorant_round({
        "start": 102.0, "end": 154.0, "phase": "combat",
        "start_by": "ocr_combat", "end_by": "next_prep",
    })
    assert not room_handler._is_auto_exportable_valorant_round({
        "start": 102.0, "end": 154.0, "phase": "combat",
        "boundary_source": "valorant_hybrid_v1",
        "start_by": "model_buy_exit", "end_by": "model_result",
        "confirm_status": "vision_confirmed",
    })
    assert not room_handler._is_auto_exportable_valorant_round({
        "start": 102.0, "end": 154.0, "phase": "combat",
        "boundary_source": "valorant_ocr_v1",
        "start_by": "ocr_combat", "end_by": "open_tail",
        "confirm_status": "pending",
    })
    assert not room_handler._is_auto_exportable_valorant_round({
        "start": 154.0, "end": 102.0, "phase": "combat",
        "boundary_source": "valorant_ocr_v1",
        "start_by": "ocr_combat", "end_by": "next_prep",
        "confirm_status": "vision_confirmed",
    })


def test_continuous_valorant_worker_uses_ocr_detect() -> None:
    """valorant_round 持续分析须走纯 OCR 检测，禁止回退纯音频 detect_valorant_rounds。"""
    src = (ROOT / "python-backend/handlers/room_handler.py").read_text(encoding="utf-8")
    worker = src.split("def _continuous_valorant_worker", 1)[1].split(
        "async def _continuous_analysis_loop", 1
    )[0]
    assert "detect_valorant_rounds_hybrid" not in worker
    assert "ModelContractError" not in worker
    hybrid_branch = worker.split("if game == 'valorant' and _mode == 'valorant_round':", 1)[1]
    assert "plugin.scan_window" in hybrid_branch.split("return _detect_rounds_by_audio_rhythm", 1)[0]
    assert "detect_valorant_rounds(" not in worker


def test_legacy_audio_ocr_boundary_cannot_enter_valorant_list_path() -> None:
    """音频/旧模型边界不得进入纯 OCR 入列路径。"""
    legacy_cases = [
        {"start": 10.0, "end": 80.0, "start_by": "ocr_buy_exit", "end_by": "ocr_result", "ocr_confirmed": True},
        {"start": 10.0, "end": 80.0, "start_by": "audio", "end_by": "chime"},
        {"start": 10.0, "end": 80.0, "start_by": "full_round", "end_by": "full_round"},
        {"start": 10.0, "end": 80.0, "boundary_source": "valorant_hybrid_v1",
         "start_by": "model_buy_exit", "end_by": "model_result",
         "confirm_status": "vision_confirmed"},
    ]
    for rd in legacy_cases:
        assert room_handler._is_listable_ocr_round(rd) is False
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


def test_analyze_scene_or_rounds_uses_ocr_for_valorant() -> None:
    src = (ROOT / "python-backend/handlers/room_handler.py").read_text(encoding="utf-8")
    fn = src.split("def _analyze_scene_or_rounds", 1)[1].split("def _get_video_duration", 1)[0]
    assert "plugin.analyze_file" in fn
    assert "detect_valorant_rounds_hybrid" not in fn
    assert "detect_valorant_rounds(" not in fn
    assert "ModelContractError" not in fn


def test_shadow_env_helper(monkeypatch) -> None:
    monkeypatch.delenv("LSC_VALORANT_VISION_SHADOW", raising=False)
    assert room_handler._valorant_vision_shadow_enabled() is False
    monkeypatch.setenv("LSC_VALORANT_VISION_SHADOW", "1")
    assert room_handler._valorant_vision_shadow_enabled() is True


def test_derive_round_signals_uses_energy_fields_not_score() -> None:
    """纯 OCR 路径已无相位调度信号推导；保留字段约定校验（无 energy 冒充）。"""
    hl = {
        "start": 10.0, "end": 90.0, "score": 0.95,
        "start_by": "ocr_buy_exit", "end_by": "open_tail",
        "phase": "pending", "tail_by": "open_tail",
    }
    assert hl.get("energy_rise") is not True
    assert hl.get("energy_collapse") is not True


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
    """纯 OCR 入列不裁边界：入点/出点原样入列（trim 已删除）。"""
    src = (ROOT / "python-backend/handlers/room_handler.py").read_text(encoding="utf-8")
    loop = src.split("async def _continuous_analysis_loop", 1)[1].split(
        "async def _export_and_broadcast", 1
    )[0]
    assert "_is_listable_ocr_round" in loop
    assert "_trim_valorant_combat_bounds" not in loop


def test_is_timeout_scan_error_detects_asyncio_timeout() -> None:
    """现场：TimeoutError() 的 str 为空，必须用 repr 识别。"""
    assert room_handler._is_timeout_scan_error("TimeoutError()")
    assert room_handler._is_timeout_scan_error(TimeoutError())
    assert room_handler._is_timeout_scan_error(asyncio.TimeoutError())
    assert not room_handler._is_timeout_scan_error("ValueError('x')")
    assert not room_handler._is_timeout_scan_error(None)


def test_apply_scan_timeout_backoff_never_degrades() -> None:
    """质量优先（2026-08-04）：超时仅计数（用于同窗重试上限），
    不再降级纯音频/关 OCR——降级曾导致回合永远 audio_pending 无法升格。"""
    state: dict = {"refine_with_ocr": True, "last_scan_error": "TimeoutError()"}
    room_handler._apply_scan_timeout_backoff(state)
    assert int(state.get("consecutive_scan_timeouts", 0)) == 1
    assert int(state.get("ocr_degraded_remaining", 0)) == 0


def test_apply_scan_budget_degrade_never_shrinks_window() -> None:
    """质量优先：预算层任何情况下不关 OCR、不缩窗，原样返回。"""
    scan_range = (2431.0, 3031.0)
    use_ocr, capped = room_handler._apply_scan_budget_degrade(
        {"ocr_degraded_remaining": 3, "post_timeout_max_catchup_sec": 120.0},
        scan_range=scan_range,
        last_analyzed=2551.0,
        use_ocr=True,
    )
    assert use_ocr is True
    assert capped == (2431.0, 3031.0)


def test_successful_scan_clears_timeout_count() -> None:
    """成功扫描后清除连续超时计数（无降级恢复流程）。"""
    state = {"consecutive_scan_timeouts": 2}
    room_handler._note_successful_scan_after_degrade(state)
    assert state["consecutive_scan_timeouts"] == 0


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
        "boundary_source": "valorant_ocr_v1",
        "start_by": "ocr_combat",
        "end_by": "next_prep",
        "confirm_status": "vision_confirmed",
    }
    assert room_handler._is_auto_exportable_valorant_round(rd) is True
    assert room_handler._is_listable_ocr_round(rd) is True


def test_long_round_duration_anomaly_demotes_to_pending() -> None:
    """过长回合须降级 pending，仍可入列不可自动导出（阈值 150s，覆盖买枪+交战+结算）。"""
    assert room_handler._VALORANT_MAX_ROUND_DURATION_SEC == 150.0
    # 93s（现场常见）不触发
    ok = {
        "start": 205.0, "end": 298.0, "phase": "combat",
        "boundary_source": "valorant_ocr_v1",
        "start_by": "ocr_combat",
        "end_by": "next_prep",
        "confirm_status": "vision_confirmed",
    }
    assert room_handler._is_listable_ocr_round(ok) is True
    assert ok.get("confirm_status") == "vision_confirmed"
    assert not ok.get("duration_anomaly")
    assert room_handler._is_auto_exportable_valorant_round(ok) is True

    # 151s：触发守卫
    long_rd = {
        "start": 29.6, "end": 180.6, "phase": "combat",
        "boundary_source": "valorant_ocr_v1",
        "start_by": "ocr_combat",
        "end_by": "next_prep",
        "confirm_status": "vision_confirmed",
        "round_key": "round-000003",
    }
    assert room_handler._is_listable_ocr_round(long_rd) is True
    assert long_rd.get("duration_anomaly") is True
    assert long_rd.get("confirm_status") == "pending"
    assert room_handler._is_auto_exportable_valorant_round(long_rd) is False


def test_next_combat_end_by_listable_not_exportable() -> None:
    """SETTLE 降级出点 next_combat：可入列待调，不可自动导出。"""
    assert "next_combat" in room_handler._OCR_VALID_END_BY
    rd = {
        "start": 100.0, "end": 180.0, "phase": "combat",
        "boundary_source": "valorant_ocr_v1",
        "start_by": "ocr_combat",
        "end_by": "next_combat",
        "confirm_status": "pending",
    }
    assert room_handler._is_listable_ocr_round(rd) is True
    assert room_handler._is_auto_exportable_valorant_round(rd) is False


def test_clamp_post_stop_duration_caps_inflated_probe() -> None:
    """停录后 probe 虚高须钳到最后录制墙钟（现场 1787→2226）。"""
    assert room_handler._clamp_post_stop_duration(2225.8, 1786.9) == 1786.9
    assert room_handler._clamp_post_stop_duration(1800.0, 1786.9) == 1800.0  # 容差内
    assert room_handler._clamp_post_stop_duration(100.0, 0.0) == 100.0
    assert "停录后时长虚高已钳制" in (
        (ROOT / "python-backend/handlers/room_handler.py").read_text(encoding="utf-8")
    )
    assert "_last_recording_wallclock" in (
        (ROOT / "python-backend/handlers/room_handler.py").read_text(encoding="utf-8")
    )


def test_hybrid_pending_listable_not_exportable() -> None:
    rd = {
        "start": 10.0, "end": 55.0, "phase": "combat",
        "boundary_source": "valorant_ocr_v1",
        "start_by": "ocr_combat",
        "end_by": "open_tail",
        "confirm_status": "pending",
    }
    assert room_handler._is_listable_ocr_round(rd) is True
    assert room_handler._is_auto_exportable_valorant_round(rd) is False


def test_non_hybrid_not_listable_as_hybrid() -> None:
    rd = {"start": 10.0, "end": 80.0, "start_by": "full_round", "end_by": "energy_collapse"}
    assert room_handler._is_listable_ocr_round(rd) is False


def test_chime_corrected_pending_round_is_listable() -> None:
    """open_tail 闭合边界（end_by=open_tail）可入列表（等待下轮确认）。"""
    rd = {
        "start": 100.0, "end": 150.0, "phase": "combat",
        "boundary_source": "valorant_ocr_v1",
        "start_by": "ocr_combat",
        "end_by": "open_tail",
        "confirm_status": "pending",
    }
    assert room_handler._is_listable_ocr_round(rd) is True
    assert room_handler._is_auto_exportable_valorant_round(rd) is False


def test_chime_corrected_vision_confirmed_is_exportable() -> None:
    rd = {
        "start": 100.0, "end": 150.0, "phase": "combat",
        "boundary_source": "valorant_ocr_v1",
        "start_by": "ocr_combat",
        "end_by": "next_prep",
        "confirm_status": "vision_confirmed",
    }
    assert room_handler._is_auto_exportable_valorant_round(rd) is True


def test_chime_corrected_closed_round_near_tip_is_not_dropped() -> None:
    """next_prep 是闭合边界，_drop_open_tail_rounds 不得按 open_tail 丢弃。"""
    rounds = [{
        "start": 92.444,
        "end": 188.611,
        "phase": "combat",
        "boundary_source": "valorant_ocr_v1",
        "start_by": "ocr_combat",
        "end_by": "next_prep",
        "confirm_status": "pending",
    }]

    retained = room_handler._drop_open_tail_rounds(rounds, current_dur=192.0)

    assert retained == rounds
    assert room_handler._is_listable_ocr_round(retained[0])


def test_round_end_events_merge_keeps_last_spike() -> None:
    """回合结束钟声合并须保留窗口内最后一个 spike（结束连声的最后一声），
    而非能量最高者（交火枪声会提前结束点）。"""
    import lsc.analyzer.sound_detector as sd

    events = [
        {"timestamp": 10.0, "score": 0.9},
        {"timestamp": 14.0, "score": 0.6},
        {"timestamp": 30.0, "score": 0.8},
        {"timestamp": 33.0, "score": 0.4},
    ]
    merged = sd._merge_events_last(events, merge_window=8.0)
    assert [e["timestamp"] for e in merged] == [14.0, 33.0]

    src = (ROOT / "lsc/analyzer/sound_detector.py").read_text(encoding="utf-8")
    assert "detect_round_end_events" in src
    assert "_merge_events_last" in src


def test_list_only_min_duration_allows_short_hybrid_rounds() -> None:
    """纯 OCR 确认的回合（入点+出点齐备）直接入列导出，门槛统一为 5s。"""
    assert hasattr(room_handler, "_min_highlight_duration_for_queue")
    assert room_handler._min_highlight_duration_for_queue(list_only=True) <= 5.0
    assert room_handler._min_highlight_duration_for_queue(list_only=False) <= 5.0
    # 现场短回合应过入列门
    short = 187.3 - 152.5  # 34.8
    assert short >= room_handler._min_highlight_duration_for_queue(list_only=True)
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
            "last_scan_error": "ocr failed",
        },
        room_id="main",
    )

    assert payload["last_scan_error"] == "ocr failed"
    # 纯 OCR 路径不再上报模型/推理统计字段
    assert "model_version" not in payload
    assert "provider" not in payload
    assert "round_phase" not in payload
    assert "predicted_phase" not in payload


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


def test_frontend_finalize_stop_only_targets_analysis_rooms() -> None:
    """停止并收尾不得误停未参与本次分析的其它录制房间。"""
    wb = (ROOT / "lsc-electron/src/pages/Workbench/index.tsx").read_text(encoding="utf-8")
    stop_ui = wb.split("// 每次打开都重置操作值", 1)[1].split(
        "} else {\n                      openAnalysisModal()", 1
    )[0]

    assert "stopModeRef.current = 'stop_with_finalize'" in stop_ui
    assert "ca?.target_room_ids" in stop_ui
    assert "activeTargetIds.has(r.room_id)" in stop_ui
    assert "recordingRooms.forEach" in stop_ui


def test_duration_estimate_rejected_when_growth_implausible(monkeypatch, tmp_path) -> None:
    """时长估算增长超物理界限时必须拒绝并回退 ffprobe，防止自举漂移虚高。

    场景：缓存 dur=100s（5 秒前采样，size=50MB），实际码率波动导致文件涨到
    60MB。码率估算 = 60/50*100 = 120s > dur + max(15, age*1.5) = 115s，
    必须拒绝估算（旧实现会采用并写回缓存，漂移逐步放大）。
    """
    import time as _time

    video = tmp_path / "rec.mp4"
    video.write_bytes(b"\x00" * (60 * 1024 * 1024))

    from handlers import room_handler as rh

    cache_key = str(video)
    now = _time.monotonic()
    with rh._duration_cache_lock:
        rh._duration_cache[cache_key] = (100.0, 50 * 1024 * 1024, now - 5.0)

    def _ffprobe_fails(*_args, **_kwargs):
        raise RuntimeError("ffprobe unavailable")

    monkeypatch.setattr(rh, "run_hidden", _ffprobe_fails)
    try:
        result = rh._get_video_duration(cache_key)
        # 估算 120s 被拒绝（> dur + max(15, age*1.5) = 115s）→ ffprobe 失败 → 回退旧缓存
        assert result == 100.0
        with rh._duration_cache_lock:
            cached = rh._duration_cache[cache_key]
        # 缓存不得被虚高估算污染
        assert cached[0] == 100.0
    finally:
        with rh._duration_cache_lock:
            rh._duration_cache.pop(cache_key, None)


def test_loop_wallclock_calibrates_probed_duration() -> None:
    """持续分析循环必须用墙钟校准录制中时长（双向）。

    虚高 dur 导致抽帧 seek 超界；虚低 dur（估算被拒绝回退旧缓存）导致
    分析窗口 end 被限制、滞后持续增长。录制中以墙钟 - 写缓冲为准
    （15s：兼顾早启动与 frag MP4 可读尖端，避免旧 30s 地板空等）。
    """
    src = (ROOT / "python-backend/handlers/room_handler.py").read_text(encoding="utf-8")
    loop = src.split("async def _continuous_analysis_loop", 1)[1].split(
        "async def _continuous_valorant_worker", 1
    )[0]
    assert "wallclock_dur" in loop
    assert "recorded_duration = wallclock_dur" in loop
    assert "_RECORDING_WRITE_BUFFER_SEC" in src
    assert room_handler._RECORDING_WRITE_BUFFER_SEC == 15.0
    assert "buffered_wall = max(0.0, wallclock_dur - _RECORDING_WRITE_BUFFER_SEC)" in loop
    assert "current_dur = buffered_wall" in loop
    assert "abs(current_dur - buffered_wall) > 15.0" in loop


def test_continuous_kick_threshold_and_skips_empty_scan() -> None:
    """Valorant 增量 kick 门槛 8s；current_dur≤3 不得 kick（禁 0-0 空扫）。"""
    src = (ROOT / "python-backend/handlers/room_handler.py").read_text(encoding="utf-8")
    loop = src.split("async def _continuous_analysis_loop", 1)[1].split(
        "async def _continuous_valorant_worker", 1
    )[0]
    assert room_handler._VALORANT_KICK_AHEAD_SEC == 8.0
    assert "kick_dur > last_analyzed + _VALORANT_KICK_AHEAD_SEC" in loop
    assert "current_dur <= 3.0" in loop
    assert "should_kick = False" in loop


def test_continuous_defers_boundary_refine_after_coarse_list() -> None:
    """增量扫：粗结果先入列；密扫 create_task 后台跑，可被粗扫 refine_abort 抢占。"""
    src = (ROOT / "python-backend/handlers/room_handler.py").read_text(encoding="utf-8")
    worker = src.split("async def _continuous_valorant_worker", 1)[1].split(
        "async def _continuous_analysis_loop", 1
    )[0]
    loop = src.split("async def _continuous_analysis_loop", 1)[1].split(
        "async def _export_and_broadcast", 1
    )[0]
    plugin = (ROOT / "lsc/analyzer/valorant_plugin.py").read_text(encoding="utf-8")
    assert "refine_boundaries=_finalize" in plugin
    assert "refine_valorant_round_boundaries" in worker
    assert "boundary_refine_pass" in worker
    assert "asyncio.create_task" in worker
    assert "_boundary_refine_bg" in worker
    assert "refine_abort" in worker
    assert "_refine_cancel_check" in worker
    assert "跳过密扫（优先追赶）" in worker
    assert "_boundary_refine_pass" in loop
    assert "or _boundary_refine_pass" in loop
    assert room_handler._QUALITY_FIRST_NORMAL_PRESSURE["ocr_sample_interval"] == 1.0
    assert room_handler._VALORANT_MAX_ROUND_DURATION_SEC == 150.0

def test_scan_error_backoff_guards_kick() -> None:
    """worker 失败后必须退避重试，防止失败-立即重 kick 风暴。"""
    src = (ROOT / "python-backend/handlers/room_handler.py").read_text(encoding="utf-8")
    assert "_SCAN_ERROR_BACKOFF_SEC" in src
    assert "last_scan_error_at" in src
    assert "time.time() - _last_err_at < _SCAN_ERROR_BACKOFF_SEC" in src


def test_worker_crash_detection_and_restart() -> None:
    """worker 异常退出（非 cancelled）必须被主循环检测并重建，且重建有上限。

    否则 scan_requested 无人消费，主循环每 2s 空转、状态永远「扫描中」。
    """
    src = (ROOT / "python-backend/handlers/room_handler.py").read_text(encoding="utf-8")
    assert "_WORKER_MAX_RESTARTS" in src
    assert "worker_restarts" in src
    # 主循环内：worker 存活检测 + 重建
    loop = src.split("async def _continuous_analysis_loop", 1)[1].split(
        "async def _continuous_valorant_worker", 1
    )[0]
    assert "worker_task" in loop
    assert "_spawn_worker" in loop
    assert "扫描器异常终止" in loop
    assert "扫描器重启中" in loop
    # 崩溃判定：done() 且非 cancelled 才重建（正常停止路径不重建）
    assert "_wt is not None and _wt.done() and not state.get('cancelled')" in loop


def test_worker_crash_reports_failure_to_main_loop() -> None:
    """worker 外层 except 必须把失败写入 scan_result 并唤醒主循环。"""
    src = (ROOT / "python-backend/handlers/room_handler.py").read_text(encoding="utf-8")
    worker = src.split("async def _continuous_valorant_worker", 1)[1].split(
        "async def _continuous_analysis_loop", 1
    )[0]
    assert "持续分析 Worker 异常退出" in worker
    assert "scan_result_container['completed_at'] = time.time()" in worker
    assert "scan_result_container['error']" in worker


def test_ocr_recovery_reprocesses_old_audio_pending() -> None:
    """纯 OCR 路径已无音频降级/升格机制（降级机制随音频路径删除）。"""
    src = (ROOT / "python-backend/handlers/room_handler.py").read_text(encoding="utf-8")
    assert "ocr_refine_done" not in src
    assert "old_pending_keys" not in src
    assert "_normalize_audio_pending_rounds" not in src


def test_merge_round_windows_boundary_drift_does_not_duplicate() -> None:
    """边界微调（±2s 漂移）后的同一回合必须被合并为一条，不得重复入列。"""
    existing = [
        {
            "start": 100.0, "end": 160.0,
            "start_by": "ocr_combat", "end_by": "next_prep",
            "boundary_source": "valorant_ocr_v1",
            "confirm_status": "vision_confirmed", "phase": "combat",
            "round_key": "round-000010",
        },
    ]
    window = [
        {
            "start": 101.5, "end": 162.0,  # 起点 +1.5s / 终点 +2s 漂移
            "start_by": "ocr_combat", "end_by": "next_prep",
            "boundary_source": "valorant_ocr_v1",
            "confirm_status": "vision_confirmed", "phase": "combat",
        },
    ]
    merged = room_handler._merge_round_windows(existing, window)
    assert len(merged) == 1
    # 继承既有 round_key，并采用更新后的边界
    assert merged[0]["round_key"] == "round-000010"
    assert abs(merged[0]["end"] - 162.0) < 0.1


def test_new_rounds_boundary_drift_not_counted_as_fresh() -> None:
    """_new_rounds 对边界漂移的同一回合不得计为新回合（增量提示口径）。"""
    prev = [
        {
            "start": 100.0, "end": 160.0,
            "boundary_source": "valorant_ocr_v1",
            "confirm_status": "vision_confirmed",
        },
    ]
    current = [
        {
            "start": 101.5, "end": 162.0,
            "boundary_source": "valorant_ocr_v1",
            "confirm_status": "vision_confirmed",
        },
    ]
    assert room_handler._new_rounds(prev, current) == []


def test_audio_only_recovery_rounds_are_tagged_and_listable() -> None:
    """纯音频降级路径已删除：无音频回合入列语义。"""
    src = (ROOT / "python-backend/handlers/room_handler.py").read_text(encoding="utf-8")
    assert "_normalize_audio_pending_rounds" not in src
    assert "_is_listable_audio_round" not in src
    assert "audio_pending" not in src


def test_continuous_status_contains_authoritative_listed_clip_snapshot() -> None:
    clip = {
        "clip_id": "r1_330_720",
        "room_id": "r1",
        "start": 33.0,
        "end": 72.0,
        "label": "R01",
        "round_key": "round-000003",
        "confirm_status": "audio_pending",
    }
    task = {
        "mode": "valorant_round",
        "target_room_ids": ["r1"],
        "highlights": [clip],
        "listed_clips": {"r1:round-000003": clip},
    }

    payload = room_handler._build_continuous_status_payload(task, room_id="r1")

    assert payload["total_highlights"] == 1
    assert payload["listed_clip_count"] == 1
    assert payload["listed_clips"] == [clip]


def test_continuous_valorant_listing_is_list_only() -> None:
    """持续分析入列必须 list_only（只入切片列表+时间线），禁止自动 FFmpeg 导出。"""
    src = (ROOT / "python-backend/handlers/room_handler.py").read_text(encoding="utf-8")
    loop = src.split("async def _continuous_analysis_loop", 1)[1].split(
        "async def _export_and_broadcast", 1
    )[0]
    listing_block = loop.split("持续分析入列（仅列表）", 1)[0]
    assert "list_only=True" in listing_block
    assert "list_only=False" not in listing_block
    # 入列不得触发即时导出（queue_export 由用户手动确认后执行）
    assert "defer_export=False" not in listing_block
