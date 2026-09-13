"""L3 实测验收：一次真实会话之后，一条命令判「列表↔草稿↔权威账本」是否自洽（只读）。

把计划 §五 的 L3 清单从"人眼读日志"变成可判定的检查：

1. ``draft_counts_consistent``             requested == included + skipped
2. ``skipped_have_reason_codes``           有跳过就必须给逐条 reason_code（v1.0.15 起）
3. ``no_finalized_clip_dropped``           **权威集合里"出点已定稿"的切片一条都不许丢**（事故 105 的判据）
4. ``draft_segments_match_included``       草稿明文备份的切片轨段数 == included_clip_count
5. ``finalization_completed``              收尾 sidecar phase==completed
6. ``all_listed_have_terminal``            导出时每条已入列切片都有终态归属（C6 契约；事故里 71/76/105/123 缺）
7. ``authority_snapshot_preserved``        C1：存在「终态权威快照已保留」日志；且快照内切片不得被报 NOT_IN_AUTHORITY

用法（日志可重复传，按时间顺序合并轮转文件）：

    python scripts/verify_live_session.py \
      --log "%APPDATA%/lsc-electron/logs/backend-stdout.log.1" \
      --log "%APPDATA%/lsc-electron/logs/backend-stdout.log" \
      --room-id <room_id> \
      --finalization "<录像目录>/<stem>.finalization.json" \
      --draft-dir "D:/迅雷云盘/JianyingPro Drafts/LSC_xxx" \
      --out docs/reports/verify-live-session-<date>.json

自查（合成"全绿"场景，验证本工具不是只会报红）：

    python scripts/verify_live_session.py --self-test

退出码：0 全部通过 / 1 有不通过 / 2 用法或输入错误。
在**修复前**的归档日志上跑，本工具应当报红（本次事故归档：5 项红）——它同时充当改前基线。

时序要求：**导出后尽快跑**。草稿目录可能被清理、`draft_content.json` 被剪映加密；
两者都会让第 4 项变成"无法验证"（按 FAIL 处理，不会假绿）。
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

_VALID_END_BY = frozenset({"next_prep", "broadcast_exclusion"})
_TERMINAL_LOG_MARKERS = (
    "精修候选终态入可靠队列",
    "精修候选终态:",
    "扫描通路终态已补投影",
    "仍未定稿的已入列切片落 manual_review",
    "赛事回合区间跨回合拒绝",
    "同一回合去重（L2）",
)
_ROUND_KEY_RE = re.compile(r"round-\d+(?:-s\d+)*")


def _iter_payloads(lines: list[str], needle: str) -> list[tuple[str, dict[str, Any]]]:
    """按出现顺序抽取日志行里的 ``data=<python repr>`` 载荷。"""
    out: list[tuple[str, dict[str, Any]]] = []
    for line in lines:
        if needle not in line:
            continue
        index = line.find(" data=")
        if index < 0:
            continue
        try:
            payload = ast.literal_eval(line[index + 6:].strip())
        except (SyntaxError, ValueError):
            continue
        if isinstance(payload, dict):
            out.append((line[:19], payload))
    return out


def _load_lines(paths: list[str]) -> list[str]:
    lines: list[str] = []
    for path in paths:
        lines.extend(Path(path).read_text(encoding="utf-8", errors="replace").splitlines())
    return lines


def _is_finalized_broadcast(clip: dict[str, Any]) -> bool:
    """与门禁同一判据：出点已定稿的赛事切片。"""
    if not isinstance(clip, dict):
        return False
    if str(clip.get("source_profile") or "").strip().lower() != "broadcast":
        return False
    if str(clip.get("confirm_status") or "").strip().lower() in ("rejected", "refining"):
        return False
    if str(clip.get("broadcast_audit") or "").strip().lower() != "passed":
        return False
    if str(clip.get("end_quality") or "").strip().lower() != "precise":
        return False
    if bool(clip.get("end_review_required")) or bool(clip.get("duration_anomaly")):
        return False
    return str(clip.get("end_by") or "") in _VALID_END_BY


def _round_key(clip: dict[str, Any]) -> str:
    return str(clip.get("round_key") or "")


def _authority_snapshot(lines: list[str], export_ts: str, room: str) -> dict[str, dict[str, Any]]:
    """导出前最后一次带 listed_clips 的状态里的权威切片集合。"""
    authority: dict[str, dict[str, Any]] = {}
    for ts, payload in _iter_payloads(lines, "get_continuous_analysis_status_response"):
        if room and room not in json.dumps(payload, ensure_ascii=False):
            continue
        if ts > export_ts:
            break
        listed = payload.get("listed_clips")
        entries = listed.values() if isinstance(listed, dict) else (listed or [])
        snapshot = {
            _round_key(item): item
            for item in entries
            if isinstance(item, dict) and _round_key(item)
        }
        if snapshot:
            authority = snapshot
    return authority


def _terminal_keys(lines: list[str], finalization: dict[str, Any] | None) -> set[str]:
    """有终态归属的 round_key：sidecar 三桶 + 日志里落终态的行。"""
    keys: set[str] = set()
    if isinstance(finalization, dict):
        for bucket in ("accepted_candidates", "rejected_candidates", "pending_candidates"):
            for item in finalization.get(bucket) or []:
                if isinstance(item, dict) and _round_key(item):
                    keys.add(_round_key(item))
    for line in lines:
        if any(marker in line for marker in _TERMINAL_LOG_MARKERS):
            keys.update(_ROUND_KEY_RE.findall(line))
    return keys


def _draft_clip_segments(draft_dir: Path) -> tuple[int | None, str]:
    """草稿切片轨段数：优先读明文备份（剪映打开后 draft_content.json 会加密）。"""
    if not draft_dir.is_dir():
        return None, f"草稿目录不存在（可能已被清理/移走）: {draft_dir}"
    candidates: list[Path] = []
    backup_dir = draft_dir / ".backup"
    if backup_dir.is_dir():
        candidates.extend(sorted(backup_dir.glob("*.load.bak")))
    candidates.append(draft_dir / "draft_content.json")
    for path in candidates:
        if not path.is_file():
            continue
        raw = path.read_bytes()
        if not raw.lstrip().startswith(b"{"):
            continue  # 加密内容
        try:
            content = json.loads(raw.decode("utf-8-sig"))
        except (UnicodeDecodeError, ValueError):
            continue
        segs = [
            len(track.get("segments") or [])
            for track in (content.get("tracks") or [])
            if track.get("type") == "video"
        ]
        if not segs:
            return 0, f"{path.name}: 无视频轨"
        return max(segs), f"{path.name}: 视频轨段数={segs}（取最大为切片轨）"
    return None, (
        f"无明文草稿内容（{draft_dir} 内 .backup/*.load.bak 与 draft_content.json "
        "均已加密或缺失；请在剪映打开前跑本校验）"
    )


def _evaluate(
    lines: list[str],
    *,
    room: str,
    finalization: dict[str, Any] | None,
    draft_dir: str,
) -> dict[str, Any] | None:
    responses = _iter_payloads(lines, "type=generate_jianying_draft_response")
    if not responses:
        return None
    # 响应载荷里没有 room_id，因此按 request_id 与请求关联（请求里才有 room_ids/clips）
    wanted_ids: list[str] = []
    requests_matched: list[tuple[str, dict[str, Any]]] = []
    for ts, payload in _iter_payloads(lines, "type=generate_jianying_draft,"):
        if room and room not in json.dumps(payload, ensure_ascii=False):
            continue
        requests_matched.append((ts, payload))
        request_id = str(payload.get("request_id") or "")
        if request_id:
            wanted_ids.append(request_id)
    picked = [
        (ts, payload)
        for ts, payload in responses
        if not wanted_ids or str(payload.get("request_id") or "") in wanted_ids
    ]
    if not picked:
        return None
    export_ts, export = picked[-1]
    # 只导出子集时（"导出选中"/单条导出）不能拿"权威集合定稿数"当分母：取请求内的 round_key 交集
    export_request_id = str(export.get("request_id") or "")
    request_payload = next(
        (payload for _ts, payload in reversed(requests_matched)
         if export_request_id and str(payload.get("request_id") or "") == export_request_id),
        next((payload for ts, payload in reversed(requests_matched) if ts <= export_ts), {}),
    )
    requested_keys = {
        str(clip.get("round_key") or "")
        for clip in (request_payload.get("clips") or [])
        if isinstance(clip, dict)
    } - {""}

    authority = _authority_snapshot(lines, export_ts, room)
    if isinstance(finalization, dict):
        for bucket in ("accepted_candidates", "rejected_candidates", "pending_candidates"):
            for item in finalization.get(bucket) or []:
                if isinstance(item, dict) and _round_key(item):
                    authority.setdefault(_round_key(item), item)
    exportable = {key: item for key, item in authority.items() if _is_finalized_broadcast(item)}
    terminal = _terminal_keys(lines, finalization)

    requested = int(export.get("requested_clip_count") or 0)
    included = int(export.get("included_clip_count") or 0)
    skipped_count = int(export.get("skipped_clip_count") or 0)
    skipped_entries = export.get("skipped") if isinstance(export.get("skipped"), list) else []
    skipped_keys = {str(entry.get("round_key") or "") for entry in skipped_entries}

    checks: list[dict[str, Any]] = []

    def _check(name: str, passed: bool, detail: str) -> None:
        checks.append({"name": name, "passed": bool(passed), "detail": detail})

    _check(
        "draft_counts_consistent",
        requested == included + skipped_count,
        f"requested={requested}, included={included}, skipped={skipped_count}",
    )
    missing_reasons = [
        entry
        for entry in skipped_entries
        if not str(entry.get("reason_code") or entry.get("reason") or "").strip()
    ]
    _check(
        "skipped_have_reason_codes",
        (skipped_count == 0) or (len(skipped_entries) == skipped_count and not missing_reasons),
        "无跳过" if skipped_count == 0 else (
            f"跳过 {skipped_count} 条, 逐条明细 {len(skipped_entries)} 条, "
            f"缺原因码 {len(missing_reasons)} 条"
            + ("" if skipped_entries else "（响应只有聚合告警，无法定位到具体切片）")
        ),
    )
    scoped = {
        key: item for key, item in exportable.items()
        if not requested_keys or key in requested_keys
    }
    lost = max(0, len(scoped) - included)
    suspects = sorted(key for key in scoped if key not in skipped_keys)
    scope_note = (
        f"（请求内 {len(requested_keys)} 条" + ("" if requested_keys else "；请求无 clips 明细，按权威集合全量")
        + "）" if requested_keys or exportable else ""
    )
    detail = (
        f"请求内定稿 {len(scoped)} 条 {sorted(scoped)}, 实际写入 {included} 条{scope_note}"
        + (f"; 权威集合定稿 {len(exportable)} 条" if len(exportable) != len(scoped) else "")
    )
    if lost:
        detail += f"; 少写 {lost} 条"
        detail += (
            f"，疑似被丢: {suspects}"
            if skipped_entries
            else "（老响应无逐条明细，无法定位到具体切片——升级后应带 skipped 明细）"
        )
    _check("no_finalized_clip_dropped", lost == 0, detail)

    segments, how = (None, "未提供 --draft-dir")
    if draft_dir:
        segments, how = _draft_clip_segments(Path(draft_dir))
    _check(
        "draft_segments_match_included",
        segments is not None and segments == included,
        f"草稿切片轨段数={segments}, included_clip_count={included}（{how}）",
    )
    phase = str((finalization or {}).get("phase") or "")
    _check(
        "finalization_completed",
        phase == "completed",
        f"收尾 sidecar phase={phase or '（未提供/无 sidecar）'}"
        + (
            f", final_round_count={finalization.get('final_round_count')}"
            if isinstance(finalization, dict)
            else ""
        ),
    )
    missing_terminal = sorted(key for key in authority if key not in terminal)
    _check(
        "all_listed_have_terminal",
        not missing_terminal,
        f"已入列 {len(authority)} 条, 有终态归属 {len(terminal)} 条"
        + (f"; 缺终态: {missing_terminal}" if missing_terminal else ""),
    )
    snapshot_lines = [
        line[:19] for line in lines if "终态权威快照已保留" in line and (not room or room in line)
    ]
    not_in_authority = [
        entry
        for entry in skipped_entries
        if str(entry.get("reason_code") or "").strip().upper() == "NOT_IN_AUTHORITY"
    ]
    _check(
        "authority_snapshot_preserved",
        bool(snapshot_lines) and not not_in_authority,
        f"快照日志 {len(snapshot_lines)} 条"
        + (
            f"（最后一次 {snapshot_lines[-1]}）"
            if snapshot_lines
            else "（C1 未生效：任务态 pop 后权威不可达）"
        )
        + (f"; 仍报 NOT_IN_AUTHORITY {len(not_in_authority)} 条" if not_in_authority else ""),
    )

    return {
        "export_at": export_ts,
        "draft_name": export.get("draft_name"),
        "draft_dir": export.get("draft_dir"),
        "requested": requested,
        "included": included,
        "skipped": skipped_count,
        "exportable_authority": sorted(exportable),
        "skipped_entries": skipped_entries,
        "checks": checks,
        "passed": all(check["passed"] for check in checks),
    }


def _self_test() -> int:
    """合成一次"全绿"会话（3 条候选 / 2 条入草稿 / 1 条带原因码跳过），验证校验器能变绿。"""
    root = Path(tempfile.mkdtemp(prefix="l3-selftest-"))
    try:
        return _self_test_impl(root)
    finally:
        # 默认清理，避免每次（含 CI 每次跑测试）都留一个临时目录；
        # 调试时设 L3_SELFTEST_KEEP=1 保留现场。
        if not os.environ.get("L3_SELFTEST_KEEP"):
            shutil.rmtree(root, ignore_errors=True)


def _self_test_impl(root: Path) -> int:
    room = "room-x"

    def _round(key: str, start: float, end: float, *, finalized: bool) -> dict[str, Any]:
        if finalized:
            return {
                "round_key": key, "room_id": room, "start": start, "end": end,
                "source_profile": "broadcast", "broadcast_audit": "passed",
                "end_quality": "precise", "end_by": "broadcast_exclusion",
                "confirm_status": "vision_confirmed", "end_review_required": False,
            }
        return {
            "round_key": key, "room_id": room, "start": start, "end": end,
            "source_profile": "broadcast", "broadcast_audit": "pending_lookahead",
            "end_by": "next_prep", "confirm_status": "pending",
        }

    listed = [
        _round("round-000001", 100.0, 160.0, finalized=True),
        _round("round-000002", 200.0, 260.0, finalized=False),
        _round("round-000003", 300.0, 360.0, finalized=True),
    ]
    sidecar = {
        "phase": "completed", "final_round_count": 3, "candidate_count": 3,
        "accepted_candidates": [listed[0], listed[2]],
        "rejected_candidates": [listed[1]],
        "pending_candidates": [],
    }
    draft_dir = root / "LSC_SIM_1"
    draft_dir.mkdir()
    (draft_dir / "draft_content.json").write_text(
        json.dumps({
            "tracks": [
                {"type": "video", "segments": [{}, {}]},   # 切片轨：2 段
                {"type": "video", "segments": [{}]},       # 录制全片轨：1 段
                {"type": "text", "segments": [{}, {}]},
            ]
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    status = {
        "room_id": room, "phase": "completed", "finalization_state": "completed",
        "pending_queue_depth": 0, "coverage_complete": True, "listed_clips": listed,
    }
    response = {
        "success": True, "draft_name": "LSC_SIM_1", "draft_dir": str(draft_dir),
        "tracks": 3, "segments": 5,
        "requested_clip_count": 3, "included_clip_count": 2, "skipped_clip_count": 1,
        "skipped": [
            {"round_key": "round-000002", "reason_code": "NEVER_AUDITED", "reason": "从未审计"}
        ],
        "warnings": [], "error": "", "error_code": "",
        "request_id": "req-sim-1",
    }
    request = {
        "room_ids": [room], "main_room_id": room, "include_pending": False,
        "request_id": "req-sim-1",
    }
    lines = [
        "2026-09-12 10:00:00 [INFO] lsc.server: Received WS message: "
        f"type=generate_jianying_draft, data={request!r}",
        "2026-09-12 10:00:01 [INFO] lsc.handlers: "
        f"终态权威快照已保留: room_id={room}, listed=3, source=continuous_finalize",
        "2026-09-12 10:00:01 [INFO] lsc.handlers: 精修候选终态入可靠队列: "
        f"room_id={room}, recording_id=rec-x, round_key=round-000001, "
        "candidate_state_before=passed, audit_outcome=accepted, "
        "delivery_state=queued, listed_state_after=pending_delivery",
        "2026-09-12 10:00:01 [INFO] lsc.handlers: 精修候选终态: "
        f"room_id={room}, recording_id=rec-x, round_key=round-000002, "
        "candidate_state_before=pending_lookahead, audit_outcome=rejected, "
        "delivery_state=not_required, listed_state_after=removed:1",
        "2026-09-12 10:00:01 [INFO] lsc.handlers: 精修候选终态入可靠队列: "
        f"room_id={room}, recording_id=rec-x, round_key=round-000003, "
        "candidate_state_before=passed, audit_outcome=accepted, "
        "delivery_state=queued, listed_state_after=pending_delivery",
        "2026-09-12 10:00:02 [INFO] lsc.server: Sending WS response: "
        f"type=get_continuous_analysis_status_response, data={status!r}",
        "2026-09-12 10:00:03 [INFO] lsc.server: Sending WS response: "
        f"type=generate_jianying_draft_response, data={response!r}",
    ]
    report = _evaluate(lines, room=room, finalization=sidecar, draft_dir=str(draft_dir))
    if report is None:
        print("[self-test] 未产出报告（构造失败）", file=sys.stderr)
        return 2
    for check in report["checks"]:
        print(("[OK]   " if check["passed"] else "[FAIL] ") + check["name"] + ": " + check["detail"])
    if report["passed"]:
        keep = bool(os.environ.get("L3_SELFTEST_KEEP"))
        where = f"（合成场景保留在 {root}）" if keep else "（合成场景已清理；L3_SELFTEST_KEEP=1 可保留）"
        print(f"[self-test] {len(report['checks'])}/{len(report['checks'])} 通过{where}")
        return 0
    print("[self-test] 合成场景本应全绿却报红 —— 校验器或构造有问题", file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", action="append", help="backend 日志；可重复")
    parser.add_argument("--room-id", default="", help="只看该房间")
    parser.add_argument("--finalization", default="", help="收尾 sidecar（*.finalization.json）")
    parser.add_argument("--draft-dir", default="", help="草稿目录（读明文备份核段数）")
    parser.add_argument("--out", default="", help="报告 JSON 输出路径；缺省只打印")
    parser.add_argument("--self-test", action="store_true", help="跑合成全绿场景自检")
    args = parser.parse_args(argv)

    if args.self_test:
        return _self_test()
    if not args.log:
        parser.error("需要 --log（或 --self-test）")

    lines = _load_lines(args.log)
    if not lines:
        print("日志为空", file=sys.stderr)
        return 2
    room = args.room_id.strip()
    finalization = None
    if args.finalization and Path(args.finalization).is_file():
        finalization = json.loads(Path(args.finalization).read_text(encoding="utf-8"))

    report = _evaluate(lines, room=room, finalization=finalization, draft_dir=args.draft_dir)
    if report is None:
        print("日志里找不到该房间的导出响应（导出未发生？）", file=sys.stderr)
        return 2
    if args.out:
        out = Path(args.out)
        tmp = out.with_suffix(out.suffix + ".tmp")
        tmp.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        tmp.replace(out)
    for check in report["checks"]:
        print(("[OK]   " if check["passed"] else "[FAIL] ") + check["name"] + ": " + check["detail"])
    failed = [check["name"] for check in report["checks"] if not check["passed"]]
    if failed:
        print(f"L3 未通过: {', '.join(failed)}", file=sys.stderr)
        return 1
    print("L3 全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
