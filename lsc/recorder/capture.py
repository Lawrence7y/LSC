"""FFmpeg-based live stream capture."""
from __future__ import annotations

import contextlib
import os
import subprocess
import sys
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from threading import Thread

from lsc import get_logger
from lsc.config import LscConfig

_log = get_logger(__name__)

_FRIENDLY_STDERR_RULES = (
    ("403", "直播流鉴权失败或链接已过期"),
    ("404", "直播流地址已失效"),
    ("timed out", "连接直播流超时"),
    ("Connection refused", "直播服务器拒绝连接"),
)


def _friendly_ffmpeg_message(exit_code: int, stderr_tail: str) -> str:
    """Map FFmpeg exit code + stderr to a user-friendly Chinese message."""
    haystack = stderr_tail.lower()
    for needle, text in _FRIENDLY_STDERR_RULES:
        if needle.lower() in haystack:
            return f"{text} (code {exit_code})"
    return f"FFmpeg 异常退出 (code {exit_code})"


class CaptureStatus(Enum):
    IDLE = "idle"
    CONNECTING = "connecting"
    RECORDING = "recording"
    PAUSED = "paused"
    STOPPED = "stopped"
    ERROR = "error"


@dataclass
class CaptureResult:
    """Result of a capture operation."""
    success: bool
    output_path: str
    duration_sec: float = 0.0
    file_size_mb: float = 0.0
    error: str = ""


