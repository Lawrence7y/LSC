"""TimelineContext 和 ClipSnapshot 单元测试 — TDD 先行。"""
from __future__ import annotations

import time
from uuid import uuid4

import pytest

from lsc.core.models import (
    ClipSnapshot,
    RoomTimeSnapshot,
    TimelineContext,
)


class TestRoomTimeSnapshot:
    def test_create_defaults(self):
        snap = RoomTimeSnapshot(room_id="room-1")
        assert snap.room_id == "room-1"
        assert snap.preview_epoch_id == ""
        assert snap.recording_id == ""
        assert snap.preview_to_common_delta == 0.0
        assert snap.recording_to_common_delta == 0.0
        assert snap.align_confidence == 0.0
        assert snap.media_start_mono == 0.0

    def test_create_with_values(self):
        snap = RoomTimeSnapshot(
            room_id="room-1",
            preview_epoch_id="epoch-abc",
            recording_id="rec-xyz",
            preview_to_common_delta=2.5,
            recording_to_common_delta=1.8,
            align_confidence=0.95,
            media_start_mono=100.0,
        )
        assert snap.preview_epoch_id == "epoch-abc"
        assert snap.recording_id == "rec-xyz"
        assert snap.preview_to_common_delta == 2.5
        assert snap.recording_to_common_delta == 1.8
        assert snap.align_confidence == 0.95
        assert snap.media_start_mono == 100.0


class TestTimelineContext:
    def test_create_timeline(self):
        ctx = TimelineContext(
            timeline_id="tl-001",
            reference_room_id="room-1",
        )
        assert ctx.timeline_id == "tl-001"
        assert ctx.reference_room_id == "room-1"
        assert ctx.preview_ready is False
        assert ctx.clip_ready is False
        assert ctx.created_at == 0.0
        assert ctx.room_snapshots == {}

    def test_create_with_snapshots(self):
        snap = RoomTimeSnapshot(room_id="room-1", preview_to_common_delta=1.0)
        ctx = TimelineContext(
            timeline_id="tl-001",
            reference_room_id="room-1",
            room_snapshots={"room-1": snap},
        )
        assert "room-1" in ctx.room_snapshots
        assert ctx.room_snapshots["room-1"].preview_to_common_delta == 1.0

    def test_preview_to_common_roundtrip(self):
        """common → preview → common 双向转换误差 < 1ms"""
        snap = RoomTimeSnapshot(
            room_id="room-1",
            preview_to_common_delta=2.5,
        )
        ctx = TimelineContext(
            timeline_id="tl-001",
            reference_room_id="room-1",
            room_snapshots={"room-1": snap},
        )
        original_common = 100.0
        preview = ctx.common_to_preview("room-1", original_common)
        assert preview == pytest.approx(97.5)
        back_to_common = ctx.preview_to_common("room-1", preview)
        assert back_to_common == pytest.approx(original_common, abs=1e-6)

    def test_recording_to_common_roundtrip(self):
        """common → recording → common 双向转换误差 < 1ms"""
        snap = RoomTimeSnapshot(
            room_id="room-1",
            recording_to_common_delta=1.8,
        )
        ctx = TimelineContext(
            timeline_id="tl-001",
            reference_room_id="room-1",
            room_snapshots={"room-1": snap},
        )
        original_common = 200.0
        recording = ctx.common_to_recording("room-1", original_common)
        assert recording == pytest.approx(198.2)
        back_to_common = ctx.recording_to_common("room-1", recording)
        assert back_to_common == pytest.approx(original_common, abs=1e-6)

    def test_media_start_mapping_scenario(self):
        """媒体起点 102/109 时主房 20 秒映射为目标 13 秒
        
        场景：主房间 media_start_mono=102，目标房间 media_start_mono=109
        主房间 common 时间 20s 对应目标房间 recording 时间 13s
        (因为目标房间晚了 7s 开始，所以同一 common 时刻，目标房间的 recording 时间少了 7s)
        """
        main_snap = RoomTimeSnapshot(
            room_id="main",
            recording_to_common_delta=102.0,  # common = recording + 102
        )
        target_snap = RoomTimeSnapshot(
            room_id="target",
            recording_to_common_delta=109.0,  # common = recording + 109
        )
        ctx = TimelineContext(
            timeline_id="tl-001",
            reference_room_id="main",
            room_snapshots={"main": main_snap, "target": target_snap},
        )
        # 主房间 recording=20 → common = 20 + 102 = 122
        common = ctx.recording_to_common("main", 20.0)
        assert common == pytest.approx(122.0)
        # common=122 → 目标房间 recording = 122 - 109 = 13
        target_recording = ctx.common_to_recording("target", common)
        assert target_recording == pytest.approx(13.0)

    def test_missing_room_raises(self):
        """访问不存在的房间应抛出 KeyError"""
        ctx = TimelineContext(
            timeline_id="tl-001",
            reference_room_id="room-1",
        )
        with pytest.raises(KeyError):
            ctx.common_to_preview("nonexistent", 100.0)
        with pytest.raises(KeyError):
            ctx.preview_to_common("nonexistent", 100.0)
        with pytest.raises(KeyError):
            ctx.common_to_recording("nonexistent", 100.0)
        with pytest.raises(KeyError):
            ctx.recording_to_common("nonexistent", 100.0)

    def test_multiple_rooms_independent(self):
        """不同房间的时间转换相互独立"""
        snap_a = RoomTimeSnapshot(room_id="a", preview_to_common_delta=1.0)
        snap_b = RoomTimeSnapshot(room_id="b", preview_to_common_delta=3.0)
        ctx = TimelineContext(
            timeline_id="tl-001",
            reference_room_id="a",
            room_snapshots={"a": snap_a, "b": snap_b},
        )
        common = 50.0
        assert ctx.common_to_preview("a", common) == pytest.approx(49.0)
        assert ctx.common_to_preview("b", common) == pytest.approx(47.0)


