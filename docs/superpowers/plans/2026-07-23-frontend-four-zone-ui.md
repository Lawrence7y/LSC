# 前端四区域 UI 优化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 按 [前端四区域UI优化方案_task-587.md](../../../.qoder/specs/前端四区域UI优化方案_task-587.md) 完成工作台四区域视觉/信息架构优化，交互与状态机冻结不变，guard 与实现文案对齐，tsc + 相关 pytest 全绿。

**Architecture:** Working tree 里 **P0（AnalysisProgress / ClipList）与 P1（Timeline 视觉）已落地约 90–95%**；**Settings 半成品在 L455 处截断且缺少底部 helper，是当前编译 blocker**。本计划先把 WIP 与 guard 对齐收口，再以 `git HEAD` Settings 为干净基线完成 P2 重构。严禁改 scrub/打标/磁吸/Live/DVR handler；仅用 `tokens.css` 语义变量。

**Tech Stack:** React + TypeScript + Ant Design + Zustand；pytest 源码守卫；CSS tokens（`--brand-*` / `--bg-*` / `--text-*` / `--state-*`）

**Spec:** `.qoder/specs/前端四区域UI优化方案_task-587.md`

**Commits:** 用户未要求则不提交。建议按 Task 组拆 commit（P0 / P1 / P2 / guards）。

---

## 现状快照（计划起点，勿重复已完成工作）

| 区域 | Working tree | 剩余 |
|------|--------------|------|
| AnalysisProgress | 已有 `derivePrimaryStatus`、三段式 compact、滞后文案、待调/入列/导出门控、「去确认」 | card 模式可选精简；`useMemo`；**guard 文案落后** |
| ClipList | brand「导出全部/所选」、计数 chips、多房 chip、空态 CTA、`onConfirmAll`/`onStartAnalysis` | ≈ Tag 微调；**guard「待确认」vs 源码「待调」** |
| Timeline | 10px 刻度、key pill、AI 虚线/实色、播放头 glow、回直播按钮、min-height 76 | 清单核对；**禁止改 handler** |
| Settings | WIP：`SECTIONS` + `saveNow` + antd Select 开头；**L455 截断**；`SettingsSection`/`SettingsRow`/`ToggleSwitch`/`DepStatus` 未定义 | **先恢复再重构** |
| Guards | `test_frontend_stability_guards.py` 仍断言旧文案（`OCR可导` / `已跟进至` / `showProgress` / ClipList `待确认`） | 与实现同步改断言 |

**HEAD Settings 参考（`c13b701`，约 1141 行）：** 单文件含内联 helper（`ToggleSwitch`、`DepStatus` ~L1009、`SettingsRow` ~L1038、`SettingsSection`）、原生 `<select className="settings-select">`、底部统一保存逻辑与「录制设置」大组混杂项。

---

## File map

| 文件 | 职责 |
|------|------|
| `lsc-electron/src/components/AnalysisProgress.tsx` | 三段式状态条；`derivePrimaryStatus`；compact/card |
| `lsc-electron/src/pages/Workbench/components/ClipList.tsx` | 头部工具条、行内、空态（行为冻结） |
| `lsc-electron/src/pages/Workbench/index.tsx` | 已接线 `exportSummary` / `onConfirmAll` / `onStartAnalysis` / `scrollToClipPanel` — **仅在缺口时微调** |
| `lsc-electron/src/components/Timeline/index.tsx` | **仅渲染层**（标签文案/class）；handler 不动 |
| `lsc-electron/src/components/Timeline/Timeline.css` | 刻度/回合带/播放头/回直播视觉 |
| `lsc-electron/src/pages/Settings/index.tsx` | 布局 + `saveNow` + 分区渲染（恢复后重构） |
| `lsc-electron/src/pages/Settings/SettingsSection.tsx` | 可选抽出：锚点 section 容器 |
| `lsc-electron/src/pages/Settings/SettingsRow.tsx` | 可选抽出：label + control 行 |
| `lsc-electron/src/pages/Settings/settings.css` | sticky 左侧导航（宽屏） |
| `lsc-electron/src/types/index.ts` | 仅当 Settings/状态类型缺口时改；勿改 Clip 导出契约 |
| `tests/test_frontend_stability_guards.py` | AnalysisProgress / ClipList / Timeline / Settings 源码断言 |
| `tests/test_cross_feature_guards.py` | Settings `preview_quality` + 公共轴警告文案 |
| `tests/test_ux_habit_guards.py` | ClipList tooltip / 近似 Tag（改文案时核对） |

