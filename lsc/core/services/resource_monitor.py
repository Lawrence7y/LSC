"""轻量级系统资源采集与压力分级。"""
from __future__ import annotations

import logging
import os
import shutil
from typing import Any

_log = logging.getLogger(__name__)

try:
    import psutil
except ImportError:
    psutil = None


def classify_resource_pressure(stats: dict[str, Any]) -> dict[str, Any]:
    cpu = float(stats.get("cpu_percent", -1))
    memory = float(stats.get("memory_percent", -1))
    if cpu < 0 or memory < 0:
        cpu = memory = 0
    # 仅极端占用 pause，避免 OCR 自身把 CPU 推到 90% 后永久卡死分析。
    if cpu >= 95 or memory >= 95:
        return {
            "level": "critical",
            "analysis_interval_multiplier": 4,
            "pause_analysis": True,
            "degrade_analysis": True,
            "analysis_window_sec": 30,
            "ocr_sample_interval": 5.0,
        }
    if cpu >= 90 or memory >= 90:
        return {
            "level": "critical",
            "analysis_interval_multiplier": 3,
            "pause_analysis": False,
            "degrade_analysis": True,
            "analysis_window_sec": 60,
            "ocr_sample_interval": 4.0,
        }
    if cpu >= 80 or memory >= 80:
        return {
            "level": "pressure",
            "analysis_interval_multiplier": 2,
            "pause_analysis": False,
            "degrade_analysis": True,
            "analysis_window_sec": 120,
            "ocr_sample_interval": 3.0,
        }
    return {
        "level": "normal",
        "analysis_interval_multiplier": 1,
        "pause_analysis": False,
        "degrade_analysis": False,
        "analysis_window_sec": 240,
        "ocr_sample_interval": 2.0,
    }


def collect_system_stats(output_dir: str = "", extra: dict[str, Any] | None = None) -> dict[str, Any]:
    stats: dict[str, Any] = {
        "cpu_percent": -1.0, "memory_percent": -1.0,
        "memory_total_gb": 0.0, "memory_used_gb": 0.0,
        "disk_percent": -1.0, "disk_total_gb": 0.0, "disk_free_gb": 0.0,
    }
    if psutil is not None:
        stats["cpu_percent"] = psutil.cpu_percent(interval=None)
        memory = psutil.virtual_memory()
        stats.update(memory_percent=round(memory.percent, 1), memory_total_gb=round(memory.total / 1024**3, 1), memory_used_gb=round(memory.used / 1024**3, 1))
    if output_dir:
        try:
            os.makedirs(output_dir, exist_ok=True)
            total, used, free = shutil.disk_usage(output_dir)
            stats.update(disk_percent=round(used / total * 100, 1) if total else 0.0, disk_total_gb=round(total / 1024**3, 1), disk_free_gb=round(free / 1024**3, 1))
        except OSError as exc:
            _log.debug("Disk usage query failed: %s", exc)
    if extra:
        stats.update(extra)
    return stats


def get_resource_pressure() -> dict[str, Any]:
    return classify_resource_pressure(collect_system_stats())
