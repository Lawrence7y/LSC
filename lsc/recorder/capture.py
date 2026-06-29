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
    """验证录制文件的完整性。

    执行三层验证以确保录制文件有效：
      1. 路径验证：检查路径非空且文件存在
      2. 大小验证：文件大小需大于 min_size_mb（默认 0.1 MB）
      3. 格式验证：读取文件头部字节，检查是否为有效的视频格式签名
         - MP4: 偏移 4 处应为 'ftyp'
         - FLV: 前 3 字节应为 'FLV'
         - MKV: 前 4 字节应为 EBML 头（0x1A45DFA3）

    Args:
        output_path: 录制文件路径
        min_size_mb: 最小文件大小阈值（MB），小于此值视为录制失败

    Returns:
        (is_valid, error_message) 元组
        - is_valid: 文件是否通过所有验证
        - error_message: 验证失败时的描述信息，成功时为空字符串
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
    except OSError as e:
        return False, f"文件读取失败: {e}"

    return True, ""


class StreamCapture:
    """FFmpeg 直播流录制器。

    封装 FFmpeg 子进程，管理直播流录制的完整生命周期：

    生命周期状态流转：
        IDLE -> CONNECTING -> RECORDING -> STOPPED
                                     -> ERROR

    典型使用流程：
        1. start(url, output_path) — 构造 FFmpeg 命令并启动子进程
        2. _wait_for_startup_data() — 等待 FFmpeg 产生首帧输出数据（超时 5 秒）
        3. RECORDING 状态 — 定期调用 check_health() 监控进程和文件增长
        4. stop() — 三级优雅停止（stdin 'q' -> terminate -> kill）

    共享资源：
        - 所有实例共享一个 stderr 读取线程池，避免大量并发录制时创建过多线程
    """

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
        self._stderr_future: Future[None] | None = None
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
        """等待 FFmpeg 产生首帧输出数据（启动探测）。

        启动后轮询检查输出文件是否已创建且有内容。如果 FFmpeg 进程在超时前退出，
        则返回当前文件状态。主要用于检测：
        - 直播流是否已开播
        - URL 是否有效
        - FFmpeg 是否能正常连接并写入数据

        超时时间由 STARTUP_PROBE_TIMEOUT_SEC（默认 5 秒）控制，
        轮询间隔由 STARTUP_PROBE_INTERVAL_SEC（默认 0.1 秒）控制。

        Returns:
            True 表示已收到数据，False 表示超时或 FFmpeg 异常退出
        """
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
        """构建启动失败时的用户友好错误信息。

        根据 FFmpeg 进程退出状态和 stderr 输出生成中文错误描述：
        - 如果 FFmpeg 已退出：通过 _friendly_ffmpeg_message 解析 exit code 和 stderr
        - 如果 FFmpeg 仍在运行但超时：返回通用的启动失败提示

        Returns:
            用户可读的错误描述字符串
        """
        proc = self._process
        if proc is not None and proc.poll() is not None:
            return _friendly_ffmpeg_message(proc.returncode or -1, self.stderr_tail)
        return "启动录制失败：没有收到直播数据，请确认直播已开播或链接未过期"

    def start(self, url: str, output_path: str, *,
              codec: str = "copy",
              input_args: list[str] | None = None,
              extra_args: list[str] | None = None) -> bool:
        """启动直播流录制。

        构造 FFmpeg 命令行参数并启动子进程。根据 codec 参数选择不同的编码模式：
        - "copy": 直接复制视频流，无重新编码（最快，画质无损）
        - "custom": 编码参数由 extra_args 提供
        - 其他值: 使用 libx264 + AAC 进行重新编码

        FFmpeg 命令构建逻辑：
          1. 基础参数：-y（覆盖输出）、-loglevel warning
          2. HTTP/HTTPS 流自动添加重连参数：-reconnect、-timeout 等
          3. input_args 在 -i 之前插入（用于自定义 headers 等）
          4. 根据 codec 添加编码器参数
          5. 输出格式：MP4 + frag_keyframe（适合流式写入）
          6. 通过 prepare_launch 构建跨平台启动环境

        Args:
            url: 直播流地址（m3u8、flv 等）
            output_path: 输出文件路径
            codec: 视频编码模式（"copy" | "custom" | 其他值使用 libx264）
            input_args: FFmpeg 输入参数列表，在 -i 之前插入
            extra_args: 额外的 FFmpeg 参数，在编码参数之后添加

        Returns:
            True 表示录制成功启动，False 表示启动失败（可查询 last_error）
        """
        with self._lock:
            already_recording = self._status == CaptureStatus.RECORDING
            self._last_error = ""

        if already_recording:
            _log.warning("Already recording, force-stopping old capture first")
            self._force_kill()
            self._set_status(CaptureStatus.STOPPED)

        output_dir_path = os.path.dirname(output_path) or "."
        try:
            os.makedirs(output_dir_path, exist_ok=True)
        except OSError as exc:
            raise RuntimeError(f"录制目录不可写：{output_dir_path}（{exc.strerror or exc}）") from exc

        cmd = [self.ffmpeg, "-y", "-loglevel", "warning"]

        # 流重连参数（仅对 HTTP/HTTPS 直播流生效）
        # -reconnect 1: 启用自动重连
        # -reconnect_streamed 1: 对流式传输也启用重连
        # -reconnect_delay_max 5: 最大重连间隔 5 秒
        # -timeout 30000000: 网络超时 30 秒（微秒）
        if url.startswith(("http://", "https://")):
            cmd += [
                "-reconnect", "1",
                "-reconnect_streamed", "1",
                "-reconnect_delay_max", "5",
                "-timeout", "30000000",
            ]

        # 输入专属选项（如自定义 headers）必须放在 -i 之前
        if input_args:
            cmd += input_args

        cmd += ["-i", url]

        # 编码模式选择
        if codec == "copy":
            # 直接复制视频流，无重新编码，速度最快且画质无损
            cmd += ["-c", "copy"]
        elif codec == "custom":
            # 自定义编码，参数由 extra_args 提供
            pass
        else:
            # 使用 libx264 编码视频，AAC 编码音频
            # preset medium: 编码速度与压缩率的平衡
            # crf 23: 恒定质量因子（18-28 为常用范围，越小质量越高）
            cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", "23"]
            cmd += ["-c:a", "aac", "-b:a", "128k"]

        if extra_args:
            cmd += extra_args

        # 输出格式配置
        # -f mp4: 强制 MP4 容器格式
        # -movflags frag_keyframe+empty_moov+faststart:
        #   frag_keyframe: 每个关键帧生成一个片段（支持流式写入）
        #   empty_moov: 文件头在结束时写入（支持边录边写）
        #   faststart: 移动 moov atom 到文件开头（便于网络播放）
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
        """停止当前录制，采用三级优雅停止策略。

        三级停止机制确保 FFmpeg 有充分机会完成文件写入和元数据刷新：
          1. 通过 stdin 发送 'q' 命令，触发 FFmpeg 优雅退出（写入 moov atom）
          2. 等待 5 秒，允许 FFmpeg 完成清理
          3. 发送 SIGTERM（terminate()），再等待 3 秒
          4. 发送 SIGKILL（kill()），最后等待 5 秒
          5. 若进程仍不退出，记录孤儿 PID 并标记为 ERROR 状态

        停止后会验证输出文件是否存在，并计算录制时长和文件大小。

        Returns:
            CaptureResult 包含 success、output_path、duration_sec、
            file_size_mb 等字段。若进程无法退出则 error 字段包含 PID 信息。
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
        self._close_proc_pipes(proc)

        self._release_stderr_executor_once()

        # 如果有孤儿进程，标记为 ERROR 而非 STOPPED
        if orphaned_pid is not None:
            self._set_status(CaptureStatus.ERROR)
            _log.warning("Capture stopped with orphaned FFmpeg PID %s", orphaned_pid)
            return CaptureResult(False, "", error=f"FFmpeg 进程未正常退出 (PID={orphaned_pid})")

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
            # 关闭管道，防止文件描述符泄漏（与 stop() 路径保持一致）
            self._close_proc_pipes(proc)
            self._release_stderr_executor_once()
            self._set_status(CaptureStatus.ERROR)
            return exit_code
        return None

    @staticmethod
    def _close_proc_pipes(proc: subprocess.Popen | None) -> None:
        """关闭 FFmpeg 进程的所有管道，防止文件描述符泄漏。"""
        if proc is None:
            return
        for pipe_name in ("stdin", "stdout", "stderr"):
            pipe = getattr(proc, pipe_name, None)
            if pipe is not None:
                try:
                    pipe.close()
                except Exception:
                    pass

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
            pid = proc.pid
            try:
                if proc.poll() is None:
                    _log.debug("Terminating FFmpeg process %d", pid)
                    proc.terminate()
                    try:
                        proc.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        _log.warning("FFmpeg %d did not terminate, killing", pid)
                        proc.kill()
                        try:
                            proc.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            _log.error("FFmpeg %d refused to die after kill", pid)
                    _log.info("Force-killed FFmpeg process %d", pid)
            except Exception as exc:
                _log.warning("Error force-killing FFmpeg %d: %s", pid, exc)
            finally:
                for pipe_name in ("stdin", "stdout", "stderr"):
                    pipe = getattr(proc, pipe_name, None)
                    if pipe is not None:
                        try:
                            if not pipe.closed:
                                pipe.close()
                        except Exception:
                            pass
                self._release_stderr_executor_once()

    def check_health(self) -> str:
        """检查录制健康状态，返回状态描述或空字符串表示正常。

        执行两项检查：
          1. 进程存活检查：如果 FFmpeg 进程已退出，记录错误信息并清理资源
          2. 文件增长检查：连续 3 次检查文件大小未变化则判定为卡住（可能直播流中断）

        文件卡住检测逻辑：
          - 每次调用记录当前文件大小
          - 如果连续 3 次（即约 3 个检查周期）文件大小无变化且文件非空，
            则认为直播流可能已卡住，返回警告信息
          - 文件大小恢复增长时会重置计数器

        Returns:
            空字符串表示状态正常；非空字符串为错误/警告描述
        """
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
            # 关闭管道，防止文件描述符泄漏（与 stop() 路径保持一致）
            self._close_proc_pipes(proc)
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
