# CS2 开箱精调与规则编辑器验收增强设计

> **日期：** 2026-07-16  
> **状态：** 契约修订稿 — 待用户审阅；**审阅通过前不得进入实施计划**  
> **方案：** A — 引擎优先（多官方预设 + 编辑器验收；不重做向导）  
> **修订：** 纳入审阅 P1/P2 契约（基线、草稿试用、phase 直传、关键词 casefold、试跑执行期、状态区校验、砍 audio / 补样例清单）

## 代码基线（P1 · 前置条件）

本设计**不**建立在当前 `main` 的非文档代码之上。

| 项 | 约定 |
|----|------|
| 实施基线分支 | `feat/fps-round-rule-pack`（已有 `fps_round` 解释器、`trial_ocr_region`、`builtin:cs2`、RulePackEditor 等） |
| 与 `main` 对齐 | 实施计划启动前：将该分支与 `main` 合并/rebase 对齐；确认前置规格 [2026-07-16-fps-round-rule-pack-design.md](./2026-07-16-fps-round-rule-pack-design.md) 已在实施基线上可引用（若仅存在于 worktree，先合入仓库） |
| 本设计前置 | 规则包引擎已落地且 Valorant parity 可接受；本轮是其上的 **CS2 开箱 + 试用契约修正**，不是从 `main` 零实现规则包 |

> 若基线未对齐就开实施计划，会引用不存在的符号与未跟踪前置文档 — **禁止**。

**其它文档前置（在基线分支上有效）：**

- [2026-07-16-fps-round-rule-pack-design.md](./2026-07-16-fps-round-rule-pack-design.md)
- [2026-07-14-valorant-phase-scheduler-continuous-analysis-design.md](./2026-07-14-valorant-phase-scheduler-continuous-analysis-design.md)
- [2026-07-15-continuous-analysis-quality-first-design.md](./2026-07-15-continuous-analysis-quality-first-design.md)

## Goal

在无畏契约持续分析已达标、且规则包引擎已在基线分支可用的前提下：

1. 把 **CS2** 官方规则包拉到接近无畏的开箱体验（切回合、默认框/词、相位节奏），并修掉会静默回退 Valorant / 关键词大小写不一致等契约洞。
2. 在**不重做**「复制→编辑→保存→启动」主流程的前提下，补齐 **checklist + 试读/试跑**；试用必须能验**未保存草稿**，且与正式扫描共用同一套匹配与 phase 语义。

## 决策摘要（已确认）

| 项 | 选择 |
|----|------|
| 总体方案 | A：引擎优先；编辑器只补验收，不做 4 步向导 |
| CS2 开箱期望 | 常见 HUD 开箱即用；特殊皮肤/分辨率再手调 |
| HUD 覆盖 | 中英双语关键词 + 选手包 + 观战/赛事包 |
| 自定义痛点 | 步骤多、缺引导、缺验收（预览标定可接受） |
| 验收形态 | 单帧试读增强（绿/黄/红）+ 短片段试跑时间轴预览 |
| 试用输入 | **优先 `draft_pack`**；`rule_pack_id` 仅兼容已保存包 |
| 无畏契约 | 行为与产品链路不改（修复共享匹配/phase 直传时须跑 Valorant 回归） |
| `audio.*` 本轮 | **不做**映射与精调（见非目标） |

## 非目标

- 重做成多页向导 / 大改持续分析 Modal 信息架构
- 自动探框、规则云市场、分析中热换包
- 改变确认门默认（仍要求 OCR 起止才可升格）
- 改变 pending → 精修 → 手动导出产品链路
- 改变「只分析主房录制」、预览与录制分离、导出队列
- 无畏产品行为变更（回归测试必须绿）
- 本轮为规则包增加 / 消费 `audio.*` 字段映射（解释器现状未消费；无样例证据前不扩）
- 新建独立试跑任务系统 / 与持续分析并行的第二套 FFmpeg 池

## Architecture

```
draft_pack 或 已保存 rule_pack_id
        │
        ▼
┌──────────────────────────┐
│ validate_rule_pack()     │  ← 只校验，试用路径不落盘
└────────────┬─────────────┘
             ▼
┌──────────────────────────┐
│ fps_round 解释器          │
│ + 共享 casefold 关键词匹配 │
│ + phase profile 对象直传   │  ← 禁止 get_profile(name) 二次回退
└────────────┬─────────────┘
             │
        ┌────┴────┬────────────────┐
        ▼         ▼                ▼
      试读     试跑（短窗）      正式 continuous
   (单帧OCR)  (_analysis_semaphore)  (同信号语义)
```

编辑器路径（骨架不变，试用语义修正）：

```
选包 → 管理规则 → 复制/编辑（内存 draft）
         │
         ├─ checklist（框有效 → 试读绿 → 试跑有回合 → 保存）
         ├─ 试读(draft_pack) / 试跑(draft_pack)
         │
         ▼
保存落盘 →（启动前可对已保存 id 或再带 draft 试跑）→ start_continuous_analysis
```

## 关键契约修订（实施前必须落地）

### C1 · 试用接受草稿包（P1）

