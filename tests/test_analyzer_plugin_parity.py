from __future__ import annotations

from pathlib import Path

from handlers import room_handler

from lsc.analyzer.registry import get

REQUIRED_STATUS_KEYS = {
    "running",
    "room_id",
    "target_room_ids",
    "mode",
    "analyzed_duration",
    "recorded_duration",
    "confirmed_rounds",
    "pending_rounds",
    "analysis_stage",
    "total_highlights",
    "phase",
    "updated_at",
    "scan_mode",
    "scan_range",
    "scan_timeout",
    "full_rescan",
    "refine_with_ocr",
    "progress",
    "scan_phase",
    "scan_reason",
    "scan_elapsed_sec",
    "scan_running",
    "valorant_profile",
    "finalizing",
    "completed",
    "status",
    "analysis_lag_sec",
    "last_scan_error",
}

REQUIRED_HIGHLIGHTS_KEYS = {
    "room_id",
    "main_room_id",
    "target_room_ids",
    "highlights",
    "new_count",
    "total",
    "mapped_highlights_by_room",
    "mapping_fallback",
    "error",
}


def test_valorant_analyze_file_matches_ocr_direct(monkeypatch, tmp_path):
    fixed = [{"start": 1.0, "end": 10.0, "title": "R1"}]
    monkeypatch.setattr(
        "lsc.analyzer.valorant_ocr_rounds.detect_valorant_rounds_ocr",
        lambda *a, **k: fixed,
    )
    out = get("valorant").analyze_file(str(tmp_path / "x.mp4"))
    assert out == fixed


def test_valorant_analyze_file_cancel_returns_none(tmp_path):
    out = get("valorant").analyze_file(
        str(tmp_path / "x.mp4"),
        cancel_check=lambda: True,
    )
    assert out is None


def test_generic_analyze_file_delegates_scene(monkeypatch, tmp_path):
    fixed = [{"start": 0.0, "end": 5.0, "score": 0.5}]
    monkeypatch.setattr(
        "lsc.analyzer.scene_analysis.run_scene_analysis",
        lambda *a, **k: fixed,
    )
    out = get("generic").analyze_file(str(tmp_path / "x.mp4"), options={"threshold": 0.2})
    assert out == fixed


def test_plan_scan_window_matches_budget_helper():
    state = {
        "mode": "valorant_round",
        "last_analyzed": 120.0,
        "tick_count": 3,
    }
    pressure = {"analysis_window_sec": 60.0}
    window = get("valorant").plan_scan_window(dict(state), 200.0, pressure)
    scan_range, use_ocr, timeout, full = room_handler._continuous_valorant_scan_budget(
        "valorant_round",
        120.0,
        200.0,
        pressure,
    )
    assert (window.start_sec, window.end_sec) == scan_range
    assert window.use_ocr is use_ocr
    assert int(window.timeout_sec) == timeout
    assert state.get("full_rescan") is None  # plan mutates copy
    assert full is False


def test_adaptive_catchup_floor_is_45s():
    """有吞吐历史时 catchup 下限 45s（旧 120s 过大，落后时难收敛到 ≤30s 滞后）。"""
    from lsc.analyzer import valorant_plugin as vp

    assert vp.MIN_CATCHUP_SEC == 45.0
    # 吞吐很好时仍不低于下限
    assert vp._adaptive_catchup_cap([3.0, 3.0, 3.0], kick_interval=5.0) == 45.0
    # 无历史沿用 MAX
    assert vp._adaptive_catchup_cap(None, kick_interval=5.0) == vp.MAX_CATCHUP_SEC
    # 低吞吐时公式仍被下限托住（避免窗缩到无法覆盖 lookback 外新内容）
    assert vp._adaptive_catchup_cap([0.2], kick_interval=5.0) == 45.0


def test_status_payload_keys_stable():
    task = {
        "target_room_ids": ["r1"],
        "mode": "valorant_round",
        "last_analyzed": 10.0,
        "recorded_duration": 20.0,
        "confirmed_rounds": 1,
        "pending_rounds": 0,
        "analysis_stage": "分析中",
        "highlights": [{"start": 1.0, "end": 2.0}],
        "scan_phase": "incremental",
        "scan_range": (0.0, 20.0),
        "scan_timeout": 120,
        "full_rescan": False,
        "refine_with_ocr": False,
        "scan_running": False,
        "round_phase": "combat",
        "round_phase_detail": "交战",
        "valorant_profile": "standard",
        "pending_start": None,
        "predicted_wake_at": None,
        "predicted_phase": None,
        "prediction_detail": None,
        "finalizing": False,
        "completed": False,
        "status": "running",
        "model_version": "v1",
        "provider": "cpu",
        "provider_warning": None,
        "last_scan_error": None,
    }
    payload = room_handler._build_continuous_status_payload(
        task,
        room_id="r1",
        recorded_duration=20.0,
        analysis_stage="分析中",
        phase="running",
        all_highlights=task["highlights"],
        last_analyzed=10.0,
        current_dur=20.0,
    )
    assert set(payload.keys()) >= REQUIRED_STATUS_KEYS


def test_continuous_highlights_payload_keys_shape():
    # Freeze the broadcast field set used by continuous_highlights consumers.
    sample = {
        "room_id": "r1",
        "main_room_id": "r1",
        "target_room_ids": ["r1"],
        "highlights": [{"start": 1.0, "end": 2.0, "score": 0.9}],
        "new_count": 1,
        "total": 1,
        "mapped_highlights_by_room": {"r1": []},
        "mapping_fallback": False,
        "error": None,
    }
    assert set(sample.keys()) >= REQUIRED_HIGHLIGHTS_KEYS
    src = Path(__file__).resolve().parents[1] / "python-backend/handlers/room_handler.py"
    text = src.read_text(encoding="utf-8")
    block = text.split("'type': 'continuous_highlights'", 1)[1].split("'data': {", 1)[1]
    block = block.split("},", 1)[0]
    for key in REQUIRED_HIGHLIGHTS_KEYS:
        assert f"'{key}'" in block or f'"{key}"' in block


def test_handler_scan_budget_reexport_parity():
    a = room_handler._continuous_valorant_scan_budget(
        "valorant_round", 0.0, 90.0, {}
    )
    from lsc.analyzer.valorant_plugin import compute_valorant_scan_budget

    b = compute_valorant_scan_budget(
        mode="valorant_round",
        last_analyzed=0.0,
        current_dur=90.0,
        pressure={},
    )
    assert a == b
