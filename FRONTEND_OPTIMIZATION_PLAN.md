# 前端性能优化计划（时间线区重点）

**创建时间**: 2026-07-22
**目标文件**: `lsc-electron/src/pages/Workbench/index.tsx` 及相关组件
**核心目标**: 消除卡顿、降低交互延迟，同时**不减少任何现有功能**、**不改变用户已习得的操作直觉**

---

## 〇、设计约束（先于一切优化）

### 0.1 用户操作直觉冻结清单（已复核，全部保留）

以下交互已被用户习得，优化过程中**行为必须逐位保持不变**：

| 区域 | 交互 | 现状实现位置 | 保留要求 |
|------|------|-------------|----------|
| 时间线 | 拖拽 scrub：本地 `dragTime` 即时跟手，松手才正式 seek | `Timeline/index.tsx:298-364` | ✅ 保持"拖拽不 seek、松手一次落点" |
| 时间线 | 磁吸 snap：mark > highlight > clip > dvr > playhead > tick 分级 | `Timeline/index.tsx:116-156` | ✅ 保持阈值 0.85s 与优先级 |
| 时间线 | Shift+单击=入点，Ctrl+单击=出点 | `Timeline/index.tsx:385-394` | ✅ 保持 |
| 时间线 | 右键 marker=删除该标记 | `Timeline/index.tsx:628-632, 645-649` | ✅ 保持 |
| 时间线 | Ctrl+滚轮缩放 1-20x，左缘锚定 windowStart | `Timeline/index.tsx:423-435` | ✅ 保持 |
| 时间线 | 跟随直播 followLive：贴右沿；scrub/步进退 Live；点「直播」恢复 | `Workbench/index.tsx:262, 1305-1314` | ✅ 保持状态机 |
| 时间线 | DVR 紫标左边界：拖过紫标左沿回 Live | `Workbench/index.tsx:1307-1310` | ✅ 保持容差 0.25s |
| 时间线 | 精修模式硬色带 + 顶部提示条 | `Workbench/index.tsx:3590-3598` | ✅ 保持 |
| 控制栏 | 播放/暂停、±10s、倍速、入/出点、添加切片、试听选区、直播 | `ControlBar.tsx:395-582` | ✅ 保持全部按钮与快捷键 |
| 快捷键 | I/O 入出点、空格播放、方向键步进、Shift+,/. 倍速 | `Workbench/index.tsx:3106-3160` | ✅ 保持全部映射 |
| 房间卡 | Ctrl/Shift 多选、checkbox 切换、放大预览 | `Workbench/index.tsx:1053-1112` | ✅ 保持 |
| 多选 | 多选横幅提示"时间线/入出点/播放控制全局生效" | `ControlBar.tsx:346-362` | ✅ 保持 |
| 切片列表 | 单条确认并导出、批量导出、进度展示、取消 | `ClipList.tsx` | ✅ 保持 |

### 0.2 设计一致性要求

优化代码必须沿用项目已有的成熟模式：

1. **memo + 自定义比较器模式**：`areRoomPropsEqual` / `areControlBarPropsEqual` 已是项目惯例，新增组件或修改 props 时必须同步维护比较器
2. **ref 稳定回调模式**：`Timeline/index.tsx:208-215` 已将回调存入 ref（`onSeekRef` 等），新回调一律沿用此模式避免闭包重建
3. **乐观 UI 模式**：`handleToggleMute`（`index.tsx:965-981`）先写 store 再 send，新交互保持一致
4. **CSS 变量体系**：`var(--bg-secondary)` / `var(--accent-primary)` 等，新增样式不得硬编码颜色

---

## 一、时间线区性能瓶颈精确定位

### 1.1 当前渲染链路（问题路径）

```
setInterval 200ms (index.tsx:271-307)
  └─ 读取 window.__msePlayers[*].videoElement.currentTime
  └─ setPreviewPositions(next)                    ← 全局 setState
      └─ Workbench 整体重渲染（3875 行组件）
          ├─ timelineView useMemo 重算 (index.tsx:406-532)
          │   ├─ 遍历 clips 全量 map/filter（O(n) 坐标转换）
          │   ├─ resolveLiveContentSpan / panTimelineWindowStart
          │   └─ 返回新对象引用（每次必然新引用）
          ├─ dvrStart useMemo 重算 (index.tsx:535-562)
          │   └─ resolveDvrSourceRoomId 遍历候选房间
          ├─ activeRefineRange useMemo 重算 (index.tsx:3011-3061)
          └─ ControlBar 渲染（memo 被 timelineView 新引用击穿）
              ├─ areControlBarPropsEqual 比较 30+ 字段（开销小但存在）
              ├─ localTimeline/timelineView 二选一换算
              └─ Timeline 渲染
                  ├─ ticks useMemo 重算（windowStart 随 contentEnd 增长而变化）
                  └─ 30+ 个绝对定位 div 重建
```

