from __future__ import annotations

from pathlib import Path

from lsc.core.models import RoomTimeSnapshot, TimelineContext
from lsc.exporter.jianying_draft import clip_allowed_for_draft, resolve_common_range

_ERROR_CODES = (
    "draft_dir_missing",
    "no_rooms",
    "no_aligned_context",
    "library_missing",
    "write_failed",
    "invalid_state",
)


def test_load_settings_default_includes_jianying_draft_dir():
    text = Path("python-backend/handlers/room_handler.py").read_text(encoding="utf-8")
    assert "'jianying_draft_dir'" in text or '"jianying_draft_dir"' in text


def test_jianying_handlers_module_exists():
    from handlers import jianying_handlers  # noqa: F401


def test_register_exports_expected_message_names():
    text = Path("python-backend/handlers/jianying_handlers.py").read_text(encoding="utf-8")
    assert "get_jianying_draft_dir" in text
    assert "generate_jianying_draft" in text
    for code in _ERROR_CODES:
        assert code in text


def test_handler_reads_include_pending_flag() -> None:
    src = Path("python-backend/handlers/jianying_handlers.py").read_text(encoding="utf-8")
    assert "include_pending" in src


def test_clip_allowed_rejects_pending_and_approx():
    assert clip_allowed_for_draft({"confirm_status": "pending"}) is False
    assert clip_allowed_for_draft({"mark_precision": "approximate"}) is False
    assert clip_allowed_for_draft(
        {"confirm_status": "user_confirmed", "mark_precision": "exact"}
    ) is True
    assert clip_allowed_for_draft({}) is True


def test_resolve_common_range_prefers_common_fields():
    r = resolve_common_range({"common_start": 1.0, "common_end": 2.5}, None)
    assert r == (1.0, 2.5, "exact")


def test_resolve_common_range_wallclock_with_ctx():
    ctx = TimelineContext(
        timeline_id="t1",
        reference_room_id="r1",
        room_snapshots={
            "r1": RoomTimeSnapshot(room_id="r1", media_start_mono=100.0),
            "r2": RoomTimeSnapshot(room_id="r2", media_start_mono=101.5),
        },
    )
    r = resolve_common_range(
        {"mark_in_wallclock": 105.0, "mark_out_wallclock": 108.0},
        ctx,
    )
    assert r == (5.0, 8.0, "exact")


def test_resolve_common_range_returns_none_without_common_or_wallclock():
    assert resolve_common_range({"mark_in_wallclock": 1.0}, None) is None
    assert resolve_common_range({}, None) is None


def test_handler_derives_room_deltas_from_clips_without_ctx():
    """ctx 缺失（预览重启/未对齐）时，从切片内联坐标反推每房 delta 兜底。"""
    from handlers.jianying_handlers import _derive_room_deltas_from_clips

    clips = [
        {"room_id": "r1", "start": 3.0, "common_start": 3.0},
        {"room_id": "r1", "start": 50.0, "common_start": 50.0},
        {"room_id": "r2", "start": 0.0, "common_start": 4.94},
        {"room_id": "r2", "start": 100.0, "common_start": 104.94},
        {"room_id": "", "start": 1.0, "common_start": 1.0},  # 无 room_id 忽略
        {"start": 1.0, "common_start": 2.0},  # 缺 room_id 忽略
        {"room_id": "r3", "start": 1.0},  # 缺 common 忽略
        {"room_id": "r4", "start": "x", "common_start": 2.0},  # 非数值忽略
    ]
    deltas = _derive_room_deltas_from_clips(clips)
    assert deltas.keys() == {"r1", "r2"}
    assert abs(deltas["r1"] - 0.0) < 1e-9
    assert abs(deltas["r2"] - 4.94) < 1e-9
    assert "r3" not in deltas and "r4" not in deltas


