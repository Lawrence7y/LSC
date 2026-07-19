# 使用习惯十四项修复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 按设计说明逐项修复工作台 14 个使用习惯/逻辑问题，使命名、门禁、快捷键与文档一致。

**Architecture:** 以前端 `Workbench/index.tsx`、`ClipList.tsx`、`useKeyboardShortcuts` 周边与 `CLAUDE.md` 为主；每项独立可测；禁止静默绕过 AI 确认门禁。

**Tech Stack:** React/TypeScript、Ant Design、Zustand、WebSocket、pytest（既有字符串门禁测试可扩展）

**Spec:** `docs/superpowers/specs/2026-07-16-ux-habit-fourteen-fixes-design.md`

**Status (2026-07-16):** ✅ Task 1–15 全部完成。回归：`tests/test_ux_habit_guards.py` + `test_frontend_stability_guards.py` **102 passed**；`lsc-electron` `tsc --noEmit` **ok**。未创建 git commit。

**执行约束：**
- **不要 git commit**（除非用户另行要求）。
- 只改本 Task 列出的文件；不做无关重构。
- 工作目录：`D:\Project\直播切片多人`
- 完成后报告：`DONE` / `DONE_WITH_CONCERNS` / `BLOCKED`，并列出改动文件与验证命令结果。

---

## 文件结构（总览）

| 文件 | 涉及 Task |
|------|-----------|
| `lsc-electron/src/pages/Workbench/index.tsx` | 1–10, 12–14 |
| `lsc-electron/src/pages/Workbench/components/ClipList.tsx` | 2, 6 |
| `lsc-electron/src/hooks/useKeyboardShortcuts.ts` | （文档对照；实现多在 Workbench） |
| `CLAUDE.md` | 11, 13 |
| `tests/test_frontend_stability_guards.py` 或新建 `tests/test_ux_habit_guards.py` | 2, 3, 4, 7（字符串断言） |

---

### Task 1: 「分析导出」改名为「分析」

**Files:**
- Modify: `lsc-electron/src/pages/Workbench/index.tsx`（工具栏 Button 文案约 `分析导出`）

- [x] **Step 1:** 将工具栏按钮在非 `continuousAnalyzing` 时的文案从 `分析导出` 改为 `分析`。
- [x] **Step 2:** 搜索仓库确认无其它用户可见「分析导出」误导文案（Modal 内说明可保留「分析…入列」）。
- [x] **Step 3:** 验证：`rg "分析导出" lsc-electron/src` 仅剩注释或历史无关处；按钮为「分析」。

**验收：** 工具栏显示「分析」；弹窗标题/确认按钮仍明确「入列」而非「导出成片」。

---

### Task 2: 批量导出不得自动确认 pending

**Files:**
- Modify: `lsc-electron/src/pages/Workbench/components/ClipList.tsx`
- Modify: `lsc-electron/src/pages/Workbench/index.tsx`（`canExportForShortcut`、`handleExportMany`）
- Test: `tests/test_ux_habit_guards.py`（新建，读源码字符串断言）

**行为：**
1. `canExportOrConfirmExport`：**删除**「pending 可因有 onConfirmAndExport 而 actionable」逻辑；`actionableClips` 仅 `canExportClip`。
2. 「导出全部 / 导出所选」tooltip：若存在 pending，提示「请先确认待调整的切片」且按钮在无可导确认项时 disabled。
3. `canExportForShortcut`：pending/refining **不可**导出（与 `canExportClip` 一致）。
4. `handleExportMany`：去掉对 pending/refining 的 `handleConfirmClip(..., boundsOnly)` 自动确认分支；若传入 pending 则跳过并 warning。
5. 单条「确认并导出」(`onConfirmAndExport`) **保留**。

- [ ] **Step 1:** 写 `tests/test_ux_habit_guards.py`，断言 `canExportForShortcut` 源码中不再把 pending 当作可导出，且 `handleExportMany` 不再对 pending 调 `boundsOnly` 自动确认（可用「不存在某模式」断言）。
- [ ] **Step 2:** 改 ClipList / Workbench 实现。
- [ ] **Step 3:** `pytest tests/test_ux_habit_guards.py -v` 通过。

**验收：** 列表有 pending 时「导出全部」不能把它们打出去；Ctrl+E 遇到仅 pending 时 toast「没有可导出的切片（…未确认）」。

---

### Task 3: 单房间允许一次性分析入列

**Files:**
- Modify: `lsc-electron/src/pages/Workbench/index.tsx`（`handleConfirmAnalysisExport` 非持续分支）

