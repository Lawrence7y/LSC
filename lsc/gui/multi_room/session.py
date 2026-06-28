"""Session model for multi-room workbench."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from lsc.platforms.base import StreamInfo
from lsc.platforms.registry import get_display_name


@dataclass(slots=True)
class RoomSession:
    room_id: str
    room_url: str
    platform: str = ""
    platform_name: str = ""
    streamer_name: str = ""
    stream_title: str = ""
    stream_info: StreamInfo | None = None
    selected_quality: str = ""
    preview_muted: bool = True
    preview_enabled: bool = False
    preview_paused: bool = False
    preview_error: str = ""
    include_in_cut: bool = True
    is_connecting: bool = False
    is_connected: bool = False
    is_recording: bool = False
    record_output_path: str = ""
    record_started_at: datetime | None = None
    last_error: str = ""
    controller: object | None = None
    preview_widget: object | None = None
    # 每房间独立的时间线选区（秒），避免切房间时选区"串房"
    mark_in: float | None = None
    mark_out: float | None = None
    # 墙钟时间轴（time.monotonic）：标记时的绝对时间戳，用于精确对齐录制文件位置。
    # 切片时通过 mark_in_wallclock - recording_start_mono 转换为录制文件中的秒位置，
    # 消除预览延迟和预览重启导致的 currentTime 偏移。
    mark_in_wallclock: float | None = None
    mark_out_wallclock: float | None = None
    # 录制开始的 monotonic 时间戳，由 manager 在录制启动时回填
    recording_start_mono: float | None = None
    # 预览延迟（秒），即从录制开始到首个 MSE segment 到达的延迟，默认 2.0 秒
    preview_latency: float = 2.0
    # 录制文件大小（MB），由 manager 每 tick 回填
    record_size_mb: float = 0.0
    # 直播流分辨率/帧率（由 ffprobe 异步探测，连接成功后回填）
    stream_resolution: str = ""
    stream_fps: str = ""
    # 分析高光片段列表（由分析流程填充；目前仅作为可用性判断存在，
    # 留空则"导出分析高光"按钮保持禁用）
    analysis_highlights: list = field(default_factory=list)
    # 分析/导出进行中标志（默认未进行；流程启动时置 True、完成后回 False）
    analysis_in_progress: bool = False
    export_in_progress: bool = False
    # 自动重连状态
    reconnect_next_attempt_at: float = 0.0
    reconnect_attempts: int = 0
    reconnect_output_dir: str = ""
    reconnect_encoder: str = ""
    reconnect_crf: int = 23
    reconnect_param_mode: str = "CRF 质量"
    reconnect_bitrate: str = ""
    reconnect_bitrate_unit: str = "kbps"
    # 每房间独立的预览画质（覆盖全局设置），空字符串表示使用全局设置
    preview_quality: str = ""

    def apply_stream_info(self, info: StreamInfo) -> None:
        self.platform = info.platform
        self.platform_name = get_display_name(info.platform)
        self.stream_info = info
        self.selected_quality = info.selected_quality
        self.is_connected = bool(info.is_live and info.stream_url)
        self.is_connecting = False
        self.streamer_name = getattr(info, "streamer", "") or ""
        self.stream_title = getattr(info, "title", "") or ""
        self.last_error = ""

    def set_error(self, message: str) -> None:
        self.is_connected = False
        self.is_connecting = False
        self.last_error = message

    def status_text(self) -> str:
        parts: list[str] = []
        if self.is_connecting:
            parts.append("连接中")
        elif self.is_recording:
            parts.append("录制中")
        elif self.is_connected:
            parts.append("已连接")
        else:
            parts.append("未连接")
        if self.preview_enabled:
            parts.append("已暂停" if self.preview_paused else "预览中")
        if self.last_error:
            parts.append(self.friendly_error)
        return "，".join(parts)

    @property
    def friendly_error(self) -> str:
        """返回用户友好的错误描述。"""
        from lsc.utils.error_messages import humanize_error
        return humanize_error(self.last_error)