def test_handler_ctx_less_fallback_builds_sources(monkeypatch):
    """无对齐上下文时多房草稿不再直接拒绝：按切片反推 delta 构建房间源。"""

    from handlers import jianying_handlers as h

    class FakeRoom:
        room_id = ""
        streamer_name = ""
        record_output_path = "C:/tmp/a.mp4"
        record_manifest_path = ""

    r1 = FakeRoom()
    r1.room_id = "r1"
    r1.streamer_name = "主房"
    r2 = FakeRoom()
    r2.room_id = "r2"
    r2.streamer_name = "副房"

    class FakeManager:
        def list_rooms(self):
            return [r1, r2]

        def get_room(self, rid):
            return r1 if rid == "r1" else r2

    class FakeTimeline:
        def get_active_timeline_for_room(self, _rid):
            return None

        def get_clip_snapshot(self, _cid):
            return None

    monkeypatch.setattr(h, "get_timeline_service", lambda: FakeTimeline())
    err, sources, clip_sources, options, warnings, _req = h._collect_draft_inputs(
        FakeManager(),
        {
            "room_ids": ["r1", "r2"],
            "main_room_id": "r1",
            "clips": [
                {
                    "clip_id": "c1",
                    "room_id": "r1",
                    "start": 3.0,
                    "end": 80.2,
                    "common_start": 3.0,
                    "common_end": 80.2,
                    "confirm_status": "vision_confirmed",
                },
                {
                    "clip_id": "c2",
                    "room_id": "r2",
                    "start": 0.0,
                    "end": 75.3,
                    "common_start": 4.94,
                    "common_end": 80.24,
                    "confirm_status": "vision_confirmed",
                },
            ],
            "options": {},
        },
    )
    assert err is None, err
    assert {s.room_id: s.recording_to_common_delta for s in sources} == {
        "r1": 0.0,
        "r2": 4.94,
    }
    assert {c.room_id for c in clip_sources} == {"r1", "r2"}
    assert any("反推" in w for w in warnings)
    assert all("需先一键对齐" not in w for w in warnings)


def test_handler_ctx_less_fallback_still_rejects_without_coords(monkeypatch):
    """无 ctx 且切片也没有坐标时，仍按原契约返回 no_aligned_context。"""

    from handlers import jianying_handlers as h

    class FakeRoom:
        room_id = "r1"
        streamer_name = "x"
        record_output_path = "C:/tmp/a.mp4"
        record_manifest_path = ""

    class FakeManager:
        def list_rooms(self):
            return [FakeRoom()]

        def get_room(self, rid):
            return FakeRoom()

    class FakeTimeline:
        def get_active_timeline_for_room(self, _rid):
            return None

        def get_clip_snapshot(self, _cid):
            return None

    monkeypatch.setattr(h, "get_timeline_service", lambda: FakeTimeline())
    err, _s, _c, _o, _w, _req = h._collect_draft_inputs(
        FakeManager(),
        {
            "room_ids": ["r1", "r2"],
            "clips": [{"clip_id": "c1", "room_id": "r1"}],
            "options": {},
        },
    )
    assert err is not None
    assert err["error_code"] == "no_aligned_context"


def test_draft_rejects_in_progress_recording_path(monkeypatch):
    """草稿不得引用停录后必然改名的 _录制中.mp4。"""
    from handlers import jianying_handlers as h

    class Room:
        room_id = "r1"
        streamer_name = "main"
        record_output_path = "D:/output/2026-09-10_02-06-12_录制中.mp4"
        record_manifest_path = ""
        is_recording = True

    room = Room()

    class Manager:
        def list_rooms(self):
            return [room]

        def get_room(self, _rid):
            return room

    class Timeline:
        def get_active_timeline_for_room(self, _rid):
            return None

        def get_clip_snapshot(self, _cid):
            return None

    monkeypatch.setattr(h, "get_timeline_service", lambda: Timeline())
    err, *_rest = h._collect_draft_inputs(
        Manager(),
        {
            "room_ids": ["r1"],
            "main_room_id": "r1",
            "clips": [{
                "clip_id": "c1",
                "room_id": "r1",
                "start": 1.0,
                "end": 10.0,
                "confirm_status": "user_confirmed",
            }],
            "options": {"include_clips": True},
        },
    )
    assert err is not None
    assert err["error_code"] == "recording_not_finalized"


