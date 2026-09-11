"""Headless recording controller for RoomOrchestrator.

Provides pure core recording and export state without any lsc.gui / PySide6 / Qt dependencies.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime
from typing import Any
from uuid import uuid4

from lsc.recorder.capture import StreamCapture
from lsc.utils.gpu_ffmpeg import nvenc_available
from lsc.utils.process_launcher import kill_process_tree

_log = logging.getLogger(__name__)


class HeadlessRecordingController:
    """无头录制控制器。

    作为核心编排层与录制捕获/导出服务之间的桥梁，彻底解除对 `lsc.gui`
    和 Qt 事件循环的依赖。
    """

    def __init__(self) -> None:
        self.stream_url: str = ""
        self.input_args: list[str] | None = None
        self.video_path: str = ""
        self.record_manifest_path: str = ""
        self.is_recording: bool = False
        self.recording_start_mono: float = 0.0
        self.is_exporting: bool = False
        self._last_export_error: str = ""
        self.exporter_init_error: str = ""
        self._capture: StreamCapture | None = None
        self._exporter: Any = None
        self._export_lock = threading.Lock()
        self._export_threads: dict[str, threading.Thread] = {}
        self._export_processes: dict[str, Any] = {}
        self._cancelled_exports: dict[str, bool] = {}

    def init_capture(self) -> None:
        """初始化录制捕获组件。"""
        if self._capture is None:
            try:
                from lsc.config import load_config
                cfg = load_config()
                self._capture = StreamCapture(cfg)
            except Exception as exc:
                _log.debug("StreamCapture init deferred/failed: %s", exc)

    def init_exporter(self) -> None:
        """初始化导出组件。"""
        try:
            from lsc.config import load_config
            from lsc.exporter.clip import ClipExporter
            if self._exporter is None:
                cfg = load_config()
                self._exporter = ClipExporter(cfg)
                self.exporter_init_error = ""
        except Exception as exc:
            _log.warning("Failed to initialize exporter: %s", exc)
            self.exporter_init_error = str(exc) or "导出器初始化失败"
            self._exporter = None

    def is_nvenc_available(self) -> bool:
        """检测系统 NVENC 硬件加速是否可用。"""
        try:
            return nvenc_available()
        except Exception:
            return False

    def start_recording_with_crf(
        self,
        stream_url: str,
        output_dir: str,
        encoder: str,
        crf: int,
        param_mode: str = "crf",
        bitrate: str = "",
        bitrate_unit: str = "M",
        input_args: list[str] | None = None,
        resolution: str = "",
        framerate: str = "",
        audio_bitrate: str = "",
    ) -> tuple[bool, str, str, str]:
        """启动录制（单文件模式回退）。

        ⚠️ **2026-09-11 修复：这条回退路径此前是死的。** 原实现调用
        ``self._capture.start_with_crf(...)``，而 ``lsc/recorder/capture.py`` 的
        ``StreamCapture`` **从来没有这个方法**（``git log -S "def start_with_crf"
        -- lsc/recorder/capture.py`` 为空）→ 一旦走到这里就
        ``AttributeError: 'StreamCapture' object has no attribute 'start_with_crf'``。
        实测现场：无预览/shared-ingest 的环境（headless 起后端）会回退到这条路径 →
        录制报错，持续分析随即被拒（"主直播间尚未开始录制"）。
        真实入口是 ``StreamCapture.start(url, output_path, *, codec, input_args,
        extra_args)``；CRF/码率等编码参数走 ``codec="custom"`` + ``extra_args`` 传下去。
        产物仍按 ``*_录制中.mp4`` 命名，与收尾定稿、剪映草稿守卫的命名契约一致。
        """
        self.init_capture()
        if self._capture is None:
            return False, "", "", "Capture component unavailable"

        self.stream_url = stream_url
        self.input_args = input_args
        from lsc.core.recording_layout import recording_in_progress_path

        output_path = recording_in_progress_path(output_dir, datetime.now())
        extra_args = self._build_encoder_args(
            encoder, crf, param_mode, bitrate, bitrate_unit,
            resolution, framerate, audio_bitrate,
        )
        _log.info(
            "[录制] 回退路径启动: encoder=%s mode=%s crf=%s %s%s resolution=%s fps=%s → %s",
            encoder, param_mode, crf, bitrate, bitrate_unit, resolution, framerate, output_path,
        )
        try:
            ok = bool(self._capture.start(
                stream_url, output_path, codec="custom",
                input_args=input_args, extra_args=extra_args,
            ))
        except Exception as exc:  # noqa: BLE001 - 回退路径必须把失败转成返回值，不能抛穿
            _log.error("[录制] 回退路径启动异常: %s", exc)
            return False, "", "", f"start failed: {exc}"
        if ok:
            self.is_recording = True
            self.video_path = output_path
            self.recording_start_mono = time.monotonic()
            return True, output_path, encoder, ""
        err = (self._capture.last_error or self._capture.stderr_tail or "capture start failed").strip()
        return False, "", "", err

    @staticmethod
    def _build_encoder_args(
        encoder: str,
        crf: int,
        param_mode: str,
        bitrate: str,
        bitrate_unit: str,
        resolution: str,
        framerate: str,
        audio_bitrate: str,
    ) -> list[str]:
        """把 UI 的录制参数翻译成 ffmpeg 输出侧参数（``codec="custom"`` 用）。

        ``param_mode`` 口径与 ``python-backend/handlers/recording_handlers.py`` 的
        effective spec 一致：``CRF 质量`` / ``自定义码率`` / ``码率限制`` / ``不限制``。
        """
        args: list[str] = ["-c:v", encoder or "h264_nvenc"]
        mode = (param_mode or "").strip()
        if mode in ("自定义码率", "码率限制"):
            args += ["-b:v", f"{bitrate or '8000'}{bitrate_unit or 'kbps'}"]
        elif mode != "不限制":
            args += ["-crf", str(int(crf))]      # 默认（含 "CRF 质量"）走 CRF
        if encoder and encoder.endswith("_nvenc"):
            args += ["-preset", "p4"]
        if resolution and resolution != "原画":
            width, _, height = resolution.partition(":")
            if width.isdigit() and height.isdigit():
                args += ["-vf", f"scale={width}:{height}"]
        if framerate and framerate != "原画" and str(framerate).isdigit():
            args += ["-r", str(framerate)]
        args += ["-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", audio_bitrate or "128k"]
        return args

    def stop_recording(self) -> bool:
        """停止录制。"""
        if self._capture is not None and self.is_recording:
            try:
                self._capture.stop()
            except Exception as exc:
                _log.warning("Error stopping capture: %s", exc)
        self.is_recording = False
        return True

    def stop_recording_async(self) -> bool:
        """非阻塞停止录制（回退路径）。

        ⚠️ **2026-09-11 修复：与 ``StreamCapture`` 的接口不一致（第二处）。**
        ``orchestrator`` 有三处调用 ``controller.stop_recording_async()``，
        而控制器此前只有同步的 ``stop_recording`` → 停止录制时
        ``AttributeError: 'HeadlessRecordingController' object has no attribute
        'stop_recording_async'``（实测：验收脚本 stop_recording 失败，录像因此
        永远停在 ``_录制中``，剪映草稿被守卫永久拒绝）。真实接口是
        ``StreamCapture.stop_async()``（立即置 STOPPING、后台线程执行三级优雅停止）。
        """
        if self._capture is None:
            self.is_recording = False
            return False
        try:
            self._capture.stop_async()
        except Exception as exc:  # noqa: BLE001 - 停止路径同样不得抛穿
            _log.warning("Error stopping capture (async): %s", exc)
        self.is_recording = False
        return True

    def get_recording_duration(self) -> float:
        """获取已录制秒数。"""
        if not self.is_recording or self.recording_start_mono <= 0:
            return 0.0
        return max(0.0, time.monotonic() - self.recording_start_mono)

    def start_export(
        self,
        start_sec: float,
        end_sec: float,
        output_dir: str,
        name: str,
        on_done: Any = None,
        profile: Any = None,
        on_progress: Any = None,
    ) -> str:
        """启动异步导出切片任务。"""
        self.init_exporter()
        if self._exporter is None:
            self._last_export_error = self.exporter_init_error or "导出器未初始化"
            return ""

        export_input = self.video_path
        if not export_input or not os.path.isfile(export_input):
            self._last_export_error = f"录制文件不存在: {self.video_path}"
            return ""

        export_id = uuid4().hex
        self._last_export_error = ""
        self.is_exporting = True

        def _worker_run() -> None:
            import dataclasses

            from lsc.exporter.clip import ExportResult

            def _on_process(p: Any) -> None:
                with self._export_lock:
                    self._export_processes[export_id] = p

            kwargs: dict[str, Any] = {
                "title": name,
                "progress_callback": on_progress,
                "on_process": _on_process,
            }
            if profile is not None:
                kwargs["profile"] = profile

            try:
                result = self._exporter.export_clip(
                    export_input, start_sec, end_sec, output_dir, **kwargs
                )
            except Exception as exc:
                _log.exception("Headless export_clip raised: %s", exc)
                result = ExportResult(
                    False, "", "", "",
                    error=f"导出异常：{exc}", file_size_mb=0.0, thumbnail_path="",
                )

            with self._export_lock:
                was_cancelled = self._cancelled_exports.pop(export_id, False)
                self._export_threads.pop(export_id, None)
                self._export_processes.pop(export_id, None)
                if not self._export_threads:
                    self.is_exporting = False

            if was_cancelled and not result.success:
                result = dataclasses.replace(
                    result, success=False, output_path="",
                    error="导出已取消", file_size_mb=0.0, thumbnail_path="",
                )

            callback_args = (
                result.success,
                result.output_path,
                result.error,
                result.file_size_mb,
                result.thumbnail_path or "",
            )
            if on_done is not None:
                try:
                    on_done(*callback_args)
                except Exception:
                    _log.exception("on_done callback failed in headless export")

        thread = threading.Thread(
            target=_worker_run,
            name=f"export-{export_id[:8]}",
            daemon=True,
        )
        with self._export_lock:
            self._export_threads[export_id] = thread
        thread.start()
        return export_id

    def cancel_export(self, export_id: str | None = None) -> bool:
        """取消指定或当前的导出任务。"""
        with self._export_lock:
            if export_id is not None:
                if export_id in self._export_threads:
                    self._cancelled_exports[export_id] = True
                    proc = self._export_processes.get(export_id)
                    if proc is not None:
                        try:
                            kill_process_tree(proc)
                        except Exception:
                            pass
                    return True
                return False
            cancelled_any = False
            for eid, proc in list(self._export_processes.items()):
                self._cancelled_exports[eid] = True
                if proc is not None:
                    try:
                        kill_process_tree(proc)
                        cancelled_any = True
                    except Exception:
                        pass
            return cancelled_any
