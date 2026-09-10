from __future__ import annotations

from types import SimpleNamespace

from continuous_finalization import (
    FinalizationJob,
    boundary_quality_reason_code,
    classify_boundary_quality,
    finalization_requires_full_rescan,
    merge_ranges,
    uncovered_ranges,
)
from persistence import load_finalization_job, save_finalization_job


def test_merge_ranges_coalesces_overlap_and_adjacent_windows() -> None:
    assert merge_ranges([(0.0, 10.0), (9.5, 20.0), (20.0, 30.0), (40.0, 50.0)]) == [
        (0.0, 30.0),
        (40.0, 50.0),
    ]


def test_uncovered_ranges_reports_middle_and_tail() -> None:
    assert uncovered_ranges(
        [(10.0, 20.0), (0.0, 5.0), (19.0, 30.0)],
        0.0,
        40.0,
    ) == [(5.0, 10.0), (30.0, 40.0)]


def test_full_rescan_uses_coverage_gaps_not_tail_lag() -> None:
    # Continuous coverage reaches the analyzed cursor; the finalizer should
    # scan only the unwritten tail instead of repeating the whole history.
    assert not finalization_requires_full_rescan(
        final_duration=3746.4,
        last_analyzed=3174.9,
        coverage_ranges=[(0.0, 3174.9)],
    )
    # A historical coverage gap still requires a fresh pass.
    assert finalization_requires_full_rescan(
        final_duration=3746.4,
        last_analyzed=3174.9,
        coverage_ranges=[(0.0, 1200.0), (1300.0, 3174.9)],
    )
    assert finalization_requires_full_rescan(
        final_duration=100.0,
        last_analyzed=100.0,
        coverage_ranges=[(0.0, 100.0)],
        scan_error=True,
    )
    assert not finalization_requires_full_rescan(
        final_duration=100.0,
        last_analyzed=100.0,
        coverage_ranges=[(0.0, 100.0)],
    )


def test_failed_window_does_not_advance_coverage_cursor() -> None:
    job = FinalizationJob.create(
        job_id="job-failure",
        room_id="room-1",
        recording_id="recording-1",
        source_path="D:/recording.mp4",
        final_duration=100.0,
    )

    job.add_failure(40.0, 60.0, "ocr timeout")

    assert job.scan_cursor == 0.0
    assert job.coverage_ranges == []
    assert job.failed_ranges[0]["start"] == 40.0
    assert job.last_error == "ocr timeout"


def test_successful_retry_clears_resolved_failed_range() -> None:
    job = FinalizationJob.create(
        job_id="job-retry",
        room_id="room-1",
        recording_id="recording-1",
        source_path="D:/recording.mp4",
        final_duration=100.0,
    )
    job.add_failure(40.0, 60.0, "ocr timeout")
    job.add_coverage(0.0, 100.0)

    assert job.failed_ranges == []
    assert job.is_fully_covered()


def test_finalization_job_round_trip_preserves_coverage_and_phase() -> None:
    job = FinalizationJob.create(
        job_id="job-1",
        room_id="room-1",
        recording_id="recording-1",
        source_path="D:/recording.mp4",
        final_duration=100.0,
    )
    job.add_coverage(0.0, 40.0)
    job.add_coverage(39.0, 100.0)
    job.phase = "completed"

    restored = FinalizationJob.from_dict(job.to_dict())

    assert restored.job_id == "job-1"
    assert restored.phase == "completed"
    assert restored.coverage_ranges == [(0.0, 100.0)]
    assert restored.is_fully_covered()


def test_finalization_job_persists_next_to_recording(tmp_path) -> None:
    video = tmp_path / "recording.mp4"
    video.write_bytes(b"mp4")
    job = FinalizationJob.create(
        job_id="job-2",
        room_id="room-2",
        recording_id="recording-2",
        source_path=str(video),
        final_duration=30.0,
    )
    job.add_coverage(0.0, 30.0)

    assert save_finalization_job(str(video), job.to_dict())
    restored = load_finalization_job(str(video))

    assert restored is not None
    assert restored["job_id"] == "job-2"
    assert restored["coverage_ranges"] == [[0.0, 30.0]]


