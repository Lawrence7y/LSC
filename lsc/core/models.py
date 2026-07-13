"""核心领域模型 — 纯数据类，无业务逻辑。

所有模型使用 dataclass，保持简单纯粹，
序列化友好，便于在 GUI 层和核心层之间传递。

模块职责:
- 定义直播录制全生命周期中的核心数据结构
- 作为各层之间传递数据的契约（DTO）
- 不包含任何业务逻辑或服务调用
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class RecordingStatus(str, Enum):
    """录制状态枚举，表示录制会话在整个生命周期中的阶段。"""

    IDLE = "idle"  # 空闲状态，未开始录制
    CONNECTING = "connecting"  # 正在连接直播服务器
    RECORDING = "recording"  # 正在录制中
    PAUSED = "paused"  # 录制暂停中
    STOPPED = "stopped"  # 已停止录制（正常结束）
    ERROR = "error"  # 发生错误，录制终止
    RECONNECTING = "reconnecting"  # 正在重连服务器（直播中断）


@dataclass(slots=True)
class StreamQuality:
    """直播流画质选项。"""

    name: str  # 画质名称（如 "原画", "高清", "标清"）
    url: str  # 对应画质的直播流地址


@dataclass(slots=True)
class RoomInfo:
    """直播间信息。

    从平台适配器解析出来的房间元数据，
    用于展示和后续录制/预览。
    """

    platform: str  # 直播平台标识（如 "douyin", "bilibili"）
    room_url: str  # 直播间页面 URL
    stream_url: str = ""  # 实际直播流地址（解析后填充）
    title: str = ""  # 直播标题
    streamer: str = ""  # 主播昵称
    is_live: bool = False  # 是否正在直播
    qualities: list[StreamQuality] = field(default_factory=list)  # 可用画质列表
    selected_quality: str = ""  # 当前选中的画质名称
    headers: dict[str, str] = field(default_factory=dict)  # 请求直播流所需的额外 HTTP 头
    error: str = ""  # 解析错误信息（如有）
    error_code: str = ""  # 平台错误码（如有）
    raw: dict[str, Any] = field(default_factory=dict)  # 平台原始响应数据，用于调试


@dataclass(slots=True)
class RecordingSession:
    """录制会话领域模型。

    代表一次完整的录制过程，从开始到停止。
    包含录制状态、输出路径、编码参数等。
    """

    session_id: str  # 唯一会话 ID
    room_url: str  # 目标直播间 URL
    output_dir: str  # 录制文件输出目录
    output_path: str = ""  # 完整输出文件路径（录制结束后确定）
    status: RecordingStatus = RecordingStatus.IDLE  # 当前录制状态
    stream_url: str = ""  # 实际使用的直播流地址
    stream_title: str = ""  # 录制时直播标题快照
    streamer_name: str = ""  # 主播昵称快照
    platform: str = ""  # 平台标识快照
    start_time: datetime | None = None  # 录制开始时间（None 表示未开始）
    end_time: datetime | None = None  # 录制结束时间（None 表示未结束）
    duration_sec: float = 0.0  # 录制时长（秒）
    file_size_mb: float = 0.0  # 输出文件大小（MB）
    encoder: str = ""  # 使用的视频编码器名称
    crf: int | None = None  # 恒定质量因子（None 表示未设置）
    bitrate: str = ""  # 视频码率（如 "8000k"）
    last_error: str = ""  # 最近一次错误信息
    reconnect_attempts: int = 0  # 已重连次数
    max_reconnect_attempts: int = 3  # 最大允许重连次数


@dataclass(slots=True)
class Clip:
    """视频片段（切片）。

    代表从一个完整录制中切出的一个精彩片段。
    同时承载录制时的关键帧标记，用于精确定位视频内容。
    """

    clip_id: str  # 唯一片段 ID
    title: str  # 片段标题
    start_sec: float  # 在源视频中的开始时间（秒，相对录制起点）
    end_sec: float  # 在源视频中的结束时间（秒，相对录制起点）
    source_video: str = ""  # 源录制文件路径
    output_path: str = ""  # 导出后的片段文件路径
    thumbnail_path: str = ""  # 缩略图文件路径
    duration_sec: float = 0.0  # 片段时长（秒）
    file_size_mb: float = 0.0  # 输出文件大小（MB）
    score: float = 0.0  # 精彩程度评分（由评分模型计算）
    exported: bool = False  # 是否已导出为独立文件
    error: str = ""  # 导出错误信息（如有）
    mark_in_wallclock: float = 0.0  # 入点墙钟时间戳（time.monotonic），用于精确对齐录制文件位置
    mark_out_wallclock: float = 0.0  # 出点墙钟时间戳（time.monotonic），用于精确对齐录制文件位置
    content_offset: float = 0.0  # 音频互相关偏移量（秒），导出时用于补偿不同房间的内容延迟
    # 符号约定: 正数表示当前房间的音频内容相对于参考音频"延后"，
    # 导出时会调整 clip 的时间戳以对齐多房间内容。
    # 典型场景: 多房间同步剪辑时，通过 cross-correlation 计算得到
    score_breakdown: dict = field(default_factory=dict)  # 各维度评分明细，如 {"speech": 0.9, "visual": 0.7, "scene": 0.5}
    highlight_reason: str = ""  # 高光原因描述（如 "语音情绪激动 + 画面剧烈变化"）
    transcript: str = ""  # 该片段对应的语音转录文本


@dataclass(slots=True)
class RoomTimeSnapshot:
    """单个房间在 TimelineContext 提交时的时间快照。

    记录房间在某一时刻的时间轴参数，用于双向时间转换。
    common = preview_local + preview_to_common_delta
    common = recording_local + recording_to_common_delta
    """
    room_id: str
    preview_epoch_id: str = ""
    recording_id: str = ""
    preview_to_common_delta: float = 0.0
    recording_to_common_delta: float = 0.0
    align_confidence: float = 0.0
    media_start_mono: float = 0.0


@dataclass(slots=True)
class TimelineContext:
    """公共时间模型 — 所有播放头、seek、mark、loop 和切片的时间基准。

    通过双向转换将不同房间的时间轴统一到同一 common 时间轴上：
    - common = preview_local + preview_to_common_delta
    - common = recording_local + recording_to_common_delta

    纯内存对象，不跨重启持久化。
    """
    timeline_id: str
    reference_room_id: str
    preview_ready: bool = False
    clip_ready: bool = False
    created_at: float = 0.0
    room_snapshots: dict[str, RoomTimeSnapshot] = field(default_factory=dict)

    def common_to_preview(self, room_id: str, common_time: float) -> float:
        """从公共时间转换到预览时间。"""
        snap = self.room_snapshots.get(room_id)
        if snap is None:
            raise KeyError(f"Room {room_id} not in timeline {self.timeline_id}")
        return common_time - snap.preview_to_common_delta

    def preview_to_common(self, room_id: str, preview_time: float) -> float:
        """从预览时间转换到公共时间。"""
        snap = self.room_snapshots.get(room_id)
        if snap is None:
            raise KeyError(f"Room {room_id} not in timeline {self.timeline_id}")
        return preview_time + snap.preview_to_common_delta

    def common_to_recording(self, room_id: str, common_time: float) -> float:
        """从公共时间转换到录制文件时间。"""
        snap = self.room_snapshots.get(room_id)
        if snap is None:
            raise KeyError(f"Room {room_id} not in timeline {self.timeline_id}")
        return common_time - snap.recording_to_common_delta

    def recording_to_common(self, room_id: str, recording_time: float) -> float:
        """从录制文件时间转换到公共时间。"""
        snap = self.room_snapshots.get(room_id)
        if snap is None:
            raise KeyError(f"Room {room_id} not in timeline {self.timeline_id}")
        return recording_time + snap.recording_to_common_delta


@dataclass(slots=True, frozen=True)
class ClipSnapshot:
    """不可变的切片快照 — 后端冻结的录制文件坐标。

    start/end 是录制文件中的物理位置，一经创建不可修改。
    通过 recording_id 绑定到特定录制文件版本。
    """
    clip_id: str
    clip_group_id: str
    timeline_id: str
    recording_id: str
    common_start: float
    common_end: float
    room_id: str
    source: str = ""
    source_highlight_id: str = ""
    output_path: str = ""
    thumbnail_path: str = ""
    exported: bool = False
    error: str = ""


@dataclass(slots=True)
class ExportOptions:
    """导出配置选项。

    对应用户在导出时选择的编码参数。
    使用 dataclass 而非直接传 dict，便于类型检查和重构。
    """

    codec: str = "libx264"  # 视频编码器（默认 H.264）
    crf: int = 23  # 恒定质量因子（18-28 为常用范围，越小质量越高）
    preset: str = "medium"  # 编码预设（影响编码速度与压缩率）
    audio_bitrate: str = "128k"  # 音频码率
    rate_mode: str = "crf"  # 码率控制模式: "crf" | "bitrate" | "unrestricted"
    video_bitrate: str = "8000k"  # 视频码率（bitrate 模式或 unrestricted 时使用）
    resolution: str = ""  # 输出分辨率（如 "1920x1080"），空字符串表示不缩放
    fps: float = 0.0  # 输出帧率（0 表示保持原帧率）
    vertical_crop: bool = False  # 是否裁切为 9:16 竖屏格式（适用于短视频平台）
    generate_thumbnail: bool = True  # 是否生成视频缩略图


@dataclass(slots=True)
class ExportResult:
    """单次导出的结果。"""

    success: bool  # 导出是否成功
    clip_id: str  # 对应片段 ID
    output_path: str = ""  # 输出文件路径（成功时有效）
    thumbnail_path: str = ""  # 缩略图文件路径（生成时有效）
    duration_sec: float = 0.0  # 导出片段的时长（秒）
    file_size_mb: float = 0.0  # 输出文件大小（MB）
    error: str = ""  # 错误信息（失败时有效）
