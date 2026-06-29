"""Recording controller - business logic extracted from RecordPage.

Separates recording concerns (FFmpeg capture, timer, NVENC probe, URL parsing)
from pure UI rendering, following the ViewModel / Controller pattern.
"""

from __future__ import annotations

import logging
import os
import subprocess
import time as _time
from argparse import Namespace
from datetime import datetime
from typing import Any
from uuid import uuid4

from PySide6.QtCore import QThread, QTimer, Signal

try:
    from lsc import get_logger
except ImportError:
    def get_logger(name: str):
        return logging.getLogger(name)

from lsc.exporter.clip import ClipExporter
from lsc.platforms.registry import parse_stream, select_quality
from lsc.recorder.capture import validate_recording

_log = get_logger(__name__)

_ESTIMATED_MB_PER_SEC = 0.45  # Rough estimate for 1080p H.264


def friendly_ffmpeg_exit_message(exit_code: int, stderr_tail: str) -> str:
    """Map FFmpeg exit code + stderr to a user-friendly Chinese message.

    Delegates to the capture module's internal mapping for consistency.
    """
    from lsc.recorder.capture import _friendly_ffmpeg_message

    return _friendly_ffmpeg_message(exit_code, stderr_tail)


class UrlParserWorker(QThread):
    """Background thread for parsing Douyin page URL."""

    finished = Signal(dict)

    def __init__(self, page_url: str, parse_fn):
        super().__init__()
        self._url = page_url
        self._parse_fn = parse_fn

    def run(self):
        try:
            result = self._parse_fn(self._url)
            self.finished.emit(result)
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning("UrlParserWorker error: %s", exc)
            self.finished.emit({"error": str(exc), "isLive": False})


class ProbeWorker(QThread):
    """Background thread for non-blocking ffprobe video duration queries."""

    finished = Signal(float)

    def __init__(self, video_path: str, probe_fn):
        super().__init__()
        self._video_path = video_path
        self._probe_fn = probe_fn

    def run(self):
        try:
            dur = self._probe_fn(self._video_path)
            self.finished.emit(dur)
        except Exception:
            self.finished.emit(0.0)


class ExportWorker(QThread):
    """Background thread for clip export."""

    finished = Signal(bool, str, str, float, str)  # success, path, error, size_mb, thumbnail_path
    progress = Signal(float, float, float)  # percent, elapsed_sec, total_sec

    def __init__(self, exporter, video_path, start, end, output_dir, title,
                 profile=None):
        super().__init__()
        self._exporter = exporter
        self._video_path = video_path
        self._start = start
        self._end = end
        self._output_dir = output_dir
        self._title = title
        self._profile = profile
        self._proc = None  # FFmpeg Popen 进程引用，供 cancel 使用
        self._cancelled = False

    def run(self):
        kwargs = {"title": self._title, "progress_callback": self._on_progress,
                  "on_process": self._on_process}
        if self._profile is not None:
            kwargs["profile"] = self._profile
        result = self._exporter.export_clip(
            self._video_path, self._start, self._end, self._output_dir,
            **kwargs,
        )
        # 如果被取消，覆盖错误信息以让前端识别为取消
        if self._cancelled and not result.success:
            import dataclasses
            result = dataclasses.replace(
                result, success=False, output_path="",
                error="导出已取消", file_size_mb=0.0, thumbnail_path="",
            )
        self.finished.emit(
            result.success, result.output_path, result.error,
            result.file_size_mb, result.thumbnail_path or "",
        )

    def _on_process(self, proc) -> None:
        """保存 FFmpeg 进程引用，供 cancel 使用。"""
        self._proc = proc

    def _on_progress(self, percent: float, elapsed: float, total: float) -> None:
        """FFmpeg 进度回调，转发到 Qt 信号。"""
        self.progress.emit(percent, elapsed, total)

    def cancel(self) -> bool:
        """请求取消导出：终止 FFmpeg 进程并标记为已取消。

        Returns
        -------
        bool
            True 表示已成功发送 kill 信号；False 表示进程已退出或未启动。
        """
        if self._cancelled:
            return False
        self._cancelled = True
        proc = self._proc
        if proc is None:
            return False
        try:
            if proc.poll() is None:  # 仍在运行
                proc.kill()
                return True
        except Exception:
            pass
        return False


