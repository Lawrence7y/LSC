"""剪映草稿导出 WebSocket handlers。"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Callable

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
    options = JianyingDraftOptions(
        include_recordings=bool(raw_opt.get("include_recordings", True)),
        include_clips=bool(raw_opt.get("include_clips", True)),
        text_labels=bool(raw_opt.get("text_labels", True)),
        vertical=bool(raw_opt.get("vertical", False)),
        draft_name=str(raw_opt.get("draft_name") or ""),
        non_main_volume_zero=bool(raw_opt.get("non_main_volume_zero", False)),
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
                    recording_to_common_delta=float(snap.recording_to_common_delta),
                    is_main=(rid == main_id),
                )
            )
    else:
        if len(room_ids_resolved) > 1 and not allow_single_fallback:
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
                recording_to_common_delta=0.0,
                is_main=True,
            )
        )

    clip_sources: list[ClipDraftSource] = []
    inline_clips = list(data.get("clips") or [])
    if inline_clips:
        for c in inline_clips:
            cid = c.get("clip_id") or c.get("clip_snapshot_id")
            if clip_ids and cid not in clip_ids:
                continue
            if not clip_allowed_for_draft(c):
                label = c.get("label") or cid or "切片"
                warnings.append(f"切片 {label} 未确认或为近似定位，已跳过")
                continue
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
                )
            )

    return None, sources, clip_sources, options, warnings


def register_jianying_handlers(
    server,
    *,
    bridge,
    manager,
    load_settings: Callable[[], dict],
) -> None:
    """注册剪映相关 WS handlers。"""

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
