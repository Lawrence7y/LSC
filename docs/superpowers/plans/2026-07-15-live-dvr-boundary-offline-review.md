# Live DVR 左边界 · 下线分层 · 录制回看 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把时间线紫标改成 DVR 可回看左边界；分层处理 offline vs 网络错误；一期完成房间去叠层 UI；二期完成确认下线后的录制文件回看与全文时间线。

**Architecture:** 前端用房间级 `previewMode`（`live_mse` | `recording_review` | `degraded`）隔离直播跟播与文件回看，避免 `recording_local` 污染直播 `windowStart`。紫标在 live 模式映射 MSE `bufStart`（经 `previewToCommon`）。后端 `mse_error` / `recording_stopped` 携带显式 `reason`；仅 `offline` 触发停录+回看切换。房间卡片仅改布局，不改业务 IPC。

**Tech Stack:** Electron React/TS、Zustand、MSE `mediaSourcePlayer`、Python `room_handler` / `MultiRoomManager`、pytest 源码守卫测试。

**Spec:** [2026-07-15-live-dvr-boundary-offline-review-design.md](../specs/2026-07-15-live-dvr-boundary-offline-review-design.md)

---

## File map

| File | Responsibility |
|------|----------------|
| `lsc-electron/src/services/mediaSourcePlayer.ts` | 暴露 `getBufferedRange()`；二期可播文件源 |
| `lsc-electron/src/types/index.ts` | `previewMode`、`mse_error`/`recording_stopped` 的 `reason` |
| `lsc-electron/src/store/appStore.ts` | 房间字段含 `preview_mode`（若落 store） |
| `lsc-electron/src/components/Timeline/index.tsx` + `Timeline.css` | `dvrStart` 紫标（左边界）；回看模式无紫标 |
| `lsc-electron/src/pages/Workbench/components/ControlBar.tsx` | 传入 `dvrStart` 而非右沿 `recordedEnd` |
| `lsc-electron/src/pages/Workbench/index.tsx` | 越左回跟播；`mse_error` 按 reason 分支 |
| `lsc-electron/src/hooks/useWebSocket.ts` | `mse_error` 不再无条件关预览；认 `reason` |
| `lsc-electron/src/pages/Workbench/components/RoomCard.tsx` | 去叠层顶栏 LIVE/回看 |
| `python-backend/handlers/room_handler.py` | broadcast `reason`；offline 切文件 MSE（二期） |
| `lsc/gui/multi_room/manager.py` | offline 判定已有；停录时保留 path；二期文件预览入口 |
| `tests/test_frontend_stability_guards.py` | 更新紫标/错误分支守卫 |
| `tests/test_ux_important_followups.py` / 新测 | offline reason 广播守卫 |

---

## Phase 1 — 语义、误伤、房间 UI

### Task 1: `MediaSourcePlayer.getBufferedRange()`

**Files:**
- Modify: `lsc-electron/src/services/mediaSourcePlayer.ts`
- Test: `tests/test_frontend_stability_guards.py`（源码守卫）

- [ ] **Step 1: Write failing guard test**

在 `tests/test_frontend_stability_guards.py` 末尾追加：

```python
def test_mse_player_exposes_buffered_range() -> None:
    src = (ROOT / "lsc-electron/src/services/mediaSourcePlayer.ts").read_text(encoding="utf-8")
    assert "getBufferedRange(" in src
    assert "buffered.start(0)" in src
```

- [ ] **Step 2: Run test — expect FAIL**

Run: `pytest tests/test_frontend_stability_guards.py::test_mse_player_exposes_buffered_range -v`  
Expected: FAIL（`getBufferedRange` 不存在）

- [ ] **Step 3: Implement `getBufferedRange`**

在 `MediaSourcePlayer` 类中、`goLive()` 附近添加：

```typescript
/** 返回当前 SourceBuffer 可 seek 区间（preview 轴秒）；无缓冲则 null */
getBufferedRange(): { start: number; end: number } | null {
  if (!this._video || this._video.buffered.length === 0) return null
  const start = this._video.buffered.start(0)
  const end = this._video.buffered.end(this._video.buffered.length - 1)
  if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) return null
  return { start, end }
}
```

