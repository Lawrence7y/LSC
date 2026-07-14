# 待确认切片人工精修 + 时间线精修友好升级

> **日期：** 2026-07-14  
> **状态：** 待用户审阅后进入实施计划  
> **前置：**  
> - [2026-07-12-valorant-round-continuous-analysis-design.md](./2026-07-12-valorant-round-continuous-analysis-design.md)  
> - [2026-07-13-common-timeline-ux-design.md](./2026-07-13-common-timeline-ux-design.md)  
> - [2026-07-14-valorant-phase-scheduler-continuous-analysis-design.md](./2026-07-14-valorant-phase-scheduler-continuous-analysis-design.md)

## Goal

让持续分析检出的「待确认」回合**立刻进入切片列表**，用户可点选到时间线微调入出点并确认；用户确认过的跳过 OCR，其余仍可由 OCR 升格。同步把时间线提升到「精修友好」水位（去波形、拖拽降卡、无预览可出轨、修遮挡、选中硬色带），使人工闭环不依赖脆弱的收尾 OCR。

## 决策摘要（已确认）

| 项 | 选择 |
|----|------|
| 架构 | 方案 1：前端主导精修 + 轻量后端状态/多房同步 |
| 确认后行为 | 只改 `confirm_status`，**不**自动导出 |
| 入列时机 | 边录边分析：一检出闭合回合即进列表 |
| 多房间 | 确认时把修改同步到其它目标房间，标记为同一时段切片并一并 `user_confirmed` |
| OCR 冲突 | 精修中（`refining`）与已 `user_confirmed` 冻结；其它 `pending` 仍可 OCR 升格 |
| 时间线档位 | **B 精修友好**：去波形 + 松手提交 + 无预览出轨 + 遮挡 + 硬色带 + 手势分离 + 列表滚窗联动 |
| OCR 升格后是否自动导出 | **否**（与用户确认一致，一律手动导出） |
| 进入精修是否通知后端 | **是**（`begin_refine_clip` 立即冻结 round_key） |

## 非目标

- 不做多轨 NLE、波形、缩略图胶片、完整撤销栈
- 不做无预览时的录制文件画面/音频试听（可提示开启预览）
- 不改 MSE / 录制 / 导出并发上限等核心架构
- 不持久化 TimelineContext（仍可与既有 common timeline 设计对齐，但不本轮重做对齐系统）
- 不放宽「未确认切片可直接导出」

## 问题背景

实测收尾常出现：`finalize` 完成、高光数 > 0，但 `confirmed_rounds=0`，无 `clip_queued`，切片列表为空。根因包括：

1. 前端只在 `clip_queued` 时入列表；`continuous_highlights` 仅通知
2. 自动入列要求 OCR 双可信边界；Onset/纯音频永远 pending
3. Onset 早退路径在 `scan_range is not None` 时跳过 OCR 精修（收尾必中）
4. 时间线半成品：拖拽卡顿、无预览无进度条、UI 遮挡，不支撑人工精修

---

## 1. 交互流

1. **入列**：持续分析检出闭合回合 → `clip_queued`（`confirm_status=pending`，`export_deferred`）→ 列表出现「待精修」。
2. **进入精修**：点击 pending 切片 → `begin_refine_clip` → 本地/广播 `refining` → 列表蓝光 +「精修中」→ 时间线硬色带 + 写入相关房间 mark_in/out → 滚窗使选区居中；有预览则 seek 到入点附近。
3. **微调**：拖 I/O 只改本地显示；松手再 `set_mark_*`（及有预览时一次 seek）。拖拽中不同步其它房间。
4. **确认**：点「确认」→ `confirm_highlight_clip` → 主房 + 映射目标房均为 `user_confirmed` → 蓝光消失 → **不**自动导出。
5. **取消/切换**：点另一条或「取消精修」→ `cancel_refine_clip` → 丢弃未确认微调，恢复进入精修前的 start/end。
6. **OCR 并行**：仅更新仍为 `pending` 的同 `round_key`；升为 `ocr_confirmed`，不自动导出。

---

## 2. 时间线（档 B）

### 2.1 必须做

| 项 | 说明 |
|----|------|
| 去掉波形 | UI 不再渲染波形；峰值采集可停用或闲置 |
| 拖拽降卡 | 拖 I/O：本地更新；松手 commit `set_mark_*`；拖中节流/禁止多房同步 |
| 无预览出轨 | 时长优先级：录制文件时长 → 持续分析 `recorded_duration` → MSE duration；无预览可动播放头 UI，不强制 seek 视频 |
| 修遮挡 | 时间线固定底栏安全区；列表/卡片不得盖拖拽热区；精修态提高时间线层级 |
| 选中硬色带 | `refining` 区间品牌蓝半透明底 + 双边；与列表蓝光同语义 |
| 手势分离 | 拖 I/O = 改边界；点空白/拖播放头 = seek（无预览只改 UI） |
| 列表联动 | 进入精修自动滚窗，选区落在可视区中部；确认后色带改为「已精修」弱样式 |

### 2.2 明确不做

- 波形、胶片、多轨、无预览文件预览、完整 Ctrl+Z

---

## 3. 状态模型

### 3.1 `confirm_status`