def test_draft_rejects_zero_usable_clips_instead_of_successful_empty_draft(monkeypatch):
    """请求了切片却全被门禁过滤时，不能只生成整段录像轨。"""
    from handlers import jianying_handlers as h

    class Room:
        room_id = "r1"
        streamer_name = "main"
        record_output_path = "D:/output/final.mp4"
        record_manifest_path = ""
        is_recording = False

    room = Room()

    class Manager:
        def list_rooms(self):
            return [room]

        def get_room(self, _rid):
            return room

    class Timeline:
        def get_active_timeline_for_room(self, _rid):
            return None

        def get_clip_snapshot(self, _cid):
            return None

    monkeypatch.setattr(h, "get_timeline_service", lambda: Timeline())
    err, *_rest = h._collect_draft_inputs(
        Manager(),
        {
            "room_ids": ["r1"],
            "main_room_id": "r1",
            "clips": [{
                "clip_id": "c1",
                "room_id": "r1",
                "start": 1.0,
                "end": 10.0,
                "confirm_status": "pending",
                "source_profile": "broadcast",
                "broadcast_audit": "pending_lookahead",
                "end_by": "next_prep",
            }],
            "options": {"include_clips": True},
        },
    )
    assert err is not None
    assert err["error_code"] == "no_usable_clips"
    assert err["requested_clip_count"] == 1
    assert err["included_clip_count"] == 0


# ── 当前录制 epoch 权威校验（recording_id + round_key + 当前 sidecar）────────


def _authority_room(tmp_path, recording_id="rec-cur"):
    import types

    media = tmp_path / "final.mp4"
    media.write_bytes(b"")  # 仅需存在性：sidecar 读取由测试 monkeypatch
    return types.SimpleNamespace(
        room_id="r1",
        streamer_name="主房",
        record_output_path=str(media),
        record_manifest_path="",
        recording_id=recording_id,
    )


def test_reconcile_rejects_round_not_in_current_authority(monkeypatch, tmp_path):
    """存在权威来源但 round_key 不在其中 → 旧分析会话遗留切片被拒。"""
    from handlers import jianying_handlers as h

    monkeypatch.setattr(h, "load_finalization_job", lambda _p: None)
    monkeypatch.setattr(h, "load_analysis_results", lambda _p: None)
    task_state = {
        "target_room_ids": ["r1"],
        "listed_clips": {"r1:round-000002": {"clip_id": "c-2", "round_key": "round-000002"}},
    }
    monkeypatch.setattr(h, "_continuous_tasks", {"t1": task_state})
    monkeypatch.setattr(h, "_analysis_jobs", {})
    clip = {
        "clip_id": "c-stale",
        "room_id": "r1",
        "round_key": "round-000000",
        "start": 1.0,
        "end": 22.3,
        "confirm_status": "pending",
    }
    merged, reason = h._reconcile_clip_with_authority(clip, _authority_room(tmp_path))
    assert merged is None
    assert "旧分析会话遗留切片" in reason


def test_reconcile_allows_current_session_listed_clip(monkeypatch, tmp_path):
    """当前会话 listed_clips 命中的切片照常放行。"""
    from handlers import jianying_handlers as h

    monkeypatch.setattr(h, "load_finalization_job", lambda _p: None)
    monkeypatch.setattr(h, "load_analysis_results", lambda _p: None)
    listed = {
        "clip_id": "c-2",
        "room_id": "r1",
        "round_key": "round-000002",
        "start": 19.3,
        "end": 96.2,
        "confirm_status": "pending",
        "source_profile": "broadcast",
        "broadcast_audit": "passed",
    }
    task_state = {
        "target_room_ids": ["r1"],
        "listed_clips": {"r1:round-000002": listed},
    }
    monkeypatch.setattr(h, "_continuous_tasks", {"t1": task_state})
    monkeypatch.setattr(h, "_analysis_jobs", {})
    merged, reason = h._reconcile_clip_with_authority(
        {"clip_id": "c-2", "room_id": "r1", "round_key": "round-000002"},
        _authority_room(tmp_path),
    )
    assert merged is not None and reason == ""
    assert merged["round_key"] == "round-000002"


