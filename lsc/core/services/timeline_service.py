"""TimelineContext 生命周期管理服务 — 纯内存，不跨重启持久化。

职责：
- 原子提交对齐结果（全组成功/失败）
- 预览重建/断线/录制重连时生成新 epoch/recording ID
- 使旧 TimelineContext 失效并广播 timeline_invalidated
- 提供双向时间转换 API
- 管理 ClipSnapshot 的创建和查询
- 从对齐结果构建 RoomTimeSnapshot
"""
from __future__ import annotations

import logging
import threading
import time as _time
from collections.abc import Callable
from typing import Any
from uuid import uuid4

from lsc.core.models import (
    ClipSnapshot,
    RoomTimeSnapshot,
    TimelineContext,
)

_log = logging.getLogger(__name__)

# 对齐置信度阈值：低于此值视为不可信
_ALIGN_CONFIDENCE_THRESHOLD = 0.3


def build_room_snapshots_from_align(
    reference_room_id: str,
    offsets: dict[str, float],
    scores: dict[str, float],
    room_meta: dict[str, dict[str, Any]],
    confidence_threshold: float = _ALIGN_CONFIDENCE_THRESHOLD,
) -> dict[str, RoomTimeSnapshot]:
    """从对齐 offsets/scores 构建 RoomTimeSnapshot 映射。

    Delta 约定（锁定）::

        preview_to_common_delta[room] = content_offset[room] - content_offset[reference]
        recording_to_common_delta = media_start_mono + preview_to_common_delta

    仅包含置信度 >= threshold 的房间。
    """
    ref_offset = float(offsets.get(reference_room_id, 0.0) or 0.0)
    snapshots: dict[str, RoomTimeSnapshot] = {}
    for room_id, offset in offsets.items():
        score = float(scores.get(room_id, 0.0) or 0.0)
        if score < confidence_threshold:
            continue
        meta = room_meta.get(room_id) or {}
        preview_delta = float(offset) - ref_offset
        media_start = float(meta.get("media_start_mono", 0.0) or 0.0)
        snapshots[room_id] = RoomTimeSnapshot(
            room_id=room_id,
            preview_epoch_id=str(meta.get("preview_epoch_id", "") or ""),
            recording_id=str(meta.get("recording_id", "") or ""),
            preview_to_common_delta=preview_delta,
            recording_to_common_delta=media_start + preview_delta,
            align_confidence=score,
            media_start_mono=media_start,
        )
    return snapshots


