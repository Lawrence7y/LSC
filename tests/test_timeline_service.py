"""TimelineService 单元测试 — TDD 先行。"""
from __future__ import annotations

import time

import pytest

from lsc.core.models import (
    ClipSnapshot,
    RoomTimeSnapshot,
    TimelineContext,
)
from lsc.core.services.timeline_service import TimelineService


@pytest.fixture
def service():
    return TimelineService()


@pytest.fixture
def valid_snapshots():
    """创建一组有效的房间快照（置信度 >= 0.3）。"""
    return {
        "room-1": RoomTimeSnapshot(
            room_id="room-1",
            preview_epoch_id="epoch-1",
            recording_id="rec-1",
            preview_to_common_delta=1.0,
            recording_to_common_delta=2.0,
            align_confidence=0.95,
            media_start_mono=100.0,
        ),
        "room-2": RoomTimeSnapshot(
            room_id="room-2",
            preview_epoch_id="epoch-2",
            recording_id="rec-2",
            preview_to_common_delta=3.0,
            recording_to_common_delta=4.0,
            align_confidence=0.85,
            media_start_mono=105.0,
        ),
    }


class TestTimelineServiceCreate:
    def test_create_timeline_success(self, service, valid_snapshots):
        """所有房间置信度 >= 0.3 时创建成功。"""
        ctx = service.create_timeline("room-1", valid_snapshots)
        assert ctx is not None
        assert ctx.reference_room_id == "room-1"
        assert len(ctx.room_snapshots) == 2
        assert ctx.timeline_id  # 非空 UUID

    def test_create_timeline_reference_missing_from_snapshots(self, service, valid_snapshots):
        """reference_room_id 不在 room_snapshots 中时返回 None。"""
        ctx = service.create_timeline("room-missing", valid_snapshots)
        assert ctx is None
        assert service.get_active_timeline_for_room("room-1") is None

    def test_create_timeline_low_confidence_fails(self, service):
        """任一路置信度 < 0.3 则整组失败。"""
        snapshots = {
            "room-1": RoomTimeSnapshot(
                room_id="room-1", align_confidence=0.95
            ),
            "room-2": RoomTimeSnapshot(
                room_id="room-2", align_confidence=0.1  # 太低
            ),
        }
        ctx = service.create_timeline("room-1", snapshots)
        assert ctx is None

    def test_create_timeline_missing_room_fails(self, service):
        """任一路缺失（空快照）则整组失败。"""
        snapshots = {
            "room-1": RoomTimeSnapshot(room_id="room-1", align_confidence=0.95),
            # room-2 缺失
        }
        ctx = service.create_timeline("room-1", snapshots, required_room_ids=["room-1", "room-2"])
        assert ctx is None

    def test_create_timeline_registers_mapping(self, service, valid_snapshots):
        """创建成功后可通过 room_id 查找 timeline。"""
        ctx = service.create_timeline("room-1", valid_snapshots)
        found = service.get_active_timeline_for_room("room-2")
        assert found is not None
        assert found.timeline_id == ctx.timeline_id


class TestTimelineServiceGet:
    def test_get_timeline_by_id(self, service, valid_snapshots):
        ctx = service.create_timeline("room-1", valid_snapshots)
        found = service.get_timeline(ctx.timeline_id)
        assert found is not None
        assert found.timeline_id == ctx.timeline_id

    def test_get_nonexistent_returns_none(self, service):
        assert service.get_timeline("nonexistent") is None

    def test_get_active_timeline_for_room(self, service, valid_snapshots):
        ctx = service.create_timeline("room-1", valid_snapshots)
        found = service.get_active_timeline_for_room("room-1")
        assert found is not None
        assert found.timeline_id == ctx.timeline_id

    def test_get_active_timeline_no_room(self, service):
        assert service.get_active_timeline_for_room("nonexistent") is None


class TestTimelineServiceInvalidate:
    def test_invalidate_timeline(self, service, valid_snapshots):
        ctx = service.create_timeline("room-1", valid_snapshots)
        service.invalidate_timeline(ctx.timeline_id, "test invalidation")
        assert service.get_timeline(ctx.timeline_id) is None
        assert service.get_active_timeline_for_room("room-1") is None

    def test_invalidate_nonexistent_no_error(self, service):
        service.invalidate_timeline("nonexistent", "no crash")

    def test_invalidate_preserves_clip_snapshots(self, service, valid_snapshots):
        """invalidate 后 ClipSnapshot 仍可查询（设计 §5.1）。"""
        ctx = service.create_timeline("room-1", valid_snapshots)
        clip = service.create_clip_snapshot(
            ctx.timeline_id, "room-1", 10.0, 25.0, source="manual"
        )
        assert clip is not None
        service.invalidate_timeline(ctx.timeline_id, "context expired")
        assert service.get_timeline(ctx.timeline_id) is None
        found = service.get_clip_snapshot(clip.clip_id)
        assert found is not None
        assert found.clip_id == clip.clip_id
        assert found.recording_id == "rec-1"
        assert found.timeline_id == ctx.timeline_id


