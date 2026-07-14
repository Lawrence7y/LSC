# 待确认切片人工精修 + 时间线精修友好 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 持续分析 pending 回合立刻进切片列表；用户可点选精修入出点并确认（多房同步、跳过 OCR）；时间线达到精修友好水位；顺带修 Onset 收尾跳过 OCR 的 bug。

**Architecture:** 前端主导精修 UI + 轻量后端 `confirm_status` / refine 冻结 / 多房 upsert。Pending 与 OCR/用户确认一律 `clip_queued` 入列但不自动 FFmpeg 导出。时间线去掉波形、松手提交拖拽、无预览用录制/分析时长出轨。

**Tech Stack:** Python (`room_handler`, `round_detector`)、pytest、React/TS、Zustand、Ant Design、WebSocket

**Spec:** [docs/superpowers/specs/2026-07-14-pending-clip-refine-timeline-design.md](../specs/2026-07-14-pending-clip-refine-timeline-design.md)

---

## File map

| File | Responsibility |
|------|----------------|
| `lsc/analyzer/round_detector.py` | Onset 路径 OCR 精修；OCR 空结果日志 |
| `tests/test_round_detector.py`（或新建 `tests/test_onset_ocr_refine.py`） | Onset+OCR 契约测试 |
| `python-backend/handlers/room_handler.py` | pending 入列、冻结集、refine/confirm/cancel、停自动导出 |
| `tests/test_continuous_analysis_guards.py` | 后端契约 / 源码形状测试 |
| `tests/test_clip_refine_handlers.py`（新建） | begin/confirm/cancel 与多房 upsert |
| `lsc-electron/src/types/index.ts` | `confirm_status`、`round_key` |
| `lsc-electron/src/pages/Workbench/components/ClipList.tsx` | 标签、蓝光、确认按钮、导出门禁 |
| `lsc-electron/src/pages/Workbench/index.tsx` | 入列/精修/确认/时间线联动；停波形驱动 |
| `lsc-electron/src/components/Timeline/index.tsx` | 松手提交、硬色带、手势分离、去波形渲染 |
| `lsc-electron/src/components/Timeline/Timeline.css` | 蓝光色带、底栏层级 |
| `lsc-electron/src/pages/Workbench/components/ControlBar.tsx` | 去 waveform props；遮挡/布局 |

---

### Task 1: Onset 收尾必须跑 OCR 精修

**Files:**
- Modify: `lsc/analyzer/round_detector.py`（约 323–331）
- Modify: `lsc/analyzer/round_detector.py`（约 157–184，空 OCR 日志）
- Test: `tests/test_onset_ocr_refine.py`（新建）或扩 `tests/test_round_detector.py`

- [ ] **Step 1: 写失败测试 — Onset 路径在有 time_range 时仍应调用 OCR refine**

```python
"""Onset early-return must OCR-refine when refine_with_ocr=True even if time_range set."""
from __future__ import annotations
from unittest.mock import patch
import numpy as np
import pytest

def test_onset_path_refines_with_ocr_when_time_range_set(tmp_path, monkeypatch):
    # 构造：RMS 失效触发 onset；time_range=(0, 200)；refine_with_ocr=True
    # mock onset 产出 2 段；assert _refine_rounds_with_ocr 被调用恰好 1 次
    ...
```

（实现时用现有 `detect_valorant_rounds` 测试夹具风格；若全链路过重，改为断言源码不再包含 `scan_range is None` 守卫 + 单测 `_refine` 调用包装。）

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_onset_ocr_refine.py -v`（或对应用例）  
Expected: FAIL（仍有 `scan_range is None` 或 mock 未被调用）

- [ ] **Step 3: 最小修复**

把：

```python
if refine_with_ocr and results0 and scan_range is None:
```

改为：

```python
if refine_with_ocr and results0:
```

并在主 OCR 分支：`phase_markers` 为空或分辨率失败时 `_log.warning(...)`。

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_onset_ocr_refine.py tests/test_round_detector.py -v --tb=line`  
Expected: PASS

- [ ] **Step 5: Commit**（仅当用户要求提交时）

```bash
git add lsc/analyzer/round_detector.py tests/test_onset_ocr_refine.py
git commit -m "fix: run OCR refine on onset path even with time_range"
```

---

### Task 2: 后端 — pending 入列且停止自动导出

**Files:**
- Modify: `python-backend/handlers/room_handler.py`（`_auto_export_highlights`、持续分析消费循环约 5154–5210）
- Modify: `tests/test_continuous_analysis_guards.py`

**行为变更（spec）：** OCR 确认与 pending 一律只 `clip_queued(export_deferred)` **且不入** `_deferred_export_jobs` 自动冲刷；或入 deferred 但前端/导出门禁挡住——**推荐：pending/ocr_confirmed 只广播入列，不写 deferred 导出队列；用户确认后仍不自动导出，仅手动 export。**

- [ ] **Step 1: 写失败测试**

