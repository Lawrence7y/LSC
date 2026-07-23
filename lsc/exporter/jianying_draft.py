from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass

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
) -> tuple[float, float, float, float, bool] | tuple[float, float, float, float] | None:
    """
    成功时返回 (t_start, t_dur, s_start, s_dur[, clamped])。
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
    if dur_sec is not None:
        return (t_start, s_dur, s_start, s_dur, clamped)
    return (t_start, s_dur, s_start, s_dur)


def clip_source_usable(*, precision: str, confirm_status: str | None) -> bool:
    if precision == "approximate":
        return False
    if confirm_status in ("pending", "refining"):
        return False
    return True


def seconds_trange(start_sec: float, dur_sec: float):
    """构造 pyJianYingDraft Timerange（秒 → 带单位字符串，避免微秒陷阱）。"""
    from pyJianYingDraft import trange

    return trange(f"{start_sec}s", f"{dur_sec}s")


def center_crop_9_16(*, width: int, height: int):
    """16:9（或任意横屏）居中裁成 9:16 的 CropSettings（归一化 0~1）。"""
    from pyJianYingDraft import CropSettings

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