---

## Phase 0 — 编译 blocker：恢复 Settings 可构建基线

> 不先修这个，`npx tsc --noEmit` 必挂，后续改动无法验证。

### Task 0: 恢复 Settings 完整文件

**Files:**
- Modify: `lsc-electron/src/pages/Settings/index.tsx`

- [ ] **Step 1: 确认损坏范围**

当前文件约在「默认画质」`Select` options 的 `{ value: '高清'` 处截断；引用了未定义的 `SettingsSection` / `SettingsRow` / `ToggleSwitch` / `DepStatus`。

- [ ] **Step 2: 从 HEAD 恢复干净基线**

```bash
git checkout HEAD -- lsc-electron/src/pages/Settings/index.tsx
```

Expected: 文件恢复为完整单列 Settings（含 helper + 原生 select + 原保存模型）。

- [ ] **Step 3: 确认前端可类型检查**

```bash
cd lsc-electron && npx tsc --noEmit
```

Expected: Settings 相关错误消失（允许其它既有 WIP 文件仍有无关错误时，先只盯 Settings）。

- [ ] **Step 4: 记下 WIP 中已写好、待 P2 回填的片段**（不要丢设计意图）

从 git / 对话记忆保留这些 WIP 设计，供 Task 7+ 使用：

- `SECTIONS` 锚点列表（general / preview / env / recording / ai / storage / account / shortcuts / about / logs）
- `saveNow()` 300ms 防抖立即保存
- 系统环境右上角「重新检测」icon `extra`
- 预览画质独立到「预览体验」组的意图

---

## Phase P0 — 状态栏 + 切片列表收口

### Task 1: AnalysisProgress 与 guard 对齐

**Files:**
- Modify: `lsc-electron/src/components/AnalysisProgress.tsx`
- Modify: `tests/test_frontend_stability_guards.py`（`test_analysis_progress_labels_listed_not_raw_highlights`、`test_compact_analysis_progress_uses_live_following_copy_and_hides_empty_export_summary`）

**当前实现要点（勿回退）：**

- `derivePrimaryStatus` → `{ verb, detail, tone, nextAction? }`
- compact 三段：主状态 / 进度（已分析/已录/滞后）/ chips +「去确认 →」
- `exportActive` 门控导出 chip；无「N秒前更新」
- 「可导 N」替代旧「OCR可导」；完成态 verb「分析完成」+ detail 含待确认数

- [ ] **Step 1: 写/改失败的 guard 断言（先改测试对准产品文案）**

将旧断言改为反映现实现：

```python
# test_analysis_progress_labels_listed_not_raw_highlights
assert "入列" in progress
assert "待调" in progress
assert "可导" in progress          # 不再要求 "OCR可导"
assert "分析完成" in progress       # 不再要求 "分析完成·待确认" 字面量
assert "条待确认" in progress or "去确认" in progress

# test_compact_analysis_progress_uses_live_following_copy_and_hides_empty_export_summary
assert "const hasFixedScanRange" in source
# 删除对 "已跟进至" / "const showProgress = hasFixedScanRange" 的硬编码要求
# 改为断言：无固定扫描范围时不把空进度当完成 —— 例如 progress bar 宽度受 hasFixedScanRange 门控
assert "hasFixedScanRange ? livePercent" in source or "hasFixedScanRange" in source
assert "summary.queued > 0 || summary.exporting" in source  # 导出门控仍保留（可含 failed）
```

- [ ] **Step 2: 跑测试确认旧断言失败原因已消除，新断言通过**

```bash
pytest tests/test_frontend_stability_guards.py::test_analysis_progress_labels_listed_not_raw_highlights tests/test_frontend_stability_guards.py::test_compact_analysis_progress_uses_live_following_copy_and_hides_empty_export_summary -v
```

Expected: PASS

- [ ] **Step 3: 小幅实现补强（仅缺口）**

在 `AnalysisProgress` 内：

1. 对 `derivePrimaryStatus(current, summary)` 结果做 `useMemo`（deps: `current` 关键字段 + `summary`）
2. compact 行动区：若 `nextAction`/`stage` 需要「请先停录」引导且现无文案，补一行与状态机一致的 secondary 文案（**不要**改 phase 判断逻辑）
3. token 收敛：进度条底色优先 `--bg-tertiary` / `--background-700` 二选一并与其它 chip 一致，禁止引入新 hex
4. card 模式与 compact **同源** chips/进度文案（Workbench 只用 compact；card 保持可用即可）

