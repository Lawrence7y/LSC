from __future__ import annotations

import logging
import os
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from lsc.core.models import JianyingDraftOptions, JianyingDraftResult

_log = logging.getLogger(__name__)

_ILLEGAL = re.compile(r'[<>:"/\\|?*]')
_MIN_SEG_SEC = 0.2
_MAX_DRAFT_DURATION_SEC = 150.0


@dataclass(slots=True)
class RoomDraftSource:
    room_id: str
    name: str
    record_output_path: str
    recording_to_common_delta: float
    is_main: bool = False
    record_manifest_path: str = ""


@dataclass(slots=True)
class ClipDraftSource:
    clip_id: str
    common_start: float
    common_end: float
    label: str
    precision: str = "exact"  # exact | approximate
    confirm_status: str | None = None
    room_id: str | None = None
    # 赛事审计字段（从 clip_queued / 后端权威快照透传，用于失败关闭门禁）
    source_profile: str | None = None
    broadcast_audit: str | None = None
    broadcast_review_required: bool = False
    start_quality: str | None = None
    end_quality: str | None = None
    start_review_required: bool = False
    end_review_required: bool = False
    duration_anomaly: bool = False
    end_by: str | None = None


_BROADCAST_VALID_END_BY = frozenset({"next_prep", "broadcast_exclusion"})


def sanitize_draft_token(name: str) -> str:
    text = (name or "").strip() or "room"
    return _ILLEGAL.sub("_", text)


def compute_draft_origin(deltas: dict[str, float]) -> float:
    if not deltas:
        return 0.0
    return float(min(deltas.values()))


def map_recording_timeranges(
    *,
    recording_to_common_delta: float,
    draft_origin: float,
    dur_sec: float,
) -> tuple[float, float, float, float]:
    """返回 (target_start, target_dur, source_start, source_dur)，单位秒。"""
    return (
        recording_to_common_delta - draft_origin,
        dur_sec,
        0.0,
        dur_sec,
    )


def map_clip_timeranges(
    common_start: float,
    common_end: float,
    recording_to_common_delta: float,
    draft_origin: float,
    dur_sec: float | None = None,
) -> tuple[float, float, float, float, bool] | None:
    """
    成功时返回 (t_start, t_dur, s_start, s_dur, clamped)。
    当传入 dur_sec 时始终返回 5 元组（含 clamped）；跳过/过短返回 None。
    """
    dur = common_end - common_start
    if dur <= _MIN_SEG_SEC:
        return None
    t_start = common_start - draft_origin
    s_start = common_start - recording_to_common_delta
    s_dur = dur
    clamped = False
    if s_start < 0:
        return None
    if dur_sec is not None and s_start + s_dur > dur_sec:
        s_dur = dur_sec - s_start
        clamped = True
        if s_dur <= _MIN_SEG_SEC:
            return None
    return (t_start, s_dur, s_start, s_dur, clamped)


def _broadcast_gate_passed(
    *,
    confirm_status: str | None,
    source_profile: str | None,
    broadcast_audit: str | None,
    broadcast_review_required: bool,
    start_quality: str | None = None,
    end_quality: str | None = None,
    start_review_required: bool = False,
    end_review_required: bool = False,
    duration_anomaly: bool,
    end_by: str | None,
    include_pending: bool,
) -> bool:
    """赛事（broadcast）草稿失败关闭门禁。

    - 人工确认（user_confirmed）可绕过审计缺失（用户已复核）；
    - 手动勾选「包含待确认切片」（include_pending=True）时允许 pending/refining
      以暂定出点入草稿；
    - 除此之外，broadcast 切片必须 vision_confirmed + broadcast_audit==passed
      + 无复核标记 + 无时长异常 + 出点为 next_prep / broadcast_exclusion。
    """
    status = str(confirm_status or "").strip().lower()
    audit = str(broadcast_audit or "").strip().lower()
    if str(source_profile or "").strip().lower() != "broadcast":
        return True
    # 拒绝/纯回放候选永不进入剪映草稿，即使“包含待确认”也不复活。
    if (
        status == "rejected"
        or audit.startswith("rejected")
    ):
        return False
    if status == "user_confirmed":
        return True
    end_is_authoritative = bool(
        audit == "passed"
        and str(end_quality or "").strip().lower() == "precise"
        and not end_review_required
        and not duration_anomaly
        and str(end_by or "") in _BROADCAST_VALID_END_BY
    )
    # 赛事切片的出点已由视觉审计定稿时，允许带着
    # coarse 但合法的 OCR 入点进入自动草稿；不再因一个
    # 聚合的 broadcast_review_required 把整条切片删掉。
    if status in ("pending", "vision_confirmed") and end_is_authoritative:
        return True
    if status in ("pending", "refining") and include_pending:
        if duration_anomaly:
            return False
        return True
    if status != "vision_confirmed":
        return False
    if audit != "passed":
        return False
    if broadcast_review_required:
        return False
    if duration_anomaly:
        return False
    if str(end_by or "") not in _BROADCAST_VALID_END_BY:
        return False
    return True


