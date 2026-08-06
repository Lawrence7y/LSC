# Timeline 1x Zero + Preview Purple Window + rAF Clocks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 主时间线 1x 左端永远绝对 `0:00:00`（整段压进视口）；zoom>1 为以播放头/直播沿为中心的局部窗；预览条左端=紫线=`liveEdge−120s`；程序内时间文案与播放头同源走 rAF。

**Architecture:** 在 `timelineViewModel.ts` 增加 `zoomLevel` 驱动的窗口公式，并抽 `computeTimelineWindow` 供 ControlBar 本地轴复用；紫线/预览条统一 `DVR_LOOKBACK_SEC=120`；时钟文案扩展 `playheadStore`（或旁路 `clockStore`）用 rAF 刷新 DOM/细粒度订阅，避免整树 60fps `setState`。

**Tech Stack:** React + TypeScript + Vite（`lsc-electron`）、现有 Vitest、`playheadStore` rAF 通道、CLAUDE.md §8.7 坐标系。

**Spec:** [2026-08-06-timeline-1x-zero-and-raf-clocks-design.md](../specs/2026-08-06-timeline-1x-zero-and-raf-clocks-design.md)

---

## File map

| File | Responsibility |
|------|----------------|
| `lsc-electron/src/utils/timelineWindow.ts` | **新建**：纯函数 `computeTimelineWindow` + `DVR_LOOKBACK_SEC` |
| `lsc-electron/src/utils/timelineViewModel.ts` | 调用 `computeTimelineWindow`；`TimelineViewInput` 增加 `zoomLevel` |
| `lsc-electron/src/hooks/useTimelineViewModel.ts` | 传入 `zoomLevel`；依赖数组包含 zoom |
| `lsc-electron/src/workers/timelineView.worker.ts` | 透传新字段（若 structured clone input 已含即可） |
| `lsc-electron/src/pages/Workbench/components/ControlBar.tsx` | 本地轴改用同一窗口函数；时钟文案订阅 rAF |
| `lsc-electron/src/pages/Workbench/index.tsx` | 传 `zoomLevel`；`dvrStart = liveEdge−120`；录制计时走 rAF |
| `lsc-electron/src/pages/Workbench/components/RoomCard.tsx` | 预览条左端=紫线=`live−120`；已录时长 rAF |
| `lsc-electron/src/utils/playheadStore.ts`（或 `clockStore.ts`） | 录制显示秒 / 可选绝对时钟 rAF 发布 |
| `lsc-electron/src/utils/timelineWindow.test.ts` | Vitest：1x / zoom>1 / scrub / refine |
| `tests/test_frontend_stability_guards.py` | 源码守卫：禁止 Live 用 `record_started_at` 推窗；断言 `DVR_LOOKBACK_SEC` / 1x `ws=0` 存在 |

**Out of scope:** `valorant_ocr_rounds.py`、持续分析协议、导出策略。

---

### Task 1: `computeTimelineWindow` 纯函数 + 单测

**Files:**
- Create: `lsc-electron/src/utils/timelineWindow.ts`
- Create: `lsc-electron/src/utils/timelineWindow.test.ts`
- Modify: none yet

- [ ] **Step 1: Write the failing tests**

