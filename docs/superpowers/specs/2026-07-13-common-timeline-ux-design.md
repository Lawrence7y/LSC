# 公共时间轴产品化与时间线体验升级设计

> **目标读者：** 实施开发者。本文定义要做什么、为什么、边界与验收标准，不展开逐步实现代码。

**Goal:** 将已有但未接入主流程的 `TimelineContext` / `ClipSnapshot` 产品化，时间线以单一公共时间轴为唯一语义；补齐波形与 AI 高光可视化，以及精确拖拽与若干交互增强，使时间线达到「专业直播切片工作台」水位——仍明确不做多轨 NLE。

**Architecture:** 对齐成功后后端原子创建 `TimelineContext` 并广播；前端时间线只渲染 common 时间；添加切片走 `create_clip_snapshot`，导出走 `export_clip_by_id`。未对齐时降级到现有 per-room 墙钟路径。波形由前端预览音频峰值环缓冲绘制；高光由切片/分析结果映射到 common 后叠色带。

**Tech Stack:** React/TypeScript（Timeline、Workbench、AudioWorklet）、Zustand、WebSocket、Python `TimelineService`、pytest

---

## 1. 背景与问题

当前时间线是单轨 scrubber，已有 I/O 标记、缩放、磁吸、4h 滚动窗口。后端已具备：

- `TimelineContext` / `RoomTimeSnapshot` / `ClipSnapshot`（`lsc/core/models.py`）
- `TimelineService`（`lsc/core/services/timeline_service.py`）
- WebSocket：`create_clip_snapshot` / `get_clip_snapshot` / `export_clip_by_id` / `get_timeline`

但主 UI **从未调用**上述 API；`appStore.timelineContext` 闲置。结果是：

| 痛点 | 现状 |
|------|------|
| 公共时间轴半成品 | 对齐只写 `content_offset` + `align_group_id`，不创建 TimelineContext |
| 信息密度低 | 无波形；AI 高光只在切片列表，时间线不可见 |
| 拖拽降级为「近似」 | `live=false` 不写墙钟 |
| 多房间 UI 语义分裂 | 时间线显示代表房间；I/O 对各房间取各自 preview 时间 |

## 2. 目标

1. **公共时间轴产品化**：对齐成功 → 创建 TimelineContext；mark / seek / 拖拽 / 添加切片 / 导出统一走 common。
2. **波形可视化**：预览音频峰值绘于公共轨（不做缩略图胶片）。
3. **AI 高光上轨**：色带可视化；可点击定位选区、磁吸边界、悬停显示原因。
4. **精确拖拽**：有 TimelineContext 时拖拽不再标「近似」。
5. **对齐状态徽标**：清晰区分「公共轴就绪 / 未对齐降级」。

## 3. 非目标

- 不做多轨 NLE、trim/ripple、转场特效、缩略图胶片条
- 不做完整编辑撤销栈（Ctrl+Z 入出点留二期）
- 不替换 MSE 预览架构，不改录制/共享进样核心
- 不持久化 TimelineContext（仍纯内存，重启失效）
- 不在本轮重做持续分析算法本身

## 4. 用户决策摘要

| 决策 | 选择 |
|------|------|
| 多房间时间线形态 | **A. 单一公共轨** |
| 信息密度 | **波形 + AI 高光**（无胶片） |
| TimelineContext 策略 | **对齐后全面切换，未对齐降级** |
| 本轮体验增强 | 点击高光定位、对齐徽标、磁吸高光边界、悬停原因 |
| 实现路径 | **前端驱动公共轴（方案 1）** |

---

## 5. 架构与数据流

### 5.1 状态机

```
[无 TimelineContext]
  · 单房间或未对齐
  · 时间线显示代表房间 preview 本地时间
  · 徽标：「未对齐 · 本地时间」
  · I/O / 导出：现有墙钟 / content_offset 路径
  · 拖拽标记：仍可标「近似」（与现网一致）
        │
        │ 一键对齐成功且可信房间 ≥ 2
        ▼
[TimelineContext 活跃]
  · 后端 create_timeline + 广播 timeline_ready
  · 时间线刻度 / 播放头 / 入出点 / 波形 / 高光 = common 时间
  · 徽标：「公共轴已就绪」
  · 添加切片 → create_clip_snapshot → 前端 Clip 绑定 clip_id
  · 导出 → export_clip_by_id
  · 拖拽入出点：改 common 坐标，精确，无「近似」标签
        │
        │ 预览重建 / 对齐失效 / invalidate_timeline
        ▼
[失效 → 回到无 Context]
  · 广播 timeline_invalidated
  · 清空 store；已创建的 ClipSnapshot 仍可按 clip_id 导出（若 recording_id 未变）
  · 提示用户可重新对齐
```