def clip_source_usable(
    *,
    precision: str,
    confirm_status: str | None,
    include_pending: bool = False,
    source_profile: str | None = None,
    broadcast_audit: str | None = None,
    broadcast_review_required: bool = False,
    start_quality: str | None = None,
    end_quality: str | None = None,
    start_review_required: bool = False,
    end_review_required: bool = False,
    duration_anomaly: bool = False,
    end_by: str | None = None,
) -> bool:
    if precision == "approximate":
        return False
    if (
        confirm_status in ("pending", "refining")
        and not include_pending
        and str(source_profile or "").strip().lower() != "broadcast"
    ):
        return False
    return _broadcast_gate_passed(
        confirm_status=confirm_status,
        source_profile=source_profile,
        broadcast_audit=broadcast_audit,
        broadcast_review_required=broadcast_review_required,
        start_quality=start_quality,
        end_quality=end_quality,
        start_review_required=start_review_required,
        end_review_required=end_review_required,
        duration_anomaly=duration_anomaly,
        end_by=end_by,
        include_pending=include_pending,
    )


def clip_allowed_for_draft(clip: dict, *, include_pending: bool = False) -> bool:
    """WS/前端切片 dict 是否允许进入草稿（与导出口径一致）。"""
    status = clip.get("confirm_status")
    if (
        status in ("pending", "refining")
        and not include_pending
        and str(clip.get("source_profile") or "").strip().lower() != "broadcast"
    ):
        return False
    if clip.get("mark_precision") == "approximate":
        return False
    # 无效坐标/异常时长即使 include_pending 也不得进入（仅当调用方提供了坐标时）。
    if "start" in clip and "end" in clip:
        try:
            start = float(clip.get("start") or 0.0)
            end = float(clip.get("end") or 0.0)
        except (TypeError, ValueError):
            return False
        if end <= start or end - start > _MAX_DRAFT_DURATION_SEC:
            return False
    return _broadcast_gate_passed(
        confirm_status=status,
        source_profile=clip.get("source_profile"),
        broadcast_audit=clip.get("broadcast_audit"),
        broadcast_review_required=bool(clip.get("broadcast_review_required")),
        start_quality=clip.get("start_quality"),
        end_quality=clip.get("end_quality"),
        start_review_required=bool(clip.get("start_review_required")),
        end_review_required=bool(clip.get("end_review_required")),
        duration_anomaly=bool(clip.get("duration_anomaly")),
        end_by=clip.get("end_by"),
        include_pending=include_pending,
    )


