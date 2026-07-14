> **Status (2026-07-14):** 主体已在代码落地；剩余项并入 `2026-07-14-next-iteration-trust-platform-hygiene.md`。请勿按本文件空 checkbox 从头重做。

# Common Timeline UX Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 产品化 `TimelineContext`/`ClipSnapshot`，时间线以单一公共轴渲染，并补齐波形、AI 高光色带、精确拖拽与对齐徽标。

**Architecture:** 对齐成功后后端 `create_timeline` 并广播 `timeline_ready`；前端以 common 时间为唯一 UI 坐标；添加切片走 `create_clip_snapshot`，导出走 `export_clip_by_id`；未对齐降级现有墙钟路径。波形取自参考房间预览音频峰值；高光来自切片列表映射。

**Tech Stack:** React/TypeScript, Zustand, WebSocket, Python TimelineService, pytest

**Spec:** `docs/superpowers/specs/2026-07-13-common-timeline-ux-design.md`

---

## File structure

| File | Responsibility |
|------|----------------|
| `lsc/core/services/timeline_service.py` | 可选：invalidate 时回调/序列化 helper |
| `python-backend/handlers/room_handler.py` | 对齐后 create_timeline；广播 ready/invalidated；export source 修复 |
| `tests/test_align_creates_timeline.py` | 对齐 → TimelineContext 契约测试（新建） |
| `tests/test_timeline_delta_consistency.py` | delta 符号与导出一致性（新建） |
| `lsc-electron/src/types/index.ts` | 完整 TimelineContext / HighlightBand / ClipSegment.clip_snapshot_id |
| `lsc-electron/src/utils/timelineCoords.ts` | common ↔ preview 换算纯函数（新建） |
| `lsc-electron/src/utils/waveformPeaks.ts` | 预览峰值环缓冲（新建） |
| `lsc-electron/src/store/appStore.ts` | timelineMode / alignStatus（或由 context null 推导） |
| `lsc-electron/src/components/Timeline/index.tsx` | 波形层、高光带、扩展磁吸、点击高光 |
| `lsc-electron/src/components/Timeline/Timeline.css` | 波形/高光/tooltip 样式 |
| `lsc-electron/src/pages/Workbench/components/ControlBar.tsx` | 对齐徽标；把新 props 传给 Timeline |
| `lsc-electron/src/pages/Workbench/index.tsx` | 监听 timeline_*；seek/mark/add/export 走 common |
| `lsc-electron/src/pages/Workbench/components/ClipList.tsx` | exact 来自 snapshot；不再误标近似 |
| `lsc-electron/src/services/websocket.ts` | 无需改协议层（沿用 on/send） |

---

### Task 1: 对齐成功创建 TimelineContext（后端）

**Files:**
- Create: `tests/test_align_creates_timeline.py`
- Modify: `python-backend/handlers/room_handler.py`（`handle_align_preview_audio` 成功分支，约 2915–2947）
- Modify: `lsc/core/services/timeline_service.py`（如需 `to_dict` 序列化 helper）

- [ ] **Step 1: 写失败测试 — 对齐 trusted 后应能 get_active_timeline**