def test_reconcile_uses_sidecar_accepted_boundary(monkeypatch, tmp_path):
    """sidecar accepted 终态命中：边界与审计字段以 sidecar 为准，旧 listed 版本不生效。"""
    from handlers import jianying_handlers as h

    sidecar = {
        "accepted_candidates": [{
            "round_key": "round-000019",
            "start": 192.98,
            "end": 294.75,
            "broadcast_audit": "passed",
            "end_by": "broadcast_exclusion",
            "confirm_status": "vision_confirmed",
        }],
        "rejected_candidates": [],
        "pending_candidates": [],
    }
    monkeypatch.setattr(h, "load_finalization_job", lambda _p: sidecar)
    monkeypatch.setattr(h, "load_analysis_results", lambda _p: None)
    monkeypatch.setattr(h, "_continuous_tasks", {})
    monkeypatch.setattr(h, "_analysis_jobs", {})
    clip = {
        "clip_id": "c-19",
        "room_id": "r1",
        "round_key": "round-000019",
        "start": 193.0,
        "end": 294.8,
        "confirm_status": "pending",
        "broadcast_audit": "pending_lookahead",
        "end_by": "next_prep",
    }
    merged, reason = h._reconcile_clip_with_authority(clip, _authority_room(tmp_path))
    assert merged is not None and reason == ""
    assert merged["start"] == 192.98
    assert merged["end"] == 294.75
    assert merged["broadcast_audit"] == "passed"
    assert merged["confirm_status"] == "vision_confirmed"


def test_reconcile_rejects_sidecar_rejected_candidate(monkeypatch, tmp_path):
    """sidecar rejected 命中：被审计拒绝的回合不得进入草稿。"""
    from handlers import jianying_handlers as h

    sidecar = {
        "accepted_candidates": [],
        "rejected_candidates": [{
            "round_key": "round-000000",
            "start": 1.0,
            "end": 22.28,
            "broadcast_audit": "rejected_no_stable_combat_start",
        }],
        "pending_candidates": [],
    }
    monkeypatch.setattr(h, "load_finalization_job", lambda _p: sidecar)
    monkeypatch.setattr(h, "load_analysis_results", lambda _p: None)
    monkeypatch.setattr(h, "_continuous_tasks", {})
    monkeypatch.setattr(h, "_analysis_jobs", {})
    clip = {
        "clip_id": "c-0",
        "room_id": "r1",
        "round_key": "round-000000",
        "start": 1.0,
        "end": 22.3,
        "confirm_status": "pending",
        "broadcast_audit": "pending_lookahead",
    }
    merged, reason = h._reconcile_clip_with_authority(clip, _authority_room(tmp_path))
    assert merged is None
    assert "已拒绝" in reason


def test_reconcile_rejects_other_epoch_recording_id(monkeypatch, tmp_path):
    """切片 recording_id 与房间当前录制不一致 → 旧录制会话切片。"""
    from handlers import jianying_handlers as h

    monkeypatch.setattr(h, "load_finalization_job", lambda _p: None)
    monkeypatch.setattr(h, "load_analysis_results", lambda _p: None)
    monkeypatch.setattr(h, "_continuous_tasks", {})
    monkeypatch.setattr(h, "_analysis_jobs", {})
    clip = {
        "clip_id": "c-old",
        "room_id": "r1",
        "round_key": "round-000002",
        "start": 1.0,
        "end": 20.0,
        "recording_id": "rec-old-epoch",
    }
    merged, reason = h._reconcile_clip_with_authority(clip, _authority_room(tmp_path))
    assert merged is None
    assert "旧录制会话切片" in reason


