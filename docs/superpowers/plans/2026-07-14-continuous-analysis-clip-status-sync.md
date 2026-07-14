# 持续分析 clip/status 同步 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** pending 切片边界随 OCR/合并自动 upsert；GET 状态与广播对齐；OCR re-kick 不被同 range 吞掉。

**Architecture:** 在 `room_handler.py` 抽出纯函数（入列决策、状态 payload、kick 跳过），`list_only` 路径改用 `_listed_clip_ids` + `_listed_clip_bounds`，前端复用已有 `clip_queued` upsert。

**Tech Stack:** Python 3.12、pytest、现有 WebSocket 广播

**Spec:** [2026-07-14-continuous-analysis-clip-status-sync-design.md](../specs/2026-07-14-continuous-analysis-clip-status-sync-design.md)

---

### Task 1: 纯函数 — 入列 upsert 决策

**Files:**
- Modify: `python-backend/handlers/room_handler.py`（模块级 helper）
- Test: `tests/test_clip_list_upsert.py`

- [ ] 实现 `_should_broadcast_clip_list_update(...)` + 常量 `_CLIP_BOUNDS_UPSERT_THRESHOLD = 0.3`
- [ ] 测试：首次 / upsert / 阈值内跳过 / refined 跳过 / exported 跳过

### Task 2: `_auto_export_highlights` 接上拆分集合

**Files:**
- Modify: `python-backend/handlers/room_handler.py`（`register_room_handlers` 内）

- [ ] 增加 `_listed_clip_ids`、`_listed_clip_bounds`
- [ ] `list_only` 不再写 `_exported_clip_ids`；按 helper 决定是否广播
- [ ] 清理主循环里对 list_only 的二次 `_exported_clip_ids.add`

### Task 3: re-kick 条件

**Files:**
- Modify: `python-backend/handlers/room_handler.py` ~5605
- Test: `tests/test_clip_list_upsert.py` 测 `_should_skip_continuous_scan_kick`

### Task 4: 状态 payload 共用

**Files:**
- Modify: `python-backend/handlers/room_handler.py`
- Test: confirmed_rounds 缺省 0；字段含 round_phase / refine_with_ocr

### Task 5: 前端 toast 与轮询字段（如需）

**Files:**
- Modify: `lsc-electron/src/pages/Workbench/index.tsx`（upsert 不弹「新切片」）

### Task 6: 回归

- [ ] `pytest tests/test_clip_list_upsert.py tests/test_continuous_analysis_guards.py tests/test_clip_refine_state.py -q`