**实测影响**：每 200ms 一次完整链路 ≈ **每秒 5 次全页面渲染**，播放预览时 React 主线程持续繁忙，拖拽 scrub、快捷键响应被抢帧。

### 1.2 高频事件二次击穿

| 事件 | 频率 | 击穿点 |
|------|------|--------|
| `export_progress` | 4-8 次/秒 | `setClips(map(...))` 全量复制数组 → clips 引用变 → timelineView/ClipList 重算 |
| `rooms_updated` | 每 5 秒全量 | rooms 引用变 → `handleTogglePreview` 等回调重建 → RoomCard memo 失效 |
| `timelineTick` | 录制中 1 次/秒 | 仅用于 recordedDurationHint，却让整个 timelineView 重算 |
| ControlBar 内部 tick | 录制中 1 次/秒 | 合理（局部），但与父级 tick 叠加 |

---

## 二、优化方案（按实施顺序）

### 阶段 A：时间线播放头直写（收益最大，改动最聚焦）

#### A1. 播放头位置改为「订阅式 ref + rAF 直写 DOM」，移除 200ms setState 轮询

**问题**：`previewPositions` 是播放头唯一数据源，但它驱动的是**纯视觉元素**（播放头圆点、时间码文本），却被提升为 React state，导致全组件树为重绘买单。

**方案**：引入 `playheadStore`（模块级订阅器），播放头位置**不进 React state**：

```typescript
// 新增文件: lsc-electron/src/utils/playheadStore.ts
type Listener = (positions: Readonly<Record<string, number>>) => void

const positions: Record<string, number> = {}
const listeners = new Set<Listener>()
let rafId: number | null = null
let dirty = false

/** 由 200ms 采样循环调用（替代原 setPreviewPositions） */
export function writePlayhead(roomId: string, t: number) {
  if (Math.abs((positions[roomId] ?? -1) - t) <= 0.01) return
  positions[roomId] = t
  dirty = true
  scheduleFlush()
}

export function readPlayhead(roomId: string): number {
  return positions[roomId] ?? 0
}

export function subscribePlayhead(fn: Listener): () => void {
  listeners.add(fn)
  return () => listeners.delete(fn)
}

function scheduleFlush() {
  if (rafId !== null) return
  rafId = requestAnimationFrame(() => {
    rafId = null
    if (!dirty) return
    dirty = false
    const snapshot = { ...positions }
    listeners.forEach(fn => fn(snapshot))
  })
}
```

**Timeline 组件内**新增一个**不进 React 渲染周期**的播放头图层：

```typescript
// Timeline/index.tsx 新增
const playheadRef = useRef<HTMLDivElement>(null)
const timecodeRef = useRef<HTMLSpanElement>(null)

// 父级通过 ref 暴露 imperative 接口，直接移动播放头，不触发 render
useEffect(() => {
  return subscribePlayhead((positions) => {
    const t = positions[roomIdRef.current]
    if (t == null || !playheadRef.current) return
    const rel = commonToPreviewAxis(t)  // 复用现有换算
    const pct = clamp((rel / effectiveDurationRef.current) * 100, 0, 100)
    playheadRef.current.style.left = `${pct}%`   // 直写 DOM，60fps 流畅
    if (timecodeRef.current) timecodeRef.current.textContent = formatTime(t)
  })
}, [])
```

**关键设计决策**：
- `previewPositions` state **保留**（`timelineView` 的 `curCommon` 仍需它计算 contentEnd），但**只有数值变化超阈值才 setState**，且频率降回 500ms
- 播放头视觉层改由 rAF 直写 DOM（60fps），与 React render 解耦 —— 这是"播放头更平滑"（S4 注释的原始意图）的正确实现方式，而非提升轮询频率
- scrub 拖拽期间行为不变：`timelineScrubbingRef.current` 跳过采样（现有逻辑 L274 保留）