| 值 | 含义 | 可导出 | OCR 可否改边界 |
|----|------|--------|----------------|
| `pending` | AI 检出未精修 | 否 | 可 |
| `refining` | 用户精修中 | 否 | 冻结 |
| `user_confirmed` | 用户已确认 | 是（手动） | 冻结 |
| `ocr_confirmed` | OCR 已确认 | 是（手动） | 不再改 |

与既有 `export_status`（queued/exporting/completed/failed/pending 延后导出）正交：确认管可信度，export 管导出队列。

### 3.2 列表标签

- `pending` →「待精修」
- `refining` → 蓝光 +「精修中」+「确认」
- `user_confirmed` →「已精修」
- `ocr_confirmed` →「OCR已确认」
- 导出：仅后两种可点

### 3.3 多房同步（确认时）

1. 主房间写入最终 in/out（能算则写墙钟）→ `user_confirmed`
2. 按 TimelineContext common 轴或 `content_offset` 映射到 `target_room_ids`
3. 各目标房 upsert 同 `round_key` 切片为同一时段，`user_confirmed`
4. 已存在的 pending/ocr 同 key 合并，不重复堆叠

### 3.4 标识

- 稳定键：`round_key`（与持续分析现有 `_valorant_round_key` 一致）
- 前端 `clip_id` 仍可按房间区分；同步靠 `round_key` + `room_id`

---

## 4. 协议与职责

### 4.1 前端

- 实现时间线档 B 与精修 UI
- 入列表继续以 `clip_queued` / `clip_confirm_status` 为准
- 导出入口按 `confirm_status` 门禁

### 4.2 后端

- 闭合回合即 `clip_queued`（含 pending）
- OCR 只升格 `pending`
- `begin_refine_clip` / `confirm_highlight_clip` / `cancel_refine_clip`
- 确认时多房 upsert + 广播
- **OCR 升格与用户确认均不自动导出**（本期统一手动）

### 4.3 消息

| 方向 | 类型 | 载荷要点 |
|------|------|----------|
| ← | `clip_queued`（扩展） | `confirm_status`, `round_key`, start/end, room_id, label, export_deferred |
| ← | `clip_confirm_status` | room_id, round_key, confirm_status, start/end（可选多房批量） |
| → | `begin_refine_clip` | room_id, round_key 或 clip_id |
| → | `confirm_highlight_clip` | room_id, round_key/clip_id, start, end, target_room_ids |
| → | `cancel_refine_clip` | room_id, round_key 或 clip_id |

### 4.4 同批检测修复

1. Onset 早退：`refine_with_ocr=True` 时必须调用 `_refine_rounds_with_ocr`，**删除** `scan_range is None` 限制
2. 主 OCR 空结果 / 分辨率失败 / 早退：打 **WARNING** 级明确日志，避免静默
3. 保留并依赖既有 `_window_scan_timeout`（OCR 短窗不再 ~50s 饿死）与 `_finalize_scan_timeout`

---

## 5. 架构示意

```
持续分析 Worker
    │ 闭合回合
    ▼
clip_queued (pending) ──► 切片列表
    │
    ├─ OCR 升格 ──► clip_confirm_status (ocr_confirmed)  [仅 pending]
    │
    └─ 用户点选 ──► begin_refine_clip (冻结)
            │ 时间线微调（本地拖，松手 commit）
            ▼
       confirm_highlight_clip
            │ 映射 target rooms
            ▼
       clip_confirm_status (user_confirmed × N 房间)
            │
            ▼
       用户手动导出（现有 export 路径）
```

---

## 6. 验收标准

1. 边录边分析时，pending 切片持续出现在列表，无需等收尾。
2. 无预览房间：时间线可见、可拖 I/O 与播放头 UI；不因无 MSE 而整条消失。
3. 有预览：拖 I/O 过程流畅；松手后 mark 与 seek 一致，无明显卡顿风暴。
4. 精修中：列表蓝光 + 时间线硬色带；该 `round_key` 不被 OCR 改边界。
5. 确认后：所有目标房间出现同一时段 `user_confirmed` 切片；**无**自动导出任务。
6. 未精修 pending：收尾/增量 OCR 可升为 `ocr_confirmed`；已精修不动。
7. UI：时间线拖拽热区不被列表/卡片遮挡。
8. Onset+收尾路径在 `refine_with_ocr=True` 时会跑 OCR 精修（有日志可证）。

## 7. 风险与缓解

| 风险 | 缓解 |
|------|------|
| pending 大量涌入列表 | 标签区分；导出门禁；可选后续「折叠待精修」 |
| 无预览精修缺少视听反馈 | 文案提示开启预览；数字时长与选区长度始终可见 |
| 多房映射在未对齐时偏差 | 有 TimelineContext 用 common；否则 content_offset；都没有则同 start/end 并标「未对齐近似」 |
| 切换精修丢失微调 | 文档化行为；确认前可再点回（未确认则已丢，可接受） |

## 8. 实现顺序建议

1. 检测修复：Onset OCR + 日志（立刻改善收尾自动确认率）
2. 后端：pending `clip_queued` + refine/confirm/cancel + OCR 冻结
3. 前端：列表状态/蓝光/确认按钮 + 入列
4. 时间线档 B
5. 多房确认同步与联调验收