def test_reconcile_passes_manual_clip_without_round_key(monkeypatch, tmp_path):
    """无 round_key 的手动切片不走 epoch 校验（沿用既有门禁）。"""
    from handlers import jianying_handlers as h

    monkeypatch.setattr(h, "load_finalization_job", lambda _p: None)
    monkeypatch.setattr(h, "load_analysis_results", lambda _p: None)
    monkeypatch.setattr(h, "_continuous_tasks", {})
    monkeypatch.setattr(h, "_analysis_jobs", {})
    clip = {
        "clip_id": "c-manual",
        "room_id": "r1",
        "start": 1.0,
        "end": 20.0,
        "confirm_status": "user_confirmed",
        "mark_precision": "exact",
    }
    merged, reason = h._reconcile_clip_with_authority(clip, _authority_room(tmp_path))
    assert merged is clip and reason == ""


def test_collect_draft_inputs_skips_rejected_listed_clip(monkeypatch, tmp_path):
    """handler 级：任务拒绝 tombstone 命中的切片被跳过并给出原因告警。"""
    from handlers import jianying_handlers as h

    room = _authority_room(tmp_path)

    class Manager:
        def list_rooms(self):
            return [room]

        def get_room(self, _rid):
            return room

    class Timeline:
        def get_active_timeline_for_room(self, _rid):
            return None

        def get_clip_snapshot(self, _cid):
            return None

    monkeypatch.setattr(h, "get_timeline_service", lambda: Timeline())
    monkeypatch.setattr(h, "load_finalization_job", lambda _p: None)
    monkeypatch.setattr(h, "load_analysis_results", lambda _p: None)
    task_state = {
        "target_room_ids": ["r1"],
        "listed_clips": {},
        "rejected_round_keys": {"round-000000": {"reason": "no_stable_combat_start"}},
    }
    monkeypatch.setattr(h, "_continuous_tasks", {"t1": task_state})
    monkeypatch.setattr(h, "_analysis_jobs", {})
    err, _s, clips, _o, warnings, _req = h._collect_draft_inputs(
        Manager(),
        {
            "room_ids": ["r1"],
            "clips": [{
                "clip_id": "c-rejected",
                "room_id": "r1",
                "round_key": "round-000000",
                "start": 1.0,
                "end": 22.3,
                "confirm_status": "pending",
                "source_profile": "broadcast",
                "broadcast_audit": "pending_lookahead",
            }],
            "options": {"include_clips": True},
            "include_pending": True,
        },
    )
    assert err is not None
    assert err["error_code"] == "no_usable_clips"
    assert any("非当前录制权威切片" in w for w in warnings)
    assert clips == []


def test_reconcile_rejects_current_recording_id_but_not_in_authority(monkeypatch, tmp_path):
    """P1 回归：recording_id 与当前录制一致，但 round_key 不在任何权威集合 → 仍拒绝。

    旧实现有「同 recording_id 即兜底放行」的分支，会让 recording_id 恰好正确、
    却不在权威集合中的旧会话/脏切片混入草稿。新实现要求必须命中 listed_clips /
    accepted / rejected / pending / analysis 之一才放行。
    """
    from handlers import jianying_handlers as h

    room = _authority_room(tmp_path, recording_id="rec-cur")
    monkeypatch.setattr(h, "load_finalization_job", lambda _p: None)
    monkeypatch.setattr(h, "load_analysis_results", lambda _p: None)
    task_state = {
        "target_room_ids": ["r1"],
        "listed_clips": {"r1:round-000002": {"clip_id": "c-2", "round_key": "round-000002"}},
    }
    monkeypatch.setattr(h, "_continuous_tasks", {"t1": task_state})
    monkeypatch.setattr(h, "_analysis_jobs", {})
    clip = {
        "clip_id": "c-099",
        "room_id": "r1",
        "round_key": "round-000099",
        "recording_id": "rec-cur",  # 与房间当前录制一致（旧实现会据此放行）
        "start": 500.0,
        "end": 520.0,
        "confirm_status": "vision_confirmed",
        "source_profile": "broadcast",
        "broadcast_audit": "passed",
        "end_by": "broadcast_exclusion",
    }
    merged, reason = h._reconcile_clip_with_authority(clip, room)
    assert merged is None
    assert "旧分析会话遗留切片" in reason