```python
def test_continuous_loop_queues_pending_rounds_not_only_ocr():
    source = (ROOT / "python-backend/handlers/room_handler.py").read_text(encoding="utf-8")
    # 应存在 pending 入列路径（非仅 _is_auto_exportable）
    assert "confirm_status" in source or "pending" in source.split("_auto_export_highlights", 1)[1][:2000]
    # 自动导出冲刷不得再对 valorant 默认 force OCR 即导
    ...


def test_auto_export_helper_accepts_pending_confirm_status():
    # 单元：调用 _auto_export_highlights 风格的纯函数抽出后，
    # full_round pending 也会产生 clip_queued 载荷且 confirm_status=pending
    ...
```

若 `_auto_export_highlights` 难单测，先抽：

```python
def _highlight_clip_queued_payload(..., confirm_status: str) -> dict: ...
```

- [ ] **Step 2: 跑测试确认失败**

- [ ] **Step 3: 实现**

1. 扩展 `_auto_export_highlights`（或新建 `_queue_highlight_clips`）：
   - 参数 `confirm_status: str = "pending"`
   - `clip_queued` 增加 `confirm_status`, `round_key`
   - `list_only=True`：只广播，不 `_deferred_export_jobs.append`，不 `queue_export`
2. 持续分析循环：
   - **所有**新闭合回合（含非 OCR）→ `list_only` + `confirm_status=pending`
   - OCR 升格且非冻结 → 广播 `clip_confirm_status` / 或再次 `clip_queued` upsert 为 `ocr_confirmed`，仍 `list_only`
   - 删除「OCR 确认即 `_auto_export_highlights` 进 deferred 导出」的默认行为（valorant 路径）
3. 维护 `_refined_round_keys: set[str]`（room 或全局 session）：`begin_refine` / `user_confirmed` 加入；OCR 升格前检查

- [ ] **Step 4: 跑测试**

Run: `QT_QPA_PLATFORM=offscreen pytest tests/test_continuous_analysis_guards.py -v --tb=line`  
Expected: PASS（更新旧断言「only exports confirmed」为「queues pending; export manual」）

- [ ] **Step 5: Commit**（用户要求时）

---

### Task 3: 后端 — begin / confirm / cancel refine handlers

**Files:**
- Modify: `python-backend/handlers/room_handler.py`（在 `register_room_handlers` 内新增 `@server.on`）
- Create: `tests/test_clip_refine_handlers.py`

- [ ] **Step 1: 写失败测试**

```python
def test_confirm_highlight_maps_to_target_rooms_and_freezes():
    # mock manager rooms + bridge.queue_broadcast
    # begin_refine → key in freeze set
    # confirm → N 条 clip_confirm_status user_confirmed；freeze 保留
    # cancel → 若未 confirm 则移出 refining（可保留 freeze 至 cancel 清除 refining 标记）
    ...


def test_ocr_upgrade_skips_frozen_round_key():
    ...
```

- [ ] **Step 2: 跑测试确认失败**

- [ ] **Step 3: 实现 handlers**

```python
@server.on('begin_refine_clip')
async def handle_begin_refine_clip(data): ...

@server.on('confirm_highlight_clip')
async def handle_confirm_highlight_clip(data):
    # start/end, room_id, round_key, target_room_ids
    # 映射公式复用 _auto_export_highlights 的 delta
    # bridge.queue_broadcast clip_confirm_status（可批量 rooms）

@server.on('cancel_refine_clip')
async def handle_cancel_refine_clip(data): ...
```

状态可放在 `_continuous_tasks[room_id]` 旁的模块级 `_clip_refine_state: dict[str, dict]`：
`{ round_key: { status, start, end, room_ids } }`

- [ ] **Step 4: 跑测试通过**

- [ ] **Step 5: Commit**（用户要求时）

---

### Task 4: 前端类型 + clip_queued 入列 pending

**Files:**
- Modify: `lsc-electron/src/types/index.ts`
- Modify: `lsc-electron/src/pages/Workbench/index.tsx`（`clip_queued` 处理约 1868+）

- [ ] **Step 1: 扩展类型**

```typescript
export type ClipConfirmStatus = 'pending' | 'refining' | 'user_confirmed' | 'ocr_confirmed'

// ClipSegment 增加：
confirm_status?: ClipConfirmStatus
round_key?: string
```

- [ ] **Step 2: 更新 `clip_queued` handler**

- 写入 `confirm_status`（默认 `pending`）、`round_key`
- 同 `room_id+round_key` 已存在则 **update** 边界/状态，不重复 add
- 监听 `clip_confirm_status`：按 round_key 更新多房间条目

- [ ] **Step 3: `npx tsc --noEmit`（在 lsc-electron）通过**

- [ ] **Step 4: Commit**（用户要求时）

---

### Task 5: ClipList — 标签、蓝光、确认、导出门禁

**Files:**
- Modify: `lsc-electron/src/pages/Workbench/components/ClipList.tsx`
- Modify: `lsc-electron/src/pages/Workbench/index.tsx`（传入回调）

- [ ] **Step 1: 扩展 props**

