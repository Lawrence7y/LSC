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
    align_mono: float | None = None,
) -> dict[str, RoomTimeSnapshot]:
    """从对齐 offsets/scores 构建 RoomTimeSnapshot 映射。

    Delta 约定（锁定）::

        preview_to_common_delta[room] = (align_mono - origin_mono) - preview_current_time[room]
                                          + content_offset[room] - content_offset[reference]
        recording_to_common_delta = media_start_mono + content_offset[room] - content_offset[reference]
                                      - origin_mono

    其中 ``origin_mono`` = 各可信房间 ``media_start_mono`` 的最小值（最早录制起点，
    公共轴零点）。``align_mono`` = 对齐请求接收时刻（单调时钟）。

    设计要点：
    - ``preview_current_time`` 是 MSE 预览流 currentTime，基座为流相关任意值
      （常见 0 或流起始 PTS），与录制轴基座 ``media_start_mono`` 相差数百~数千秒；
      不锚定时公共轴上「播放头（preview→common）」与「切片（recording→common）」
      错位 ~media_start_mono 秒，时间线显示播放头与切片相距极远、点击切片 seek
      到预览缓冲范围之外。
    - 公共轴零点取最早录制起点而非对齐时刻：若录制早于对齐开始，对齐时刻之前的
      内容仍落在正轴；且时间线最大值 = 会话时长，而不是 time.monotonic() 的
      系统开机基座（Windows 下可达数小时，导致时间线默认最大值为“两个多小时”）。

    仅当 ``align_mono`` 与 room_meta 内 ``preview_current_time`` 均可用时锚定；
    否则退化为旧行为（仅相对偏移，供旧前端/旧数据兼容）。

    仅包含置信度 >= threshold 的房间。
    """
    ref_offset = float(offsets.get(reference_room_id, 0.0) or 0.0)

    # 先收集锚点数据：所有可信房间的预览 PTS、捕获结束单调时刻与媒体起点
    # 旧前端/旧数据不传 preview_capture_mono 时，回退到 align_mono（整体接收时刻）。
    anchored_rooms: dict[str, tuple[float, float, float]] = {}  # room_id -> (pct, capture_mono, media_start)
    for room_id, _offset in offsets.items():
        score = float(scores.get(room_id, 0.0) or 0.0)
        if score < confidence_threshold:
            continue
        meta = room_meta.get(room_id) or {}
        pct = meta.get("preview_current_time")
        if align_mono is None or not isinstance(pct, (int, float)) or float(pct) <= 0:
            continue
        capture_mono = float(meta.get("preview_capture_mono") or align_mono)
        if capture_mono <= 0:
            capture_mono = float(align_mono)
        media_start = float(meta.get("media_start_mono", 0.0) or 0.0)
        anchored_rooms[room_id] = (float(pct), capture_mono, media_start)
    origin_mono = 0.0
    if anchored_rooms:
        valid_starts = [ms for _, _, ms in anchored_rooms.values() if ms > 0.0]
        if valid_starts:
            origin_mono = min(valid_starts)

    snapshots: dict[str, RoomTimeSnapshot] = {}
    for room_id, offset in offsets.items():
        score = float(scores.get(room_id, 0.0) or 0.0)
        if score < confidence_threshold:
            continue
        meta = room_meta.get(room_id) or {}
        rel_delta = float(offset) - ref_offset
        media_start = float(meta.get("media_start_mono", 0.0) or 0.0)
        preview_delta = rel_delta
        rec_delta = media_start + rel_delta
        anchored = room_id in anchored_rooms
        if anchored:
            pct, capture_mono, _ms = anchored_rooms[room_id]
            preview_delta = (capture_mono - origin_mono - pct) + rel_delta
            rec_delta = media_start + rel_delta - origin_mono
        snapshots[room_id] = RoomTimeSnapshot(
            room_id=room_id,
            preview_epoch_id=str(meta.get("preview_epoch_id", "") or ""),
            recording_id=str(meta.get("recording_id", "") or ""),
            preview_to_common_delta=preview_delta,
            recording_to_common_delta=rec_delta,
            align_confidence=score,
            media_start_mono=media_start,
            preview_rel_delta=rel_delta,
            origin_mono=origin_mono,
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
            # 同时冻结录制轴坐标。TimelineContext 可能在预览重建/重新对齐后失效，
            # 但 ClipSnapshot 仍允许导出；只保留 common_start/end 会导致失效后
            # 被当前房间标记或新 offset 重新解释成另一段内容。
            recording_start_sec = ctx.common_to_recording(room_id, common_start)
            recording_end_sec = ctx.common_to_recording(room_id, common_end)
            clip = ClipSnapshot(
                clip_id=clip_id,
                clip_group_id=group_id,
                timeline_id=timeline_id,
                recording_id=snap.recording_id,
                common_start=common_start,
                common_end=common_end,
                room_id=room_id,
                recording_start_sec=recording_start_sec,
                recording_end_sec=recording_end_sec,
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

    def on_recording_id_change(
        self,
        room_id: str,
        new_recording_id: str,
        media_start_mono: float | None = None,
    ) -> None:
        """录制 epoch 变化时更新首个 recording，重连则使旧时间线上下文失效。

        首次从“未录制”进入录制时可保留已有预览对齐，并把精确媒体起点写入
        ``recording_to_common_delta``。已有 recording_id 变为另一个非空 ID
        表示录制重启/重连；旧快照的媒体起点已经失效，必须整体失效后重新对齐。
        已创建的 ClipSnapshot 仍冻结旧 recording_id，不受影响。
        """
        invalidate_timeline_id = ""
        with self._lock:
            ctx = self.get_active_timeline_for_room(room_id)
            if ctx is not None and room_id in ctx.room_snapshots:
                snap = ctx.room_snapshots[room_id]
                old_id = snap.recording_id
                if old_id and new_recording_id and old_id != new_recording_id:
                    invalidate_timeline_id = ctx.timeline_id
                else:
                    snap.recording_id = new_recording_id
                    media_start = float(media_start_mono or 0.0)
                    if media_start > 0.0:
                        snap.media_start_mono = media_start
                        # 只叠加房间相对偏移（preview_rel_delta）；若快照来自旧版本
                        # （无该字段），回退到 preview_to_common_delta（旧版无锚点，
                        # p2c 即相对偏移，回退等价）。锚点快照额外减去公共轴零点
                        # origin_mono（最早录制起点），保持时间线从 0 起算会话时长。
                        rel = (
                            snap.preview_rel_delta
                            if snap.preview_rel_delta is not None
                            else snap.preview_to_common_delta
                        )
                        snap.recording_to_common_delta = (
                            media_start + rel - snap.origin_mono
                        )
                    ctx.clip_ready = all(
                        bool(item.recording_id) for item in ctx.room_snapshots.values()
                    )
                    _log.info(
                        "recording epoch 更新: room=%s, old=%s, new=%s, "
                        "media_start=%.6f, clip_ready=%s",
                        room_id,
                        old_id,
                        new_recording_id,
                        media_start,
                        ctx.clip_ready,
                    )
        if invalidate_timeline_id:
            self.invalidate_timeline(
                invalidate_timeline_id,
                f"recording_epoch_change:{room_id}:{new_recording_id}",
            )


# 全局单例
_timeline_service: TimelineService | None = None


def get_timeline_service() -> TimelineService:
    """获取 TimelineService 全局单例。"""
    global _timeline_service
    if _timeline_service is None:
        _timeline_service = TimelineService()
    return _timeline_service
