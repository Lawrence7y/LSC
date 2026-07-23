# D2 — 分析会话草稿开关 + 结束自动生成

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在分析导出 Modal 增加「完成后生成剪映草稿」；持续分析 / 一次性分析在结束时自动生成一份多房对齐草稿；失败可在状态区重试。

**Architecture:** 前端 `AnalysisDraftSession`（ref/state）在启动时 `armed`；本场 AI `clip_queued` 记入 `clipKeys`；持续分析进入终态或一次性 `start_analysis_export_response.success` 后调用现有 `requestJianyingDraft`，传 `include_pending: true`、`allow_single_fallback: false`。

**Tech Stack:** React、Zustand clips、现有 WS `generate_jianying_draft`、`AnalysisProgress`

**Spec:** `docs/superpowers/specs/2026-07-23-analysis-auto-jianying-draft-design.md` §4.1、§4.3、§5、§8  
**前置:** D1（入口已清）、D3（`include_pending`）  
**后继:** 无（可选更新 `docs/PROJECT_DESIGN.md` 一句）

**执行约束：** 工作目录 `D:\Project\直播切片多人`；**不要 git commit**（除非用户要求）。

---

## Spec 校正（实现锁定）

| 项 | 锁定 |
|----|------|
| 一次性分析触发 | `start_analysis_export_response.success` 后 `setTimeout(400ms)` 再生成，给 `clip_queued` 入列窗口；若 `submitted_count===0` 走 H1 |
| 持续分析触发 | `wantDraft && armed`，且 status 从「曾 running/finalizing」变为 `running===false` 且 `phase` ∈ `{idle, completed}`；用 ref `sawRunning` 防启动失败误触 |
| 重试入口 | `AnalysisProgress` 旁或紧挨其下方的 Text Button「重试生成草稿」，仅 `status==='failed'` 显示 |
| 切片筛选 | `is_ai_highlight` 且（`clip_id` 或 `round_key`）在本场 `clipKeys`；启动后清空并重建 keys |

---

## 文件结构

| 文件 | 职责 |
|------|------|
| `lsc-electron/src/pages/Workbench/index.tsx` | Modal 开关、session、触发、重试 |
| `lsc-electron/src/components/AnalysisProgress.tsx` | 可选：接受 `onRetryDraft` / `draftFailed` props |
| `tests/test_analysis_draft_ux_guards.py` | 扩展 Modal 文案 / include_pending 调用守卫 |
| `lsc-electron/src/types/index.ts` | 可选：`analysis_session_id` on ClipSegment |

---

### Task 1: Modal 开关 + 守卫

**Files:**
- Modify: `tests/test_analysis_draft_ux_guards.py`
- Modify: `lsc-electron/src/pages/Workbench/index.tsx`

- [ ] **Step 1: 扩展失败测试**

```python
def test_analysis_modal_has_draft_switch_copy() -> None:
    text = WORKBENCH.read_text(encoding="utf-8")
    assert "完成后生成剪映草稿" in text


def test_auto_draft_sends_include_pending_and_no_fallback() -> None:
    text = WORKBENCH.read_text(encoding="utf-8")
    assert "include_pending" in text
    assert "allow_single_fallback" in text or "allowSingleFallback" in text
```

- [ ] **Step 2: 跑测确认文案断言失败**

```bash
python -m pytest tests/test_analysis_draft_ux_guards.py::test_analysis_modal_has_draft_switch_copy -v
```

Expected: FAIL

- [ ] **Step 3: Modal UI**

在分析导出 Modal（`continuousModalOpen`）内、持续分析 Switch 附近增加：

```tsx
<Switch checked={wantAnalysisDraft} onChange={setWantAnalysisDraft} disabled={continuousAnalyzing || continuousSubmitting} />
<span>完成后生成剪映草稿</span>
```

辅助短文案：多房须已对齐；含本场全部回合（含待确认）。

---

### Task 2: AnalysisDraftSession + 收集 clipKeys

**Files:**
- Modify: `lsc-electron/src/pages/Workbench/index.tsx`

- [ ] **Step 4: 会话状态**

```ts
type DraftSessionStatus = 'idle' | 'armed' | 'generating' | 'done' | 'failed'
// useRef 保存：
// wantDraft, mainRoomId, targetRoomIds, sessionId, clipKeys: Set<string>,
// status, sawRunning, lastError
```