```typescript
// lsc-electron/src/utils/timelineWindow.test.ts
import { describe, expect, it } from 'vitest'
import { computeTimelineWindow, DVR_LOOKBACK_SEC, computeDvrLeftEdge } from './timelineWindow'

describe('computeTimelineWindow', () => {
  it('1x followLive: windowStart is always 0 even when contentEnd > 600', () => {
    const r = computeTimelineWindow({
      contentEnd: 2400,
      zoomLevel: 1,
      followLive: true,
      scrubbing: false,
      frozenWindowStart: null,
      playhead: 2400,
      prevWindowStart: 1800,
      refining: null,
    })
    expect(r.windowStart).toBe(0)
    expect(r.duration).toBe(2400)
    expect(r.visibleSpan).toBe(2400)
  })

  it('zoom>1 followLive: local window ending at contentEnd (left > 0)', () => {
    const r = computeTimelineWindow({
      contentEnd: 2400,
      zoomLevel: 2,
      followLive: true,
      scrubbing: false,
      frozenWindowStart: null,
      playhead: 2400,
      prevWindowStart: 0,
      refining: null,
    })
    expect(r.visibleSpan).toBe(1200)
    expect(r.windowStart).toBe(1200)
    expect(r.duration).toBe(2400)
  })

  it('zoom>1 scrubbing: centers on playhead via pan helper semantics', () => {
    const r = computeTimelineWindow({
      contentEnd: 2400,
      zoomLevel: 2,
      followLive: false,
      scrubbing: true,
      frozenWindowStart: 500,
      playhead: 1000,
      prevWindowStart: 500,
      refining: null,
    })
    expect(r.windowStart).toBe(500)
    expect(r.visibleSpan).toBe(1200)
  })

  it('refine window overrides zoom/live', () => {
    const r = computeTimelineWindow({
      contentEnd: 2400,
      zoomLevel: 1,
      followLive: true,
      scrubbing: false,
      frozenWindowStart: null,
      playhead: 2400,
      prevWindowStart: 0,
      refining: { start: 100, end: 130 },
    })
    expect(r.windowStart).toBeGreaterThan(0)
    expect(r.windowStart).toBeLessThan(100)
  })
})

describe('computeDvrLeftEdge', () => {
  it('returns max(0, liveEdge - 120)', () => {
    expect(DVR_LOOKBACK_SEC).toBe(120)
    expect(computeDvrLeftEdge(500)).toBe(380)
    expect(computeDvrLeftEdge(60)).toBe(0)
  })
})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd lsc-electron && npx vitest run src/utils/timelineWindow.test.ts`

Expected: FAIL (module not found)

- [ ] **Step 3: Implement `timelineWindow.ts`**

```typescript
// lsc-electron/src/utils/timelineWindow.ts
import { panTimelineWindowStart } from '@/utils/timelineCoords'
import { TIMELINE_MAX_WINDOW } from '@/utils/timelineViewModel'

export const DVR_LOOKBACK_SEC = 120

export type RefineRange = { start: number; end: number }

export type TimelineWindowInput = {
  contentEnd: number
  zoomLevel: number
  followLive: boolean
  scrubbing: boolean
  frozenWindowStart: number | null
  playhead: number
  prevWindowStart: number
  refining: RefineRange | null
}

export type TimelineWindowResult = {
  windowStart: number
  duration: number
  visibleSpan: number
}

/** 预览/common 轴上的 DVR 左边界（紫线）：liveEdge − 120s，禁止用录制墙钟。 */
export function computeDvrLeftEdge(liveEdgeSec: number): number {
  if (!Number.isFinite(liveEdgeSec) || liveEdgeSec <= 0) return 0
  return Math.max(0, liveEdgeSec - DVR_LOOKBACK_SEC)
}

/**
 * 1x + followLive + !scrub + !refine → windowStart=0，整段压进视口。
 * zoom>1 → visibleSpan = contentEnd/zoom，Live 时窗贴右缘；scrub 时用 pan/frozen。
 */
export function computeTimelineWindow(input: TimelineWindowInput): TimelineWindowResult {
  const contentEnd = Math.max(1, input.contentEnd)
  const zoom = Math.max(1, input.zoomLevel || 1)
  const refining = input.refining

  if (refining && refining.end > refining.start) {
    const mid = (refining.start + refining.end) / 2
    const half = Math.min(TIMELINE_MAX_WINDOW, Math.max(30, (refining.end - refining.start) * 4)) / 2
    const ws = Math.max(0, mid - half)
    const dur = Math.max(contentEnd, ws + half * 2, 1)
    return { windowStart: ws, duration: dur, visibleSpan: dur - ws }
  }

  if (zoom <= 1 && input.followLive && !input.scrubbing) {
    return { windowStart: 0, duration: contentEnd, visibleSpan: contentEnd }
  }

  if (zoom <= 1 && contentEnd <= TIMELINE_MAX_WINDOW) {
    return { windowStart: 0, duration: contentEnd, visibleSpan: contentEnd }
  }

  // zoom>1 或 1x 非 Live 长内容：局部窗
  const visibleSpan =
    zoom > 1
      ? Math.max(30, Math.min(contentEnd, contentEnd / zoom))
      : Math.min(contentEnd, TIMELINE_MAX_WINDOW)

  let ws = 0
  if (input.followLive && !input.scrubbing) {
    ws = Math.max(0, contentEnd - visibleSpan)
  } else if (input.scrubbing && input.frozenWindowStart != null) {
    ws = input.frozenWindowStart
  } else {
    ws = panTimelineWindowStart(
      Math.max(0, input.playhead),
      contentEnd,
      visibleSpan,
      input.frozenWindowStart ?? input.prevWindowStart,
    )
  }

  return { windowStart: ws, duration: contentEnd, visibleSpan }
}
```

