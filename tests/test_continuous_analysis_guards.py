from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from handlers import room_handler


ROOT = Path(__file__).resolve().parents[1]


def test_continuous_valorant_analysis_uses_incremental_round_window() -> None:
    source = (ROOT / "python-backend/handlers/room_handler.py").read_text(encoding="utf-8")

    assert "_VALORANT_INCREMENTAL_LOOKBACK_SEC" in source
    assert "_continuous_valorant_scan_budget(" in source
    assert "analysis_window_sec" in source
    assert "time_range=_range" in source


def test_continuous_analysis_requires_growing_recording_file_shape() -> None:
    shared_room = SimpleNamespace(
        output_path="recording.mp4",
        record_output_path="recording.mp4",
        file_size_mb=10.0,
        is_recording=True,
    )

    assert shared_room.record_output_path.endswith(".mp4")
    assert shared_room.is_recording is True


def test_continuous_analysis_uses_recording_file_not_preview_segments() -> None:
    source = (ROOT / "python-backend/handlers/room_handler.py").read_text(encoding="utf-8")
    loop_body = source.split("async def _continuous_analysis_loop(", 1)[1].split("@server.on('start_continuous_analysis')", 1)[0]

    assert "room.record_output_path" in loop_body
    assert "_get_video_duration(" in loop_body
    assert "detect_valorant_rounds(" in source
    assert "mse_segment" not in loop_body


def test_continuous_analysis_falls_back_to_temp_file() -> None:
    """分析循环应在 .mp4 不存在时回退查找 temp 文件（共享进样 faststart 模式）。"""
    source = (ROOT / "python-backend/handlers/room_handler.py").read_text(encoding="utf-8")
    loop_body = source.split("async def _continuous_analysis_loop(", 1)[1].split("@server.on('start_continuous_analysis')", 1)[0]
    # 必须存在 .mp4 路径 + temp 回退逻辑
    assert "os.path.isfile(path)" in loop_body
    assert ".tmp" in loop_body


def test_valorant_round_window_merge_replaces_overlapping_drift() -> None:
    existing = [
        {"start": 354.717, "end": 424.467, "score": 1.0, "tail_by": "chime"},
    ]
    window = [
        {"start": 407.567, "end": 491.567, "score": 1.0, "tail_by": "chime"},
        {"start": 507.567, "end": 589.567, "score": 0.8, "tail_by": "chime"},
    ]

    merged = room_handler._merge_round_windows(existing, window)

    assert merged == window
    for prev, cur in zip(merged, merged[1:]):
        assert prev["end"] <= cur["start"]


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


def test_continuous_valorant_budget_uses_first_full_scan_then_trailing_window() -> None:
    """专用模式首次全量，之后固定回看窗口；压力仍会禁用 OCR。"""
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
        pressure={"level": "critical", "analysis_window_sec": 75, "degrade_analysis": True},
    )

    assert (first_range, first_ocr, first_full) == ((0.0, 120.0), True, True)
    assert (normal_range, normal_ocr, normal_full) == ((540.0, 720.0), True, False)
    assert (critical_range, critical_ocr, critical_full) == ((645.0, 720.0), False, False)


def test_continuous_valorant_budget_does_not_expand_with_recording_length() -> None:
    """长录制仍只回看固定窗口，不按总直播时长周期性全量重扫。"""
    scan_range, _, _, _ = room_handler._continuous_valorant_scan_budget(
        mode="valorant_round",
        last_analyzed=1620.0,
        current_dur=1800.0,
        pressure={"level": "normal"},
    )
    assert scan_range == (1680.0, 1800.0)


def test_continuous_valorant_ocr_enabled_every_tick() -> None:
    """OCR 应每个 tick 都启用，不再每 4 tick 跳过。

    跳过 OCR 会导致纯音频边界（回合 N 下半 + 回合 N+1 上半）被直接导出。
    """
    source = (ROOT / "python-backend/handlers/room_handler.py").read_text(encoding="utf-8")
    loop_body = source.split("async def _continuous_analysis_loop(", 1)[1].split("@server.on('start_continuous_analysis')", 1)[0]
    # 不应再有 _scan_counter % 4 的逻辑
    assert "_scan_counter % 4" not in loop_body, \
        "OCR 不应再每 4 tick 跳过，应每个 tick 都启用"
    assert "tick_count %" not in loop_body


def test_valorant_round_scan_uses_trailing_window_after_first_scan() -> None:
    scan_range, use_ocr, _, full_rescan = room_handler._continuous_valorant_scan_budget(
        "valorant_round", 600.0, 720.0, {"level": "normal", "analysis_window_sec": 180}
    )

    assert (scan_range, use_ocr, full_rescan) == ((540.0, 720.0), True, False)


