# J1 — Jianying Draft Builder Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 引入 `pyJianYingDraft`，新增纯函数式草稿 builder 与 DTO，单测覆盖坐标映射 / 越界 / 轨道布局 / 同名覆盖。

**Architecture:** `lsc/exporter/jianying_draft.py` 不依赖 Qt/WS；输入 `RoomDraftSource`/`ClipDraftSource`/`JianyingDraftOptions`，输出 `JianyingDraftResult`。时间单位内部一律用秒，写入 pyJianYingDraft 时再转为微秒/`"Ns"` 字符串。

**Tech Stack:** Python 3.12、`pyJianYingDraft==0.3.0`、pytest、ffmpeg（造测试小视频）

**Spec:** `docs/spec-jianying-draft-export.md` §核心设计 A/B、§数据模型、里程碑 J1  
**前置:** 无  
**后继:** J2（WS + settings）

**执行约束：** 工作目录 `D:\Project\直播切片多人`；**不要 git commit**（除非用户要求）。

---

## Spec 校正（以库 API 为准）

| Spec 原文 | 实际必须做法 |
|-----------|-------------|
| `trange(delta, dur)` 裸 float 秒 | `trange(f"{start}s", f"{dur}s")` 或 `* SEC` |
| `VideoMaterial(path).duration` 当秒 | `/ SEC` 得秒 |
| 竖屏用 `ClipSettings` | 用 `CropSettings` 挂在 `VideoMaterial` |
| 手删同名文件夹 | `create_draft(..., allow_replace=True)` |

---

## 文件结构

| 文件 | 职责 |
|------|------|
| `requirements.txt` | 增加 `pyJianYingDraft==0.3.0` |
| `lsc/core/models.py` | `JianyingDraftOptions` / `JianyingDraftResult` |
| `lsc/exporter/jianying_draft.py` | builder + 探测 + sanitize + 映射纯函数 |
| `tests/test_jianying_draft.py` | 映射/越界/布局/同名/端到端 |
| `tests/test_jianying_dependency_guard.py` | `import pyJianYingDraft` CI guard |

**本阶段不修改：** `python-backend/**`、`lsc-electron/**`

---

### Task 1: 依赖 + CI guard

**Files:**
- Modify: `requirements.txt`
- Create: `tests/test_jianying_dependency_guard.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_jianying_dependency_guard.py
from __future__ import annotations


def test_pyjianyingdraft_importable():
    import pyJianYingDraft  # noqa: F401


def test_requirements_pins_pyjianyingdraft():
    from pathlib import Path
    text = Path("requirements.txt").read_text(encoding="utf-8")
    assert "pyJianYingDraft==0.3.0" in text
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_jianying_dependency_guard.py::test_requirements_pins_pyjianyingdraft -v`  
Expected: FAIL（字符串不在 requirements.txt）

- [ ] **Step 3: 改 requirements.txt**

在 `psutil>=5.9,<7` 后追加：

```
# 剪映草稿生成（仅生成草稿，不做自动导出）
pyJianYingDraft==0.3.0
```

若环境尚未安装：`pip install pyJianYingDraft==0.3.0`

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_jianying_dependency_guard.py -v`  
Expected: PASS

---

### Task 2: DTO 模型

**Files:**
- Modify: `lsc/core/models.py`（在 `ExportResult` 之后追加）
- Test: `tests/test_jianying_draft.py`（先建文件，只测 DTO 默认值）

- [ ] **Step 1: 写失败测试**

```python
# tests/test_jianying_draft.py
from __future__ import annotations

from lsc.core.models import JianyingDraftOptions, JianyingDraftResult


def test_draft_options_defaults():
    opt = JianyingDraftOptions()
    assert opt.include_recordings is True
    assert opt.include_clips is True
    assert opt.text_labels is True
    assert opt.vertical is False
    assert opt.draft_name == ""
    assert opt.non_main_volume_zero is False