Note: avoid circular import — move `TIMELINE_MAX_WINDOW` into `timelineWindow.ts` and re-export from `timelineViewModel.ts`, **or** duplicate the constant `600` in `timelineWindow.ts` and keep viewModel importing from window module. Prefer:

```typescript
// timelineWindow.ts
export const TIMELINE_MAX_WINDOW = 600
export const DVR_LOOKBACK_SEC = 120
```

```typescript
// timelineViewModel.ts
export { TIMELINE_MAX_WINDOW } from '@/utils/timelineWindow'
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd lsc-electron && npx vitest run src/utils/timelineWindow.test.ts`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add lsc-electron/src/utils/timelineWindow.ts lsc-electron/src/utils/timelineWindow.test.ts
git commit -m "$(cat <<'EOF'
feat(timeline): add computeTimelineWindow for 1x-zero and zoom local pane

EOF
)"
```

---

### Task 2: Wire `computeTimelineWindow` into `timelineViewModel` + hook

**Files:**
- Modify: `lsc-electron/src/utils/timelineViewModel.ts`
- Modify: `lsc-electron/src/hooks/useTimelineViewModel.ts`
- Modify: `lsc-electron/src/pages/Workbench/index.tsx`（传入 `zoomLevel`）
- Modify: `lsc-electron/src/workers/timelineView.worker.ts`（若手写字段列表则补上）

- [ ] **Step 1: Extend `TimelineViewInput`**

Add:

```typescript
zoomLevel?: number
```

- [ ] **Step 2: Replace inline ws/dur block in `computeTimelineViewModel`**

Replace the block currently at ~169–191 with:

```typescript
  const zoomLevel = input.zoomLevel ?? 1
  const refining =
    refineStart != null && refineEnd != null && refineEnd > refineStart
      ? { start: refineStart, end: refineEnd }
      : null

  const win = computeTimelineWindow({
    contentEnd,
    zoomLevel,
    followLive: timelineFollowLive,
    scrubbing: timelineScrubbing,
    frozenWindowStart,
    playhead: Math.max(0, curCommon),
    prevWindowStart,
    refining,
  })
  const ws = win.windowStart
  const dur = win.duration