def test_stop_checkpoint_is_written_before_loop_can_create_finalization_job(tmp_path) -> None:
    from handlers.analysis_handlers import _persist_finalization_checkpoint

    video = tmp_path / "recording_in_progress.mp4"
    video.write_bytes(b"mp4")
    state = {
        "recorded_duration": 240.0,
        "coverage_ranges": [[0.0, 120.0]],
    }
    room = SimpleNamespace(
        record_output_path=str(video),
        record_manifest_path="",
        recording_id="recording-3",
    )

    payload = _persist_finalization_checkpoint(state, room, "room-3")
    restored = load_finalization_job(str(video))

    assert payload is not None
    assert restored is not None
    assert restored["phase"] == "pending"
    assert restored["final_duration"] == 240.0
    assert restored["coverage_ranges"] == [[0.0, 120.0]]
    assert state["finalization_job_id"] == restored["job_id"]


def test_load_checkpoint_recovers_error_phase_job(tmp_path) -> None:
    """回归“收尾失败后 pending 解说候选永久丢失”：phase==error 是超时/扫描
    失败重试 3 次后放弃的标记，其 pending_candidates 仍有效且正是需要恢复的
    对象，加载器不得跳过（只跳 completed）。"""
    from handlers.analysis_handlers import _load_finalization_checkpoint_for_room

    video = tmp_path / "recording.mp4"
    video.write_bytes(b"mp4")
    job = FinalizationJob.create(
        job_id="job-err",
        room_id="room-err",
        recording_id="recording-err",
        source_path=str(video),
        final_duration=120.0,
    )
    job.phase = "error"
    job.update_pending_candidates([{"start": 10.0, "end": 60.0}])
    assert save_finalization_job(str(video), job.to_dict())

    room = SimpleNamespace(
        record_output_path=str(video),
        record_manifest_path="",
        recording_id="recording-err",
    )
    recovered = _load_finalization_checkpoint_for_room(room, "room-err")

    assert recovered is not None
    assert recovered.job_id == "job-err"
    assert recovered.phase == "error"
    assert recovered.pending_candidates and recovered.pending_candidates[0]["start"] == 10.0


def test_load_checkpoint_skips_completed_job(tmp_path) -> None:
    """phase==completed 的收尾任务无需恢复，加载器应跳过。"""
    from handlers.analysis_handlers import _load_finalization_checkpoint_for_room

    video = tmp_path / "recording.mp4"
    video.write_bytes(b"mp4")
    job = FinalizationJob.create(
        job_id="job-done",
        room_id="room-done",
        recording_id="recording-done",
        source_path=str(video),
        final_duration=120.0,
    )
    job.phase = "completed"
    assert save_finalization_job(str(video), job.to_dict())

    room = SimpleNamespace(
        record_output_path=str(video),
        record_manifest_path="",
        recording_id="recording-done",
    )
    assert _load_finalization_checkpoint_for_room(room, "room-done") is None


def test_load_checkpoint_rebinds_from_in_progress_filename_after_recording_rename(tmp_path) -> None:
    from handlers.analysis_handlers import _load_finalization_checkpoint_for_room

    old_path = tmp_path / "2026-09-08_13-00-00_录制中.mp4"
    final_path = tmp_path / "2026-09-08_13-00-00_至_2026-09-08_14-00-00.mp4"
    old_path.write_bytes(b"recording")
    job = FinalizationJob.create(
        job_id="job-rename",
        room_id="room-rename",
        recording_id="epoch-rename",
        source_path=str(old_path),
        final_duration=60.0,
    )
    assert save_finalization_job(str(old_path), job.to_dict())
    old_path.rename(final_path)
    room = SimpleNamespace(
        record_output_path=str(old_path),
        record_manifest_path="",
        recording_id="epoch-rename",
    )

    recovered = _load_finalization_checkpoint_for_room(room, "room-rename")

    assert recovered is not None
    assert recovered.source_path == str(final_path)
    assert load_finalization_job(str(final_path))["source_path"] == str(final_path)


