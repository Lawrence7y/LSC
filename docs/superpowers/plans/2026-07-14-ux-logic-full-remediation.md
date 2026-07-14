# UX / 逻辑全量修复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development`（推荐）按 Workstream 派生子代理。配套提示词见同目录 `2026-07-14-ux-logic-full-remediation-subagent-prompts.md`。Steps 使用 checkbox（`- [ ]`）追踪。

**Goal:** 一次性修完「切片命名/导出队列/设置误导/品牌色」已拍板项，以及真实用户走查发现的全部逻辑与 UX 问题（含 AI 精修坐标系 P0）。

**Architecture:** 按 8 条可并行 Workstream 拆分；W1（坐标系）与 W2（命名）尽量先合入，因其影响导出正确性与列表契约。其余流在不改公共契约的前提下可并行。禁止扩大产品范围（不做手动切片精修、不做 NLE）。

**Tech Stack:** Electron React/TS/Zustand/Ant Design；Python `room_handler` + `MultiRoomManager` + `ClipExporter`；pytest 契约测试 + `test_frontend_stability_guards.py` 源码守卫。

**非目标:**
- 手动切片进入精修 UI（产品已定：仅统一模型与命名）
- 改默认 `shared_ingest_enabled=True`
- 重写 ExportQueue 独立页面（继续用 ClipList + AnalysisProgress）
- 自动下载安装更新

---

## 0. 已拍板决策（锁定）

| ID | 决策 |
|----|------|
| D1 | 文件命名：`{主播}_手动_{NNN}.mp4` / `{主播}_AI_回合{RR}_{NNN}.mp4`（序号每房间独立递增；非法字符走现有 `safe_title`） |
| D2 | 导出并发：设置项可选 `1` 或 `2`，默认 `2`；写入 `settings.json`，后端 worker 池读取 |
| D3 | 删除设置「绝对分数下限」`absolute_threshold`（UI + 类型默认 + 请求传参）；不接线分析管线 |
| D4 | 主色：`#31B3AE`（图标取样略亮）；`--brand-500` 及 Ant `colorPrimary` 收敛；消灭 `#007aff` / `#1677ff` 硬编码 |
| D5 | 手动切片：保证 `clip_id` 等模型字段完整、命名统一；**不**进入 refine |

## 1. 走查问题 → Workstream 映射

| 审计# | 摘要 | WS | 严重度 |
|------|------|----|--------|
| 9/10 | AI 精修确认/seek 坐标系混用 | W1 | P0 |
| 11 | AI 误标「近似」 | W1 | P1 |
| 12 | 精修横幅时间轴文案 | W1 | P2 |
| 5/21 | 三套命名混乱 | W2 | P2 |
| 13 | 手动点列表无反应 | W2 | P2（按 D5：保证有 clip_id，但仍不进 refine；可选 toast「手动切片请直接导出」） |
| 22 | 并发硬编码 2 | W3 | P2 |
| 18 | Ctrl+E 只导第一条 | W3 | P1 |
| 23 | 批量导出静默跳过 | W3 | P2 |
| 24 | 同步分析仍立即导出 vs 持续分析 pending | W3 | P2 |
| 15 | absolute_threshold 无效 | W4 | P2 |
| 1 | 共享进样改了不保存不生效 | W4 | P1 |
| 16 | 设置文案不一致 | W4 | P2 |
| 17 | 品牌色未统一 | W5 | P2 |
| 6 | 对齐后默认静音快房 | W6 | P1 |
| 7 | 断房清空整组公共轴 | W6 | P1 |
| 8 | 多房分析对齐门槛提示晚 | W6 | P1 |
| 4 | 未对齐拖拽近似导出 | W6 | P1（加强门槛，不假精确） |
| 19 | Modal 内快捷键仍触发 | W7 | P2 |
| 20 | 无选中房间快捷键静默 | W7 | P2 |
| 14 | 删列表不取消导出/精修 | W8 | P2 |
| 25 | 磁盘满停录无强提示 | W8 | P1 |
| 2 | 多路预览降画质无持久提示 | W8 | P2 |
| 3 | B 站预览长等待无进度 | W8 | P2 |
| 26 | MSE watchdog 打断观看 | W8 | P2 |
| 27 | 离线连接长时间「连接中」 | W8 | P2 |