```

Keep `liveCur` / markIn / markOut / return shape unchanged.

- [ ] **Step 3: Pass `zoomLevel` from Workbench into `useTimelineViewModel`**

In `index.tsx` where view-model input is built, add `zoomLevel: timelineZoomLevel` (or existing zoom state name — grep `zoomLevel` / `setZoom`).

Ensure `useTimelineViewModel` dependency list includes `input.zoomLevel`.

- [ ] **Step 4: Add Vitest covering viewModel integration (optional thin)**

```typescript
// in timelineWindow.test.ts or timelineViewModel.test.ts
import { computeTimelineViewModel } from './timelineViewModel'
// minimal fixture with commonMode + fake timelineContext is heavy;
// prefer keeping logic tests in timelineWindow.test.ts and a source guard in Task 5.
```

- [ ] **Step 5: Run frontend unit tests**

Run: `cd lsc-electron && npx vitest run src/utils/timelineWindow.test.ts`

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add lsc-electron/src/utils/timelineViewModel.ts lsc-electron/src/hooks/useTimelineViewModel.ts lsc-electron/src/pages/Workbench/index.tsx lsc-electron/src/workers/timelineView.worker.ts
git commit -m "$(cat <<'EOF'
feat(timeline): 1x windowStart=0; zoom>1 uses local pane in viewModel

EOF
)"
```

---

### Task 3: Align ControlBar local-axis window formula

**Files:**
- Modify: `lsc-electron/src/pages/Workbench/components/ControlBar.tsx` (~210–291)

- [ ] **Step 1: Replace local `TIMELINE_MAX_WINDOW` sliding math**

Import `computeTimelineWindow` and replace the `else if (contentEnd > TIMELINE_MAX_WINDOW) { ws = contentEnd - … }` branch with the same call pattern as Task 2, using props `zoomLevel`, `followLive`, `isScrubbing`, `frozenWindowStart`, playhead=`previewPos` (local axis) / common when `timelineView` is null.

When `timelineView` is non-null, **keep trusting** `timelineView.windowStart` / `duration`（common 轴）— only fix the `localTimeline` useMemo path so non-aligned mode matches.

- [ ] **Step 2: Manual sanity (or component test if cheap)**

Non-aligned room, long preview, zoom 1 → left label `0:00:00`.

- [ ] **Step 3: Commit**

```bash
git add lsc-electron/src/pages/Workbench/components/ControlBar.tsx
git commit -m "$(cat <<'EOF'
fix(timeline): align ControlBar local window with computeTimelineWindow

EOF
)"
```

---

### Task 4: Preview strip + main purple = `liveEdge − 120`

**Files:**
- Modify: `lsc-electron/src/pages/Workbench/components/RoomCard.tsx` (~247–260)
- Modify: `lsc-electron/src/pages/Workbench/index.tsx` (`dvrStart` useMemo ~545–572)
- Test: extend `timelineWindow.test.ts` already has `computeDvrLeftEdge`; add RoomCard source guard in Task 6

- [ ] **Step 1: RoomCard — remove boundary lead; pin left to purple**

Replace:

```typescript
  const boundaryLeadSeconds = Math.max(10, Math.min(60, liveDvrDuration * 0.2))
  const expTimelineStart = hasLiveDvrRange
    ? Math.max(0, bufferedStart - boundaryLeadSeconds)
    : 0
  ...
  const replayBoundary = hasLiveDvrRange ? bufferedStart : expTimelineStart
```

With:

```typescript
  import { computeDvrLeftEdge } from '@/utils/timelineWindow'

  const liveEdge = hasLiveDvrRange ? bufferedEnd : fallbackEnd
  const purple = hasLiveDvrRange ? computeDvrLeftEdge(liveEdge) : 0
  const expTimelineStart = hasLiveDvrRange ? purple : 0
  const expTimelineEnd = hasLiveDvrRange ? bufferedEnd : fallbackEnd
  const expTimelineSpan = Math.max(1, expTimelineEnd - expTimelineStart)
  const replayBoundary = purple
```

Update tooltip copy if it still says「左侧留白」类描述 → 「DVR 左边界 / 可回看起点（约实时−2分钟）」.

Seek clamp: keep `target = max(replayBoundary, …)`.

- [ ] **Step 2: Workbench `dvrStart`**

