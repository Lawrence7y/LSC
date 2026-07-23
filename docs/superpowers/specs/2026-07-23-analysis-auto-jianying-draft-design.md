# 分析结束自动剪映草稿 + 切片列表 UI 收敛

> **状态：** 已拍板（2026-07-23）  
> **前置：** `docs/spec-jianying-draft-export.md`（时间轴映射 / builder / WS API 仍有效）  
> **本 spec 覆盖：** 草稿**触发入口与时机**、入列门槛残留修复、切片列表 UI；并**修正**「pending 不进草稿」策略以匹配本场自动草稿。

---

## 1. 目标

1. 持续分析 / 一次性分析导出：在**开始时**让用户确认是否「完成后生成剪映草稿」。
2. 勾选后，在分析**正常结束**时自动生成**一份**多机位对齐草稿（可含多房间 + 本场全部切片）。
3. 切片列表与导航栏**不再**提供草稿入口；切片列表只做 MP4 导出。
4. 切片列表无横向滚动条，一行内完整展示必要信息（换行/收缩，不裁切成需左右滑）。
5. 保留已修的持续分析短回合入列：`list_only` 最短时长 5s（导出路径仍 35s）。

---

## 2. 已拍板决策

| 项 | 选择 |
|----|------|
| 草稿写出时机 | **A**：分析结束自动生成 |
| 切片范围 | **A**：本场分析产出的**全部**切片（含 `pending` / `refining`） |
| 适用范围 | **B**：持续分析 + 一次性「分析导出」 |
| 实现路径 | 前端会话标记 + 结束后调现有 `generate_jianying_draft`（推荐做法 1） |
| 多房未对齐 | **失败**，不静默降级单房；toast + 状态区可重试一次 |
| 草稿开关默认 | **关** |
| 近似定位切片 | 仍**不进**草稿（与导出口径一致），写入 warnings |

---

## 3. 非目标

- 不恢复切片列表「MP4/草稿/两者」Segmented。
- 不保留导航栏「⋯ → 生成剪映草稿」。
- 不做导出预览弹窗里的草稿目标选项。
- 不做剪映自动导出 / 控制剪映 UI。
- 不持久化「要出草稿」跨应用重启（刷新丢失可接受；重试用本场内存会话即可）。

---

## 4. UI 变更

### 4.1 分析导出 Modal

在现有「持续分析」开关旁（或下方）增加：

- Switch / Checkbox：**完成后生成剪映草稿**（默认 off）
- 辅助文案：多房须已一键对齐；结束后自动生成一份草稿（含本场全部回合切片，含待确认）；设置页可配草稿目录。

启动持续分析 / 一次性分析时，把该开关写入前端会话状态（见 §5）。

### 4.2 移除的草稿入口

| 位置 | 动作 |
|------|------|
| `ClipList` extra Segmented（MP4/草稿/两者） | **删除**；导出固定为 MP4 |
| 工作台导航 `Dropdown`「生成剪映草稿」 | **删除**（若 ⋯ 菜单空则去掉整个 ⋯） |
| 导出预览 Modal 内 exportTarget 草稿选项 | **删除**；确认导出只走 MP4 |

设置页「剪映草稿目录」**保留**。

### 4.3 分析结束后的反馈

- 成功：沿用现有「剪映草稿已生成」Modal（打开目录）。
- 失败：`message.error` + 在 `AnalysisProgress` / 持续分析状态附近提供 **「重试生成草稿」**（仅本场曾勾选且尚未成功时显示）。
- 生成中：`jianyingLoading`，避免重复触发。

### 4.4 切片列表布局

- 列表容器：`overflow-x: hidden`；允许纵向滚动。
- 行：禁止依赖横向滚动查看信息；标题/时段/状态/操作用换行或弹性布局完整展示。
- 去掉因 `white-space: nowrap` + 过窄列导致的左右滑条。

---

## 5. 前端会话状态

```ts
type AnalysisDraftSession = {
  wantDraft: boolean
  mainRoomId: string
  targetRoomIds: string[]
  /** clip_queued 时打上的本场标记，用于筛选 */
  sessionId: string
  /** 本场已收集的 clip 稳定 id（clip_id / round_key） */
  clipKeys: Set<string>
  status: 'idle' | 'armed' | 'generating' | 'done' | 'failed'
  lastError?: string
}
```