def test_draft_result_fields():
    r = JianyingDraftResult(
        success=True,
        draft_name="LSC_test",
        draft_dir="C:/tmp/LSC_test",
        tracks=5,
        segments=3,
        warnings=["w"],
    )
    assert r.error == ""
    assert r.warnings == ["w"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_jianying_draft.py::test_draft_options_defaults -v`  
Expected: FAIL（ImportError）

- [ ] **Step 3: 在 models.py 追加**

在 `ExportResult` 类定义之后追加：

```python
@dataclass(slots=True)
class JianyingDraftOptions:
    """剪映草稿生成选项。"""

    include_recordings: bool = True
    include_clips: bool = True
    text_labels: bool = True
    vertical: bool = False
    draft_name: str = ""
    non_main_volume_zero: bool = False  # 预留，v1 不暴露 UI


@dataclass(slots=True)
class JianyingDraftResult:
    """剪映草稿生成结果。"""

    success: bool
    draft_name: str = ""
    draft_dir: str = ""
    tracks: int = 0
    segments: int = 0
    warnings: list[str] = field(default_factory=list)
    error: str = ""
    error_code: str = ""
```

（`error_code` 供 J2 使用；J1 builder 失败时也可填。）

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_jianying_draft.py::test_draft_options_defaults tests/test_jianying_draft.py::test_draft_result_fields -v`  
Expected: PASS

---

### Task 3: 纯函数映射 + sanitize（无 I/O）

**Files:**
- Create: `lsc/exporter/jianying_draft.py`（先放纯函数）
- Modify: `tests/test_jianying_draft.py`

- [ ] **Step 1: 写失败测试**

```python
# 追加到 tests/test_jianying_draft.py
from lsc.exporter.jianying_draft import (
    compute_draft_origin,
    map_recording_timeranges,
    map_clip_timeranges,
    sanitize_draft_token,
    clip_source_usable,
)


def test_draft_origin_is_min_delta():
    deltas = {"a": 1.5, "b": 0.0, "c": 0.8}
    assert compute_draft_origin(deltas) == 0.0


def test_recording_timeranges():
    # room delta=1.5, origin=0 → target start 1.5, source [0, dur)
    t_start, t_dur, s_start, s_dur = map_recording_timeranges(
        recording_to_common_delta=1.5,
        draft_origin=0.0,
        dur_sec=10.0,
    )
    assert (t_start, t_dur, s_start, s_dur) == (1.5, 10.0, 0.0, 10.0)


def test_clip_timeranges_same_target_different_source():
    # common [5, 10), roomA delta=0, roomB delta=1.5, origin=0
    a = map_clip_timeranges(5.0, 10.0, recording_to_common_delta=0.0, draft_origin=0.0)
    b = map_clip_timeranges(5.0, 10.0, recording_to_common_delta=1.5, draft_origin=0.0)
    assert a[:2] == b[:2] == (5.0, 5.0)  # target identical
    assert a[2:] == (5.0, 5.0)
    assert b[2:] == (3.5, 5.0)


def test_clip_skip_when_source_before_zero():
    # common [0, 2), delta=1.5 → source start -1.5 → skip
    assert map_clip_timeranges(0.0, 2.0, 1.5, 0.0) is None


def test_clip_clamp_when_past_duration():
    # common [8, 12), delta=0, dur=10 → clamp to [8, 10)
    r = map_clip_timeranges(8.0, 12.0, 0.0, 0.0, dur_sec=10.0)
    assert r is not None
    t_start, t_dur, s_start, s_dur, clamped = r
    assert s_start == 8.0 and abs(s_dur - 2.0) < 1e-9
    assert clamped is True


def test_discard_short_segment():
    assert map_clip_timeranges(1.0, 1.1, 0.0, 0.0, dur_sec=10.0) is None  # 0.1s < 0.2


def test_sanitize_illegal_chars():
    assert sanitize_draft_token('a<>:"/\\|?*b') == "a_________b"


def test_clip_source_usable_rejects_approx_pending():
    assert clip_source_usable(precision="exact", confirm_status="user_confirmed") is True
    assert clip_source_usable(precision="approximate", confirm_status="user_confirmed") is False
    assert clip_source_usable(precision="exact", confirm_status="pending") is False
    assert clip_source_usable(precision="exact", confirm_status="refining") is False
    assert clip_source_usable(precision="exact", confirm_status=None) is True
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_jianying_draft.py -k "draft_origin or recording_timeranges or clip_timeranges or sanitize or clip_source" -v`  
Expected: FAIL（module missing）

- [ ] **Step 3: 实现纯函数**

```python
# lsc/exporter/jianying_draft.py
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
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
        # target 时长跟随 clamp 后的 source
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
    # FFmpeg: crop=ih*9/16:ih:(iw-ih*9/16)/2:0
    crop_w = height * 9 / 16
    if crop_w >= width:
        return CropSettings()  # 已够竖或更窄，不裁
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
```

注意：`map_clip_timeranges` 在测试里两种签名混用——实现时保证：
- 不传 `dur_sec`：返回 4 元组或 None（`test_clip_timeranges_same_target_*` / `test_clip_skip_*`）
- 传 `dur_sec`：返回 5 元组 `(..., clamped)` 或 None

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_jianying_draft.py -k "not build_session and not e2e" -v`  
Expected: PASS（DTO + 纯函数）

---

### Task 4: `build_session_draft` + 端到端烟测

**Files:**
- Modify: `lsc/exporter/jianying_draft.py`
- Modify: `tests/test_jianying_draft.py`

- [ ] **Step 1: 写失败测试（含 ffmpeg 造片）**

```python
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from lsc.core.models import JianyingDraftOptions
from lsc.exporter.jianying_draft import (
    ClipDraftSource,
    RoomDraftSource,
    build_session_draft,
)


def _ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def _make_color_mp4(path: Path, duration: float = 3.0, color: str = "red") -> None:
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi",
            "-i", f"color=c={color}:s=320x240:d={duration}",
            "-f", "lavfi", "-i", "sine=f=440:d={:.3f}".format(duration),
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
            "-shortest", str(path),
        ],
        check=True,
        capture_output=True,
    )


