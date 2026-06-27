"""多房间录制适配器 — 展示如何在多房间场景中使用核心录制服务。

这是一个参考实现，展示了如何用 QtRecordingService
来管理多个直播间的录制，替代 MultiRoomManager 中直接操作
StreamCapture 的部分。

设计目标：
- 提供清晰的 API，简化多房间录制管理
- 与现有 MultiRoomManager 并存，可渐进式替换
- 完全基于核心服务，不直接操作 FFmpeg 进程
- 支持批量操作和全局心跳

注意：这是一个适配器/参考实现，不是生产就绪的完整替换。
完整的 MultiRoomManager 迁移需要在后续阶段进行。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

from PySide6.QtCore import QObject, QTimer, Signal

from lsc import get_logger
from lsc.core.models import (
    RecordingSession,
    RecordingStatus,
    RoomInfo,
)
from lsc.core.qt.recording import QtRecordingService, RecordingConfig

_log = get_logger(__name__)


@dataclass(slots=True)
class RoomRecordingState:
    """单个房间的录制状态快照。

    这是给 GUI 层使用的只读数据结构，
    避免 GUI 直接操作内部状态。
    """

    room_url: str
    session_id: str = ""
    status: RecordingStatus = RecordingStatus.IDLE
    output_path: str = ""
    output_dir: str = ""
    duration_sec: float = 0.0
    file_size_mb: float = 0.0
    last_error: str = ""
    room_info: RoomInfo | None = None


class MultiRoomRecordingAdapter(QObject):
    """多房间录制管理器 — 基于核心服务的高层封装。

    提供面向多房间场景的便捷 API，
    内部使用 QtRecordingService 管理所有录制会话。

    主要功能：
    - 多房间批量录制启动/停止
    - 全局心跳（更新时长、文件大小、健康检查）
    - 录制状态查询与统计
    - 房间 ↔ 会话映射管理

    使用示例::

        adapter = MultiRoomRecordingAdapter(self)
        adapter.recording_started.connect(self.on_recording_started)
        adapter.recording_stopped.connect(self.on_recording_stopped)

        # 批量开始录制
        rooms = [room1, room2, room3]
        adapter.start_recordings(rooms, output_dir)

        # 获取统计
        total_duration = adapter.get_total_duration()
        active_count = adapter.get_active_count()
    """

    # ── 信号 ────────────────────────────────────────────────

    recording_started = Signal(str, object)
    """信号: 单个房间录制开始 (room_url, RecordingSession)"""

    recording_stopped = Signal(str, object)
    """信号: 单个房间录制停止 (room_url, RecordingSession)"""

    recording_error = Signal(str, str)
    """信号: 录制出错 (room_url, error_message)"""

    health_warning = Signal(str, str)
    """信号: 健康警告 (room_url, warning_message)"""

    batch_progress = Signal(int, int, str, bool)
    """信号: 批量操作进度 (current, total, room_url, success)"""

    batch_finished = Signal(int, int)
    """信号: 批量操作完成 (started_count, total_count)"""

    global_tick = Signal()
    """信号: 全局心跳（每秒触发一次，GUI 可用于刷新显示）"""

    stats_changed = Signal(int, float, float)
    """信号: 统计数据变更 (active_count, total_duration_sec, total_size_mb)"""

    # ── 常量 ────────────────────────────────────────────────

    MAX_CONCURRENT_RECORDINGS = 12
    """最大并发录制数"""

    _HEALTH_CHECK_INTERVAL = 5
    """健康检查间隔（心跳次数）"""

    _STATS_INTERVAL = 1
    """统计更新间隔（心跳次数）"""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)

        self._recording_service = QtRecordingService(self)
        self._room_to_session: dict[str, str] = {}  # room_url -> session_id
        self._session_to_room: dict[str, str] = {}  # session_id -> room_url

        # 全局心跳定时器
        self._tick_timer: QTimer | None = None
        self._tick_counter: int = 0

        # 连接核心服务信号
        self._recording_service.session_started.connect(self._on_session_started)
        self._recording_service.session_stopped.connect(self._on_session_stopped)
        self._recording_service.session_error.connect(self._on_session_error)
        self._recording_service.health_warning.connect(self._on_health_warning)
        self._recording_service.batch_record_progress.connect(self._on_batch_progress)
        self._recording_service.batch_record_finished.connect(self._on_batch_finished)

    # ── 房间 ↔ 会话映射 ─────────────────────────────────

    def get_session_id_for_room(self, room_url: str) -> str | None:
        """获取指定房间的录制会话 ID。"""
        return self._room_to_session.get(room_url)

    def get_room_url_for_session(self, session_id: str) -> str | None:
        """获取指定会话对应的房间 URL。"""
        return self._session_to_room.get(session_id)

    def _register_session(self, room_url: str, session_id: str) -> None:
        """注册房间与会话的映射关系。"""
        self._room_to_session[room_url] = session_id
        self._session_to_room[session_id] = room_url

    def _unregister_session(self, session_id: str) -> None:
        """取消房间与会话的映射关系。"""
        room_url = self._session_to_room.pop(session_id, None)
        if room_url:
            self._room_to_session.pop(room_url, None)

    # ── 录制控制 ───────────────────────────────────────────

    def start_recording(
        self,
        room: RoomInfo,
        output_dir: str,
        config: RecordingConfig | None = None,
    ) -> RecordingSession | None:
        """开始单个房间的录制。

        Args:
            room: 房间信息
            output_dir: 输出目录
            config: 录制配置

        Returns:
            成功返回 RecordingSession，失败返回 None
        """
        if self.get_active_count() >= self.MAX_CONCURRENT_RECORDINGS:
            _log.warning("已达最大并发录制数 (%d)", self.MAX_CONCURRENT_RECORDINGS)
            return None

        # 预检
        preflight = self._recording_service.preflight_check(output_dir)
        if preflight:
            _log.warning("录制预检失败: %s", preflight)
            return None

        try:
            session = self._recording_service.start_recording(room, output_dir, config)
            if session.status == RecordingStatus.RECORDING:
                self._register_session(room.room_url, session.session_id)
                self._ensure_tick_timer()
            return session
        except Exception as exc:
            _log.error("启动录制失败 %s: %s", room.room_url, exc)
            return None

    def stop_recording(self, room_url: str) -> RecordingSession | None:
        """停止单个房间的录制。

        Args:
            room_url: 房间 URL

        Returns:
            停止后的会话快照，失败返回 None
        """
        session_id = self._room_to_session.get(room_url)
        if not session_id:
            return None

        try:
            session = self._recording_service.stop_recording(session_id)
            return session
        except Exception as exc:
            _log.error("停止录制失败 %s: %s", room_url, exc)
            return None

    def remove_recording(self, room_url: str) -> bool:
        """移除一个房间的录制记录。

        如果正在录制，会先停止再移除。

        Returns:
            True 表示成功移除
        """
        session_id = self._room_to_session.get(room_url)
        if not session_id:
            return False

        try:
            self._recording_service.remove_session(session_id)
            self._unregister_session(session_id)
            self._check_stop_timer()
            return True
        except Exception as exc:
            _log.error("移除录制失败 %s: %s", room_url, exc)
            return False

    # ── 批量操作 ───────────────────────────────────────────

    def start_recordings(
        self,
        rooms: list[RoomInfo],
        base_output_dir: str,
        config: RecordingConfig | None = None,
        *,
        per_room_subdir: bool = True,
    ) -> list[RecordingSession]:
        """批量开始录制。

        Args:
            rooms: 房间信息列表
            base_output_dir: 基础输出目录
            config: 录制配置
            per_room_subdir: 是否为每个房间创建独立子目录

        Returns:
            成功启动的会话列表
        """
        if not rooms:
            return []

        current_active = self.get_active_count()
        available_slots = self.MAX_CONCURRENT_RECORDINGS - current_active

        if available_slots <= 0:
            _log.warning("已达最大并发录制数 (%d)", self.MAX_CONCURRENT_RECORDINGS)
            return []

        # 限制实际启动的数量不超过可用槽位
        rooms_to_start = rooms[:available_slots]
        if len(rooms_to_start) < len(rooms):
            _log.warning(
                "并发录制数限制：请求 %d 个，实际启动 %d 个（已用 %d/%d）",
                len(rooms), len(rooms_to_start),
                current_active, self.MAX_CONCURRENT_RECORDINGS,
            )

        # 预检（按实际并发数计算）
        preflight = self._recording_service.preflight_check(
            base_output_dir, concurrent_streams=len(rooms_to_start)
        )
        if preflight:
            _log.warning("批量录制预检失败: %s", preflight)
            return []

        started = self._recording_service.start_many(
            rooms_to_start,
            base_output_dir,
            config,
            per_room_subdir=per_room_subdir,
        )

        # 注册房间映射
        for session in started:
            # 找到对应的房间 URL
            for room in rooms_to_start:
                if room.stream_url == session.stream_url or room.room_url == session.room_url:
                    self._register_session(room.room_url, session.session_id)
                    break

        if started:
            self._ensure_tick_timer()

        return started

    def stop_all_recordings(self) -> list[RecordingSession]:
        """停止所有正在进行的录制。"""
        sessions = self._recording_service.stop_all()
        return sessions

    def get_room_state(self, room_url: str) -> RoomRecordingState | None:
        """获取指定房间的录制状态快照。"""
        session_id = self._room_to_session.get(room_url)
        if not session_id:
            return None

        # 更新时长后再返回
        self._recording_service.update_duration(session_id)

        session = self._recording_service.get_session(session_id)
        if not session:
            return None

        return RoomRecordingState(
            room_url=room_url,
            session_id=session.session_id,
            status=session.status,
            output_path=session.output_path,
            output_dir=session.output_dir,
            duration_sec=session.duration_sec,
            file_size_mb=session.file_size_mb,
            last_error=session.last_error,
        )

    def get_all_room_states(self) -> list[RoomRecordingState]:
        """获取所有房间的录制状态快照。"""
        states: list[RoomRecordingState] = []
        for room_url, _session_id in self._room_to_session.items():
            state = self.get_room_state(room_url)
            if state:
                states.append(state)
        return states

    # ── 统计查询 ───────────────────────────────────────────

    def get_active_count(self) -> int:
        """获取正在录制的房间数量。"""
        return self._recording_service.get_active_count()

    def get_total_duration(self) -> float:
        """获取所有录制的总时长（秒）。"""
        return self._recording_service.get_total_duration()

    def get_total_file_size_mb(self) -> float:
        """获取所有录制文件的总大小（MB）。"""
        return self._recording_service.get_total_file_size_mb()

    def is_any_recording(self) -> bool:
        """是否有任何正在进行的录制。"""
        return self._recording_service.has_active_recordings()

    def is_room_recording(self, room_url: str) -> bool:
        """指定房间是否正在录制。"""
        session_id = self._room_to_session.get(room_url)
        if not session_id:
            return False
        return self._recording_service.is_recording(session_id)

    # ── 健康检查 ───────────────────────────────────────────

    def check_room_health(self, room_url: str) -> str:
        """检查指定房间的录制健康状态。

        Returns:
            错误信息，空字符串表示健康
        """
        session_id = self._room_to_session.get(room_url)
        if not session_id:
            return ""
        return self._recording_service.check_health(session_id)

    def check_all_health(self) -> dict[str, str]:
        """检查所有房间的健康状态。

        Returns:
            {room_url: error_message} 字典
        """
        session_health = self._recording_service.check_all_health()
        room_health: dict[str, str] = {}
        for session_id, error in session_health.items():
            room_url = self._session_to_room.get(session_id)
            if room_url:
                room_health[room_url] = error
        return room_health

    # ── 全局心跳 ───────────────────────────────────────────

    def _ensure_tick_timer(self) -> None:
        """确保心跳定时器正在运行。"""
        if self._tick_timer is not None:
            return

        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(1000)  # 每秒
        self._tick_timer.timeout.connect(self._on_tick)
        self._tick_timer.start()
        _log.debug("Global tick timer started")

    def _check_stop_timer(self) -> None:
        """如果没有活动录制，停止心跳定时器。"""
        if not self.is_any_recording() and self._tick_timer is not None:
            self._tick_timer.stop()
            self._tick_timer = None
            _log.debug("Global tick timer stopped (no active recordings)")

    def _on_tick(self) -> None:
        """全局心跳回调。"""
        self._tick_counter += 1

        # 更新所有录制时长
        self._recording_service.update_all_durations()

        # 定期健康检查
        if self._tick_counter % self._HEALTH_CHECK_INTERVAL == 0:
            self.check_all_health()

        # 定期发出统计变更信号
        if self._tick_counter % self._STATS_INTERVAL == 0:
            self.stats_changed.emit(
                self.get_active_count(),
                self.get_total_duration(),
                self.get_total_file_size_mb(),
            )

        # 通知 GUI 刷新
        self.global_tick.emit()

    # ── 信号转发 ───────────────────────────────────────────

    def _on_session_started(self, session: RecordingSession) -> None:
        room_url = self._session_to_room.get(session.session_id, session.room_url)
        self.recording_started.emit(room_url, session)
        self._emit_stats_changed()

    def _on_session_stopped(self, session: RecordingSession) -> None:
        room_url = self._session_to_room.get(session.session_id, session.room_url)
        self.recording_stopped.emit(room_url, session)
        self._check_stop_timer()
        self._emit_stats_changed()

    def _on_session_error(self, session_id: str, error: str) -> None:
        room_url = self._session_to_room.get(session_id, "")
        self.recording_error.emit(room_url, error)

    def _on_health_warning(self, session_id: str, warning: str) -> None:
        room_url = self._session_to_room.get(session_id, "")
        self.health_warning.emit(room_url, warning)

    def _on_batch_progress(self, current: int, total: int, room_id: str, success: bool) -> None:
        self.batch_progress.emit(current, total, room_id, success)

    def _on_batch_finished(self, started: int, total: int) -> None:
        self.batch_finished.emit(started, total)

    def _emit_stats_changed(self) -> None:
        self.stats_changed.emit(
            self.get_active_count(),
            self.get_total_duration(),
            self.get_total_file_size_mb(),
        )

    # ── 清理 ───────────────────────────────────────────────

    def cleanup(self) -> None:
        """清理所有资源。

        停止所有录制，停止心跳定时器。
        """
        try:
            self.stop_all_recordings()
        except Exception:
            pass

        if self._tick_timer is not None:
            self._tick_timer.stop()
            self._tick_timer = None

        self._room_to_session.clear()
        self._session_to_room.clear()

    @staticmethod
    def make_room_output_dir(base_dir: str, room: RoomInfo) -> str:
        """生成房间专属的输出目录路径。

        静态方法，方便在需要时直接调用。
        """
        platform = re.sub(r"[^\w\-]", "_", (room.platform or "unknown")).strip("_")[:20]
        streamer = re.sub(r"[^\w\-]", "_", (room.streamer or "room")).strip("_")[:30]
        short_id = hex(hash(room.room_url) & 0xFFFFFF)[2:].zfill(6)
        name = f"{platform}_{streamer}_{short_id}"
        name = re.sub(r"_+", "_", name).strip("_")
        if not name:
            name = f"room_{short_id}"

        full_path = os.path.join(base_dir, name)
        suffix = 1
        while os.path.exists(full_path):
            full_path = os.path.join(base_dir, f"{name}_{suffix}")
            suffix += 1

        os.makedirs(full_path, exist_ok=True)
        return full_path


__all__ = [
    "MultiRoomRecordingAdapter",
    "RoomRecordingState",
]