- 用户在 Modal 确认启动且 `wantDraft=true` → `status='armed'`，生成新 `sessionId`。
- 本场 `clip_queued`（AI 高光）→ 写入 `clipKeys`，并在 `ClipSegment` 上可选挂 `analysis_session_id`（便于筛选）。
- 触发自动生成条件：
  - **持续分析**：曾 `armed`，且 `continuous_analysis_status` 进入终态（`running=false` 且 `phase` 为 `idle` / `completed`，且本场曾真正跑过，不是启动失败）。
  - **一次性分析**：`start_analysis_export_response.success`，且已处理完本批 `clip_queued`（response 后短延迟或等 submitted_count 对应入列；实现计划里定防抖）。
- 触发后：`status='generating'` → 调 `generate_jianying_draft`：
  - `room_ids` = `targetRoomIds`
  - `main_room_id` = `mainRoomId`
  - `include_clips: true`
  - `clips`: 本场 `clipKeys` 对应的 store 切片（全部 confirm_status，含 pending）
  - `allow_single_fallback: false`（多房未对齐直接失败）
- 成功 → `done`；失败 → `failed` + 重试按钮（仍用同一 `clipKeys`）。

手动切片（非 AI）不进本场自动草稿集合。

---

## 6. 后端 / builder 策略修正

原 `clip_allowed_for_draft` / `clip_source_usable` **拒绝** `pending`/`refining`。本场自动草稿要求**全部切片**进草稿，故：

1. **锁定**：`generate_jianying_draft` 增加 `include_pending: bool`（默认 `false`，保持旧测与手动路径语义）；本场自动草稿请求传 `true`。`clip_allowed_for_draft` / `clip_source_usable` 增加同名参数或并行 helper，避免静默改掉默认拒绝 pending 的行为。

2. `approximate` 仍跳过并 warning（与 `include_pending` 无关）。

3. 多房 + 无对齐 TimelineContext：返回 `success=false` / `error_code=no_aligned_context`，**不**降级单房（调用方 `allow_single_fallback=false`）。

4. 时间轴映射算法仍按 `docs/spec-jianying-draft-export.md` §核心设计 A/B；公共轴坐标优先 `common_start/common_end`，缺失则尝试墙钟，再不行跳过该片。

---

## 7. 持续分析入列（已落地，本 spec 锁定）

- `_VALORANT_MIN_LIST_DURATION_SEC = 5.0` 用于 `list_only`。
- `_VALORANT_MIN_EXPORT_DURATION_SEC = 35.0` 用于真正导出路径。
- 现场 34.8s hybrid 回合须能 `clip_queued`，不得只 toast `continuous_highlights`。

---

## 8. 边界情况

| ID | 场景 | 行为 |
|----|------|------|
| H1 | 勾选草稿但本场 0 切片 | 不调用生成；info「无切片，跳过草稿」 |
| H2 | 多房未对齐 | 失败 toast；显示重试；不降级 |
| H3 | 草稿目录未配置/不可写 | 失败，提示去设置页 |
| H4 | 用户中途取消持续分析（stopping→idle）且曾有产出 | 若 `armed` 且有切片 → 仍自动生成；无切片 → H1 |
| H5 | 启动失败（从未 running） | 不生成 |
| H6 | 生成中用户再点重试 | 忽略 / disable |
| H7 | 页面刷新 | `wantDraft` 丢失；不自动补生成 |
| H8 | pending 切片无 common 坐标 | 尽量用 recording 本地 + timeline 换算；仍失败则 warning 跳过该片 |
| H9 | 单房分析 | 无需对齐组；可生成单房草稿 |

---

## 9. 测试要点

- Guard：ClipList / 导航 / 导出预览源码中不再出现草稿 Segmented /「生成剪映草稿」菜单项。
- Guard：分析 Modal 含「完成后生成剪映草稿」。
- `include_pending=true` 时 pending 切片进入 builder；默认 false 时行为与旧测一致。
- list_only 时长门槛回归测保留。
- 前端：armed → 终态触发一次 generate；失败可重试；`allow_single_fallback: false`。

---

## 10. 实现分期（供 writing-plans）

| 里程碑 | 内容 |
|--------|------|
| D1 | ClipList 去草稿 Segmented + 去横向滚动；去掉导航草稿入口与导出预览草稿选项 |
| D2 | 分析 Modal 开关 + `AnalysisDraftSession` + 结束自动生成 / 重试 |
| D3 | 后端 `include_pending` + 单测；多房严格对齐失败路径核对 |

---

## 11. 与旧 spec 的关系

- `docs/spec-jianying-draft-export.md` 的映射/轨道/WS **仍有效**。
- 其中「导出目标三选一（MP4/草稿/两者）」UI **被本 spec 取代**：草稿仅由分析开始开关驱动，结束后自动生成。
- 「pending 不进草稿」**被本 spec 修正**为：自动草稿路径通过 `include_pending` 允许 pending。