- [ ] **Step 4: 再跑上述两测 + tsc**

```bash
cd lsc-electron && npx tsc --noEmit
pytest tests/test_frontend_stability_guards.py::test_analysis_progress_labels_listed_not_raw_highlights tests/test_frontend_stability_guards.py::test_compact_analysis_progress_uses_live_following_copy_and_hides_empty_export_summary tests/test_frontend_stability_guards.py::test_continuous_status_preserves_task_snapshot_and_labels_waiting_recording -v
```

---

### Task 2: ClipList 与 guard / UX habit 对齐

**Files:**
- Modify: `lsc-electron/src/pages/Workbench/components/ClipList.tsx`（仅文案/展示微调）
- Modify: `tests/test_frontend_stability_guards.py`（`test_clip_list_uses_readable_summary_and_virtual_row_height_for_export_progress`）
- Check: `tests/test_ux_habit_guards.py`（「请先确认待调整的切片」、近似 Tag）

**已实现勿回退：** `ExportOutlined` 主按钮、「全部确认」、`共 N · 待调 N · 可导 N`、多房 chip、空态「启动持续分析」、`ROW_HEIGHT = 88`、`contentVisibility`

- [ ] **Step 1: 更新 guard**

```python
assert "const ROW_HEIGHT = 88" in source
assert "共 {clips.length}" in source
assert "待调" in source   # 产品用词；不再强制「待确认」字面出现在头部
```

若其它测试仍要求行内「待确认」语义，保留 `confirm_status` / tooltip 中的「待调整」即可。

- [ ] **Step 2: 可选 UI 微调（spec 残留）**

1. pending +「近似」：优先单一 Tag + tooltip 含 ≈ 说明；避免独立 orange「近似」Tag 与「待调」双标打架（`test_ux_habit_guards` 若断言 `color="orange"`，改断言为 tooltip/`≈` 存在）
2. 确认 Workbench 仍传：`onConfirmAll={handleConfirmAllClips}`、`onStartAnalysis={openAnalysisModal}`（`index.tsx` ~3354–3377 / ~3742–3758）

- [ ] **Step 3: 验证**

```bash
pytest tests/test_frontend_stability_guards.py::test_clip_list_uses_readable_summary_and_virtual_row_height_for_export_progress tests/test_ux_habit_guards.py -v
```

Expected: PASS；交互：行选中、checkbox、确认/导出/删除语义不变。

---

### Task 3: P0 手工验收清单（不改代码，打勾）

- [ ] 持续分析运行时：一眼能答「在干什么 / 进度 / 下一步」
- [ ] 待调>0 → 点「待调」或「去确认」滚到切片列表
- [ ] 无活动导出时无导出 chip；有排队/导出中才出现
- [ ] 空切片列表 →「启动持续分析」打开 Modal
- [ ] 「全部确认」走 `boundsOnly`，不改边界
- [ ] dark / light 各看一眼 AnalysisProgress + ClipList

---

## Phase P1 — 时间线视觉层（核对 + 补洞）

### Task 4: Timeline 视觉核对与最小补强

**Files:**
- Modify only if gap: `Timeline.css`, `Timeline/index.tsx` **渲染 JSX**
- **禁止修改：** `handleMouseDown` / `attachWindowDragListeners` / `endPointerDrag` / `applyPointerTime` / `findSnapTarget` / `handleMarkerMouseDown` / `subscribeDisplayPlayhead` / followLive 状态机

**Checklist（已有则跳过）：**

| Spec | 期望 | 现状线索 |
|------|------|----------|
| 次刻度 10px | `.lsc-timeline__tick-label { font-size: 10px }` | CSS ~L88 |
| 关键阶段 pill | `--key` label 半透明底 | CSS ~L99 |
| AI 带标签 | 宽度够时 `Rxx` + 时长 | index ~L647 |
| pending 虚线 / confirmed 实色 | dashed vs `--confirmed` | CSS ~L165 |
| 播放头 3px + glow + scrub 变色 | `--dragging` | CSS ~L346 |
| 回直播浮动按钮 | `!followLive && onGoLive` | index ~L751 |
| 时间码左/中/右 | `__timecode` | index ~L764 |
| 轨道 min-height 76 | `__scroll` | CSS ~L21 |

