"""对齐成功后创建 TimelineContext — Task 1 TDD。"""
from __future__ import annotations

import pytest

from lsc.core.services.timeline_service import (
    TimelineService,
    build_room_snapshots_from_align,
)


class TestBuildRoomSnapshotsFromAlign:
    def test_reference_preview_delta_is_zero(self):
        snapshots = build_room_snapshots_from_align(
            reference_room_id="ref",
            offsets={"ref": 0.0, "r1": 0.8},
            scores={"ref": 1.0, "r1": 0.9},
            room_meta={
                "ref": {
                    "preview_epoch_id": "e-ref",
                    "recording_id": "rec-ref",
                    "media_start_mono": 100.0,
                },
                "r1": {
                    "preview_epoch_id": "e-r1",
                    "recording_id": "rec-r1",
                    "media_start_mono": 102.0,
                },
            },
        )
        assert snapshots["ref"].preview_to_common_delta == pytest.approx(0.0)
        assert snapshots["r1"].preview_to_common_delta == pytest.approx(0.8)

    def test_recording_delta_equals_media_start_plus_preview_delta(self):
        snapshots = build_room_snapshots_from_align(
            reference_room_id="ref",
            offsets={"ref": 0.0, "r1": 0.8},
            scores={"ref": 1.0, "r1": 0.9},
            room_meta={
                "ref": {"media_start_mono": 100.0, "recording_id": "a", "preview_epoch_id": "e0"},
                "r1": {"media_start_mono": 102.0, "recording_id": "b", "preview_epoch_id": "e1"},
            },
        )
        # recording_to_common_delta = media_start_mono + preview_to_common_delta
        assert snapshots["ref"].recording_to_common_delta == pytest.approx(100.0)
        assert snapshots["r1"].recording_to_common_delta == pytest.approx(102.0 + 0.8)

    def test_excludes_low_confidence_rooms(self):
        snapshots = build_room_snapshots_from_align(
            reference_room_id="ref",
            offsets={"ref": 0.0, "r1": 0.5, "r2": 0.3},
            scores={"ref": 1.0, "r1": 0.9, "r2": 0.1},
            room_meta={
                "ref": {"media_start_mono": 10.0},
                "r1": {"media_start_mono": 11.0},
                "r2": {"media_start_mono": 12.0},
            },
            confidence_threshold=0.3,
        )
        assert set(snapshots) == {"ref", "r1"}
        assert snapshots["r1"].align_confidence == pytest.approx(0.9)

    def test_relative_to_non_zero_reference_offset(self):
        """若基准房间 offset 非 0，其它房间 delta 仍相对基准。"""
        snapshots = build_room_snapshots_from_align(
            reference_room_id="ref",
            offsets={"ref": 0.2, "r1": 1.0},
            scores={"ref": 1.0, "r1": 0.95},
            room_meta={
                "ref": {"media_start_mono": 50.0},
                "r1": {"media_start_mono": 50.0},
            },
        )
        assert snapshots["ref"].preview_to_common_delta == pytest.approx(0.0)
        assert snapshots["r1"].preview_to_common_delta == pytest.approx(0.8)


class TestCreateTimelineFromAlignSnapshots:
    def test_create_timeline_sets_preview_ready_and_clip_ready(self):
        service = TimelineService()
        snapshots = build_room_snapshots_from_align(
            reference_room_id="ref",
            offsets={"ref": 0.0, "r1": 0.8},
            scores={"ref": 1.0, "r1": 0.9},
            room_meta={
                "ref": {
                    "preview_epoch_id": "e-ref",
                    "recording_id": "rec-ref",
                    "media_start_mono": 100.0,
                },
                "r1": {
                    "preview_epoch_id": "e-r1",
                    "recording_id": "rec-r1",
                    "media_start_mono": 102.0,
                },
            },
        )
        ctx = service.create_timeline("ref", snapshots, required_room_ids=["ref", "r1"])
        assert ctx is not None
        assert ctx.preview_ready is True
        assert ctx.clip_ready is True
        assert ctx.room_snapshots["ref"].preview_to_common_delta == pytest.approx(0.0)
        assert ctx.room_snapshots["r1"].preview_to_common_delta == pytest.approx(0.8)

    def test_clip_ready_false_without_recording_ids(self):
        service = TimelineService()
        snapshots = build_room_snapshots_from_align(
            reference_room_id="ref",
            offsets={"ref": 0.0, "r1": 0.8},
            scores={"ref": 1.0, "r1": 0.9},
            room_meta={
                "ref": {"media_start_mono": 100.0, "recording_id": ""},
                "r1": {"media_start_mono": 102.0, "recording_id": ""},
            },
        )
        ctx = service.create_timeline("ref", snapshots)
        assert ctx is not None
        assert ctx.preview_ready is True
        assert ctx.clip_ready is False

    def test_create_timeline_requires_reference_in_snapshots(self):
        """reference 不在 snapshots 中时创建失败。"""
        service = TimelineService()
        snapshots = build_room_snapshots_from_align(
            reference_room_id="ref",
            offsets={"r1": 0.8},  # 故意不含 ref
            scores={"r1": 0.9},
            room_meta={"r1": {"media_start_mono": 102.0, "recording_id": "rec-r1"}},
        )
        # build 不会包含 ref；create 应拒绝
        assert "ref" not in snapshots
        ctx = service.create_timeline("ref", snapshots)
        assert ctx is None


class TestInvalidateBroadcastHook:
    def test_invalidate_notifies_listener(self):
        service = TimelineService()
        snapshots = build_room_snapshots_from_align(
            reference_room_id="ref",
            offsets={"ref": 0.0, "r1": 0.8},
            scores={"ref": 1.0, "r1": 0.9},
            room_meta={
                "ref": {"media_start_mono": 1.0, "recording_id": "a"},
                "r1": {"media_start_mono": 2.0, "recording_id": "b"},
            },
        )
        ctx = service.create_timeline("ref", snapshots)
        assert ctx is not None

        events: list[tuple[str, str]] = []
        service.add_invalidate_listener(lambda tid, reason: events.append((tid, reason)))

        service.on_preview_epoch_change("ref", "new-epoch")
        assert len(events) == 1
        assert events[0][0] == ctx.timeline_id
        assert "preview_epoch" in events[0][1]
        assert service.get_timeline(ctx.timeline_id) is None

    def test_invalidate_keeps_existing_clips(self):
        """上下文失效后 ClipSnapshot 仍可导出（recording_id 未变）。"""
        service = TimelineService()
        snapshots = build_room_snapshots_from_align(
            reference_room_id="ref",
            offsets={"ref": 0.0, "r1": 0.8},
            scores={"ref": 1.0, "r1": 0.9},
            room_meta={
                "ref": {"media_start_mono": 1.0, "recording_id": "a"},
                "r1": {"media_start_mono": 2.0, "recording_id": "b"},
            },
        )
        ctx = service.create_timeline("ref", snapshots)
        clip = service.create_clip_snapshot(ctx.timeline_id, "ref", 5.0, 15.0)
        assert clip is not None
        service.invalidate_timeline(ctx.timeline_id, "realign")
        assert service.get_clip_snapshot(clip.clip_id) is not None
        assert service.get_clip_snapshot(clip.clip_id).recording_id == "a"