```typescript
onSelectClip?: (clip: ClipSegment, index: number) => void
onConfirmClip?: (clip: ClipSegment, index: number) => void
refiningClipId?: string | null
```

- [ ] **Step 2: UI**

- 点击行 → `onSelectClip`（进入精修）
- `refining` / `refiningClipId` 匹配：`box-shadow: 0 0 0 2px var(--brand-500), 0 0 12px rgba(0,122,255,.45)`
- Tag：待精修 / 精修中 / 已精修 / OCR已确认
- 「确认」按钮：仅 refining 显示
- 「导出」：仅 `user_confirmed` | `ocr_confirmed` 可点；否则 disabled + title 提示

- [ ] **Step 3: Workbench 接线**

- select → `send('begin_refine_clip')` + 设 mark_in/out + `refiningClipId`
- confirm → `send('confirm_highlight_clip', { start, end, ... })`
- 切换/取消 → `cancel_refine_clip` + 恢复快照边界

- [ ] **Step 4: 手动点检或组件级 smoke**（无强制前端单测则手测清单写入 PR）

- [ ] **Step 5: Commit**（用户要求时）

---

### Task 6: 时间线档 B — 去波形、松手提交、硬色带、手势分离

**Files:**
- Modify: `lsc-electron/src/components/Timeline/index.tsx`
- Modify: `lsc-electron/src/components/Timeline/Timeline.css`
- Modify: `lsc-electron/src/pages/Workbench/components/ControlBar.tsx`
- Modify: `lsc-electron/src/pages/Workbench/index.tsx`（停 `WaveformPeakBuffer` 驱动；时长回退）

- [ ] **Step 1: 去掉波形渲染与 props 传递**

- Timeline 不再画 waveform path（可保留 prop 但忽略）
- Workbench 停止更新 `waveformPeaks`（可删除 buffer 调用）

- [ ] **Step 2: 松手提交拖拽**

- `onMarkerDrag` 拖动中只更新**本地** mark 显示（Workbench 用 local state）
- `onMarkerDragEnd`（新建）在 mouseup 时调用现有 `handleMarkerDragEnd` → `set_mark_*`
- Timeline：`handleMouseMove` 拖 marker 时不调用会触发 WS 的回调；`handleMouseUp` 调 `onMarkerDragEnd`

- [ ] **Step 3: 手势分离**

- 拖 marker：只改边界
- 拖 track / 播放头：seek
- 避免 marker 拖动时触发 onSeek 风暴

- [ ] **Step 4: refining 硬色带**

```tsx
// props: activeRefine?: { start: number; end: number } | null
// 渲染绝对定位区间，className timeline-refine-band
```

CSS：品牌蓝半透明 + 左右边线；`z-index` 高于普通 highlight。

- [ ] **Step 5: 无预览出轨时长**

Workbench `timelineView.duration` 优先级：

1. 选中/主房间录制文件时长（若后端/rooms 有）  
2. `continuousAnalysisStatus.recorded_duration`  
3. MSE / preview duration  

无预览时：seek 回调只更新 UI 时间，不调用 `mseSeek`（或 no-op）。

- [ ] **Step 6: 遮挡**

- ControlBar/Timeline 容器：`position: sticky` 或固定底栏、`z-index` 提升
- 确保 ClipList 滚动区不覆盖时间线命中区域（查 MainLayout / Workbench 布局）

- [ ] **Step 7: 列表滚窗联动**

进入精修时：根据 clip.start/end 设置 Timeline `windowStart` / zoom，使选区居中。

- [ ] **Step 8: `npx tsc --noEmit` + 手测清单**

- [ ] **Step 9: Commit**（用户要求时）

---

### Task 7: 联调验收与回归

**Files:** 无新文件；跑测试 + 手工

- [ ] **Step 1: 后端测试**

```bash
# Windows PowerShell
$env:QT_QPA_PLATFORM='offscreen'
pytest tests/test_continuous_analysis_guards.py tests/test_clip_refine_handlers.py tests/test_onset_ocr_refine.py tests/test_round_detector.py -v --tb=line
```

Expected: PASS

- [ ] **Step 2: 前端类型**

```bash
cd lsc-electron && npx tsc --noEmit
```

Expected: exit 0

- [ ] **Step 3: 手工验收（对照 spec §6）**

1. 边录：pending 进列表  
2. 无预览：时间线仍在  
3. 拖 I/O 不卡；松手后 mark 正确  
4. 精修蓝光+色带；OCR 不改该片（看日志/状态）  
5. 确认后多房同片 `user_confirmed`；无自动导出  
6. 其它 pending 可被 OCR 升格  
7. 收尾 Onset 路径有 OCR 日志  

- [ ] **Step 4: 总提交**（用户要求时）

---

## 执行方式

实现时优先 **subagent-driven-development**：每 Task 一个子代理，结束后跑该 Task 的验证再进下一个。  
不要跳过 Task 1（Onset OCR）——它可独立改善「收尾无确认」。

**不要**在未获用户明确要求时 `git commit` / `git push`（仓库用户规则优先于本计划中的 commit 步骤）。