### 5.2 时间换算（已有模型，本轮接上）

```
common = preview_local + preview_to_common_delta
common = recording_local + recording_to_common_delta

playhead_common = preview_to_common(room_id, video.currentTime)
seek_preview    = common_to_preview(room_id, common_time)
export_range    = common_to_recording via ClipSnapshot / recording_to_common_delta
```

**基准房间**：对齐返回的 `reference_room_id`。约定 `preview_to_common_delta[ref] = 0`（或等价：common 与基准房间 preview 对齐），其它房间 delta 由 `content_offset` 推导：

```
preview_to_common_delta[room] = content_offset[room] - content_offset[ref]
```

（符号以现有 `audio_aligner`「正值=领先」与导出公式 `export = wall - rec_start - content_offset` 为准，实施时用现有测试夹具锁定，避免符号反了。）

**recording_to_common_delta**：对齐提交瞬间用各房间 `recording_media_start_mono`（或 `recording_start_mono`）与 preview 墙钟关系写入 `RoomTimeSnapshot`。无录制的房间：`clip_ready=false` 仍可 preview 同步；仅预览不可 `create_clip_snapshot`。

### 5.3 对齐成功时的后端写入（关键接缝）

在现有 `handle_align_preview_audio` 成功分支（已写 `content_offset` / `align_group_id`）之后，**原子调用**：

```
TimelineService.create_timeline(
  reference_room_id,
  room_snapshots={...},  # 仅置信度 ≥ 0.3 的 trusted 房间
  required_room_ids=list(trusted.keys()),
)
```

成功则 `bridge.queue_broadcast({ type: 'timeline_ready', timeline: {...} })`。  
`create_timeline` 返回 `None` 时：对齐 offset 仍保留（兼容分析门槛），但不广播 ready；徽标保持未就绪，前端不进入公共轴模式。

失效触发点（调用已有 `invalidate_timeline` / `on_preview_epoch_changed`）：

- MSE 预览重建 / 断线重连导致 preview epoch 变化
- 用户对组内房间重新对齐（先 invalidate 旧的再 create）
- 组内房间移除

录制重连：沿用现有「更新 recording_id、不整表 invalidate」策略。

---

## 6. 前端设计

### 6.1 组件与职责

| 单元 | 路径 | 职责 |
|------|------|------|
| `Timeline` | `lsc-electron/src/components/Timeline/index.tsx` | 公共/本地时间渲染；波形层；高光色带；磁吸扩展；点击高光 |
| `Timeline.css` | 同目录 | 波形/高光/徽标样式，保持暗色 HIG token |
| `ControlBar` | `.../ControlBar.tsx` | 传入 common 坐标 props；对齐徽标；缩放窗口仍按 common/本地 duration |
| `Workbench` | `.../Workbench/index.tsx` | 对齐后写入 store；seek/mark 经换算；添加切片走 snapshot API |
| `appStore` | `src/store/appStore.ts` | 充实 `timelineContext`；`timelineMode: 'common' \| 'local'` |
| `types` | `src/types/index.ts` | 完整 `TimelineContext` / `RoomTimeSnapshot` / 高光轨类型 |
| `waveformPeaks.ts`（新） | `src/utils/` 或 `services/` | 预览峰值环缓冲采集与按 common 对齐 |
| `websocket` | `src/services/websocket.ts` | 监听 `timeline_ready` / `timeline_invalidated` |

**刻意不做：** 分房间叠轨、胶片缩略图、完整 undo。

### 6.2 Timeline Props 扩展（语义）

在现有 props 上增加（保持向后兼容默认值）：

