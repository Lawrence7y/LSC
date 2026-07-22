# 工作台状态栏与单轨时间线 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让持续分析状态准确表达直播跟进状态，并在不增加多轨的前提下提高时间线和切片列表的可读性。

**Architecture:** 复用既有状态、切片、时间轴 props；只改变紧凑状态栏的展示条件和单一时间轴的 CSS 图层。后端协议、坐标转换、确认与导出流程均不变。

**Tech Stack:** React 18、TypeScript、Ant Design、CSS、pytest。

---

### Task 1: 修正紧凑持续分析栏

**Files:**
- Modify: `lsc-electron/src/components/AnalysisProgress.tsx:121-229`
- Modify: `lsc-electron/src/pages/Workbench/index.tsx:3142-3191`
- Modify: `tests/test_frontend_stability_guards.py`

- [ ] **Step 1: 写失败测试**

```python
def test_compact_analysis_progress_uses_live_following_copy_and_hides_empty_export_summary() -> None:
    source = (ROOT / "lsc-electron/src/components/AnalysisProgress.tsx").read_text(encoding="utf-8")
    assert "const hasFixedScanRange" in source
    assert "已跟进至" in source
    assert "const showProgress = hasFixedScanRange" in source
    assert "summary.queued > 0 || summary.exporting > 0 || summary.completed > 0 || summary.failed > 0" in source
```

- [ ] **Step 2: 验证测试为红**

Run: `python -m pytest tests/test_frontend_stability_guards.py -q`

Expected: FAIL，旧实现没有 `hasFixedScanRange` 和“已跟进至”。

- [ ] **Step 3: 进行最小实现**

```ts
const hasFixedScanRange = Boolean(scanEnd > 0 && (current.scan_running || current.phase === 'finalizing'))
const showProgress = hasFixedScanRange && current.phase !== 'completed'
const showExportSummary = summary.queued > 0 || summary.exporting > 0 || summary.completed > 0 || summary.failed > 0
```

主状态使用“分析中”；在没有固定扫描范围的运行态显示 `已跟进至 {formatDuration(liveAnalyzedDuration)}`，导出四项摘要仅在 `showExportSummary` 时显示。
运行中的“停止持续分析”按钮改为 `danger` 外观，但保留现有收尾二次确认和启动分析外观。

- [ ] **Step 4: 验证测试为绿**

Run: `python -m pytest tests/test_frontend_stability_guards.py -q`

Expected: PASS。

- [ ] **Step 5: 提交状态栏改动**

```powershell
git add tests/test_frontend_stability_guards.py lsc-electron/src/components/AnalysisProgress.tsx lsc-electron/src/pages/Workbench/index.tsx
git commit -m "fix: clarify continuous analysis live status"
```

### Task 2: 用单轴 CSS 表达时间线区间层级

**Files:**
- Modify: `lsc-electron/src/components/Timeline/index.tsx:47,653-665`
- Modify: `lsc-electron/src/components/Timeline/Timeline.css:161-336`
- Modify: `tests/test_frontend_stability_guards.py`

- [ ] **Step 1: 写失败测试**

```python
def test_timeline_uses_one_axis_with_distinct_clip_ai_selection_and_playhead_layers() -> None:
    source = (ROOT / "lsc-electron/src/components/Timeline/Timeline.css").read_text(encoding="utf-8")
    assert ".lsc-timeline__highlight" in source and "border: 1px dashed" in source
    assert ".lsc-timeline__clip" in source and "height: 9px" in source
    assert ".lsc-timeline__selection" in source and "border: 1px solid" in source
    assert ".lsc-timeline__playhead::after" in source and "height: 24px" in source
    timeline = (ROOT / "lsc-electron/src/components/Timeline/index.tsx").read_text(encoding="utf-8")
    assert "const DEFAULT_CLIP_COLOR = 'rgba(63, 131, 248, 0.72)'" in timeline
```

- [ ] **Step 2: 验证测试为红**

Run: `python -m pytest tests/test_frontend_stability_guards.py -q`

Expected: FAIL，旧 CSS 没有候选虚线、9px 切片块或播放头竖线。

- [ ] **Step 3: 进行最小实现**

将 `DEFAULT_CLIP_COLOR` 改为 `rgba(63, 131, 248, 0.72)`；将 AI 候选设为橙色虚线半透明区间，已入列切片设为蓝色 9px 实心区间，当前选区设为 17px 蓝色透明底及细边框，播放头增加 24px 短竖线。维持现有单一轨道、拖拽、缩放、标记和点击回调，不新增 DOM 或 props。

- [ ] **Step 4: 验证测试为绿**

Run: `python -m pytest tests/test_frontend_stability_guards.py -q`

Expected: PASS。

- [ ] **Step 5: 提交时间线改动**

```powershell
git add tests/test_frontend_stability_guards.py lsc-electron/src/components/Timeline/index.tsx lsc-electron/src/components/Timeline/Timeline.css
git commit -m "feat: clarify single-axis timeline layers"
```

### Task 3: 改善列表摘要并防止虚拟行裁剪

**Files:**
- Modify: `lsc-electron/src/pages/Workbench/components/ClipList.tsx:8-11,447-449`
- Modify: `tests/test_frontend_stability_guards.py`

- [ ] **Step 1: 写失败测试**

```python
def test_clip_list_uses_readable_summary_and_virtual_row_height_for_export_progress() -> None:
    source = (ROOT / "lsc-electron/src/pages/Workbench/components/ClipList.tsx").read_text(encoding="utf-8")
    assert "const ROW_HEIGHT = 88" in source
    assert "共 {clips.length}" in source
    assert "待确认" in source
```

- [ ] **Step 2: 验证测试为红**

Run: `python -m pytest tests/test_frontend_stability_guards.py -q`

Expected: FAIL，旧实现是 76px 固定行和 `N/N待` 摘要。

- [ ] **Step 3: 进行最小实现**

将 `ROW_HEIGHT` 改为 `88`，标题摘要改成 `共 {clips.length}` 和可选的 `· 待确认 {pendingCount}`。不增加缩略图、筛选器或新交互。

- [ ] **Step 4: 验证测试、类型检查并提交**

Run: `python -m pytest tests/test_frontend_stability_guards.py -q`

Expected: PASS。

Run: `npm exec tsc -- --noEmit`（工作目录：`lsc-electron`）

Expected: exit 0。

- [ ] **Step 5: 提交切片列表改动**

```powershell
git add tests/test_frontend_stability_guards.py lsc-electron/src/pages/Workbench/components/ClipList.tsx
git commit -m "fix: improve clip list summary readability"
```
