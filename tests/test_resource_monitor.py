from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_resource_pressure_policy_levels_and_analysis_actions() -> None:
    from lsc.core.services.resource_monitor import classify_resource_pressure

    normal = classify_resource_pressure({"cpu_percent": 42.0, "memory_percent": 55.0})
    pressure = classify_resource_pressure({"cpu_percent": 82.0, "memory_percent": 70.0})
    critical = classify_resource_pressure({"cpu_percent": 92.0, "memory_percent": 88.0})
    extreme = classify_resource_pressure({"cpu_percent": 97.0, "memory_percent": 88.0})
    unknown = classify_resource_pressure({"cpu_percent": -1.0, "memory_percent": -1.0})

    assert normal["level"] == "normal"
    assert normal["analysis_interval_multiplier"] == 1
    assert normal["pause_analysis"] is False

    assert pressure["level"] == "pressure"
    assert pressure["analysis_interval_multiplier"] > normal["analysis_interval_multiplier"]
    assert pressure["pause_analysis"] is False

    # 90%+：降载（拉长 OCR 间隔）但不立刻 pause，避免 OCR 自触发死锁
    assert critical["level"] == "critical"
    assert critical["pause_analysis"] is False
    assert critical["degrade_analysis"] is True
    assert float(critical.get("ocr_sample_interval", 0)) >= 4.0
    assert critical["analysis_window_sec"] < pressure["analysis_window_sec"]

    # 仅极端占用才 pause
    assert extreme["pause_analysis"] is True

    assert unknown["level"] == "normal"
    assert unknown["pause_analysis"] is False


def test_resource_monitor_reports_shared_ingest_counts() -> None:
    from lsc.core.services.resource_monitor import collect_system_stats

    stats = collect_system_stats(extra={
        "shared_ingests": 2,
        "recording_sinks": 2,
        "preview_subscribers": 3,
        "legacy_mse_streamers": 1,
    })

    assert stats["shared_ingests"] == 2
    assert stats["recording_sinks"] == 2
    assert stats["preview_subscribers"] == 3
    assert stats["legacy_mse_streamers"] == 1


def test_room_handler_system_stats_include_ingest_diagnostics() -> None:
    source = (ROOT / "python-backend/handlers/room_handler.py").read_text(encoding="utf-8")

    assert "def _ingest_diagnostics()" in source
    assert "collect_system_stats(output_dir, extra=_ingest_diagnostics())" in source