def resolve_common_range(clip: dict[str, Any], ctx: Any) -> tuple[float, float, str] | None:
    """返回 (common_start, common_end, precision) 或 None（无法映射）。"""
    cs = clip.get("common_start")
    ce = clip.get("common_end")
    if cs is not None and ce is not None:
        return float(cs), float(ce), "exact"
    # 手动切片的 start/end 是预览轴；若前端已携带后端确认/计算出的录制轴
    # 范围，优先使用它，避免在无 TimelineContext 的单房场景把预览时间误当
    # 成录制时间（预览启动延迟可能达到数秒）。
    recording_start = clip.get("recording_start_sec")
    recording_end = clip.get("recording_end_sec")
    if recording_start is not None and recording_end is not None:
        try:
            rs = float(recording_start)
            re = float(recording_end)
        except (TypeError, ValueError):
            pass
        else:
            if re > rs:
                if ctx is not None:
                    room_id = clip.get("room_id")
                    snapshots = getattr(ctx, "room_snapshots", None) or {}
                    snap = snapshots.get(room_id) if room_id else None
                    if snap is not None:
                        return (
                            rs + float(snap.recording_to_common_delta),
                            re + float(snap.recording_to_common_delta),
                            "exact",
                        )
                # 无公共轴时，单房录制轴就是剪映草稿的公共轴。
                return rs, re, "exact"
    # H8 回退：切片 recording 本地时间 + 该房 timeline 快照 delta 换算公共轴。
    # AI 切片在 clip_queued 时若公共轴未就绪会缺 common 坐标，此处补救。
    if ctx is not None:
        room_id = clip.get("room_id")
        snapshots = getattr(ctx, "room_snapshots", None) or {}
        snap = snapshots.get(room_id) if room_id else None
        if snap is not None:
            try:
                s = float(clip.get("start", 0))
                e = float(clip.get("end", 0))
                delta = float(snap.recording_to_common_delta)
            except (TypeError, ValueError):
                pass
            else:
                if e > s:
                    return s + delta, e + delta, "exact"
    mark_in = clip.get("mark_in_wallclock")
    mark_out = clip.get("mark_out_wallclock")
    media_starts: list[float] = []
    if ctx is not None:
        media_starts = [
            float(s.media_start_mono)
            for s in ctx.room_snapshots.values()
            if s.media_start_mono
        ]
    if mark_in is not None and mark_out is not None and media_starts:
        origin = min(media_starts)
        return float(mark_in) - origin, float(mark_out) - origin, "exact"
    # 兼容较早版本前端产生的手动切片：虽没有 recording_start_sec，仍可用
    # 入列时冻结的墙钟与录制媒体起点恢复录制轴。不能直接退回 preview start/end。
    if ctx is None:
        try:
            mark_in_value = float(mark_in)
            mark_out_value = float(mark_out)
            recording_start_mono = float(
                clip.get("recording_media_start_mono")
                if clip.get("recording_media_start_mono") is not None
                else clip.get("recording_start_mono")
            )
            content_offset = float(clip.get("content_offset") or 0.0)
        except (TypeError, ValueError):
            pass
        else:
            rs = max(0.0, mark_in_value - recording_start_mono - content_offset)
            re = max(0.0, mark_out_value - recording_start_mono - content_offset)
            if re > rs:
                return rs, re, "exact"
    if ctx is None:
        # 单房降级兜底：无 timeline ctx 时该房 delta=0，recording 时间即公共坐标。
        # 多房未对齐已被 handler 前置拦截（allow_single_fallback=False）；
        # 单房场景房间从不对齐，clip_queued 也不会写 common 坐标，必须在此恒等映射。
        try:
            s = float(clip.get("start", 0))
            e = float(clip.get("end", 0))
        except (TypeError, ValueError):
            return None
        return (s, e, "exact") if e > s else None
    return None


def seconds_trange(start_sec: float, dur_sec: float) -> Any:
    """构造 pyJianYingDraft Timerange（秒 → 带单位字符串，避免微秒陷阱）。"""
    from pyJianYingDraft import trange  # type: ignore[import-untyped]

    return trange(f"{start_sec}s", f"{dur_sec}s")


def center_crop_9_16(*, width: int, height: int) -> Any:
    """16:9（或任意横屏）居中裁成 9:16 的 CropSettings（归一化 0~1）。"""
    from pyJianYingDraft import CropSettings  # type: ignore[import-untyped]

    if width <= 0 or height <= 0:
        return CropSettings()
    crop_w = height * 9 / 16
    if crop_w >= width:
        return CropSettings()
    x0 = (width - crop_w) / (2 * width)
    x1 = 1.0 - x0
    return CropSettings(
        upper_left_x=x0,
        upper_left_y=0.0,
        upper_right_x=x1,
        upper_right_y=0.0,
        lower_left_x=x0,
        lower_left_y=1.0,
        lower_right_x=x1,
        lower_right_y=1.0,
    )


def detect_jianying_draft_dir() -> str | None:
    local = os.environ.get("LOCALAPPDATA") or ""
    if not local:
        return None
    path = os.path.join(
        local, "JianyingPro", "User Data", "Projects", "com.lveditor.draft"
    )
    return path if os.path.isdir(path) else None


def validate_draft_dir(path: str) -> bool:
    if not path or not isinstance(path, str):
        return False
    try:
        os.makedirs(path, exist_ok=True)
        probe = os.path.join(path, ".lsc_write_probe")
        with open(probe, "w", encoding="utf-8") as f:
            f.write("ok")
        os.remove(probe)
        return True
    except OSError:
        return False


