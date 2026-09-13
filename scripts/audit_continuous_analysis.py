"""只读解析持续分析日志，生成吞吐/backlog/coverage 基线报告。

该工具不触碰录制文件、分析 JSON 或剪映草稿；默认只向 stdout 输出 JSON。
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

_KICK_RE = re.compile(
    r"^(?P<timestamp>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*?"
    r"持续分析 kick worker: room_id=(?P<room>[^,]+), dur=(?P<dur>[\d.]+)s, "
    r"range=(?P<start>[\d.]+)-(?P<end>[\d.]+),.*?"
    r"OCR=(?P<ocr>\w+),.*?full=(?P<full>\w+),.*?finalize=(?P<finalize>\w+)"
)
_COMPLETE_RE = re.compile(
    r"^(?P<timestamp>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*?"
    r"持续分析 Worker 完成: room_id=(?P<room>[^,]+), (?P<rounds>\d+) 回合"
)
_DELIVERY_RE = re.compile(
    r"精修候选终态入可靠队列: room_id=(?P<room>[^,]+), "
    r"recording_id=(?P<recording>[^,]+), round_key=(?P<round>[^,]+), "
    r"candidate_state_before=(?P<before>[^,]+), audit_outcome=(?P<outcome>[^,]+), "
    r"delivery_state=(?P<delivery>[^,]+), listed_state_after=(?P<listed>[^,]+)"
)
_DELIVERED_RE = re.compile(
    r"精修候选已由主循环消费: room_id=(?P<room>[^,]+), delivered=(?P<count>\d+), "
    r"delivery_state=(?P<delivery>[^,]+), listed_state_after=(?P<listed>[^,]+)"
)
_REJECTED_RE = re.compile(
    r"精修候选终态: room_id=(?P<room>[^,]+), recording_id=(?P<recording>[^,]+), "
    r"round_key=(?P<round>[^,]+), candidate_state_before=(?P<before>[^,]+), "
    r"audit_outcome=rejected, delivery_state=not_required, listed_state_after=not_listed"
)


def _timestamp_seconds(value: str) -> float:
    from datetime import datetime

    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").timestamp()


def _bool(value: str) -> bool:
    return value.lower() in {"1", "true", "yes", "on"}


def _log_field(line: str, name: str) -> str | None:
    match = re.search(rf"(?:^|,\s*){re.escape(name)}=([^,\s]+)", line)
    return match.group(1) if match else None


def _optional_float(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return float(value.rstrip("sS"))
    except ValueError:
        return None


def parse_log(lines: Iterable[str], room_id: str | None = None) -> list[dict[str, Any]]:
    """将 kick 与其后的同房 Worker 完成日志按顺序配对。"""
    kicks: list[dict[str, Any]] = []
    completions: list[dict[str, Any]] = []
    for line in lines:
        kick = _KICK_RE.search(line)
        if kick and (room_id is None or kick.group("room") == room_id):
            backlog = _log_field(line, "backlog")
            new_media = _log_field(line, "new_media")
            throughput_avg = _log_field(line, "throughput_avg")
            reason = _log_field(line, "reason")
            kicks.append(
                {
                    "room_id": kick.group("room"),
                    "kick_at": kick.group("timestamp"),
                    "_kick_epoch": _timestamp_seconds(kick.group("timestamp")),
                    "recorded_duration_at_kick": float(kick.group("dur")),
                    "range": [float(kick.group("start")), float(kick.group("end"))],
                    "ocr": _bool(kick.group("ocr")),
                    "full_rescan": _bool(kick.group("full")),
                    "finalize": _bool(kick.group("finalize")),
                    "scan_reason": reason,
                    "planned_new_media_sec": _optional_float(new_media),
                    "throughput_avg": _optional_float(throughput_avg),
                    "_logged_backlog": _optional_float(backlog),
                }
            )
            continue
        complete = _COMPLETE_RE.search(line)
        if complete and (room_id is None or complete.group("room") == room_id):
            completions.append(
                {
                    "room_id": complete.group("room"),
                    "complete_at": complete.group("timestamp"),
                    "_complete_epoch": _timestamp_seconds(complete.group("timestamp")),
                    "worker_rounds": int(complete.group("rounds")),
                }
            )

    result: list[dict[str, Any]] = []
    completion_index = 0
    for kick in kicks:
        matched = False
        while completion_index < len(completions):
            completion = completions[completion_index]
            completion_index += 1
            if completion["_complete_epoch"] < kick["_kick_epoch"]:
                continue
            wall_sec = max(0.0, completion["_complete_epoch"] - kick["_kick_epoch"])
            media_start, media_end = kick["range"]
            media_sec = max(0.0, media_end - media_start)
            item = {
                key: value
                for key, value in {**kick, **completion}.items()
                if not key.startswith("_")
            }
            item["wall_sec"] = round(wall_sec, 3)
            item["media_sec"] = round(media_sec, 3)
            item["throughput"] = round(media_sec / wall_sec, 3) if wall_sec else 0.0
            item["backlog_at_kick"] = round(
                max(
                    0.0,
                    kick["_logged_backlog"]
                    if kick.get("_logged_backlog") is not None
                    else kick["recorded_duration_at_kick"] - media_end,
                ),
                3,
            )
            result.append(item)
            matched = True
            break
        if not matched:
            media_start, media_end = kick["range"]
            media_sec = max(0.0, media_end - media_start)
            item = {
                key: value
                for key, value in kick.items()
                if not key.startswith("_")
            }
            item["media_sec"] = round(media_sec, 3)
            item["backlog_at_kick"] = round(
                max(
                    0.0,
                    kick["_logged_backlog"]
                    if kick.get("_logged_backlog") is not None
                    else kick["recorded_duration_at_kick"] - media_end,
                ),
                3,
            )
            result.append(item)
    return result


def parse_delivery_events(
    lines: Iterable[str], room_id: str | None = None
) -> list[dict[str, Any]]:
    """Parse the per-candidate durable-queue audit trail."""
    events: list[dict[str, Any]] = []
    for line in lines:
        queued = _DELIVERY_RE.search(line)
        if queued and (room_id is None or queued.group("room") == room_id):
            events.append({
                "room_id": queued.group("room"),
                "recording_id": queued.group("recording"),
                "round_key": queued.group("round"),
                "candidate_state_before": queued.group("before"),
                "audit_outcome": queued.group("outcome"),
                "delivery_state": queued.group("delivery"),
                "listed_state_after": queued.group("listed"),
            })
            continue
        delivered = _DELIVERED_RE.search(line)
        if delivered and (room_id is None or delivered.group("room") == room_id):
            events.append({
                "room_id": delivered.group("room"),
                "delivered_count": int(delivered.group("count")),
                "delivery_state": delivered.group("delivery"),
                "listed_state_after": delivered.group("listed"),
            })
            continue
        rejected = _REJECTED_RE.search(line)
        if rejected and (room_id is None or rejected.group("room") == room_id):
            events.append({
                "room_id": rejected.group("room"),
                "recording_id": rejected.group("recording"),
                "round_key": rejected.group("round"),
                "candidate_state_before": rejected.group("before"),
                "audit_outcome": "rejected",
                "delivery_state": "not_required",
                "listed_state_after": "not_listed",
            })
    return events


def parse_status_snapshots(
    lines: Iterable[str], room_id: str | None = None
) -> list[dict[str, Any]]:
    """Extract compact continuous-analysis status snapshots from WS logs."""
    snapshots: list[dict[str, Any]] = []
    for line in lines:
        if "Sending WS response: type=get_continuous_analysis_status_response" not in line:
            continue
        payload_text = line.split(" data=", 1)[1] if " data=" in line else ""
        try:
            payload = ast.literal_eval(payload_text) if payload_text else None
        except (SyntaxError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        if room_id is not None and str(payload.get("room_id") or "") != room_id:
            continue
        keys = (
            "room_id", "running", "phase", "status", "analysis_stage",
            "analyzed_duration", "recorded_duration", "analysis_lag_sec",
            "analysis_lag_p90_sec", "analysis_lag_max_sec", "confirmed_rounds",
            "pending_rounds", "total_highlights", "listed_clip_count",
            "audit_queue_depth", "audit_accepted_count", "audit_rejected_count",
            "audit_manual_review_count", "audit_terminal_total",
            "audit_delivered_total", "audit_delivery_gap", "pending_queue_depth",
            "refine_result_queue_depth", "finalization_pending_jobs",
            "finalization_state", "coverage_ranges", "coverage_uncovered_ranges",
            "coverage_complete", "scan_throughput", "net_coverage_throughput",
            "provider", "valorant_profile", "updated_at",
        )
        snapshot = {key: payload[key] for key in keys if key in payload}
        snapshots.append(snapshot)
    return snapshots


def parse_audit_conclusions(lines: Iterable[str]) -> list[dict[str, Any]]:
    """解析「赛事回合审计完成」行：每条 = 一个回合的审计结论。

    现场（2026-09-11 20:45）：日志里 8 条结论，durable 终态只有 4 条 ——
    no-silent-drop 不变量①就是拿这两个数对账。
    """
    marker = "赛事回合审计完成:"
    out: list[dict[str, Any]] = []
    for line in lines:
        if marker not in line:
            continue
        timestamp = ""
        m = re.match(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", line)
        if m:
            timestamp = m.group(1)
        body = line.split(marker, 1)[1].strip()
        fields = {key: value for key, value in re.findall(r"(\w+)=([^,\s]+)", body)}
        out.append({
            "timestamp": timestamp,
            "span": body.split(",", 1)[0],
            "audit": fields.get("audit"),
            "status": fields.get("status"),
            "end_by": fields.get("end_by"),
            "reason": fields.get("reason"),
        })
    return out


def parse_draft_responses(lines: Iterable[str]) -> list[dict[str, Any]]:
    """解析 `generate_jianying_draft_response` 行：requested / included / skipped 口径与明细有无。"""
    marker = "generate_jianying_draft_response"
    out: list[dict[str, Any]] = []
    for line in lines:
        if marker not in line:
            continue
        entry: dict[str, Any] = {"timestamp": ""}
        m = re.match(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", line)
        if m:
            entry["timestamp"] = m.group(1)
        for key in ("requested_clip_count", "included_clip_count", "skipped_clip_count"):
            mm = re.search(rf"'{key}': (\d+)", line)
            if mm:
                entry[key] = int(mm.group(1))
        entry["has_skipped_details"] = "'skipped': [" in line
        out.append(entry)
    return out


def build_invariants(
    *,
    audit_conclusions: list[dict[str, Any]],
    finalization: dict[str, Any] | None,
    analysis: dict[str, Any] | None,
    draft_responses: list[dict[str, Any]],
) -> dict[str, Any]:
    """no-silent-drop 三条不变量（计划 §〇 夹具 C）。

    ① 每条审计结论都必须有 durable 终态（收尾 sidecar 的 accepted/rejected/pending）；
    ② 分析快照里的每个回合都必须有终态归属；
    ③ 草稿 requested == included + skipped，且有跳过时必须给逐条明细。
    """
    checks: list[dict[str, Any]] = []
    terminal_keys: set[str] = set()
    if isinstance(finalization, dict):
        for bucket in ("accepted_candidates", "rejected_candidates", "pending_candidates"):
            for item in finalization.get(bucket) or []:
                if isinstance(item, dict) and item.get("round_key"):
                    terminal_keys.add(str(item["round_key"]))

    checks.append({
        "name": "audit_conclusions_have_durable_terminals",
        "passed": (not audit_conclusions) or len(terminal_keys) >= len(audit_conclusions),
        "detail": (
            f"审计结论 {len(audit_conclusions)} 条 vs 收尾 sidecar 终态 {len(terminal_keys)} 条"
            if audit_conclusions else "无审计结论可对账（日志未提供）"
        ),
    })

    unterminal: list[str] = []
    if isinstance(analysis, dict):
        for item in analysis.get("highlights") or []:
            if not isinstance(item, dict):
                continue
            round_key = str(item.get("round_key") or "")
            if round_key and round_key not in terminal_keys:
                unterminal.append(round_key)
    checks.append({
        "name": "listed_clips_have_terminal_attribution",
        "passed": not unterminal,
        "detail": (
            "分析快照里无终态归属的回合: " + ", ".join(unterminal)
            if unterminal else "全部分析候选都有终态归属（或未提供 sidecar）"
        ),
    })

    draft_failures: list[str] = []
    for response in draft_responses:
        requested = response.get("requested_clip_count")
        included = response.get("included_clip_count")
        skipped = response.get("skipped_clip_count")
        if None in (requested, included, skipped):
            continue
        if included + skipped != requested:
            draft_failures.append(
                f"{response.get('timestamp')}: included+skipped={included + skipped} != requested={requested}"
            )
        if skipped and not response.get("has_skipped_details"):
            draft_failures.append(
                f"{response.get('timestamp')}: {skipped} 条跳过但响应无逐条明细（只有聚合告警）"
            )
    checks.append({
        "name": "draft_skips_are_accountable",
        "passed": not draft_failures,
        "detail": "; ".join(draft_failures) if draft_failures else "草稿口径一致且跳过可辨",
    })

    return {
        "passed": all(item["passed"] for item in checks),
        "checks": checks,
        "audit_conclusion_count": len(audit_conclusions),
        "durable_terminal_count": len(terminal_keys),
        "unterminal_round_keys": unterminal,
    }


def _load_json(path: str | Path | None) -> dict[str, Any] | None:
    if not path:
        return None
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _probe_duration(video_path: str | Path, ffprobe: str) -> float | None:
    try:
        completed = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(video_path),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=30,
        )
        if completed.returncode != 0:
            return None
        return float(completed.stdout.strip())
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def _load_draft_summary(path: str | Path | None) -> dict[str, Any] | None:
    """Summarize Jianying draft video ranges without modifying the draft."""
    draft = _load_json(path)
    if draft is None:
        return None
    video_tracks = [
        track for track in (draft.get("tracks") or [])
        if isinstance(track, dict) and track.get("type") == "video"
    ]
    all_segments: list[dict[str, Any]] = []
    for track in video_tracks:
        for segment in track.get("segments") or []:
            if not isinstance(segment, dict):
                continue
            timerange = segment.get("source_timerange") or segment.get("target_timerange")
            if not isinstance(timerange, dict):
                continue
            try:
                start = float(timerange.get("start", 0.0)) / 1_000_000.0
                duration = float(timerange.get("duration", 0.0)) / 1_000_000.0
            except (TypeError, ValueError):
                continue
            if duration > 0.0:
                all_segments.append({"start": round(start, 3), "end": round(start + duration, 3)})
    full_duration = max((item["end"] for item in all_segments), default=0.0)
    # The draft's first full-length video segment is the source track; the
    # shorter ranges on the highlight track are the exported/listed clips.
    # Keep this explicit instead of depending on track ordering: a draft may
    # add a text or overlay track before the source video track.
    clip_segments = [
        item for item in all_segments
        if item["end"] - item["start"] < full_duration - 1.0
    ]
    return {
        "path": str(Path(path).resolve()) if path else None,
        "draft_name": draft.get("name"),
        "duration_sec": round(float(draft.get("duration", 0.0) or 0.0) / 1_000_000.0, 3),
        "video_track_count": len(video_tracks),
        "video_segment_count": len(all_segments),
        "clip_segment_count": len(clip_segments),
        "clip_ranges_sec": clip_segments,
    }


def _recording_summary(
    path: str | Path | None,
    *,
    duration_sec: float | None = None,
) -> dict[str, Any] | None:
    if not path:
        return None
    file_path = Path(path)
    try:
        stat = file_path.stat()
    except OSError:
        return {"path": str(file_path), "exists": False}
    try:
        with file_path.open("rb") as stream:
            header = stream.read(32)
    except OSError:
        header = b""
    return {
        "path": str(file_path.resolve()),
        "exists": True,
        "size_bytes": stat.st_size,
        "mtime": stat.st_mtime,
        "duration_sec": duration_sec,
        "container_header_hex": header.hex(),
    }


def build_report(
    scans: list[dict[str, Any]],
    *,
    analysis: dict[str, Any] | None = None,
    video_duration: float | None = None,
    delivery_events: list[dict[str, Any]] | None = None,
    status_snapshots: list[dict[str, Any]] | None = None,
    draft_path: str | Path | None = None,
    recording_path: str | Path | None = None,
) -> dict[str, Any]:
    normalized_scans: list[dict[str, Any]] = []
    for raw in scans:
        item = dict(raw)
        if "backlog_at_kick" not in item:
            try:
                item["backlog_at_kick"] = round(
                    max(0.0, float(item.get("recorded_duration_at_kick", 0.0))
                    - float(item.get("range", [0.0, 0.0])[1])),
                    3,
                )
            except (TypeError, ValueError, IndexError):
                item["backlog_at_kick"] = 0.0
        normalized_scans.append(item)
    throughputs = [
        float(item["throughput"])
        for item in normalized_scans
        if item.get("throughput", 0) > 0
    ]
    summary: dict[str, Any] = {
        "scan_count": len(normalized_scans),
        "completed_scan_count": len(normalized_scans),
        "average_throughput": round(sum(throughputs) / len(throughputs), 3) if throughputs else 0.0,
        "minimum_throughput": round(min(throughputs), 3) if throughputs else 0.0,
        "maximum_backlog_at_kick": round(
            max((float(item.get("backlog_at_kick", 0.0)) for item in normalized_scans), default=0.0),
            3,
        ),
        "analysis_highlights": (
            len(analysis.get("highlights") or []) if analysis is not None else None
        ),
        "video_duration": video_duration,
        "audit_terminal_total": sum(
            1 for item in (delivery_events or [])
            if item.get("audit_outcome") in {"accepted", "manual_review", "rejected"}
        ),
        "audit_delivered_total": sum(
            int(item.get("delivered_count", 0) or 0)
            for item in (delivery_events or [])
            if item.get("delivery_state") == "delivered"
        ),
    }
    summary["audit_delivery_gap"] = max(
        0,
        summary["audit_terminal_total"] - summary["audit_delivered_total"],
    )
    if status_snapshots:
        final_status = status_snapshots[-1]
        summary.update({
            "status_snapshot_count": len(status_snapshots),
            "final_status": final_status,
            "max_observed_lag_sec": max(
                float(item.get("analysis_lag_sec", 0.0) or 0.0)
                for item in status_snapshots
            ),
            "max_observed_lag_p90_sec": max(
                float(item.get("analysis_lag_p90_sec", 0.0) or 0.0)
                for item in status_snapshots
            ),
            "final_audit_terminal_total": final_status.get("audit_terminal_total"),
            "final_audit_accepted_count": final_status.get("audit_accepted_count"),
            "final_audit_delivered_total": final_status.get("audit_delivered_total"),
            "final_audit_delivery_gap": final_status.get("audit_delivery_gap"),
            "final_pending_queue_depth": final_status.get("pending_queue_depth"),
            "final_refine_result_queue_depth": final_status.get("refine_result_queue_depth"),
            "finalization_state": final_status.get("finalization_state"),
            "max_observed_audit_delivery_gap": max(
                int(item.get("audit_delivery_gap", 0) or 0)
                for item in status_snapshots
            ),
            "observed_audit_delivery_gap_nonzero": any(
                int(item.get("audit_delivery_gap", 0) or 0) > 0
                for item in status_snapshots
            ),
        })
        for summary_key, status_key in (
            ("audit_terminal_total", "audit_terminal_total"),
            ("audit_delivered_total", "audit_delivered_total"),
            ("audit_delivery_gap", "audit_delivery_gap"),
        ):
            if final_status.get(status_key) is not None:
                summary[summary_key] = final_status[status_key]
    coverage_ranges = []
    listed_count = None
    if isinstance(analysis, dict):
        coverage_ranges = analysis.get("coverage_ranges") or []
        listed_count = analysis.get("listed_clip_count")
        if listed_count is None:
            listed_count = len(analysis.get("listed_clips") or [])
    draft_summary = _load_draft_summary(draft_path)
    recording_summary = _recording_summary(
        recording_path,
        duration_sec=video_duration,
    )
    reconciliation: dict[str, Any] | None = None
    if draft_summary is not None and isinstance(analysis, dict):
        candidates = list(analysis.get("highlights") or [])
        clips = list(draft_summary.get("clip_ranges_sec") or [])
        matched_keys: set[str] = set()
        clip_matches: list[dict[str, Any]] = []
        for clip in clips:
            best = None
            best_error = float("inf")
            for candidate in candidates:
                key = str(candidate.get("round_key") or "")
                if key in matched_keys:
                    continue
                error = abs(float(clip["start"]) - float(candidate.get("start", 0.0))) + abs(
                    float(clip["end"]) - float(candidate.get("end", 0.0))
                )
                if error < best_error:
                    best = candidate
                    best_error = error
            if best is not None and best_error <= 2.0:
                key = str(best.get("round_key") or "")
                matched_keys.add(key)
                clip_matches.append({
                    "round_key": key,
                    "clip_start": clip["start"],
                    "clip_end": clip["end"],
                    "candidate_start": best.get("start"),
                    "candidate_end": best.get("end"),
                    "confirm_status": best.get("confirm_status"),
                    "boundary_quality": best.get("boundary_quality"),
                })
            else:
                clip_matches.append({"round_key": None, "clip": clip})
        reconciliation = {
            "analysis_candidate_count": len(candidates),
            "draft_clip_count": len(clips),
            "matched_count": len(matched_keys),
            "analysis_candidates_missing_from_draft": [
                str(item.get("round_key") or "")
                for item in candidates
                if str(item.get("round_key") or "") not in matched_keys
            ],
            "draft_clips_without_candidate": [
                item for item in clip_matches if item.get("round_key") is None
            ],
            "matches": [item for item in clip_matches if item.get("round_key") is not None],
        }
    return {
        "summary": summary,
        "scans": normalized_scans,
        "analysis": analysis if analysis is not None else None,
        "delivery_events": delivery_events or [],
        "status_snapshots": status_snapshots or [],
        "coverage_ledger": {
            "ranges": coverage_ranges,
            "listed_clip_count": listed_count,
            "candidate_transition_count": len(delivery_events or []),
            "audit_delivery_gap": summary["audit_delivery_gap"],
        },
        "draft": draft_summary,
        "recording": recording_summary,
        "clip_reconciliation": reconciliation,
    }


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--log",
        action="append",
        required=True,
        help="backend.log 路径；可重复传入以按时间顺序合并轮转日志",
    )
    parser.add_argument("--room-id", help="只分析指定 room_id")
    parser.add_argument("--analysis", help="分析 JSON 路径（只读）")
    parser.add_argument(
        "--finalization",
        help="收尾 sidecar（*.finalization.json）路径（只读）：no-silent-drop 不变量对账用",
    )
    parser.add_argument("--video", help="录像路径，仅用于读取 ffprobe 时长")
    parser.add_argument("--ffprobe", default="ffprobe", help="ffprobe 可执行文件")
    parser.add_argument("--draft", help="剪映 draft_content.json 路径，用于切片对账")
    parser.add_argument("--recording", help="录制文件路径，用于文件存在性/大小/头部核验")
    parser.add_argument(
        "--recording-duration",
        type=float,
        default=None,
        help="已知录制时长（秒）；适用于当前环境不能直接运行 Windows ffprobe 的情况",
    )
    parser.add_argument("--output", help="新报告 JSON 路径；不指定则输出 stdout")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    lines: list[str] = []
    for log_path in args.log:
        try:
            lines.extend(
                Path(log_path).read_text(
                    encoding="utf-8", errors="replace"
                ).splitlines()
            )
        except OSError as exc:
            print(f"读取日志失败: {log_path}: {exc}", file=sys.stderr)
            return 2
    scans = parse_log(lines, room_id=args.room_id)
    delivery_events = parse_delivery_events(lines, room_id=args.room_id)
    status_snapshots = parse_status_snapshots(lines, room_id=args.room_id)
    audit_conclusions = parse_audit_conclusions(lines)
    draft_responses = parse_draft_responses(lines)
    analysis = _load_json(args.analysis)
    finalization = _load_json(args.finalization)
    video_duration = (
        args.recording_duration
        if args.recording_duration is not None
        else (_probe_duration(args.video, args.ffprobe) if args.video else None)
    )
    report = build_report(
        scans,
        analysis=analysis,
        video_duration=video_duration,
        delivery_events=delivery_events,
        status_snapshots=status_snapshots,
        draft_path=args.draft,
        recording_path=args.recording,
    )
    report["audit_conclusions"] = audit_conclusions
    report["draft_responses"] = draft_responses
    report["invariants"] = build_invariants(
        audit_conclusions=audit_conclusions,
        finalization=finalization,
        analysis=analysis,
        draft_responses=draft_responses,
    )
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        output = Path(args.output)
        tmp = output.with_suffix(output.suffix + ".tmp")
        tmp.write_text(payload + "\n", encoding="utf-8")
        tmp.replace(output)
    else:
        print(payload)
    # no-silent-drop 不变量失败 = 报告红：脚本给非零退出码，便于 L2 门禁直接失败。
    if not report["invariants"]["passed"]:
        failed = [c["name"] for c in report["invariants"]["checks"] if not c["passed"]]
        print(f"不变量未通过: {', '.join(failed)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