**涉及修改**：
- `Workbench/index.tsx:255-307`：轮询体内 `setPreviewPositions` 改为「阈值过滤 + writePlayhead + 500ms 低频 setState」
- `Timeline/index.tsx`：播放头 div 挂 ref，新增订阅 effect；`progressPct` 仍由 props 渲染（初始值/seek 后对齐）
- `ControlBar.tsx:476-494`：时间码文本挂 ref 直写（可选，P2）

**功能不变性验证**：播放头位置、时间码、seek 后对齐、Live 钉右沿、scrub 冻结 —— 全部逐点比对。

#### A2. timelineView 计算分片：把「必变部分」与「稳定部分」拆开

**问题**：`timelineView` useMemo 依赖 14 个值，其中 `previewPositions`/`timelineTick` 高频变化，导致整个大对象每 200-500ms 重建；但 `clipBlocks`（clips 坐标转换 O(n)）和窗口公式大部分输入是稳定的。

**方案**：拆成三层 memo，各自独立失效：

```typescript
// 第一层：clips 坐标转换（仅在 clips/timelineContext 变化时重算）
const clipBlocks = useMemo(() => {
  if (!commonMode || !timelineContext) return []
  return clips
    .filter(c => !c.is_ai_highlight && c.end > c.start)
    .map(c => { /* 现有转换逻辑原样保留 */ })
    .filter(Boolean)
}, [commonMode, timelineContext, clips])   // ← 不再依赖 previewPositions

// 第二层：contentEnd 右沿（仅依赖播放头与 clips 的 max，增量维护）
const contentEnd = useMemo(() => {
  // 复用现有 resolveLiveContentSpan 逻辑，输入改为 clipBlocks
}, [curCommon, clipBlocks, recordedHint, ...])

// 第三层：窗口 windowStart（依赖 contentEnd + followLive/scrubbing 状态机）
const windowStart = useMemo(() => {
  // 现有 panTimelineWindowStart 逻辑原样保留
}, [contentEnd, curCommon, timelineFollowLive, timelineScrubbing, frozenWindowStart, refineRange])

// 最终组装（轻量，字段拷贝）
const timelineView = useMemo(() => ({
  duration, currentTime: liveCur, windowStart, markIn, markOut,
  clips: clipBlocks, highlights: timelineHighlights, contentEnd,
}), [/* 各层输出 */])
```

**收益**：`previewPositions` 每 500ms 变化时，只有第二、三层重算（O(1) 数学），clips 的 O(n) 转换不再被触发。

**涉及修改**：`Workbench/index.tsx:406-532`（重构成三个 memo，逻辑逐行搬迁不改语义）。

#### A3. Timeline 刻度 ticks 缓存

**问题**：`ticks` useMemo 依赖 `ws`（windowStart），followLive 模式下 ws 随 contentEnd 每秒增长，导致刻度数组每秒重建（几十到上百个对象 + sort）。

**方案**：刻度按「绝对时间对齐」已是现状（`index.tsx:467-468` 注释），因此只需把缓存键从 `ws` 改为量化窗口：

```typescript
// 刻度只关心 [floor(ws), ws+duration] 区间内的绝对整点；ws 微小移动时集合不变
const quantizedWs = Math.floor(ws / tickInterval) * tickInterval
const ticks = useMemo(() => {
  // 现有逻辑，内部用真实 ws
}, [effectiveDuration, tickInterval, quantizedWs])   // ← ws 变化 < tickInterval 时不重算
```

**收益**：followLive 时窗口每秒右移 1s，但 tickInterval 通常 ≥ 5s，刻度重建频率降为原来的 1/5~1/30。

### 阶段 B：高频事件节流与引用稳定

#### B1. export_progress 更新节流合并

**问题**：`export_progress` 4-8 次/秒，每次 `setClips(clips.map(...))` 全量复制 + `setExportProgressMap` 再次 setState。

**方案**（不改消息协议，仅前端合帧）：

```typescript
// index.tsx:783-838 改造
const exportProgressBufferRef = useRef<Map<string, ExportProgressInfo>>(new Map())
const exportFlushScheduledRef = useRef(false)

unsubs.push(on('export_progress', (data: any) => {
  if (!data?.job_id || typeof data.percent !== 'number') return
  exportProgressBufferRef.current.set(data.job_id, {
    percent: data.percent, elapsed: data.elapsed ?? 0, total: data.total ?? 0,
  })
  if (exportFlushScheduledRef.current) return
  exportFlushScheduledRef.current = true
  requestAnimationFrame(() => {          // 每帧最多一次合并刷新
    exportFlushScheduledRef.current = false
    const batch = exportProgressBufferRef.current
    if (batch.size === 0) return
    const updates = new Map(batch)
    batch.clear()
    setExportProgressMap(prev => ({ ...prev, ...Object.fromEntries(updates) }))
    // clips 的 export_status 仅在「状态跃迁」时更新（pending→exporting→completed），
    // 进度百分比不进 clips —— 保持 clips 引用稳定，timelineView 不被击穿
  })
}))
```

