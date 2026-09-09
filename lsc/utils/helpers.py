"""LSC 工具函数。"""
from __future__ import annotations

import os
import subprocess
import sys

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


def open_in_explorer(path: str) -> None:
    """在文件管理器中打开指定目录。"""
    if not path:
        return
    folder = path if os.path.isdir(path) else os.path.dirname(path)
    if sys.platform == "win32":
        os.startfile(folder)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", folder])
    else:
        subprocess.Popen(["xdg-open", folder])


def probe_duration(video_path: str, ffprobe: str = "ffprobe") -> float:
    """使用 ffprobe 获取视频时长（秒）。"""
    if not os.path.isfile(video_path):
        return 0.0
    try:
        result = subprocess.run(
            [ffprobe, "-v", "quiet", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", video_path],
            capture_output=True, text=True, timeout=10,
            encoding="utf-8", errors="replace",
            creationflags=get_creation_flags(),
        )
        return float(result.stdout.strip())
    except Exception:
        return 0.0


VALID_VIDEO_EXTENSIONS = {
    ".mp4", ".mkv", ".flv", ".ts", ".mov", ".avi", ".webm", ".m4v"
}


def resolve_real_video_path(video_path: str) -> str:
    """当录制文件在停止录制时被重命名（如 _录制中.mp4 -> _至_*.mp4）时，
    自动解析并重定向到最新存在的实际文件路径。"""
    if not video_path:
        return ""
    if os.path.isfile(video_path):
        return video_path

    parent = os.path.dirname(video_path)
    base = os.path.basename(video_path)
    if not parent or not os.path.isdir(parent):
        return video_path

    orig_ext = os.path.splitext(base)[1].lower()

    prefix = ""
    for marker in ("_录制中", "_in_progress"):
        if marker in base:
            prefix = base.split(marker, 1)[0]
            break

    if prefix:
        try:
            candidates: list[tuple[int, float, str]] = []
            for fname in os.listdir(parent):
                ext = os.path.splitext(fname)[1].lower()
                if ext not in VALID_VIDEO_EXTENSIONS:
                    continue
                if fname.startswith(prefix) and (
                    "_至_" in fname or "_to_" in fname or not any(m in fname for m in ("_录制中", "_in_progress"))
                ):
                    candidate = os.path.join(parent, fname)
                    if os.path.isfile(candidate) and os.path.getsize(candidate) > 0:
                        has_range = ("_至_" in fname or "_to_" in fname)
                        matches_ext = (ext == orig_ext) if orig_ext in VALID_VIDEO_EXTENSIONS else True
                        score = (2 if has_range else 0) + (1 if matches_ext else 0)
                        try:
                            mtime = os.path.getmtime(candidate)
                        except OSError:
                            mtime = 0.0
                        candidates.append((score, mtime, candidate))
            if candidates:
                candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
                return candidates[0][2]
        except OSError:
            pass

    return video_path


__all__ = ["fmt_time", "open_in_explorer", "probe_duration", "resolve_real_video_path", "VALID_VIDEO_EXTENSIONS"]