**问题：** 流程是「试读 → 试跑 → 保存」，但若只用 `rule_pack_id` 读磁盘，试用验的是旧包，与 UI 草稿不一致。

**契约：**

| 接口 | 入参 | 行为 |
|------|------|------|
| `trial_ocr_region` | 必填：框 + 图像；**可选 `draft_pack` 或关键词子集**；可选 `rule_pack_id` | 有 `draft_pack` 时：`validate_rule_pack(draft)`（**不保存**）后用其 `keywords` 做命中分类；仅 id 时加载已保存包。无词表时不得假装「命中绿」 |
| `trial_scan_rounds` | **`draft_pack` 优先**；否则 `rule_pack_id`；另需房间 id、时间窗 | 有 draft：校验不落盘 → 解释器扫描；仅 id：加载已保存包。二者互斥时 **draft 胜出** |

响应试读增加：

- `matched_category`: `"buy" | "result" | "none"`
- `status`: `"hit" | "partial" | "empty"`（有字未命中 = partial；空白/失败 = empty）

前端编辑中的试读/试跑**必须**带上当前表单拼出的 `draft_pack`（含框与关键词），不得只发框图。

### C2 · Phase profile 对象直传（P1）

**问题：** 包内写了 `phase` 后，`profile_from_mapping` 可能生成名 `pack`，扫描预算再 `get_profile("pack")` 会静默回退 Valorant（实测 lookback 30→120）。

**契约：**

- 持续分析与试跑的扫描窗口/预算，必须接收 `_continuous_valorant_phase_profile(state)`（或等价）**返回的 profile 对象**，直接使用其字段。
- **禁止**再通过 `get_profile(profile.name)` 二次解析。
- 为 CS2 官方包写入专用 `phase` **同时**修直传；只改 JSON 不算完成本条。
- 回归：构造 `phase.lookback_sec` 与 Valorant 默认不同的包，断言扫描预算采用包内值。

### C3 · 共享 casefold 关键词匹配（P1）

**问题：** 买枪词按原大小写、结算侧文本小写但词表不转；`COUNTER-TERRORISTS WIN` 等正式扫描可 False，试读若另写匹配会「试读绿、正式不中」。

**契约：**

- 抽出共享函数（名实施定），对文本与词表两侧均 `casefold()`（或等价）后再做包含匹配。
- **正式扫描**（`round_detector` 买枪/结果路径）与 **试读命中分类** 必须调用同一函数。
- 扩展 CS2 英文词表**不能**替代本修复；词表仍可保留常见大小写写法，匹配不依赖大小写。
- 单测：大写结果句 + 小写/混合词表 → 正式路径与试读分类均为命中。

### C4 · 试跑执行期契约（P1）

| 项 | 契约 |
|----|------|
| 资源 | 试跑复用 `_analysis_semaphore` 与 `_ai_executor`，与持续分析同一串行护栏 |
| 并发 | **持续分析运行中禁止试跑**（返回明确错误，如 `analysis_busy`）；不新建并行任务系统 |
| 超时 | 服务端：短窗扫描须有明确上限（建议与窗长挂钩，且 ≤ 现有单次 OCR/扫描可接受上限；具体秒数写入实施计划）。客户端：试跑请求不得沿用默认 10s；须单独更长超时或进度/可取消约定（实施计划二选一写死） |
| 取消 | 至少支持：前端关闭/取消时中断等待；服务端在 executor 任务可检查取消点则检查（最小：客户端放弃结果，服务端仍占 semaphore 至任务结束 — 若采用此降级须在 UI 标明「请等待当前试跑结束」） |
| 录制中文件 | 活动录制若 `moov` 未完成可能导致无法 OCR：试跑前检测；不支持时返回明确错误（如 `recording_file_not_seekable`），提示停止录制后再试或仅对已收尾文件试跑 |
| 响应坐标 | 必须包含 `window_start_sec`、`window_end_sec`、`recording_duration_sec`，以及回合 `{start,end,...}`（相对录制文件时间轴），供时间轴渲染 |

试跑**不**写入切片列表、**不**触发导出。

### C5 · 有效状态区服务端校验（P2→本轮必做）

**问题：** `x=1,y=1` 会归一成零面积框而非拒绝；设计不得声称「与既有后端约束一致」。

**契约：**

- 在 `validate_rule_pack()` 中增加状态区**最小宽高**校验（相对坐标，阈值实施计划写死，如 `w≥0.02` 且 `h≥0.02` 且落在画面内）。
- 零面积 / 过小 / 越界 → 校验失败。
- **保存用户包**与 **启动持续分析**与 **试用 validate** 共用同一校验。
- 前端可同步提示，但权威在服务端。

## CS2 引擎

### 官方包

| id | 用途 |
|----|------|
| `builtin:cs2` | 选手第一人称 HUD（主流 16:9） |
| `builtin:cs2_spectator` | 观战 / 赛事解说 HUD |

- 关键词：中英双语同一列表；匹配走 **C3 casefold**。
- `combat` 可空：开始仍以「离开买枪」为主。
- `confirm`：`require_ocr_bounds=true`，`start_by=ocr_buy_exit`，`end_by=[ocr_result, next_buy]`。