def test_stale_in_progress_sidecar_cannot_resurrect_completed_final_sidecar(tmp_path) -> None:
    from handlers.analysis_handlers import _load_finalization_checkpoint_for_room

    old_path = tmp_path / "2026-09-08_13-00-00_录制中.mp4"
    final_path = tmp_path / "2026-09-08_13-00-00_至_2026-09-08_14-00-00.mp4"
    old_path.write_bytes(b"recording")
    old_job = FinalizationJob.create(
        job_id="job-old",
        room_id="room-resurrect",
        recording_id="epoch-resurrect",
        source_path=str(old_path),
        final_duration=60.0,
    )
    save_finalization_job(str(old_path), old_job.to_dict())
    old_path.rename(final_path)
    final_job = FinalizationJob.create(
        job_id="job-final",
        room_id="room-resurrect",
        recording_id="epoch-resurrect",
        source_path=str(final_path),
        final_duration=60.0,
    )
    final_job.phase = "completed"
    final_job.updated_at += 10.0
    save_finalization_job(str(final_path), final_job.to_dict())
    room = SimpleNamespace(
        record_output_path=str(old_path),
        record_manifest_path="",
        recording_id="epoch-resurrect",
    )

    assert _load_finalization_checkpoint_for_room(room, "room-resurrect") is None
    assert load_finalization_job(str(final_path))["phase"] == "completed"


def test_resume_checkpoint_rejects_recording_epoch_mismatch(tmp_path) -> None:
    from handlers.analysis_handlers import (
        _finalization_validation_error,
        _load_finalization_checkpoint_for_room,
    )

    video = tmp_path / "recording.mp4"
    video.write_bytes(b"recording")
    job = FinalizationJob.create(
        job_id="job-epoch",
        room_id="room-epoch",
        recording_id="old-epoch",
        source_path=str(video),
        final_duration=60.0,
    )
    assert save_finalization_job(str(video), job.to_dict())
    room = SimpleNamespace(
        record_output_path=str(video),
        record_manifest_path="",
        recording_id="new-epoch",
    )

    assert _load_finalization_checkpoint_for_room(room, "room-epoch") is None
    assert "recording_id" in (_finalization_validation_error(room, "room-epoch") or "")


def test_boundary_quality_separates_semantic_confirmation_from_precision() -> None:
    assert classify_boundary_quality(
        confirm_status="vision_confirmed",
        end_by="next_prep",
        boundary_refined=True,
        start_confidence=0.95,
        end_confidence=0.94,
        start_delta=0.2,
        end_delta=0.3,
    ) == "precise"
    assert classify_boundary_quality(
        confirm_status="vision_confirmed",
        end_by="next_prep",
        boundary_refined=False,
        start_confidence=0.95,
        end_confidence=0.94,
    ) == "coarse"
    assert classify_boundary_quality(
        confirm_status="pending",
        end_by="open_tail",
        boundary_refined=True,
        start_confidence=0.95,
        end_confidence=0.2,
    ) == "pending"
    assert classify_boundary_quality(
        confirm_status="vision_confirmed",
        end_by="next_prep",
        boundary_refined=True,
        start_confidence=0.95,
        end_confidence=0.94,
        start_delta=2.0,
        end_delta=0.2,
    ) == "invalid"