class TestClipSnapshot:
    def test_create_snapshot(self):
        snap = ClipSnapshot(
            clip_id="clip-001",
            clip_group_id="group-001",
            timeline_id="tl-001",
            recording_id="rec-001",
            common_start=10.0,
            common_end=25.0,
            room_id="room-1",
        )
        assert snap.clip_id == "clip-001"
        assert snap.clip_group_id == "group-001"
        assert snap.timeline_id == "tl-001"
        assert snap.recording_id == "rec-001"
        assert snap.common_start == 10.0
        assert snap.common_end == 25.0
        assert snap.room_id == "room-1"
        assert snap.source == ""
        assert snap.exported is False
        assert snap.error == ""

    def test_immutability(self):
        """ClipSnapshot 是不可变的（frozen dataclass）"""
        snap = ClipSnapshot(
            clip_id="clip-001",
            clip_group_id="group-001",
            timeline_id="tl-001",
            recording_id="rec-001",
            common_start=10.0,
            common_end=25.0,
            room_id="room-1",
        )
        with pytest.raises(AttributeError):
            snap.common_start = 20.0

    def test_with_source(self):
        snap = ClipSnapshot(
            clip_id="clip-001",
            clip_group_id="group-001",
            timeline_id="tl-001",
            recording_id="rec-001",
            common_start=10.0,
            common_end=25.0,
            room_id="room-1",
            source="ai_highlight",
            source_highlight_id="hl-001",
        )
        assert snap.source == "ai_highlight"
        assert snap.source_highlight_id == "hl-001"


class TestUUIDNoCollision:
    def test_uuid_no_collision(self):
        """10000 次创建 UUID 不碰撞"""
        ids = set()
        for _ in range(10000):
            uid = uuid4().hex
            assert uid not in ids
            ids.add(uid)
        assert len(ids) == 10000
