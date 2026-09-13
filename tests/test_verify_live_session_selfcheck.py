"""L3 校验器 `scripts/verify_live_session.py` 自身的回归（绿路径 + 红路径）。

为什么要它：门禁如果"永远绿"或"只会红"都毫无价值。绿路径由脚本的 `--self-test`
覆盖；红路径在这里用**合成的"105 类"场景**（4 条已定稿却只写 3 条、响应无逐条明细）
断言它确实会红且点名 `no_finalized_clip_dropped`。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "verify_live_session.py"
ROOM = "room-l3"


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args], cwd=str(ROOT), capture_output=True, text=True
    )


def test_self_test_reports_all_green() -> None:
    proc = _run("--self-test")
    assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"
    assert "7/7 通过" in proc.stdout


def test_finalized_clip_dropped_is_flagged(tmp_path: Path) -> None:
    """105 类回归：权威集合里 2 条已定稿，只写入 1 条 ⇒ 必须红并点名。"""
    def _round(key: str, start: float) -> dict:
        return {
            "round_key": key, "room_id": ROOM, "start": start, "end": start + 60.0,
            "source_profile": "broadcast", "broadcast_audit": "passed",
            "end_quality": "precise", "end_by": "broadcast_exclusion",
            "confirm_status": "vision_confirmed", "end_review_required": False,
        }

    listed = [_round("round-000001", 100.0), _round("round-000002", 200.0)]
    status = {"room_id": ROOM, "phase": "completed", "finalization_state": "completed",
              "pending_queue_depth": 0, "coverage_complete": True, "listed_clips": listed}
    response = {"success": True, "draft_name": "LSC_BAD", "draft_dir": str(tmp_path),
                "tracks": 3, "segments": 4,
                "requested_clip_count": 2, "included_clip_count": 1, "skipped_clip_count": 1,
                "warnings": ["切片 X 未确认/近似定位/未通过赛事审计，已跳过"], "request_id": "req-bad"}
    request = {
        "room_ids": [ROOM], "main_room_id": ROOM, "request_id": "req-bad",
        "clips": [{"round_key": "round-000001"}, {"round_key": "round-000002"}],
    }
    log = tmp_path / "backend-stdout.log"
    log.write_text(
        chr(10).join([
            "2026-09-12 11:00:00 [INFO] lsc.server: Received WS message: "
            f"type=generate_jianying_draft, data={request!r}",
            "2026-09-12 11:00:01 [INFO] lsc.handlers: "
            f"终态权威快照已保留: room_id={ROOM}, listed=2, source=continuous_finalize",
            "2026-09-12 11:00:01 [INFO] lsc.handlers: 精修候选终态入可靠队列: "
            f"room_id={ROOM}, recording_id=rec-x, round_key=round-000001, "
            "candidate_state_before=passed, audit_outcome=accepted, delivery_state=queued, "
            "listed_state_after=pending_delivery",
            "2026-09-12 11:00:01 [INFO] lsc.handlers: 精修候选终态入可靠队列: "
            f"room_id={ROOM}, recording_id=rec-x, round_key=round-000002, "
            "candidate_state_before=passed, audit_outcome=accepted, delivery_state=queued, "
            "listed_state_after=pending_delivery",
            "2026-09-12 11:00:02 [INFO] lsc.server: Sending WS response: "
            f"type=get_continuous_analysis_status_response, data={status!r}",
            "2026-09-12 11:00:03 [INFO] lsc.server: Sending WS response: "
            f"type=generate_jianying_draft_response, data={response!r}",
        ]) + chr(10),
        encoding="utf-8",
    )
    sidecar = tmp_path / "rec.finalization.json"
    sidecar.write_text(
        json.dumps({"phase": "completed", "accepted_candidates": listed,
                    "rejected_candidates": [], "pending_candidates": []}),
        encoding="utf-8",
    )
    proc = _run("--log", str(log), "--room-id", ROOM,
                "--finalization", str(sidecar), "--draft-dir", str(tmp_path))
    assert proc.returncode == 1, f"应当报红：{proc.stdout}\n{proc.stderr}"
    assert "no_finalized_clip_dropped" in proc.stdout
    assert "少写 1 条" in proc.stdout
    assert "skipped_have_reason_codes" in proc.stdout


def test_subset_export_is_not_flagged(tmp_path: Path) -> None:
    """只导出子集（2 条定稿里只导 1 条）不得误报"已定稿切片被丢"。

    回归：首版按「权威集合定稿数 > 写入数」判断，用户用「导出选中」时会假红；
    现已按请求内的 round_key 取交集。
    """
    def _round(key: str, start: float) -> dict:
        return {
            "round_key": key, "room_id": ROOM, "start": start, "end": start + 60.0,
            "source_profile": "broadcast", "broadcast_audit": "passed",
            "end_quality": "precise", "end_by": "broadcast_exclusion",
            "confirm_status": "vision_confirmed", "end_review_required": False,
        }

    listed = [_round("round-000001", 100.0), _round("round-000002", 200.0)]
    draft_dir = tmp_path / "LSC_SUBSET"
    draft_dir.mkdir()
    (draft_dir / "draft_content.json").write_text(
        json.dumps({"tracks": [
            {"type": "video", "segments": [{}]},   # 切片轨：只 1 段（用户只导了一条）
            {"type": "video", "segments": [{}]},   # 录制全片轨
        ]}, ensure_ascii=False),
        encoding="utf-8",
    )
    status = {"room_id": ROOM, "phase": "completed", "finalization_state": "completed",
              "pending_queue_depth": 0, "coverage_complete": True, "listed_clips": listed}
    request = {"room_ids": [ROOM], "main_room_id": ROOM, "request_id": "req-sub",
               "clips": [{"round_key": "round-000001"}]}
    response = {"success": True, "draft_name": "LSC_SUBSET", "draft_dir": str(draft_dir),
                "tracks": 2, "segments": 2, "requested_clip_count": 1,
                "included_clip_count": 1, "skipped_clip_count": 0,
                "warnings": [], "request_id": "req-sub"}
    log = tmp_path / "backend-stdout.log"
    log.write_text(chr(10).join([
        "2026-09-12 12:00:00 [INFO] lsc.server: Received WS message: "
        f"type=generate_jianying_draft, data={request!r}",
        "2026-09-12 12:00:01 [INFO] lsc.handlers: "
        f"终态权威快照已保留: room_id={ROOM}, listed=2, source=continuous_finalize",
        "2026-09-12 12:00:01 [INFO] lsc.handlers: 精修候选终态入可靠队列: "
        f"room_id={ROOM}, recording_id=rec-x, round_key=round-000001, "
        "candidate_state_before=passed, audit_outcome=accepted, delivery_state=queued, "
        "listed_state_after=pending_delivery",
        "2026-09-12 12:00:01 [INFO] lsc.handlers: 精修候选终态入可靠队列: "
        f"room_id={ROOM}, recording_id=rec-x, round_key=round-000002, "
        "candidate_state_before=passed, audit_outcome=accepted, delivery_state=queued, "
        "listed_state_after=pending_delivery",
        "2026-09-12 12:00:02 [INFO] lsc.server: Sending WS response: "
        f"type=get_continuous_analysis_status_response, data={status!r}",
        "2026-09-12 12:00:03 [INFO] lsc.server: Sending WS response: "
        f"type=generate_jianying_draft_response, data={response!r}",
    ]) + chr(10), encoding="utf-8")
    sidecar = tmp_path / "rec2.finalization.json"
    sidecar.write_text(
        json.dumps({"phase": "completed", "accepted_candidates": listed,
                    "rejected_candidates": [], "pending_candidates": []}),
        encoding="utf-8",
    )
    proc = _run("--log", str(log), "--room-id", ROOM,
                "--finalization", str(sidecar), "--draft-dir", str(draft_dir))
    assert proc.returncode == 0, f"子集导出不应报红：{proc.stdout}\n{proc.stderr}"
    assert "no_finalized_clip_dropped" in proc.stdout
    assert "请求内定稿 1 条" in proc.stdout
