"""Common background worker threads for LSC GUI.

This module houses worker threads that are shared across different pages
or managers, resolving cross-layer dependency violations.
"""
from __future__ import annotations

import os
from argparse import Namespace
from PySide6.QtCore import QThread, Signal

from lsc.exporter.clip import ClipExporter


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
            results = list(self._exporter.export_all(
                self._video_path, self._highlights, self._output_dir
            ))
            ClipExporter.save_export_manifest(
                self._video_path, self._output_dir, results
            )
            exported_count = sum(1 for result in results if result.success)
            self.finished.emit(True, exported_count, len(results), "", results)
        except Exception as exc:
            self.finished.emit(False, 0, 0, str(exc), None)
