# 主时间线 1x 从零 + 预览紫线窗 + rAF 实时时钟

**日期：** 2026-08-06  
**状态：** 待用户审阅  
**范围：** 前端时间线窗口语义 + 预览条 DVR 左端 + 程序内时间显示刷新频率  
**明确不做（本轮）：** 持续分析 OCR / 出入点算法（另开一轮）

---

## 1. 背景与目标

当前行为问题：

1. **主时间线 1x + Live + 内容 > 600s** 时，`windowStart = contentEnd − 600`，左端标签是「现在 − 10 分钟」，不是 `0:00:00`。
2. **预览区时间线**左端往往在紫线（DVR 左边界）左侧再留一段 lead，与「左边 = 紫线 = 实时约 2 分钟前」不一致。
3. **时间文案**多依赖 500ms–1000ms（分析进度甚至 5s）的 React tick；播放头已是 rAF，时钟不同步、观感「跳秒」。

用户已确认：

| 决策 | 选择 |
|------|------|
| 主时间线 1x 左端 | 永远绝对 `0:00:00`，整段压缩进视口 |
| zoom > 1x | 局部窗口，以播放头 / 直播沿为中心，左端可以不是 0 |
| 预览区时间线左端 | = 紫线 = 实时 − 2 分钟（预览轴） |
| 时钟刷新 | 与播放头同源，`requestAnimationFrame`（~60fps） |
| 持续分析 | 本轮不做 |

---

## 2. 坐标系约束（CLAUDE.md §8.7）

Live / 预览相关窗口与紫线计算 **只允许** 使用：

- `preview_local`（MSE `currentTime` / buffered），或  
- `common`（对齐后公共轴）

**禁止**用 `record_started_at` 墙钟差或 `recorded_duration` 驱动 Live 的 `windowStart` / 预览左端 / 紫线位置（会导致播放头被钳到 0%）。

「实时 − 2 分钟」的实现契约：

```
liveEdge = buffered.end（或 followLive 时 contentEnd / preview 沿）
purple   = max(0, liveEdge - 120)
```

在 MSE 稳态下 `buffered.start` 已约等于 `buffered.end − 120`；本轮以 **显式 `liveEdge − 120`** 作为紫线与预览左端的产品定义，避免 buffer 未满时左端乱漂。

---

## 3. 主时间线窗口语义

### 3.1 1x（`zoomLevel === 1`）且非精修窗

```
windowStart = 0
contentEnd  = 既有 resolveLiveContentSpan / common 轴逻辑（单调非减）
duration    = max(contentEnd, ε)   // 整段压进视口
```

- 左标签：`formatTickTime(0)` → `0:00:00`（或现有格式的等价零点）
- 右标签：`formatTickTime(contentEnd)`
- Live 时播放头仍钉在 **视口右缘**（相对位置 ≈ 100%），对应绝对时间 `contentEnd`
- 切片 / 高光 / mark 继续用 **绝对** common/preview 秒映射到 `[0, contentEnd]` 百分比

### 3.2 zoom > 1x

保持「局部窗」：

- 可见跨度 ≈ `TIMELINE_MAX_WINDOW / zoom` 或现有 zoom 与 `panTimelineWindowStart` 等价行为
- **中心**：followLive 时跟 `contentEnd`；scrub 时跟播放头
- `windowStart` 可 > 0；左标签为绝对时间，不再强制 0

### 3.3 精修 / 非 Live scrub

精修窗、`followLive === false` 且用户拖动时的冻结窗逻辑保留；**仅当** `zoomLevel === 1 && followLive && !refining`（及对等的本地 ControlBar 路径）强制 `windowStart = 0`。

若 1x 且用户主动 scrub 离开 Live：允许临时非零 `windowStart`（与现有 scrub 冻结一致）；用户点回 Live 后恢复 `windowStart = 0`。

### 3.4 主要改动点

| 文件 | 变更 |
|------|------|
| `lsc-electron/src/utils/timelineViewModel.ts` | `computeTimelineViewModel`：1x + followLive → `ws = 0`；传入或读取 `zoomLevel` |
| `lsc-electron/src/pages/Workbench/components/ControlBar.tsx` | 本地轴重复公式与 viewModel 对齐 |
| `lsc-electron/src/hooks/useTimelineViewModel.ts` | 将 `zoomLevel` 纳入依赖 / worker 输入 |
| `lsc-electron/src/components/Timeline/index.tsx` | 确认左标签用 `ws`；1x 下刻度密度可调（`chooseTickInterval`） |
| 单测 | `timelineViewModel` / ControlBar 相关：长内容 1x Live 左端为 0；zoom>1 左端可非 0 |

---

## 4. 预览区时间线（RoomCard 放大预览）

### 4.1 目标公式

```
const DVR_LOOKBACK_SEC = 120
liveEdge = bufferedEnd   // 无效时回退 previewPos
purple   = max(0, liveEdge - DVR_LOOKBACK_SEC)
expTimelineStart = purple
replayBoundary   = purple   // 紫线贴左端
expTimelineEnd   = max(liveEdge, purple + ε)
```

