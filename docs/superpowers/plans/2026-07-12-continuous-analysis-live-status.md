# Continuous Analysis Live Status Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让持续分析状态栏展示录制文件的真实时长和实际工作阶段。

**Architecture:** 持续分析循环在每个 tick 开始时读取录制文件并更新任务快照。后端状态事件始终携带该快照，前端只合并同一任务的增量状态，避免短事件清空已有指标。

**Tech Stack:** Python asyncio/WebSocket、React、TypeScript、pytest。

---

### Task 1: 后端真实任务快照

**Files:**
- Modify: `python-backend/handlers/room_handler.py`
- Test: `tests/test_continuous_analysis_guards.py`

- [ ] **Step 1: 写入失败守卫测试**

验证循环在开始决策前读取录制时长，并将 `recorded_duration` 和 `analysis_stage` 写入任务状态。

- [ ] **Step 2: 运行测试确认失败**

Run: `$env:PYTHONPATH='.'; pytest tests/test_continuous_analysis_guards.py -q`

- [ ] **Step 3: 最小实现**

将录制文件探测前置到 tick 开头，更新 `current_dur`、`recorded_duration` 和阶段；所有状态广播从任务快照读取这些字段。

- [ ] **Step 4: 运行测试确认通过**

Run: `$env:PYTHONPATH='.'; pytest tests/test_continuous_analysis_guards.py -q`

### Task 2: 前端状态合并与阶段展示

**Files:**
- Modify: `lsc-electron/src/pages/Workbench/index.tsx`
- Modify: `lsc-electron/src/components/AnalysisProgress.tsx`
- Test: `tests/test_frontend_stability_guards.py`

- [ ] **Step 1: 写入失败守卫测试**

验证同一房间的增量状态不会清空已有字段，且等待录制时不显示“运行中”。

- [ ] **Step 2: 运行测试确认失败**

Run: `$env:PYTHONPATH='.'; pytest tests/test_frontend_stability_guards.py -q -k continuous`

- [ ] **Step 3: 最小实现**

按 `room_id` 合并同一任务的状态；将 `等待新录制` 映射为等待状态和非活动徽标。

- [ ] **Step 4: 验证**

Run: `lsc-electron/node_modules/.bin/tsc.cmd --noEmit -p lsc-electron/tsconfig.json`
