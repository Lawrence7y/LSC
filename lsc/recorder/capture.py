"""FFmpeg-based live stream capture."""
from __future__ import annotations

import os
import subprocess
import time
from collections import deque
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from enum import Enum
from threading import Lock

from lsc import get_logger
from lsc.config import LscConfig

_log = get_logger(__name__)

_FRIENDLY_STDERR_RULES = (
    ("403", "直播流鉴权失败或链接已过期"),
    ("404", "直播流地址已失效"),
    ("timed out", "连接直播流超时"),
    ("Connection refused", "直播服务器拒绝连接"),
    ("Connection reset", "网络连接被重置"),
    ("No space left", "磁盘空间不足"),
    ("permission denied", "文件写入权限不足"),
    ("Invalid data found", "直播流数据异常"),
    ("Server returned 5", "服务器返回错误"),
    ("HTTP error", "HTTP 请求失败"),
    ("Cookie", "Cookie 无效或已过期"),
)

STARTUP_PROBE_TIMEOUT_SEC = 5.0
STARTUP_PROBE_INTERVAL_SEC = 0.1


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
    is_valid: bool = True
    validation_error: str = ""


def validate_recording(output_path: str, min_size_mb: float = 0.1) -> tuple[bool, str]:
    """Validate a recorded file's integrity.

    Args:
        output_path: Path to the recorded file.
        min_size_mb: Minimum file size in MB to be considered valid.

    Returns:
        Tuple of (is_valid, error_message).
    """
    import os

    if not output_path:
        return False, "输出路径为空"

    if not os.path.isfile(output_path):
        return False, f"文件不存在: {output_path}"

    file_size = os.path.getsize(output_path)
    file_size_mb = file_size / (1024 * 1024)

    if file_size_mb < min_size_mb:
        return False, f"文件太小 ({file_size_mb:.2f} MB)，可能录制失败"

    # Check if file is readable
    try:
        with open(output_path, 'rb') as f:
            # Read first few bytes to check file header
            header = f.read(12)
            if len(header) < 12:
                return False, "文件头不完整"

            # Check for common video file signatures
            # MP4: ftyp at offset 4
            if header[4:8] != b'ftyp':
                # Not MP4, check if it's a valid video format
                # FLV starts with 'FLV'
                # MKV starts with 0x1A45DFA3
                if not (header[:3] == b'FLV' or header[:4] == b'\x1A\x45\xDF\xA3'):
                    return False, "文件格式异常，可能不是有效的视频文件"
    except IOError as e:
        return False, f"文件读取失败: {e}"

    return True, ""


