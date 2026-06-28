"""核心领域模型 — 纯数据类，无业务逻辑。

所有模型使用 dataclass，保持简单纯粹，
序列化友好，便于在 GUI 层和核心层之间传递。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class RecordingStatus(str, Enum):
    """录制状态枚举。"""

    IDLE = "idle"
    CONNECTING = "connecting"
    RECORDING = "recording"
    PAUSED = "paused"
    STOPPED = "stopped"
    ERROR = "error"
    RECONNECTING = "reconnecting"


@dataclass(slots=True)
class StreamQuality:
    """直播流画质选项。"""

    name: str
    url: str


@dataclass(slots=True)
class RoomInfo:
    """直播间信息。

    从平台适配器解析出来的房间元数据，
    用于展示和后续录制/预览。
    """

    platform: str
    room_url: str
    stream_url: str = ""
    title: str = ""
    streamer: str = ""
    is_live: bool = False
    qualities: list[StreamQuality] = field(default_factory=list)
    selected_quality: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    error: str = ""
    error_code: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class RecordingSession:
    """录制会话领域模型。

    代表一次完整的录制过程，从开始到停止。
    包含录制状态、输出路径、编码参数等。
    """

    session_id: str
    room_url: str
    output_dir: str
    output_path: str = ""
    status: RecordingStatus = RecordingStatus.IDLE
    stream_url: str = ""
    stream_title: str = ""
    streamer_name: str = ""
    platform: str = ""
    start_time: datetime | None = None
    end_time: datetime | None = None
    duration_sec: float = 0.0
    file_size_mb: float = 0.0
    encoder: str = ""
    crf: int | None = None
    bitrate: str = ""
    last_error: str = ""
    reconnect_attempts: int = 0
    max_reconnect_attempts: int = 3


@dataclass(slots=True)
class Clip:
    """视频片段（切片）。

    代表从一个完整录制中切出的一个精彩片段。
    """

    clip_id: str
    title: str
    start_sec: float
    end_sec: float
    source_video: str = ""
    output_path: str = ""
    thumbnail_path: str = ""
    duration_sec: float = 0.0
    file_size_mb: float = 0.0
    score: float = 0.0
    exported: bool = False
    error: str = ""
    # 墙钟时间戳（time.monotonic），用于精确对齐录制文件位置
    mark_in_wallclock: float = 0.0
    mark_out_wallclock: float = 0.0


@dataclass(slots=True)
class ExportOptions:
    """导出配置选项。

    对应用户在导出时选择的编码参数。
    使用 dataclass 而非直接传 dict，便于类型检查和重构。
    """

    codec: str = "libx264"
    crf: int = 23
    preset: str = "medium"
    audio_bitrate: str = "128k"
    rate_mode: str = "crf"  # "crf" | "bitrate" | "unrestricted"
    video_bitrate: str = "8000k"
    resolution: str = ""  # 空字符串表示不缩放，如 "1920x1080"
    fps: float = 0.0  # 0 表示保持原帧率
    vertical_crop: bool = False
    generate_thumbnail: bool = True


@dataclass(slots=True)
class ExportResult:
    """单次导出的结果。"""

    success: bool
    clip_id: str
    output_path: str = ""
    thumbnail_path: str = ""
    duration_sec: float = 0.0
    file_size_mb: float = 0.0
    error: str = ""