```python
"""对齐成功后必须创建 TimelineContext。"""
from __future__ import annotations

from lsc.core.models import RoomTimeSnapshot
from lsc.core.services.timeline_service import TimelineService, _ALIGN_CONFIDENCE_THRESHOLD


def build_snapshots_from_offsets(
    reference_room_id: str,
    offsets: dict[str, float],
    scores: dict[str, float],
    room_meta: dict[str, dict],
) -> dict[str, RoomTimeSnapshot]:
    """与生产代码共用的 delta 推导（测试先定义期望，实现时抽到可导入函数）。

    约定：content_offset 正值 = 该房间内容领先基准。
    preview_to_common_delta[room] = content_offset[room] - content_offset[reference]
    使得 preview_to_common(ref, t) == t（当 ref offset 为 0 时）。
    """
    ref_off = float(offsets.get(reference_room_id, 0.0))
    out: dict[str, RoomTimeSnapshot] = {}
    for rid, offset in offsets.items():
        score = float(scores.get(rid, 0.0) or 0.0)
        if score < _ALIGN_CONFIDENCE_THRESHOLD:
            continue
        meta = room_meta.get(rid, {})
        preview_delta = float(offset) - ref_off
        # recording_local ≈ mono - media_start；对齐瞬间令 common≈preview_ref
        media_start = float(meta.get("media_start_mono") or 0.0)
        recording_delta = media_start + preview_delta
        out[rid] = RoomTimeSnapshot(
            room_id=rid,
            preview_epoch_id=str(meta.get("preview_epoch_id") or ""),
            recording_id=str(meta.get("recording_id") or ""),
            preview_to_common_delta=preview_delta,
            recording_to_common_delta=recording_delta,
            align_confidence=score,
            media_start_mono=media_start,
        )
    return out


def test_build_snapshots_reference_has_zero_preview_delta():
    snaps = build_snapshots_from_offsets(
        "r0",
        {"r0": 0.0, "r1": 0.8},
        {"r0": 1.0, "r1": 0.9},
        {
            "r0": {"recording_id": "a", "media_start_mono": 100.0, "preview_epoch_id": "e0"},
            "r1": {"recording_id": "b", "media_start_mono": 100.5, "preview_epoch_id": "e1"},
        },
    )
    assert snaps["r0"].preview_to_common_delta == 0.0
    assert abs(snaps["r1"].preview_to_common_delta - 0.8) < 1e-9


def test_create_timeline_from_align_snapshots():
    svc = TimelineService()
    snaps = build_snapshots_from_offsets(
        "r0",
        {"r0": 0.0, "r1": 0.5},
        {"r0": 0.95, "r1": 0.9},
        {
            "r0": {"recording_id": "rec0", "media_start_mono": 10.0, "preview_epoch_id": "p0"},
            "r1": {"recording_id": "rec1", "media_start_mono": 10.2, "preview_epoch_id": "p1"},
        },
    )
    ctx = svc.create_timeline("r0", snaps, required_room_ids=["r0", "r1"])
    assert ctx is not None
    assert svc.get_active_timeline_for_room("r1") is ctx
    assert abs(ctx.preview_to_common("r1", 5.0) - 5.5) < 1e-9
```

- [ ] **Step 2: 跑测试确认失败或先绿（纯函数未接入 handler）**

Run: `pytest tests/test_align_creates_timeline.py -v`
Expected: PASS for pure helpers if defined in test file；下一步把 helper 挪到可导入模块并在 handler 调用。

- [ ] **Step 3: 抽出可导入 helper 并在对齐成功分支调用**

在 `lsc/core/services/timeline_service.py` 增加（或新建 `lsc/core/services/timeline_align.py`）：

```python
def build_room_snapshots_from_align(
    reference_room_id: str,
    offsets: dict[str, float],
    scores: dict[str, float],
    room_meta: dict[str, dict],
    confidence_threshold: float = _ALIGN_CONFIDENCE_THRESHOLD,
) -> dict[str, RoomTimeSnapshot]:
    ...  # 与测试相同逻辑
```

在 `handle_align_preview_audio` 写入 `content_offset`/`align_group_id` 成功后：