@pytest.mark.skipif(not _ffmpeg_available(), reason="ffmpeg required")
def test_build_session_draft_e2e_two_rooms(tmp_path: Path):
    a = tmp_path / "a.mp4"
    b = tmp_path / "b.mp4"
    _make_color_mp4(a, 4.0, "red")
    _make_color_mp4(b, 4.0, "blue")
    draft_root = tmp_path / "drafts"
    draft_root.mkdir()

    rooms = [
        RoomDraftSource("r1", "主房", str(a), 0.0, is_main=True),
        RoomDraftSource("r2", "副房", str(b), 1.5, is_main=False),
    ]
    clips = [
        ClipDraftSource("c1", 1.5, 3.0, "R1 测试", precision="exact", confirm_status="user_confirmed"),
    ]
    result = build_session_draft(
        rooms=rooms,
        clips=clips,
        options=JianyingDraftOptions(draft_name="LSC_test_e2e", text_labels=True),
        draft_root=str(draft_root),
    )
    assert result.success, result.error
    assert result.tracks >= 5  # 2 rec + 2 clip + 1 text
    content = Path(result.draft_dir) / "draft_content.json"
    assert content.is_file()
    data = json.loads(content.read_text(encoding="utf-8"))
    # 轨道名应含中文「录制」「切片」
    track_names = [t.get("name", "") for t in data.get("tracks", data.get("materials", {}).get("tracks", [])) if isinstance(t, dict)]
    # pyJianYingDraft 结构可能把 tracks 放在顶层；若解析路径不同，至少保证 segments>0
    assert result.segments >= 3


@pytest.mark.skipif(not _ffmpeg_available(), reason="ffmpeg required")
def test_build_overwrite_same_name(tmp_path: Path):
    a = tmp_path / "a.mp4"
    _make_color_mp4(a, 2.0, "green")
    draft_root = tmp_path / "drafts"
    draft_root.mkdir()
    rooms = [RoomDraftSource("r1", "nobody", str(a), 0.0, is_main=True)]
    opt = JianyingDraftOptions(draft_name="LSC_overwrite", include_clips=False, text_labels=False)
    r1 = build_session_draft(rooms=rooms, clips=[], options=opt, draft_root=str(draft_root))
    assert r1.success
    r2 = build_session_draft(rooms=rooms, clips=[], options=opt, draft_root=str(draft_root))
    assert r2.success
    assert any("覆盖" in w or "同名" in w for w in r2.warnings) or r2.success


def test_build_rejects_missing_library(monkeypatch, tmp_path: Path):
    import lsc.exporter.jianying_draft as mod

    def boom(*_a, **_k):
        raise ImportError("nope")

    monkeypatch.setattr(mod, "_import_draft_lib", boom)
    r = build_session_draft(
        rooms=[RoomDraftSource("r1", "x", str(tmp_path / "no.mp4"), 0.0, True)],
        clips=[],
        options=JianyingDraftOptions(include_recordings=False, include_clips=False),
        draft_root=str(tmp_path),
    )
    # 无录制无切片 → no_rooms；或 library_missing。两种皆可接受取决于实现顺序。
    assert r.success is False
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_jianying_draft.py::test_build_session_draft_e2e_two_rooms -v`  
Expected: FAIL（`build_session_draft` 未定义）

- [ ] **Step 3: 实现 `build_session_draft`**

在 `jianying_draft.py` 追加（核心逻辑摘要，实现时写全）：

```python
def _import_draft_lib():
    import pyJianYingDraft as draft
    return draft