### 相对基线 `builtin:cs2` 的缺口

1. **写入 CS2 专用 `phase`**，并落实 **C2 直传**（二者缺一不可）。
2. **精调 `ocr_regions.status`**：选手 vs 观战分开，用样例标定。
3. **扩展 `keywords.buy` / `keywords.result`**：Freeze / Buy / 中英胜负等；依赖 C3，而非靠大小写变体碰运气。
4. **`duration.min_sec` / `max_sec`**：按样例收紧。
5. ~~按需微调 `audio.*`~~ → **本轮删除**（见非目标）。

### 样例与可复现验收（P2）

仓库当前无受版本控制的 CS2 黄金样例。本轮验收前必须具备：

| 交付物 | 要求 |
|--------|------|
| 样例清单 | 至少：选手 HUD、观战/赛事 HUD；中英尽量覆盖；路径/获取方式写入 `docs` 或测试夹具说明（大文件可 Git LFS / 外部固定 URL，但清单入仓） |
| 期望标注 | 每条样例的期望回合 `{start,end}`（容差目标 ≤ 1.0s） |
| 自动化或脚本 | 至少能对清单跑解释器并比对边界（可先脚本后 pytest） |

无清单与标注时，不得声称「明显改善」或「≤1s」已验收。

## 编辑器验收 UX

### 单帧试读

- 入口：编辑页现有「从这帧试读」。
- 必须带 **draft 关键词**（C1）；色态：绿 hit / 黄 partial / 红 empty。
- 与正式扫描共用 C3 匹配。

### 短片段试跑

- 入口：编辑页 + 启动前；遵守 C4。
- 默认最近 3 或 5 分钟，封顶建议 5 分钟。
- UI 用响应中的 `window_*` 与 `recording_duration_sec` 画轴。
- 0 回合：提示换选手/观战预设、挪框、改词。
- 分析中 / 文件不可 seek：明确错误文案。

### 轻引导（非向导）

Checklist：有效状态区 → 试读绿（或黄且用户知悉）→ 试跑 ≥1 回合 → 保存。  
弱引导不强制阻断保存；**无效状态区**由 C5 在保存/启动/试用校验时拒绝。

## 与现有契约的关系

- 正式持续分析仍 pending → 精修 → 手动导出。
- `round_key` 含 `rule_pack_id`。
- 旧 `game=valorant` → `builtin:valorant`。
- 用户包仍「复制官方 → 改可编辑字段」。
- 试用草稿**不**自动落盘；只有用户点保存才写入 `data/analysis_rules/`。

## Testing / 验收

1. **基线**：在 `feat/fps-round-rule-pack`（已与 main 对齐）上开发与测。
2. **C1**：改关键词未保存 → 试读/试跑反映新词；保存后 id 路径一致。
3. **C2**：CS2 `lookback`（或其它 phase 字段）与 Valorant 默认不同时，扫描预算等于包内值。
4. **C3**：`COUNTER-TERRORISTS WIN` 类句在正式扫描与试读均为命中。
5. **C4**：分析运行中试跑被拒；响应含窗口与时长字段；切片列表不被试跑污染。
6. **C5**：零面积/过小状态区保存与启动失败。
7. **CS2 开箱**：按样例清单与标注验收边界 ≤ 1.0s 与漏切/粘连改善。
8. **观战包**：观战样例上优于误用选手包。
9. **无畏回归**：parity / continuous / confirm 相关测试通过。

## 成功标准（产品体感）

- 选 CS2 官方包，常见选手 HUD 开箱可进 pending 回合。
- 观战局换观战包后质量可接受。
- 自定义时：对**当前草稿**试读看色、试跑看轴，结果与正式启动语义一致。
- 无畏用户无感（回归绿）。

## 实施顺序（供计划拆解 — 仅审阅通过后）

0. **基线对齐**：`feat/fps-round-rule-pack` ↔ `main`；前置规格入仓可引用  
1. **C3** 共享 casefold 匹配 + 测试  
2. **C2** phase profile 直传 + 测试  
3. **C5** `validate_rule_pack` 最小状态区  
4. CS2 `phase` + 双语词 + 选手框；新增 `builtin:cs2_spectator`  
5. **C1** 试用 draft_pack；试读命中色 UI  
6. **C4** `trial_scan_rounds` 执行期 + 时间轴 UI + checklist  
7. 样例清单/标注 + CS2/无畏回归  

## 开放问题（已拍板）

| 问题 | 决定 |
|------|------|
| 是否做 4 步向导 | 否（方案 A） |
| 观战是否单独官方包 | 是，`builtin:cs2_spectator` |
| 试跑是否入切片列表 | 否 |
| 试跑默认窗口 | 3 或 5 分钟可选，封顶建议 5 分钟 |
| 用户能否关 OCR 确认门 | 否 |
| 试用是否支持未保存草稿 | **是，draft_pack 优先** |
| 分析中能否试跑 | **否** |
| 本轮是否映射 audio.* | **否** |
| 实施基线 | **`feat/fps-round-rule-pack`，先与 main 对齐** |