```python
# 伪代码位置：return success 之前
meta = {}
for rid in trusted:
    room = manager.get_room(rid)
    if room is None:
        continue
    meta[rid] = {
        "preview_epoch_id": getattr(room, "preview_epoch_id", "") or "",
        "recording_id": getattr(room, "recording_id", "") or "",
        "media_start_mono": getattr(room, "recording_media_start_mono", None)
            or getattr(room, "recording_start_mono", None)
            or 0.0,
    }
snaps = build_room_snapshots_from_align(
    result.reference_room_id, trusted, scores, meta
)
ctx = _timeline_svc.create_timeline(
    result.reference_room_id, snaps, required_room_ids=list(trusted.keys())
)
timeline_payload = None
if ctx is not None:
    timeline_payload = {
        "timeline_id": ctx.timeline_id,
        "reference_room_id": ctx.reference_room_id,
        "preview_ready": ctx.preview_ready,
        "clip_ready": all(
            bool(s.recording_id) for s in ctx.room_snapshots.values()
        ),
        "created_at": ctx.created_at,
        "room_snapshots": {
            rid: {
                "preview_epoch_id": s.preview_epoch_id,
                "recording_id": s.recording_id,
                "preview_to_common_delta": s.preview_to_common_delta,
                "recording_to_common_delta": s.recording_to_common_delta,
                "align_confidence": s.align_confidence,
                "media_start_mono": s.media_start_mono,
            }
            for rid, s in ctx.room_snapshots.items()
        },
    }
    bridge.queue_broadcast({"type": "timeline_ready", "timeline": timeline_payload})

return {
    "success": True,
    "offsets": result.offsets,
    "reference_room_id": result.reference_room_id,
    "method": result.method,
    "scores": result.correlation_scores,
    "align_group_id": group_id,
    "timeline": timeline_payload,  # 同步响应里也带一份，便于前端不依赖广播时序
}
```

- [ ] **Step 4: invalidate 时广播**

在 `TimelineService.invalidate_timeline` 增加可选 `on_invalidate` 回调，或在 handler 包装处于调用 invalidate 后：

```python
bridge.queue_broadcast({
    "type": "timeline_invalidated",
    "timeline_id": timeline_id,
    "reason": reason,
})
```

找到现有 `on_preview_epoch_change` 调用点，确保会触发广播（若尚无调用点，在 MSE 重建路径加一行 `get_timeline_service().on_preview_epoch_change(room_id, new_epoch)`）。

- [ ] **Step 5: 跑测试**

Run: `pytest tests/test_align_creates_timeline.py tests/test_timeline_service.py tests/test_clip_snapshot_handlers.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add lsc/core/services/timeline_service.py python-backend/handlers/room_handler.py tests/test_align_creates_timeline.py
git commit -m "feat: create TimelineContext on successful preview align"
```

---

### Task 2: delta 与导出一致性回归

**Files:**
- Create: `tests/test_timeline_delta_consistency.py`

- [ ] **Step 1: 写测试 — common→recording 与墙钟公式同号同结果**

```python
def test_common_export_matches_wallclock_formula():
    """同一事件：墙钟导出与 TimelineContext.common_to_recording 应一致。

    设定：reference r0 offset=0, r1 offset=+0.8（r1 领先）。
    用户在 common=20 切；r1 的 preview 当时为 19.2。
    """
    from lsc.core.services.timeline_service import TimelineService
    from tests.test_align_creates_timeline import build_snapshots_from_offsets

    # 若 helper 已迁到 production，改为 from lsc... import
    snaps = build_snapshots_from_offsets(
        "r0",
        {"r0": 0.0, "r1": 0.8},
        {"r0": 1.0, "r1": 0.9},
        {
            "r0": {"recording_id": "a", "media_start_mono": 1000.0},
            "r1": {"recording_id": "b", "media_start_mono": 1000.0},
        },
    )
    svc = TimelineService()
    ctx = svc.create_timeline("r0", snaps, required_room_ids=["r0", "r1"])
    assert ctx is not None

    common_t = 20.0
    # r1 preview local
    assert abs(ctx.common_to_preview("r1", common_t) - 19.2) < 1e-6
    rec_r1 = ctx.common_to_recording("r1", common_t)
    # recording_to_common_delta = media_start + preview_delta = 1000 + 0.8
    assert abs(rec_r1 - (common_t - 1000.8)) < 1e-6
```

若符号与现网 `export = wall - rec_start - content_offset` 冲突，**以夹具锁定后改 `build_room_snapshots_from_align` 公式**，不得静默两个公式并存。

- [ ] **Step 2: 跑通并 Commit**

```bash
pytest tests/test_timeline_delta_consistency.py -v
git add tests/test_timeline_delta_consistency.py lsc/core/services/*.py
git commit -m "test: lock timeline delta sign against wallclock export"
```

---

### Task 3: 前端类型与换算 helper

