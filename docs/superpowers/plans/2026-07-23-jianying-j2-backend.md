# J2 — Jianying Draft Backend (WS + Settings) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 暴露 `generate_jianying_draft` / `get_jianying_draft_dir` WebSocket API；装配 TimelineContext、录制路径、切片资格；settings 增加 `jianying_draft_dir`。

**Architecture:** 新建 `python-backend/handlers/jianying_handlers.py`（仿 `timeline_handlers` 注入依赖），在 `register_room_handlers` 末尾注册。生成在线程池执行（`VideoMaterial` I/O），同步响应，**不入**导出队列。

**Tech Stack:** Python 3.12、asyncio、`get_timeline_service()`、现有 `load_settings`/`save_settings`

**Spec:** `docs/spec-jianying-draft-export.md` §后端实现、§边界 H1–H6/H14–H15、里程碑 J2  
**前置:** J1 完成（`build_session_draft` 可用）  
**后继:** J3（前端）

**执行约束：** 工作目录 `D:\Project\直播切片多人`；**不要 git commit**（除非用户要求）。**不要**修改既有 `export_clip` / `queue_export` 行为。

---

## 文件结构

| 文件 | 职责 |
|------|------|
| `python-backend/handlers/jianying_handlers.py` | WS handlers + 装配逻辑 |
| `python-backend/handlers/room_handler.py` | `load_settings` 默认键；末尾 `register_jianying_handlers(...)` |
| `tests/test_jianying_ws_guards.py` | payload / error_code / 装配边界 guard |

---

### Task 1: settings 键 `jianying_draft_dir`

**Files:**
- Modify: `python-backend/handlers/room_handler.py` — `load_settings` 默认 dict
- Test: `tests/test_jianying_ws_guards.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_jianying_ws_guards.py
from __future__ import annotations

from pathlib import Path


def test_load_settings_default_includes_jianying_draft_dir():
    text = Path("python-backend/handlers/room_handler.py").read_text(encoding="utf-8")
    # 默认 dict 字面量中应出现键（允许空字符串默认）
    assert "'jianying_draft_dir'" in text or '"jianying_draft_dir"' in text
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_jianying_ws_guards.py::test_load_settings_default_includes_jianying_draft_dir -v`  
Expected: FAIL

- [ ] **Step 3: 在 `load_settings` 默认 dict 增加**

在 `room_handler.py` 约 2409–2425 的默认 `_settings_cache = { ... }` 中追加：

```python
        'jianying_draft_dir': '',  # 空 = 自动探测 %LOCALAPPDATA%\JianyingPro\...
```

在 `handle_get_settings` 的 backfill 逻辑旁（若有逐键补全）同样补上 `jianying_draft_dir`，避免旧 settings.json 缺键。

`handle_save_settings`：**不要**用 `_is_allowed_output_dir` 限制草稿目录（LOCALAPPDATA 不在 `~/LSC` 下）。仅校验：若非空则 `os.path.isdir` 或可创建；非法时返回友好错误。

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_jianying_ws_guards.py::test_load_settings_default_includes_jianying_draft_dir -v`  
Expected: PASS

---

### Task 2: `jianying_handlers.py` — get_jianying_draft_dir

**Files:**
- Create: `python-backend/handlers/jianying_handlers.py`
- Modify: `python-backend/handlers/room_handler.py`（末尾注册）
- Modify: `tests/test_jianying_ws_guards.py`

- [ ] **Step 1: 写失败测试**

```python
def test_jianying_handlers_module_exists():
    from handlers import jianying_handlers  # noqa: F401


def test_register_exports_expected_message_names():
    text = Path("python-backend/handlers/jianying_handlers.py").read_text(encoding="utf-8")
    assert "generate_jianying_draft" in text
    assert "get_jianying_draft_dir" in text
    for code in (
        "draft_dir_missing",
        "no_rooms",
        "no_aligned_context",
        "library_missing",
        "write_failed",
        "invalid_state",
    ):
        assert code in text
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_jianying_ws_guards.py::test_jianying_handlers_module_exists -v`  
Expected: FAIL

- [ ] **Step 3: 实现模块骨架 + get handler**

```python
# python-backend/handlers/jianying_handlers.py
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
    detect_jianying_draft_dir,
    validate_draft_dir,
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