```typescript
  const buf = getRoomBufferedRange(rid)
  if (!buf) return null
  const liveEdgePreview = buf.end
  const dvrPreview = computeDvrLeftEdge(liveEdgePreview)
  if (commonMode && timelineContext?.room_snapshots[rid]) {
    try {
      return previewToCommon(timelineContext, rid, dvrPreview)
    } catch {
      return dvrPreview
    }
  }
  return dvrPreview
```

Do **not** use `record_started_at` here.

- [ ] **Step 3: Commit**

```bash
git add lsc-electron/src/pages/Workbench/components/RoomCard.tsx lsc-electron/src/pages/Workbench/index.tsx
git commit -m "$(cat <<'EOF'
feat(timeline): pin preview/main DVR purple to liveEdge-120s

EOF
)"
```

---

### Task 5: rAF clock channel for recorded + displayed times

**Files:**
- Modify: `lsc-electron/src/utils/playheadStore.ts`（扩展）或 Create: `lsc-electron/src/utils/clockStore.ts`
- Modify: `lsc-electron/src/hooks/usePlayheadSampling.ts` 或 Workbench tick 写入
- Modify: `ControlBar.tsx`（`formatTime(progressSummary.previewPosition)`）
- Modify: `RoomCard.tsx`（已录时长）
- Modify: `index.tsx`（降低/移除仅服务时钟的 1s 依赖）

- [ ] **Step 1: Add clock helpers to `playheadStore.ts`（保持单 rAF 合帧）**

```typescript
export type ClockSnapshot = {
  /** roomId → 显示用已录秒（含 rAF 插值） */
  recordedSec: Readonly<Record<string, number>>
  displayPlayhead: number
}

const recordedBase: Record<string, { sec: number; monoMs: number }> = {}
const clockListeners = new Set<(snap: ClockSnapshot) => void>()

export function writeRecordedClockBase(roomId: string, recordedSec: number): void {
  recordedBase[roomId] = { sec: recordedSec, monoMs: performance.now() }
  dirty = true
  scheduleFlush()
}

export function readRecordedDisplaySec(roomId: string, isRecording: boolean): number {
  const b = recordedBase[roomId]
  if (!b) return 0
  if (!isRecording) return b.sec
  return b.sec + (performance.now() - b.monoMs) / 1000
}

// In scheduleFlush notify: also compute interpolated recorded map + display playhead
```

Ensure flush still runs on rAF even when only wall-time elapsed (no playhead write): start a **lightweight** `requestAnimationFrame` loop while any room is recording OR subscribe ControlBar to drive `scheduleFlush` each frame via `subscribeDisplayPlayhead` already firing — for idle Live with paused preview but recording, Workbench should call `writeRecordedClockBase` on status updates and run:

```typescript
useEffect(() => {
  if (!anyRecording) return
  let id = 0
  const tick = () => {
    // force clockListeners without changing bases
    dirty = true
    scheduleFlush()
    id = requestAnimationFrame(tick)
  }
  id = requestAnimationFrame(tick)
  return () => cancelAnimationFrame(id)
}, [anyRecording])
```

Put the force-flush loop inside `playheadStore.startRecordingClockLoop()` to keep Workbench thin.

- [ ] **Step 2: ControlBar current-time label**

Replace React-only `formatTime(progressSummary.previewPosition)` with a small span + `useEffect` that `subscribeDisplayPlayhead` writes `textContent = formatTime(t)` (absolute display axis). Keep `progressSummary` for non-visual logic if needed.

- [ ] **Step 3: RoomCard recorded duration**

Replace `recordingTick`-driven string with `subscribeClock` / `readRecordedDisplaySec(room_id, is_recording)` updating a ref span. Workbench can stop passing `recordingTick={Math.floor(timelineTick / 4)}` once unused (or leave prop unused one commit then delete).

- [ ] **Step 4: Analysis progress optional interpolate（YAGNI-light）**

If `AnalysisProgress` shows `analyzed_duration` that jumps every 5s, add local:

```typescript
const displayAnalyzed = lastAnalyzed + Math.min(
  Math.max(0, (Date.now() - lastStatusAt) / 1000),
  Math.max(0, (recorded ?? 0) - lastAnalyzed),
)
```

