# 六模块稳定性 / 延迟全量修复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 按设计说明修复六路扫描列出的全部稳定性/延迟问题（正确性优先，延迟其次）。

**Architecture:** 分模块小步：先正确性（导出/分析/对齐/录制门禁），再 MSE 恢复编排，再 WS/store 节流；每项配源码或行为守卫测试。

**Tech Stack:** Python asyncio/Qt、Electron React/Zustand、pytest、tsc

**Spec:** `docs/superpowers/specs/2026-07-16-stability-latency-six-module-fixes-design.md`

**Status (2026-07-16):** ✅ Task 1–10 已落地。回归：`test_stability_latency_guards.py` **18 passed** + 相关 **7 passed**；`tsc --noEmit` **ok**。未创建 git commit。明确非目标未做：base64 协议、Workbench 大拆分、epoch 按房增量失效、分析 semaphore 扩容、frag_duration、缩短 8s 对齐。

**执行约束：**
- **不要 git commit**
- 只改本 Task 列出的范围
- 工作目录：`D:\Project\直播切片多人`
- 完成后报告：DONE / DONE_WITH_CONCERNS / BLOCKED

---

## 文件结构

| 文件 | Task |
|------|------|
| `python-backend/handlers/room_handler.py` | C1–C3, C5, D1, D4, E1, E3, A2, F2 |
| `python-backend/handlers/timeline_handlers.py` | C5 |
| `python-backend/server.py` / `message_bridge.py` | F2 |
| `lsc/gui/multi_room/manager.py` | B1–B3 |
| `lsc/core/services/recording_service.py` | B2 |
| `lsc/core/services/mse_streamer.py` | A5 |
| `lsc/core/services/shared_ingest.py` | A3 |
| `lsc-electron/src/hooks/useWebSocket.ts` | A1, A4, F3, F4 |
| `lsc-electron/src/store/appStore.ts` | F1 |
| `lsc-electron/src/pages/Workbench/index.tsx` | D1 |
| `tests/test_stability_latency_guards.py` | 全部（新建）+ 既有测试扩展 |

---

### Task 1: 导出启动失败补 clip_failed（C1）

**Files:** `room_handler.py` `_process_export_job`；`tests/test_stability_latency_guards.py`

- [ ] **Step 1:** 写测试：源码断言 `_process_export_job` 在启动失败/error 路径含 `clip_failed` broadcast（或行为单测 mock）。
- [ ] **Step 2:** 实现：`if result.get('error') and not result.get('success')`（或等价）→ `broadcast('clip_failed', {job_id, error, ...})`。
- [ ] **Step 3:** `pytest tests/test_stability_latency_guards.py -k export_start -v` 通过。

---

### Task 2: 导出 cancel / export_jobs 竞态（C2）

**Files:** `room_handler.py`

- [ ] **Step 1:** `start_export` 成功拿到 clip_id 后立刻写入 `export_jobs[job_id]`（在 bridge 返回路径最早处）。
- [ ] **Step 2:** `handle_cancel_export`：若 job 已在 `export_jobs` 则 kill；否则加入 cancelled set；若 worker 已过检查则尝试按 clip 杀。
- [ ] **Step 3:** 守卫测试断言注册发生在等待 done_event 之前或紧随 start 成功。

---

### Task 3: 导出队列满不阻塞（C3）+ by_id offset 快照（C5）

**Files:** `room_handler.py` `queue_export`；`timeline_handlers.py`

- [ ] **Step 1:** `put_nowait` + `QueueFull` → `{success:false, error:'导出队列已满，请稍后重试'}`。
- [ ] **Step 2:** `export_clip_by_id` 传入创建时 `content_offset` 快照给 `queue_export`。
- [ ] **Step 3:** 守卫测试。

---

### Task 4: 对齐 split-brain（D1）+ 互相关 executor（D4）

**Files:** `room_handler.py`；`Workbench/index.tsx`