**关键设计决策**：进度百分比**只进 exportProgressMap**（局部于 ClipList 进度条），**不进 clips 数组**。`export_status` 跃迁（queued/exporting/completed/failed）才更新 clips —— 这与现状语义一致（现状也只有状态字段进 clips），功能不变。

#### B2. 回调引用稳定化（修复 RoomCard memo 失效）

**问题**：`handleTogglePreview` 依赖 `rooms`（L1004-1017），rooms_updated 每 5s 重建回调 → 12 张 RoomCard 全部重渲染。同理审查所有传给 RoomCard/ControlBar 的回调。

**方案**：沿用 `handleToggleMute` 已验证的模式 —— 回调内用 `useAppStore.getState()` 现取，从依赖数组移除 `rooms`：

```typescript
const handleTogglePreview = useCallback((roomId: string, enabled: boolean) => {
  if (enabled) {
    const currentRooms = useAppStore.getState().rooms   // ← 现取，非闭包捕获
    const activePreviews = currentRooms.filter(r => r.preview_enabled && r.room_id !== roomId).length
    if (activePreviews >= 4) { message.warning('最多 4 路同时预览，请先关闭一路'); return }
    if (activePreviews >= 3) { message.info('多路预览已自动降画质以保证流畅', 3) }
  }
  send('enable_preview', { room_id: roomId, enabled, mode: 'mse' })
}, [send])   // ← rooms 移出依赖
```

**逐一审查清单**（index.tsx 内所有 useCallback）：

| 回调 | 当前依赖 | 处理 |
|------|---------|------|
| `handleTogglePreview` L1004 | `[send, rooms]` | 改为 `[send]`（getState 现取）|
| `handleTimelineSeek` L1290-1330 | 含 `rooms` | 同上 |
| `handleTimelineScrubEnd` L1340 | 含 `rooms` | 同上 |
| `handleSeekByDelta` L1433 | 含 `rooms, selectedRoomIds...` | getState 现取 rooms |
| `handleNudgeMark` L1485 | 含 `commonMarkIn/Out` | 用 ref 镜像（已有 refiningClipIdRef 模式）|
| `handleSelect/handleToggleMultiSelect` | `[lastClickedIndex]` | 合理，保留 |

**注意**：`commonMarkIn/commonMarkOut` 进 ref 时需保证「写 state 同时写 ref」（项目已有 `refiningClipIdRef.current = refiningClipId` 同款写法，L209-210）。

### 阶段 C：结构性优化（中期，不改交互）

#### C1. ClipList 虚拟化（功能零变化）

**问题**：200 条切片全量 DOM，每条含 Progress/Tooltip/Button 组。export 进度刷新时全列表 reconcile。

**方案**：antd List 换 `react-window` 的 `FixedSizeList`（项目未引入该依赖时，用简版自研：可视区 ± overscan 5 条）：

```typescript
// ClipList.tsx 结构保持对外 props 完全不变，仅内部渲染层替换
const ROW_HEIGHT = 72   // 与现有卡片实际高度一致（CSS 已固定）
const [scrollTop, setScrollTop] = useState(0)
const visibleRange = useMemo(() => {
  const start = Math.max(0, Math.floor(scrollTop / ROW_HEIGHT) - 5)
  const end = Math.min(clips.length, Math.ceil((scrollTop + viewportH) / ROW_HEIGHT) + 5)
  return [start, end]
}, [scrollTop, viewportH, clips.length])
// 外层容器高度 = clips.length * ROW_HEIGHT，内部 absolute 定位可见项
```

**保留**：多选 checkbox、批量操作栏、进度条、hover 菜单、排序 —— 逐项渲染逻辑原样搬入 row renderer。

#### C2. RoomCard 录制计时集中化

**问题**：`RoomCard.tsx:132-142` 每卡 1s setInterval，12 卡 = 12 个定时器。