def test_broadcast_boundary_quality_strict_evidence_gate() -> None:
    """B-04: broadcast 严格质量门禁测试。"""
    # 具备完整双边界证据 + audit passed -> precise
    assert classify_boundary_quality(
        confirm_status="vision_confirmed",
        end_by="broadcast_exclusion",
        boundary_refined=True,
        start_confidence=0.95,
        end_confidence=0.92,
        start_delta=0.1,
        end_delta=0.2,
        source_profile="broadcast",
        broadcast_audit="passed",
        broadcast_audit_reason="broadcast_replay_or_non_game",
    ) == "precise"

    # broadcast 缺少任一 delta 字段 -> coarse，不能是 precise
    assert classify_boundary_quality(
        confirm_status="vision_confirmed",
        end_by="broadcast_exclusion",
        boundary_refined=True,
        start_confidence=0.95,
        end_confidence=0.92,
        start_delta=0.1,
        end_delta=None,
        source_profile="broadcast",
        broadcast_audit="passed",
        broadcast_audit_reason="broadcast_replay_or_non_game",
    ) == "coarse"

    # broadcast audit 未通过 (如 pending 或 skipped) -> pending
    assert classify_boundary_quality(
        confirm_status="vision_confirmed",
        end_by="broadcast_exclusion",
        boundary_refined=True,
        start_confidence=0.95,
        end_confidence=0.92,
        start_delta=0.1,
        end_delta=0.2,
        source_profile="broadcast",
        broadcast_audit="pending_no_exclusion",
        broadcast_audit_reason="none",
    ) == "pending"

    # Broadcast 的 1fps 粗边界可能与视觉精修相差约 2s；双边界证据和
    # audit 均通过时不应因为采样漂移被错误降为 invalid。
    assert classify_boundary_quality(
        confirm_status="vision_confirmed",
        end_by="broadcast_exclusion",
        boundary_refined=True,
        start_confidence=0.95,
        end_confidence=0.92,
        start_delta=2.0,
        end_delta=0.4,
        source_profile="broadcast",
        broadcast_audit="passed",
        broadcast_audit_reason="broadcast_replay_or_non_game",
    ) == "precise"

    # broadcast 无排除证据 (reason_none) 且非 next_prep -> pending
    assert classify_boundary_quality(
        confirm_status="vision_confirmed",
        end_by="next_combat",
        boundary_refined=True,
        start_confidence=0.95,
        end_confidence=0.92,
        start_delta=0.1,
        end_delta=0.2,
        source_profile="broadcast",
        broadcast_audit="passed",
        broadcast_audit_reason="reason_none",
    ) == "pending"


def test_broadcast_replay_exclusion_large_end_delta_is_structural_not_invalid() -> None:
    """2026-09-08 修复：回放/暂停截断（broadcast_exclusion + audit passed）的
    end_delta 是结构性截断距离（实测 15–65s），不得按 ±3s 采样容差判 invalid；
    否则所有被回放过滤的正常回合都会永久 pending 无法自动导出。"""
    # 审计通过 + 大 end_delta（回放截断）：不再 invalid，可评为 precise
    assert classify_boundary_quality(
        confirm_status="vision_confirmed",
        end_by="broadcast_exclusion",
        boundary_refined=True,
        start_confidence=0.95,
        end_confidence=0.92,
        start_delta=0.1,
        end_delta=63.7,
        source_profile="broadcast",
        broadcast_audit="passed",
        broadcast_audit_reason="broadcast_replay_or_non_game",
    ) == "precise"
    # 终点甚至可能比粗出点更晚（交战延续到粗出点之后）
    assert classify_boundary_quality(
        confirm_status="vision_confirmed",
        end_by="broadcast_exclusion",
        boundary_refined=True,
        start_confidence=0.95,
        end_confidence=0.92,
        start_delta=0.1,
        end_delta=14.4,
        source_profile="broadcast",
        broadcast_audit="passed",
        broadcast_audit_reason="broadcast_replay_or_non_game",
    ) == "precise"
    # 豁免只适用于 audit passed 的排除截断：审计未通过仍按原规则
    assert classify_boundary_quality(
        confirm_status="vision_confirmed",
        end_by="broadcast_exclusion",
        boundary_refined=True,
        start_confidence=0.95,
        end_confidence=0.92,
        start_delta=0.1,
        end_delta=63.7,
        source_profile="broadcast",
        broadcast_audit="pending_no_exclusion",
        broadcast_audit_reason="none",
    ) == "pending"
    # 起点精修仍是细粒度密扫：start_delta 大（>3s）仍然 invalid
    assert classify_boundary_quality(
        confirm_status="vision_confirmed",
        end_by="broadcast_exclusion",
        boundary_refined=True,
        start_confidence=0.95,
        end_confidence=0.92,
        start_delta=6.0,
        end_delta=63.7,
        source_profile="broadcast",
        broadcast_audit="passed",
        broadcast_audit_reason="broadcast_replay_or_non_game",
    ) == "invalid"


    # non-exclusion 出点（next_prep）不享受豁免：大 end_delta 仍 invalid
    assert classify_boundary_quality(
        confirm_status="vision_confirmed",
        end_by="next_prep",
        boundary_refined=True,
        start_confidence=0.95,
        end_confidence=0.92,
        start_delta=0.1,
        end_delta=6.0,
        source_profile="broadcast",
        broadcast_audit="passed",
        broadcast_audit_reason="next_prep",
    ) == "invalid"


