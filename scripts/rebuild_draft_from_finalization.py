"""从收尾 sidecar 离线重建剪映草稿（不改动运行中的应用）。

为什么需要它：直播源断开（ingest broken pipe）会让录制 ffmpeg 死在写盘中途，
收尾改名没跑 ⇒ 录像停在 ``*_录制中.mp4`` ⇒ 剪映草稿守卫
（``jianying_handlers`` 只按文件名/房间路径判"仍在录制"）永远拒绝生成草稿。
此时磁盘上的 ``*.finalization.json`` 已经含有全部候选与审计出点，
可以完全绕开应用内存态直接建草稿。

与生产一致的部分（刻意复用，不另写一套）：
- 改名规则：``finalize_recording_file`` + ``move_recording_sidecars``，
  起止时刻取「文件名前缀」与「文件 mtime」，与启动自愈
  ``lsc.core.recording_layout.heal_stale_in_progress_recordings`` 完全一致；
- 门禁：``clip_allowed_for_draft``（失败关闭，不得放宽）；
- 导出：``build_session_draft``；原因码：``boundary_quality_reason_code``。

用法：
  python scripts/rebuild_draft_from_finalization.py \
      --recording "D:\\...\\2026-09-12_15-16-24_录制中.mp4" \
      [--dry-run] [--no-rename] [--include-pending] [--draft-name ...]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for extra in (ROOT, ROOT / "python-backend"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from lsc.core.models import JianyingDraftOptions  # noqa: E402
from lsc.core.recording_layout import (  # noqa: E402
    _IN_PROGRESS_STEM_FORMAT,
    _IN_PROGRESS_SUFFIX,
    finalize_recording_file,
    move_recording_sidecars,
)
from lsc.exporter.jianying_draft import (  # noqa: E402
    ClipDraftSource,
    RoomDraftSource,
    build_session_draft,
    clip_allowed_for_draft,
)
from continuous_finalization import boundary_quality_reason_code  # noqa: E402

# 侧车里的候选字段 → ClipDraftSource / 门禁需要的字段名（尽量原样透传）
_AUDIT_FIELDS = (
    "confirm_status", "source_profile", "broadcast_audit", "broadcast_review_required",
    "start_quality", "end_quality", "start_review_required", "end_review_required",
    "duration_anomaly", "end_by",
)


def _probe_duration(path: str) -> float:
    from lsc.config import load_config

    cfg = load_config()
    ffprobe = cfg.ffprobe_path or "ffprobe"
    try:
        out = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", path],
            capture_output=True, text=True, timeout=60,
        )
        return float((out.stdout or "0").strip() or 0.0)
    except (OSError, ValueError, subprocess.SubprocessError):
        return 0.0


def _rename_in_progress(recording: Path) -> tuple[Path, list[str]]:
    """按启动自愈的规则把 `_录制中` 定稿改名（含 sidecar）。返回 (新录像, 改名项)。"""
    name = recording.name
    if _IN_PROGRESS_SUFFIX not in name:
        return recording, []
    started_raw = name.split(_IN_PROGRESS_SUFFIX, 1)[0]
    try:
        started_at = datetime.strptime(started_raw, _IN_PROGRESS_STEM_FORMAT)
    except ValueError:
        return recording, []
    ended_at = datetime.fromtimestamp(recording.stat().st_mtime)
    dest = finalize_recording_file(
        str(recording), started_at=started_at, ended_at=ended_at,
        dest_dir=str(recording.parent),
    )
    moved = move_recording_sidecars(str(recording), dest)
    return Path(dest), moved


def _sidecar_for(recording: Path, explicit: str | None) -> Path:
    if explicit:
        return Path(explicit)
    return recording.with_name(recording.stem + ".finalization.json")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--recording", required=True)
    ap.add_argument("--finalization", default=None)
    ap.add_argument("--room-id", default=None)
    ap.add_argument("--room-name", default=None)
    ap.add_argument("--draft-root", default=None)
    ap.add_argument("--draft-name", default=None)
    ap.add_argument("--include-pending", action="store_true")
    ap.add_argument("--no-rename", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--out", default=None, help="报告 JSON 输出路径")
    a = ap.parse_args(argv)

    recording = Path(a.recording).expanduser().resolve()
    if not recording.is_file():
        print(f"录像不存在: {recording}", file=sys.stderr)
        return 2
    sidecar = _sidecar_for(recording, a.finalization)
    if not sidecar.is_file():
        print(f"收尾 sidecar 不存在: {sidecar}", file=sys.stderr)
        return 2

    renames: list[str] = []
    if not a.no_rename and not a.dry_run:
        recording, moved = _rename_in_progress(recording)
        if moved or recording.name != Path(a.recording).name:
            renames = [f"{Path(a.recording).name} -> {recording.name}"] + [
                os.path.basename(m) for m in moved
            ]
        sidecar = _sidecar_for(recording, a.finalization)

    final = json.loads(sidecar.read_text(encoding="utf-8"))
    room_id = a.room_id or str(final.get("room_id") or "")
    room_name = a.room_name or recording.parent.name
    duration = _probe_duration(str(recording))
    accepted = list(final.get("accepted_candidates") or [])
    rejected = list(final.get("rejected_candidates") or [])
    print(f"录像: {recording.name}  ({duration:.2f}s)")
    print(f"sidecar: {sidecar.name}  phase={final.get('phase')}  "
          f"duration={final.get('final_duration')}")
    for line in renames:
        print(f"改名: {line}")

    clip_sources: list[ClipDraftSource] = []
    skipped: list[dict] = []
    for cand in accepted:
        c = dict(cand)
        c.setdefault("room_id", room_id)
        c.setdefault("label", f"回合 {c.get('round_key')}")
        # 单房草稿的公共轴 = 录制轴（与 handler 的 ctx 缺失兜底一致）
        c["recording_start_sec"] = c.get("start")
        c["recording_end_sec"] = c.get("end")
        c["common_start"] = float(c.get("start") or 0.0)
        c["common_end"] = float(c.get("end") or 0.0)
        code = boundary_quality_reason_code(
            confirm_status=c.get("confirm_status"), end_by=c.get("end_by"),
            boundary_refined=bool(c.get("boundary_refined")),
            start_confidence=c.get("start_confidence"),
            end_confidence=c.get("end_confidence"),
            start_delta=c.get("start_delta"), end_delta=c.get("end_delta"),
            source_profile=c.get("source_profile"),
            broadcast_audit=c.get("broadcast_audit"),
            broadcast_audit_reason=c.get("broadcast_audit_reason"),
        )
        if not clip_allowed_for_draft(c, include_pending=a.include_pending):
            skipped.append({
                "round_key": c.get("round_key"), "start": c.get("start"),
                "end": c.get("end"), "end_by": c.get("end_by"),
                "audit": c.get("broadcast_audit"), "reason_code": code,
            })
            continue
        clip_sources.append(ClipDraftSource(
            clip_id=str(c.get("clip_id") or f"{room_id}_{c.get('round_key')}"),
            common_start=float(c["common_start"]),
            common_end=float(c["common_end"]),
            label=str(c.get("label") or "回合"),
            precision="exact",
            room_id=room_id,
            **{k: c.get(k) for k in _AUDIT_FIELDS},
        ))

    print(f"\n候选 {len(accepted)} 条 → 门禁通过 {len(clip_sources)} 条，跳过 {len(skipped)} 条"
          f"（rejected 桶另有 {len(rejected)} 条，按设计不入稿）")
    for s in skipped:
        print(f"  跳过 {s['round_key']:16s} {s['start']:8.2f}-{s['end']:8.2f} "
              f"{s['end_by']} audit={s['audit']} 原因={s['reason_code']}")

    stamp = datetime.fromtimestamp(recording.stat().st_mtime).strftime("%Y%m%d_%H%M")
    draft_name = a.draft_name or f"LSC_{room_name}_{stamp}"
    root = a.draft_root
    if not root:
        settings_path = Path(os.path.expanduser("~")) / "AppData/Roaming/lsc-electron/settings.json"
        try:
            root = json.loads(settings_path.read_text(encoding="utf-8")).get("jianying_draft_dir")
        except (OSError, json.JSONDecodeError):
            root = None
    if not root:
        print("未取到剪映草稿目录（settings.json 缺 jianying_draft_dir）", file=sys.stderr)
        return 2

    report = {
        "recording": str(recording), "duration_sec": duration, "sidecar": str(sidecar),
        "renames": renames, "draft_root": root, "draft_name": draft_name,
        "candidates": len(accepted), "included": len(clip_sources),
        "skipped": skipped, "rejected_in_sidecar": len(rejected),
        "clips": [{"round_key": s.clip_id.split("_")[-1], "start": s.common_start,
                   "end": s.common_end, "label": s.label,
                   "end_by": s.end_by, "audit": s.broadcast_audit} for s in clip_sources],
    }
    if a.dry_run:
        print("\n[dry-run] 未写入草稿")
    elif clip_sources:
        result = build_session_draft(
            rooms=[RoomDraftSource(room_id=room_id, name=room_name,
                                   record_output_path=str(recording),
                                   recording_to_common_delta=0.0, is_main=True)],
            clips=clip_sources,
            options=JianyingDraftOptions(
                include_recordings=True, include_clips=True, text_labels=True,
                draft_name=draft_name, include_pending=a.include_pending,
            ),
            draft_root=root,
        )
        report.update({
            "success": result.success, "draft_dir": result.draft_dir,
            "draft_name_result": result.draft_name, "tracks": result.tracks,
            "segments": result.segments, "placed_clip_count": result.placed_clip_count,
            "excluded_clips": result.excluded_clips,
            "error": getattr(result, "error", ""),
        })
        print(f"\n草稿: success={result.success} name={result.draft_name} "
              f"tracks={result.tracks} segments={result.segments} "
              f"placed_clip_count={result.placed_clip_count} dir={result.draft_dir}")
        if getattr(result, "error", ""):
            print(f"error: {result.error}")
        if result.excluded_clips:
            print(f"导出器内部再筛掉 {len(result.excluded_clips)} 条:")
            for e in result.excluded_clips:
                print(f"   {e}")
        if not result.success:
            print("建草稿失败", file=sys.stderr)
            return 1
    else:
        print("\n没有通过门禁的切片，未写入草稿", file=sys.stderr)
        return 1

    if a.out:
        Path(a.out).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"报告: {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
