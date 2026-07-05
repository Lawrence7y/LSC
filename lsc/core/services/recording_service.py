"""录制服务 — 录制生命周期门面，封装录制业务逻辑，与 UI 无关。

RecordingService 是录制功能的统一入口（Facade），
单房间录制页和多房间工作台都通过它来操作录制。
完整管理录制生命周期：解析直播流 → 启动 FFmpeg 录制 → 跟踪状态 → 停止并校验文件。

设计原则：
- 不依赖 Qt / PySide6，便于单元测试
- 使用回调函数而非 Qt 信号进行状态通知
- 线程安全：内部状态变更有锁保护
- 支持多会话并发录制
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from threading import Lock
from uuid import uuid4

from lsc import get_logger
from lsc.config import ExportProfile, LscConfig, load_config
from lsc.core.models import (
    RecordingSession,
    RecordingStatus,
    RoomInfo,
    StreamQuality,
)
from lsc.platforms.base import StreamInfo
from lsc.platforms.registry import parse_stream
from lsc.recorder.capture import StreamCapture, validate_recording

_log = get_logger(__name__)


StatusCallback = Callable[[RecordingSession], None]
ProgressCallback = Callable[[RecordingSession, float, float], None]


@dataclass(slots=True)
class RecordingConfig:
    """录制启动配置。

    将所有录制参数集中在一个 dataclass 中，
    避免函数参数过长，也便于添加新参数。
    """

    encoder: str = "copy"  # "copy" | "libx264" | "h264_nvenc"
    crf: int = 23
    rate_mode: str = "crf"  # "crf" | "bitrate" | "unrestricted"
    bitrate: str = "8000k"
    preset: str = "medium"
    audio_bitrate: str = "128k"
    resolution: str = ""
    fps: float = 0.0
    vertical_crop: bool = False
    auto_reconnect: bool = True
    max_reconnect_attempts: int = 3


class RecordingService:
    """录制服务 — 管理所有录制会话。

    这是一个门面（Facade）类，统一封装了：
    - 直播流解析
    - FFmpeg 录制启动/停止
    - 录制状态跟踪
    - 自动重连
    - 录制文件校验

    线程安全：所有公共方法都可以安全地从任意线程调用。
    """

    def __init__(self, config: LscConfig | None = None) -> None:
        self._config = config or load_config()
        self._sessions: dict[str, _SessionHandle] = {}
        self._lock = Lock()
        self._on_status_changed: StatusCallback | None = None

    def set_status_callback(self, callback: StatusCallback | None) -> None:
        """设置状态变更回调。

        回调会在会话状态变更时被调用，
        调用线程可能是后台线程，UI 层需要自行调度到主线程。
        """
        self._on_status_changed = callback

    # ── 流解析 ─────────────────────────────────────────────

    def parse_room(self, url: str, *, force_refresh: bool = False) -> RoomInfo:
        """解析直播间 URL，返回房间信息。

        这是录制服务的第一步：先解析出直播流地址，
        再调用 start_recording() 开始录制。
        """
        info = parse_stream(url, force_refresh=force_refresh)
        return self._stream_info_to_room_info(info)

    @staticmethod
    def _stream_info_to_room_info(info: StreamInfo) -> RoomInfo:
        """将平台层的 StreamInfo 转换为核心层的 RoomInfo。"""
        qualities = [
            StreamQuality(name=name, url=url)
            for name, url in info.quality_urls.items()
        ]
        return RoomInfo(
            platform=info.platform,
            room_url=info.room_url,
            stream_url=info.stream_url,
            title=info.title,
            streamer=info.streamer,
            is_live=info.is_live,
            qualities=qualities,
            selected_quality=info.selected_quality,
            headers=dict(info.headers),
            error=info.error,
            error_code=info.error_code,
            raw=dict(info.raw),
        )

    # ── 录制会话管理 ───────────────────────────────────────

    def list_sessions(self) -> list[RecordingSession]:
        """列出所有录制会话（快照）。"""
        with self._lock:
            return [self._copy_session(h.session) for h in self._sessions.values()]

    def get_session(self, session_id: str) -> RecordingSession | None:
        """获取指定会话的快照。"""
        with self._lock:
            handle = self._sessions.get(session_id)
            if handle is None:
                return None
            return self._copy_session(handle.session)

    @staticmethod
    def _copy_session(s: RecordingSession) -> RecordingSession:
        """创建会话的浅拷贝，防止外部修改内部状态。"""
        return RecordingSession(
            session_id=s.session_id,
            room_url=s.room_url,
            output_dir=s.output_dir,
            output_path=s.output_path,
            status=s.status,
            stream_url=s.stream_url,
            stream_title=s.stream_title,
            streamer_name=s.streamer_name,
            platform=s.platform,
            start_time=s.start_time,
            end_time=s.end_time,
            duration_sec=s.duration_sec,
            file_size_mb=s.file_size_mb,
            encoder=s.encoder,
            crf=s.crf,
            bitrate=s.bitrate,
            last_error=s.last_error,
            reconnect_attempts=s.reconnect_attempts,
            max_reconnect_attempts=s.max_reconnect_attempts,
        )

    # ── 录制生命周期 ───────────────────────────────────────

    def start_recording(
        self,
        room: RoomInfo,
        output_dir: str,
        config: RecordingConfig | None = None,
    ) -> RecordingSession:
        """开始录制。

        Args:
            room: 房间信息（从 parse_room 获取）
            output_dir: 输出目录
            config: 录制配置，为 None 时使用默认值

        Returns:
            新创建的录制会话

        Raises:
            ValueError: 房间未开播或流地址无效
            RuntimeError: 录制启动失败
        """
        if not room.is_live or not room.stream_url:
            raise ValueError(f"房间未开播或流地址无效: {room.error or '未知错误'}")

        config = config or RecordingConfig()
        session_id = uuid4().hex

        os.makedirs(output_dir, exist_ok=True)
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        # 追加 uuid 短后缀避免同秒并发录制覆盖
        unique_suffix = uuid4().hex[:6]
        output_path = os.path.join(output_dir, f"recording_{timestamp}_{unique_suffix}.mp4")

        session = RecordingSession(
            session_id=session_id,
            room_url=room.room_url,
            output_dir=output_dir,
            output_path=output_path,
            status=RecordingStatus.CONNECTING,
            stream_url=room.stream_url,
            stream_title=room.title,
            streamer_name=room.streamer,
            platform=room.platform,
            encoder=config.encoder,
            crf=config.crf,
            bitrate=config.bitrate,
            max_reconnect_attempts=config.max_reconnect_attempts,
        )

        capture = StreamCapture(self._config)

        # 构建 FFmpeg 参数
        input_args = self._headers_to_input_args(room.headers)
        extra_args = self._build_extra_args(config)
        codec = "custom" if extra_args else config.encoder

        handle = _SessionHandle(
            session=session,
            capture=capture,
            config=config,
            room_headers=dict(room.headers),
        )

        with self._lock:
            self._sessions[session_id] = handle

        # 启动录制
        try:
            ok = capture.start(
                room.stream_url,
                output_path,
                codec=codec,
                input_args=input_args,
                extra_args=extra_args if extra_args else None,
            )
            if not ok:
                session.status = RecordingStatus.ERROR
                session.last_error = capture.last_error or "录制启动失败"
                # 启动失败：清理会话和 capture 资源，避免残留
                with self._lock:
                    self._sessions.pop(session_id, None)
                try:
                    capture.force_cleanup()
                except Exception:
                    pass
                self._notify_status(session)
                return self._copy_session(session)

            session.status = RecordingStatus.RECORDING
            from datetime import datetime

            session.start_time = datetime.now()
            self._notify_status(session)
            return self._copy_session(session)

        except Exception as exc:
            session.status = RecordingStatus.ERROR
            session.last_error = str(exc)
            # 异常路径：同样清理会话和 capture 资源
            with self._lock:
                self._sessions.pop(session_id, None)
            try:
                capture.force_cleanup()
            except Exception:
                pass
            self._notify_status(session)
            raise

    def stop_recording(self, session_id: str) -> RecordingSession:
        """停止录制。

        Args:
            session_id: 会话 ID

        Returns:
            停止后的会话快照

        Raises:
            KeyError: 会话不存在
        """
        with self._lock:
            handle = self._sessions.get(session_id)
            if handle is None:
                raise KeyError(f"Session not found: {session_id}")

        session = handle.session
        capture = handle.capture

        if session.status in (RecordingStatus.IDLE, RecordingStatus.STOPPED, RecordingStatus.ERROR):
            return self._copy_session(session)

        try:
            result = capture.stop()
            from datetime import datetime

            session.end_time = datetime.now()
            session.duration_sec = result.duration_sec
            session.file_size_mb = result.file_size_mb

            # 根据 capture.stop() 的返回值设置状态：
            # 成功 -> STOPPED，失败（如孤儿进程）-> ERROR
            if result.success:
                session.status = RecordingStatus.STOPPED
                if result.output_path:
                    is_valid, validation_error = validate_recording(result.output_path)
                    if not is_valid:
                        _log.warning("Recording validation failed: %s", validation_error)
                        session.last_error = validation_error
            else:
                session.status = RecordingStatus.ERROR
                session.last_error = result.error or "停止录制失败"

            self._notify_status(session)
        except Exception as exc:
            session.status = RecordingStatus.ERROR
            session.last_error = f"停止录制失败: {exc}"
            self._notify_status(session)

        return self._copy_session(session)

    def stop_all(self) -> list[RecordingSession]:
        """停止所有正在进行的录制。"""
        results: list[RecordingSession] = []
        with self._lock:
            session_ids = list(self._sessions.keys())

        for session_id in session_ids:
            try:
                result = self.stop_recording(session_id)
                results.append(result)
            except Exception as exc:
                _log.warning("Failed to stop session %s: %s", session_id, exc)

        return results

    def remove_session(self, session_id: str) -> bool:
        """移除一个已停止的会话。

        如果会话正在录制，会先停止再移除。

        Returns:
            True 表示移除成功，False 表示会话不存在
        """
        with self._lock:
            handle = self._sessions.get(session_id)
            if handle is None:
                return False

        # 确保录制已停止
        if handle.session.status == RecordingStatus.RECORDING:
            try:
                self.stop_recording(session_id)
            except Exception:
                pass

        # 清理资源
        try:
            handle.capture.force_cleanup()
        except Exception:
            pass

        with self._lock:
            self._sessions.pop(session_id, None)

        return True

    # ── 健康检查 ───────────────────────────────────────────

    def check_health(self, session_id: str) -> str:
        """检查指定录制会话的健康状态。

        Returns:
            错误信息字符串，空字符串表示健康
        """
        with self._lock:
            handle = self._sessions.get(session_id)
            if handle is None:
                return "会话不存在"

        if handle.session.status != RecordingStatus.RECORDING:
            return ""

        return handle.capture.check_health()

    def check_all_health(self) -> dict[str, str]:
        """检查所有录制会话的健康状态。

        Returns:
            {session_id: error_message} 字典，健康的会话不包含在结果中
        """
        results: dict[str, str] = {}
        with self._lock:
            session_ids = list(self._sessions.keys())

        for session_id in session_ids:
            error = self.check_health(session_id)
            if error:
                results[session_id] = error

        return results

    def update_duration(self, session_id: str) -> float:
        """更新并返回指定会话的录制时长（秒）。"""
        with self._lock:
            handle = self._sessions.get(session_id)
            if handle is None:
                return 0.0

        if handle.session.status == RecordingStatus.RECORDING:
            handle.session.duration_sec = handle.capture.duration

        return handle.session.duration_sec

    def update_all_durations(self) -> dict[str, float]:
        """更新所有录制会话的时长。

        Returns:
            {session_id: duration_sec} 字典
        """
        results: dict[str, float] = {}
        with self._lock:
            session_ids = list(self._sessions.keys())

        for session_id in session_ids:
            duration = self.update_duration(session_id)
            results[session_id] = duration

        return results

    # ── 统计与查询 ─────────────────────────────────────────

    def get_active_count(self) -> int:
        """获取正在录制的会话数量。"""
        with self._lock:
            return sum(
                1
                for h in self._sessions.values()
                if h.session.status == RecordingStatus.RECORDING
            )

    def get_recording_sessions(self) -> list[RecordingSession]:
        """获取所有正在录制的会话（快照）。"""
        with self._lock:
            return [
                self._copy_session(h.session)
                for h in self._sessions.values()
                if h.session.status == RecordingStatus.RECORDING
            ]

    def get_total_duration(self) -> float:
        """获取所有录制会话的总时长（秒）。"""
        total = 0.0
        with self._lock:
            for handle in self._sessions.values():
                if handle.session.status == RecordingStatus.RECORDING:
                    total += handle.capture.duration
                else:
                    total += handle.session.duration_sec
        return total

    def get_total_file_size_mb(self) -> float:
        """获取所有录制文件的总大小（MB）。

        注意：这是基于当前文件大小的估算，
        对于正在录制的会话，可能不是最终大小。
        """
        total = 0.0
        with self._lock:
            for handle in self._sessions.values():
                if handle.session.status == RecordingStatus.RECORDING:
                    path = handle.session.output_path
                    if path and os.path.isfile(path):
                        try:
                            total += os.path.getsize(path) / (1024 * 1024)
                        except OSError:
                            total += handle.session.file_size_mb
                else:
                    total += handle.session.file_size_mb
        return total

    # ── 批量操作 ───────────────────────────────────────────

    def parse_rooms(
        self,
        urls: list[str],
        *,
        force_refresh: bool = False,
        progress_callback: Callable[[int, int, str], None] | None = None,
    ) -> list[RoomInfo]:
        """批量解析直播间 URL。

        Args:
            urls: 直播间 URL 列表
            force_refresh: 是否强制刷新缓存
            progress_callback: 进度回调 (current, total, url)

        Returns:
            RoomInfo 列表，顺序与输入一致
        """
        results: list[RoomInfo] = []
        total = len(urls)

        for i, url in enumerate(urls, 1):
            try:
                room = self.parse_room(url, force_refresh=force_refresh)
            except Exception as exc:
                room = RoomInfo(
                    platform="unknown",
                    room_url=url,
                    error=str(exc),
                    error_code="parse_exception",
                )
            results.append(room)

            if progress_callback is not None:
                try:
                    progress_callback(i, total, url)
                except Exception:
                    pass

        return results

    def start_many(
        self,
        rooms: list[RoomInfo],
        base_output_dir: str,
        config: RecordingConfig | None = None,
        *,
        per_room_subdir: bool = True,
        progress_callback: Callable[[int, int, str, bool], None] | None = None,
    ) -> list[RecordingSession]:
        """批量开始录制。

        Args:
            rooms: 房间信息列表
            base_output_dir: 基础输出目录
            config: 录制配置
            per_room_subdir: 是否为每个房间创建独立子目录
            progress_callback: 进度回调 (current, total, room_id, success)

        Returns:
            成功启动的录制会话列表
        """
        config = config or RecordingConfig()
        started: list[RecordingSession] = []
        total = len(rooms)

        for i, room in enumerate(rooms, 1):
            success = False
            session_id = ""

            try:
                if per_room_subdir:
                    output_dir = self._make_room_output_dir(
                        base_output_dir, room
                    )
                else:
                    output_dir = base_output_dir

                session = self.start_recording(room, output_dir, config)
                session_id = session.session_id
                if session.status == RecordingStatus.RECORDING:
                    started.append(session)
                    success = True
            except Exception as exc:
                _log.warning("Failed to start recording for %s: %s", room.room_url, exc)

            if progress_callback is not None:
                try:
                    progress_callback(i, total, session_id or room.room_url, success)
                except Exception:
                    pass

        return started

    def stop_many(self, session_ids: list[str]) -> list[RecordingSession]:
        """批量停止录制。

        Args:
            session_ids: 会话 ID 列表

        Returns:
            停止后的会话快照列表（按输入顺序）
        """
        results: list[RecordingSession] = []
        for session_id in session_ids:
            try:
                result = self.stop_recording(session_id)
                results.append(result)
            except Exception as exc:
                _log.warning("Failed to stop session %s: %s", session_id, exc)
        return results

    def remove_many(self, session_ids: list[str]) -> int:
        """批量移除会话。

        Returns:
            成功移除的数量
        """
        count = 0
        for session_id in session_ids:
            if self.remove_session(session_id):
                count += 1
        return count

    # ── 工具方法 ───────────────────────────────────────────

    @staticmethod
    def _make_room_output_dir(base_dir: str, room: RoomInfo) -> str:
        """生成可读的房间录制子目录名。

        格式: {platform}_{streamer}_{room_id_short}
        避免纯 uuid 难以辨认，同时保证唯一性。
        """
        import re

        platform = re.sub(r"[^\w\-]", "_", (room.platform or "unknown")).strip("_")[:20]
        streamer = re.sub(r"[^\w\-]", "_", (room.streamer or "room")).strip("_")[:30]
        # 用 room_url 的 hash 作为短 ID，避免依赖 uuid
        short_id = hex(hash(room.room_url) & 0xFFFFFF)[2:].zfill(6)
        name = f"{platform}_{streamer}_{short_id}"
        name = re.sub(r"_+", "_", name).strip("_")
        if not name:
            name = f"room_{short_id}"

        full_path = os.path.join(base_dir, name)
        # 避免目录已存在时覆盖
        suffix = 1
        while os.path.exists(full_path):
            full_path = os.path.join(base_dir, f"{name}_{suffix}")
            suffix += 1

        os.makedirs(full_path, exist_ok=True)
        return full_path

    # ── 内部方法 ───────────────────────────────────────────

    def _notify_status(self, session: RecordingSession) -> None:
        """触发状态变更回调。"""
        if self._on_status_changed is not None:
            try:
                self._on_status_changed(self._copy_session(session))
            except Exception as exc:
                _log.warning("Status callback raised: %s", exc)

    @staticmethod
    def _headers_to_input_args(headers: dict[str, str]) -> list[str]:
        """将 HTTP 头转换为 FFmpeg 输入参数。"""
        from lsc.platforms.base import headers_to_ffmpeg_input_args

        return headers_to_ffmpeg_input_args(headers)

    @staticmethod
    def _build_extra_args(config: RecordingConfig) -> list[str]:
        """根据录制配置构建 FFmpeg 额外参数。

        如果使用 copy 模式，返回空列表（使用默认的 copy 编码）。
        """
        if config.encoder == "copy":
            return []

        export_profile = ExportProfile(
            crf=config.crf,
            codec=config.encoder,
            preset=config.preset,
            audio_bitrate=config.audio_bitrate,
            rate_mode=config.rate_mode,
            video_bitrate=config.bitrate,
            resolution=config.resolution,
            fps=config.fps,
            vertical_crop=config.vertical_crop,
        )

        video_args = export_profile.ffmpeg_video_args()
        audio_args = export_profile.ffmpeg_audio_args()
        filter_args = export_profile.ffmpeg_filter_args()

        return video_args + audio_args + filter_args

    # ── 磁盘空间预检 ──────────────────────────────────────

    _MIN_FREE_BYTES_PER_STREAM = 8 * 1024 * 1024 * 1024  # 8 GB

    @classmethod
    def preflight_check(cls, output_dir: str, concurrent_streams: int = 1) -> str:
        """录制前磁盘空间预检。

        在开始录制前检查输出目录所在磁盘的可用空间是否满足要求。
        每路并发录制至少需要 8 GB 可用空间。

        Args:
            output_dir: 输出目录路径
            concurrent_streams: 并发录制路数，默认 1

        Returns:
            错误信息字符串；空字符串表示检查通过，非空字符串表示空间不足
        """
        import shutil

        os.makedirs(output_dir, exist_ok=True)
        _total, _used, free = shutil.disk_usage(output_dir)
        required = cls._MIN_FREE_BYTES_PER_STREAM * max(1, concurrent_streams)
        if free < required:
            free_gb = free / (1024**3)
            required_gb = required / (1024**3)
            return (
                f"磁盘空间不足，当前仅剩 {free_gb:.1f} GB，"
                f"需要 {required_gb:.1f} GB（{concurrent_streams} 路并发录制）"
            )
        return ""


class _SessionHandle:
    """内部会话句柄，包含会话数据和底层 capture 对象。

    不对外暴露，仅供 RecordingService 内部使用。
    """

    __slots__ = ("session", "capture", "config", "room_headers")

    def __init__(
        self,
        *,
        session: RecordingSession,
        capture: StreamCapture,
        config: RecordingConfig,
        room_headers: dict[str, str],
    ) -> None:
        self.session = session
        self.capture = capture
        self.config = config
        self.room_headers = room_headers