**方案**：appStore 增加 `recordingTick`（单一 1s 定时器在 Workbench 顶层，已有 `timelineTick` L309-315 可复用），RoomCard 通过 props 接收 `tick`，或显示层改用「起始时间戳 + CSS 动画」：

```typescript
// 最小改动版：RoomCard 内定时器保留，但 12 卡共享同一 tick 源
// Workbench 已有 timelineTick（L309），通过 context 下发
const TickContext = createContext(0)
// RoomCard: const tick = useContext(TickContext)  —— 只重渲染文本节点
```

**注意**：`timelineTick` 当前依赖 `rooms.some(...)` 判断录制中（L311），集中化后行为不变。

#### C3. Workbench 组件拆分（长期，仅物理拆分不改逻辑）

3875 行单文件拆为同目录下模块（**纯代码搬迁，逻辑逐行不变**）：

```
Workbench/
  index.tsx                    ← 布局 + 组合（~800 行）
  hooks/
    useTimelineView.ts         ← timelineView/dvrStart/activeRefine 三个 memo
    usePlayheadSampling.ts     ← 200/500ms 采样循环
    useExportProgress.ts       ← export_progress 节流
    useRoomActions.ts          ← 录制/预览/静音/删除等回调
```

拆分以「自定义 hook 为单位」，state 不下移、不重命名，确保 git diff 可审查。

---

## 三、实施顺序与回归验证

### 执行顺序（每步独立可回滚）

| 步骤 | 内容 | 预估收益 | 风险 |
|------|------|---------|------|
| A1 | 播放头订阅式直写 + 轮询降频 500ms | **渲染次数 -60%**，播放头反而更顺滑 | 低（视觉层解耦）|
| B2 | 回调引用稳定化（handleTogglePreview 等）| rooms_updated 时 12 卡零重渲染 | 低 |
| B1 | export_progress rAF 合帧 | 导出期 UI 恢复可用 | 低 |
| A2 | timelineView 三层拆分 | 播放中 clips O(n) 重算消除 | 中（需逐行核对语义）|
| A3 | ticks 量化缓存 | 刻度重建 -80% | 低 |
| C2 | RoomCard tick 集中 | 定时器 12→1 | 低 |
| C1 | ClipList 虚拟化 | 200 切片滚动流畅 | 中（需测多选/滚动定位）|
| C3 | 组件物理拆分 | 可维护性 | 零（纯搬迁）|

### 回归验证清单（每步必跑）

**交互直觉逐项验证**（对应 0.1 冻结清单）：
1. 拖拽播放头：跟手、磁吸闪烁、松手 seek 一次（不多不少）
2. Shift/Ctrl 单击打入/出点；右键删 marker
3. Ctrl+滚轮缩放，刻度不抖动
4. Live 钉右沿 → scrub 退出 → 「直播」按钮恢复
5. 拖过 DVR 紫标左沿回 Live
6. 多选 2 房：横幅出现，seek/mark 双房同步
7. 精修模式：色带 + 顶部提示条 + I/O 键微调
8. I/O/空格/方向键/倍速快捷键全部可用
9. 添加切片、试听选区循环
10. 导出进度条实时刷新（合帧后 ≤1 帧延迟，人眼无感）

**自动化验证**：
```bash
# 现有测试必须全绿（含 test_ux_habit_guards 中对 ClipList 结构的断言）
cd lsc-electron && npm run test
# 性能基准：React DevTools Profiler 录制 30s 预览场景
# 目标：render 次数从 ~150 次/30s 降至 <40 次/30s；单次 render <16ms
```

**功能不变性红线**：
- `timelineView` 输出对象的每个字段在相同输入下逐值相等（A2 重构后写对照测试）
- `areControlBarPropsEqual` 比较器字段随 props 增减同步维护
- 所有 message 提示文案、disabled 条件、tooltip 内容不变

---

## 四、预期效果汇总

| 指标 | 优化前 | 优化后（A+B 完成）|
|------|--------|------------------|
| 预览播放时全页面渲染 | 5 次/秒 | ~1 次/秒（仅 contentEnd 增长时）|
| 播放头流畅度 | 200ms 阶梯跳动 | 60fps rAF 直写 |
| 导出 4 任务时 UI | 近不可用 | 正常交互 |
| rooms_updated(5s) 引发的重渲染 | 12 卡 + 控制栏 | 仅变化卡片 |
| ClipList 200 条滚动 | 掉帧 | 稳定 60fps |
| 功能完整性 | — | **100% 保留（红线）**|