Update on rAF or 100ms — only if the component already mounts during continuous analysis. Skip if costly; spec allows it but not mandatory for Task gate.

- [ ] **Step 5: Commit**

```bash
git add lsc-electron/src/utils/playheadStore.ts lsc-electron/src/pages/Workbench/components/ControlBar.tsx lsc-electron/src/pages/Workbench/components/RoomCard.tsx lsc-electron/src/pages/Workbench/index.tsx
git commit -m "$(cat <<'EOF'
feat(ui): drive recorded/preview clock labels via rAF store

EOF
)"
```

---

### Task 6: Source guards + regression checklist

**Files:**
- Modify: `tests/test_frontend_stability_guards.py`

- [ ] **Step 1: Add guards**

```python
def test_timeline_1x_zero_and_dvr_lookback_contract():
    window = (ROOT / "lsc-electron/src/utils/timelineWindow.ts").read_text(encoding="utf-8")
    assert "DVR_LOOKBACK_SEC = 120" in window
    assert "computeTimelineWindow" in window
    assert "zoom <= 1 and input.followLive" in window.replace(" ", "") or (
        "zoom <= 1" in window and "followLive" in window and "windowStart: 0" in window
    )
    room = (ROOT / "lsc-electron/src/pages/Workbench/components/RoomCard.tsx").read_text(encoding="utf-8")
    assert "boundaryLeadSeconds" not in room
    assert "computeDvrLeftEdge" in room
    workbench = (ROOT / "lsc-electron/src/pages/Workbench/index.tsx").read_text(encoding="utf-8")
    # dvrStart must not be driven by record_started_at
    # (soft check: computeDvrLeftEdge used near dvrStart)
    assert "computeDvrLeftEdge" in workbench
```

Tune the zoom assert to match final source formatting.

- [ ] **Step 2: Run**

```bash
pytest tests/test_frontend_stability_guards.py::test_timeline_1x_zero_and_dvr_lookback_contract -v
cd lsc-electron && npx vitest run src/utils/timelineWindow.test.ts
```

Expected: PASS

- [ ] **Step 3: Manual checklist（执行时勾选）**

1. Live 录制 >10min，zoom 1x：左标签 `0:00:00`，右端随进度变，切片块比例正确  
2. zoom 2x：左端 > 0，窗跟直播沿/播放头  
3. 放大预览：紫线贴左，≈ now−2min  
4. 已录时长与控制条当前时刻连续走动（非 1s 一跳）  
5. 对齐 common 轴、精修窗、recording_review 无紫线  

- [ ] **Step 4: Commit**

```bash
git add tests/test_frontend_stability_guards.py
git commit -m "$(cat <<'EOF'
test: guard timeline 1x-zero and DVR 120s purple contract

EOF
)"
```

---

## Spec coverage (self-review)

| Spec requirement | Task |
|------------------|------|
| 1x `windowStart=0` full compress | Task 1–3 |
| zoom>1 local pane centered / live-right | Task 1–3 |
| Preview left = purple = live−120 | Task 4 |
| Main purple same contract | Task 4 |
| rAF clocks | Task 5 |
| §8.7 no recording wallclock for Live window | Task 4 + Task 6 |
| No OCR / continuous analysis | Out of scope (stated) |
| Analysis optional interpolate | Task 5 Step 4 (optional) |

## Placeholder scan

No TBD/TODO left in task steps. Circular import risk called out with preferred `TIMELINE_MAX_WINDOW` home in `timelineWindow.ts`.

---

## Execution handoff

Plan complete and saved to `docs/superpowers/plans/2026-08-06-timeline-1x-zero-and-raf-clocks.md`.

**Two execution options:**

1. **Subagent-Driven (recommended)** — fresh subagent per task, review between tasks  
2. **Inline Execution** — execute in this session with executing-plans checkpoints  

Which approach?