- `timeBase: 'common' | 'local'`（默认 `local`）
- `waveformPeaks?: Float32Array | number[]`（归一化 0–1，覆盖 `[windowStart, windowStart+visibleDuration]`）
- `highlights?: { id, start, end, score?, reason?, label? }[]`（与 timeBase 同坐标系）
- `onHighlightClick?: (h) => void`
- `snapTargets?: number[]`（额外磁吸点，含高光边界）
- `alignStatus?: 'ready' | 'local' | 'invalidated'`（也可由 ControlBar 单独渲染徽标）

播放头 / markIn / markOut / currentTime / duration / clips：**调用方负责换成 timeBase 坐标**，Timeline 内部不懂房间换算。

### 6.3 多房间交互语义（统一）

当 `timelineMode === 'common'` 且多选：

| 操作 | 行为 |
|------|------|
| Seek | 对每个选中房间 `seek(common_to_preview(rid, t))` |
| I / O 键 | 取**各房间当前 preview** → `preview_to_common` → 写入**同一** common 入/出点（显示一条选区）；后端各房间 mark 可同步写 preview 等价或只存 common 于前端 |
| 拖拽入/出点 | 只改 common；松手后同步到各房间 preview mark（可选）并清除「近似」 |
| 添加切片 | `create_clip_snapshot` 对 `target_room_ids` 原子映射 |
| 导出 | 优先 `export_clip_by_id`；无 snapshot 时降级旧 `queue_export` |

当 `timelineMode === 'local'`：保持现有代表房间 UI + 各房间各自 I/O 行为（兼容单房间与未对齐）。

### 6.4 对齐徽标

位置：ControlBar 时间线左侧或标题旁。

| 状态 | 文案 | 样式 |
|------|------|------|
| ready | 公共轴已就绪 | 成功绿 |
| local | 未对齐 · 本地时间 | 次要灰 |
| invalidated | 公共轴已失效 · 请重新对齐 | 警告橙 |

### 6.5 波形

- **来源**：预览 `<video>` → AudioContext / 扩展现有 Align 用的音频图；用 `AnalyserNode` 或轻量 ScriptProcessor/Worklet 取时域峰值，**不**为波形再拉 CDN。
- **缓冲**：环形峰值数组，键为 common 时间桶（如每 50ms 一桶）；多房间时以 reference 房间主采，或取多房间 envelope max（实施选简单：仅参考房间波形，避免 CPU 爆）。
- **绘制**：Timeline 内 SVG/canvas 一层，高度约 32–48px，颜色用品牌绿低透明度，置于进度轨下方或融合成加厚轨。
- **降级**：无音频轨 / 未预览 → 不画波形，不报错。
- **性能**：≥4 路预览时只采当前参考房间；峰值桶上限与 4h 窗口一致可滚动丢弃。

### 6.6 AI 高光可视化与交互

- **数据**：切片列表中 `is_ai_highlight` 项 + 持续分析入队的 clip；映射到 common（有 context 时用 snapshot 的 `common_start/end` 或 `start + offset` 规则与现有多房间映射一致）。
- **视觉**：琥珀色半透明色带 + 左右边界线；可与手动切片绿色块区分。
- **点击**：`onHighlightClick` → seek 到 start，set markIn/Out 为该高光区间。
- **磁吸**：`findSnapTarget` 增加高光起止，优先级介于 mark 与 tick 之间（如 90）。
- **悬停**：tooltip 显示 `reason` / score（无 reason 则显示「AI 高光」+ 时长）。

### 6.7 精确拖拽

| 模式 | 拖拽松手 | 切片列表标签 |
|------|----------|--------------|
| common | 更新 common marks；添加切片时走 ClipSnapshot | **精确**（或不显示近似） |
| local | 保持现网：`live=false`，可标近似 | **近似** |

有 TimelineContext 时，**禁止**再走「无墙钟 ≈ 近似」的拖拽路径作为主路径。

---

## 7. 后端设计

### 7.1 必须改动

1. **`handle_align_preview_audio`**：trusted 对齐成功后 `create_timeline` + 广播 `timeline_ready`（含完整 room_snapshots 序列化，与 `get_timeline` 同构）。
2. **失效广播**：`invalidate_timeline` 时 `queue_broadcast({ type: 'timeline_invalidated', timeline_id, reason })`（若尚未广播则补上）。
3. **`RoomTimeSnapshot` 填充**：对齐时写入 `preview_epoch_id`、`recording_id`、两个 delta、`align_confidence`、`media_start_mono`。
4. **单房间**：不强制 create_timeline；保持 local 模式。若未来要对「单房间也建伪 context」——**本轮不做**。

