"""Cross-platform subprocess launcher.

Abstracts Windows-specific concerns (CREATE_NO_WINDOW flag, DLL directory
cleanup) behind a small API so that callers don't need ``sys.platform``
checks scattered throughout the codebase.
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys

_log = logging.getLogger(__name__)

_IS_WINDOWS = sys.platform == "win32"

# Cache for the cleaned PATH suffix (everything except the FFmpeg directory).
# Keyed by the FFmpeg directory since that's what determines conflict checks.
_clean_path_cache: dict[str, str] = {}


def get_creation_flags() -> int:
    """Return platform-appropriate creation flags for subprocess.

    On Windows, returns CREATE_NO_WINDOW to prevent console popups.
    On other platforms, returns 0.
    """
    if _IS_WINDOWS:
        return getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    return 0


def clear_dll_directory() -> None:
    """Clear DLL directories added by PySide6/Qt on Windows.

    AddDllDirectory is inherited by child processes and can cause avcodec
    version conflicts when launching FFmpeg. This clears the directory
    so the child process only uses FFmpeg's own DLLs.

    No-op on non-Windows platforms.
    """
    if not _IS_WINDOWS:
        return
    try:
        import ctypes
        ctypes.windll.kernel32.SetDllDirectoryW(None)
    except Exception as exc:
        _log.debug("SetDllDirectoryW failed: %s", exc)


def build_clean_env(ffmpeg_path: str) -> dict[str, str]:
    """Build a clean environment for launching FFmpeg.

    Prioritizes FFmpeg's directory in PATH and removes directories
    containing competing avcodec DLLs to avoid version conflicts.
    """
    env = os.environ.copy()
    ffmpeg_dir = os.path.dirname(os.path.abspath(ffmpeg_path))

    cached_clean_path = _clean_path_cache.get(ffmpeg_dir)
    if cached_clean_path is None:
        path_dirs = env.get("PATH", "").split(os.pathsep)
        clean_path = []
        for d in path_dirs:
            if d and os.path.isdir(d):
                has_avcodec = any(
                    f.startswith("avcodec-") and f.endswith(".dll")
                    for f in os.listdir(d)
                    if os.path.isfile(os.path.join(d, f))
                )
                if has_avcodec and os.path.normcase(d) != os.path.normcase(ffmpeg_dir):
                    _log.debug("Removing conflicting DLL path: %s", d)
                    continue
            clean_path.append(d)
        cached_clean_path = os.pathsep.join(clean_path)
        _clean_path_cache[ffmpeg_dir] = cached_clean_path

    env["PATH"] = ffmpeg_dir + os.pathsep + cached_clean_path
    return env


def prepare_launch(ffmpeg_path: str) -> tuple[dict[str, str], int, str | None]:
    """Prepare environment, flags, and cwd for launching a subprocess.

    Returns
    -------
    tuple[dict, int, str | None]
        (env, creation_flags, cwd) — pass these to subprocess.Popen.
    """
    env = build_clean_env(ffmpeg_path)
    creation_flags = get_creation_flags()
    clear_dll_directory()
    cwd = os.path.dirname(os.path.abspath(ffmpeg_path)) or None
    return env, creation_flags, cwd


__all__ = [
    "get_creation_flags",
    "clear_dll_directory",
    "build_clean_env",
    "prepare_launch",
]