## 2. 文件职责图

| 文件 | 职责 |
|------|------|
| `lsc-electron/src/utils/clipNaming.ts` | **新建**：统一 label/文件名生成 |
| `lsc-electron/src/pages/Workbench/index.tsx` | 精修坐标系、导出、对齐静音、快捷键、删切片、分析门槛 |
| `lsc-electron/src/pages/Workbench/components/ClipList.tsx` | 近似徽标、导出状态、选择导出反馈 |
| `lsc-electron/src/hooks/useKeyboardShortcuts.ts` | Modal/焦点门控 |
| `lsc-electron/src/pages/Settings/index.tsx` | 删阈值、并发设置、共享进样即时保存、文案 |
| `lsc-electron/src/styles/tokens.css` / `App.tsx` | 品牌色 |
| `lsc-electron/src/types/index.ts` / `store/appStore.ts` | 设置类型与默认值 |
| `python-backend/handlers/room_handler.py` | 导出并发、AI confirm 坐标、timeline invalidate、磁盘满广播、预览 phase |
| `lsc/exporter/clip.py` | 仅消费 title（命名在上游统一） |
| `lsc/gui/multi_room/manager.py` | 磁盘满事件字段 |
| `tests/test_clip_refine_handlers.py` 等 | 坐标系与命名契约 |
| `tests/test_frontend_stability_guards.py` | 前端源码守卫 |

## 3. 时间轴契约（W1 必读，禁止破坏）

```
# AI 持续分析入列时 clip.start/end = 录制文件秒（recording_local）
# 预览 seek / set_mark_* 的 time = 预览本地秒（preview_local）
# common = preview_local + preview_to_common_delta
# recording_local = common - recording_to_common_delta

# 精修确认写回列表 / 导出 source=ai_highlight 时：
# start/end 必须仍是 recording_local，绝不能把 preview_local 原样塞回
```

对齐就绪时：
`recording = common_to_recording(ctx, room_id, commonMark)`  
或等价：`preview_to_common` → `common_to_recording`。

未对齐时：精修 seek 不得用 recording 秒直接 `mseSeek`；应：
- 有 `common_*` 则经 context 转换；或
- 用墙钟/content_offset 近似映射到 preview；或
- 禁止进入精修并提示「请先对齐/预览」。

---

## Workstream W1 — AI 精修坐标系（P0）

### Task W1.1: 失败测试锁定「确认后仍为 recording 秒」

**Files:**
- Modify/Create: `tests/test_clip_refine_handlers.py`
- Modify: `tests/test_frontend_stability_guards.py`（可选守卫）

- [ ] **Step 1:** 增加测试：模拟 `confirm_highlight_clip` 收到 preview 秒时，后端若做转换应产出 recording 秒；或契约规定前端必须传 recording 秒并断言 handler 不把 preview 当 recording。
- [ ] **Step 2:** 运行 `pytest tests/test_clip_refine_handlers.py -v` 确认当前红/绿基线。
- [ ] **Step 3:** 修复 `Workbench/index.tsx` `applySelectClip` / `handleConfirmClip`：
  - seek/mark：recording→preview（经 TimelineContext 或安全降级）
  - confirm 写回：preview/common→recording
  - `confirm_highlight_clip` payload 的 start/end = recording_local
- [ ] **Step 4:** `isApproximateClip`：`is_ai_highlight === true` 时返回 false（除非明确 `mark_precision==='approximate'`）。
- [ ] **Step 5:** 精修横幅按 `commonMode` 显示「公共时间轴」或「预览时间轴」，勿写死「录制时间轴」。
- [ ] **Step 6:** 测试全绿后提交：`fix: keep AI refine confirm/export on recording timeline`

---

## Workstream W2 — 命名与手动切片模型

### Task W2.1: 统一命名工具

**Files:**
- Create: `lsc-electron/src/utils/clipNaming.ts`
- Modify: `Workbench/index.tsx`（手动 add / clip_queued 展示 label）
- Modify: `python-backend/handlers/room_handler.py`（`_auto_export_highlights` / `start_analysis_export` label）

命名规则：

