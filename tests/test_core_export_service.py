"""核心导出服务单元测试。

使用 mock 隔离 FFmpeg 依赖，专注于测试业务逻辑。
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from lsc.core.models import Clip, ExportOptions, ExportResult
from lsc.core.services.export_service import (
    BatchExportResult,
    ExportService,
)


@pytest.fixture
def sample_clip() -> Clip:
    return Clip(
        clip_id="clip-001",
        title="精彩瞬间",
        start_sec=10.0,
        end_sec=25.0,
        source_video="/tmp/source.mp4",
        score=0.85,
    )


@pytest.fixture
def sample_clips() -> list[Clip]:
    return [
        Clip(clip_id="c1", title="片段1", start_sec=0.0, end_sec=10.0),
        Clip(clip_id="c2", title="片段2", start_sec=20.0, end_sec=30.0),
        Clip(clip_id="c3", title="片段3", start_sec=40.0, end_sec=50.0),
    ]


@pytest.fixture
def service():
    """创建一个 mock 了底层 ClipExporter 的 ExportService。"""
    from lsc.exporter.clip import ExportResult as ClipExportResult

    with patch("lsc.core.services.export_service.ClipExporter") as MockExporter:
        mock_exporter = MagicMock()
        mock_exporter.export_clip.return_value = ClipExportResult(
            success=True,
            output_path="/tmp/out.mp4",
            clip_index=0,
            title="test",
            duration=15.0,
            file_size_mb=3.0,
        )
        MockExporter.return_value = mock_exporter
        svc = ExportService(max_concurrent=2)
        yield svc, mock_exporter


class TestExportOptions:
    def test_defaults(self):
        opts = ExportOptions()
        assert opts.codec == "libx264"
        assert opts.crf == 23
        assert opts.generate_thumbnail is True

    def test_custom(self):
        opts = ExportOptions(
            codec="copy",
            vertical_crop=True,
            generate_thumbnail=False,
        )
        assert opts.codec == "copy"
        assert opts.vertical_crop is True
        assert opts.generate_thumbnail is False


class TestExportServiceSafeFilename:
    def test_normal_title(self):
        result = ExportService.safe_filename("精彩片段")
        assert result == "精彩片段"

    def test_special_chars(self):
        result = ExportService.safe_filename('test/:*?"<>|.mp4')
        assert "/" not in result
        assert "\\" not in result
        assert ":" not in result
        assert "*" not in result
        assert "?" not in result
        assert '"' not in result
        assert "<" not in result
        assert ">" not in result
        assert "|" not in result

    def test_path_traversal(self):
        result = ExportService.safe_filename("../../../etc/passwd")
        assert ".." not in result
        assert "/" not in result

    def test_empty_title(self):
        result = ExportService.safe_filename("")
        assert result == "clip"

    def test_only_dots(self):
        result = ExportService.safe_filename("...")
        assert result != "..."
        assert len(result) > 0


class TestExportServiceSingleExport:
    def test_export_clip_sync_success(self, service, sample_clip, tmp_path):
        svc, mock_exporter = service

        result = svc.export_clip(
            "/tmp/source.mp4",
            sample_clip,
            str(tmp_path),
            async_mode=False,
        )

        assert result is not None
        assert result.success is True
        assert result.clip_id == "clip-001"
        assert result.duration_sec == 15.0
        assert result.file_size_mb == 3.0
        mock_exporter.export_clip.assert_called_once()

    def test_export_clip_sync_failure(self, service, sample_clip, tmp_path):
        svc, mock_exporter = service
        from lsc.exporter.clip import ExportResult as ClipExportResult

        mock_exporter.export_clip.return_value = ClipExportResult(
            success=False,
            output_path="",
            clip_index=0,
            title="test",
            error="FFmpeg error",
        )

        result = svc.export_clip(
            "/tmp/source.mp4",
            sample_clip,
            str(tmp_path),
            async_mode=False,
        )

        assert result is not None
        assert result.success is False
        assert result.error == "FFmpeg error"

    def test_export_clip_async(self, service, sample_clip, tmp_path):
        svc, mock_exporter = service
        from lsc.exporter.clip import ExportResult as ClipExportResult
        import time

        # 让 mock 稍微慢一点，确保 is_exporting 能检测到
        original_call_count = {"n": 0}

        def _slow_side_effect(*args, **kwargs):
            time.sleep(0.1)
            original_call_count["n"] += 1
            return ClipExportResult(
                success=True,
                output_path=str(tmp_path / "out.mp4"),
                clip_index=0,
                title="test",
                duration=15.0,
                file_size_mb=3.0,
            )

        mock_exporter.export_clip.side_effect = _slow_side_effect

        result = svc.export_clip(
            "/tmp/source.mp4",
            sample_clip,
            str(tmp_path),
            async_mode=True,
        )

        assert result is None
        # 异步导出刚启动时应该处于导出中状态
        assert svc.is_exporting("clip-001") is True
        assert svc.get_active_count() == 1
        svc.cleanup()  # 等待异步任务完成
        assert svc.get_active_count() == 0


class TestExportServiceBatchExport:
    def test_export_all_success(self, service, sample_clips, tmp_path):
        svc, mock_exporter = service
        from lsc.exporter.clip import ExportResult as ClipExportResult

        call_count = 0

        def _side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return ClipExportResult(
                success=True,
                output_path=str(tmp_path / f"clip_{call_count}.mp4"),
                clip_index=call_count,
                title=f"clip_{call_count}",
                duration=10.0,
                file_size_mb=1.0,
            )

        mock_exporter.export_clip.side_effect = _side_effect

        result = svc.export_all(
            "/tmp/source.mp4",
            sample_clips,
            str(tmp_path),
        )

        assert isinstance(result, BatchExportResult)
        assert result.total == 3
        assert result.succeeded == 3
        assert result.failed == 0
        assert len(result.results) == 3
        assert mock_exporter.export_clip.call_count == 3

    def test_export_all_partial_failure(self, service, sample_clips, tmp_path):
        svc, mock_exporter = service
        from lsc.exporter.clip import ExportResult as ClipExportResult

        call_count = 0

        def _side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                return ClipExportResult(
                    success=False,
                    output_path="",
                    clip_index=call_count,
                    title="fail",
                    error="test error",
                )
            return ClipExportResult(
                success=True,
                output_path=str(tmp_path / f"c{call_count}.mp4"),
                clip_index=call_count,
                title=f"c{call_count}",
                duration=10.0,
                file_size_mb=1.0,
            )

        mock_exporter.export_clip.side_effect = _side_effect

        result = svc.export_all(
            "/tmp/source.mp4",
            sample_clips,
            str(tmp_path),
        )

        assert result.total == 3
        assert result.succeeded == 2
        assert result.failed == 1


class TestExportServiceStatus:
    def test_get_active_count_empty(self, service):
        svc, _ = service
        assert svc.get_active_count() == 0

    def test_cancel_export_not_found(self, service):
        svc, _ = service
        assert svc.cancel_export("nonexistent") is False


class TestExportServiceThumbnail:
    def test_generate_thumbnail_success(self, service, tmp_path):
        svc, _ = service
        with patch("subprocess.run") as mock_run:
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_run.return_value = mock_result

            thumb_path = tmp_path / "test_thumb.jpg"
            thumb_path.touch()

            result = svc.generate_thumbnail(
                str(tmp_path / "video.mp4"),
                5.0,
                str(tmp_path),
                "test",
            )

            assert result.endswith("test_thumb.jpg")
            assert os.path.isfile(result)
            mock_run.assert_called_once()

    def test_generate_thumbnail_failure(self, service, tmp_path):
        svc, _ = service
        with patch("subprocess.run") as mock_run:
            mock_result = MagicMock()
            mock_result.returncode = 1
            mock_run.return_value = mock_result

            result = svc.generate_thumbnail(
                str(tmp_path / "video.mp4"),
                5.0,
                str(tmp_path),
                "fail_test",
            )

            assert result == ""


class TestExportServiceManifest:
    def test_save_manifest(self, tmp_path):
        results = [
            ExportResult(
                success=True,
                clip_id="c1",
                output_path="/tmp/c1.mp4",
                duration_sec=10.0,
                file_size_mb=1.0,
                thumbnail_path="/tmp/c1_thumb.jpg",
            ),
            ExportResult(
                success=False,
                clip_id="c2",
                error="failed",
            ),
        ]

        manifest_path = ExportService.save_manifest(
            "/tmp/source.mp4",
            str(tmp_path),
            results,
        )

        assert os.path.isfile(manifest_path)
        assert manifest_path.endswith("export_manifest.json")

        import json

        with open(manifest_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        assert data["source"] == "/tmp/source.mp4"
        assert data["total_clips"] == 2
        assert data["successful"] == 1
        assert data["failed"] == 1
        assert len(data["clips"]) == 2


class TestExportServiceCallbacks:
    def test_progress_callback(self, service, sample_clip, tmp_path):
        svc, mock_exporter = service
        from lsc.exporter.clip import ExportResult as ClipExportResult

        progress_callback = MagicMock()
        svc.set_progress_callback(progress_callback)

        def _export_side_effect(*args, **kwargs):
            cb = kwargs.get("progress_callback")
            if cb:
                cb(50.0, 7.5, 15.0)
            return ClipExportResult(
                success=True,
                output_path=str(tmp_path / "out.mp4"),
                clip_index=0,
                title="test",
                duration=15.0,
                file_size_mb=3.0,
            )

        mock_exporter.export_clip.side_effect = _export_side_effect

        svc.export_clip(
            "/tmp/source.mp4",
            sample_clip,
            str(tmp_path),
            async_mode=False,
        )

        progress_callback.assert_called_once()
        args = progress_callback.call_args[0]
        assert args[0] == "clip-001"
        assert args[1] == 50.0

    def test_done_callback(self, service, sample_clip, tmp_path):
        svc, mock_exporter = service
        from lsc.exporter.clip import ExportResult as ClipExportResult

        done_callback = MagicMock()
        svc.set_done_callback(done_callback)

        mock_exporter.export_clip.return_value = ClipExportResult(
            success=True,
            output_path=str(tmp_path / "out.mp4"),
            clip_index=0,
            title="test",
            duration=15.0,
            file_size_mb=3.0,
        )

        svc.export_clip(
            "/tmp/source.mp4",
            sample_clip,
            str(tmp_path),
            async_mode=False,
        )

        done_callback.assert_called_once()
        result = done_callback.call_args[0][0]
        assert isinstance(result, ExportResult)
        assert result.success is True
