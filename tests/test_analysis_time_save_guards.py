# tests/test_analysis_time_save_guards.py
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HANDLER = (ROOT / "python-backend" / "handlers" / "room_handler.py").read_text(encoding="utf-8")


def test_analysis_export_defines_analysis_time_before_save():
    """_do_analysis_and_export must assign analysis_time before save_analysis_results."""
    assert "analysis_time_sec=analysis_time" in HANDLER
    # 必须在同一函数作用域内有 monotonic 差值赋值（禁止未定义名）
    assert "analysis_time = time.monotonic() - t0" in HANDLER or \
           "analysis_time = time.monotonic()-t0" in HANDLER
    assert "t0 = time.monotonic()" in HANDLER


def test_start_analysis_passes_analysis_time_sec():
    """handle_start_analysis 落盘不得省略 analysis_time_sec。"""
    # 粗粒度：save_analysis_results 调用均应带 analysis_time_sec= 关键字
    import re
    calls = list(re.finditer(r"save_analysis_results\((.*?)\)", HANDLER, re.S))
    assert len(calls) >= 3
    for m in calls:
        args = m.group(1)
        assert "analysis_time_sec" in args, f"missing analysis_time_sec in: {args[:120]}"