- [ ] **Step 4: Run test — expect PASS**

Run: `pytest tests/test_frontend_stability_guards.py::test_mse_player_exposes_buffered_range -v`

- [ ] **Step 5: Commit**

```bash
git add lsc-electron/src/services/mediaSourcePlayer.ts tests/test_frontend_stability_guards.py
git commit -m "feat(mse): expose getBufferedRange for DVR left edge"
```

---

### Task 2: Timeline 紫标改为 `dvrStart`（左边界）

**Files:**
- Modify: `lsc-electron/src/components/Timeline/index.tsx`
- Modify: `lsc-electron/src/components/Timeline/Timeline.css`（若需改 class 语义注释）
- Modify: `lsc-electron/src/pages/Workbench/components/ControlBar.tsx`
- Test: `tests/test_frontend_stability_guards.py`

- [ ] **Step 1: Update / add guard test**

将 `test_timeline_scrub_can_leave_live_edge` 相关断言扩展（或新增）：

```python
def test_timeline_dvr_start_prop() -> None:
    timeline = (ROOT / "lsc-electron/src/components/Timeline/index.tsx").read_text(encoding="utf-8")
    control = (ROOT / "lsc-electron/src/pages/Workbench/components/ControlBar.tsx").read_text(encoding="utf-8")
    assert "dvrStart" in timeline
    assert "dvrStart=" in control or "dvrStart={" in control
    # 紫标应对齐 dvrStart，不再用 recordedEnd 作为唯一紫标
    assert "dvrStart" in timeline.split("lsc-timeline__record-end")[0] or "dvrStartPct" in timeline
```

- [ ] **Step 2: Run — expect FAIL**

Run: `pytest tests/test_frontend_stability_guards.py::test_timeline_dvr_start_prop -v`

- [ ] **Step 3: Timeline props**

在 `Timeline` props 中：

- 保留或弃用 `recordedEnd`：本任务改为新增 `dvrStart?: number | null`。
- 紫标位置改为 `dvrStartPct = dvrStart != null ? clamp(((dvrStart - ws) / effectiveDuration) * 100, 0, 100) : null`。
- `applyPointerTime` / scrub：若 `time < dvrStart`，钳制到 `dvrStart`（或交给上层回跟播；本组件至少不允许画出左边界外的 scrub 目标）。
- `findSnapTarget`：对 `dvrStart` 加入 snap（priority 与原 record 点类似）。

CSS class 可继续用 `lsc-timeline__record-end`，但注释改为「DVR 左边界」。

- [ ] **Step 4: ControlBar 传入 dvrStart**

删除/停止 `recordedEnd={isLive ? contentEnd : null}`。

改为由 Workbench 传入（Task 3 接数据）；本步先在 ControlBar 增加 prop：

```tsx
dvrStart?: number | null
// ...
<Timeline
  ...
  dvrStart={dvrStart ?? null}
/>
```

若 ControlBar 暂时算不出 bufStart，先传 `null`（紫标隐藏），Task 3 接通。

- [ ] **Step 5: Run guards PASS + Commit**

```bash
git add lsc-electron/src/components/Timeline lsc-electron/src/pages/Workbench/components/ControlBar.tsx tests/test_frontend_stability_guards.py
git commit -m "feat(timeline): purple marker uses dvrStart left boundary"
```

---

### Task 3: Workbench — 缓冲左边界驱动紫标 + 越左回跟播

**Files:**
- Modify: `lsc-electron/src/pages/Workbench/index.tsx`
- Modify: `lsc-electron/src/pages/Workbench/components/ControlBar.tsx`（接线）

- [ ] **Step 1: Guard test for left-edge snap**

```python
def test_timeline_seek_snaps_left_of_dvr_to_live() -> None:
    workbench = (ROOT / "lsc-electron/src/pages/Workbench/index.tsx").read_text(encoding="utf-8")
    handler = workbench.split("const handleTimelineSeek = useCallback", 1)[1].split(
        "const handleTimelineScrubStart", 1
    )[0]
    assert "dvrStart" in handler or "bufStart" in handler
    assert "enterTimelineLive" in handler
```

Run — expect FAIL，然后实现。

