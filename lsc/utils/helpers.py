"""LSC 工具函数。"""
from __future__ import annotations

import os
import subprocess

from lsc.utils.process_launcher import get_creation_flags


def fmt_time(seconds: float) -> str:
    """将秒数格式化为 HH:MM:SS 或 MM:SS。"""
    if seconds < 0:
        seconds = 0
    total = int(seconds)
    h = total // 3600
    m = (total % 3600) // 60
    s = total % 60
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def probe_duration(video_path: str, ffprobe: str = "ffprobe") -> float:
    """使用 ffprobe 获取视频时长（秒）。"""
    if not os.path.isfile(video_path):
        return 0.0
    try:
        result = subprocess.run(
            [ffprobe, "-v", "quiet", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", video_path],
            capture_output=True, text=True, timeout=10,
            creationflags=get_creation_flags(),
        )
        return float(result.stdout.strip())
    except Exception:
        return 0.0


__all__ = ["fmt_time", "probe_duration"]
