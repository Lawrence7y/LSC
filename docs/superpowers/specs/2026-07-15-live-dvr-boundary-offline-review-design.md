# 直播 DVR 左边界紫标 · 下线录制回看 · 分层错误处理 设计

> **日期：** 2026-07-15  
> **状态：** 待用户审阅定稿  
> **交付策略：** 完整设计一次定稿；实现分两期（一期语义/误伤/房间 UI，二期文件回看）  
> **相关：** CLAUDE.md §7–8（预览/切片时间轴）、现有 MSE ~120s 缓冲、`mse_error` 误判链路

## Goal

1. 时间线**紫色标签**表示「可回看窗口的**左边界**」：左边不可拖（超过则回跟播），右边可回看已缓冲内容。  
2. **LIVE** 状态只出现在**房间卡片**顶栏胶囊，时间线不画 LIVE 前沿绿标。  
3. **确认主播下线**后：自动停录制、保留预览并切到录制文件回看、紫标取消、时间线变为录制全文（按需加载）、持续分析走现有 finalize 收尾。  
4. **分层错误**：网络失败不冒充「主播下线」；修复现状「任意 `mse_error` → 停录 + 下线文案」。  
5. 房间卡片**去叠层重排**，角标互不遮挡；该 UI 改动不得影响录制/预览/切片等其它功能。

## 决策摘要（已确认）

| 项 | 选择 |
|----|------|
| 紫标语义 | **B**：DVR **左边界**（≈ MSE `bufStart`），非右沿 live edge |
| LIVE 指示 | 房间卡片顶栏胶囊；时间线不画绿光标 |
| 下线后回看范围 | **混合**：时间线显示录制全文，拖到未加载区按需 seek |
| 下线判定 | **C 分层**：仅确认 `offline` 走「停录+回看」；网络失败走 degraded/重连 |
| 落地方式 | 方案 2 完整能力 + 方案 3 **分期实现** |
| 房间卡片 | 去叠层：顶栏勾选/名/LIVE·回看；预览仅底栏控件；元信息行在画面下 |

---

## 1. 房间预览模式

每个房间独立：

| `previewMode` | 何时 | 紫标 | 预览源 | 房间胶囊 |
|---------------|------|------|--------|----------|
| `live_mse` | 正常直播推流 | 有（DVR 左边界） | 直播 MSE | `LIVE` |
| `recording_review` | **确认** offline 且有有效录制文件 | **取消** | 本地录制文件（复用 MSE 通道） | `回看` |
| `degraded` | 网络/编码等非 offline 失败 | 视情况 | 重连或暂停 | 无 LIVE；可提示网络异常 |

**隔离约束：** `recording_review` 必须独立于直播跟播路径；禁止把 `recording_local` 混入直播态的 `windowStart` / `followLive` 计算（见 CLAUDE.md §8.7）。

---

## 2. Live 紫标与 Seek（仅 `live_mse`）

### 2.1 UI

- 时间线紫色指示器 = 可回看窗口**左边界**（映射 MSE `buffered.start(0)` / 等价 bufStart，约保留最近 **120s**）。  
- **不**在时间线绘制 LIVE 前沿绿/白光标。  
- 内容右沿仍可跟播（行为保留）；贴右沿 `LIVE_EDGE_TOLERANCE_SEC`（1s）内自动跟播可保留。

### 2.2 行为

| 操作 | 结果 |
|------|------|
| 拖到紫标**左侧**（缓冲外） | `enterTimelineLive()`（回到跟播） |
| 紫标右侧至内容右沿 | 可 scrub 回看 |
| 贴右沿容差内松手/步进 | 保持现有跟播回 Live |

### 2.3 与现状差异

- **现状：** 紫条在 `recordedEnd` / contentEnd（右沿）；贴右回 Live。  
- **目标：** 紫条在 bufStart（左沿）；越左回跟播。  
- 前端稳定性守卫测试中与右沿紫标相关的断言需同步更新。

---

## 3. 房间卡片去叠层（一期，功能隔离）

### 3.1 布局契约

1. **顶栏（预览外）：** 勾选 · 主播名 · `LIVE` / `回看` 唯一状态胶囊  
2. **预览区：** 禁止左上/右上角标叠层；仅底部渐变工具条（画质 / 静音 / 放大 / 停预览）  
3. **元信息行：** 录制状态 · 时长/总长 · 体积（单行）  
4. **标题行 + 操作行：** 保持现有主操作语义（开始/停止录制、断开、删除）

### 3.2 隔离

- 仅改 `RoomCard` 布局与样式。  
- LIVE 判定可继续：`preview_enabled && preview_phase === 'streaming'`；回看态另用 `previewMode === 'recording_review'`。  
- **不得**改动 WebSocket 指令、录制生命周期、切片/导出/对齐逻辑。

---

## 4. 分层错误处理

### 4.1 分类与动作

| 类型 | 判定 | 动作 |
|------|------|------|
| `confirmed_offline` | 平台 `refresh_stream_url` / 解析明确 offline 或 `is_live=false` | 停录制（保留 `record_output_path`）→ 有文件则进 `recording_review` → 胶囊 LIVE→回看 → 紫标取消 → 分析 finalize → toast「主播已下线，已切换到录制回看」 |
| `network` / recoverable | MSE/录制可恢复错误、重连中 | **不**冒充下线；录制侧自重连；预览重连或 `degraded` |
| `fatal`（如 `disk_full`） | 不可恢复本地错误 | 按 reason 停录；有文件可回看；不冒充 offline |

### 4.2 必须修复的现状