- [ ] **Step 2: 收集参考房 bufStart 并换算到当前时间线轴**

在 Workbench（与 `mseSeek` / `timelineView` 同文件）：

```typescript
function getRoomBufferedRange(roomId: string): { start: number; end: number } | null {
  const player = msePlayersRef.current[roomId] // 以实际 ref 名为准
  return player?.getBufferedRange?.() ?? null
}
```

计算传给 ControlBar 的 `dvrStart`（common 轴就绪时用 `previewToCommon(roomId, bufStart)`，否则用 preview 轴值）。与 `contentEnd` 同一套坐标，避免混轴。

轮询或在 segment/`timeupdate` 时更新 state（可用 `requestAnimationFrame` / 现有 preview position tick，避免另开高频定时器）。

- [ ] **Step 3: Seek / scrubEnd / seekByDelta**

在现有「贴右沿 → enterTimelineLive」之外增加：

```typescript
const DVR_LEFT_TOLERANCE_SEC = 0.25
// clamped 为 seek 目标（与 contentEnd 同一轴）
if (dvrStart != null && clamped < dvrStart - DVR_LEFT_TOLERANCE_SEC) {
  enterTimelineLive(targets)
  return
}
```

贴右沿逻辑保留。

- [ ] **Step 4: 手动验证清单（开发模式）**

1. 开预览跟播，紫标应在轨道偏左（缓冲起点），不是最右。  
2. 拖到紫标左侧松手 → 回到跟播。  
3. 紫标右侧可回看几分钟内内容。  
4. 切片 I/O、导出仍可用。

- [ ] **Step 5: Commit**

```bash
git add lsc-electron/src/pages/Workbench/index.tsx lsc-electron/src/pages/Workbench/components/ControlBar.tsx tests/test_frontend_stability_guards.py
git commit -m "feat(workbench): drive DVR left edge and snap past-left to live"
```

---

### Task 4: 后端 `mse_error` / 停录广播带 `reason`

**Files:**
- Modify: `python-backend/handlers/room_handler.py`（`_on_mse_error` 耗尽分支、其它 `mse_error` broadcast）
- Modify: `lsc/gui/multi_room/manager.py`（offline 停录路径若发事件，带 `reason=offline`）
- Modify: `python-backend/message_bridge.py`（若 `recording_stopped` 已有 reason，确认透传）
- Test: `tests/test_room_handler_lifecycle.py` 或新建 `tests/test_mse_error_reason.py`

- [ ] **Step 1: Failing test — broadcast includes reason**

```python
def test_mse_error_broadcast_includes_reason_key() -> None:
    source = (ROOT / "python-backend/handlers/room_handler.py").read_text(encoding="utf-8")
    # 耗尽重连的 broadcast 必须带 reason
    chunk = source.split("MSE reconnect exhausted", 1)[1].split("return", 1)[0]
    assert "'reason'" in chunk or '"reason"' in chunk
```

- [ ] **Step 2: Implement**

所有 `broadcast('mse_error', {...})` 增加：

```python
'reason': 'network',  # 默认；若 refresh 判定 offline 则为 'offline'
```

在 `_on_mse_error` 重连循环内，若 `refresh_stream_url` / `_is_stream_offline_error` 为真：

```python
'reason': 'offline',
'error': humanize_error(...)  # 保持友好文案
```

录制侧确认 offline 停录时，确保前端能收到 `reason=offline`（优先扩展现有 `recording_stopped` 或 `rooms_updated` + 专用字段；不要只靠字符串「下线」）。

- [ ] **Step 3: Run tests PASS + Commit**

```bash
git add python-backend/handlers/room_handler.py lsc/gui/multi_room/manager.py tests/
git commit -m "feat(backend): tag mse_error and stop paths with reason"
```

---

### Task 5: 前端按 `reason` 分层处理（去掉误报下线）

**Files:**
- Modify: `lsc-electron/src/types/index.ts`
- Modify: `lsc-electron/src/hooks/useWebSocket.ts`
- Modify: `lsc-electron/src/pages/Workbench/index.tsx`
- Test: `tests/test_frontend_stability_guards.py`

- [ ] **Step 1: Types**

