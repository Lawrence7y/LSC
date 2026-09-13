"""核心领域层 — 房间会话模型。

RoomSession 是多房间工作台中单个直播间连接的完整状态快照。
涵盖连接状态、预览控制、录制信息、时间线选区、重连参数及导出内容偏移量等字段。

设计原则：纯数据 + 轻量方法，不依赖 PySide6 / Qt，便于单元测试和复用。
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime
from threading import Event
from typing import Any

from lsc.platforms.base import StreamInfo
from lsc.platforms.registry import get_display_name


@dataclass(slots=True)
class RoomSession:
    """单个直播间在工作台中的会话状态。

    聚合了连接、预览、录制、时间线标记、重连配置及内容偏移等所有字段，
    由 RoomOrchestrator 负责生命周期管理和状态更新。
    """
    room_id: str
    room_url: str
    platform: str = ""
    platform_name: str = ""
    # Redacted credential state projected to the UI; plaintext cookies never
    # belong in a room session or persisted room snapshot.
    credential_status: str = "NOT_CONFIGURED"
    streamer_name: str = ""
    stream_title: str = ""
    stream_info: StreamInfo | None = None
    # Shared request context (proxy/transport hints) reused by resolver,
    # ffprobe and the eventual FFmpeg ingest process.
    network_context: dict[str, object] = field(default_factory=dict)
    selected_quality: str = ""
    preview_muted: bool = True
    preview_enabled: bool = False
    preview_paused: bool = False
    preview_error: str = ""
    # live_mse | degraded
    # （2026-09-11 方案 A：回看不再经后端流通道，改由前端直接播本地录制文件，
    # 因此不再有文件回看模式与回看时间基座字段。）
    preview_mode: str = "live_mse"
    include_in_cut: bool = True
    is_connecting: bool = False
    is_connected: bool = False
    is_recording: bool = False
    record_output_path: str = ""
    # 录制镜像（<录制路径去扩展名>.dvr.mp4）：录制中前端本地回看的数据源。
    # 与 record_output_path 同生命周期：录制启动时设置、停止时保留、随归档重命名
    # 同步改名，无镜像（未启用/失败）时为空串。
    dvr_output_path: str = ""
    # Segmented V2 recordings expose their durable asset separately from the
    # legacy single-file path; the latter remains untouched during migration.
    record_manifest_path: str = ""
    canonical_room_id: str = ""
    record_started_at: datetime | None = None
    last_error: str = ""
    controller: Any | None = None
    preview_widget: Any | None = None
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
    recording_media_start_mono: float | None = None
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
    is_reconnecting: bool = False
    reconnect_next_attempt_at: float = 0.0
    reconnect_attempts: int = 0
    reconnect_output_dir: str = ""
    reconnect_encoder: str = ""
    reconnect_crf: int = 23
    reconnect_param_mode: str = "CRF 质量"
    reconnect_bitrate: str = ""
    reconnect_bitrate_unit: str = "kbps"
    reconnect_resolution: str = ""
    reconnect_framerate: str = ""
    reconnect_audio_bitrate: str = ""
    # 每房间独立的预览画质（覆盖全局设置），空字符串表示使用全局设置
    preview_quality: str = ""
    # 直播分类
    category: str = ""
    # 音频互相关计算的内容偏移量（秒）
    # 正值 = 该房间内容比最慢房间快（导出时需减去此值）
    # 0 = 基准房间（最慢）
    # 房间重连/录制重启时重置为 0
    # 导出时用于将所有房间的音频轨道按此偏移量对齐到基准房间时间线
    _room_lock: threading.Lock = field(default_factory=threading.Lock)
    content_offset: float = 0.0
    # 对齐组 ID：一次 audio_align 的所有参与房间共享同一 id（空串=未对齐）
    # 多房间同步导出时校验：target_room_ids 的 align_group_id 必须一致且非空
    # 房间重连/录制重启时与 content_offset 一起重置
    align_group_id: str = ""
    # 一键对齐成功后的组合输出根目录（{主播A}+{主播B}）。空串=未归组。
    output_bundle_dir: str = ""
    # #7: set by disconnect_room, checked by _on_connect_finished
    disconnect_requested: bool = False
    # 重连取消事件：用户断开/删除房间时 set()，通知进行中的重连退出
    _cancel_reconnect: Event = field(default_factory=Event)
    # 主线程重连进行中标记（防 global tick 重入）
    _reconnect_in_progress: bool = False
    # 兼容旧字段名（已不再使用后台线程）
    _reconnect_thread: object | None = None
    # 首帧写入校正标记：recording_start_mono 首帧校正完成后置 True，避免重复校正
    # 录制启动时重置为 False（支持重连场景重新校正）
    _first_frame_corrected: bool = False
    # 共享进样录制文件停滞检测（中频 tick 每 5s 检查，6 次无增长 ≈ 30s）
    _shared_ingest_last_file_size: int = 0
    _shared_ingest_stall_checks: int = 0
    # 预览 epoch ID：每次预览启动/重建时生成新 UUID，用于检测预览流版本变化
    preview_epoch_id: str = ""

    # 直播 MSE 预览轴相对录制文件轴的运行时映射。
    # 约定：preview_local = recording_local + recording_to_preview_delta。
    # 预览晚于录制 15 秒时，该值为 -15；仅在当前预览/录制 epoch 内有效，不持久化。
    recording_to_preview_delta: float | None = None
    preview_clock_epoch_id: str = ""
    preview_clock_anchor_mono: float | None = None
    preview_clock_anchor_time: float | None = None
    preview_clock_updated_mono: float = 0.0
    # 录制 ID：每次录制启动/重连时生成新 UUID，用于绑定 ClipSnapshot 到特定录制文件
    recording_id: str = ""
    # 缓存的流地址：连接/刷新时保存，避免录制/预览启动时重复刷新
    stream_url_cached: str = ""
    # 缓存解析时间戳（unix time），用于判断缓存是否过期（5分钟有效期）
    stream_parsed_at: float = 0.0

    def apply_stream_info(self, info: StreamInfo) -> None:
        """用异步流探测结果回填房间会话字段，并更新连接状态。"""
        self.platform = info.platform
        self.platform_name = get_display_name(info.platform)
        self.stream_info = info
        self.selected_quality = info.selected_quality
        if info.stream_url:
            self.stream_url_cached = info.stream_url
            self.stream_parsed_at = __import__('time').time()
        self.is_connected = bool(info.is_live and info.stream_url)
        self.is_connecting = False
        self.streamer_name = getattr(info, "streamer", "") or ""
        self.stream_title = getattr(info, "title", "") or ""
        self.category = getattr(info, "category", "") or ""
        raw = getattr(info, "raw", {}) or {}
        if isinstance(raw, dict):
            self.canonical_room_id = str(
                raw.get("room_id") or raw.get("roomId") or raw.get("roomid") or ""
            )
        self.last_error = ""

    def set_error(self, message: str) -> None:
        """设置错误状态：断开连接并记录错误信息。"""
        self.is_connected = False
        self.is_connecting = False
        self.last_error = message

    def clear_preview_clock(self) -> None:
        """清除当前预览 epoch 的录制轴→预览轴映射。"""
        self.recording_to_preview_delta = None
        self.preview_clock_epoch_id = ""
        self.preview_clock_anchor_mono = None
        self.preview_clock_anchor_time = None
        self.preview_clock_updated_mono = 0.0

    def status_text(self) -> str:
        """生成当前房间状态的简短文本描述，供 UI 状态栏展示。"""
        parts: list[str] = []
        if self.is_reconnecting:
            parts.append(f"重连中({self.reconnect_attempts})")
        elif self.is_connecting:
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
        """返回面向用户的友好错误描述（经 humanize 处理）。"""
        from lsc.utils.error_messages import humanize_error
        return humanize_error(self.last_error)