**Files:**
- Modify: `lsc-electron/src/types/index.ts`
- Create: `lsc-electron/src/utils/timelineCoords.ts`
- Create: `lsc-electron/src/utils/timelineCoords.test.ts`（若项目无 vitest，改为纯函数旁注释 + 后续手工；有 vitest 则写测试）

- [ ] **Step 1: 扩展类型**

```typescript
export interface RoomTimeSnapshot {
  preview_epoch_id: string
  recording_id: string
  preview_to_common_delta: number
  recording_to_common_delta: number
  align_confidence: number
  media_start_mono?: number
}

export interface TimelineContext {
  timeline_id: string
  reference_room_id: string
  preview_ready: boolean
  clip_ready: boolean
  created_at: number
  room_snapshots: Record<string, RoomTimeSnapshot>
}

export type TimelineAlignStatus = 'ready' | 'local' | 'invalidated'

export interface TimelineHighlightBand {
  id: string
  start: number
  end: number
  score?: number
  reason?: string
  label?: string
}
```

`ClipSegment` 增加可选：`clip_snapshot_id?: string`，`timeline_id?: string`，`common_start?: number`，`common_end?: number`。

- [ ] **Step 2: 实现换算**

```typescript
// lsc-electron/src/utils/timelineCoords.ts
import type { TimelineContext } from '@/types'

export function previewToCommon(ctx: TimelineContext, roomId: string, previewTime: number): number {
  const snap = ctx.room_snapshots[roomId]
  if (!snap) throw new Error(`room ${roomId} not in timeline`)
  return previewTime + snap.preview_to_common_delta
}

export function commonToPreview(ctx: TimelineContext, roomId: string, commonTime: number): number {
  const snap = ctx.room_snapshots[roomId]
  if (!snap) throw new Error(`room ${roomId} not in timeline`)
  return commonTime - snap.preview_to_common_delta
}

export function getAlignStatus(ctx: TimelineContext | null, invalidated: boolean): 'ready' | 'local' | 'invalidated' {
  if (invalidated) return 'invalidated'
  if (ctx?.timeline_id) return 'ready'
  return 'local'
}
```

- [ ] **Step 3: Commit**

```bash
git add lsc-electron/src/types/index.ts lsc-electron/src/utils/timelineCoords.ts
git commit -m "feat: add timeline coordinate helpers and typed TimelineContext"
```

---

### Task 4: Workbench 接入 timeline_ready / invalidated

**Files:**
- Modify: `lsc-electron/src/pages/Workbench/index.tsx`
- Modify: `lsc-electron/src/store/appStore.ts`（可选 `timelineInvalidatedReason`）

- [ ] **Step 1: 监听广播与对齐响应**

在现有 `align_preview_audio_response` 成功分支，除 `set_content_offset` 外：

```typescript
if (data.timeline) {
  setTimelineContext(data.timeline)
  setTimelineInvalidated(false)
} else {
  setTimelineContext(null)
}
```

另增：

```typescript
on('timeline_ready', (data) => {
  if (data?.timeline) {
    setTimelineContext(data.timeline)
    setTimelineInvalidated(false)
  }
})
on('timeline_invalidated', (data) => {
  setTimelineContext(null)
  setTimelineInvalidated(true)
  message.warning('公共轴已失效，请重新对齐')
})
```

- [ ] **Step 2: seek / I/O 在 common 模式统一**

