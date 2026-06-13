"""Recording controller 鈥?business logic extracted from RecordPage.

Separates recording concerns (FFmpeg capture, timer, NVENC probe, URL parsing)
from pure UI rendering, following the ViewModel / Controller pattern.
"""

from __future__ import annotations

import os
import subprocess
import time as _time
import logging
from argparse import Namespace
from datetime import datetime, timezone

from PySide6.QtCore import QThread, QTimer, Signal

try:
    from lsc import get_logger
except ImportError:
    def get_logger(name: str):
        return logging.getLogger(name)

from lsc.platforms.registry import parse_stream, select_quality

_log = get_logger(__name__)

_ESTIMATED_MB_PER_SEC = 0.45  # Rough estimate for 1080p H.264


def friendly_ffmpeg_exit_message(exit_code: int, stderr_tail: str) -> str:
    """Map FFmpeg exit code + stderr to a user-friendly Chinese message.

    Delegates to the capture module's internal mapping for consistency.
    """
    from lsc.recorder.capture import _friendly_ffmpeg_message

    return _friendly_ffmpeg_message(exit_code, stderr_tail)
_QUALITY_PRESET_CANDIDATES = {
    "原画": ["origin", "FULL_HD1", "uhd", "UHD1", "hd", "HD1", "sd", "SD1", "SD2", "ld", "ao"],
    "高清": ["hd", "HD1", "uhd", "UHD1", "FULL_HD1", "sd", "SD1", "origin", "SD2", "ld", "ao"],
    "流畅": ["sd", "SD1", "SD2", "ld", "hd", "HD1", "uhd", "UHD1", "origin", "FULL_HD1", "ao"],
}


class UrlParserWorker(QThread):
    """Background thread for parsing Douyin page URL."""

    finished = Signal(dict)

    def __init__(self, page_url: str, parse_fn):
        super().__init__()
        self._url = page_url
        self._parse_fn = parse_fn

    def run(self):
        result = self._parse_fn(self._url)
        self.finished.emit(result)


class ExportWorker(QThread):
    """Background thread for clip export."""

    finished = Signal(bool, str, str, float)  # success, path, error, size_mb

    def __init__(self, exporter, video_path, start, end, output_dir, title):
        super().__init__()
        self._exporter = exporter
        self._video_path = video_path
        self._start = start
        self._end = end
        self._output_dir = output_dir
        self._title = title

    def run(self):
        result = self._exporter.export_clip(
            self._video_path, self._start, self._end, self._output_dir,
            title=self._title,
        )
        self.finished.emit(result.success, result.output_path, result.error, result.file_size_mb)


class AnalysisWorker(QThread):
    """Background thread for recording analysis."""

    finished = Signal(bool, str, str, int)  # success, result_path, error, highlight_count

    def __init__(self, video_path: str, profile_name: str, output_dir: str):
        super().__init__()
        self._video_path = video_path
        self._profile_name = profile_name
        self._output_dir = output_dir

    def run(self):
        try:
            from lsc.cli import cmd_analyze

            base_name = os.path.splitext(os.path.basename(self._video_path))[0]
            result_path = os.path.join(self._output_dir, f"{base_name}_lsc_analysis.json")
            args = Namespace(
                video=self._video_path,
                config="",
                profile=self._profile_name,
                output=result_path,
            )
            result = cmd_analyze(args)
            highlights = result.get("highlights", []) if isinstance(result, dict) else []
            self.finished.emit(True, result_path, "", len(highlights))
        except SystemExit as exc:
            self.finished.emit(False, "", f"分析失败 (exit {exc.code})", 0)
        except Exception as exc:
            self.finished.emit(False, "", str(exc), 0)


class BatchExportWorker(QThread):
    """Background thread for batch highlight export."""

    finished = Signal(bool, int, int, str, object)  # success, exported_count, total_count, error, results

    def __init__(self, exporter, video_path: str, highlights: list, output_dir: str):
        super().__init__()
        self._exporter = exporter
        self._video_path = video_path
        self._highlights = highlights
        self._output_dir = output_dir

    def run(self):
        try:
            results = self._exporter.export_all(self._video_path, self._highlights, self._output_dir)
            exported_count = sum(1 for result in results if result.success)
            self.finished.emit(True, exported_count, len(self._highlights), "", results)
        except Exception as exc:
            self.finished.emit(False, 0, len(self._highlights), str(exc), [])


