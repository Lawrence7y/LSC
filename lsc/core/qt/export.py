"""Qt 导出服务适配器 — 将 ExportService 包装为带 Qt 信号的类。

GUI 层可以通过此类使用核心导出服务，
使用 Qt 信号而非函数回调。
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from lsc import get_logger
from lsc.core.models import Clip, ExportOptions, ExportResult
from lsc.core.services.export_service import (
    BatchExportResult,
    ExportService,
)

_log = get_logger(__name__)


class QtExportService(QObject):
    """Qt 友好的导出服务适配器。

    提供与 ExportService 相同的功能，
    但使用 Qt 信号进行进度和状态通知。
    """

    # ── 信号 ────────────────────────────────────────────────

    export_progress = Signal(str, float, float, float)
    """信号: 导出进度 (clip_id, percent, elapsed_sec, total_sec)"""

    export_done = Signal(object)
    """信号: 单个导出完成 (ExportResult)"""

    export_started = Signal(str)
    """信号: 导出任务开始 (clip_id)"""

    batch_complete = Signal(object)
    """信号: 批量导出完成 (BatchExportResult)"""

    thumbnail_done = Signal(str, str)
    """信号: 缩略图生成完成 (clip_id, thumbnail_path)"""

    def __init__(self, parent: QObject | None = None, *, max_concurrent: int = 2) -> None:
        super().__init__(parent)
        self._service = ExportService(max_concurrent=max_concurrent)
        self._service.set_progress_callback(self._on_progress)
        self._service.set_done_callback(self._on_done)

    # ── 单个导出 ───────────────────────────────────────────

    def export_clip(
        self,
        video_path: str,
        clip: Clip,
        output_dir: str,
        options: ExportOptions | None = None,
        *,
        async_mode: bool = True,
    ) -> ExportResult | None:
        """导出一个视频片段。

        Args:
            video_path: 源视频路径
            clip: 片段信息
            output_dir: 输出目录
            options: 导出配置
            async_mode: 是否异步（默认异步，通过信号通知）

        Returns:
            同步模式返回 ExportResult，异步模式返回 None
        """
        if async_mode:
            self.export_started.emit(clip.clip_id)

        return self._service.export_clip(
            video_path,
            clip,
            output_dir,
            options,
            async_mode=async_mode,
        )

    # ── 批量导出 ───────────────────────────────────────────

    def export_all(
        self,
        video_path: str,
        clips: list[Clip],
        output_dir: str,
        options: ExportOptions | None = None,
    ) -> BatchExportResult:
        """批量导出（同步）。

        完成后通过 batch_complete 信号通知。
        """
        result = self._service.export_all(video_path, clips, output_dir, options)
        self.batch_complete.emit(result)
        return result

    def export_all_async(
        self,
        video_path: str,
        clips: list[Clip],
        output_dir: str,
        options: ExportOptions | None = None,
    ) -> None:
        """批量导出（异步）。"""
        for clip in clips:
            self.export_started.emit(clip.clip_id)
        self._service.export_all_async(video_path, clips, output_dir, options)

    # ── 状态查询 ───────────────────────────────────────────

    def is_exporting(self, clip_id: str) -> bool:
        """检查指定片段是否正在导出。"""
        return self._service.is_exporting(clip_id)

    def get_active_count(self) -> int:
        """获取当前正在进行的导出任务数量。"""
        return self._service.get_active_count()

    def has_active_exports(self) -> bool:
        """是否有正在进行的导出任务。"""
        return self._service.get_active_count() > 0

    def cancel_export(self, clip_id: str) -> bool:
        """取消一个导出任务。"""
        return self._service.cancel_export(clip_id)

    # ── 缩略图 ─────────────────────────────────────────────

    def generate_thumbnail(
        self,
        video_path: str,
        time_sec: float,
        output_dir: str,
        name: str,
    ) -> str:
        """生成视频缩略图。

        Returns:
            缩略图路径，失败时返回空字符串
        """
        result = self._service.generate_thumbnail(video_path, time_sec, output_dir, name)
        if result:
            self.thumbnail_done.emit(name, result)
        return result

    # ── 工具方法 ───────────────────────────────────────────

    @staticmethod
    def safe_filename(title: str) -> str:
        """生成安全的文件名。"""
        return ExportService.safe_filename(title)

    @staticmethod
    def save_manifest(video_path: str, output_dir: str, results: list[ExportResult]) -> str:
        """保存导出清单。"""
        return ExportService.save_manifest(video_path, output_dir, results)

    # ── 内部回调 ───────────────────────────────────────────

    def _on_progress(
        self,
        clip_id: str,
        percent: float,
        elapsed: float,
        total: float,
    ) -> None:
        """核心服务的进度回调 — 转发为 Qt 信号。"""
        self.export_progress.emit(clip_id, percent, elapsed, total)

    def _on_done(self, result: ExportResult) -> None:
        """核心服务的完成回调 — 转发为 Qt 信号。"""
        self.export_done.emit(result)

    # ── 底层访问（迁移过渡期使用） ─────────────────────

    @property
    def core_service(self) -> ExportService:
        """直接访问底层核心服务（迁移过渡期使用，新代码尽量避免）。"""
        return self._service

    def cleanup(self) -> None:
        """清理资源，等待所有导出任务完成。"""
        self._service.cleanup()


__all__ = ["QtExportService"]