- [ ] **Step 1:** 删除或改写：
  ```ts
  if (targetRoomIds.length < 2) {
    message.warning('请至少选中 2 个房间（或开启持续分析）')
    return
  }
  ```
  改为：非持续模式允许 `length >= 1`；仅当 `length < 1` 时 warning。
- [ ] **Step 2:** 在 `tests/test_ux_habit_guards.py` 断言上述「请至少选中 2 个房间（或开启持续分析）」字符串已从 `index.tsx` 移除。
- [ ] **Step 3:** 确认持续模式仍保留「打开时多选≥2、确认时不足」的拦截（Task 14 相关逻辑勿删）。

**验收：** 只选 1 房、不勾持续分析，可「开始分析并入列」。

---

### Task 4: 无预览禁止 I/O 标记

**Files:**
- Modify: `lsc-electron/src/pages/Workbench/index.tsx`（`handleControlMarkIn` / `handleControlMarkOut`）

- [ ] **Step 1:** 在 mark in/out 开头：对 `selectedRoomIds`，若任一目标房 `!preview_enabled` 或 `__msePlayers[rid]` 无 video，则 `message.warning('请先开启预览再标记入出点')` 并 `return`（整次操作取消，避免半成功）。
- [ ] **Step 2:** 保证快捷键 `mark:in` / `mark:out` 走同一 handler。
- [ ] **Step 3:** 测试断言：源码含「请先开启预览再标记入出点」。

**验收：** 未开预览按 I → toast，房间 mark_in 不变。

---

### Task 5: 未对齐多房标记警告

**Files:**
- Modify: `lsc-electron/src/pages/Workbench/index.tsx`

- [ ] **Step 1:** 在 `handleControlMarkIn` / `Out`：当 `selectedRoomIds.size >= 2` 且 `getAlignStatus(...) !== 'ready'`，`message.warning('多房间未对齐：各房入出点按各自预览时间标记，导出可能不同步。建议先「一键对齐」', 4)`（仍继续标记）。
- [ ] **Step 2:** 避免同一次 I+O 双击刷屏：可用 ref 记录 2s 内只提示一次，或仅 mark:in 提示。推荐：**仅 mark:in 提示**。

**验收：** 多选未对齐按 I 有警告；仍能标上点。

---

### Task 6: 近似定位更显眼 + 导出二次确认

**Files:**
- Modify: `lsc-electron/src/pages/Workbench/components/ClipList.tsx`
- Modify: `lsc-electron/src/pages/Workbench/index.tsx`（`handleConfirmExport`、批量路径）

- [ ] **Step 1:** ClipList 行内：`isApprox` 时显示橙色 Tag「近似」（不必只靠 hover）。
- [ ] **Step 2:** 单条确认导出若 `isApproximateClip(previewClip)`：先 `Modal.confirm`（标题「近似定位切片」，内容说明可能偏差数秒，okText「仍要导出」），确认后再真正 `send('export_clip'...)`。
- [ ] **Step 3:** `handleExportMany` 若含近似：先 Modal 一次汇总确认，取消则整批不提交。

**验收：** 列表可见「近似」；导出近似条必须点确认。

---

### Task 7: 停录/收尾文案去掉错误「导出」

**Files:**
- Modify: `lsc-electron/src/pages/Workbench/index.tsx`

替换至少这些文案：
- `录制已停止。请稍候，持续分析正在收尾确认回合并导出，勿立刻关掉分析`
  → `录制已停止。请稍候，持续分析正在收尾并将回合入列待确认，请勿立刻停止分析`
- `…确认回合并导出，不要立刻关闭分析`
  → `…收尾并将回合入列待确认，请勿立刻停止分析`
- 其它含「收尾…导出」且实际不自动导出的用户可见字符串一并改。

- [ ] **Step 1:** `rg "回合并导出|确认回合并导出" lsc-electron/src` 清零用户可见误导。
- [ ] **Step 2:** 测试断言旧字符串不存在、新字符串存在。

**验收：** 停录 toast 不再暗示自动导出成片。

---

### Task 8: M 键只切换主选房间静音

**Files:**
- Modify: `lsc-electron/src/pages/Workbench/index.tsx`（`mute:toggle` 分支）

- [ ] **Step 1:** `mute:toggle` 改为只对一个 roomId 调用 `handleToggleMute`：
  - 优先 `selectedRoomId`（恰好单选时 store 有值）
  - 否则 `referenceRoomId` 或 `[...selectedRoomIds][0]`
- [ ] **Step 2:** 工具栏「全员静音」按钮行为不变。

**验收：** 多选 3 房按 M 只切 1 房静音。

---

### Task 9: 对齐失效保留房间本地入出点说明

