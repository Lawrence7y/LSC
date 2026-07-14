"""预览帧抓取服务 — 为 Electron 前端通过 FFmpeg 抓取直播流 JPEG 帧。

通过 subprocess 启动 FFmpeg 将直播流转为 MJPEG 流，从 stdout 读取 bytes，
按 JPEG 边界（SOI 0xFFD8 / EOI 0xFFD9）分割出完整帧，缓存最新一帧供
Qt 主线程定时推送至前端。

设计原则：
- 不依赖 Qt / PySide6，便于单元测试
- 内部状态变更有锁保护
- FFmpeg 异常退出自动重试（最多 3 次）
"""
from __future__ import annotations

import subprocess
import threading

from lsc import get_logger

_log = get_logger(__name__)

# JPEG 边界标记
_JPEG_SOI = b"\xff\xd8"
_JPEG_EOI = b"\xff\xd9"

_MAX_RETRY = 3
_RETRY_DELAY_SEC = 5.0
_READ_CHUNK = 65536


class FrameCaptureWorker:
    """通过 FFmpeg 抓取直播流 JPEG 帧的工作器。

    启动后会在后台运行 FFmpeg 子进程和读线程，调用方通过
    ``get_latest_frame()`` 取最新一帧 JPEG bytes。
    """

    def __init__(
        self,
        stream_url: str,
        headers: dict[str, str] | None = None,
        user_agent: str | None = None,
        fps: int = 10,
        width: int = 480,
        height: int = 270,
        quality: int = 12,
    ) -> None:
        self._stream_url = stream_url
        self._headers = dict(headers) if headers else {}
        self._user_agent = user_agent
        self._fps = fps
        self._width = width
        self._height = height
        self._quality = quality

        self._process: subprocess.Popen | None = None
        self._reader_thread: threading.Thread | None = None
        self._stop_event = threading.Event()

        self._latest_frame: bytes | None = None
        self._frame_lock = threading.Lock()

        self._retry_count = 0
        self._error: str = ""

    # ── FFmpeg 命令构造 ───────────────────────────────────────

    def _build_args(self) -> list[str]:
        args: list[str] = ["ffmpeg"]
        # -headers：多行 Key: Value\r\n 拼成单个字符串参数
        if self._headers:
            header_blob = "".join(
                f"{key}: {value}\r\n" for key, value in self._headers.items()
            )
            args.extend(["-headers", header_blob])
        if self._user_agent:
            args.extend(["-user_agent", self._user_agent])
        args.extend(["-i", self._stream_url])
        args.extend(["-an"])  # 显式禁用音频解码，降低 CPU 负载
        args.extend([
            "-vf", f"fps={self._fps},scale={self._width}:{self._height}",
            "-f", "image2pipe",
            "-vcodec", "mjpeg",
            "-q:v", str(self._quality),
            "-",
        ])
        return args

    def _spawn_process(self) -> subprocess.Popen:
        from lsc.utils.process_launcher import prepare_launch, set_stream_nonblocking

        args = self._build_args()
        ffmpeg_bin = args[0]
        env, creation_flags, cwd = prepare_launch(ffmpeg_bin)
        _log.info("FrameCapture spawning FFmpeg for stream (fps=%d, %dx%d, q=%d)",
                  self._fps, self._width, self._height, self._quality)
        popen_kwargs: dict = {
            "stdout": subprocess.PIPE,
            "stderr": subprocess.DEVNULL,
            "stdin": subprocess.DEVNULL,
            "env": env,
            "cwd": cwd,
        }
        if creation_flags:
            popen_kwargs["creationflags"] = creation_flags
        proc = subprocess.Popen(args, **popen_kwargs)
        set_stream_nonblocking(proc.stdout)
        return proc

    # ── 读线程 ────────────────────────────────────────────────

    def _reader_loop(self) -> None:
        """从 FFmpeg stdout 持续读取 bytes，按 JPEG 边界分割帧。"""
        buffer = bytearray()
        while not self._stop_event.is_set():
            process = self._process
            if process is None or process.stdout is None:
                break
            try:
                chunk = process.stdout.read(_READ_CHUNK)
            except Exception as exc:
                _log.warning("FrameCapture read error: %s", exc)
                break
            if not chunk:
                # stdout EOF：FFmpeg 进程已退出
                self._handle_process_exit()
                if self._stop_event.is_set() or self._error:
                    break
                # 已重启：下一轮循环读新 process 的 stdout
                continue
            buffer.extend(chunk)
            # 提取所有完整 JPEG 帧，保留最后一个到 _latest_frame
            while True:
                soi = buffer.find(_JPEG_SOI)
                if soi == -1:
                    buffer.clear()
                    break
                eoi = buffer.find(_JPEG_EOI, soi + 2)
                if eoi == -1:
                    # 还未收到完整帧，丢弃 SOI 之前的内容
                    if soi > 0:
                        del buffer[:soi]
                    break
                frame = bytes(buffer[soi:eoi + 2])
                with self._frame_lock:
                    self._latest_frame = frame
                # 保留 EOI 之后的数据
                del buffer[:eoi + 2]

    def _handle_process_exit(self) -> None:
        """FFmpeg 进程退出处理：重试或设置错误状态。"""
        process = self._process
        if process is not None:
            try:
                process.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                pass
        self._process = None

        if self._stop_event.is_set():
            return

        if self._retry_count < _MAX_RETRY:
            self._retry_count += 1
            _log.info("FrameCapture FFmpeg exited, retry %d/%d after %.0fs",
                      self._retry_count, _MAX_RETRY, _RETRY_DELAY_SEC)
            # 等待重试间隔（可被 stop 打断）
            if self._stop_event.wait(_RETRY_DELAY_SEC):
                return
            try:
                self._process = self._spawn_process()
            except Exception as exc:
                _log.warning("FrameCapture respawn failed: %s", exc)
                self._error = "预览抓帧失败，已重试 3 次"
        else:
            self._error = "预览抓帧失败，已重试 3 次"

    # ── 公开 API ──────────────────────────────────────────────

    def start(self) -> None:
        """启动 FFmpeg 子进程和读线程。"""
        if self._process is not None:
            return
        self._stop_event.clear()
        try:
            self._process = self._spawn_process()
        except Exception as exc:
            _log.warning("FrameCapture start failed: %s", exc)
            self._error = f"预览抓帧启动失败: {exc}"
            return
        self._reader_thread = threading.Thread(
            target=self._reader_loop, name="FrameCaptureReader", daemon=True
        )
        self._reader_thread.start()

    def get_latest_frame(self) -> bytes | None:
        """返回最新缓存的 JPEG bytes，无帧返回 None。"""
        with self._frame_lock:
            return self._latest_frame

    def stop(self) -> None:
        """终止子进程并清理资源。幂等。"""
        self._stop_event.set()
        process = self._process
        if process is not None:
            try:
                process.terminate()
                try:
                    process.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    process.kill()
                    try:
                        process.wait(timeout=1.0)
                    except subprocess.TimeoutExpired:
                        pass
            except Exception as exc:
                _log.warning("FrameCapture stop error: %s", exc)
        self._process = None
        thread = self._reader_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        self._reader_thread = None

    def is_alive(self) -> bool:
        """FFmpeg 进程是否仍在运行。"""
        process = self._process
        return process is not None and process.poll() is None

    def get_error(self) -> str:
        """返回错误状态（重试耗尽时设置）。"""
        return self._error