```typescript
// mse_error payload
mse_error: { room_id: string; error: string; reason?: 'offline' | 'network' | 'disk_full' | 'unknown' }
```

- [ ] **Step 2: Guard test**

```python
def test_mse_error_does_not_unconditionally_stop_recording() -> None:
    workbench = (ROOT / "lsc-electron/src/pages/Workbench/index.tsx").read_text(encoding="utf-8")
    block = workbench.split("on('mse_error'", 1)[1].split("return () =>", 1)[0]
    assert "reason" in block
    assert "主播已下线" in block
    # 停录必须受 reason === 'offline' 约束
    assert "offline" in block
```

- [ ] **Step 3: Workbench handler**

替换现有「任意 mse_error → stop_recording + 主播已下线」：

```typescript
unsubs.push(on('mse_error', (data: { room_id?: string; error?: string; reason?: string }) => {
  if (!data?.room_id) return
  const reason = data.reason || 'unknown'
  const r = useAppStore.getState().rooms.find(x => x.room_id === data.room_id)
  if (reason === 'offline') {
    if (r?.is_recording) send('stop_recording', { room_id: data.room_id })
    message.warning('主播已下线，录制已保存' + (/* 二期: 可回看 */ ''), 5)
    return
  }
  message.warning(data.error || '预览异常，请检查网络或重试预览', 5)
}))
```

- [ ] **Step 4: useWebSocket**

`mse_error` 处理改为：

- 写入 `mse_error` / `preview_phase`  
- **仅当** `reason !== 'offline'`（或一期：一律先不关预览，改为 `preview_phase: 'error'`）时再 `preview_enabled: false`  
- 一期最小正确行为：`reason === 'network' | 'unknown'` → 可关预览；`reason === 'offline'` → **保持** `preview_enabled`（二期切文件；一期至少不立刻清掉，便于后续 Task）

推荐一期实现：

```typescript
const reason = data.reason || 'unknown'
useAppStore.getState().updateRoom(data.room_id, {
  mse_error: data.error,
  mse_reconnecting: undefined,
  ...(reason === 'offline'
    ? { preview_phase: 'error' as const } // 二期再切 recording_review
    : { preview_enabled: false, preview_phase: 'error' as const }),
})
```

- [ ] **Step 5: Commit**

```bash
git add lsc-electron/src/types/index.ts lsc-electron/src/hooks/useWebSocket.ts lsc-electron/src/pages/Workbench/index.tsx tests/test_frontend_stability_guards.py
git commit -m "fix(preview): gate offline stop/toast on mse_error reason"
```

---

### Task 6: 房间卡片去叠层（不影响业务）

**Files:**
- Modify: `lsc-electron/src/pages/Workbench/components/RoomCard.tsx` only

- [ ] **Step 1: Guard — LIVE not absolutely positioned on preview corners with status cluster**

可选轻量守卫（或手工验收）：顶栏结构含 `room-card__header` / LIVE pill 在 preview **之外**。

- [ ] **Step 2: 重构布局**

按 spec §3：

1. 预览区**上方**顶栏：`checkbox | streamer_name | LIVE pill`（`isLive` 时）  
2. 从 preview 内移除左上 status+LIVE 叠层、右上时长/体积叠层  
3. preview **下方**元信息行：`录制中 · 01:24:08 · 812 MB`（录制时）  
4. 保留 preview 内**仅**底部渐变工具条 + 录制底边脉冲线  
5. 标题行只放 `stream_title`（「已选中」可留在顶栏名旁或标题旁，勿叠画面）  
6. 操作按钮行逻辑**一字不改**（仍调用原 onStartRecord / onStopRecord 等）

- [ ] **Step 3: 手工验收**

- 多选勾选、连接、录制、预览、静音、放大、删除均可用  
- 窄卡片下顶栏胶囊与名字不互相覆盖（ellipsis）  

- [ ] **Step 4: Commit**

```bash
git add lsc-electron/src/pages/Workbench/components/RoomCard.tsx
git commit -m "refactor(ui): declutter RoomCard badges into header and meta row"
```

---

### Task 7: Phase 1 回归

- [ ] **Step 1: Run frontend guards**

```bash
pytest tests/test_frontend_stability_guards.py -v --tb=short
```

