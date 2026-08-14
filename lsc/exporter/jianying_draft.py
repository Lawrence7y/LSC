from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from lsc.core.models import JianyingDraftOptions, JianyingDraftResult

_log = logging.getLogger(__name__)

_ILLEGAL = re.compile(r'[<>:"/\\|?*]')
_MIN_SEG_SEC = 0.2


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


def clip_source_usable(
    *,
    precision: str,
    confirm_status: str | None,
    include_pending: bool = False,
) -> bool:
    if precision == "approximate":
        return False
    return not (confirm_status in ("pending", "refining") and not include_pending)


def clip_allowed_for_draft(clip: dict, *, include_pending: bool = False) -> bool:
    """WS/前端切片 dict 是否允许进入草稿（与导出口径一致）。"""
    status = clip.get("confirm_status")
    if status in ("pending", "refining") and not include_pending:
        return False
    return clip.get("mark_precision") != "approximate"


def resolve_common_range(clip: dict[str, Any], ctx: Any) -> tuple[float, float, str] | None:
    """返回 (common_start, common_end, precision) 或 None（无法映射）。"""
    cs = clip.get("common_start")
    ce = clip.get("common_end")
    if cs is not None and ce is not None:
        return float(cs), float(ce), "exact"
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
        )

    deltas = {r.room_id: r.recording_to_common_delta for r in usable}
    origin = compute_draft_origin(deltas)
    main = next((r for r in usable if r.is_main), usable[0])
    name = (
        sanitize_draft_token(options.draft_name)
        if options.draft_name
        else _default_draft_name(main.name)
    )

    width, height = (1080, 1920) if options.vertical else (1920, 1080)
    folder = draft.DraftFolder(draft_root)
    existed = folder.has_draft(name)
    script = folder.create_draft(name, width, height, allow_replace=True)
    if existed:
        warnings.append("已覆盖同名草稿，若剪映中已打开请先关闭")

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
        return JianyingDraftResult(
            success=False,
            error="没有可生成的轨道",
            error_code="invalid_state",
            warnings=warnings,
        )
    script.append_tracks(specs)

    SEC = draft.SEC
    segments = 0
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

    usable_clips = [
        c
        for c in clips
        if clip_source_usable(
            precision=c.precision,
            confirm_status=c.confirm_status,
            include_pending=options.include_pending,
        )
    ]
    skipped = len(clips) - len(usable_clips)
    if skipped:
        warnings.append(f"已排除 {skipped} 条 pending/approximate 切片")

    if options.include_clips:
        for r in ordered_rec:
            mat = _material_for(r)
            dur_sec = mat.duration / SEC
            for c in usable_clips:
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
                script.add_segment(seg, _track_label(r, "切片"))
                segments += 1

        if options.text_labels:
            for c in usable_clips:
                t0 = c.common_start - origin
                td = c.common_end - c.common_start
                if td <= _MIN_SEG_SEC:
                    continue
                script.add_segment(
                    draft.TextSegment(c.label or "回合", seconds_trange(t0, td)),
                    "回合标签",
                )
                segments += 1

    script.save()
    draft_dir = os.path.join(draft_root, name)
    return JianyingDraftResult(
        success=True,
        draft_name=name,
        draft_dir=draft_dir,
        tracks=len(specs),
        segments=segments,
        warnings=warnings,
    )
