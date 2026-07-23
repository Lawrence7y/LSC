# D1 — 切片列表 / 导航草稿入口清理 + 列表无横向滚动

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 去掉切片列表与导航栏的剪映草稿入口；导出预览只保留 MP4；切片列表禁止横向滚动并完整展示信息。

**Architecture:** 删除 J3 引入的 `exportTarget` UI 与会话菜单项；保留 `generate_jianying_draft` 调用能力供 D2 使用（`requestJianyingDraft` / 成功 Modal 可留）。ClipList 行布局改为可换行、`overflow-x: hidden`。

**Tech Stack:** React、Ant Design、pytest 源码 guard

**Spec:** `docs/superpowers/specs/2026-07-23-analysis-auto-jianying-draft-design.md` §4.2、§4.4  
**前置:** 无  
**后继:** D3 → D2

**执行约束：** 工作目录 `D:\Project\直播切片多人`；**不要 git commit**（除非用户要求）。

---

## 文件结构

| 文件 | 职责 |
|------|------|
| `lsc-electron/src/pages/Workbench/components/ClipList.tsx` | 删 Segmented；修横向滚动 |
| `lsc-electron/src/pages/Workbench/index.tsx` | 删 Dropdown 草稿项、导出预览草稿 Radio、`exportTarget` 状态（若不再使用） |
| `tests/test_analysis_draft_ux_guards.py` | 新建：入口清除 + 列表 overflow 守卫 |
| `lsc-electron/src/types/index.ts` | `ExportTarget` 可保留类型（D2 不用也可留）；本里程碑不强制删类型 |

---

### Task 1: 失败守卫 — 禁止草稿入口

**Files:**
- Create: `tests/test_analysis_draft_ux_guards.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_analysis_draft_ux_guards.py
from __future__ import annotations
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLIPLIST = ROOT / "lsc-electron/src/pages/Workbench/components/ClipList.tsx"
WORKBENCH = ROOT / "lsc-electron/src/pages/Workbench/index.tsx"


def test_cliplist_has_no_draft_export_target_segmented() -> None:
    text = CLIPLIST.read_text(encoding="utf-8")
    assert "value: 'draft'" not in text
    assert "label: '草稿'" not in text
    assert "onExportTargetChange" not in text


def test_workbench_nav_has_no_jianying_menu() -> None:
    text = WORKBENCH.read_text(encoding="utf-8")
    assert "生成剪映草稿" not in text
    # 导出预览不得再提供草稿目标
    assert "仅剪映草稿" not in text
    assert "导出并生成草稿" not in text


def test_cliplist_forbids_horizontal_scroll() -> None:
    text = CLIPLIST.read_text(encoding="utf-8")
    assert "overflowX: 'hidden'" in text or 'overflow-x: hidden' in text or "overflowX: \"hidden\"" in text
```

- [ ] **Step 2: 跑测确认失败**

```bash
cd D:\Project\直播切片多人
set QT_QPA_PLATFORM=offscreen
python -m pytest tests/test_analysis_draft_ux_guards.py -v --tb=short
```

Expected: FAIL（入口仍在 / 无 overflowX hidden）

---

### Task 2: ClipList 去 Segmented + 无横向滚动

**Files:**
- Modify: `lsc-electron/src/pages/Workbench/components/ClipList.tsx`

- [ ] **Step 3: 实现**

1. 删除 props：`exportTarget`、`onExportTargetChange` 及 `ExportTarget` import（若仅此处用）。
2. 删除 `extra` 里 Segmented（MP4/草稿/两者）；保留「导出全部 / 导出所选」与计数。
3. 列表滚动容器：`overflowY: 'auto'`, `overflowX: 'hidden'`（替换单纯 `overflow: 'auto'`）。
4. 标题行：去掉强制 `whiteSpace: 'nowrap'` + ellipsis 作为唯一展示；改为 `whiteSpace: 'normal'`、`wordBreak: 'break-word'`，保证完整可读（Tooltip 可保留作补充）。
5. 第二行时间/按钮：允许 `flexWrap: 'wrap'`，避免挤出横向滚动。

- [ ] **Step 4: Workbench 去掉传给 ClipList 的 exportTarget props**

Modify `index.tsx`：`<ClipList ...>` 不再传 `exportTarget` / `onExportTargetChange`。

---

### Task 3: 导航与导出预览去草稿

**Files:**
- Modify: `lsc-electron/src/pages/Workbench/index.tsx`

- [ ] **Step 5: 实现**

1. 删除顶部 `Dropdown`（「生成剪映草稿」）；若菜单仅此一项，删除整个 `Dropdown` + `MoreOutlined` import（若无其它用途）。
2. 删除导出预览 Modal 内 `exportTarget` Radio（仅剪映草稿 / 两者等）；确认按钮文案固定为导出 MP4 相关；`handleConfirmExport` 只走 MP4 路径，删除 `doDraft` 分支。
3. 删除 `EXPORT_TARGET_KEY` / `readExportTarget` / `persistExportTarget` / `exportTarget` state（若无引用）。
4. **保留**：`requestJianyingDraft`、`jianyingResult` 成功 Modal、`jianyingLoading`（D2 要用）。可暂时留下未使用的 `handleGenerateSessionDraft`，或改名为内部 helper；不要删成功 Modal。

- [ ] **Step 6: 跑守卫与相关 UX guard**

```bash
python -m pytest tests/test_analysis_draft_ux_guards.py tests/test_ux_habit_guards.py -v --tb=short
```

Expected: PASS（若 ux_habit 依赖 exportTarget async，按失败信息微调）

- [ ] **Step 7: 手动快速核对（可选）**

打开工作台：切片列表无 Segmented；顶栏无 ⋯ 草稿；导出弹窗无草稿选项；列表窄宽度下无左右滑条。

---

## Done 标准

- [ ] `test_analysis_draft_ux_guards.py` 全绿
- [ ] ClipList / 导航 / 导出预览无草稿入口
- [ ] `requestJianyingDraft` + 成功 Modal 仍可被 D2 调用
