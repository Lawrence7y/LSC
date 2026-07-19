# 功能交叉冲突十二条修复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复持续分析/精修/对齐/刷新/停录/多选等功能交叉运行时的 12 类冲突。

**Architecture:** 以前端 `Workbench/index.tsx` 生命周期门禁为主；后端 `remove_room` 兜底停持续分析；新增 `tests/test_cross_feature_guards.py` 源码守卫。

**Tech Stack:** React/TypeScript、Ant Design、WebSocket、pytest

**Spec:** `docs/superpowers/specs/2026-07-16-cross-feature-twelve-fixes-design.md`

**Status (2026-07-16):** ✅ Task 1–13 全部完成。回归：`test_cross_feature_guards.py` 13 项 + `test_ux_habit_guards.py` 合计 **33 passed**；`tsc --noEmit` **ok**。未创建 git commit。

**执行约束：**
- **不要 git commit**
- 只改本 Task 列出的范围
- 工作目录：`D:\Project\直播切片多人`
- 报告：DONE / DONE_WITH_CONCERNS / BLOCKED

---

## 文件结构

| 文件 | Task |
|------|------|
| `lsc-electron/src/pages/Workbench/index.tsx` | 1–12（多数） |
| `python-backend/handlers/room_handler.py` | 1, 4（兜底） |
| `lsc-electron/src/pages/Settings/index.tsx` | 12（可选提示） |
| `tests/test_cross_feature_guards.py` | 全部（新建） |

---

### Task 1: 删房/断连停持续分析

**Files:** Modify `index.tsx` `handleRemove`；Modify `room_handler.py` `handle_remove_room`

- [ ] **Step 1:** 前端 `handleRemove(roomId)`：读取 `continuousAnalysisStatus`；若 `running` 且 (`room_id === roomId` 或 `target_room_ids` 含 roomId)：
  - `send('stop_continuous_analysis', { main_room_id: continuousStatus.room_id })`
  - 再 `remove_room`
- [ ] **Step 2:** 后端 `handle_remove_room`：若 `_continuous_tasks` 的 key 为该房，或某任务 `target_room_ids` 含该房：取消对应 main 任务（复用现有 stop 逻辑 / 设 cancelled）。主房删除必须停；副房删除：从 target 移除或停任务（推荐：**副房删除 → toast 级前端已停；后端若 main 仍在则仅从该 task 的 target_room_ids 去掉该 id**；若删的是 main → 取消任务）。
- [ ] **Step 3:** `tests/test_cross_feature_guards.py` 断言 `handleRemove` 含 `stop_continuous_analysis`。

**最小后端实现建议：** 删房时若 `room_id in _continuous_tasks` → 标记 cancelled（与 stop 一致）；若 room_id 仅在其它任务的 target_room_ids 中 → 从 list 中 remove。

---

### Task 2: 精修只写本房 mark

**Files:** `index.tsx` `applySelectClip`

- [ ] 将 `const targets = new Set<string>([roomId, ...selectedRoomIds])` 改为 `const targets = new Set<string>(roomId ? [roomId] : [])`
- [ ] 测试断言：`applySelectClip` 函数体内不存在 `...selectedRoomIds` 用于 set_mark。

---

### Task 3: 短按刷新确认

**Files:** `index.tsx` `handleRefreshShortClick`

- [ ] 若 `continuousAnalyzing` 或 `getAlignStatus(timelineContext, timelineInvalidated)==='ready'`：先 `Modal.confirm`（标题「刷新预览将使公共轴失效」；内容说明分析不自动停止、需重新对齐；ok 后再执行现有 refresh 逻辑）。
- [ ] 否则直接刷新（无对齐无分析时无需打断）。
- [ ] 测试断言存在该 Modal 标题或关键文案。

---

### Task 4: 副房退出分析映射提示

**Files:** `index.tsx` `handleDisconnect`；可选后端

- [ ] `handleDisconnect`：若分析 running 且 roomId 在 `target_room_ids` 且不是 main：`message.warning('该房间已退出持续分析映射，后续回合可能仅入列主房')`，然后照常 disconnect（不必停整任务）。
- [ ] 主房断连：保持现有 stop_continuous_analysis。
- [ ] 测试断言警告文案存在。

---

### Task 5: 公共轴失效退出精修

