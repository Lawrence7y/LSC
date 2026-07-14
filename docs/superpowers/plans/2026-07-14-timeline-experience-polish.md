# 时间线体验抛光包 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在单轨 scrubber 上补齐专业播控手感（步进/穿梭/变速）、入出点微调、A-B 真循环与时间线视觉分层，不引入 NLE/波形。

**Architecture:** 快捷键与 ControlBar 统一走 Workbench 的 `mseSeek` / mark helpers（公共轴用 common↔preview 换算）；A-B loop 用 rAF 听播放头；视觉只改 CSS；契约测试用前端源码守卫。

**Tech Stack:** React/TS、`useKeyboardShortcuts`、MSE `<video>`、pytest 源码守卫

**Spec:** [2026-07-14-timeline-experience-polish-design.md](../specs/2026-07-14-timeline-experience-polish-design.md)

**Commits:** 用户未要求则不提交。

---

## File map

| 文件 | 职责 |
|------|------|
| `lsc-electron/src/hooks/useKeyboardShortcuts.ts` | 增量快捷键常量；允许 seek/nudge 连按 |
| `lsc-electron/src/pages/Workbench/index.tsx` | seekByDelta、nudge mark、rate、A-B rAF loop、接线 |
| `lsc-electron/src/pages/Workbench/components/ControlBar.tsx` | 速率 Select/按钮 |
| `lsc-electron/src/components/Timeline/Timeline.css` | 视觉分层 |
| `tests/test_frontend_stability_guards.py` | 守卫：新快捷键 id、无波形复活、未对齐仍有「近似」 |

---

### Task 1: 快捷键表 + 连按

**Files:**
- Modify: `lsc-electron/src/hooks/useKeyboardShortcuts.ts`
- Modify: `tests/test_frontend_stability_guards.py`

- [ ] 在 `WORKBENCH_SHORTCUTS` 增加：`seek:back-1`/`fwd-1`、`seek:back-fine`/`fwd-fine`、`seek:back-2`/`fwd-2`、`play:toggle` 的 K、`mark:nudge-in`/`out`、`rate:cycle`
- [ ] `e.repeat`：对 `seek:` / `mark:nudge` 放行连按
- [ ] 守卫断言源码含 `seek:back-1` 与 `j`/`l` 等

### Task 2: Workbench seek / nudge / rate / loop

**Files:**
- Modify: `lsc-electron/src/pages/Workbench/index.tsx`
- Modify: `lsc-electron/src/pages/Workbench/components/ControlBar.tsx`

- [ ] `handleSeekByDelta(delta)`：公共轴用 ref 房 common 时间 +delta 再映射各房；否则各房 preview ±delta
- [ ] `handleNudgeMark(which, delta)`：调 common 或 room mark，并 `send` set_mark_*
- [ ] `handleSetPlaybackRate(rate)` / `handleCycleRate`：写各选中房 `video.playbackRate`
- [ ] A-B：rAF 检测 ≥ out → seek in；停用 setInterval
- [ ] ControlBar：0.5/1/1.5/2 速率控件
- [ ] 快捷键 switch 接线

### Task 3: 时间线 CSS 分层

**Files:**
- Modify: `lsc-electron/src/components/Timeline/Timeline.css`

- [ ] 弱化厚底板；进度线品牌色；播放头/选区 z-index 清晰；时间码对比度

### Task 4: 回归

- [ ] `pytest tests/test_frontend_stability_guards.py -q`
- [ ] 手动：公共轴步进同步、未对齐拖拽仍 toast「近似」、循环无漂移