def _import_draft_lib() -> Any:
    import pyJianYingDraft as draft  # type: ignore[import-untyped]

    return draft


def _default_draft_name(main_name: str) -> str:
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    return sanitize_draft_token(f"LSC_{main_name}_{stamp}")


def _track_label(room: RoomDraftSource, suffix: str) -> str:
    return f"{sanitize_draft_token(room.name)}·{suffix}"


def build_session_draft(
    *,
    rooms: list[RoomDraftSource],
    clips: list[ClipDraftSource],
    options: JianyingDraftOptions,
    draft_root: str,
) -> JianyingDraftResult:
    warnings: list[str] = []
    # 导出器内部筛掉的切片（逐条留痕，供响应逐条对账）
    excluded_clips: list[dict[str, Any]] = []
    try:
        draft = _import_draft_lib()
    except ImportError:
        return JianyingDraftResult(
            success=False,
            error="未安装 pyJianYingDraft，请检查依赖",
            error_code="library_missing",
        )

    if not validate_draft_dir(draft_root):
        return JianyingDraftResult(
            success=False,
            error="剪映草稿目录不可写或不存在",
            error_code="write_failed" if draft_root else "draft_dir_missing",
        )

    usable: list[RoomDraftSource] = []
    for room in rooms:
        has_legacy_file = bool(room.record_output_path and os.path.isfile(room.record_output_path))
        has_manifest = bool(room.record_manifest_path and os.path.isfile(room.record_manifest_path))
        if (options.include_recordings or options.include_clips) and not (has_legacy_file or has_manifest):
            warnings.append(f"房间 {room.name} 无录制文件，已跳过")
            continue
        usable.append(room)
    if not usable:
        return JianyingDraftResult(
            success=False,
            error="没有可用的录制房间",
            error_code="no_rooms",
            warnings=warnings,
            excluded_clips=list(excluded_clips),
        )

    deltas = {r.room_id: r.recording_to_common_delta for r in usable}
    origin = compute_draft_origin(deltas)
    main = next((r for r in usable if r.is_main), usable[0])
    explicit_name = bool(options.draft_name)
    name = (
        sanitize_draft_token(options.draft_name)
        if explicit_name
        else _default_draft_name(main.name)
    )

    width, height = (1080, 1920) if options.vertical else (1920, 1080)
    draft_dir = os.path.join(draft_root, name)
    try:
        folder = draft.DraftFolder(draft_root)
        if not explicit_name:
            # 自动命名只精确到分钟：同一分钟内的两次导出会撞名并互相覆盖
            # （2026-09-12 09:01 现场：4 段的自动草稿被 3 段的手动导出顶掉）。
            # 自动命名一律避让；显式命名（前端"重试生成草稿"）仍按调用方意图覆盖。
            base = name
            suffix = 1
            while suffix <= 50 and folder.has_draft(name):
                suffix += 1
                name = f"{base}_{suffix}"
            if suffix > 1:
                draft_dir = os.path.join(draft_root, name)
                warnings.append(f"已存在同名草稿，本次写入「{name}」以免覆盖上一份")
        existed = folder.has_draft(name)
        script = folder.create_draft(name, width, height, allow_replace=True)
        if existed:
            warnings.append("已覆盖同名草稿，若剪映中已打开请先关闭")
    except Exception as exc:
        _log.warning("剪映草稿初始化失败，清理半成品目录: %s", exc, exc_info=True)
        shutil.rmtree(draft_dir, ignore_errors=True)
        return JianyingDraftResult(
            success=False,
            error=f"剪映草稿初始化失败: {exc}",
            error_code="draft_failed",
            warnings=warnings,
            excluded_clips=list(excluded_clips),
        )

    non_main = [r for r in usable if not r.is_main]
    ordered_rec = list(reversed(non_main)) + [main]
    specs = []
    TrackSpec = draft.TrackSpec
    TrackType = draft.TrackType
    if options.include_recordings:
        for r in ordered_rec:
            specs.append(TrackSpec(TrackType.video, _track_label(r, "录制")))
    if options.include_clips:
        for r in ordered_rec:
            specs.append(TrackSpec(TrackType.video, _track_label(r, "切片")))
    if options.text_labels and options.include_clips:
        specs.append(TrackSpec(TrackType.text, "回合标签"))
    if not specs:
        shutil.rmtree(draft_dir, ignore_errors=True)
        return JianyingDraftResult(
            success=False,
            error="没有可生成的轨道",
            error_code="invalid_state",
            warnings=warnings,
            excluded_clips=list(excluded_clips),
        )
    try:
        script.append_tracks(specs)
    except Exception as exc:
        _log.warning("剪映草稿轨道创建失败，清理半成品目录: %s", exc, exc_info=True)
        shutil.rmtree(draft_dir, ignore_errors=True)
        return JianyingDraftResult(
            success=False,
            error=f"剪映草稿轨道创建失败: {exc}",
            error_code="draft_failed",
            warnings=warnings,
            excluded_clips=list(excluded_clips),
        )

    SEC = draft.SEC
    segments = 0
    placed_clip_count = 0
    materials: dict[str, Any] = {}

    def _material_for(room: RoomDraftSource) -> Any:
        if room.room_id in materials:
            return materials[room.room_id]
        material_path = room.record_output_path
        if room.record_manifest_path:
            try:
                from lsc.config import load_config
                from lsc.recorder.assets import RecordingAsset

                cfg = load_config()
                material_path = RecordingAsset.recover(
                    room.record_manifest_path
                ).materialize_persistent(
                    ffmpeg_path=cfg.ffmpeg_path,
                    ffprobe_path=cfg.ffprobe_path,
                )
            except (FileNotFoundError, OSError, RuntimeError) as exc:
                raise RuntimeError(
                    f"房间 {room.name} 的分段录制无法作为剪映素材: {exc}"
                ) from exc
        if options.vertical:
            raw = draft.VideoMaterial(material_path)
            crop = center_crop_9_16(width=raw.width, height=raw.height)
            mat = draft.VideoMaterial(material_path, crop_settings=crop)
        else:
            mat = draft.VideoMaterial(material_path)
        materials[room.room_id] = mat
        return mat

    try:
        if options.include_recordings:
            for r in ordered_rec:
                mat = _material_for(r)
                dur_sec = mat.duration / SEC
                t0, td, s0, sd = map_recording_timeranges(
                    recording_to_common_delta=r.recording_to_common_delta,
                    draft_origin=origin,
                    dur_sec=dur_sec,
                )
                vol = 0.0 if (options.non_main_volume_zero and not r.is_main) else 1.0
                seg = draft.VideoSegment(
                    mat,
                    seconds_trange(t0, td),
                    source_timerange=seconds_trange(s0, sd),
                    volume=vol,
                )
                script.add_segment(seg, _track_label(r, "录制"))
                segments += 1

        usable_clips: list[ClipDraftSource] = []
        for c in clips:
            usable = clip_source_usable(
                precision=c.precision,
                confirm_status=c.confirm_status,
                include_pending=options.include_pending,
                source_profile=c.source_profile,
                broadcast_audit=c.broadcast_audit,
                broadcast_review_required=c.broadcast_review_required,
                start_quality=c.start_quality,
                end_quality=c.end_quality,
                start_review_required=c.start_review_required,
                end_review_required=c.end_review_required,
                duration_anomaly=c.duration_anomaly,
                end_by=c.end_by,
            )
            if usable:
                usable_clips.append(c)
                continue
            # 逐条留痕：此前只有一句聚合告警、连标签都不带，导出侧无法对账
            # （L3 实测 requested−included=6 而明细只有 5 条、找不到是谁）。
            excluded_clips.append({
                "clip_id": c.clip_id,
                "label": c.label,
                "room_id": c.room_id,
                "start": c.common_start,
                "end": c.common_end,
                "reason_code": "EXCLUDED_BY_SOURCE_FILTER",
                "reason": "未通过导出器源可用性过滤（pending/近似定位/未通过赛事审计）",
                "confirm_status": c.confirm_status,
                "broadcast_audit": c.broadcast_audit,
                "end_by": c.end_by,
                "end_quality": c.end_quality,
            })
            warnings.append(f"导出器排除切片「{c.label}」（源可用性过滤）")
        skipped = len(clips) - len(usable_clips)
        if skipped:
            warnings.append(f"已排除 {skipped} 条 pending/approximate 或未通过赛事审计的切片")

        if options.include_clips:
            # 每条切片只进「其所属房间」的切片轨：主/副房同回合切片在公共轴上的
            # 位置相差 media_start 差（秒级），若全部房间轨都添加全部切片，必然
            # 相互重叠 → pyJianYingDraft SegmentOverlap → save() 未执行 → 草稿
            # 目录不完整，剪映能看到但打不开。room_id 缺失的旧数据仅进主房轨。
            for r in ordered_rec:
                mat = _material_for(r)
                dur_sec = mat.duration / SEC
                for c in usable_clips:
                    if c.room_id is not None and c.room_id != r.room_id:
                        continue
                    if c.room_id is None and not r.is_main:
                        continue
                    mapped = map_clip_timeranges(
                        c.common_start,
                        c.common_end,
                        r.recording_to_common_delta,
                        origin,
                        dur_sec=dur_sec,
                    )
                    if mapped is None:
                        warnings.append(
                            f"房间 {r.name} 无此时段素材或片段过短，已跳过「{c.label}」"
                        )
                        continue
                    t0, td, s0, sd, clamped = mapped
                    if clamped:
                        warnings.append(
                            f"房间 {r.name} 片段「{c.label}」已按当前文件时长裁剪"
                        )
                    vol = 0.0 if (options.non_main_volume_zero and not r.is_main) else 1.0
                    seg = draft.VideoSegment(
                        mat,
                        seconds_trange(t0, td),
                        source_timerange=seconds_trange(s0, sd),
                        volume=vol,
                    )
                    try:
                        script.add_segment(seg, _track_label(r, "切片"))
                    except Exception as exc:
                        # 防御：未知边界的重叠段跳过并告警，保证草稿仍可保存打开。
                        # 逐条留痕必须进 excluded_clips：只写 warnings 时响应的
                        # skipped_unaccounted 残差 > 0，对不上账（2026-09-13 现场
                        # 收尾补扫的 4 条重复候选就是这么"消失"的）。
                        excluded_clips.append({
                            "clip_id": c.clip_id,
                            "label": c.label,
                            "room_id": c.room_id,
                            "start": c.common_start,
                            "end": c.common_end,
                            "reason_code": "OVERLAP_DEDUP",
                            "reason": f"与同轨已有片段重叠，已去重跳过: {exc}",
                            "confirm_status": c.confirm_status,
                            "broadcast_audit": c.broadcast_audit,
                            "end_by": c.end_by,
                            "end_quality": c.end_quality,
                        })
                        warnings.append(
                            f"房间 {r.name} 片段「{c.label}」与其它片段重叠，已跳过: {exc}"
                        )
                        continue
                    segments += 1
                    placed_clip_count += 1

            if options.text_labels:
                # 回合标签轨：主/副房同回合标签在公共轴上错位（media_start 差），
                # 按时间去重只保留第一条，避免同轨重叠导致草稿保存失败。
                text_segs: list[tuple[float, float, str]] = []
                for c in usable_clips:
                    t0 = c.common_start - origin
                    td = c.common_end - c.common_start
                    if td <= _MIN_SEG_SEC:
                        continue
                    overlap = False
                    for et0, etd, _elabel in text_segs:
                        if not (t0 + td <= et0 or et0 + etd <= t0):
                            overlap = True
                            break
                    if overlap:
                        continue
                    text_segs.append((t0, td, c.label or "回合"))
                for t0, td, label in text_segs:
                    script.add_segment(
                        draft.TextSegment(label, seconds_trange(t0, td)),
                        "回合标签",
                    )
                    segments += 1

        script.save()
    except Exception as exc:
        # 任何构建/保存失败都要清理半成品目录，避免剪映注册损坏草稿
        _log.warning("剪映草稿生成失败，清理半成品目录: %s", exc, exc_info=True)
        shutil.rmtree(draft_dir, ignore_errors=True)
        return JianyingDraftResult(
            success=False,
            error=f"剪映草稿生成失败: {exc}",
            error_code="draft_failed",
            warnings=warnings,
            excluded_clips=list(excluded_clips),
        )
    return JianyingDraftResult(
        success=True,
        draft_name=name,
        draft_dir=draft_dir,
        tracks=len(specs),
        segments=segments,
        placed_clip_count=placed_clip_count if options.include_clips else 0,
        excluded_clips=list(excluded_clips),
        warnings=warnings,
    )
