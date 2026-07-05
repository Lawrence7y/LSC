"""导出服务 — 封装视频切片导出业务逻辑，与 UI 无关。

ExportService 是导出功能的统一入口，
单房间录制页和多房间工作台都通过它来操作导出。

设计原则：
- 不依赖 Qt / PySide6，便于单元测试
- 使用回调函数而非 Qt 信号进行进度通知
- 支持批量导出
- 支持并发控制
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from threading import Lock

from lsc import get_logger
from lsc.config import ExportProfile, LscConfig, load_config
from lsc.core.models import Clip, ExportOptions, ExportResult
from lsc.exporter.clip import ClipExporter

_log = get_logger(__name__)


ExportProgressCallback = Callable[[str, float, float, float], None]
"""导出进度回调: (clip_id, percent, elapsed_sec, total_sec)"""

ExportDoneCallback = Callable[[ExportResult], None]
"""导出完成回调"""


@dataclass(slots=True)
class BatchExportResult:
    """批量导出结果汇总。"""

    total: int
    succeeded: int
    failed: int
    results: list[ExportResult]


class ExportService:
    """导出服务 — 管理所有导出任务。

    这是一个门面（Facade）类，统一封装了：
    - 单个片段导出
    - 批量导出
    - 导出进度跟踪
    - 缩略图生成
    - 导出清单保存

    线程安全：所有公共方法都可以安全地从任意线程调用。
    """

    _DEFAULT_MAX_CONCURRENT = 2

    def __init__(
        self,
        config: LscConfig | None = None,
        *,
        max_concurrent: int = _DEFAULT_MAX_CONCURRENT,
    ) -> None:
        self._config = config or load_config()
        self._exporter = ClipExporter(self._config)
        self._max_concurrent = max_concurrent
        self._executor: ThreadPoolExecutor | None = None
        self._futures: dict[str, Future[ExportResult]] = {}
        # clip_id -> 正在运行的 FFmpeg 进程，用于取消时终止
        self._processes: dict[str, object] = {}
        self._lock = Lock()
        self._on_progress: ExportProgressCallback | None = None
        self._on_done: ExportDoneCallback | None = None

    def set_progress_callback(self, callback: ExportProgressCallback | None) -> None:
        """设置导出进度回调。"""
        self._on_progress = callback

    def set_done_callback(self, callback: ExportDoneCallback | None) -> None:
        """设置单个导出完成回调。"""
        self._on_done = callback

    # ── 单个导出 ───────────────────────────────────────────

    def export_clip(
        self,
        video_path: str,
        clip: Clip,
        output_dir: str,
        options: ExportOptions | None = None,
        *,
        async_mode: bool = False,
    ) -> ExportResult | None:
        """导出一个视频片段。

        Args:
            video_path: 源视频文件路径
            clip: 要导出的片段信息
            output_dir: 输出目录
            options: 导出配置，为 None 时使用默认值
            async_mode: 是否异步执行

        Returns:
            同步模式下返回 ExportResult
            异步模式下返回 None（通过回调通知）
        """
        options = options or ExportOptions()

        if async_mode:
            self._export_async(video_path, clip, output_dir, options)
            return None

        return self._export_sync(video_path, clip, output_dir, options)

    def _export_sync(
        self,
        video_path: str,
        clip: Clip,
        output_dir: str,
        options: ExportOptions,
    ) -> ExportResult:
        """同步导出单个片段。"""
        profile = self._options_to_profile(options)

        try:
            result = self._exporter.export_clip(
                video_path,
                clip.start_sec,
                clip.end_sec,
                output_dir,
                title=clip.title,
                clip_index=0,
                profile=profile,
                progress_callback=self._make_progress_cb(clip.clip_id)
                if self._on_progress
                else None,
                on_process=lambda p: self._register_process(clip.clip_id, p),
            )
        finally:
            # 导出结束（正常完成或异常）后清理进程引用
            with self._lock:
                self._processes.pop(clip.clip_id, None)

        export_result = ExportResult(
            success=result.success,
            clip_id=clip.clip_id,
            output_path=result.output_path,
            thumbnail_path=result.thumbnail_path,
            duration_sec=result.duration,
            file_size_mb=result.file_size_mb,
            error=result.error,
        )

        if self._on_done is not None:
            try:
                self._on_done(export_result)
            except Exception as exc:
                _log.warning("Export done callback raised: %s", exc)

        return export_result

    def _export_async(
        self,
        video_path: str,
        clip: Clip,
        output_dir: str,
        options: ExportOptions,
    ) -> None:
        """异步导出单个片段。"""
        executor = self._ensure_executor()
        future = executor.submit(
            self._export_sync, video_path, clip, output_dir, options
        )
        with self._lock:
            self._futures[clip.clip_id] = future

        # 完成后自动清理，防止 _futures 字典无限增长
        def _on_done(f, cid: str = clip.clip_id) -> None:
            with self._lock:
                # 仅在仍是同一个 future 时移除（避免移除重新提交的新 future）
                if self._futures.get(cid) is f:
                    del self._futures[cid]

        future.add_done_callback(_on_done)

    def _make_progress_cb(
        self, clip_id: str
    ) -> Callable[[float, float, float], None]:
        """创建 FFmpeg 进度回调，转发给用户回调。"""
        on_progress = self._on_progress

        def _cb(percent: float, elapsed: float, total: float) -> None:
            if on_progress is not None:
                try:
                    on_progress(clip_id, percent, elapsed, total)
                except Exception as exc:
                    _log.warning("Progress callback raised: %s", exc)

        return _cb

    def _register_process(self, clip_id: str, proc: object) -> None:
        """注册正在运行的 FFmpeg 进程，供 cancel_export 终止。"""
        with self._lock:
            self._processes[clip_id] = proc

    # ── 批量导出 ───────────────────────────────────────────

    def export_all(
        self,
        video_path: str,
        clips: list[Clip],
        output_dir: str,
        options: ExportOptions | None = None,
    ) -> BatchExportResult:
        """批量导出多个片段（同步）。

        Args:
            video_path: 源视频文件路径
            clips: 要导出的片段列表
            output_dir: 输出目录
            options: 导出配置

        Returns:
            批量导出结果汇总
        """
        options = options or ExportOptions()
        results: list[ExportResult] = []

        for clip in clips:
            result = self._export_sync(video_path, clip, output_dir, options)
            results.append(result)

        succeeded = sum(1 for r in results if r.success)
        failed = len(results) - succeeded

        # 保存导出清单
        try:
            self.save_manifest(video_path, output_dir, results)
        except Exception as exc:
            _log.warning("Failed to save export manifest: %s", exc)

        return BatchExportResult(
            total=len(results),
            succeeded=succeeded,
            failed=failed,
            results=results,
        )

    def export_all_async(
        self,
        video_path: str,
        clips: list[Clip],
        output_dir: str,
        options: ExportOptions | None = None,
    ) -> None:
        """批量导出多个片段（异步）。"""
        options = options or ExportOptions()
        for clip in clips:
            self._export_async(video_path, clip, output_dir, options)

    # ── 导出状态查询 ───────────────────────────────────────

    def is_exporting(self, clip_id: str) -> bool:
        """检查指定片段是否正在导出。"""
        with self._lock:
            future = self._futures.get(clip_id)
            if future is None:
                return False
            return not future.done()

    def get_active_count(self) -> int:
        """获取当前正在进行的导出任务数量。"""
        with self._lock:
            return sum(1 for f in self._futures.values() if not f.done())

    def cancel_export(self, clip_id: str) -> bool:
        """取消一个导出任务，终止对应的 FFmpeg 进程。

        Returns:
            True 表示找到并处理了该任务（进程已终止或已不存在），False 表示任务不存在
        """
        with self._lock:
            proc = self._processes.pop(clip_id, None)
            future_existed = clip_id in self._futures

        # 终止仍在运行的 FFmpeg 进程
        if proc is not None and getattr(proc, "poll", lambda: None)() is None:
            try:
                proc.kill()
            except Exception as exc:
                _log.warning("Failed to kill FFmpeg process for %s: %s", clip_id, exc)

        return proc is not None or future_existed

    # ── 缩略图 ─────────────────────────────────────────────

    def generate_thumbnail(
        self,
        video_path: str,
        time_sec: float,
        output_dir: str,
        name: str,
    ) -> str:
        """生成视频缩略图。

        Args:
            video_path: 视频文件路径
            time_sec: 截取时间点（秒）
            output_dir: 输出目录
            name: 文件名（不含扩展名）

        Returns:
            缩略图文件路径，失败时返回空字符串
        """
        os.makedirs(output_dir, exist_ok=True)
        thumb_path = os.path.join(output_dir, f"{name}_thumb.jpg")

        cmd = [
            self._config.ffmpeg_path,
            "-y",
            "-loglevel",
            "quiet",
            "-ss",
            f"{time_sec:.3f}",
            "-i",
            video_path,
            "-vframes",
            "1",
            "-q:v",
            "3",
            thumb_path,
        ]

        import subprocess
        from lsc.utils.process_launcher import prepare_launch

        try:
            env, creation_flags, cwd = prepare_launch(self._config.ffmpeg_path)
            run_kwargs = {"capture_output": True, "timeout": 30, "env": env, "cwd": cwd}
            if creation_flags:
                run_kwargs["creationflags"] = creation_flags
            result = subprocess.run(cmd, **run_kwargs)
            if result.returncode == 0 and os.path.isfile(thumb_path):
                return thumb_path
        except Exception as exc:
            _log.warning("Thumbnail generation failed: %s", exc)

        return ""

    # ── 导出清单 ───────────────────────────────────────────

    @staticmethod
    def save_manifest(
        video_path: str,
        output_dir: str,
        results: list[ExportResult],
    ) -> str:
        """保存导出清单 JSON 文件。

        Args:
            video_path: 源视频路径
            output_dir: 输出目录
            results: 导出结果列表

        Returns:
            清单文件路径
        """
        import json

        manifest = {
            "source": video_path,
            "total_clips": len(results),
            "successful": sum(1 for r in results if r.success),
            "failed": sum(1 for r in results if not r.success),
            "clips": [
                {
                    "clip_id": r.clip_id,
                    "output": r.output_path,
                    "duration": r.duration_sec,
                    "size_mb": r.file_size_mb,
                    "thumbnail": r.thumbnail_path,
                    "success": r.success,
                    "error": r.error,
                }
                for r in results
            ],
        }

        manifest_path = os.path.join(output_dir, "export_manifest.json")
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
        return manifest_path

    # ── 工具方法 ───────────────────────────────────────────

    @staticmethod
    def safe_filename(title: str) -> str:
        """生成安全的文件名（去除非法字符，防止路径遍历）。"""
        # 去除 Windows / Linux 非法字符
        safe = re.sub(r'[\\/:*?"<>|]', "_", title)
        # 防止路径遍历
        safe = safe.replace("..", "__").strip(". ")
        # 确保非空
        if not safe:
            safe = "clip"
        return safe

    # ── 内部方法 ───────────────────────────────────────────

    def _ensure_executor(self) -> ThreadPoolExecutor:
        """确保线程池已创建（线程安全）。"""
        if self._executor is None:
            with self._lock:
                if self._executor is None:
                    self._executor = ThreadPoolExecutor(
                        max_workers=self._max_concurrent,
                        thread_name_prefix="lsc-export",
                    )
        return self._executor

    @staticmethod
    def _options_to_profile(options: ExportOptions) -> ExportProfile:
        """将 ExportOptions 转换为 ExportProfile。"""
        return ExportProfile(
            crf=options.crf,
            codec=options.codec,
            preset=options.preset,
            audio_bitrate=options.audio_bitrate,
            rate_mode=options.rate_mode,
            video_bitrate=options.video_bitrate,
            resolution=options.resolution,
            fps=options.fps,
            vertical_crop=options.vertical_crop,
        )

    def cleanup(self) -> None:
        """清理资源，等待所有导出任务完成。"""
        if self._executor is not None:
            self._executor.shutdown(wait=True)
            self._executor = None
        with self._lock:
            self._futures.clear()