class RecordingController:
    """Non-UI controller that manages the recording lifecycle.

    Owns: StreamCapture, timers, export workers, URL parsing.
    Emits signals via Qt signals on the RecordPage.
    """

    def __init__(self):
        self.is_recording = False
        self.has_start = False
        self.start_sec: float | None = None
        self.end_sec: float | None = None
        self.total_sec: int = 0
        self.record_start_mono: float = 0.0

        self.video_path: str = ""
        self.stream_url: str = ""
        self.page_url: str = ""
        self.last_stream_info = None
        self.input_args: list[str] = []
        self.output_dir: str = ""
        self.exported: list[tuple] = []

        self._capture = None
        self._exporter = None
        self._export_thread: ExportWorker | None = None
        self._analysis_thread: AnalysisWorker | None = None
        self._batch_export_thread: BatchExportWorker | None = None
        self._url_parser: UrlParserWorker | None = None
        self.capture_init_error = ""
        self.exporter_init_error = ""

        # Timers (owned here, connected externally)
        self.timer = QTimer()
        self.watchdog = QTimer()

    # 鈹€鈹€ Initialisation 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

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

    # 鈹€鈹€ Preflight checks 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

    _MIN_RECORDING_FREE_BYTES = 8 * 1024 * 1024 * 1024  # 8 GB

    def preflight_recording(self, output_dir: str) -> str:
        """Check disk space before recording. Returns error message or empty."""
        import shutil

        os.makedirs(output_dir, exist_ok=True)
        _total, _used, free = shutil.disk_usage(output_dir)
        if free < self._MIN_RECORDING_FREE_BYTES:
            free_gb = free / (1024 ** 3)
            return f"磁盘空间不足，当前仅剩 {free_gb:.1f} GB"
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

    # 鈹€鈹€ Capture accessors 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

    @property
    def capture(self):
        return self._capture

    @property
    def capture_is_recording(self) -> bool:
        return self._capture is not None and self._capture.is_recording

    def check_capture_crash(self) -> int | None:
        """Public API: check if FFmpeg process has crashed.

        Returns exit code if crashed, None if healthy or no capture.
        Safe to call from UI layer 鈥?no private-member access needed.
        """
        if not self._capture or not self._capture.is_recording:
            return None
        return self._capture.check_and_handle_crash()

    # 鈹€鈹€ URL parsing 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

    def parse_stream_url(self, url: str) -> dict:
        """Parse a supported room URL into the legacy dict consumed by the GUI."""
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
        self._url_parser = UrlParserWorker(url, self.parse_stream_url)
        self._url_parser.finished.connect(on_parsed)
        self._url_parser.start()

    # 鈹€鈹€ NVENC probe 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

    @staticmethod
    def check_nvenc_available() -> bool:
        """Quick test: can FFmpeg use h264_nvenc on this system?"""
        try:
            from lsc.config import load_config
            cfg = load_config()
            ffmpeg = cfg.ffmpeg_path
            if not ffmpeg or not os.path.isfile(ffmpeg):
                return False
            result = subprocess.run(
                [ffmpeg, "-y", "-loglevel", "error",
                 "-f", "lavfi", "-i", "testsrc=duration=1:size=64x64:rate=1",
                 "-c:v", "h264_nvenc", "-frames:v", "1",
                 "-f", "null", "-"],
                capture_output=True, text=True, timeout=10,
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

    # 鈹€鈹€ Recording lifecycle 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

    def start_recording_with_crf(self, stream_url: str, output_dir: str,
                                  encoder: str, crf: int,
                                  param_mode: str = "CRF 质量",
                                  bitrate: str | None = None,
                                  bitrate_unit: str = "kbps",
                                  input_args: list[str] | None = None,
                                  on_status=None) -> tuple[bool, str, str]:
        """Start recording with explicit CRF value.

        CRF is respected for all encoder modes:
        - H.264 CPU: -crf <value>
        - H.264 NVENC: -cq <value>  (NVENC's equivalent of CRF)
        - Copy: CRF is ignored (stream is saved as-is)

        Sets is_recording and record_start_mono atomically so the UI
        layer doesn't need to set them separately.
        """
        if not self._capture:
            return False, "", encoder

        os.makedirs(output_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = os.path.join(output_dir, f"recording_{timestamp}.mp4")

        if input_args is None:
            input_args = []

        output_args: list[str] = []
        encoder_used = encoder

        target_bitrate = self._normalize_bitrate_value(bitrate, bitrate_unit)
        bufsize = ""

        if encoder == "H.264 NVENC":
            if on_status:
                on_status("检测 NVENC 硬件编码...", "info")
            if not self.check_nvenc_available():
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
            output_args += ["-c:a", "aac", "-b:a", "128k"]
            self._capture.start(stream_url, output_path,
                                codec="custom", input_args=input_args,
                                extra_args=output_args)
        elif encoder_used == "H.264 CPU":
            # Use -preset medium for better compression efficiency than fast
            # (same CRF = smaller file, slightly higher CPU usage)
            output_args += ["-c:v", "libx264", "-preset", "medium", "-crf", str(crf)]
            output_args += ["-c:a", "aac", "-b:a", "128k"]
            self._capture.start(stream_url, output_path,
                                codec="custom", input_args=input_args,
                                extra_args=output_args)
        elif encoder_used == "H.264 NVENC" and param_mode == "码率限制" and target_bitrate:
            output_args += ["-c:v", "h264_nvenc", "-preset", "p4", "-rc", "cbr_hq"]
            output_args += ["-b:v", target_bitrate, "-maxrate", target_bitrate]
            if bufsize:
                output_args += ["-bufsize", bufsize]
            output_args += ["-c:a", "aac", "-b:a", "128k"]
            self._capture.start(stream_url, output_path,
                                codec="custom", input_args=input_args,
                                extra_args=output_args)
        elif encoder_used == "H.264 NVENC":
            # NVENC uses -cq (constant quality) which is the NVENC equivalent of CRF
            output_args += ["-c:v", "h264_nvenc", "-preset", "p4", "-rc", "vbr", "-cq", str(crf), "-b:v", "0"]
            output_args += ["-c:a", "aac", "-b:a", "128k"]
            self._capture.start(stream_url, output_path,
                                codec="custom", input_args=input_args,
                                extra_args=output_args)
        else:
            self._capture.start(stream_url, output_path,
                                input_args=input_args,
                                extra_args=output_args)

        if self._capture.status.value == "recording":
            # Capture confirmed running 鈥?set controller state atomically
            self.is_recording = True
            self.total_sec = 0
            self.record_start_mono = _time.monotonic()
            self.has_start = False
            self.start_sec = None
            self.end_sec = None
            self.stream_url = stream_url
            self.video_path = output_path
            return True, output_path, encoder_used
        else:
            return False, "", encoder_used

    def stop_recording(self) -> tuple[bool, float, str]:
        """Stop recording. Returns (success, size_mb, output_path)."""
        self.is_recording = False
        size_mb = 0.0
        output_path = self.video_path

        if self._capture and self._capture.is_recording:
            result = self._capture.stop()
            if result.success:
                self.video_path = result.output_path
                size_mb = result.file_size_mb
                output_path = result.output_path
                return True, size_mb, output_path
        elif self._capture and not self._capture.is_alive() and self.video_path:
            if os.path.isfile(self.video_path):
                size_mb = os.path.getsize(self.video_path) / (1024 * 1024)
                return True, size_mb, output_path

        return False, size_mb, output_path

    def probe_video_duration(self) -> float:
        """Use FFprobe to get actual video duration."""
        if not self.video_path or not os.path.isfile(self.video_path):
            return 0.0
        try:
            import json
            from lsc.config import load_config
            cfg = load_config()
            ffprobe = cfg.ffprobe_path
            if not ffprobe or not os.path.isfile(ffprobe):
                return 0.0
            cmd = [ffprobe, "-v", "quiet", "-print_format", "json",
                   "-show_format", self.video_path]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            data = json.loads(result.stdout)
            dur = float(data.get("format", {}).get("duration", 0))
            return dur if dur > 0 else 0.0
        except Exception:
            return 0.0

    # 鈹€鈹€ Timer tick 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

    def tick(self) -> int:
        """Update elapsed time using monotonic clock. Returns current total_sec."""
        if self.is_recording:
            self.total_sec = int(_time.monotonic() - self.record_start_mono)
        return self.total_sec

    # 鈹€鈹€ Watchdog 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

    def watchdog_check(self) -> str:
        """Check FFmpeg health. Returns error message or empty string."""
        if not self._capture or not self.is_recording:
            return ""
        # Use public API 鈥?no private-member access
        exit_code = self._capture.check_and_handle_crash()
        if exit_code is not None:
            return f"FFmpeg 异常退出 (code {exit_code})"
        msg = self._capture.check_health()
        return msg

    # 鈹€鈹€ Export 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

    def start_export(self, start_sec: float, end_sec: float,
                     output_dir: str, name: str,
                     on_done) -> bool:
        """Start async clip export. Returns True if export was started."""
        if not self._exporter or not self.video_path or not os.path.isfile(self.video_path):
            return False

        self._export_thread = ExportWorker(
            self._exporter, self.video_path,
            start_sec, end_sec, output_dir, name,
        )
        self._export_thread.finished.connect(on_done)
        self._export_thread.start()
        return True

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

    # 鈹€鈹€ Cleanup 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

    def cleanup(self):
        """Release all resources.

        Waits for background threads (export, URL parser) to finish
        before cleaning up the capture, preventing use-after-free
        when the main window is closed during an active export.
        """
        self.timer.stop()
        self.watchdog.stop()

        # Wait for background QThreads to finish
        if self._export_thread and self._export_thread.isRunning():
            self._export_thread.quit()
            self._export_thread.wait(5000)
        if self._analysis_thread and self._analysis_thread.isRunning():
            self._analysis_thread.quit()
            self._analysis_thread.wait(5000)
        if self._batch_export_thread and self._batch_export_thread.isRunning():
            self._batch_export_thread.quit()
            self._batch_export_thread.wait(5000)
        if self._url_parser and self._url_parser.isRunning():
            self._url_parser.quit()
            self._url_parser.wait(5000)

        if self._capture:
            if self._capture.is_recording:
                self._capture.stop()
            else:
                self._capture.force_cleanup()