class TimelineService:
    """管理 TimelineContext 和 ClipSnapshot 的生命周期。

    纯内存服务，应用退出后所有数据丢失。
    线程安全：所有公共方法通过 self._lock 保护。
    """

    def __init__(self) -> None:
        self._timelines: dict[str, TimelineContext] = {}  # timeline_id -> ctx
        self._room_timeline: dict[str, str] = {}  # room_id -> timeline_id
        self._clip_snapshots: dict[str, ClipSnapshot] = {}  # clip_id -> snapshot
        self._lock = threading.RLock()
        self._invalidate_listeners: list[Callable[[str, str], None]] = []

    def add_invalidate_listener(self, callback: Callable[[str, str], None]) -> None:
        """注册 timeline 失效回调（timeline_id, reason）。"""
        with self._lock:
            self._invalidate_listeners.append(callback)

    def create_timeline(
        self,
        reference_room_id: str,
        room_snapshots: dict[str, RoomTimeSnapshot],
        required_room_ids: list[str] | None = None,
    ) -> TimelineContext | None:
        """原子创建 TimelineContext。

        所有房间置信度必须 >= 0.3，且所有 required_room_ids 必须存在。
        任一路缺失或低置信则返回 None，不部分写入。

        preview_ready 始终为 True；clip_ready 仅当全部房间都有非空 recording_id。
        """
        with self._lock:
            if reference_room_id not in room_snapshots:
                _log.warning(
                    "原子对齐失败: reference_room_id %s 不在 room_snapshots 中",
                    reference_room_id,
                )
                return None

            if required_room_ids:
                for rid in required_room_ids:
                    if rid not in room_snapshots:
                        _log.warning("原子对齐失败: 缺少房间 %s", rid)
                        return None

            for rid, snap in room_snapshots.items():
                if snap.align_confidence < _ALIGN_CONFIDENCE_THRESHOLD:
                    _log.warning(
                        "原子对齐失败: 房间 %s 置信度 %.3f < %.1f",
                        rid, snap.align_confidence, _ALIGN_CONFIDENCE_THRESHOLD,
                    )
                    return None

            clip_ready = all(bool(snap.recording_id) for snap in room_snapshots.values())
            timeline_id = uuid4().hex
            ctx = TimelineContext(
                timeline_id=timeline_id,
                reference_room_id=reference_room_id,
                preview_ready=True,
                clip_ready=clip_ready,
                created_at=_time.monotonic(),
                room_snapshots=dict(room_snapshots),
            )

            self._timelines[timeline_id] = ctx
            for rid in room_snapshots:
                self._room_timeline[rid] = timeline_id

            _log.info(
                "TimelineContext 创建成功: timeline_id=%s, rooms=%d, reference=%s, clip_ready=%s",
                timeline_id, len(room_snapshots), reference_room_id, clip_ready,
            )
            return ctx

    def get_timeline(self, timeline_id: str) -> TimelineContext | None:
        """通过 ID 获取 TimelineContext。"""
        with self._lock:
            return self._timelines.get(timeline_id)

    def get_active_timeline_for_room(self, room_id: str) -> TimelineContext | None:
        """获取指定房间当前绑定的 TimelineContext。"""
        with self._lock:
            tid = self._room_timeline.get(room_id)
            if tid is None:
                return None
            return self._timelines.get(tid)

    def invalidate_timeline(self, timeline_id: str, reason: str = "") -> None:
        """使 TimelineContext 失效并清理相关映射，通知 listeners。

        不删除 ClipSnapshot：上下文失效后，若 recording_id 未变，
        已创建的切片仍可通过 export_clip_by_id 导出（设计 §5.1）。
        """
        listeners: list[Callable[[str, str], None]] = []
        with self._lock:
            ctx = self._timelines.pop(timeline_id, None)
            if ctx is None:
                return
            for rid in ctx.room_snapshots:
                if self._room_timeline.get(rid) == timeline_id:
                    del self._room_timeline[rid]
            listeners = list(self._invalidate_listeners)
            _log.info(
                "TimelineContext 已失效: timeline_id=%s, reason=%s",
                timeline_id, reason,
            )
        for cb in listeners:
            try:
                cb(timeline_id, reason)
            except Exception as exc:
                _log.debug("timeline invalidate listener 失败: %s", exc)

    def create_clip_snapshot(
        self,
        timeline_id: str,
        room_id: str,
        common_start: float,
        common_end: float,
        source: str = "",
        source_highlight_id: str = "",
        clip_group_id: str | None = None,
    ) -> ClipSnapshot | None:
        """原子创建 ClipSnapshot。

        验证 timeline 存在、房间存在、时间范围有效。
        任一路越界或时钟不可用则返回 None。
        """
        with self._lock:
            ctx = self._timelines.get(timeline_id)
            if ctx is None:
                _log.warning("创建 ClipSnapshot 失败: timeline %s 不存在", timeline_id)
                return None

            snap = ctx.room_snapshots.get(room_id)
            if snap is None:
                _log.warning("创建 ClipSnapshot 失败: 房间 %s 不在 timeline 中", room_id)
                return None

            if common_start < 0 or common_end <= common_start:
                _log.warning(
                    "创建 ClipSnapshot 失败: 无效时间范围 [%.3f, %.3f]",
                    common_start, common_end,
                )
                return None

            clip_id = uuid4().hex
            group_id = clip_group_id or f"group_{timeline_id[:8]}_{_time.monotonic():.0f}"
            clip = ClipSnapshot(
                clip_id=clip_id,
                clip_group_id=group_id,
                timeline_id=timeline_id,
                recording_id=snap.recording_id,
                common_start=common_start,
                common_end=common_end,
                room_id=room_id,
                source=source,
                source_highlight_id=source_highlight_id,
            )
            self._clip_snapshots[clip_id] = clip
            _log.info(
                "ClipSnapshot 创建成功: clip_id=%s, room=%s, [%.3f, %.3f]",
                clip_id, room_id, common_start, common_end,
            )
            return clip

    def get_clip_snapshot(self, clip_id: str) -> ClipSnapshot | None:
        """通过 ID 获取 ClipSnapshot。"""
        with self._lock:
            return self._clip_snapshots.get(clip_id)

    def delete_clip_snapshot(self, clip_id: str) -> bool:
        """删除指定 ClipSnapshot。成功返回 True，不存在返回 False。"""
        with self._lock:
            if clip_id not in self._clip_snapshots:
                return False
            del self._clip_snapshots[clip_id]
            _log.info("ClipSnapshot 已删除: clip_id=%s", clip_id)
            return True

    def on_preview_epoch_change(self, room_id: str, new_epoch_id: str) -> None:
        """预览重建/断线时调用，使绑定该房间的 TimelineContext 失效。"""
        with self._lock:
            ctx = self.get_active_timeline_for_room(room_id)
            if ctx is None:
                return
            tid = ctx.timeline_id
        self.invalidate_timeline(tid, f"preview_epoch_change: {room_id}:{new_epoch_id}")

    def on_recording_id_change(self, room_id: str, new_recording_id: str) -> None:
        """录制启动/重连时调用，更新 recording_id。

        不使 TimelineContext 失效，只更新快照中的 recording_id。
        已创建的 ClipSnapshot.recording_id 保持冻结旧值。
        """
        with self._lock:
            ctx = self.get_active_timeline_for_room(room_id)
            if ctx is not None and room_id in ctx.room_snapshots:
                old_id = ctx.room_snapshots[room_id].recording_id
                ctx.room_snapshots[room_id].recording_id = new_recording_id
                ctx.clip_ready = all(
                    bool(snap.recording_id) for snap in ctx.room_snapshots.values()
                )
                _log.info(
                    "recording_id 更新: room=%s, old=%s, new=%s, clip_ready=%s",
                    room_id, old_id, new_recording_id, ctx.clip_ready,
                )


# 全局单例
_timeline_service: TimelineService | None = None


def get_timeline_service() -> TimelineService:
    """获取 TimelineService 全局单例。"""
    global _timeline_service
    if _timeline_service is None:
        _timeline_service = TimelineService()
    return _timeline_service