```ts
// clipNaming.ts
export function formatManualClipLabel(streamer: string, index: number): string {
  return `${sanitize(streamer)}_手动_${String(index).padStart(3, '0')}`
}
export function formatAiRoundClipLabel(streamer: string, roundIdx: number, index: number): string {
  return `${sanitize(streamer)}_AI_回合${String(roundIdx).padStart(2, '0')}_${String(index).padStart(3, '0')}`
}
```

Python 侧实现等价函数（可放 `room_handler` 顶部小 helper，避免跨包大重构）。

- [ ] **Step 1:** 实现 TS + Python helper；序号按 **room_id** 维度计数（勿用全局 `clips.length`）。
- [ ] **Step 2:** 替换所有 label 拼接点；导出仍用 label 作 title。
- [ ] **Step 3:** 手动 `create_clip_snapshot` / `handleAddClip` 路径确保写入 `clip_id`（可用 snapshot id 或 `manual-${Date.now()}`），使列表模型完整；`handleSelectClip` 对 `source!=='ai'` 且无 `round_key` 的手动条：不进 refine，toast「手动切片可直接导出」。
- [ ] **Step 4:** 守卫测试断言命名 helper 存在且 Workbench 不再出现 `片段 ${` / `_高光` 旧格式。
- [ ] **Step 5:** Commit: `feat: unify clip file naming with source prefixes`

---

## Workstream W3 — 导出队列与快捷键导出

### Task W3.1: 并发可配置

**Files:**
- Modify: `Settings/index.tsx`, `types/index.ts`, `appStore.ts`, `persistence` 默认
- Modify: `room_handler.py` `_EXPORT_MAX_CONCURRENT` → 从 settings 读取并支持热更新（`save_settings` 时重建 worker 池或调整目标并发）

- [ ] **Step 1:** 设置键 `export_max_concurrent: 1 | 2`，默认 2；UI Select。
- [ ] **Step 2:** `queue_export` 初始化/更新 worker 数；降到 1 时不杀正在跑的任务，仅停止启动新的第二路。
- [ ] **Step 3:** 测试：改设置后池大小符合预期。

### Task W3.2: Ctrl+E / 静默跳过 / 同步分析一致性

- [ ] **Step 1:** `export:clip`：若 ClipList 有勾选则导出勾选可导项；否则若有 refining 则导出该条；否则导出第一条可导项；无则 toast。
- [ ] **Step 2:** `handleExportMany` 统计 `skippedNoFile`，结束 toast「已排队 N，跳过 M（无录制文件）」。
- [ ] **Step 3:** `start_analysis_export`（同步分析）改为与持续分析一致：`list_only` / `clip_queued` pending，**不**自动 `queue_export`（或设置开关「分析后自动导出」默认关）。优先：改为 pending 一致。
- [ ] **Step 4:** Commit: `fix: export queue UX, Ctrl+E selection, analysis list-only`

---

## Workstream W4 — 设置清理

### Task W4.1

- [ ] **Step 1:** 删除 Settings「绝对分数下限」UI；从 `AnalysisSettings` 类型与 `appStore` 默认移除；Workbench 请求体不再传 `absolute_threshold`。
- [ ] **Step 2:** 共享进样开关 `onChange` 后立即 `save_settings`（与主题一致），并 toast「已保存，新预览/录制生效」。
- [ ] **Step 3:** 文案：「码率限制」→「自定义码率」；预览画质选项与后端契约对齐（去掉或映射「标清」）。
- [ ] **Step 4:** Commit: `chore: remove dead absolute_threshold; fix settings save UX`

---

## Workstream W5 — 品牌色 #31B3AE

### Task W5.1

- [ ] **Step 1:** `tokens.css`：`--brand-500: #31B3AE`；配套 `--brand-400`（略亮）、`--brand-600`（略暗）。
- [ ] **Step 2:** `App.tsx` ConfigProvider `colorPrimary: '#31B3AE'`。
- [ ] **Step 3:** Grep 消灭 `#007aff`、`#1677ff`、`#2e8dff` 业务硬编码（测试/文档除外）；`ErrorBoundary` fallback 同步。
- [ ] **Step 4:** Commit: `style: switch brand primary to icon cyan #31B3AE`

---

## Workstream W6 — 对齐 / 公共轴 / 分析门槛

### Task W6.1: 对齐静音改为 opt-in