class StreamCapture:
    """FFmpeg-based live stream capture."""

    # Shared thread pool for stderr readers across all capture instances.
    # Limiting workers to 2 keeps thread stack usage low even when many
    # rooms are recording simultaneously.
    _stderr_executor: ThreadPoolExecutor | None = None
    _stderr_executor_users: int = 0
    _stderr_executor_lock: Lock = Lock()

    def __init__(self, config: LscConfig):
        self.config = config
        self.ffmpeg = config.ffmpeg_path
        self._lock = Lock()
        self._process: subprocess.Popen | None = None
        self._status = CaptureStatus.IDLE
        self._output_path = ""
        self._start_time = 0.0
        self._last_file_size = 0
        self._stall_checks = 0
        self._on_status_change: Callable[[CaptureStatus], None] | None = None
        self._stderr_tail: deque[str] = deque(maxlen=20)
        self._stderr_future: "Future[None] | None" = None
        self._stderr_released = False
        self._last_error: str = ""

    @property
    def status(self) -> CaptureStatus:
        with self._lock:
            return self._status

    @property
    def last_error(self) -> str:
        """Last error message from a failed start/stop operation."""
        return self._last_error

    @property
    def is_recording(self) -> bool:
        with self._lock:
            return self._status == CaptureStatus.RECORDING

    @property
    def duration(self) -> float:
        with self._lock:
            if self._start_time <= 0:
                return 0.0
            return time.time() - self._start_time

    def set_status_callback(self, cb: Callable[[CaptureStatus], None]):
        """Set callback for status changes."""
        self._on_status_change = cb

    def _set_status(self, status: CaptureStatus):
        with self._lock:
            self._status = status
        if self._on_status_change:
            try:
                self._on_status_change(status)
            except Exception as exc:
                _log.warning("status callback raised: status=%s err=%s", status, exc)

    def _start_stderr_reader(self) -> None:
        """Start a background thread to read FFmpeg stderr into ring buffer.

        Uses a shared thread pool so that many concurrent captures do not
        create one thread each (each Python thread costs ~8 MB of stack on
        Windows).
        """
        if self._process is None or self._process.stderr is None:
            return

        proc = self._process
        executor = self._acquire_stderr_executor()
        self._stderr_future = executor.submit(self._stderr_reader_loop, proc)

    def _stderr_reader_loop(self, proc: subprocess.Popen) -> None:
        """Read lines from proc.stderr until the pipe closes."""
        try:
            for line in proc.stderr:
                self._stderr_tail.append(line.rstrip())
        except Exception:
            # Pipe closed or process already gone; reader exits cleanly.
            pass

    @classmethod
    def _acquire_stderr_executor(cls) -> ThreadPoolExecutor:
        with cls._stderr_executor_lock:
            if cls._stderr_executor is None:
                cls._stderr_executor = ThreadPoolExecutor(
                    max_workers=4, thread_name_prefix="lsc-stderr"
                )
            cls._stderr_executor_users += 1
            return cls._stderr_executor

    @classmethod
    def _release_stderr_executor(cls) -> None:
        with cls._stderr_executor_lock:
            cls._stderr_executor_users -= 1
            if cls._stderr_executor_users <= 0 and cls._stderr_executor is not None:
                cls._stderr_executor.shutdown(wait=False)
                cls._stderr_executor = None

    def _release_stderr_executor_once(self) -> None:
        """确保每个实例只释放一次共享 stderr executor，防止并发路径重复释放。"""
        with self._lock:
            if self._stderr_released:
                return
            self._stderr_released = True
        self._release_stderr_executor()

    @property
    def stderr_tail(self) -> str:
        """Return the last stderr lines as a single string."""
        return "\n".join(self._stderr_tail)

    def _output_has_started(self) -> bool:
        """Return True once FFmpeg has written any media bytes."""
        try:
            return os.path.isfile(self._output_path) and os.path.getsize(self._output_path) > 0
        except OSError:
            return False

    def _wait_for_startup_data(self) -> bool:
        """Wait briefly until FFmpeg proves the input stream is producing data."""
        deadline = time.monotonic() + max(0.0, STARTUP_PROBE_TIMEOUT_SEC)
        while True:
            if self._output_has_started():
                return True
            if self._process is not None and self._process.poll() is not None:
                return self._output_has_started()
            if time.monotonic() >= deadline:
                return False
            time.sleep(STARTUP_PROBE_INTERVAL_SEC)

    def _startup_failure_message(self) -> str:
        """Build a user-facing error for a stream that never produced data."""
        proc = self._process
        if proc is not None and proc.poll() is not None:
            return _friendly_ffmpeg_message(proc.returncode or -1, self.stderr_tail)
        return "启动录制失败：没有收到直播数据，请确认直播已开播或链接未过期"

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
        with self._lock:
            already_recording = self._status == CaptureStatus.RECORDING
            self._last_error = ""

        if already_recording:
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

        # Build a clean environment and platform-specific launch flags
        from lsc.utils.process_launcher import prepare_launch
        env, creation_flags, cwd = prepare_launch(self.ffmpeg)

        try:
            proc = subprocess.Popen(  # noqa: S603
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                cwd=cwd,
                creationflags=creation_flags,
            )
            with self._lock:
                self._process = proc
                self._output_path = output_path
                self._start_time = time.time()
                self._last_file_size = 0
                self._stall_checks = 0
                self._stderr_released = False
                self._stderr_tail.clear()
            self._start_stderr_reader()
            if not self._wait_for_startup_data():
                self._last_error = self._startup_failure_message()
                _log.error("Capture startup did not receive stream data: %s", self._last_error)
                self._force_kill()
                self._set_status(CaptureStatus.ERROR)
                return False
            self._set_status(CaptureStatus.RECORDING)
            _log.info("Capture started (PID: %d)", self._process.pid)
            return True
        except FileNotFoundError:
            self._last_error = f"FFmpeg 未找到: {self.ffmpeg}"
            _log.error("FFmpeg not found: %s", self.ffmpeg)
            self._release_stderr_executor_once()
            self._set_status(CaptureStatus.ERROR)
            return False
        except Exception as exc:
            self._last_error = f"启动录制失败: {exc}"
            _log.error("Failed to start capture: %s", exc)
            self._release_stderr_executor_once()
            self._set_status(CaptureStatus.ERROR)
            return False

    def stop(self) -> CaptureResult:
        """Stop the current capture using three-level degradation.

        1. Send 'q' via stdin for graceful FFmpeg shutdown (flushes output)
        2. wait(timeout=5) — give FFmpeg time to finish
        3. terminate() + wait(timeout=3) — SIGTERM equivalent
        4. kill() + final wait(timeout=5) — force SIGKILL as last resort

        The final wait is also bounded: if FFmpeg still refuses to exit,
        we log its PID and release resources without blocking the caller.

        Returns:
            CaptureResult with success status and file info
        """
        with self._lock:
            if self._status != CaptureStatus.RECORDING or not self._process:
                return CaptureResult(False, "", error="Not recording")
            proc = self._process

        _log.info("Stopping capture...")
        orphaned_pid: int | None = None

        # Level 1: graceful 'q' command
        try:
            if proc.poll() is None and proc.stdin is not None:
                proc.stdin.write("q")
                proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            _log.debug("stdin write failed (FFmpeg may have exited): %s", exc)

        # Helper to bound every wait attempt and avoid hanging the caller.
        def _wait_with_deadline(timeout: float) -> bool:
            try:
                proc.wait(timeout=timeout)
                return True
            except subprocess.TimeoutExpired:
                return False
            except Exception as exc:
                _log.warning("Unexpected error waiting for FFmpeg: %s", exc)
                return False

        # Level 2: wait for graceful exit
        if not _wait_with_deadline(5):
            # Level 3: terminate
            _log.debug("FFmpeg didn't exit in 5s, sending terminate")
            try:
                proc.terminate()
            except Exception as exc:
                _log.warning("FFmpeg terminate failed: %s", exc)
            if not _wait_with_deadline(3):
                # Level 4: force kill
                _log.warning("FFmpeg didn't terminate in 3s, force killing")
                try:
                    proc.kill()
                except Exception as exc:
                    _log.warning("FFmpeg kill failed: %s", exc)
                if not _wait_with_deadline(5):
                    # Final safety net: do not block forever.
                    try:
                        orphaned_pid = proc.pid
                    except Exception:
                        pass
                    _log.error(
                        "FFmpeg process %s refused to exit after kill; "
                        "leaving it orphan and releasing capture resources",
                        orphaned_pid,
                    )

        duration = self.duration
        output_path = ""
        with self._lock:
            proc = self._process
            self._process = None
            output_path = self._output_path

        # 关闭所有管道，防止文件描述符泄漏
        if proc is not None:
            for pipe_name in ("stdin", "stdout", "stderr"):
                pipe = getattr(proc, pipe_name, None)
                if pipe is not None:
                    try:
                        pipe.close()
                    except Exception:
                        pass

        self._release_stderr_executor_once()
        self._set_status(CaptureStatus.STOPPED)

        if os.path.isfile(output_path):
            size_mb = os.path.getsize(output_path) / (1024 * 1024)
            _log.info("Capture stopped: %.1fs, %.1fMB", duration, size_mb)
            return CaptureResult(True, output_path, duration, size_mb)
        return CaptureResult(False, "", error="Output file not created")

    def is_alive(self) -> bool:
        """Check if the FFmpeg process is still running."""
        with self._lock:
            proc = self._process
        if not proc:
            return False
        return proc.poll() is None

    def check_and_handle_crash(self) -> int | None:
        """Check if FFmpeg process has crashed. Returns exit code if crashed, None if healthy.

        If crashed, internally cleans up state and sets status to ERROR.
        Safe to call from any thread — no private-member access needed by callers.
        """
        with self._lock:
            proc = self._process
        if not proc:
            return None
        exit_code = proc.poll()
        if exit_code is not None:
            # Process has exited
            _log.warning("FFmpeg process exited unexpectedly (code=%d)", exit_code)
            with self._lock:
                self._process = None
            self._release_stderr_executor_once()
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
        with self._lock:
            proc = self._process
            self._process = None
        if proc:
            try:
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait(timeout=5)
                    _log.info("Force-killed FFmpeg process")
            except Exception as exc:
                _log.warning("Error force-killing FFmpeg: %s", exc)
            finally:
                # 关闭所有管道，防止文件描述符泄漏
                for pipe_name in ("stdin", "stdout", "stderr"):
                    pipe = getattr(proc, pipe_name, None)
                    if pipe is not None:
                        try:
                            pipe.close()
                        except Exception:
                            pass
                self._release_stderr_executor_once()

    def check_health(self) -> str:
        """Check capture health. Returns status message or empty if OK."""
        with self._lock:
            if self._status != CaptureStatus.RECORDING:
                return ""
            proc = self._process
            output_path = self._output_path
            if proc is None:
                return ""
        if proc.poll() is not None:
            rc = proc.returncode
            with self._lock:
                self._process = None
            self._release_stderr_executor_once()
            self._set_status(CaptureStatus.ERROR)
            return _friendly_ffmpeg_message(rc, self.stderr_tail)
        # Check if file is growing
        if os.path.isfile(output_path):
            cur_size = os.path.getsize(output_path)
            with self._lock:
                if cur_size == self._last_file_size and cur_size > 0:
                    self._stall_checks += 1
                    stall_checks = self._stall_checks
                else:
                    self._stall_checks = 0
                    stall_checks = 0
                self._last_file_size = cur_size
            if stall_checks >= 3:
                _log.warning(
                    "Output file not growing, stream may be stalled (checks=%d)",
                    stall_checks,
                )
                return "输出文件长时间未增长，录制可能已卡住"
        return ""