class TestTimelineServiceClipSnapshot:
    def test_create_clip_snapshot_success(self, service, valid_snapshots):
        ctx = service.create_timeline("room-1", valid_snapshots)
        snap = service.create_clip_snapshot(
            ctx.timeline_id, "room-1", 10.0, 25.0, source="manual"
        )
        assert snap is not None
        assert snap.room_id == "room-1"
        assert snap.common_start == 10.0
        assert snap.common_end == 25.0
        assert snap.source == "manual"
        assert snap.clip_id  # 非空 UUID

    def test_create_clip_snapshot_invalid_timeline(self, service):
        snap = service.create_clip_snapshot("nonexistent", "room-1", 10.0, 25.0)
        assert snap is None

    def test_create_clip_snapshot_missing_room(self, service, valid_snapshots):
        ctx = service.create_timeline("room-1", valid_snapshots)
        snap = service.create_clip_snapshot(ctx.timeline_id, "room-99", 10.0, 25.0)
        assert snap is None

    def test_create_clip_snapshot_negative_time_fails(self, service, valid_snapshots):
        """common_start < 0 应失败。"""
        ctx = service.create_timeline("room-1", valid_snapshots)
        snap = service.create_clip_snapshot(ctx.timeline_id, "room-1", -5.0, 25.0)
        assert snap is None

    def test_create_clip_snapshot_inverted_range_fails(self, service, valid_snapshots):
        """common_start >= common_end 应失败。"""
        ctx = service.create_timeline("room-1", valid_snapshots)
        snap = service.create_clip_snapshot(ctx.timeline_id, "room-1", 30.0, 25.0)
        assert snap is None

    def test_get_clip_snapshot(self, service, valid_snapshots):
        ctx = service.create_timeline("room-1", valid_snapshots)
        snap = service.create_clip_snapshot(ctx.timeline_id, "room-1", 10.0, 25.0)
        found = service.get_clip_snapshot(snap.clip_id)
        assert found is not None
        assert found.clip_id == snap.clip_id

    def test_get_nonexistent_clip_snapshot(self, service):
        assert service.get_clip_snapshot("nonexistent") is None

    def test_delete_clip_snapshot(self, service, valid_snapshots):
        ctx = service.create_timeline("room-1", valid_snapshots)
        snap = service.create_clip_snapshot(ctx.timeline_id, "room-1", 10.0, 25.0)
        assert service.delete_clip_snapshot(snap.clip_id) is True
        assert service.get_clip_snapshot(snap.clip_id) is None
        assert service.delete_clip_snapshot(snap.clip_id) is False

    def test_multi_room_create_rolls_back_on_failure(self, service, valid_snapshots):
        """多房间创建时任一路失败应删除本批已创建的 clips。"""
        ctx = service.create_timeline("room-1", valid_snapshots)
        shared_group = "group_atomic_test"
        created_ids: list[str] = []
        room_ids = ["room-1", "room-2", "room-missing"]
        failed = False
        for room_id in room_ids:
            clip = service.create_clip_snapshot(
                ctx.timeline_id, room_id, 10.0, 25.0, clip_group_id=shared_group,
            )
            if clip is None:
                for cid in created_ids:
                    service.delete_clip_snapshot(cid)
                failed = True
                break
            created_ids.append(clip.clip_id)
        assert failed is True
        assert created_ids  # room-1 / room-2 曾成功
        for cid in created_ids:
            assert service.get_clip_snapshot(cid) is None


class TestTimelineServiceEpochChange:
    def test_preview_epoch_change_invalidates(self, service, valid_snapshots):
        """预览重建/断线时生成新 epoch_id，旧 TimelineContext 失效。"""
        ctx = service.create_timeline("room-1", valid_snapshots)
        old_tid = ctx.timeline_id
        service.on_preview_epoch_change("room-1", "new-epoch")
        assert service.get_timeline(old_tid) is None

    def test_invalidate_preserves_clip_snapshots(self, service, valid_snapshots):
        """Timeline 失效后 ClipSnapshot 仍保留，供 export_clip_by_id 使用。"""
        ctx = service.create_timeline("room-1", valid_snapshots)
        clip = service.create_clip_snapshot(ctx.timeline_id, "room-1", 10.0, 25.0)
        assert clip is not None
        service.invalidate_timeline(ctx.timeline_id, "test")
        assert service.get_timeline(ctx.timeline_id) is None
        assert service.get_clip_snapshot(clip.clip_id) is not None

    def test_recording_id_change_updates(self, service, valid_snapshots):
        """录制启动/重连时生成新 recording_id。"""
        ctx = service.create_timeline("room-1", valid_snapshots)
        service.on_recording_id_change("room-1", "new-rec-id")
        updated = service.get_timeline(ctx.timeline_id)
        # recording_id 变化不应使 timeline 失效，只更新快照
        assert updated is not None
        assert updated.room_snapshots["room-1"].recording_id == "new-rec-id"


class TestTimelineServiceAtomicAlignment:
    def test_atomic_alignment_all_or_nothing(self, service):
        """原子对齐：任一路缺失或低置信则整组失败，不部分写入。"""
        snapshots = {
            "room-1": RoomTimeSnapshot(room_id="room-1", align_confidence=0.95),
            "room-2": RoomTimeSnapshot(room_id="room-2", align_confidence=0.2),  # 太低
        }
        ctx = service.create_timeline("room-1", snapshots)
        assert ctx is None
        # 确认没有任何 timeline 被创建
        assert service.get_active_timeline_for_room("room-1") is None
        assert service.get_active_timeline_for_room("room-2") is None

    def test_atomic_alignment_success_registers_all(self, service, valid_snapshots):
        """原子对齐成功后所有房间都注册到 timeline。"""
        ctx = service.create_timeline("room-1", valid_snapshots)
        assert ctx is not None
        for rid in valid_snapshots:
            found = service.get_active_timeline_for_room(rid)
            assert found is not None
            assert found.timeline_id == ctx.timeline_id