Expected: PASS

- [ ] **Step 2: Run related backend tests**

```bash
pytest tests/test_room_handler_lifecycle.py tests/test_ux_important_followups.py -v --tb=short
```

Expected: PASS（若个别与 reason 无关失败，先记录再修）

- [ ] **Step 3: Smoke（已开 `npm run dev`）**

- 预览 + 拖时间线左右边界  
- 人为断网或杀预览进程：不应误报「主播已下线」除非 reason=offline  
- 导出一条手动切片  

- [ ] **Step 4: Commit 若有测试修缮**

```bash
git commit -m "test: align Phase1 DVR/offline guards"
```

---

## Phase 2 — 录制文件回看闭环

### Task 8: 类型与 `previewMode`

**Files:**
- Modify: `lsc-electron/src/types/index.ts`
- Modify: store / rooms payload 映射（`useWebSocket` `rooms_updated`）

```typescript
preview_mode?: 'live_mse' | 'recording_review' | 'degraded'
```

后端 `rooms_list` 或专用事件设置该字段。一期未用时默认 `live_mse`。

- [ ] Commit: `feat(types): add preview_mode for recording review`

---

### Task 9: 后端 — offline 后文件 MSE 预览

**Files:**
- Modify: `python-backend/handlers/room_handler.py`
- Modify: `lsc/core/services/mse_streamer.py`（若需 file 输入）
- Modify: `lsc/gui/multi_room/manager.py`

- [ ] **Step 1: 入口**

确认 offline 且 `record_output_path` 通过 `validate_recording`：

1. `stop_recording`（若仍在录）  
2. 停止 live CDN MSE  
3. 启动以**本地文件**为输入的 MSE（`-i path`，同样 fMP4 分片协议）  
4. `preview_enabled=True`，`preview_mode='recording_review'`  
5. broadcast `rooms_updated` + 可选 `preview_mode_changed`

无有效文件 → `preview_mode='degraded'`，友好 error。

- [ ] **Step 2: 测试**

源码/单元：offline 路径调用文件输入；无文件不 start file mse。

- [ ] Commit: `feat(preview): switch to file-backed MSE on confirmed offline`

---

### Task 10: 前端 — `recording_review` 时间线

**Files:**
- Modify: `Workbench/index.tsx`、`ControlBar.tsx`、`Timeline/index.tsx`、`RoomCard.tsx`

规则：

| | live_mse | recording_review |
|--|----------|------------------|
| `dvrStart` | bufStart | `null`（无紫标） |
| `followLive` | 可用 | 强制 false |
| 轴 | preview/common | `0..recorded_duration`（**仅此模式**允许 recording 轴撑窗口） |
| 胶囊 | LIVE | 回看 |

Seek：文件 MSE 上按需 `currentTime`；越界钳制 `[0, duration]`。

- [ ] Commit: `feat(timeline): recording_review full-span seek without DVR purple marker`

---

### Task 11: Phase 2 回归 + 文档

- [ ] 多房：一房 offline 不影响其它房 live  
- [ ] 持续分析：offline 停录后收到 `continuous_analysis_complete`  
- [ ] 更新 spec 状态为「已实现」；必要时补 CLAUDE.md 一句紫标语义（仅当用户要求改权威文档时再做）

---

## Self-review vs spec

| Spec 项 | Task |
|---------|------|
| 紫标左边界 B | 2, 3 |
| LIVE 在房间卡 | 6 |
| 分层错误 C | 4, 5 |
| 房间去叠层 | 6 |
| 下线回看混合加载 | 9, 10 |
| 分析 finalize | 5（停录）+ 现有 loop；9 确保停录 |
| 分期 | Phase1 Tasks 1–7；Phase2 8–11 |
| 不影响切片墙钟 | 全程禁止改 export 映射公式 |

无 TBD。类型名统一：`preview_mode`、`reason`、`dvrStart`、`getBufferedRange`。

---

## Execution handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-15-live-dvr-boundary-offline-review.md`.

**Two execution options:**

1. **Subagent-Driven (recommended)** — 每任务新开子代理，任务间复查  
2. **Inline Execution** — 本会话按 executing-plans 批量推进并设检查点  

你更想用哪种？