def test_boundary_quality_reason_code_explains_invalid_evidence() -> None:
    assert boundary_quality_reason_code(
        confirm_status="vision_confirmed",
        end_by="next_prep",
        boundary_refined=True,
        start_confidence=0.95,
        end_confidence=0.95,
        start_delta=-0.1,
        end_delta=0.2,
    ) == "negative_boundary_delta"
    assert boundary_quality_reason_code(
        confirm_status="vision_confirmed",
        end_by="broadcast_exclusion",
        boundary_refined=True,
        source_profile="broadcast",
        broadcast_audit="passed",
        broadcast_audit_reason="broadcast_replay_or_non_game",
    ) == "missing_bidirectional_boundary_evidence"
    # 负 delta（不可能的回退）永远 invalid
    assert classify_boundary_quality(
        confirm_status="vision_confirmed",
        end_by="broadcast_exclusion",
        boundary_refined=True,
        start_confidence=0.95,
        end_confidence=0.92,
        start_delta=-1.0,
        end_delta=0.2,
        source_profile="broadcast",
        broadcast_audit="passed",
        broadcast_audit_reason="broadcast_replay_or_non_game",
    ) == "invalid"


def test_set_boundary_quality_broadcast_strict_wiring() -> None:
    """B-04: 验证生产 _set_boundary_quality 真正将 broadcast 参数透传给严格分类器。"""
    from handlers.room_handler import _set_boundary_quality

    # 缺少物理证据的 broadcast 片段，绝不能评为 precise
    fake_broadcast_round = {
        "start": 10.0,
        "end": 50.0,
        "source_profile": "broadcast",
        "confirm_status": "vision_confirmed",
        "end_by": "broadcast_exclusion",
        "boundary_refined": True,
        # 缺少 start_delta / end_delta
    }
    q = _set_boundary_quality(fake_broadcast_round)
    assert q == "coarse"
    assert fake_broadcast_round["boundary_review_required"] is True

    # 具备完整双边界证据的 broadcast 片段 -> precise
    full_broadcast_round = {
        "start": 10.0,
        "end": 50.0,
        "source_profile": "broadcast",
        "confirm_status": "vision_confirmed",
        "end_by": "broadcast_exclusion",
        "boundary_refined": True,
        "broadcast_audit": "passed",
        "broadcast_audit_reason": "broadcast_replay_or_non_game",
        "start_confidence": 0.95,
        "end_confidence": 0.92,
        "start_delta": 0.2,
        "end_delta": 0.4,
    }
    q2 = _set_boundary_quality(full_broadcast_round)
    assert q2 == "precise"
    assert full_broadcast_round["boundary_review_required"] is False




