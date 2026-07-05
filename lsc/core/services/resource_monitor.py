"""系统资源监控模块。

通过 psutil 采集 CPU、内存、磁盘使用率，供后端心跳周期性调用，
并通过 WebSocket 广播到前端。
"""
from __future__ import annotations

import logging
import os
import shutil

_log = logging.getLogger(__name__)

try:
    import psutil
    _HAS_PSUTIL = True
except ImportError:
    _HAS_PSUTIL = False
    _log.warning("psutil not available, system resource monitoring disabled")


def collect_system_stats(output_dir: str = "") -> dict:
    """采集系统资源快照。

    Parameters
    ----------
    output_dir : str
        录制输出目录路径，用于查询磁盘使用率。

    Returns
    -------
    dict
        包含 cpu_percent, memory_percent, memory_total_gb,
        memory_used_gb, disk_percent, disk_total_gb, disk_free_gb 的字典。
        若 psutil 不可用则返回 cpu_percent=-1 等哨兵值。
    """
    if not _HAS_PSUTIL:
        return {
            "cpu_percent": -1.0,
            "memory_percent": -1.0,
            "memory_total_gb": 0.0,
            "memory_used_gb": 0.0,
            "disk_percent": -1.0,
            "disk_total_gb": 0.0,
            "disk_free_gb": 0.0,
        }

    cpu_percent = psutil.cpu_percent(interval=None)

    mem = psutil.virtual_memory()
    memory_total_gb = round(mem.total / (1024 ** 3), 1)
    memory_used_gb = round(mem.used / (1024 ** 3), 1)
    memory_percent = round(mem.percent, 1)

    disk_percent = -1.0
    disk_total_gb = 0.0
    disk_free_gb = 0.0
    if output_dir:
        try:
            if not os.path.exists(output_dir):
                os.makedirs(output_dir, exist_ok=True)
            total, used, free = shutil.disk_usage(output_dir)
            disk_total_gb = round(total / (1024 ** 3), 1)
            disk_free_gb = round(free / (1024 ** 3), 1)
            disk_percent = round(used / total * 100, 1) if total > 0 else 0.0
        except Exception as exc:
            _log.debug("Disk usage query failed: %s", exc)

    return {
        "cpu_percent": cpu_percent,
        "memory_percent": memory_percent,
        "memory_total_gb": memory_total_gb,
        "memory_used_gb": memory_used_gb,
        "disk_percent": disk_percent,
        "disk_total_gb": disk_total_gb,
        "disk_free_gb": disk_free_gb,
    }