class StreamCapture:
    """FFmpeg-based live stream capture."""

    def __init__(self, config: LscConfig):
        self.config = config
        self.ffmpeg = config.ffmpeg_path
        self._process: subprocess.Popen | None = None
        self._status = CaptureStatus.IDLE
        self._output_path = ""
        self._start_time = 0.0
        self._last_file_size = 0
        self._stall_checks = 0
        self._on_status_change: Callable[[CaptureStatus], None] | None = None
        self._stderr_tail: deque[str] = deque(maxlen=80)
        self._stderr_thread: Thread | None = None

    @property
    def status(self) -> CaptureStatus:
        return self._status

    @property
    def is_recording(self) -> bool:
        return self._status == CaptureStatus.RECORDING

    @property
    def duration(self) -> float:
        if self._start_time <= 0:
            return 0.0
        return time.time() - self._start_time

    def set_status_callback(self, cb: Callable[[CaptureStatus], None]):
        """Set callback for status changes."""
        self._on_status_change = cb

    def _set_status(self, status: CaptureStatus):
        self._status = status
        if self._on_status_change:
            with contextlib.suppress(Exception):
                self._on_status_change(status)

    def _start_stderr_reader(self) -> None:
        """Start a background thread to read FFmpeg stderr into ring buffer."""
        if self._process is None or self._process.stderr is None:
            return

        def _reader() -> None:
            for line in self._process.stderr:
                self._stderr_tail.append(line.rstrip())

        self._stderr_thread = Thread(target=_reader, daemon=True)
        self._stderr_thread.start()

    @property
    def stderr_tail(self) -> str:
        """Return the last stderr lines as a single string."""
        return "\n".join(self._stderr_tail)

    def start(self, url: str, output_path: str, *,
              codec: str = "copy",
              input_args: list[str] | None = None,
              extra_args: list[str] | None = None) -> bool:
        """Start capturing a live stream.

        Args:
            url: Stream URL (m3u8, flv, etc.)
            output_path: Output file path
            codec: Video codec ("copy" for no re-encoding)
            extra_args: Additional FFmpeg arguments

        Returns:
            True if capture started successfully
        """
        if self._status == CaptureStatus.RECORDING:
            _log.warning("Already recording, force-stopping old capture first")
            self._force_kill()
            self._set_status(CaptureStatus.STOPPED)

        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

        cmd = [self.ffmpeg, "-y", "-loglevel", "warning"]

        # Stream reconnection parameters (only for HTTP/HTTPS streams)
        if url.startswith(("http://", "https://")):
            cmd += [
                "-reconnect", "1",
                "-reconnect_streamed", "1",
                "-reconnect_delay_max", "5",
                "-timeout", "30000000",
            ]

        # Input-specific options (e.g. -headers) must come before -i
        if input_args:
            cmd += input_args

        cmd += ["-i", url]
        if codec == "copy":
            cmd += ["-c", "copy"]
        elif codec == "custom":
            pass  # Codec args provided via extra_args
        else:
            cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", "23"]
            cmd += ["-c:a", "aac", "-b:a", "128k"]

        if extra_args:
            cmd += extra_args

        cmd += ["-f", "mp4", "-movflags", "frag_keyframe+empty_moov+faststart", output_path]

        _log.info("Starting capture: %s -> %s", url, output_path)
        self._set_status(CaptureStatus.CONNECTING)

        # Build a clean environment: prioritize FFmpeg's directory in PATH
        # to avoid DLL conflicts from PySide6/Qt bundling older FFmpeg DLLs
        env = os.environ.copy()
        ffmpeg_dir = os.path.dirname(os.path.abspath(self.ffmpeg))
        path_dirs = env.get("PATH", "").split(os.pathsep)
        # Remove directories containing competing avcodec/avformat DLLs
        clean_path = []
        for d in path_dirs:
            if d and os.path.isdir(d):
                has_avcodec = any(f.startswith("avcodec-") and f.endswith(".dll")
                                  for f in os.listdir(d) if os.path.isfile(os.path.join(d, f)))
                if has_avcodec and os.path.normcase(d) != os.path.normcase(ffmpeg_dir):
                    _log.debug("Removing conflicting DLL path: %s", d)
                    continue
            clean_path.append(d)
        env["PATH"] = ffmpeg_dir + os.pathsep + os.pathsep.join(clean_path)

        # Windows: use CREATE_NO_WINDOW to prevent console popups
        creation_flags = 0
        if sys.platform == "win32":
            creation_flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0x08000000)
            # Clear DLL directories added by PySide6/Qt (AddDllDirectory is
            # inherited by child processes and can cause avcodec version conflicts)
            try:
                import ctypes
                ctypes.windll.kernel32.SetDllDirectoryW(None)
            except Exception as exc:
                _log.debug("SetDllDirectoryW failed: %s", exc)

        try:
            self._process = subprocess.Popen(  # noqa: S603
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                cwd=ffmpeg_dir,
                creationflags=creation_flags,
            )
            self._output_path = output_path
            self._start_time = time.time()
            self._last_file_size = 0
            self._stall_checks = 0
            self._stderr_tail.clear()
            self._start_stderr_reader()
            self._set_status(CaptureStatus.RECORDING)
            _log.info("Capture started (PID: %d)", self._process.pid)
            return True
        except FileNotFoundError:
            _log.error("FFmpeg not found: %s", self.ffmpeg)
            self._set_status(CaptureStatus.ERROR)
            return False
        except Exception as exc:
            _log.error("Failed to start capture: %s", exc)
            self._set_status(CaptureStatus.ERROR)
            return False

    def stop(self) -> CaptureResult:
        """Stop the current capture using three-level degradation.

        1. Send 'q' via stdin for graceful FFmpeg shutdown (flushes output)
        2. wait(timeout=5) — give FFmpeg time to finish
        3. terminate() + wait(timeout=3) — SIGTERM equivalent
        4. kill() — force SIGKILL as last resort

        Returns:
            CaptureResult with success status and file info
        """
        if self._status != CaptureStatus.RECORDING or not self._process:
            return CaptureResult(False, "", error="Not recording")

        _log.info("Stopping capture...")

        # Level 1: graceful 'q' command
        try:
            if self._process.poll() is None and self._process.stdin is not None:
                self._process.stdin.write(b"q")
                self._process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            _log.debug("stdin write failed (FFmpeg may have exited): %s", exc)

        # Level 2: wait for graceful exit
        try:
            self._process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            # Level 3: terminate
            _log.debug("FFmpeg didn't exit in 5s, sending terminate")
            self._process.terminate()
            try:
                self._process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                # Level 4: force kill
                _log.warning("FFmpeg didn't terminate in 3s, force killing")
                self._process.kill()
                self._process.wait(timeout=5)
        except Exception as exc:
            _log.warning("Error during stop, force killing: %s", exc)
            if self._process and self._process.poll() is None:
                self._process.kill()
                with contextlib.suppress(Exception):
                    self._process.wait(timeout=5)

        duration = self.duration
        self._process = None
        self._set_status(CaptureStatus.STOPPED)

        if os.path.isfile(self._output_path):
            size_mb = os.path.getsize(self._output_path) / (1024 * 1024)
            _log.info("Capture stopped: %.1fs, %.1fMB", duration, size_mb)
            return CaptureResult(True, self._output_path, duration, size_mb)
        return CaptureResult(False, "", error="Output file not created")

    def is_alive(self) -> bool:
        """Check if the FFmpeg process is still running."""
        if not self._process:
            return False
        return self._process.poll() is None

    def check_and_handle_crash(self) -> int | None:
        """Check if FFmpeg process has crashed. Returns exit code if crashed, None if healthy.

        If crashed, internally cleans up state and sets status to ERROR.
        Safe to call from any thread — no private-member access needed by callers.
        """
        if not self._process:
            return None
        exit_code = self._process.poll()
        if exit_code is not None:
            # Process has exited
            _log.warning("FFmpeg process exited unexpectedly (code=%d)", exit_code)
            self._process = None
            self._set_status(CaptureStatus.ERROR)
            return exit_code
        return None

    def force_cleanup(self) -> None:
        """Force-kill FFmpeg and clean up. Safe to call even after stop().

        Idempotent: calling stop() followed by force_cleanup() will NOT
        double-kill because stop() sets _process to None.
        """
        self._force_kill()

    def _force_kill(self):
        """Force-kill the FFmpeg process if it's still running.

        Uses terminate() before kill() to allow FFmpeg to flush output.
        """
        if self._process:
            try:
                if self._process.poll() is None:
                    self._process.terminate()
                    try:
                        self._process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        self._process.kill()
                        self._process.wait(timeout=5)
                    _log.info("Force-killed FFmpeg process")
            except Exception as exc:
                _log.warning("Error force-killing FFmpeg: %s", exc)
            finally:
                self._process = None

    def check_health(self) -> str:
        """Check capture health. Returns status message or empty if OK."""
        if self._status != CaptureStatus.RECORDING:
            return ""
        if not self.is_alive():
            rc = self._process.returncode if self._process else -1
            self._set_status(CaptureStatus.ERROR)
            return _friendly_ffmpeg_message(rc, self.stderr_tail)
        # Check if file is growing
        if os.path.isfile(self._output_path):
            cur_size = os.path.getsize(self._output_path)
            if cur_size == self._last_file_size and cur_size > 0:
                self._stall_checks += 1
                _log.warning(
                    "Output file not growing, stream may be stalled (checks=%d)",
                    self._stall_checks,
                )
                if self._stall_checks >= 3:
                    return "输出文件长时间未增长，录制可能已卡住"
            else:
                self._stall_checks = 0
            self._last_file_size = cur_size
        return ""

    def pause(self):
        """Pause the capture (not supported on Windows)."""
        if self._status != CaptureStatus.RECORDING:
            return
        _log.info("Pause not supported on Windows")

    def resume(self):
        """Resume the capture."""
        if self._status != CaptureStatus.PAUSED:
            return
        _log.info("Resume not supported on Windows")