**Files:**
- Modify: `lsc-electron/src/pages/Workbench/index.tsx`（`timeline_invalidated` 监听）

- [ ] **Step 1:** 仍清空 `commonMarkIn/Out` 与 timelineContext；**不要**发送清除各房 `set_mark_in/out null`。
- [ ] **Step 2:** toast 改为类似：`公共轴已失效，请重新对齐。各房间本地入出点仍保留；多房间精确切片需对齐后重标公共轴。`
- [ ] **Step 3:** 若仍提示「N 个多房间切片需重新对齐后导出」可保留后半句。

**验收：** 失效后房间卡片/后端 mark_in 仍在；仅公共轴清空。

---

### Task 10: 同步分析弹窗去掉误导性导出预设

**Files:**
- Modify: `lsc-electron/src/pages/Workbench/index.tsx`（分析 Modal）

- [ ] **Step 1:** 删除非持续模式下 Modal 内「导出预设」Select 区块（`continuousPresetId` 若仅此处使用可保留 state 但不再展示；`start_analysis_export` 可不传 preset 或传默认，与现网「不自动导出」一致）。
- [ ] **Step 2:** 确认说明文案仍写「入列待确认，不会自动导出」。

**验收：** 打开「多房间同步分析」看不到导出预设下拉。

---

### Task 11: 更新 CLAUDE.md 快捷键表

**Files:**
- Modify: `CLAUDE.md` §12.1

- [ ] **Step 1:** 改为：
  - `Ctrl + 1` → 工作台
  - `Ctrl + 2` → 设置
  - 删除或注明 Dashboard 快捷键已移除
- [ ] **Step 2:** 与 `MainLayout.tsx` 一致。

**验收：** 文档与代码快捷键一致。

---

### Task 12: R 键混合状态只停不启

**Files:**
- Modify: `lsc-electron/src/pages/Workbench/index.tsx`（`record:toggle`）

- [ ] **Step 1:** 若 `toStop.length > 0 && toStart.length > 0`：只走停止确认流程；`message.info('已选房间录制状态不一致：本次仅停止录制中的房间。未录制房间请再次按 R 启动')`。
- [ ] **Step 2:** 纯 start 或纯 stop 行为不变。

**验收：** 混合选中按 R 不会一边开一边停。

---

### Task 13: 对齐 loading 说明 8 秒 + 文档同步

**Files:**
- Modify: `lsc-electron/src/pages/Workbench/index.tsx`
- Modify: `CLAUDE.md` §5.3 / §8.5 中「3.0秒」为「约 8 秒（前端预览对齐采样）」

- [ ] **Step 1:** `message.loading({ content: '采集预览音频并对齐（约 8 秒）...', key: 'align', duration: 0 })`
- [ ] **Step 2:** 更新 CLAUDE.md 对应时长描述，避免文档写 3s、代码 8s。

**验收：** 对齐中文案含「约 8 秒」；文档一致。

---

### Task 14: 分析 Modal 打开期间同步目标房间

**Files:**
- Modify: `lsc-electron/src/pages/Workbench/index.tsx`

- [ ] **Step 1:** `useEffect`：当 `continuousModalOpen && !continuousAnalyzing` 时，用当前 `currentTargetIds` / `selectedRoomIds` 更新 `continuousTargetRoomIds`；若 `continuousMainRoom` 不在新列表中，设为 `targetRoomIds[0] ?? null`。
- [ ] **Step 2:** 保留「持续分析确认时，若打开时曾多选≥2 而确认时不足」的防护，或改为基于最新列表（推荐：**以 Modal 打开瞬间快照的 `openedWithMulti` ref 为准**，避免用户误减选后静默变单房持续分析）。实现建议：
  - 打开 Modal 时 `openedWithMultiRef.current = currentTargetIds.length >= 2`
  - 确认持续分析时若 `openedWithMultiRef && targetRoomIds.length < 2` 仍拦截
- [ ] **Step 3:** 去掉过时文案「请关闭弹窗后保持多选…」中「关闭弹窗」强制感，改为「请再选中至少两间目标房间」即可（列表已 live sync）。

**验收：** Modal 开着时改多选，主房 Radio 列表随之变；无需关窗。

---

## 收尾 Task 15: 回归验证

- [ ] `pytest tests/test_ux_habit_guards.py -v`（及被改到的既有 guard 测试）
- [ ] `cd lsc-electron && npx tsc --noEmit`
- [ ] `rg "分析导出|回合并导出|请至少选中 2 个房间（或开启持续分析）" lsc-electron/src` 应无误导残留

**不要 commit。**