**Files:** `index.tsx`

- [ ] 在 `timeline_invalidated` 监听里：若 `refiningClipId` 有值，对应该 clip `send('cancel_refine_clip', ...)`（可用 store clips 查找），然后 `setRefiningClipId(null)`、`setLocalDragMark(null)`。
- [ ] 对齐成功建立新 timeline（`timeline_ready`）时同样清 `refiningClipId`（避免旧精修挂在新轴上）。
- [ ] 测试断言 `timeline_invalidated` 块含 `setRefiningClipId(null)`。

---

### Task 6: 对齐中互斥标记/加切片/导出

**Files:** `index.tsx`

- [ ] 共享 helper `ensureNotAligning(): boolean`：若 `aligning`（用 state 或 ref），`message.warning('正在对齐，请稍候')` return false。
- [ ] 在 `handleControlMarkIn/Out`、`handleControlAddClip`、`handleExportClip`/`handleExportMany`/`handleConfirmExport` 开头调用；快捷键 export/mark 已走这些函数则覆盖。
- [ ] 测试断言「正在对齐，请稍候」。

---

### Task 7: 确认目标集过滤断连/对齐组

**Files:** `index.tsx` `handleConfirmClip` 内 `targetRoomIds` 计算

- [ ] 构建候选：`continuousTargetRoomIds` 优先，否则同 `align_group_id`。
- [ ] 过滤：`rooms` 中仍存在、`is_connected`（或至少仍有 record_output_path）、且若 clip 房有 `align_group_id` 则候选房必须同组或候选来自 continuous 冻结集。
- [ ] 排除 `clip.room_id`。
- [ ] 测试：确认逻辑含 `is_connected` 或等价过滤，且仍含 `continuousTargetRoomIds`。

---

### Task 8: 批量停录收尾提示

**Files:** `index.tsx` `handleBatchStop`

- [ ] Modal content：若 `continuousAnalysisStatus?.running`，追加「持续分析将收尾并将回合入列待确认，请勿立刻停止分析」。
- [ ] 测试断言该字符串在 `handleBatchStop` 附近。

---

### Task 9: AI pending 不覆盖有效手标

**Files:** `index.tsx` `clip_confirm_status` 监听

- [ ] 写 mark 前：取 room；若 `mark_in != null && mark_out != null` 且当前 `refiningClipId` 不是本 `round_key`，则**跳过** set_mark。
- [ ] 若 refining 同 round 或 mark 皆空，则写入。
- [ ] 测试断言存在跳过逻辑（如 `mark_in` 检查与 `set_mark_in` 同块）。

---

### Task 10: 主房无预览分析间隔提示

**Files:** `index.tsx` `handleConfirmAnalysisExport` 持续分析分支

- [ ] 若 `!mainRoomPreviewEnabled`：`message.info('主房未开启预览：持续分析间隔约 45 秒', 4)`（在 send 前后均可）。
- [ ] 测试断言文案。

---

### Task 11: 混选 no-DVR seek 提示

**Files:** `index.tsx` `handleTimelineSeek` / `enterTimelineLive`

- [ ] `enterTimelineLive`：若 `targetsIncludeNoDvrMode`，对**非** no-DVR 的子集仍可 goLive；对 no-DVR 跳过；若存在被跳过：`message.info('部分房间为回看模式，未跳转直播沿', 3)`。
- [ ] 或保持 enterTimelineLive 对含 no-DVR 的整集 return，但改为：过滤后再 goLive + toast（推荐过滤方案，避免整集不动）。
- [ ] 测试断言「回看模式」相关文案。

---

### Task 12: 预览画质/共享进样重启提示

**Files:** `index.tsx` 或 Settings

- [ ] 若前端有 `set_preview_quality` / 预览质量变更响应：成功后若曾 `timelineContext` 非空，warning「预览已重启，请重新一键对齐」。
- [ ] 若无独立 handler：在现有 `timeline_invalidated` toast 已足够时，于 Settings 改 `preview_quality` 保存成功处加一句 warning（改画质会重启预览）。
- [ ] 测试断言相关文案至少一处存在。

---

### Task 13: 回归

- [ ] `pytest tests/test_cross_feature_guards.py -v`
- [ ] `cd lsc-electron && npx tsc --noEmit`
- [ ] **不要 commit**