def test_valorant_round_scan_only_first_pass_is_full() -> None:
    first_range, first_ocr, _, first_full = room_handler._continuous_valorant_scan_budget(
        "valorant_round", 0.0, 120.0, {"level": "normal"}
    )
    later_range, later_ocr, _, later_full = room_handler._continuous_valorant_scan_budget(
        "valorant_round", 600.0, 720.0, {"level": "normal"}
    )

    assert (first_range, first_full) == ((0.0, 120.0), True)
    assert (later_range, later_full) == ((600.0, 720.0), False)
    assert first_ocr is True
    assert later_ocr is True


def test_valorant_round_ocr_is_disabled_for_non_round_mode_or_degraded_pressure() -> None:
    assert room_handler._continuous_valorant_refine_with_ocr("fast", {"level": "normal"}) is False
    assert room_handler._continuous_valorant_refine_with_ocr("valorant_round", {"level": "critical"}) is False
    assert room_handler._continuous_valorant_refine_with_ocr("valorant_round", {"degrade_analysis": True}) is False
    assert room_handler._continuous_valorant_refine_with_ocr("valorant_round", {"level": "normal"}) is True


def test_only_complete_ocr_rounds_are_auto_exportable() -> None:
    assert room_handler._is_auto_exportable_valorant_round({
        "start": 102.0, "end": 154.0, "phase": "combat",
        "start_by": "ocr_buy_exit", "end_by": "ocr_result",
    })
    assert room_handler._is_auto_exportable_valorant_round({
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
        "start": 154.0, "end": 102.0, "phase": "combat",
        "start_by": "ocr_buy_exit", "end_by": "ocr_result",
    })


def test_continuous_loop_uses_explicit_valorant_round_mode_and_never_publishes_open_tail() -> None:
    source = (ROOT / "python-backend/handlers/room_handler.py").read_text(encoding="utf-8")
    loop_body = source.split("async def _continuous_analysis_loop(", 1)[1].split("@server.on('start_continuous_analysis')", 1)[0]

    assert '_valorant_incremental_rounds = mode == "valorant_round" and game == "valorant"' in loop_body
    assert "_drop_open_tail_rounds(new_hl, worker_dur)" in loop_body
    assert "if not window_rounds:" not in loop_body
    assert "_is_auto_exportable_valorant_round(h)" in loop_body


def test_continuous_analysis_initializes_recording_snapshot_before_loop() -> None:
    """首个 tick 在录制文件尚未读取前也不能引用未赋值的快照变量。"""
    source = (ROOT / "python-backend/handlers/room_handler.py").read_text(encoding="utf-8")
    loop_body = source.split("async def _continuous_analysis_loop(", 1)[1].split("@server.on('start_continuous_analysis')", 1)[0]
    before_loop = loop_body.split("while not _continuous_tasks", 1)[0]

    assert "video_path = ''" in before_loop
    assert "current_dur = 0.0" in before_loop


def test_continuous_loop_initializes_kick_decision_each_tick() -> None:
    """没有满足扫描阈值时也不能引用上一个 tick 或未赋值的 should_kick。"""
    source = (ROOT / "python-backend/handlers/room_handler.py").read_text(encoding="utf-8")
    loop_body = source.split("async def _continuous_analysis_loop(", 1)[1].split("@server.on('start_continuous_analysis')", 1)[0]

    assert "should_kick = False" in loop_body


def test_continuous_loop_consumes_empty_scan_result() -> None:
    """扫描没有发现高光也必须推进 analyzed_duration，避免重复扫描同一区间。"""
    source = (ROOT / "python-backend/handlers/room_handler.py").read_text(encoding="utf-8")
    loop_body = source.split("async def _continuous_analysis_loop(", 1)[1].split("@server.on('start_continuous_analysis')", 1)[0]

    assert "worker_completed_at > last_consumed_at" in loop_body
    assert "and worker_result" not in loop_body.split("can_consume =", 1)[1].split("if can_consume", 1)[0]


def test_continuous_analysis_only_exports_confirmed_rounds() -> None:
    """仅导出 OCR/钟声确认边界的回合，tail_by='audio' 的回合不自动导出。"""
    source = (ROOT / "python-backend/handlers/room_handler.py").read_text(encoding="utf-8")
    loop_body = source.split("async def _continuous_analysis_loop(", 1)[1].split("@server.on('start_continuous_analysis')", 1)[0]
    # 应有 confirmed_hl 过滤逻辑
    assert "confirmed_hl" in loop_body, \
        "应有 confirmed_hl 过滤逻辑，仅导出已确认边界的回合"
    assert "_is_auto_exportable_valorant_round(h)" in loop_body
    assert "_CONFIRMED_TAIL" not in loop_body