### 7.2 已有可复用（尽量不改语义）

- `create_clip_snapshot` / `export_clip_by_id` / `get_timeline`
- `TimelineService` 置信度阈值 0.3、原子失败

### 7.3 小修复（若实施中发现）

- `export_clip_by_id` 里 `source='ai_highlight'` 写死：应按 snapshot.source 或请求参数传递，避免手动 snapshot 被当成 AI。
- 前端类型 `TimelineContext` 补全字段，去掉过度 `[key: string]: unknown` 依赖。

---

## 8. 错误处理与降级

| 场景 | 行为 |
|------|------|
| 对齐成功但 create_timeline 失败 | offset 保留；无 timeline_ready；徽标 local；日志 WARNING |
| timeline 失效 | 清空前端 context；toast/徽标提示；进行中的 common marks 转为 local（按参考房间 common_to_preview） |
| create_clip_snapshot → RANGE_UNAVAILABLE | 整组失败，提示哪路越界；不部分入队 |
| export 时 recording_id 变化 | 返回明确错误「录制文件已变化，请重新创建切片」 |
| 波形采集失败 | 静默无波形 |
| 高光无 common 映射 | 仅在 local 模式按代表房间时间显示 |

遵守项目错误规范：不静默吞异常；清理路径可 DEBUG。

---

## 9. 测试与验收

### 9.1 后端

- 对齐成功路径调用 `create_timeline` 并产生可 `get_timeline` 的上下文（扩展 `tests/test_timeline_service.py` / handler 测试）
- 低置信不创建 timeline
- `timeline_ready` / `timeline_invalidated` 广播契约
- `create_clip_snapshot` 多房间原子性（已有测试保持绿）
- delta 符号与导出区间和现有墙钟公式一致性的回归测试

### 9.2 前端（手工 + 必要单测）

- 对齐后徽标 → ready；播放头/seek 多房间画面内容对齐
- 拖拽入出点添加切片无「近似」；导出走 clip_id
- 高光色带可见；点击设选区；磁吸边界；hover reason
- 波形在有声预览时出现
- 失效后降级 local，可重新对齐恢复

### 9.3 验收标准（Done）

1. 多选房间一键对齐成功后，时间线进入公共轴，徽标正确。
2. 在公共轴上 I/O、拖拽、添加切片、导出，多房间片段内容对齐且不标近似。
3. AI 高光在时间线上可见并可点选/磁吸/悬停。
4. 参考房间波形可见（有音频时）。
5. 未对齐/失效行为可预期，单房间体验不回退。
6. 明确不做：多轨、胶片、Ctrl+Z。

---

## 10. 风险与缓解

| 风险 | 缓解 |
|------|------|
| delta 符号与历史 `content_offset` 不一致 | 用同一导出夹具双向测；对齐后对比旧墙钟导出与 snapshot 导出 |
| 波形 CPU | 仅参考房间；50ms 桶；4h 窗口丢弃 |
| 双路径（common/local）复杂度 | 换算集中在 Workbench 一处 helper，Timeline 保持傻组件 |
| 半接入导致更乱 | 对齐成功必须「要么 ready 要么明确失败」，禁止静默半状态 |

---

## 11. 实施顺序建议

1. 后端：对齐 → create_timeline + timeline_ready/invalidated  
2. 前端：store + 换算 helper + ControlBar 徽标 + seek/mark 走 common  
3. 添加切片 / 导出接 ClipSnapshot  
4. 精确拖拽（去近似）  
5. 高光色带 + 点击/磁吸/hover  
6. 波形峰值层  
7. 测试与回归  

---

## 12. 涉及文件（预期）

**后端：** `python-backend/handlers/room_handler.py`，`lsc/core/services/timeline_service.py`（必要时广播钩子），`tests/test_timeline_*.py`，`tests/test_clip_snapshot_handlers.py`

**前端：** `Timeline/index.tsx`，`Timeline.css`，`ControlBar.tsx`，`Workbench/index.tsx`，`appStore.ts`，`types/index.ts`，`websocket.ts`，新建 `waveformPeaks.ts`（或等价），`ClipList.tsx`（精确/近似标签）