def register_jianying_handlers(
    server,
    *,
    bridge,
    manager,
    load_settings: Callable[[], dict],
    run_in_executor: Callable | None = None,
) -> None:
    """注册剪映相关 WS。run_in_executor 默认用 asyncio 默认 executor。"""

    @server.on("get_jianying_draft_dir")
    async def handle_get_jianying_draft_dir(data):
        settings = load_settings()
        path, auto = _resolve_draft_root(settings)
        exists = bool(path and os.path.isdir(path))
        return {
            "success": True,
            "draft_dir": path or "",
            "auto_detected": auto and bool(path),
            "exists": exists,
        }

    # generate handler 在 Task 3 追加
```

在 `room_handler.py` 的 `register_room_handlers` 末尾（`register_timeline_handlers(...)` 之后）追加：

```python
    from handlers.jianying_handlers import register_jianying_handlers
    register_jianying_handlers(
        server,
        bridge=bridge,
        manager=manager,
        load_settings=load_settings,
    )
```

注意：`python-backend` 运行时 cwd/`sys.path` 已含 `handlers` 包（与 `timeline_handlers` 同级 import 风格一致）。

- [ ] **Step 4: 跑测试**

Run: `pytest tests/test_jianying_ws_guards.py -v`  
Expected: `test_jianying_handlers_module_exists` PASS；`test_register_exports_expected_message_names` 可能仍 FAIL（缺 generate / error codes）——Task 3 补齐。

---

### Task 3: `generate_jianying_draft` 装配

**Files:**
- Modify: `python-backend/handlers/jianying_handlers.py`
- Modify: `tests/test_jianying_ws_guards.py`

- [ ] **Step 1: 写装配单元测试（纯函数抽出）**

把装配逻辑抽成可测函数，避免硬启动 Qt manager：

```python
# 在 jianying_handlers.py 增加

def resolve_common_range(clip: dict, ctx, room) -> tuple[float, float, str] | None:
    """返回 (common_start, common_end, precision) 或 None（approximate）。"""
    cs = clip.get("common_start")
    ce = clip.get("common_end")
    if cs is not None and ce is not None:
        return float(cs), float(ce), "exact"
    mark_in = clip.get("mark_in_wallclock")
    mark_out = clip.get("mark_out_wallclock")
    # media_start_mono 优先取 timeline snapshot
    media_starts = []
    if ctx is not None:
        media_starts = [
            s.media_start_mono
            for s in ctx.room_snapshots.values()
            if s.media_start_mono
        ]
    if mark_in is not None and mark_out is not None and media_starts:
        origin = min(media_starts)
        return float(mark_in) - origin, float(mark_out) - origin, "exact"
    return None


def clip_allowed_for_draft(clip: dict) -> bool:
    status = clip.get("confirm_status")
    if status in ("pending", "refining"):
        return False
    if clip.get("mark_precision") == "approximate":
        return False
    return True
```

测试：

```python
from handlers.jianying_handlers import clip_allowed_for_draft, resolve_common_range


def test_clip_allowed_rejects_pending_and_approx():
    assert clip_allowed_for_draft({"confirm_status": "pending"}) is False
    assert clip_allowed_for_draft({"mark_precision": "approximate"}) is False
    assert clip_allowed_for_draft({"confirm_status": "user_confirmed", "mark_precision": "exact"}) is True
    assert clip_allowed_for_draft({}) is True


def test_resolve_common_range_prefers_common_fields():
    r = resolve_common_range({"common_start": 1.0, "common_end": 2.5}, None, None)
    assert r == (1.0, 2.5, "exact")