```typescript
const ctx = useAppStore.getState().timelineContext
const handleTimelineSeek = (commonOrLocalTime: number) => {
  selectedRoomIds.forEach(rid => {
    if (ctx?.room_snapshots[rid]) {
      mseSeek(rid, Math.max(0, commonToPreview(ctx, rid, commonOrLocalTime)))
    } else {
      const offset = rooms.find(r => r.room_id === rid)?.content_offset ?? 0
      mseSeek(rid, Math.max(0, commonOrLocalTime - offset)) // 保持旧行为仅当无 ctx
    }
  })
}

const handleControlMarkIn = () => {
  const ctx = useAppStore.getState().timelineContext
  if (ctx) {
    // 用参考房间或第一个选中房间的 preview → common，写入所有选中房间的等价 preview mark
    const ref = [...selectedRoomIds].find(id => ctx.room_snapshots[id]) ?? [...selectedRoomIds][0]
    const previewT = getPreviewCurrentTime(ref)
    const commonT = previewToCommon(ctx, ref, previewT)
    selectedRoomIds.forEach(rid => {
      if (!ctx.room_snapshots[rid]) return
      const local = commonToPreview(ctx, rid, commonT)
      send('set_mark_in', { room_id: rid, time: local, live: true })
    })
    setCommonMarkIn(commonT) // 前端显示用 state，见 Task 5
  } else {
    // 现有 per-room live mark
    selectedRoomIds.forEach(rid => {
      const time = getPreviewCurrentTime(rid)
      send('set_mark_in', { room_id: rid, time, live: true })
    })
  }
}
```

出点同理。

- [ ] **Step 3: 手工验证对齐后 store 有 timeline_id；失效后清空**

- [ ] **Step 4: Commit**

```bash
git commit -m "feat: wire timeline_ready into workbench store and seek/mark"
```

---

### Task 5: ControlBar 对齐徽标 + Timeline 传 common 坐标

**Files:**
- Modify: `lsc-electron/src/pages/Workbench/components/ControlBar.tsx`
- Modify: `lsc-electron/src/pages/Workbench/index.tsx`

- [ ] **Step 1: 徽标 UI**

```tsx
const alignStatus = getAlignStatus(timelineContext, timelineInvalidated)
const badgeText =
  alignStatus === 'ready' ? '公共轴已就绪' :
  alignStatus === 'invalidated' ? '公共轴已失效 · 请重新对齐' :
  '未对齐 · 本地时间'
// 用现有 token：success / warning / secondary
```

- [ ] **Step 2: 向 Timeline 传入 common 模式数据**

当 `alignStatus === 'ready'`：

- `currentTime` = `previewToCommon(ctx, refRoomId, mseCurrent)`
- `markIn` / `markOut` = 前端 `commonMarkIn/Out` state（I/O 与拖拽维护）
- `duration` / `windowStart` 仍用录制时长逻辑，坐标系改为 common（与 ref preview 对齐时数值接近）
- `clips` / highlights 映射到 common

当 `local`：保持现有代表房间 props。

- [ ] **Step 3: Commit**

```bash
git commit -m "feat: show timeline align badge and feed common coords to Timeline"
```

---

### Task 6: 精确拖拽 + create_clip_snapshot 添加切片

**Files:**
- Modify: `lsc-electron/src/pages/Workbench/index.tsx`
- Modify: `lsc-electron/src/pages/Workbench/components/ClipList.tsx`
- Modify: `python-backend/handlers/room_handler.py`（`export_clip_by_id` 的 source 写死问题）

- [ ] **Step 1: 拖拽在 common 模式**

```typescript
const handleMarkerDragEnd = (type: 'in' | 'out', time: number) => {
  const ctx = useAppStore.getState().timelineContext
  if (ctx) {
    if (type === 'in') setCommonMarkIn(time)
    else setCommonMarkOut(time)
    selectedRoomIds.forEach(rid => {
      if (!ctx.room_snapshots[rid]) return
      const local = commonToPreview(ctx, rid, time)
      send(type === 'in' ? 'set_mark_in' : 'set_mark_out', {
        room_id: rid, time: local, live: false,
      })
    })
    // 不提示「近似」
    return
  }
  // 现有 approximate 路径保留
  ...
}
```

- [ ] **Step 2: 添加切片走 snapshot**