- [ ] **Step 1: 逐项对照；缺口只改 CSS/className/标签文本**
- [ ] **Step 2: 跑 Timeline 相关 guard（交互冻结断言必须仍绿）**

```bash
pytest tests/test_frontend_stability_guards.py::test_timeline_uses_one_axis_with_distinct_clip_ai_selection_and_playhead_layers -v
# 另搜并跑含 attachWindowDragListeners / onScrubEnd / setTimelineFollowLive 的相关用例
pytest tests/test_frontend_stability_guards.py -k "timeline or scrub or followLive or DVR" -v
```

- [ ] **Step 3: 手工** — scrub 松手才 seek；Shift/Ctrl 打标；磁吸；偏离直播沿出现「回直播」；ControlBar「直播」与 Timeline「回直播」均可用

---

## Phase P2 — 设置页重构

> 基线 = Task 0 恢复后的 HEAD 文件。在其上重做 WIP，勿从截断文件续写。

### Task 5: 抽出 Settings 子组件 + sticky 导航壳

**Files:**
- Create: `lsc-electron/src/pages/Settings/SettingsSection.tsx`
- Create: `lsc-electron/src/pages/Settings/SettingsRow.tsx`
- Create (optional): `lsc-electron/src/pages/Settings/ToggleSwitch.tsx`、`DepStatus.tsx`（从原文件底部剪切）
- Create: `lsc-electron/src/pages/Settings/settings.css`
- Modify: `lsc-electron/src/pages/Settings/index.tsx`

- [ ] **Step 1: 从 `index.tsx` 底部剪切 helper 到独立文件，行为不变**

`SettingsSection` 需支持 `id`（锚点）、`title`、`extra?`、`children`。

- [ ] **Step 2: 增加 `SECTIONS` + 左侧 sticky nav（宽屏显示，窄屏 `@media` 隐藏）**

仅用 tokens；导航点击 `document.getElementById(id)?.scrollIntoView({ behavior: 'smooth' })`。

- [ ] **Step 3: `settings.css`**

```css
.settings-layout { display: flex; gap: 24px; }
.settings-nav { position: sticky; top: 16px; width: 140px; flex-shrink: 0; }
.settings-main { flex: 1; min-width: 0; }
@media (max-width: 1100px) { .settings-nav { display: none; } }
```

- [ ] **Step 4: tsc**

```bash
cd lsc-electron && npx tsc --noEmit
```

---

### Task 6: 保存模型统一为立即保存

**Files:**
- Modify: `lsc-electron/src/pages/Settings/index.tsx`

- [ ] **Step 1: 实现 `saveNow`（300ms 防抖）**

```typescript
const saveNow = () => {
  if (saveTimerRef.current) clearTimeout(saveTimerRef.current)
  saveTimerRef.current = setTimeout(() => {
    saveTimerRef.current = null
    const st = useAppStore.getState()
    send('save_settings', { ...st.settings, appSettings: st.appSettings })
  }, 300)
}
```

- [ ] **Step 2: 所有 `handleRecordChange` / `handleAppSettingChange` / `handleThemeChange` 走 `saveNow`**
- [ ] **Step 3: 移除底部「保存设置」按钮及「有未保存更改」类状态**
- [ ] **Step 4: OCR / 共享进样 / `export_max_concurrent` 去掉「改完再单独 `send('save_settings')`」的双重保存**（统一走 `handleRecordChange` → `saveNow`）；保留成功 toast（轻量，2s）
- [ ] **Step 5: 失败路径 — 监听 `save_settings_response`（若已有）失败时该行 `border`/`message.error`；无响应事件则保持现有 toast 行为并在注释标明
- [ ] **Step 6: 开机自启 / 托盘仍走 IPC；可选是否 `saveNow` 同步 appSettings（与 HEAD 行为一致即可，勿引入双写冲突）

---

### Task 7: 按 spec 重新分组 + antd Select

**Files:**
- Modify: `lsc-electron/src/pages/Settings/index.tsx`
- Modify: `tests/test_cross_feature_guards.py`（若警告文案搬家）
- Modify: `tests/test_frontend_stability_guards.py` Settings 相关断言（Cookie / OCR / encoder 等字符串须仍存在）

