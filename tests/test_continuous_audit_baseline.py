from __future__ import annotations

from scripts.audit_continuous_analysis import build_report, parse_delivery_events, parse_log


def test_parse_log_pairs_kick_with_worker_completion() -> None:
    log = """
2026-09-05 17:00:00 [INFO] 持续分析 kick worker: room_id=room-a, dur=120s, range=45-120, OCR=True, full=False, finalize=False
2026-09-05 17:01:30 [INFO] 持续分析 Worker 完成: room_id=room-a, 2 回合
2026-09-05 17:01:31 [INFO] 持续分析 kick worker: room_id=room-a, dur=210s, range=90-165, OCR=True, full=False, finalize=False
2026-09-05 17:02:45 [INFO] 持续分析 Worker 完成: room_id=room-a, 3 回合
"""

    scans = parse_log(log.splitlines(), room_id="room-a")

    assert len(scans) == 2
    assert scans[0]["range"] == [45.0, 120.0]
    assert scans[0]["worker_rounds"] == 2
    assert scans[0]["wall_sec"] == 90.0
    assert scans[0]["throughput"] == 0.833
    assert scans[1]["worker_rounds"] == 3


def test_build_report_keeps_analysis_snapshot_summary() -> None:
    report = build_report(
        [
            {
                "room_id": "room-a",
                "kick_at": "2026-09-05 17:00:00",
                "complete_at": "2026-09-05 17:01:00",
                "range": [0.0, 60.0],
                "recorded_duration_at_kick": 120.0,
                "worker_rounds": 1,
                "wall_sec": 60.0,
                "throughput": 1.0,
            }
        ],
        analysis={"highlights": [{"start": 10.0, "end": 20.0}]},
        video_duration=130.0,
    )

    assert report["summary"]["scan_count"] == 1
    assert report["summary"]["analysis_highlights"] == 1
    assert report["summary"]["video_duration"] == 130.0
    assert report["scans"][0]["backlog_at_kick"] == 60.0


def test_parse_log_accepts_runtime_kick_fields_and_delivery_events() -> None:
    lines = [
        "2026-09-08 13:00:00 [INFO] 持续分析 kick worker: room_id=room-a, dur=210s, range=90-165, reason=catchup, backlog=45.0s, new_media=75.0s, throughput_avg=0.8, cycle=90.0s, planner=plugin_adaptive, OCR=True, full=False, finalize=False",
        "2026-09-08 13:00:01 [INFO] 精修候选终态入可靠队列: room_id=room-a, recording_id=rec-a, round_key=round-1, candidate_state_before=pending_lookahead, audit_outcome=accepted, delivery_state=queued, listed_state_after=pending_delivery",
        "2026-09-08 13:00:02 [INFO] 精修候选已由主循环消费: room_id=room-a, delivered=1, delivery_state=delivered, listed_state_after=merged",
    ]

    assert parse_log(lines, room_id="room-a")[0]["backlog_at_kick"] == 45.0
    events = parse_delivery_events(lines, room_id="room-a")
    assert events[0]["round_key"] == "round-1"
    assert events[1]["delivered_count"] == 1
