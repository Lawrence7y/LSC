from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class AnalyzerCapabilities:
    realtime_continuous: bool
    posthoc_file: bool
    needs_ocr: bool
    needs_audio: bool
    game_specific: bool


@dataclass(slots=True)
class ScanWindow:
    start_sec: float
    end_sec: float
    timeout_sec: float
    use_ocr: bool


@runtime_checkable
class AnalyzerPlugin(Protocol):
    """无状态分析插件。会话状态放在 state dict，禁止写实例可变字段。"""

    game: str
    display_name: str

    def capabilities(self) -> AnalyzerCapabilities: ...

    def analyze_file(
        self,
        video_path: str,
        *,
        progress_callback: Callable[[str, float, str], None] | None = None,
        cancel_check: Callable[[], bool] | None = None,
        options: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]] | None: ...

    def plan_scan_window(
        self,
        state: dict[str, Any],
        current_dur: float,
        pressure: dict[str, Any],
    ) -> ScanWindow: ...

    def scan_window(
        self,
        video_path: str,
        window: ScanWindow,
        state: dict[str, Any],
        *,
        cancel_check: Callable[[], bool] | None = None,
    ) -> list[dict[str, Any]]: ...