去掉「紫线左侧再加 lead(10–60s)」的现行为。

### 4.2 主时间线紫线

主轨 `dvrStart` 与产品定义对齐：优先 `liveEdge − 120`（common/preview 映射后），在 buffer 已 trim 时可与 `buffered.start` 一致；未满 120s 时紫线贴 0 或贴真实 `buffered.start`（取 `max(0, liveEdge − 120)` 即可）。

### 4.3 主要改动点

| 文件 | 变更 |
|------|------|
| `lsc-electron/src/pages/Workbench/components/RoomCard.tsx` | `expTimelineStart` / `replayBoundary` |
| `lsc-electron/src/pages/Workbench/index.tsx` | `dvrStart` 计算与 120s 契约 |
| （可选）`mediaSourcePlayer.ts` | 注释/常量与 120s DVR 契约对齐；不强制改 trim 阈值 |

---

## 5. rAF 实时时钟

### 5.1 原则

- **显示用时间**（已录时长、控制条当前时刻、时间线当前时刻文案、房间卡片录制计时等）订阅与播放头相同的 **rAF 驱动源**，不再依赖 1s `setInterval` 才能「看起来在走」。
- **重逻辑**（整表 `rooms_updated`、分析 status 请求、导出队列同步）保持现有节流；本轮不把 5s 分析轮询改成 60fps 打后端。

### 5.2 推荐实现

新增或扩展轻量 store（例如既有 `playheadStore` 旁路）：

```
on rAF:
  nowMono = performance.now()
  // 对每个需要「墙钟流逝」的录制房：
  recordedDisplay = baseRecordedSec + (nowMono - baseMonoAtSample) / 1000
  // 预览当前时刻：优先 readDisplayPlayhead() / video.currentTime
  publish to subscribers (DOM text 或细粒度 React)
```

- `previewPositions` 的 500ms React 写入可保留给 **窗口/布局** 计算；**纯文案**改读 rAF 源，避免整 ControlBar 60fps 重渲染（优先 DOM text / store 订阅）。
- Workbench / ControlBar 的 1000ms `timelineTick`：布局相关可降为 250ms 或改为「仅在 rAF 发现 contentEnd 跨越阈值时」触发 viewModel；时钟文案不再等这个 tick。
- 持续分析进度数字：本轮若仍 5s 一轮，可在两次快照间用 `Date.now()` **本地插值** `analyzed_duration`（不超过 `recorded_duration`），避免分析条「卡数秒」；不改后端协议。

### 5.3 主要改动点

| 文件 | 变更 |
|------|------|
| `playheadStore` 或新建 `clockStore` | rAF 发布 recorded/preview 显示秒 |
| `ControlBar.tsx` | 当前时刻 / 时长文案订阅 rAF |
| `RoomCard.tsx` | 「已录 HH:MM:SS」订阅 rAF |
| `Timeline/index.tsx` | 若有独立当前时刻文案，同源 |
| `AnalysisProgress`（若有） | 可选本地插值 |
| 删除或旁路 | 仅服务于时钟跳变的 1s interval |

---

## 6. 验收标准

1. 录制/预览超过 10 分钟、zoom=1、followLive：主时间线左标签恒为 `0:00:00`，右端随进度增长；切片块位置与绝对秒一致。  
2. zoom>1、followLive：可见窗为局部，左端一般 > 0，播放头/直播沿大致居中或靠右（与现局部窗一致）。  
3. 放大预览条：左端与紫线重合；紫线 ≈ liveEdge − 120s（内容不足 120s 时为 0）。  
4. 已录时长与预览当前时刻在 Live 下肉眼连续更新（非约 1s 一跳）。  
5. 回归：对齐 common 轴、精修窗、非 Live scrub、§8.7（不用录制墙钟推 Live 窗）。

---

## 7. 风险与缓解

| 风险 | 缓解 |
|------|------|
| 1x 长会话整段压缩导致刻度过密 | 提高 `chooseTickInterval` 下限；次要刻度可降密度 |
| rAF 导致 React 过热 | 文案走 store/DOM，不整树 60fps `setState` |
| 紫线改 `liveEdge−120` 与旧 `buffered.start` 短暂不一致 | 未 trim 满时两者本就可能不同；以产品 120s 契约为准并在 tooltip 写清 |
| ControlBar 与 viewModel 双份公式漂移 | 单测锁定；能抽公共函数则抽 |

---

## 8. 非目标（再次确认）

- 不修改 `valorant_ocr_rounds` / 持续分析入出点  
- 不实现自动导出策略变更  
- 不把分析 WebSocket 轮询改成高频后端推送（仅允许前端插值）

---

## 9. 实现顺序建议

1. `timelineViewModel` + ControlBar：1x `ws=0` + zoom>1 局部窗单测  
2. RoomCard / Workbench：预览左端 = 紫线 = live−120  
3. rAF 时钟 store + 替换关键文案订阅  
4. 手动回归：多房 Live、对齐、精修、长录制 1x/2x