- [ ] **Step 1:** `create_timeline` 为 None → 响应 `success:false`（或明确 `timeline:null` + success false），清除/不写误导性 align 成功态。
- [ ] **Step 2:** 前端无 `data.timeline` 时不 toast「已精确对齐」，warning 并清 common/align 相关状态。
- [ ] **Step 3:** `align_audio_map` 放入 executor。
- [ ] **Step 4:** 守卫测试。

---

### Task 5: OCR 超时可重试（E1）+ pause 不 skip（E3）

**Files:** `room_handler.py`

- [ ] **Step 1:** `_should_skip_continuous_scan_kick`：若上次 `error`/`worker_error` 则 return False。
- [ ] **Step 2:** Valorant 持续分析路径：`pause_analysis` 只增加 interval，不 `continue` 跳过 tick。
- [ ] **Step 3:** 守卫/单测。

---

### Task 6: 录制 shared 健康检查 + 磁盘 + 重连预检（B1–B3）

**Files:** `manager.py`；`recording_service.py`；测试

- [ ] **Step 1:** shared 录制分支即使 controller 存在也跑健康/停滞检测。
- [ ] **Step 2:** shared 分支执行 2GB disk_full。
- [ ] **Step 3:** 重连预检用 2GB（或 `preflight_for_reconnect`），开录仍 8GB。
- [ ] **Step 4:** 测试。

---

### Task 7: stop_recording timeout（B4）

**Files:** `room_handler.py` `handle_stop_recording`

- [ ] **Step 1:** `bridge.call` timeout 5→15。
- [ ] **Step 2:** 守卫断言 timeout>=15。

---

### Task 8: MSE 恢复编排（A1/A2/A4）+ shared stdout watchdog（A3）+ 去 -re（A5）

**Files:** `useWebSocket.ts`；`room_handler.py`；`shared_ingest.py`；`mse_streamer.py`

- [ ] **Step 1:** 前端 watchdog：连续失败计数；≥2 → enable_preview + toast；收到真实 segment 才刷新时间戳。
- [ ] **Step 2:** shared 重连轮换 epoch。
- [ ] **Step 3:** shared 15s 无 stdout 数据触发错误恢复。
- [ ] **Step 4:** `mse_streamer` 去掉输入 `-re`。
- [ ] **Step 5:** 前端守卫测试（字符串）。

---

### Task 9: setRooms 浅比较 + rooms 节流 + stats 节流（F1–F3）

**Files:** `appStore.ts`；`useWebSocket.ts`；`room_handler.py` / bridge

- [ ] **Step 1:** `setRooms` 浅比较。
- [ ] **Step 2:** `_queue_rooms_update` 走 coalesce/节流。
- [ ] **Step 3:** systemStats 1s / diskUsage 3s。
- [ ] **Step 4:** 守卫测试。

---

### Task 10: 文档对齐（D5）+ 总回归

- [ ] **Step 1:** 更新 `2026-07-14-next-iteration-trust-platform-hygiene.md` Track A 已落地项为 `[x]`，注明 split-brain 本计划修复。
- [ ] **Step 2:** `pytest tests/test_stability_latency_guards.py tests/test_export_queue_semaphore.py tests/test_continuous_analysis_guards.py -q`
- [ ] **Step 3:** `cd lsc-electron && npx tsc --noEmit`
- [ ] **Step 4:** 报告 DONE / DONE_WITH_CONCERNS（列出未做的非目标：base64、Workbench 拆分、epoch 增量失效、分析 semaphore 扩容、frag_duration、8s 对齐缩短）。

---

## 明确本轮不做（设计非目标，写入报告即可）

- A6 base64 协议更换  
- D2 epoch 按房增量失效  
- D3 缩短 8s PCM  
- E2 扩大 analysis semaphore  
- E4 双管线激进短路  
- E5 pending 语义变更  
- F5 Workbench 大拆分  
- A5 frag_duration 改 GOP  
