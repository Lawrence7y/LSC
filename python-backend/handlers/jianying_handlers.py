"""剪映草稿导出 WebSocket handlers。"""
from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Callable
from typing import Any

from lsc.core.models import JianyingDraftOptions
from lsc.core.services.timeline_service import get_timeline_service
from lsc.exporter.jianying_draft import (
    ClipDraftSource,
    RoomDraftSource,
    build_session_draft,
    clip_allowed_for_draft,
    detect_jianying_draft_dir,
    resolve_common_range,
)

_log = logging.getLogger(__name__)

# 由 register_jianying_handlers 注入的后端权威切片快照（room_handler 的
# _continuous_tasks / _analysis_jobs 中的 listed_clips）。草稿门禁必须以此为
# 准合并赛事审计字段，不能只信任前端薄投影。
_continuous_tasks: dict[str, dict[str, Any]] = {}
_analysis_jobs: dict[str, dict[str, Any]] = {}

_ERROR_CODES = (
    "draft_dir_missing",
    "no_rooms",
    "no_aligned_context",
    "library_missing",
    "write_failed",
    "invalid_state",
)


def _resolve_draft_root(settings: dict) -> tuple[str | None, bool]:
    """返回 (path, auto_detected)。"""
    configured = (settings.get("jianying_draft_dir") or "").strip()
    if configured:
        return configured, False
    detected = detect_jianying_draft_dir()
    return detected, True


def _room_display_name(room, room_id: str) -> str:
    name = getattr(room, "streamer_name", None) or ""
    return name or room_id[:8]


def _derive_room_deltas_from_clips(clips: list[dict]) -> dict[str, float]:
    """ctx 缺失时从切片内联坐标反推每房 recording→common delta。

    切片前端携带 start/end（录制坐标）与 common_start/common_end（公共轴），
    差值即该房间 recording_to_common_delta（同一房间多切片取均值）。
    """
    deltas: dict[str, list[float]] = {}
    for c in clips:
        rid = c.get("room_id")
        try:
            start = float(c.get("start"))
            common = float(c.get("common_start"))
        except (TypeError, ValueError):
            continue
        if not rid or start < 0 or common < 0:
            continue
        deltas.setdefault(rid, []).append(common - start)
    out: dict[str, float] = {}
    for rid, vals in deltas.items():
        out[rid] = sum(vals) / len(vals)
    return out


_AUTHORITATIVE_AUDIT_FIELDS = (
    "source_profile",
    "broadcast_audit",
    "broadcast_audit_reason",
    "broadcast_excluded_reason",
    "broadcast_review_required",
    "duration_anomaly",
    "end_by",
    "start_by",
    "boundary_source",
    "broadcast_model_version",
    "broadcast_model_provider",
    "model_version",
)


def _lookup_continuous_clip(clip: dict) -> dict[str, Any] | None:
    """从后端权威快照（listed_clips）找回完整赛事审计元数据。

    后端优先于前端：前端可以覆盖 start/end/confirm_status（人工复核），
    但不得伪造 source_profile / broadcast_audit / duration_anomaly 等审计字段。
    """
    rid = str(clip.get("room_id") or "")
    rk = str(clip.get("round_key") or clip.get("clip_id") or "")
    if not rid or not rk:
        return None
    primary_key = f"{rid}:{rk}"

    def _match_in_listed(listed: Any) -> dict[str, Any] | None:
        if isinstance(listed, dict):
            item = listed.get(primary_key)
            if isinstance(item, dict):
                return item
            for value in listed.values():
                if not isinstance(value, dict):
                    continue
                if (
                    str(value.get("room_id") or "") == rid
                    and (
                        str(value.get("round_key") or "") == rk
                        or str(value.get("clip_id") or "") == rk
                    )
                ):
                    return value
            return None
        if isinstance(listed, list):
            for value in listed:
                if not isinstance(value, dict):
                    continue
                if (
                    str(value.get("room_id") or "") == rid
                    and (
                        str(value.get("round_key") or "") == rk
                        or str(value.get("clip_id") or "") == rk
                    )
                ):
                    return value
        return None

    for task in _continuous_tasks.values():
        found = _match_in_listed(task.get("listed_clips"))
        if found:
            return found
    for job in _analysis_jobs.values():
        found = _match_in_listed(job.get("listed_clips"))
        if found:
            return found
    return None


def _merge_authoritative_clip(clip: dict) -> dict:
    """把后端权威审计字段合并进前端切片 dict（后端字段优先）。"""
    merged = dict(clip)
    auth = _lookup_continuous_clip(clip)
    if not auth:
        return merged
    for key in _AUTHORITATIVE_AUDIT_FIELDS:
        if key in auth and auth[key] is not None:
            merged[key] = auth[key]
    # 后端权威坐标/round_key 优先，避免前端改动边界后漏改审计关系
    for key in ("round_key", "clip_id"):
        if key in auth and auth[key]:
            merged[key] = auth[key]
    return merged