```

> 若 `handlers` 包在 pytest 根目录不可 import，改用：
> `from pathlib import Path` 把 `python-backend` 插入 `sys.path`，或把纯函数放到 `lsc/exporter/jianying_draft.py` 再测（推荐：装配校验函数放 `lsc` 侧更稳）。
> **本 plan 约定：** `clip_allowed_for_draft` / 坐标解析若遇 path 问题，移到 `lsc/exporter/jianying_draft.py` 并从此处 import。

- [ ] **Step 2: 实现完整 generate handler**

在 `register_jianying_handlers` 内追加：

```python
    @server.on("generate_jianying_draft")
    async def handle_generate_jianying_draft(data):
        data = data or {}
        settings = load_settings()
        draft_root, _auto = _resolve_draft_root(settings)
        if not draft_root or not os.path.isdir(draft_root):
            return {
                "success": False,
                "error": "剪映草稿目录未找到，请到设置页配置",
                "error_code": "draft_dir_missing",
            }

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

        # 通过 bridge 在 Qt 主线程读 manager 房间状态
        def _collect():
            warnings: list[str] = []
            timeline_svc = get_timeline_service()
            all_rooms = manager.get_rooms() if hasattr(manager, "get_rooms") else []
            # 兼容：若 API 是 list_rooms / rooms dict，按现有 manager 实际方法调整
            room_map = {}
            if isinstance(all_rooms, dict):
                room_map = all_rooms
            else:
                for r in all_rooms:
                    rid = getattr(r, "room_id", None) or (r.get("room_id") if isinstance(r, dict) else None)
                    if rid:
                        room_map[rid] = r

            if not room_ids:
                room_ids_resolved = list(room_map.keys())
            else:
                room_ids_resolved = room_ids

            # TimelineContext：取第一个有 active timeline 的房间
            ctx = None
            for rid in room_ids_resolved:
                ctx = timeline_svc.get_active_timeline_for_room(rid)
                if ctx is not None:
                    break

            main_id = getattr(ctx, "reference_room_id", None) if ctx else (
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
                    name = getattr(room, "streamer_name", None) or rid[:8]
                    path = getattr(room, "record_output_path", "") or ""
                    sources.append(RoomDraftSource(
                        room_id=rid,
                        name=name,
                        record_output_path=path,
                        recording_to_common_delta=float(snap.recording_to_common_delta),
                        is_main=(rid == main_id),
                    ))
            else:
                # H2: 无 TimelineContext — 仅允许单房降级（调用方传 1 个 room 或 allow_single_fallback）
                allow_fallback = bool(data.get("allow_single_fallback", False))
                if len(room_ids_resolved) > 1 and not allow_fallback:
                    return {
                        "success": False,
                        "error": "多房草稿需先一键对齐",
                        "error_code": "no_aligned_context",
                        "warnings": warnings,
                    }, None, None
                rid = main_id or (room_ids_resolved[0] if room_ids_resolved else None)
                if not rid:
                    return {
                        "success": False,
                        "error": "没有可用房间",
                        "error_code": "no_rooms",
                    }, None, None
                room = room_map.get(rid) or manager.get_room(rid)
                if room is None:
                    return {
                        "success": False,
                        "error": "房间不存在",
                        "error_code": "no_rooms",
                    }, None, None
                warnings.append("未对齐，已降级为主房单房草稿")
                sources.append(RoomDraftSource(
                    room_id=rid,
                    name=getattr(room, "streamer_name", None) or rid[:8],
                    record_output_path=getattr(room, "record_output_path", "") or "",
                    recording_to_common_delta=0.0,
                    is_main=True,
                ))

            # 切片：优先从 timeline clip snapshots；否则从 data.clips 内联（前端可传）
            clip_sources: list[ClipDraftSource] = []
            inline_clips = list(data.get("clips") or [])
            if inline_clips:
                for c in inline_clips:
                    if clip_ids and c.get("clip_id") not in clip_ids and c.get("clip_snapshot_id") not in clip_ids:
                        continue
                    if not clip_allowed_for_draft(c):
                        warnings.append(f"切片 {c.get('label') or c.get('clip_id')} 未确认或为近似定位，已跳过")
                        continue
                    resolved = resolve_common_range(c, ctx, None)
                    if resolved is None:
                        warnings.append(f"切片 {c.get('label')} 无法映射到公共轴，已跳过")
                        continue
                    cs, ce, prec = resolved
                    clip_sources.append(ClipDraftSource(
                        clip_id=str(c.get("clip_snapshot_id") or c.get("clip_id") or ""),
                        common_start=cs,
                        common_end=ce,
                        label=str(c.get("label") or "回合"),
                        precision=prec,
                        confirm_status=c.get("confirm_status"),
                    ))
            elif clip_ids:
                for cid in clip_ids:
                    snap = timeline_svc.get_clip_snapshot(cid)
                    if snap is None:
                        warnings.append(f"切片 {cid} 不存在或已过期，已跳过")
                        continue
                    clip_sources.append(ClipDraftSource(
                        clip_id=snap.clip_id,
                        common_start=snap.common_start,
                        common_end=snap.common_end,
                        label=str(data.get("labels", {}).get(cid) or "回合"),
                        precision="exact",
                        confirm_status="user_confirmed",
                    ))

            if options.include_clips and not clip_sources and clip_ids:
                # 全部被过滤 —— 仍可生成纯录制轨
                options_override = options
            return None, sources, clip_sources, options, warnings

        # bridge.call 收集
        collected = bridge.call(_collect, timeout=15.0)
        # 依实际 bridge.call 返回结构调整；若 _collect 直接返回 error dict：
        if isinstance(collected, tuple) and collected[0] is not None and isinstance(collected[0], dict):
            return collected[0]
        _err, sources, clip_sources, options, warnings = collected

        loop = asyncio.get_running_loop()

        def _run():
            return build_session_draft(
                rooms=sources,
                clips=clip_sources if options.include_clips else [],
                options=options,
                draft_root=draft_root,
            )

        result = await loop.run_in_executor(None, _run)
        payload = {
            "success": result.success,
            "draft_name": result.draft_name,
            "draft_dir": result.draft_dir,
            "tracks": result.tracks,
            "segments": result.segments,
            "warnings": list(warnings) + list(result.warnings),
            "error": result.error,
            "error_code": result.error_code,
        }
        return payload
```

**实现时必须对照真实 `MultiRoomManager` API**（`get_room` / `get_rooms` / `list_rooms`）——以代码为准，不要臆造方法名。用 Grep 确认后写入最终实现。

**前端契约补充（本 handler 支持）：** 请求可带 `clips: ClipSegment[]` 内联列表（含 `common_start/end`、`confirm_status`、`mark_precision`），这样即使 clip 只在前端 store、没有 `ClipSnapshot` 也能进草稿。J3 会传这个字段。

- [ ] **Step 3: 跑 guard 测试**

```python
def test_generate_handler_error_codes_documented():
    text = Path("python-backend/handlers/jianying_handlers.py").read_text(encoding="utf-8")
    for code in _ERROR_CODES_FROM_TEST:
        assert code in text
```

Run: `pytest tests/test_jianying_ws_guards.py -v`  
Expected: PASS

- [ ] **Step 4: 手动烟测（可选）**

启动 backend，WS 发：

```json
{"type":"get_jianying_draft_dir","request_id":"1"}
```

确认返回 `draft_dir` / `exists`。

---

### Task 4: J2 自检

- [ ] 未改 `queue_export` / `export_clip` 语义
- [ ] H1 `draft_dir_missing`、H2 `no_aligned_context`、H14 `library_missing` 有路径
- [ ] 生成走 `run_in_executor`，不阻塞 asyncio 循环
- [ ] `git status` 无前端改动

---

## J2 完成标准

- `pytest tests/test_jianying_ws_guards.py tests/test_jianying_draft.py -v` 绿
- WS `get_jianying_draft_dir` / `generate_jianying_draft` 已注册
- 进入 J3