在 `handleConfirmAnalysisExport` 成功 `send` 前/后：

- 若 `wantAnalysisDraft`：初始化 session（`status='armed'`，新 `sessionId`，`clipKeys` 清空，`sawRunning=false`）。
- 若未勾选：`status='idle'`，清空 session。

在现有 `clip_queued` handler：若 session `armed|generating|failed` 且 `data` 为 AI 高光（`is_ai_highlight` 路径 / 无 manual source），把 `clip_id` 与 `round_key`（若有）加入 `clipKeys`。

持续分析 status 监听：若 `running===true` 或 `phase` 为 `running|finalizing|stopping` → `sawRunning=true`。

---

### Task 3: 结束触发自动生成

**Files:**
- Modify: `lsc-electron/src/pages/Workbench/index.tsx`（复用 `requestJianyingDraft`）

- [ ] **Step 5: `runAnalysisDraftIfNeeded` helper**

```ts
async function runAnalysisDraftIfNeeded(reason: 'auto' | 'retry') {
  const s = draftSessionRef.current
  if (!s.wantDraft || s.status === 'generating' || s.status === 'done') return
  if (reason === 'auto' && s.status !== 'armed' && s.status !== 'failed') return
  const clips = useAppStore.getState().clips.filter(c =>
    s.clipKeys.has(c.clip_id) || (c.round_key && s.clipKeys.has(c.round_key))
  )
  if (clips.length === 0) {
    message.info('无切片，跳过草稿')
    s.status = 'done'
    return
  }
  s.status = 'generating'
  setJianyingLoading(true)
  const res = await requestJianyingDraft({
    roomIds: s.targetRoomIds,
    clips,
    includeClips: true,
    allowSingleFallback: false,
    // 确保 payload 含 include_pending: true
  })
  ...
}
```

扩展 `requestJianyingDraft` / `sendRequest` payload：

```ts
include_pending: true,
allow_single_fallback: false,
main_room_id: s.mainRoomId,
room_ids: s.targetRoomIds,
clips: clips.map(...), // 含 pending 的 confirm_status、common_start/end、start/end、label、clip_id、round_key、mark_precision
```

- [ ] **Step 6: 接线触发**

1. `continuous_analysis_status` / store 更新 effect：`sawRunning && !running && phase in (idle, completed) && status==='armed'` → `runAnalysisDraftIfNeeded('auto')`（用 ref 防严格模式双触发：`autoFired`）。
2. `start_analysis_export_response` success：若 session armed → `setTimeout(() => runAnalysisDraftIfNeeded('auto'), 400)`。
3. 失败：`status='failed'`，toast error。
4. 成功：`status='done'`，打开现有成功 Modal。

- [ ] **Step 7: 重试按钮**

在 `AnalysisProgress` 一行旁：

```tsx
{draftSession.status === 'failed' && (
  <Button size="small" loading={jianyingLoading} onClick={() => void runAnalysisDraftIfNeeded('retry')}>
    重试生成草稿
  </Button>
)}
```

需把 session status 镜像到 `useState` 以便渲染，或强制 `setDraftUiTick`。

---

### Task 4: 守卫收尾 + 回归

- [ ] **Step 8: 实现守卫期望的字符串**

确保 Workbench 源码含 `完成后生成剪映草稿`、`include_pending`、且仍无「生成剪映草稿」菜单文案。

- [ ] **Step 9: 跑测**

```bash
set QT_QPA_PLATFORM=offscreen
python -m pytest tests/test_analysis_draft_ux_guards.py tests/test_jianying_draft.py tests/test_jianying_ws_guards.py tests/test_continuous_analysis_guards.py::test_list_only_min_duration_allows_short_hybrid_rounds -v --tb=short
```

Expected: PASS

- [ ] **Step 10: 类型检查（前端）**

```bash
cd lsc-electron && npx tsc --noEmit
```

Expected: 无因本改动引入的错误

---

## Done 标准

- [ ] Modal 开关默认关；勾选后持续/一次性结束自动生成一次
- [ ] 请求带 `include_pending: true`、`allow_single_fallback: false`
- [ ] 多房未对齐失败可重试；0 切片跳过
- [ ] UX guards + jianying + list_only 时长测全绿