**分组目标：**

| Section id | 内容 |
|------------|------|
| general | 主题、开机自启、托盘 |
| preview | **预览画质**（从录制组拆出）+ 预览相关说明（含「公共轴/对齐」警告若原有） |
| env | FFmpeg/FFprobe/NVENC/Python；右上角重新检测 |
| recording | 画质/分辨率/帧率/音频/编码器/参数/预设/码率/CRF |
| ai | OCR 加速、共享进样、并发导出数、默认导出预设 |
| storage | 存储路径、剪映草稿目录 |
| account | 抖音/B站 Cookie |
| shortcuts | 快捷键表 |
| about | 版本、检查更新 |
| logs | LogViewer +「打开日志目录」（若 IPC 已有则接上） |

- [ ] **Step 1: 移动 JSX 块到对应 `SettingsSection`，不改字段名与合法值**
- [ ] **Step 2: 全部原生 `<select className="settings-select">` → `<Select size="small" options={[...]} />`**
- [ ] **Step 3: 删除仅服务原生 select 的 `.settings-select` CSS（若不再使用）**
- [ ] **Step 4: 系统环境单行化 DepStatus（状态点+版本+路径）；日志区块在关于之后**
- [ ] **Step 5: 更新/确认 guards**

```bash
pytest tests/test_cross_feature_guards.py -k "preview_quality or settings" -v
pytest tests/test_frontend_stability_guards.py -k "settings or Settings or Cookie or OCR or nvenc" -v
```

Expected: PASS；关键字符串仍在源码中：`B站 Cookie`、`shared_ingest_enabled`、`OCR 加速`、`h264_nvenc`、`preview_quality`、公共轴/对齐提示。

---

### Task 8: Settings 手工验收

- [ ] 宽屏左侧导航可跳转；≤1100px 导航隐藏且无横向滚动
- [ ] 改 CRF / 画质 / OCR：约 300ms 内落盘 + toast；无底部保存按钮
- [ ] dark/light 下 Select / Toggle 对比度正常
- [ ] Cookie / 检查更新 / 打开日志目录路径仍可用

---

## Phase 验收 — 全局

### Task 9: 自动化回归

- [ ] **Step 1: 类型检查**

```bash
cd lsc-electron && npx tsc --noEmit
```

Expected: 零错误

- [ ] **Step 2: 相关 pytest**

```bash
pytest tests/test_frontend_stability_guards.py tests/test_cross_feature_guards.py tests/test_ux_habit_guards.py tests/test_analysis_draft_ux_guards.py -q
```

Expected: 全绿

- [ ] **Step 3: 13 项冻结交互手工回归（spec §六）**

scrub 松手 seek · Shift/Ctrl 打标 · 磁吸优先级 · Live 状态机 · DVR 紫标 · 精修快捷键 · 行选中跳转 · checkbox · 确认/导出/删除 · ControlBar 直播 · Timeline 回直播 · 持续分析 Modal 多房冻结列表 · 导出门禁 pending

- [ ] **Step 4: 1280px 窄窗** — 工作台与设置无遮挡、无横向滚动

---

## 明确不做（计划级禁区）

- 不改任何交互行为、快捷键绑定、Live/DVR/磁吸/scrub 状态机
- 不引入新色值、新字体、新组件库
- 不改 RoomCard / ControlBar **功能结构**（时间线仅视觉）
- 不重构 `tokens.css` M21 light 覆盖技术债
- 不扩大拆分：Settings 最多抽 Section/Row/Toggle/DepStatus，不强制 `sections/*.tsx` 十文件爆炸（若单文件仍可维护可保持 index 分区注释）

---

## 推荐执行顺序

```
Task 0 (恢复 Settings)
  → Task 1–3 (P0 收口 + guards)
  → Task 4 (P1 核对)
  → Task 5–8 (P2 Settings)
  → Task 9 (总验收)
```

若需分 PR：`Task 0+1–3` → `Task 4` → `Task 5–8`。

---

## Self-review（写 plan 时已核对）

- [x] 无 TBD/占位：Settings 恢复命令与 guard 替换断言已写明
- [x] 与 working tree 一致：承认 P0/P1 已大部分落地，避免重复实现
- [x] 与 spec 一致：四区域 + 分期 + 禁区 + 验收
- [x] 范围可控：Settings 优先恢复再重构；Timeline 禁止碰 handler