from lsc.gui.common_workers import AnalysisWorker, BatchExportWorker


class RecordingController:
    """Non-UI controller that manages the recording lifecycle.

    Owns: StreamCapture, timers, export workers, URL parsing.
    Emits signals via Qt signals on the RecordPage.
    """

    _nvenc_cache: bool | None = None
    _nvenc_checking: bool = False

    def start_nvenc_check(self):
        """Asynchronously check NVENC availability and cache the result."""
        cls = self.__class__
        if cls._nvenc_cache is not None or cls._nvenc_checking:
            return
        cls._nvenc_checking = True

        import threading
        def _check():
            try:
                available = self.check_nvenc_available()
                cls._nvenc_cache = available
            except Exception:
                cls._nvenc_cache = False
            finally:
                cls._nvenc_checking = False

        thread = threading.Thread(target=_check, daemon=True)
        thread.start()

    def is_nvenc_available(self) -> bool:
        """Get cached NVENC availability, or run synchronous check if not yet resolved."""
        # If mocked on the instance, bypass the cache and run the mock directly
        if self.check_nvenc_available != self.__class__.check_nvenc_available:
            return self.check_nvenc_available()

        cls = self.__class__
        if cls._nvenc_cache is not None:
            return cls._nvenc_cache
        # If not cached yet, run it synchronously as fallback
        available = self.check_nvenc_available()
        cls._nvenc_cache = available
        return available

    def __init__(self):
        self.is_recording = False
        self.has_start = False
        self.start_sec: float | None = None
        self.end_sec: float | None = None
        self.total_sec: int = 0
        # Current playback position in seconds (for multi-room timeline/seek).
        # Updated by seek operations and synced from the preview widget when
        # available. Distinct from total_sec (recording elapsed time).
        self.current_sec: float = 0.0
        self.record_start_mono: float = 0.0
        # 暴露给外部读取的录制开始时间戳（monotonic），用于墙钟时间轴对齐
        # 由 start_recording_with_crf() 在录制成功启动时设置，供 manager 读取
        # 并回填到 RoomSession.recording_start_mono
        self.recording_start_mono: float = 0.0

        self.video_path: str = ""
        self.stream_url: str = ""
        self.page_url: str = ""
        self.last_stream_info = None
        self.input_args: list[str] = []
        self.output_dir: str = ""
        self.exported: list[tuple] = []
        self.selected_quality: str = ""
        # 编码参数(录制启动后回写),供详情面板展示
        self.encoder: str = ""
        self.record_profile: str = ""
        self.crf: int | None = None

        self._capture = None
        self._exporter = None
        self._export_thread: ExportWorker | None = None
        self._export_workers: dict[str, ExportWorker] = {}  # export_id -> worker
        self._analysis_thread: AnalysisWorker | None = None
        self._batch_export_thread: BatchExportWorker | None = None
        self._url_parser: UrlParserWorker | None = None
        self.capture_init_error = ""
        self.exporter_init_error = ""

        # Timers (owned here, connected externally)
        self.timer = QTimer()
        self.watchdog = QTimer()

        # Trigger background check for NVENC availability
        self.start_nvenc_check()

    # -- Initialisation -------------------------------------------------

    def init_capture(self, on_status_cb=None):
        """Create StreamCapture instance."""
        try:
            from lsc.config import load_config
            from lsc.recorder.capture import StreamCapture
            cfg = load_config()
            self._capture = StreamCapture(cfg)
            self.capture_init_error = ""
            if on_status_cb:
                self._capture.set_status_callback(on_status_cb)
        except Exception as exc:
            _log.warning("Failed to initialize capture: %s", exc)
            self.capture_init_error = str(exc) or "录制器初始化失败"
            self._capture = None

    def init_exporter(self):
        """Create ClipExporter instance."""
        try:
            from lsc.config import load_config
            from lsc.exporter.clip import ClipExporter
            cfg = load_config()
            self._exporter = ClipExporter(cfg)
            self.exporter_init_error = ""
            self.output_dir = cfg.output_dir
        except Exception as exc:
            _log.warning("Failed to initialize exporter: %s", exc)
            self.exporter_init_error = str(exc) or "导出器初始化失败"
            self._exporter = None

    # -- Preflight checks -----------------------------------------------

    # Base minimum free space for a single recording stream (8 GB ≈ 15 min @ 1080p).
    _MIN_RECORDING_FREE_BYTES_PER_STREAM = 8 * 1024 * 1024 * 1024

    @classmethod
    def preflight_recording(cls, output_dir: str, concurrent_streams: int = 1) -> str:
        """Check disk space before recording.

        The required free space scales with the number of concurrent recording
        streams so that multi-room recording does not exhaust disk prematurely.

        Parameters
        ----------
        output_dir : str
            Target output directory.
        concurrent_streams : int
            Number of streams that will be recorded simultaneously. Defaults to 1.

        Returns
        -------
        str
            Error message if disk space is insufficient, otherwise empty string.
        """
        import shutil

        try:
            os.makedirs(output_dir, exist_ok=True)
        except OSError as exc:
            return f"录制目录不可写：{output_dir}（{exc.strerror or exc}）。请在设置中修改输出目录。"
        _total, _used, free = shutil.disk_usage(output_dir)
        required = cls._MIN_RECORDING_FREE_BYTES_PER_STREAM * max(1, concurrent_streams)
        if free < required:
            free_gb = free / (1024 ** 3)
            required_gb = required / (1024 ** 3)
            return (
                f"磁盘空间不足，当前仅剩 {free_gb:.1f} GB，"
                f"需要 {required_gb:.1f} GB（{concurrent_streams} 路并发录制）"
            )
        return ""

    def probe_stream_metadata(self, source: str) -> tuple[str, str]:
        """Use FFprobe to get stream resolution and frame rate.

        Returns (resolution, fps) tuple, e.g. ("1920x1080", "60 fps").
        Returns ("", "") on failure.
        """
        import json

        from lsc.config import load_config

        cfg = load_config()
        ffprobe = cfg.ffprobe_path
        if not ffprobe or not os.path.isfile(ffprobe):
            return "", ""

        try:
            cmd = [
                ffprobe,
                "-v", "quiet",
                "-print_format", "json",
                "-show_streams",
                source,
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)  # noqa: S603
            payload = json.loads(result.stdout or "{}")
            stream = next(
                (item for item in payload.get("streams", []) if item.get("codec_type") == "video"),
                {},
            )
            width = stream.get("width")
            height = stream.get("height")
            fps_raw = stream.get("avg_frame_rate", "0/1")
            fps = self._format_frame_rate(fps_raw)
            resolution = f"{width}x{height}" if width and height else ""
            return resolution, fps
        except Exception as exc:
            _log.debug("Stream metadata probe failed: %s", exc)
            return "", ""

    @staticmethod
    def _format_frame_rate(fps_raw: str) -> str:
        """Convert '60000/1001' style frame rate to '60 fps'."""
        try:
            if "/" in fps_raw:
                num, den = fps_raw.split("/")
                fps = float(num) / float(den)
            else:
                fps = float(fps_raw)
            return f"{fps:.0f} fps" if fps == int(fps) else f"{fps:.1f} fps"
        except (ValueError, ZeroDivisionError):
            return ""

    # -- Capture accessors ----------------------------------------------

    @property
    def capture(self):
        return self._capture

    @property
    def capture_is_recording(self) -> bool:
        return self._capture is not None and self._capture.is_recording

    def check_capture_crash(self) -> int | None:
        """Public API: check if FFmpeg process has crashed.

        Returns exit code if crashed, None if healthy or no capture.
        Safe to call from UI layer - no private-member access needed.
        """
        if not self._capture or not self._capture.is_recording:
            return None
        return self._capture.check_and_handle_crash()

    # -- URL parsing ----------------------------------------------------

    def parse_stream_url(self, url: str, *, force_refresh: bool = False) -> dict:
        """Parse a supported room URL into the legacy dict consumed by the GUI."""
        # Only pass force_refresh when explicitly requested so monkey-patched
        # test fakes that only accept a single ``url`` argument keep working.
        if force_refresh:
            info = parse_stream(url, force_refresh=True)
        else:
            info = parse_stream(url)
        legacy = info.to_legacy_dict()
        self.last_stream_info = info
        self.input_args = list(legacy.get("_inputArgs", []))
        return legacy

    def parse_douyin_url(self, url: str) -> dict:
        """Compatibility wrapper for the old Douyin-only API."""
        return self.parse_stream_url(url)

    def start_url_parse(self, url: str, on_parsed) -> None:
        """Launch async URL parsing. Calls on_parsed(dict) when done."""
        # 通过实例标志控制是否强制刷新缓存，避免改变 start_url_parse 的签名
        # 和破坏现有测试对 parse_fn == self.parse_stream_url 的断言。
        force_refresh = getattr(self, "_force_next_parse_refresh", False)
        self._force_next_parse_refresh = False
        if force_refresh:
            parse_fn = lambda u: self.parse_stream_url(u, force_refresh=True)
        else:
            parse_fn = self.parse_stream_url
        self._url_parser = UrlParserWorker(url, parse_fn)
        self._url_parser.finished.connect(on_parsed)
        self._url_parser.start()

    # -- NVENC probe ----------------------------------------------------

    @staticmethod
    def check_nvenc_available() -> bool:
        """Quick test: can FFmpeg use h264_nvenc on this system?"""
        try:
            from lsc.config import load_config
            from lsc.utils.process_launcher import prepare_launch
            cfg = load_config()
            ffmpeg = cfg.ffmpeg_path
            if not ffmpeg or not os.path.isfile(ffmpeg):
                return False
            # 必须走 prepare_launch 以兼容打包版（FFmpeg 在 exe 旁）：
            # 否则裸 subprocess.run 找不到 ffmpeg 而误判 NVENC 不可用，
            # 导致录制被错误降级为 Copy 模式。
            env, creation_flags, cwd = prepare_launch(ffmpeg)
            run_kwargs: dict[str, Any] = {
                "capture_output": True,
                "text": True,
                "timeout": 10,
                "env": env,
            }
            if cwd:
                run_kwargs["cwd"] = cwd
            if creation_flags:
                run_kwargs["creationflags"] = creation_flags
            result = subprocess.run(
                [ffmpeg, "-y", "-loglevel", "error",
                 "-f", "lavfi", "-i", "testsrc=duration=1:size=256x256:rate=1",
                 "-c:v", "h264_nvenc", "-frames:v", "1",
                 "-f", "null", "-"],
                **run_kwargs,
            )
            return result.returncode == 0
        except Exception:
            return False


    @staticmethod
    def select_stream_url(info: dict, quality_preset: str) -> tuple[str, str]:
        """Pick the best-matching source URL for the requested quality preset."""
        return select_quality(info, quality_preset)

    @staticmethod
    def _normalize_bitrate_value(bitrate: str | None, bitrate_unit: str) -> str:
        """Convert UI bitrate fields to FFmpeg-compatible values such as 8000k."""
        text = (bitrate or "").strip()
        if not text:
            return ""

        try:
            numeric = float(text)
        except ValueError:
            return ""

        if numeric <= 0:
            return ""

        unit = bitrate_unit.strip().lower()
        suffix = "M" if unit == "mbps" else "k"
        if numeric.is_integer():
            text = str(int(numeric))
        else:
            text = str(numeric).rstrip("0").rstrip(".")
        return f"{text}{suffix}"

    # -- Recording lifecycle --------------------------------------------

    def start_recording_with_crf(self, stream_url: str, output_dir: str,
                                  encoder: str, crf: int,
                                  param_mode: str = "CRF 质量",
                                  bitrate: str | None = None,
                                  bitrate_unit: str = "kbps",
                                  input_args: list[str] | None = None,
                                  on_status=None,
                                  resolution: str | None = None,
                                  framerate: str | None = None,
                                  audio_bitrate: str | None = None) -> tuple[bool, str, str, str]:
        """Start recording with explicit CRF value.

        CRF is respected for all encoder modes:
        - H.264 CPU: -crf <value>
        - H.264 NVENC: -cq <value>  (NVENC's equivalent of CRF)
        - Copy: CRF is ignored (stream is saved as-is)

        Sets is_recording and record_start_mono atomically so the UI
        layer doesn't need to set them separately.

        Parameters
        ----------
        resolution, framerate : str | None
            Reserved for future video filter support. Currently accepted
            but not applied (stream is recorded at native resolution/fps).
        audio_bitrate : str | None
            Audio bitrate string, e.g. "128k", "96k". Falls back to "128k"
            when None or empty.

        Returns
        -------
        tuple[bool, str, str, str]
            (success, output_path, encoder_used, error_message)
            error_message is empty on success.
        """
        if not self._capture:
            return False, "", encoder, "录制组件未初始化"
        if not stream_url:
            return False, "", encoder, "直播流地址为空"

        os.makedirs(output_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        unique_suffix = uuid4().hex[:6]
        output_path = os.path.join(output_dir, f"recording_{timestamp}_{unique_suffix}.mp4")

        if input_args is None:
            input_args = []

        # Normalize audio bitrate (fallback to 128k for backward compat)
        audio_br = (audio_bitrate or "128k").strip() or "128k"

        output_args: list[str] = []
        encoder_used = encoder

        # 编码器名称归一化：前端发送 ffmpeg 格式名（h264_nvenc/libx264 等），
        # 但以下分支检查的是旧格式名（H.264 NVENC/H.264 CPU 等），需要统一映射
        _encoder_map = {
            "h264_nvenc": "H.264 NVENC",
            "hevc_nvenc": "H.265 NVENC",
            "libx264": "H.264 CPU",
            "libx265": "H.265 CPU",
            "copy": "Copy",
            "H.264 NVENC": "H.264 NVENC",
            "H.265 NVENC": "H.265 NVENC",
            "H.264 CPU": "H.264 CPU",
            "H.265 CPU": "H.265 CPU",
            "Copy": "Copy",
        }
        encoder_used = _encoder_map.get(encoder, encoder)

        target_bitrate = self._normalize_bitrate_value(bitrate, bitrate_unit)
        bufsize = ""

        if encoder == "H.264 NVENC":
            if on_status:
                on_status("检测 NVENC 硬件编码...", "info")
            if not self.is_nvenc_available():
                if on_status:
                    on_status("NVENC 不可用，自动切换为 Copy 模式", "warning")
                encoder_used = "Copy"

        if param_mode == "不限制":
            encoder_used = "Copy"

        if target_bitrate:
            suffix = target_bitrate[-1]
            numeric_part = target_bitrate[:-1]
            try:
                if "." in numeric_part:
                    doubled = str(float(numeric_part) * 2).rstrip("0").rstrip(".")
                else:
                    doubled = str(int(numeric_part) * 2)
                bufsize = f"{doubled}{suffix}"
            except ValueError:
                bufsize = ""

        if encoder_used == "H.264 CPU" and param_mode == "码率限制" and target_bitrate:
            output_args += ["-c:v", "libx264", "-preset", "medium"]
            output_args += ["-b:v", target_bitrate, "-maxrate", target_bitrate]
            if bufsize:
                output_args += ["-bufsize", bufsize]
            output_args += ["-c:a", "aac", "-b:a", audio_br]
            self._capture.start(stream_url, output_path,
                                codec="custom", input_args=input_args,
                                extra_args=output_args)
        elif encoder_used == "H.264 CPU":
            # Use -preset medium for better compression efficiency than fast
            # (same CRF = smaller file, slightly higher CPU usage)
            output_args += ["-c:v", "libx264", "-preset", "medium", "-crf", str(crf)]
            output_args += ["-c:a", "aac", "-b:a", audio_br]
            self._capture.start(stream_url, output_path,
                                codec="custom", input_args=input_args,
                                extra_args=output_args)
        elif encoder_used == "H.264 NVENC" and param_mode == "码率限制" and target_bitrate:
            output_args += ["-c:v", "h264_nvenc", "-preset", "p4", "-rc", "cbr_hq"]
            output_args += ["-b:v", target_bitrate, "-maxrate", target_bitrate]
            if bufsize:
                output_args += ["-bufsize", bufsize]
            output_args += ["-c:a", "aac", "-b:a", audio_br]
            self._capture.start(stream_url, output_path,
                                codec="custom", input_args=input_args,
                                extra_args=output_args)
        elif encoder_used == "H.264 NVENC":
            # NVENC uses -cq (constant quality) which is the NVENC equivalent of CRF
            output_args += ["-c:v", "h264_nvenc", "-preset", "p4", "-rc", "vbr", "-cq", str(crf), "-b:v", "0"]
            output_args += ["-c:a", "aac", "-b:a", audio_br]
            self._capture.start(stream_url, output_path,
                                codec="custom", input_args=input_args,
                                extra_args=output_args)
        else:
            self._capture.start(stream_url, output_path,
                                input_args=input_args,
                                extra_args=output_args)

        if self._capture.status.value == "recording":
            # Capture confirmed running - set controller state atomically
            self.is_recording = True
            self.total_sec = 0
            self.record_start_mono = _time.monotonic()
            self.recording_start_mono = self.record_start_mono
            self.has_start = False
            self.start_sec = None
            self.end_sec = None
            self.stream_url = stream_url
            self.video_path = output_path
            # 回写编码参数,供详情面板(DetailPanel)读取展示。
            self.encoder = encoder_used
            self.record_profile = param_mode
            self.crf = crf
            return True, output_path, encoder_used, ""
        else:
            error_msg = getattr(self._capture, "last_error", "") or "录制启动失败"
            return False, "", encoder_used, error_msg

    def stop_recording(self) -> tuple[bool, float, str]:
        """Stop recording. Returns (success, size_mb, output_path)."""
        size_mb = 0.0
        output_path = self.video_path

        def _validated(success: bool, path: str) -> tuple[bool, float, str]:
            """对录制产物做完整性校验。

            被强制 kill 的 FFmpeg 可能产出截断/无效 mp4。仅凭
            ``os.path.getsize`` 无法识别，会让 UI 误报成功。与
            ``session.py`` 的做法对齐：校验文件头与最小体积，无效则标失败。
            """
            nonlocal size_mb
            if not success or not path:
                return success, size_mb, path
            valid, reason = validate_recording(path)
            if not valid:
                _log.warning("Recording validation failed for %s: %s", path, reason)
                if os.path.isfile(path):
                    size_mb = os.path.getsize(path) / (1024 * 1024)
                return False, size_mb, path
            if os.path.isfile(path):
                size_mb = os.path.getsize(path) / (1024 * 1024)
            return True, size_mb, path

        if self._capture and self._capture.is_recording:
            result = self._capture.stop()
            # 先停止 capture，再更新状态标志，确保状态一致性
            self.is_recording = False
            if result.success:
                self.video_path = result.output_path
                size_mb = result.file_size_mb
                output_path = result.output_path
                return _validated(True, output_path)
        else:
            self.is_recording = False

        if self._capture and not self._capture.is_alive() and self.video_path:
            if os.path.isfile(self.video_path):
                return _validated(True, self.video_path)

        return False, size_mb, output_path

    def probe_video_duration_sync(self, video_path: str = "") -> float:
        """Synchronously use FFprobe to get actual video duration."""
        target_path = video_path or self.video_path
        if not target_path or not os.path.isfile(target_path):
            return 0.0
        try:
            import json

            from lsc.config import load_config
            cfg = load_config()
            ffprobe = cfg.ffprobe_path
            if not ffprobe or not os.path.isfile(ffprobe):
                return 0.0
            cmd = [ffprobe, "-v", "quiet", "-print_format", "json",
                   "-show_format", target_path]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            data = json.loads(result.stdout)
            dur = float(data.get("format", {}).get("duration", 0))
            return dur if dur > 0 else 0.0
        except Exception:
            return 0.0

    def probe_video_duration(self, on_probed=None) -> float:
        """Use FFprobe to get actual video duration.

        If on_probed callback is specified, run asynchronously and return 0.0.
        Otherwise, run synchronously and return the duration.
        """
        if on_probed is None:
            return self.probe_video_duration_sync()

        # Async mode
        self._probe_worker = ProbeWorker(self.video_path, self.probe_video_duration_sync)
        self._probe_worker.finished.connect(on_probed)
        self._probe_worker.start()
        return 0.0

    # -- Timer tick -----------------------------------------------------

    def tick(self) -> int:
        """Update elapsed time using monotonic clock. Returns current total_sec."""
        if self.is_recording:
            self.total_sec = int(_time.monotonic() - self.record_start_mono)
        return self.total_sec

    # -- Watchdog -------------------------------------------------------

    def watchdog_check(self) -> str:
        """Check FFmpeg health. Returns error message or empty string."""
        if not self._capture or not self.is_recording:
            return ""
        # Use public API - no private-member access
        exit_code = self._capture.check_and_handle_crash()
        if exit_code is not None:
            return f"FFmpeg 异常退出 (code {exit_code})"
        msg = self._capture.check_health()
        return msg

    # -- Export ---------------------------------------------------------

    def start_export(self, start_sec: float, end_sec: float,
                     output_dir: str, name: str,
                     on_done, profile=None, on_progress=None) -> str:
        """Start async clip export. Returns export_id (non-empty string) if started.

        Parameters
        ----------
        profile : ExportProfile | None
            编码配置。若为 None 则使用 ClipExporter 的默认配置。
        on_progress : callable | None
            进度回调 ``callback(percent, elapsed, total)``。

        Returns
        -------
        str
            export_id（非空字符串）表示已启动；空字符串表示启动失败。
            export_id 可传给 :meth:`cancel_export` 取消该任务。
        """
        if not self._exporter or not self.video_path or not os.path.isfile(self.video_path):
            return ""

        self._export_thread = ExportWorker(
            self._exporter, self.video_path,
            start_sec, end_sec, output_dir, name,
            profile=profile,
        )
        export_id = uuid4().hex
        # 完成后从映射中移除，避免字典无限增长
        def _on_finished(*args):
            self._export_workers.pop(export_id, None)
            try:
                on_done(*args)
            except Exception:
                _log.exception("on_done callback raised in start_export")
        self._export_thread.finished.connect(_on_finished)
        if on_progress is not None:
            self._export_thread.progress.connect(on_progress)
        self._export_workers[export_id] = self._export_thread
        self._export_thread.start()
        return export_id

    def cancel_export(self, export_id: str) -> bool:
        """取消指定 export_id 的导出任务。

        Returns
        -------
        bool
            True 表示已发送 kill 信号；False 表示任务不存在或已结束。
        """
        worker = self._export_workers.get(export_id)
        if worker is None:
            return False
        ok = worker.cancel()
        # 不在此处弹出 worker，等 finished 信号触发后由 _on_finished 清理
        return ok

    def start_analysis(self, video_path: str, profile_name: str,
                       output_dir: str, on_done) -> bool:
        """Start async analysis for a completed recording."""
        if not video_path or not os.path.isfile(video_path):
            return False

        os.makedirs(output_dir, exist_ok=True)
        self._analysis_thread = AnalysisWorker(video_path, profile_name, output_dir)
        self._analysis_thread.finished.connect(on_done)
        self._analysis_thread.start()
        return True

    def start_export_all(self, video_path: str, highlights: list,
                         output_dir: str, on_done) -> bool:
        """Start async batch export for analyzed highlights."""
        if not self._exporter or not video_path or not os.path.isfile(video_path) or not highlights:
            return False

        os.makedirs(output_dir, exist_ok=True)
        self._batch_export_thread = BatchExportWorker(self._exporter, video_path, highlights, output_dir)
        self._batch_export_thread.finished.connect(on_done)
        self._batch_export_thread.start()
        return True

    # -- Cleanup --------------------------------------------------------

    def cleanup(self):
        """Release all resources.

        Waits for background threads (export, URL parser) to finish
        before cleaning up the capture, preventing use-after-free
        when the main window is closed during an active export.
        """
        self.timer.stop()
        self.watchdog.stop()

        # Wait for background QThreads to finish.
        # These workers override run() and do not run an event loop, so
        # quit() has no effect; we wait for them to finish naturally and
        # terminate only as a last resort.
        for worker, name in (
            (self._export_thread, "export"),
            (self._analysis_thread, "analysis"),
            (self._batch_export_thread, "batch_export"),
            (self._url_parser, "url_parser"),
        ):
            if worker and worker.isRunning():
                if not worker.wait(5000):
                    _log.warning("%s worker did not stop in time, terminating", name)
                    worker.terminate()
                    worker.wait(1000)

        if self._capture:
            if self._capture.is_recording:
                self._capture.stop()
            else:
                self._capture.force_cleanup()