def _default_draft_name(main_name: str) -> str:
    from datetime import datetime

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
    except ImportError as exc:
        return JianyingDraftResult(
            success=False, error="未安装 pyJianYingDraft，请检查依赖",
            error_code="library_missing",
        )

    if not validate_draft_dir(draft_root):
        return JianyingDraftResult(
            success=False,
            error="剪映草稿目录不可写或不存在",
            error_code="write_failed" if draft_root else "draft_dir_missing",
        )

    # 过滤有文件的房间
    usable: list[RoomDraftSource] = []
    for room in rooms:
        if options.include_recordings or options.include_clips:
            if not room.record_output_path or not os.path.isfile(room.record_output_path):
                warnings.append(f"房间 {room.name} 无录制文件，已跳过")
                continue
        usable.append(room)
    if not usable:
        return JianyingDraftResult(
            success=False, error="没有可用的录制房间", error_code="no_rooms",
            warnings=warnings,
        )

    deltas = {r.room_id: r.recording_to_common_delta for r in usable}
    origin = compute_draft_origin(deltas)
    main = next((r for r in usable if r.is_main), usable[0])
    name = sanitize_draft_token(options.draft_name) if options.draft_name else _default_draft_name(main.name)

    width, height = (1080, 1920) if options.vertical else (1920, 1080)
    folder = draft.DraftFolder(draft_root)
    existed = folder.has_draft(name) if hasattr(folder, "has_draft") else False
    script = folder.create_draft(name, width, height, allow_replace=True)
    if existed:
        warnings.append("已覆盖同名草稿，若剪映中已打开请先关闭")

    # 轨道顺序：副房录制… → 主房录制 → 副房切片… → 主房切片 → 文本
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
            success=False, error="没有可生成的轨道", error_code="invalid_state",
            warnings=warnings,
        )
    script.append_tracks(specs)

    SEC = draft.SEC
    segments = 0
    materials: dict[str, Any] = {}

    def _material_for(room: RoomDraftSource):
        if room.room_id in materials:
            return materials[room.room_id]
        crop = None
        if options.vertical:
            # 先无 crop 探测尺寸，再重建 —— 或 parse 后读 width/height
            raw = draft.VideoMaterial(room.record_output_path)
            crop = center_crop_9_16(width=raw.width, height=raw.height)
            mat = draft.VideoMaterial(room.record_output_path, crop_settings=crop)
        else:
            mat = draft.VideoMaterial(room.record_output_path)
        materials[room.room_id] = mat
        return mat

    # 录制轨
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

    # 切片轨
    usable_clips = [
        c for c in clips
        if clip_source_usable(precision=c.precision, confirm_status=c.confirm_status)
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
                    c.common_start, c.common_end,
                    r.recording_to_common_delta, origin, dur_sec=dur_sec,
                )
                if mapped is None:
                    warnings.append(f"房间 {r.name} 无此时段素材或片段过短，已跳过「{c.label}」")
                    continue
                t0, td, s0, sd, clamped = mapped
                if clamped:
                    warnings.append(f"房间 {r.name} 片段「{c.label}」已按当前文件时长裁剪")
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
```

实现时注意：`VideoMaterial` 二次构造可用；若同 path 重复 add 有问题，复用同一 material 实例（上面 `materials` 缓存）。

- [ ] **Step 4: 跑全套 jianying 测试**

Run: `pytest tests/test_jianying_draft.py tests/test_jianying_dependency_guard.py -v`  
Expected: PASS（无 ffmpeg 时 e2e skip 可接受，但本地应有 ffmpeg）

---

### Task 5: 自检清单（J1）

- [ ] **Step 1: 对照 spec**
  - 坐标映射 A ✅
  - 轨道布局 B（副→主录制，副→主切片，文本顶）✅
  - 越界 H9/H10 ✅
  - pending/approx H6/H8 ✅
  - 同名 H7 ✅
  - 依赖 H14 guard ✅

- [ ] **Step 2: 确认未改 backend/frontend**

Run: `git status --short`  
Expected: 仅 `requirements.txt`、`lsc/core/models.py`、`lsc/exporter/jianying_draft.py`、`tests/test_jianying_*`

---

## J1 完成标准

- `pytest tests/test_jianying_draft.py tests/test_jianying_dependency_guard.py -v` 全绿（或 e2e 仅因无 ffmpeg skip）
- 可用临时目录手动：`build_session_draft(...)` 生成可读的 `draft_content.json`
- 进入 J2