def _collect_draft_inputs(
    manager,
    data: dict[str, Any],
) -> tuple[dict[str, Any] | None, list[RoomDraftSource], list[ClipDraftSource], JianyingDraftOptions, list[str]]:
    """装配房间源、切片源与选项。成功时首项为 None，失败时返回 error payload。"""
    warnings: list[str] = []
    timeline_svc = get_timeline_service()

    room_ids = list(data.get("room_ids") or [])
    clip_ids = list(data.get("clip_ids") or [])
    raw_opt = data.get("options") or {}
    include_pending = bool(data.get("include_pending", False))
    options = JianyingDraftOptions(
        include_recordings=bool(raw_opt.get("include_recordings", True)),
        include_clips=bool(raw_opt.get("include_clips", True)),
        text_labels=bool(raw_opt.get("text_labels", True)),
        vertical=bool(raw_opt.get("vertical", False)),
        draft_name=str(raw_opt.get("draft_name") or ""),
        non_main_volume_zero=bool(raw_opt.get("non_main_volume_zero", False)),
        include_pending=include_pending,
    )
    labels = data.get("labels") or {}
    allow_single_fallback = bool(data.get("allow_single_fallback", False))

    all_rooms = manager.list_rooms()
    room_map = {r.room_id: r for r in all_rooms}

    if not room_ids:
        room_ids_resolved = list(room_map.keys())
    else:
        room_ids_resolved = room_ids

    ctx = None
    for rid in room_ids_resolved:
        ctx = timeline_svc.get_active_timeline_for_room(rid)
        if ctx is not None:
            break

    main_id = ctx.reference_room_id if ctx is not None else (
        room_ids_resolved[0] if room_ids_resolved else None
    )

    # ctx 缺失时（预览重启/画质切换后未重新对齐），尝试从切片内联坐标
    # （start/end 录制坐标 + common_start/end 公共轴）反推每房 delta 兜底。
    inline_clips = list(data.get("clips") or [])
    derived_deltas: dict[str, float] = {}
    if ctx is None:
        derived_deltas = _derive_room_deltas_from_clips(inline_clips)

    sources: list[RoomDraftSource] = []
    if ctx is not None:
        for rid in room_ids_resolved:
            snap = ctx.room_snapshots.get(rid)
            if snap is None:
                warnings.append(f"房间 {rid} 对齐置信不足或不在对齐组，已跳过")
                continue
            room = room_map.get(rid) or manager.get_room(rid)
            if room is None:
                continue
            sources.append(
                RoomDraftSource(
                    room_id=rid,
                    name=_room_display_name(room, rid),
                    record_output_path=getattr(room, "record_output_path", "") or "",
                    record_manifest_path=getattr(room, "record_manifest_path", "") or "",
                    recording_to_common_delta=float(snap.recording_to_common_delta),
                    is_main=(rid == main_id),
                )
            )
    else:
        if len(room_ids_resolved) > 1 and not allow_single_fallback:
            if not derived_deltas:
                return (
                    {
                        "success": False,
                        "error": "多房草稿需先一键对齐",
                        "error_code": "no_aligned_context",
                        "warnings": warnings,
                    },
                    [],
                    [],
                    options,
                    warnings,
                )
            # 无对齐上下文但切片自带坐标：按切片反推的 delta 构建房间源
            warnings.append("未对齐，已按切片坐标反推各房间偏移生成草稿")
            main_id = str(data.get("main_room_id") or room_ids_resolved[0])
            for rid in room_ids_resolved:
                delta = derived_deltas.get(rid)
                if delta is None:
                    warnings.append(f"房间 {rid} 无切片坐标，已跳过")
                    continue
                room = room_map.get(rid) or manager.get_room(rid)
                if room is None:
                    continue
                sources.append(
                    RoomDraftSource(
                        room_id=rid,
                        name=_room_display_name(room, rid),
                        record_output_path=getattr(room, "record_output_path", "") or "",
                        record_manifest_path=getattr(room, "record_manifest_path", "") or "",
                        recording_to_common_delta=float(delta),
                        is_main=(rid == main_id),
                    )
                )
            if not sources:
                return (
                    {
                        "success": False,
                        "error": "多房草稿需先一键对齐",
                        "error_code": "no_aligned_context",
                        "warnings": warnings,
                    },
                    [],
                    [],
                    options,
                    warnings,
                )
        else:
            rid = main_id or (room_ids_resolved[0] if room_ids_resolved else None)
            if not rid:
                return (
                    {
                        "success": False,
                        "error": "没有可用房间",
                        "error_code": "no_rooms",
                    },
                    [],
                    [],
                    options,
                    warnings,
                )
            room = room_map.get(rid) or manager.get_room(rid)
            if room is None:
                return (
                    {
                        "success": False,
                        "error": "房间不存在",
                        "error_code": "no_rooms",
                    },
                    [],
                    [],
                    options,
                    warnings,
                )
            warnings.append("未对齐，已降级为主房单房草稿")
            sources.append(
                RoomDraftSource(
                    room_id=rid,
                    name=_room_display_name(room, rid),
                    record_output_path=getattr(room, "record_output_path", "") or "",
                    record_manifest_path=getattr(room, "record_manifest_path", "") or "",
                    recording_to_common_delta=0.0,
                    is_main=True,
                )
            )

    clip_sources: list[ClipDraftSource] = []
    if inline_clips:
        for raw_c in inline_clips:
            # 后端权威审计字段优先（失败关闭门禁不能只信前端）。
            c = _merge_authoritative_clip(raw_c)
            cid = c.get("clip_id") or c.get("clip_snapshot_id")
            if clip_ids and cid not in clip_ids:
                continue
            if not clip_allowed_for_draft(c, include_pending=include_pending):
                label = c.get("label") or cid or "切片"
                warnings.append(f"切片 {label} 未确认/近似定位/未通过赛事审计，已跳过")
                continue
            if include_pending and c.get("confirm_status") in ("pending", "refining"):
                label = c.get("label") or cid or "切片"
                warnings.append(f"切片 {label} 的边界仍待确认，已按暂定出点加入草稿")
            if ctx is None and derived_deltas:
                # 无 ctx 兜底：缺 common 坐标时用反推 delta 补全（start+delta）
                rid = c.get("room_id")
                delta = derived_deltas.get(rid) if rid else None
                if delta is not None and c.get("common_start") is None and c.get("start") is not None:
                    try:
                        s = float(c["start"])
                        e = float(c["end"])
                        c["common_start"] = s + delta
                        c["common_end"] = e + delta
                    except (TypeError, ValueError):
                        pass
            resolved = resolve_common_range(c, ctx)
            if resolved is None:
                label = c.get("label") or cid or "切片"
                warnings.append(f"切片 {label} 无法映射到公共轴，已跳过")
                continue
            cs, ce, prec = resolved
            clip_sources.append(
                ClipDraftSource(
                    clip_id=str(cid or ""),
                    common_start=cs,
                    common_end=ce,
                    label=str(c.get("label") or labels.get(str(cid), "") or "回合"),
                    precision=prec,
                    confirm_status=c.get("confirm_status"),
                    room_id=str(c.get("room_id") or ""),
                    source_profile=c.get("source_profile"),
                    broadcast_audit=c.get("broadcast_audit"),
                    broadcast_review_required=bool(c.get("broadcast_review_required")),
                    duration_anomaly=bool(c.get("duration_anomaly")),
                    end_by=c.get("end_by"),
                )
            )
    elif clip_ids:
        for cid in clip_ids:
            snap = timeline_svc.get_clip_snapshot(cid)
            if snap is None:
                warnings.append(f"切片 {cid} 不存在或已过期，已跳过")
                continue
            clip_sources.append(
                ClipDraftSource(
                    clip_id=snap.clip_id,
                    common_start=snap.common_start,
                    common_end=snap.common_end,
                    label=str(labels.get(cid) or "回合"),
                    precision="exact",
                    confirm_status="user_confirmed",
                    room_id=str(snap.room_id or ""),
                )
            )

    return None, sources, clip_sources, options, warnings


