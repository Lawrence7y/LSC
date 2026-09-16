"""持续分析「直播沿优先」策略守卫（P1-5，2026-09-15）。

现场问题：中途开分析时游标从 0 起爬（首窗强制 [0, MIN_CATCHUP]），而增量窗上限只有
`MAX_CATCHUP_SEC=90s/窗`、单窗墙钟常大于窗口媒体（实测 OCR 0.78x 实时）⇒ 净推进可能
为负、backlog 单调增长：**最新回合要等整段历史爬完才出现**（一小时录像要等一小时以上），
用户视角就是「现在的回合一直不来」。

策略：只要「已覆盖到的最右端」落后文件尾超过 `LIVE_FIRST_MAX_TAIL_LAG_SEC`，本窗就让给
直播沿窗口 `[dur - catchup_cap, dur]`，否则继续按游标回填。两个方向都有界且不牺牲完整性：
  * 直播沿滞后 <= 阈值 + 一个窗；
  * 中间缺口不算「已覆盖」，收尾由既有 coverage 缺口补扫逐片扫（`uncovered_ranges_for_state`），
    运行期也会在直播沿已新鲜的周期继续回填。

本文件钉住四条性质：
  1. 阈值/重叠/首窗/无效输入下的窗口边界；
  2. `plan_scan_window` 回写 `live_first` 且不动 `full_rescan`；
  3. room_handler 只在「直播增量期」启用（收尾/停止补扫不插队）；
  4. 插队窗**不推进主游标**、且必须重置 OCR 跨窗状态（否则缺口会被误标已覆盖）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lsc.analyzer.valorant_plugin import (
    LIVE_FIRST_MAX_TAIL_LAG_SEC,
    MAX_CATCHUP_SEC,
    MIN_CATCHUP_SEC,
    ValorantAnalyzerPlugin,
    compute_valorant_scan_budget,
    live_first_scan_window,
)


def _room_handler():
    import handlers.room_handler as room_handler

    return room_handler


def _source() -> str:
    return (
        Path(__file__).resolve().parents[1]
        / "python-backend/handlers/room_handler.py"
    ).read_text(encoding="utf-8")


# --------------------------------------------------------------- 窗口边界


def test_live_first_window_jumps_to_tail_when_lagged() -> None:
    window = live_first_scan_window(
        last_analyzed=600.0,
        current_dur=3600.0,
        tail_lag_sec=1000.0,
        catchup_cap=90.0,
        lookback_sec=8.0,
    )

    assert window == (3510.0, 3600.0)


@pytest.mark.parametrize(
    ("last_analyzed", "current_dur", "tail_lag", "catchup_cap", "lookback"),
    [
        # 滞后不超阈值（含恰好等于）
        (600.0, 3600.0, LIVE_FIRST_MAX_TAIL_LAG_SEC, 90.0, 8.0),
        (600.0, 3600.0, 10.0, 90.0, 8.0),
        (600.0, 3600.0, 0.0, 90.0, 8.0),
        # 读不出滞后
        (600.0, 3600.0, None, 90.0, 8.0),
        # 首窗不插队（先扫文件头基线）
        (0.0, 3600.0, 1000.0, 90.0, 8.0),
        # 常规追赶窗已经够到文件尾：它本身就是直播沿窗
        (3550.0, 3600.0, 1000.0, 90.0, 8.0),
        # 直播沿窗会与游标窗（含 lookback 重叠）相接：没有实质插队空间
        (3505.0, 3600.0, 1000.0, 90.0, 8.0),
        # 文件尾未知
        (600.0, 0.0, 1000.0, 90.0, 8.0),
    ],
)
def test_live_first_window_declines_when_not_worth_it(
    last_analyzed, current_dur, tail_lag, catchup_cap, lookback,
) -> None:
    assert (
        live_first_scan_window(
            last_analyzed=last_analyzed,
            current_dur=current_dur,
            tail_lag_sec=tail_lag,
            catchup_cap=catchup_cap,
            lookback_sec=lookback,
        )
        is None
    )


def test_live_first_window_tolerates_garbage() -> None:
    assert live_first_scan_window(
        last_analyzed="bad",
        current_dur=3600.0,
        tail_lag_sec=1000.0,
        catchup_cap=90.0,
        lookback_sec=8.0,
    ) is None
    assert live_first_scan_window(
        last_analyzed=600.0,
        current_dur=3600.0,
        tail_lag_sec="bad",
        catchup_cap=90.0,
        lookback_sec=8.0,
    ) is None


# --------------------------------------------------------------- 预算与 planner


def test_scan_budget_switches_to_live_first_only_with_tail_lag() -> None:
    kwargs = {
        "throughput_history": [1.0, 1.0, 1.0],
        "scan_cycle_sec": 60.0,
        "lookback_sec": 8.0,
    }

    range_lagged, use_ocr, _timeout, full = compute_valorant_scan_budget(
        "valorant_round", 600.0, 3600.0, None, tail_lag_sec=1000.0, **kwargs,
    )
    assert use_ocr is True
    assert full is False
    assert range_lagged[1] == 3600.0
    assert range_lagged[0] >= 3600.0 - MAX_CATCHUP_SEC - 0.001
    # 直播沿窗不得回退到游标窗（否则没有插队意义）
    assert range_lagged[0] > 608.0

    range_normal, _use_ocr, _t, full_normal = compute_valorant_scan_budget(
        "valorant_round", 600.0, 3600.0, None, tail_lag_sec=None, **kwargs,
    )
    assert full_normal is False
    assert range_normal[0] == 592.0, "无滞后信号时必须走既有增量窗"
    assert range_normal[1] <= 600.0 + MAX_CATCHUP_SEC + 0.001


def test_planner_marks_live_first_and_preserves_full_rescan() -> None:
    plugin = ValorantAnalyzerPlugin()
    state = {
        "mode": "valorant_round",
        "last_analyzed": 600.0,
        "tail_lag_sec": 1000.0,
        "incremental_lookback": 8.0,
        "throughput_history": [1.0, 1.0],
        "scan_cycle_sec": 60.0,
    }

    window = plugin.plan_scan_window(state, 3600.0, {})

    assert state["live_first"] is True
    assert state["full_rescan"] is False
    assert window.end_sec == 3600.0

    state_small = {
        "mode": "valorant_round",
        "last_analyzed": 600.0,
        "tail_lag_sec": 5.0,
        "incremental_lookback": 8.0,
        "throughput_history": [1.0, 1.0],
        "scan_cycle_sec": 60.0,
    }
    plugin.plan_scan_window(state_small, 3600.0, {})
    assert state_small["live_first"] is False


def test_first_window_semantics_unchanged() -> None:
    """首窗仍固定 [0, MIN_CATCHUP]：既有守卫测试固化的语义不得被直播沿优先改写。"""
    scan_range, use_ocr, _timeout, full = compute_valorant_scan_budget(
        "valorant_round", 0.0, 3600.0, None, tail_lag_sec=3000.0,
    )

    assert use_ocr is True
    assert scan_range == (0.0, MIN_CATCHUP_SEC)
    assert full is False


# --------------------------------------------------------------- 已覆盖最右端


def test_tail_lag_uses_coverage_ledger_rightmost_end() -> None:
    rh = _room_handler()

    empty = {"coverage_ranges": []}
    assert rh._continuous_tail_lag_sec(empty, 3600.0) == 3600.0

    ledger = {"coverage_ranges": [[0.0, 45.0], [3510.0, 3600.0]]}
    assert rh._continuous_tail_lag_sec(ledger, 3660.0) == pytest.approx(60.0)

    # 读不出目标时长/状态非法时不下判断（= 不插队）
    assert rh._continuous_tail_lag_sec(ledger, 0.0) is None
    assert rh._continuous_tail_lag_sec(None, 3600.0) is None


def test_noncontiguous_window_resets_ocr_cross_window_state() -> None:
    rh = _room_handler()

    state = {
        "ocr_runtime_state": {
            "last_processed_ts": 3600.0,
            "ocr_fsm": object(),
            "combat_anchor": (12.0, 3400.0),
            "combat_cand_ts": 3399.0,
            "last_timer": 40.0,
            "last_timer_ts": 3600.0,
            "last_raw_timer": 40.0,
            "last_raw_ts": 3600.0,
            "broadcast_pending_rounds": [{"round_key": "round-x"}],
        },
    }

    assert rh._reset_ocr_runtime_for_noncontiguous_window(state, 900.0) is True

    runtime = state["ocr_runtime_state"]
    assert runtime["last_processed_ts"] == 900.0, "回填窗必须能重新处理自己的帧"
    for key in ("ocr_fsm", "combat_anchor", "combat_cand_ts", "last_timer", "last_timer_ts", "last_raw_timer", "last_raw_ts"):
        assert key not in runtime, f"{key} 必须清掉（跨缺口相位连续性无效）"
    assert runtime["broadcast_pending_rounds"], "待审队列不得被重置波及"
    assert state["ocr_runtime_reset_at"] == 900.0

    # 没有 runtime_state 时不得抛异常
    assert rh._reset_ocr_runtime_for_noncontiguous_window({}, 10.0) is False


# --------------------------------------------------------------- 源守卫


def test_room_handler_enables_live_first_only_during_live_incremental() -> None:
    src = _source()

    assert "_LIVE_FIRST_ENV" in src
    assert "def _live_first_enabled()" in src
    assert "_live_first_ok = bool(" in src
    assert "and not (_finalize_pending or _finalize_started)" in src
    assert "and not state.get('stop_tail_scan')" in src
    assert "'tail_lag_sec': _tail_lag_for_plan," in src
    assert "_live_first_enabled()" in src
    assert "_scan_reason = 'live_first'" in src


def test_live_first_window_does_not_advance_cursor_and_resets_ocr_state() -> None:
    src = _source()

    # 插队窗不推进主游标（否则中间缺口会被当成已分析）
    assert "_scan_was_live_first = bool(" in src
    assert "if not _scan_was_live_first:" in src
    assert "scan_result_container['live_first']" in src
    # 双向重置：插队窗本身 + 插队后回到回填的那一窗
    assert "state['_ocr_state_noncontiguous'] = True" in src
    assert "elif state.get('_ocr_state_noncontiguous'):" in src
    assert src.count("_reset_ocr_runtime_for_noncontiguous_window(") >= 3, (
        "helper 定义 + 插队窗 + 回填窗三处调用"
    )
    # 收尾/停止补扫路径必须保留它们自己的重置与定向窗口（不得被本策略改写）
    assert "_rs['last_processed_ts'] = _fin_start" in src
    assert "_STOP_TAIL_WINDOW_CAP_SEC" in src


def test_live_first_telemetry_wired() -> None:
    src = _source()

    assert "'live_first': bool(_scan_reason == 'live_first')," in src
    assert "'tail_lag_sec': (" in src
    assert "_continuous_tasks[room_id]['live_first_scans'] = int(" in src
    assert "持续分析直播沿优先窗完成" in src
