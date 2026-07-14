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
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        _log.debug("creation flags: CREATE_NO_WINDOW=0x%08x", flags)
        return flags
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


_ENV_WHITELIST = frozenset({
    "PATH", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "TEMP", "TMP",
    "HOME", "SYSTEMROOT", "PATHEXT", "PYTHONUNBUFFERED", "PYTHONPATH",
    "LSC_LOG_DIR", "LSC_BUNDLED_FFMPEG_DIR", "LSC_LOG_LEVEL",
    "LSC_BILIBILI_COOKIES",
    # #49: preserve CUDA-related env vars for multi-GPU NVENC selection
    "CUDA_VISIBLE_DEVICES", "CUDA_DEVICE_ORDER",
    "NVIDIA_VISIBLE_DEVICES", "NVIDIA_DRIVER_CAPABILITIES",
    "CUDA_PATH", "CUDA_BIN_PATH",
})


def build_clean_env(ffmpeg_path: str) -> dict[str, str]:
    """Build a clean environment for launching FFmpeg.

    Uses an environment variable whitelist to avoid polluting the subprocess
    with unnecessary parent-process variables. Prioritizes FFmpeg's directory
    in PATH and removes directories containing competing avcodec DLLs.
    """
    ffmpeg_dir = os.path.dirname(os.path.abspath(ffmpeg_path))

    cached_clean_path = _clean_path_cache.get(ffmpeg_dir)
    if cached_clean_path is None:
        path_dirs = os.environ.get("PATH", "").split(os.pathsep)
        clean_path = []
        for d in path_dirs:
            if d and os.path.isdir(d):
                try:
                    entries = os.listdir(d)
                except OSError:
                    entries = []
                has_avcodec = any(
                    f.startswith("avcodec-") and f.endswith(".dll")
                    for f in entries
                    if os.path.isfile(os.path.join(d, f))
                )
                if has_avcodec and os.path.normcase(d) != os.path.normcase(ffmpeg_dir):
                    _log.debug("Removing conflicting DLL path: %s", d)
                    continue
            clean_path.append(d)
        cached_clean_path = os.pathsep.join(clean_path)
        _clean_path_cache[ffmpeg_dir] = cached_clean_path

    env: dict[str, str] = {}
    for key in _ENV_WHITELIST:
        val = os.environ.get(key)
        if val is not None:
            env[key] = val
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
    _log.debug("prepare_launch: ffmpeg=%s flags=0x%08x cwd=%s", ffmpeg_path, creation_flags, cwd)
    return env, creation_flags, cwd


def hidden_run_kwargs(**extra: object) -> dict[str, object]:
    """Build kwargs for ``subprocess.run`` / ``Popen`` that hide consoles on Windows.

    Only injects ``creationflags`` when non-zero so POSIX callers stay clean.
    """
    out: dict[str, object] = dict(extra)
    flags = get_creation_flags()
    if flags:
        out.setdefault("creationflags", flags)
    return out


def run_hidden(cmd: list[str] | tuple[str, ...], /, **kwargs: object) -> subprocess.CompletedProcess:
    """``subprocess.run`` with Windows ``CREATE_NO_WINDOW`` to avoid CMD flash.

    Continuous analysis fires many short FFmpeg/ffprobe calls; without this flag
    each invocation pops a console and steals focus / causes UI stutter.
    """
    return subprocess.run(cmd, **hidden_run_kwargs(**kwargs))  # type: ignore[arg-type]  # noqa: S603


def set_stream_nonblocking(pipe) -> None:
    """Set a subprocess pipe (stdout/stderr) to non-blocking mode.

    Prevents pipe-buffer deadlock when the reader thread is temporarily
    busy. Uses ``fcntl`` on POSIX; no-op on Windows (threaded readers
    already prevent main-thread blockage there).
    """
    if pipe is None:
        _log.debug("set_stream_nonblocking: pipe is None, skipping")
        return
    try:
        fd = pipe.fileno()
    except (AttributeError, OSError) as exc:
        _log.debug("set_stream_nonblocking: fileno failed: %s", exc)
        return
    if sys.platform == 'win32':
        _log.debug("set_stream_nonblocking: Windows, no-op")
        return
    try:
        import fcntl
        flags = fcntl.fcntl(fd, fcntl.F_GETFL)
        fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
        _log.debug("set_stream_nonblocking: fd=%d set O_NONBLOCK", fd)
    except (OSError, ImportError) as exc:
        _log.debug("set_stream_nonblocking: fcntl failed: %s", exc)


__all__ = [
    "get_creation_flags",
    "clear_dll_directory",
    "build_clean_env",
    "prepare_launch",
    "hidden_run_kwargs",
    "run_hidden",
    "set_stream_nonblocking",
]