def register_jianying_handlers(
    server,
    *,
    bridge,
    manager,
    load_settings: Callable[[], dict],
    continuous_tasks: dict | None = None,
    analysis_jobs: dict | None = None,
) -> None:
    """注册剪映相关 WS handlers。"""
    if continuous_tasks is not None:
        _continuous_tasks = continuous_tasks
    if analysis_jobs is not None:
        _analysis_jobs = analysis_jobs

    @server.on("get_jianying_draft_dir")
    async def handle_get_jianying_draft_dir(data: dict[str, Any] | None):
        settings = load_settings()
        path, auto = _resolve_draft_root(settings)
        exists = bool(path and os.path.isdir(path))
        return {
            "success": True,
            "draft_dir": path or "",
            "auto_detected": auto and bool(path),
            "exists": exists,
        }

    @server.on("generate_jianying_draft")
    async def handle_generate_jianying_draft(data: dict[str, Any] | None):
        data = data or {}
        settings = load_settings()
        draft_root, _auto = _resolve_draft_root(settings)
        if not draft_root or not os.path.isdir(draft_root):
            return {
                "success": False,
                "error": "剪映草稿目录未找到，请到设置页配置",
                "error_code": "draft_dir_missing",
            }

        err, sources, clip_sources, options, warnings = _collect_draft_inputs(
            manager, data
        )
        if err is not None:
            return err

        loop = asyncio.get_running_loop()

        def _run():
            return build_session_draft(
                rooms=sources,
                clips=clip_sources if options.include_clips else [],
                options=options,
                draft_root=draft_root,
            )

        result = await loop.run_in_executor(None, _run)
        return {
            "success": result.success,
            "draft_name": result.draft_name,
            "draft_dir": result.draft_dir,
            "tracks": result.tracks,
            "segments": result.segments,
            "warnings": list(warnings) + list(result.warnings),
            "error": result.error,
            "error_code": result.error_code,
        }