```typescript
const handleAddClipCommon = async () => {
  const ctx = useAppStore.getState().timelineContext
  if (!ctx || commonMarkIn == null || commonMarkOut == null) return
  const targetIds = [...selectedRoomIds].filter(id => ctx.room_snapshots[id])
  const res = await request('create_clip_snapshot', {
    timeline_id: ctx.timeline_id,
    common_start: commonMarkIn,
    common_end: commonMarkOut,
    target_room_ids: targetIds,
    source: 'manual',
  })
  if (!res?.success) {
    message.error(res?.error === 'RANGE_UNAVAILABLE' ? `时间范围不可用: ${res.failed_room}` : (res?.error || '创建失败'))
    return
  }
  for (const c of res.clips) {
    addClip({
      start: c.common_start,
      end: c.common_end,
      common_start: c.common_start,
      common_end: c.common_end,
      label: `片段`,
      room_id: c.room_id,
      clip_id: c.clip_id,
      clip_snapshot_id: c.clip_id,
      timeline_id: ctx.timeline_id,
      mark_precision: 'exact',
    })
  }
}
```

（若项目 `send` 无 request/response 封装，用现有 await bridge 模式：查 `websocket.ts` 是否有 `request`；若无，用 `send` + 一次性 `on('create_clip_snapshot_response')` 或 server 的标准 reply 机制——以 `server.py` 现有 handler 返回值为准。）

- [ ] **Step 3: 导出走 export_clip_by_id**

队列导出时若 `clip.clip_snapshot_id` 存在：

```typescript
send('export_clip_by_id', {
  clip_id: clip.clip_snapshot_id,
  label: clip.label,
  preset_id,
})
```

修复后端：

```python
source=snap.source or data.get('source', 'manual'),
```

不要写死 `ai_highlight`。

- [ ] **Step 4: ClipList 精确标签**

`mark_precision === 'exact'` 或存在 `clip_snapshot_id` → 不显示「近似」。

- [ ] **Step 5: Commit**

```bash
git commit -m "feat: precise drag and ClipSnapshot add/export path"
```

---

### Task 7: Timeline 高光色带 + 点击/磁吸/hover

**Files:**
- Modify: `lsc-electron/src/components/Timeline/index.tsx`
- Modify: `lsc-electron/src/components/Timeline/Timeline.css`
- Modify: `ControlBar.tsx` / `Workbench/index.tsx`（传入 highlights）

- [ ] **Step 1: 扩展 Timeline props**

```typescript
highlights?: TimelineHighlightBand[]
onHighlightClick?: (h: TimelineHighlightBand) => void
```

- [ ] **Step 2: 渲染色带**

在 selection 层旁 map highlights → `.lsc-timeline__highlight`（琥珀半透明）。  
`onClick` 停传播并 `onHighlightClick(h)`。  
`title` 或自定义 tooltip：`h.reason || h.label || 'AI 高光'` + score。

- [ ] **Step 3: 磁吸**

扩展 `findSnapTarget`：

```typescript
for (const h of highlights) {
  targets.push({ time: h.start, priority: 90 })
  targets.push({ time: h.end, priority: 90 })
}
```

- [ ] **Step 4: Workbench 映射高光**

从 `clips.filter(c => c.is_ai_highlight)` 生成 bands；有 ctx 时用 `common_start/end` 或 `previewToCommon`。

```typescript
onHighlightClick={(h) => {
  setCommonMarkIn(h.start)
  setCommonMarkOut(h.end)
  handleTimelineSeek(h.start)
}}
```

- [ ] **Step 5: Commit**

```bash
git commit -m "feat: render AI highlights on timeline with snap and click"
```

---

### Task 8: 波形峰值层

**Files:**
- Create: `lsc-electron/src/utils/waveformPeaks.ts`
- Modify: `Timeline/index.tsx`, `Timeline.css`
- Modify: `Workbench/index.tsx`（启动/停止采集）

- [ ] **Step 1: 峰值采集器**

