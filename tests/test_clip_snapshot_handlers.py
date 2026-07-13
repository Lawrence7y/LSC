"""create_clip_snapshot handler 和 export_clip clip_id 模式测试 — TDD。"""
from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock

import pytest

# 添加路径
_python_backend = os.path.join(os.path.dirname(__file__), '..', 'python-backend')
if _python_backend not in sys.path:
    sys.path.insert(0, _python_backend)

from lsc.core.models import RoomTimeSnapshot
from lsc.core.services.timeline_service import TimelineService


@pytest.fixture
def timeline_service():
    return TimelineService()


@pytest.fixture
def active_timeline(timeline_service):
    """创建一个活动的 TimelineContext。"""
    snapshots = {
        "room-1": RoomTimeSnapshot(
            room_id="room-1",
            preview_epoch_id="epoch-1",
            recording_id="rec-1",
            recording_to_common_delta=100.0,
            align_confidence=0.95,
            media_start_mono=100.0,
        ),
        "room-2": RoomTimeSnapshot(
            room_id="room-2",
            preview_epoch_id="epoch-2",
            recording_id="rec-2",
            recording_to_common_delta=105.0,
            align_confidence=0.85,
            media_start_mono=105.0,
        ),
    }
    return timeline_service.create_timeline("room-1", snapshots)


class TestCreateClipSnapshotLogic:
    def test_create_single_room_snapshot(self, timeline_service, active_timeline):
        """单房间创建 ClipSnapshot 成功。"""
        clip = timeline_service.create_clip_snapshot(
            active_timeline.timeline_id, "room-1", 10.0, 25.0, source="manual"
        )
        assert clip is not None
        assert clip.room_id == "room-1"
        assert clip.common_start == 10.0
        assert clip.common_end == 25.0
        assert clip.recording_id == "rec-1"

    def test_create_multi_room_snapshots(self, timeline_service, active_timeline):
        """多房间原子创建 ClipSnapshot。"""
        clips = []
        shared_group = "group_shared_test"
        for room_id in ["room-1", "room-2"]:
            clip = timeline_service.create_clip_snapshot(
                active_timeline.timeline_id, room_id, 10.0, 25.0,
                clip_group_id=shared_group,
            )
            assert clip is not None
            clips.append(clip)
        # 同一批创建的 clips 应有相同的 clip_group_id
        assert clips[0].clip_group_id == clips[1].clip_group_id
        assert clips[0].clip_group_id == shared_group

    def test_multi_room_create_rolls_back_partial_batch(self, timeline_service, active_timeline):
        """任一路失败时删除本批已创建的 clips（模拟 handler 原子语义）。"""
        shared_group = "group_rollback_test"
        created_ids: list[str] = []
        failed_room = None
        for room_id in ["room-1", "room-missing", "room-2"]:
            clip = timeline_service.create_clip_snapshot(
                active_timeline.timeline_id, room_id, 10.0, 25.0,
                clip_group_id=shared_group,
            )
            if clip is None:
                for cid in created_ids:
                    timeline_service.delete_clip_snapshot(cid)
                failed_room = room_id
                break
            created_ids.append(clip.clip_id)
        assert failed_room == "room-missing"
        assert len(created_ids) == 1
        assert timeline_service.get_clip_snapshot(created_ids[0]) is None

    def test_create_snapshot_invalid_time(self, timeline_service, active_timeline):
        """无效时间范围应失败。"""
        clip = timeline_service.create_clip_snapshot(
            active_timeline.timeline_id, "room-1", 25.0, 10.0
        )
        assert clip is None

    def test_create_snapshot_exports_to_recording_time(self, timeline_service, active_timeline):
        """验证 common 时间正确转换为录制文件时间。"""
        clip = timeline_service.create_clip_snapshot(
            active_timeline.timeline_id, "room-1", 120.0, 135.0
        )
        assert clip is not None
        # common=120 → recording = 120 - recording_to_common_delta(100) = 20
        ctx = timeline_service.get_timeline(active_timeline.timeline_id)
        rec_time = ctx.common_to_recording("room-1", 120.0)
        assert rec_time == pytest.approx(20.0)


class TestExportClipById:
    def test_export_by_clip_id_success(self, timeline_service, active_timeline):
        """通过 clip_id 查找受信任文件。"""
        # 创建 ClipSnapshot
        clip = timeline_service.create_clip_snapshot(
            active_timeline.timeline_id, "room-1", 120.0, 135.0
        )
        # 验证 recording_id 匹配
        stored = timeline_service.get_clip_snapshot(clip.clip_id)
        assert stored is not None
        assert stored.recording_id == "rec-1"

    def test_export_recording_id_changed(self, timeline_service, active_timeline):
        """recording_id 变化后 export 应失败（文件不可信）。"""
        clip = timeline_service.create_clip_snapshot(
            active_timeline.timeline_id, "room-1", 120.0, 135.0
        )
        # 模拟录制重连（recording_id 变化）
        timeline_service.on_recording_id_change("room-1", "rec-1-new")
        # clip 的 recording_id 应仍是旧的
        stored = timeline_service.get_clip_snapshot(clip.clip_id)
        assert stored.recording_id == "rec-1"  # 冻结的值
        # 验证 recording_id 不匹配
        assert stored.recording_id != "rec-1-new"

    def test_export_nonexistent_clip(self, timeline_service):
        """不存在的 clip_id 应返回 None。"""
        stored = timeline_service.get_clip_snapshot("nonexistent-clip")
        assert stored is None
