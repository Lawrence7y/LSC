"""Qt 录制服务适配器 — 将 RecordingService 包装为带 Qt 信号的类。

GUI 层可以通过此类使用核心录制服务，
使用 Qt 信号而非函数回调。

线程模型：
- 核心服务的回调可能在后台线程触发
- 此适配器使用 Qt 的信号机制自动跨线程调度到 GUI 线程
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from lsc import get_logger
from lsc.core.models import (
    RecordingSession,
    RecordingStatus,
    RoomInfo,
)
from lsc.core.services.recording_service import (
    RecordingConfig,
    RecordingService,
)

_log = get_logger(__name__)


class QtRecordingService(QObject):
    """Qt 友好的录制服务适配器。

    提供与 RecordingService 相同的功能，
    但使用 Qt 信号进行状态通知。
    """

    # ── 信号 ────────────────────────────────────────────────

    session_status_changed = Signal(object)
    """信号: 录制会话状态变更 (RecordingSession)"""

    session_started = Signal(object)
    """信号: 录制开始 (RecordingSession)"""

    session_stopped = Signal(object)
    """信号: 录制停止 (RecordingSession)"""

    session_error = Signal(object, str)
    """信号: 录制出错 (session_id, error_message)"""

    health_warning = Signal(str, str)
    """信号: 健康警告 (session_id, warning_message)"""

    room_parsed = Signal(object)
    """信号: 房间解析完成 (RoomInfo)"""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._service = RecordingService()
        self._service.set_status_callback(self._on_status_changed)

    # ── 房间解析 ───────────────────────────────────────────

    def parse_room(self, url: str, *, force_refresh: bool = False) -> RoomInfo:
        """解析直播间 URL。

        同步返回 RoomInfo，同时通过 room_parsed 信号通知。
        """
        room = self._service.parse_room(url, force_refresh=force_refresh)
        self.room_parsed.emit(room)
        return room

    # ── 录制控制 ───────────────────────────────────────────

    def start_recording(
        self,
        room: RoomInfo,
        output_dir: str,
        config: RecordingConfig | None = None,
    ) -> RecordingSession:
        """开始录制。

        Returns:
            新创建的录制会话快照
        """
        session = self._service.start_recording(room, output_dir, config)
        if session.status == RecordingStatus.RECORDING:
            self.session_started.emit(session)
        return session

    def stop_recording(self, session_id: str) -> RecordingSession:
        """停止录制。"""
        session = self._service.stop_recording(session_id)
        if session.status == RecordingStatus.STOPPED:
            self.session_stopped.emit(session)
        return session

    def stop_all(self) -> list[RecordingSession]:
        """停止所有录制。"""
        return self._service.stop_all()

    def remove_session(self, session_id: str) -> bool:
        """移除会话。"""
        return self._service.remove_session(session_id)

    # ── 状态查询 ───────────────────────────────────────────

    def get_session(self, session_id: str) -> RecordingSession | None:
        """获取会话快照。"""
        return self._service.get_session(session_id)

    def list_sessions(self) -> list[RecordingSession]:
        """列出所有会话。"""
        return self._service.list_sessions()

    def is_recording(self, session_id: str) -> bool:
        """检查指定会话是否正在录制。"""
        session = self._service.get_session(session_id)
        return session is not None and session.status == RecordingStatus.RECORDING

    def has_active_recordings(self) -> bool:
        """是否有正在进行的录制。"""
        return any(
            s.status == RecordingStatus.RECORDING
            for s in self._service.list_sessions()
        )

    # ── 健康检查 ───────────────────────────────────────────

    def check_health(self, session_id: str) -> str:
        """检查会话健康状态。

        Returns:
            错误信息，空字符串表示健康
        """
        error = self._service.check_health(session_id)
        if error:
            self.health_warning.emit(session_id, error)
        return error

    def check_all_health(self) -> dict[str, str]:
        """检查所有会话的健康状态。"""
        results = self._service.check_all_health()
        for session_id, error in results.items():
            self.health_warning.emit(session_id, error)
        return results

    def update_duration(self, session_id: str) -> float:
        """更新并返录制时长。"""
        return self._service.update_duration(session_id)

    def update_all_durations(self) -> dict[str, float]:
        """更新所有录制会话的时长。

        Returns:
            {session_id: duration_sec} 字典
        """
        return self._service.update_all_durations()

    # ── 统计查询 ───────────────────────────────────────────

    def get_active_count(self) -> int:
        """获取正在录制的会话数量。"""
        return self._service.get_active_count()

    def get_recording_sessions(self) -> list[RecordingSession]:
        """获取所有正在录制的会话（快照）。"""
        return self._service.get_recording_sessions()

    def get_total_duration(self) -> float:
        """获取所有录制会话的总时长（秒）。"""
        return self._service.get_total_duration()

    def get_total_file_size_mb(self) -> float:
        """获取所有录制文件的总大小（MB）。"""
        return self._service.get_total_file_size_mb()

    # ── 批量操作 ───────────────────────────────────────────

    batch_parse_progress = Signal(int, int, str)
    """信号: 批量解析进度 (current, total, url)"""

    batch_record_progress = Signal(int, int, str, bool)
    """信号: 批量录制进度 (current, total, room_id_or_url, success)"""

    batch_record_finished = Signal(int, int)
    """信号: 批量录制完成 (started_count, total_count)"""

    def parse_rooms(
        self,
        urls: list[str],
        *,
        force_refresh: bool = False,
    ) -> list[RoomInfo]:
        """批量解析直播间 URL。

        通过 batch_parse_progress 信号报告进度。
        """
        def _progress(current: int, total: int, url: str) -> None:
            self.batch_parse_progress.emit(current, total, url)

        return self._service.parse_rooms(
            urls,
            force_refresh=force_refresh,
            progress_callback=_progress,
        )

    def start_many(
        self,
        rooms: list[RoomInfo],
        base_output_dir: str,
        config: RecordingConfig | None = None,
        *,
        per_room_subdir: bool = True,
    ) -> list[RecordingSession]:
        """批量开始录制。

        通过 batch_record_progress 信号报告进度，
        通过 batch_record_finished 信号通知完成。
        """
        def _progress(current: int, total: int, room_id: str, success: bool) -> None:
            self.batch_record_progress.emit(current, total, room_id, success)

        started = self._service.start_many(
            rooms,
            base_output_dir,
            config,
            per_room_subdir=per_room_subdir,
            progress_callback=_progress,
        )

        self.batch_record_finished.emit(len(started), len(rooms))
        return started

    def stop_many(self, session_ids: list[str]) -> list[RecordingSession]:
        """批量停止录制。"""
        return self._service.stop_many(session_ids)

    def remove_many(self, session_ids: list[str]) -> int:
        """批量移除会话。

        Returns:
            成功移除的数量
        """
        return self._service.remove_many(session_ids)

    # ── 预检 ───────────────────────────────────────────────

    @staticmethod
    def preflight_check(output_dir: str, concurrent_streams: int = 1) -> str:
        """录制前磁盘空间检查。"""
        return RecordingService.preflight_check(output_dir, concurrent_streams)

    # ── 内部回调 ───────────────────────────────────────────

    def _on_status_changed(self, session: RecordingSession) -> None:
        """核心服务的状态回调 — 转发为 Qt 信号。"""
        self.session_status_changed.emit(session)

        if session.status == RecordingStatus.ERROR and session.last_error:
            self.session_error.emit(session.session_id, session.last_error)

    # ── 底层访问（迁移过渡期使用） ─────────────────────

    @property
    def core_service(self) -> RecordingService:
        """直接访问底层核心服务（迁移过渡期使用，新代码尽量避免）。"""
        return self._service


__all__ = ["QtRecordingService", "RecordingConfig"]