```typescript
// waveformPeaks.ts
export class WaveformPeakBuffer {
  readonly bucketSec: number
  private peaks: Map<number, number> = new Map() // bucketIndex -> peak 0..1
  private raf = 0
  private analyser: AnalyserNode | null = null

  constructor(bucketSec = 0.05) { this.bucketSec = bucketSec }

  attach(video: HTMLVideoElement, audioCtx: AudioContext) {
    const source = audioCtx.createMediaElementSource(video)
    const analyser = audioCtx.createAnalyser()
    analyser.fftSize = 2048
    source.connect(analyser)
    // 注意：若 video 已有音频图，不要重复 createMediaElementSource；
    // 优先复用 previewAudioAligner 的 AudioContext 图，或只对「尚未连图」的元素 attach。
    this.analyser = analyser
  }

  start(getCommonTime: () => number) {
    const data = new Uint8Array(this.analyser!.fftSize)
    const tick = () => {
      this.analyser!.getByteTimeDomainData(data)
      let peak = 0
      for (let i = 0; i < data.length; i++) {
        const v = Math.abs(data[i] - 128) / 128
        if (v > peak) peak = v
      }
      const t = getCommonTime()
      const idx = Math.floor(t / this.bucketSec)
      const prev = this.peaks.get(idx) ?? 0
      this.peaks.set(idx, Math.max(prev, peak))
      // 丢弃窗口外：保留最近 4h
      const minIdx = idx - Math.floor(14400 / this.bucketSec)
      for (const k of this.peaks.keys()) {
        if (k < minIdx) this.peaks.delete(k)
      }
      this.raf = requestAnimationFrame(tick)
    }
    this.raf = requestAnimationFrame(tick)
  }

  stop() { cancelAnimationFrame(this.raf) }

  /** 返回覆盖 [start,end] 的峰值数组供绘制 */
  sample(start: number, end: number, bars: number): number[] {
    const out: number[] = []
    const span = Math.max(end - start, 1e-6)
    for (let i = 0; i < bars; i++) {
      const t = start + (span * i) / bars
      const idx = Math.floor(t / this.bucketSec)
      out.push(this.peaks.get(idx) ?? 0)
    }
    return out
  }
}
```

**集成约束：** 若 `createMediaElementSource` 与对齐 capture 冲突，改为在 Align Worklet 旁挂 `AnalyserNode`，或仅在参考房间 video 上、且对齐未占用时采集。优先「能画就不崩」。

- [ ] **Step 2: Timeline 绘制**

Props: `waveform?: number[]`。用 SVG polyline 或 canvas，高度 36px，颜色 `rgba(48,209,88,0.45)`。

- [ ] **Step 3: 仅参考房间；无音频静默**

- [ ] **Step 4: Commit**

```bash
git commit -m "feat: draw preview waveform peaks on common timeline"
```

---

### Task 9: 端到端验收与回归

- [ ] **Step 1: 后端回归**

```bash
pytest tests/test_align_creates_timeline.py tests/test_timeline_delta_consistency.py tests/test_timeline_service.py tests/test_clip_snapshot_handlers.py tests/test_synced_continuous_analysis.py -v
```

Expected: PASS

- [ ] **Step 2: 前端类型检查**

```bash
cd lsc-electron && npx tsc --noEmit
```

Expected: 无新增错误

- [ ] **Step 3: 手工清单（对照 spec §9.3）**

1. 多选 ≥2 房间 → 一键对齐 → 徽标「公共轴已就绪」
2. I/O 与拖拽设选区 → 添加切片 → 无「近似」→ 导出成功且多房间内容对齐
3. AI 高光色带可见；点击设选区；拖拽磁吸边界；hover 有 reason
4. 有声预览时波形可见
5. 重启预览或 invalidate → 徽标失效 → 重新对齐恢复
6. 单房间未对齐路径与改前一致

- [ ] **Step 4: 最终 Commit（若有修缮）**

```bash
git commit -m "fix: timeline UX polish from acceptance checklist"
```

---

## Self-review vs spec

| Spec 要求 | Task |
|-----------|------|
| 对齐后 create_timeline + 广播 | Task 1 |
| delta 符号锁定 | Task 2 |
| 单一公共轨 UI | Task 4–5 |
| 精确拖拽 + ClipSnapshot | Task 6 |
| 高光可视化+点击+磁吸+hover | Task 7 |
| 波形（无胶片） | Task 8 |
| 对齐徽标 | Task 5 |
| 未对齐降级 | Task 4–6 分支 |
| 非目标（多轨/胶片/undo） | 未列入 |

无 TBD 占位；`request` vs `send` 回复机制在 Task 6 要求实施时按 `server.py` 现有模式对齐。