def test_finalization_job_pending_candidates_persistence() -> None:
    job = FinalizationJob.create(
        job_id="job-candidates",
        room_id="room-c",
        recording_id="rec-c",
        source_path="D:/rec.mp4",
        final_duration=500.0,
    )
    job.update_pending_candidates([
        {"start": 10.0, "end": 80.0, "broadcast_audit": "pending_lookahead"},
        {"start": 95.5, "end": 160.0, "broadcast_audit": "pending_lookahead"},
    ])
    assert len(job.pending_candidates) == 2
    assert job.candidate_count == 2

    # 重复 candidate 更新应合并而非追加
    job.update_pending_candidates([
        {"start": 10.0, "end": 82.0, "broadcast_audit": "passed"},
    ])
    assert len(job.pending_candidates) == 2
    assert job.pending_candidates[0]["end"] == 82.0
    assert job.pending_candidates[0]["broadcast_audit"] == "passed"

    # to_dict / from_dict 往返保持
    payload = job.to_dict()
    restored = FinalizationJob.from_dict(payload)
    assert len(restored.pending_candidates) == 2
    assert restored.pending_candidates[0]["start"] == 10.0


def test_finalization_job_replace_pending_candidates_clears_terminal_items() -> None:
    job = FinalizationJob.create(
        job_id="job-replace",
        room_id="room-r",
        recording_id="rec-r",
        source_path="D:/rec-r.mp4",
        final_duration=100.0,
    )
    job.update_pending_candidates([
        {"start": 10.0, "end": 50.0, "broadcast_audit": "pending_lookahead"},
        {"start": 60.0, "end": 90.0, "broadcast_audit": "pending_lookahead"},
    ])

    job.replace_pending_candidates([
        {"start": 60.0, "end": 92.0, "broadcast_audit": "pending_lookahead"},
    ])
    assert len(job.pending_candidates) == 1
    assert job.pending_candidates[0]["start"] == 60.0

    job.replace_pending_candidates([])
    assert job.pending_candidates == []


def test_finalization_job_persists_and_deduplicates_refine_delivery_queue() -> None:
    job = FinalizationJob.create(
        job_id="job-delivery",
        room_id="room-r",
        recording_id="rec-r",
        source_path="D:/rec-r.mp4",
        final_duration=100.0,
    )
    candidate = {"start": 10.0, "end": 70.0, "round_key": "round-1"}

    assert job.enqueue_refine_result(candidate, "room-r:rec-r:round-1") is True
    assert job.enqueue_refine_result(candidate, "room-r:rec-r:round-1") is False
    restored = FinalizationJob.from_dict(job.to_dict())
    assert len(restored.refine_result_queue) == 1
    assert restored.ack_refine_results(["room-r:rec-r:round-1"]) == 1
    assert restored.refine_result_queue == []


# ── A3 守卫（2026-09-10）：precise 需要**交叉证据**，不能只靠 start_delta 自洽 ──


def test_wrong_start_never_precise_when_visual_agreement_is_low() -> None:
    """起点画面不是交战时不得评为 precise（A3：错入点必须挡住）。

    A4 把 start_confidence 由二值代理（0.95/0.70）换成实测视觉占比后，
    `confidence < 0.8 → coarse` 这道门才真正具备判别力——此前它只是复述
    "start_delta 是否存在"，给不出 boundary_refined 之外的任何证据。
    """
    # 对照：实测一致性高 + 双向 delta 齐备 → 仍可 precise
    assert classify_boundary_quality(
        confirm_status="vision_confirmed",
        end_by="next_prep",
        boundary_refined=True,
        start_confidence=0.9,
        end_confidence=0.94,
        start_delta=0.2,
        end_delta=0.3,
    ) == "precise"
    # 起点窗口内只有 1/4 帧是 combat（典型"回放里的实战镜头被当入点"）→ 不得 precise
    assert classify_boundary_quality(
        confirm_status="vision_confirmed",
        end_by="next_prep",
        boundary_refined=True,
        start_confidence=0.25,
        end_confidence=0.94,
        start_delta=0.2,
        end_delta=0.3,
    ) == "coarse"
    # 边界（恰好 0.8）仍视为达标，避免阈值抖动把正常回合误降级
    assert classify_boundary_quality(
        confirm_status="vision_confirmed",
        end_by="next_prep",
        boundary_refined=True,
        start_confidence=0.8,
        end_confidence=0.94,
        start_delta=0.2,
        end_delta=0.3,
    ) == "precise"