| 现状 | 问题 | 目标 |
|------|------|------|
| `Workbench`：任意 `mse_error` → `stop_recording` +「主播已下线」 | 网络失败误杀录制 | 仅 `reason=offline`（或等价字段）走该路径 |
| `useWebSocket`：`mse_error` → `preview_enabled: false` | 与「停录保预览」相反 | offline 成功切文件源时保持预览；network 可关或重连，文案准确 |
| 录制重连耗尽常 `stop_preview` | 下线后预览空白 | offline 路径改为切文件回看，而非无条件关预览 |

### 4.3 广播契约（建议）

在 `mse_error` / `recording_stopped` / 新事件中携带可区分字段，例如：

```json
{ "room_id": "...", "reason": "offline" | "network" | "disk_full" | "unknown", "error": "..." }
```

前端分支只认 `reason`，禁止用原始 error 字符串模糊匹配冒充 offline（可作辅助，不可作唯一依据）。

---

## 5. 录制回看与时间线（二期）

### 5.1 进入条件

- `confirmed_offline` **且** `record_output_path` 通过现有三层校验（存在 / 大小 / 格式签名）。  
- 否则：`degraded` + 明确提示（无文件可回看）。

### 5.2 预览

- 后端以录制文件为输入喂现有 MSE/fMP4 通道（或等价本地回放），前端 `VideoPreview` / `mediaSourcePlayer` **复用接口**，避免第二套播放器。  
- 关闭 live 跟播：`timelineFollowLive=false`；不再 `goLive` 贴右。

### 5.3 时间线

| | `live_mse` | `recording_review` |
|--|------------|-------------------|
| 坐标系 | preview / common | **recording_local**（0 → duration） |
| 紫标 | DVR 左边界 | **无** |
| 可视范围 | 缓冲窗口 | UI 显示全文；底层按需加载窗口 |
| 越界 | 左出缓冲 → 跟播 | 钳制在 `[0, duration]` |

### 5.4 持续分析

- 不新开管线。  
- 确认 offline 时确保 `stop_recording`，复用 `_continuous_analysis_loop` 中 `is_recording=false` 连续 tick → `_finalize_*` → `continuous_analysis_complete`。

### 5.5 明确不动

- 墙钟切片映射公式（`mark_*_wallclock - recording_start_mono - content_offset`）  
- 导出队列 / semaphore  
- 音频对齐 `compute_offset`  
- 快捷键表、设置默认值（除非二期单独增加「回看」相关设置，本设计默认不加）

---

## 6. 分期交付

### 一期（语义 + 误伤 + 房间 UI）

1. 紫标改 DVR 左边界 + 越左回跟播  
2. 错误分层：去掉误报下线；offline 仍停录并触发分析收尾  
3. 房间卡片去叠层（LIVE 顶栏胶囊）  
4. 更新相关前端稳定性守卫测试  

一期结束时：下线后**可能仍无全文文件回看**，但不再误停、误报；分析可收尾。

### 二期（回看闭环）

1. 文件源 MSE 回看  
2. `recording_review` 时间线全文 + 按需 seek  
3. 房间胶囊 `回看`；无文件 degraded  

---

## 7. 影响评估摘要

| 改动 | 风险 | 对其它功能 |
|------|------|------------|
| 房间卡片去叠层 | 低 | 仅 UI；不碰业务 |
| 紫标左边界 | 中 | 改拖拽手感与测试；不改导出/对齐 |
| 错误分层 | 中 | 纠正误停录；需保证 offline 仍停录以便 finalize |
| 二期文件回看 | **高** | 必须模式隔离，禁止污染直播时间线坐标 |

---

## 8. 测试要点

### 一期

- 拖到紫标左侧 → 回跟播；紫标右侧可 scrub  
- 模拟网络 `mse_error` → **不**出现「主播已下线」且不误停录（若 reason=network）  
- 模拟 confirmed offline → 停录 + 分析 finalize 路径被触发  
- 房间卡片：LIVE 在顶栏；预览四角无状态角标互挡  
- 回归：导出、对齐、快捷键、多选房间仍可用  

### 二期

- offline + 有效文件 → 预览播文件、紫标消失、可拖全文（未加载区按需出画）  
- offline 无文件 → degraded，不白屏假装回看  
- `recording_review` 下 followLive / goLive 不生效  
- 多房：一房 offline 不影响其它房 `live_mse`  

---

## 9. 非目标

- 不做完整 NLE / 多轨  
- 不做「整文件一次载入内存」回放  
- 不改变产品定位（仍是录制+快速切片工具）  
- 一期不实现文件回看（避免与「完整设计」混淆：设计有、实现后置）

---

## 10. 关键触及文件（实现时）

| 层级 | 文件 |
|------|------|
| 时间线紫标 | `lsc-electron/src/components/Timeline/*`、`ControlBar.tsx`、`Workbench/index.tsx` |
| 房间 UI | `RoomCard.tsx` |
| 错误分支 | `Workbench/index.tsx`、`useWebSocket.ts`、`room_handler.py`、`manager.py` |
| 二期回看 | `mse_streamer` / shared_ingest 文件输入、`mediaSourcePlayer.ts`、时间线坐标模式 |
| 测试 | `tests/test_frontend_stability_guards.py` 等 |

---

## Spec Self-Review

- [x] 无 TBD/TODO 占位  
- [x] 紫标左右语义与 LIVE 位置无矛盾（左边界紫标 + 房间 LIVE）  
- [x] 分期范围与「完整设计」一致  
- [x] 高风险二期已写明模式隔离与禁止事项  