def test_collect_draft_inputs_fills_authoritative_clip_missing_from_frontend(
    monkeypatch, tmp_path
):
    """P2 回归：前端列表遗漏已入列的权威回合 → 由后端 listed_clips 补入草稿。"""
    from handlers import jianying_handlers as h

    room = _authority_room(tmp_path)

    class Manager:
        def list_rooms(self):
            return [room]

        def get_room(self, _rid):
            return room

    class Timeline:
        def get_active_timeline_for_room(self, _rid):
            return None

        def get_clip_snapshot(self, _cid):
            return None

    monkeypatch.setattr(h, "get_timeline_service", lambda: Timeline())
    monkeypatch.setattr(h, "load_finalization_job", lambda _p: None)
    monkeypatch.setattr(h, "load_analysis_results", lambda _p: None)

    def _listed(rk, clip_id, start, end):
        return {
            "clip_id": clip_id,
            "room_id": "r1",
            "round_key": rk,
            "start": start,
            "end": end,
            "confirm_status": "vision_confirmed",
            "source_profile": "broadcast",
            "broadcast_audit": "passed",
            "end_by": "broadcast_exclusion",
        }

    listed_2 = _listed("round-000002", "c-2", 19.3, 96.2)
    listed_9 = _listed("round-000009", "c-9", 193.0, 294.8)
    task_state = {
        "target_room_ids": ["r1"],
        "listed_clips": {"r1:round-000002": listed_2, "r1:round-000009": listed_9},
    }
    monkeypatch.setattr(h, "_continuous_tasks", {"t1": task_state})
    monkeypatch.setattr(h, "_analysis_jobs", {})

    err, _s, clips, _o, warnings, req = h._collect_draft_inputs(
        Manager(),
        {
            "room_ids": ["r1"],
            # 前端只覆盖 round-000002，遗漏了权威快照里的 round-000009
            "clips": [dict(listed_2)],
            "options": {"include_clips": True},
        },
    )
    assert err is None
    ids = {c.clip_id for c in clips}
    assert "c-2" in ids
    assert "c-9" in ids, "前端遗漏的权威回合应被补入草稿"
    assert any("补入草稿" in w for w in warnings)
    assert req == 1  # requested 仅计前端请求数，补入项不计


def test_collect_draft_inputs_respects_fill_authoritative_off(monkeypatch, tmp_path):
    """fill_authoritative=false 时不做补全（未来的单条/子集导出场景）。"""
    from handlers import jianying_handlers as h

    room = _authority_room(tmp_path)

    class Manager:
        def list_rooms(self):
            return [room]

        def get_room(self, _rid):
            return room

    class Timeline:
        def get_active_timeline_for_room(self, _rid):
            return None

        def get_clip_snapshot(self, _cid):
            return None

    monkeypatch.setattr(h, "get_timeline_service", lambda: Timeline())
    monkeypatch.setattr(h, "load_finalization_job", lambda _p: None)
    monkeypatch.setattr(h, "load_analysis_results", lambda _p: None)
    listed_2 = {
        "clip_id": "c-2",
        "room_id": "r1",
        "round_key": "round-000002",
        "start": 19.3,
        "end": 96.2,
        "confirm_status": "vision_confirmed",
        "source_profile": "broadcast",
        "broadcast_audit": "passed",
        "end_by": "broadcast_exclusion",
    }
    listed_9 = dict(listed_2, clip_id="c-9", round_key="round-000009", start=193.0, end=294.8)
    task_state = {
        "target_room_ids": ["r1"],
        "listed_clips": {"r1:round-000002": listed_2, "r1:round-000009": listed_9},
    }
    monkeypatch.setattr(h, "_continuous_tasks", {"t1": task_state})
    monkeypatch.setattr(h, "_analysis_jobs", {})
    err, _s, clips, _o, _w, _req = h._collect_draft_inputs(
        Manager(),
        {
            "room_ids": ["r1"],
            "clips": [dict(listed_2)],
            "options": {"include_clips": True},
            "fill_authoritative": False,
        },
    )
    assert err is None
    assert {c.clip_id for c in clips} == {"c-2"}