- [ ] **Step 1:** 去掉对齐成功后自动 `set_preview_muted true`；改为 toast 带「静音其他房间」按钮（或 Checkbox「对齐后静音快房」默认 false）。

### Task W6.2: 断房不整组销毁

- [ ] **Step 1:** `disconnect_room`：仅标记该房 snapshot 失效 / 从 align group 移除；若剩余 ≥2 且仍有 reference，保留 `TimelineContext`；仅当剩余 <2 或 reference 断开时 `_invalidate` 全组。
- [ ] **Step 2:** 前端 `timelineInvalidated` 逻辑对齐；toast 说明「已移除某房，公共轴仍可用」或「参考房断开，请重新对齐」。

### Task W6.3: 分析前置门槛 + 近似导出

- [ ] **Step 1:** 多房「开始持续分析」按钮：未对齐时 disabled + Tooltip「请先一键对齐」。
- [ ] **Step 2:** 未对齐拖拽切片：导出确认强调风险；可选禁止「精确」文案（保持 approximate badge）。
- [ ] **Step 3:** Commit: `fix: align mute opt-in; soft timeline invalidate; analysis gate`

---

## Workstream W7 — 快捷键边界

### Task W7.1

- [ ] **Step 1:** `isInputFocused` 扩展：存在 `.ant-modal-wrap` 可见 / `role=dialog` 时视为阻塞（导航键除外）。
- [ ] **Step 2:** 无 `firstSelectedId` 时 I/O/R/M 等 `message.info('请先选择房间')`。
- [ ] **Step 3:** Commit: `fix: ignore shortcuts in modal; feedback when no room selected`

---

## Workstream W8 — 列表卫生与运行反馈

### Task W8.1: 删除联动

- [ ] **Step 1:** `handleDeleteClip`：若 `export_status` in queued/exporting → `cancel_export`；若正在 refining → `cancel_refine_clip`；再 `setClips`。

### Task W8.2: 磁盘满

- [ ] **Step 1:** manager 磁盘满停录时 `bridge.queue_broadcast({type:'recording_stopped', reason:'disk_full', room_id, message})`。
- [ ] **Step 2:** 前端始终 toast.error + 房间卡持久错误（窗口聚焦也通知）。

### Task W8.3: 预览信任

- [ ] **Step 1:** 多路降画质：持久小横幅「当前多路预览已降画质」直至 preview 数下降。
- [ ] **Step 2:** `preview_phase` 增加可区分阶段（refreshing_url / probing / transcoding），VideoPreview 显示阶段；B 站不要求假进度条百分比，但阶段必须变化。
- [ ] **Step 3:** MSE watchdog：stall 先 `request_mse_init`；仅连续 2 次失败再 `enable_preview` 重拉；toast「预览恢复中」。
- [ ] **Step 4:** 连接中：`offline` 错误尽快结束 loading（解析返回 offline 时立即 `room_connect_finished`）；卡片文案「未开播」。
- [ ] **Step 5:** Commit: `fix: delete cancels export; disk-full alert; preview trust UX`

---

## 4. 建议合并顺序

```
W1 (P0) → W2 → W3 → W6 → W7 → W8 → W4 → W5
```

可并行：W4 ∥ W5 ∥ W7（与 W1 无文件冲突时）；W5 尽量最后避免与 Settings 同文件冲突。

## 5. 验收清单

- [ ] AI pending → 点选 → 预览画面落在正确回合附近；确认后导出内容与精修一致
- [ ] 新导出文件名符合 D1；旧格式不再由新代码生成
- [ ] 设置并发=1 时系统中同时最多 1 个 ffmpeg 导出
- [ ] 设置页无「绝对分数下限」
- [ ] 主按钮/进度条/焦点环为青色系 #31B3AE
- [ ] 对齐不再自动静音；断非参考房公共轴可保留
- [ ] Ctrl+E 尊重勾选；删导出中切片会取消任务
- [ ] 磁盘满有全局错误提示
- [ ] `pytest tests/test_clip_refine_handlers.py tests/test_frontend_stability_guards.py tests/test_continuous_analysis_guards.py -v` 通过
- [ ] `cd lsc-electron && npx tsc --noEmit` 通过

## 6. 配套提示词

见：`docs/superpowers/plans/2026-07-14-ux-logic-full-remediation-subagent-prompts.md`
