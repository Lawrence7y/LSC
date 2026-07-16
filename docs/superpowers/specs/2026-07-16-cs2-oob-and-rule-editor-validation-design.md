# CS2 开箱精调与规则编辑器验收增强设计

> **日期：** 2026-07-16  
> **状态：** 契约修订稿 v3 — 待用户审阅；**审阅通过前不得进入实施计划**  
> **方案：** A — 引擎优先（多官方预设 + 编辑器验收；不重做向导）  
> **修订：** v2 基线/草稿/phase/casefold/试跑/状态区；v3 收敛 C1 互斥模式、C4 双向 busy + 超时拍板、C5 完整 draft 信任边界、C6 checklist 失效、试读错误语义、试跑范围声明、观战可计算指标

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
2. 在**不重做**「复制→编辑→保存→启动」主流程的前提下，补齐 **checklist + 试读/试跑**；试用必须能验**未保存草稿**，且与正式扫描共用同一套匹配与边界检测语义（见 C2 范围声明）。

## 决策摘要（已确认）

| 项 | 选择 |
|----|------|
| 总体方案 | A：引擎优先；编辑器只补验收，不做 4 步向导 |
| CS2 开箱期望 | 常见 HUD 开箱即用；特殊皮肤/分辨率再手调 |
| HUD 覆盖 | 中英双语关键词 + 选手包 + 观战/赛事包 |
| 自定义痛点 | 步骤多、缺引导、缺验收（预览标定可接受） |
| 验收形态 | 单帧试读增强（绿/黄/红）+ 短片段试跑时间轴预览 |
| 试用输入 | 三种互斥模式（见 C1）；编辑页走 `image + draft_pack` |
| 试跑超时 | 单次同步请求；无进度任务系统；客户端超时 > 服务端 deadline + 余量 |
| 无畏契约 | 行为与产品链路不改（共享匹配/phase 直传修复须跑 Valorant 回归） |
| `audio.*` 本轮 | **不做**映射与精调（见非目标） |

## 非目标

- 重做成多页向导 / 大改持续分析 Modal 信息架构
- 自动探框、规则云市场、分析中热换包
- 改变确认门默认（仍要求 OCR 起止才可升格）
- 改变 pending → 精修 → 手动导出产品链路
- 改变「只分析主房录制」、预览与录制分离、导出队列
- 无畏产品行为变更（回归测试必须绿）
- 本轮为规则包增加 / 消费 `audio.*` 字段映射
- 新建独立试跑任务系统 / 与持续分析并行的第二套 FFmpeg 池
- 试跑完整回放持续分析的 phase 状态机调度节奏（见 C2）

## Architecture

```
draft_pack 或 已保存 rule_pack_id
        │
        ▼
┌──────────────────────────┐
│ validate_rule_pack()     │  ← draft 信任边界：结构+数值+区域（不落盘）
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
   (单帧OCR)  (_analysis_busy)   (增量+状态机)
```

编辑器路径（骨架不变）：

```
选包 → 管理规则 → 复制/编辑（内存 draft）
         │
         ├─ checklist（仅当前 draft revision 的试读/试跑结果）
         ├─ 试读(image + draft_pack) / 试跑(draft_pack)
         │
         ▼
保存落盘 → start_continuous_analysis（试跑 busy 时拒绝）
```

## 关键契约修订（实施前必须落地）

### C1 · 试用输入三种互斥模式（P1）

**问题：** 同时允许框、`draft_pack`、关键词子集、`rule_pack_id` 时，框与草稿 `ocr_regions.status` 冲突无权威来源。

**契约：删除「关键词子集」模式。** `trial_ocr_region` / `trial_scan_rounds` 解析规则包时仅允许下列模式（按优先级判定）：

| 模式 | 入参 | 权威字段 | 行为 |
|------|------|----------|------|
| A · 草稿 | `image` + `draft_pack`（试跑无 image，仅 `draft_pack` + 房间/窗口） | **整包草稿**：含 `ocr_regions.status` 与全部 `keywords` | `validate_rule_pack(draft)`（**不保存**）→ 用草稿框+词；忽略同请求里额外的 `region` / `rule_pack_id`（若误传） |
| B · 已保存 | `image` + `rule_pack_id`（无 `draft_pack`） | 磁盘包内的框与关键词 | 加载已保存包后同样校验 |
| C · 兼容旧调用 | `image` + `region`（无 `draft_pack`、无 `rule_pack_id`） | 仅请求中的 `region` | **只返回 OCR 文本**；**不做**命中分类（无 `status`/`matched_categories`，或显式 `classified=false`） |

- `draft_pack` 与 `rule_pack_id` 同时存在 → **draft 胜出**（模式 A）。
- 编辑页试读/试跑**必须**走模式 A，禁止只发框图指望服务端拼词。
- 模式 A/B 的试读成功响应：
  - `matched_categories`: `string[]`，元素为 `"buy"` / `"result"`（可同时命中；空数组表示未命中任一类）
  - `status`: `"hit" | "partial" | "empty"`  
    - `hit`：`matched_categories` 非空  
    - `partial`：OCR 成功且有可信文字，但未命中任何关键词  
    - `empty`：**仅**表示 OCR 成功但无可信文字（空白/乱码到不可信阈值）  
  - UI：`hit`→绿，`partial`→黄，`empty`→红（「框里没字」类提示）
- **禁止**把依赖缺失、FFmpeg 失败、超时等并入 `empty`。此类必须 `success: false` + `error`（及可选 `error_code`）；UI 显示错误，不得伪装成「框里没字」。

### C2 · Phase profile 对象直传 + 试跑范围（P1）

**问题：** 包内写了 `phase` 后，再 `get_profile("pack")` 会静默回退 Valorant。

**契约：**

- 持续分析与试跑的扫描窗口/预算，必须接收 `_continuous_valorant_phase_profile(state)`（或等价）**返回的 profile 对象**，直接使用其字段。
- **禁止**再通过 `get_profile(profile.name)` 二次解析。
- 为 CS2 官方包写入专用 `phase` **同时**修直传；只改 JSON 不算完成本条。
- 回归：构造与 Valorant 默认不同的 `phase.lookback_sec`，断言扫描预算采用包内值。

**试跑范围声明（避免实施成回放调度器）：**

- 试跑 = 对选定时间窗调用与正式路径相同的 **规则包 + casefold 关键词 + OCR 边界检测 / 解释器扫描**。
- 试跑**验证**：框、词、duration/trim、确认门相关的边界检出是否合理。
- 试跑**不承诺**：与正式持续分析完全相同的 phase 状态机推进节奏、买枪休眠唤醒时序、增量窗推进过程。
- 产品文案可写「预览本窗口能切出的回合」，勿写「与正式分析逐秒一致」。

### C3 · 共享 casefold 关键词匹配（P1）

- 抽出共享函数；文本与词表两侧均 `casefold()` 后再包含匹配。
- **正式扫描**与 **试读命中分类** 必须调用同一函数。
- 扩展 CS2 词表不能替代本修复。
- 单测：大写结果句 + 混合大小写词表 → 正式与试读均命中；可同时落入 `buy` 与 `result` 时两者都进入 `matched_categories`。

### C4 · 试跑执行期与双向 busy（P1）

| 项 | 契约 |
|----|------|
| 资源 | 试跑复用 `_analysis_semaphore` 与 `_ai_executor` |
| 双向门禁（服务端权威） | ① 持续分析运行中 → 拒绝试跑（如 `analysis_busy`）② **试跑运行中 → 拒绝 `start_continuous_analysis`**（如 `trial_scan_busy`）③ **重复试跑**（已有试跑未结束）→ 拒绝（如 `trial_scan_busy`）。前端可禁用按钮，**不能**只靠 UI |
| Semaphore 生命周期 | `_analysis_semaphore` **必须持有到 executor 内真实扫描结束**（含异常路径）。**禁止**外层 await 超时后释放 semaphore、而工作线程仍继续跑 |
| 超时方案（已拍板） | **单次同步请求**；不建进度/任务系统。服务端对单次试跑设 `deadline`（与窗长挂钩，上限写入实施计划）。客户端超时 = 服务端 deadline **+ 余量**（余量写入实施计划，须明显大于网络抖动）。超时后：客户端放弃展示；服务端 busy / semaphore **仍保留到真实任务结束**；UI 标明「试跑仍在结束后才会解除占用，请稍候再试」 |
| 关闭 UI | 只放弃显示结果，**不**视为服务端取消；busy 同上 |
| 录制中文件 | 不可 seek / `moov` 未完成 → `success:false` + 明确错误（如 `recording_file_not_seekable`） |
| 响应坐标 | 必含 `window_start_sec`、`window_end_sec`、`recording_duration_sec`，以及回合 `{start,end,...}` |

试跑**不**写入切片列表、**不**触发导出。

### C5 · `validate_rule_pack()` 作为 draft 信任边界（P1）

**问题：** 仅补最小框不够；当前校验器仍可能接受负 duration、NaN、把字符串拆成字符列表、非法 phase 等。`draft_pack` 经 WebSocket 传入，校验器是信任边界。

**契约：** `validate_rule_pack()`（保存 / 启动 / 试用模式 A·B 共用）至少拒绝：

| 类别 | 规则 |
|------|------|
| 数值 | 所有数值字段必须为有限数：`math.isfinite(...)`；拒绝 NaN / Inf / 非数字字符串冒充 |
| 区域 | **先**检查原始边界与类型，再谈归一化；`ocr_regions.status`（及若存在的 killfeed）：有限数、最小宽高（如 `w≥0.02` 且 `h≥0.02`）、`x≥0,y≥0`、`x+w≤1`、`y+h≤1`。**禁止**先 clamp 再放行以掩盖越界/零面积 |
| duration | `0 < min_sec < max_sec`，且不超过与 UI 一致的上限（上限写入实施计划，前后端同一常量来源或文档对齐） |
| trim | `start_pad_sec` / `end_pad_sec` 有限且 **≥ 0** |
| 列表字段 | `keywords.buy` / `combat` / `result` 与 `confirm.end_by` **必须是 list**；拒绝字符串被拆成字符列表的静默通过 |
| phase | 若存在 `phase`：字段有限且类型正确，且 **能成功构造 profile 对象**；构造成功才算校验通过 |

失败返回明确错误，试用路径同样不落盘。

### C6 · Checklist / 试用结果失效（P1）

**问题：** 试读变绿后改框或关键词，checklist 仍显示已通过。

**契约（前端状态机，无需 hash 系统）：**

| 变更 | 失效范围 |
|------|----------|
| 状态框、任一类关键词、试读用帧、当前规则包身份（换包/复制源） | 清空**试读与试跑**结果 |
| `duration`、`trim`、试跑目标房间、试跑时间窗 | 清空**试跑**结果（试读可保留） |

- Checklist 勾选**只能**依据清空后仍有效的、对应当前 draft 的最近一次成功结果。
- 实现：在现有 `updateEditing()`（及等价修改路径）里重置结果状态即可。

## CS2 引擎

### 官方包

| id | 用途 |
|----|------|
| `builtin:cs2` | 选手第一人称 HUD（主流 16:9） |
| `builtin:cs2_spectator` | 观战 / 赛事解说 HUD |

- 关键词：中英双语；匹配走 C3。
- `combat` 可空；`confirm` 与现网一致。

### 相对基线 `builtin:cs2` 的缺口

1. 写入 CS2 专用 `phase` + 落实 C2 直传。  
2. 精调选手 / 观战 `ocr_regions.status`。  
3. 扩展 `keywords.buy` / `result`（依赖 C3）。  
4. 收紧 `duration`。  
5. ~~`audio.*`~~ 本轮不做。

### 样例与可复现验收

| 交付物 | 要求 |
|--------|------|
| 样例清单 | 选手 + 观战/赛事；中英尽量覆盖；路径/获取方式入仓 |
| 期望标注 | 每条样例期望回合 `{start,end}` |
| 指标 | 对每条样例计算：`missed`（漏切）、`merged`（粘连）、`false_positive`（多切），以及边界 **MAE**（与标注起止的平均绝对误差，秒） |
| 观战包判定 | 在**同一观战标注集**上：`missed + merged + false_positive` 总数 **低于**误用 `builtin:cs2` 的结果，并报告两边的边界 MAE；不得只写「优于」 |
| 选手开箱 | 在选手标注集上报告上述指标；边界 MAE 目标 ≤ 1.0s（作为目标，未达标须在验收记录说明差距） |

## 编辑器验收 UX

- 试读：模式 A；绿/黄/红对应 hit/partial/empty；基础设施错误走 error UI。  
- 试跑：遵守 C4；用窗口字段画轴；文案符合 C2 范围声明。  
- Checklist：遵守 C6；弱引导不强制保存；无效包由 C5 拒绝保存/启动/试用。

## 与现有契约的关系

- 正式持续分析仍 pending → 精修 → 手动导出。  
- `round_key` 含 `rule_pack_id`。  
- 旧 `game=valorant` → `builtin:valorant`。  
- 用户包仍「复制官方 → 改可编辑字段」。  
- 试用草稿不自动落盘。

## Testing / 验收

1. 基线分支已与 main 对齐。  
2. **C1**：模式 A 改词未保存即反映；A/B/C 互斥；模式 C 不分类；OCR 失败为 `success:false`。  
3. **C2**：phase 预算直传；试跑不做状态机回放断言。  
4. **C3**：大小写句正式+试读均命中；双类同时命中进 `matched_categories`。  
5. **C4**：分析中拒试跑；试跑中拒启动与重复试跑；semaphore 持有至真实结束。  
6. **C5**：负 duration / NaN / 字符串 keywords / 越界框 / 非法 phase 均校验失败。  
7. **C6**：改框后 checklist 试读勾选清除。  
8. CS2 样例指标报告；观战包满足可计算「优于」定义。  
9. 无畏回归通过。

## 成功标准（产品体感）

- CS2 选手官方包开箱可进 pending。  
- 观战包在标注集上满足可计算优于标准。  
- 草稿试读/试跑与正式边界检测语义一致（在 C2 声明范围内）。  
- 无畏回归绿。

## 实施顺序（供计划拆解 — 仅审阅通过后）

0. 基线对齐  
1. C3 casefold  
2. C2 phase 直传  
3. C5 完整 validate  
4. CS2 phase/词/框 + `builtin:cs2_spectator`  
5. C1 模式 A/B/C + 试读 UI  
6. C4 试跑 + 双向 busy + 超时常量  
7. C6 checklist 失效  
8. 样例清单/标注/指标脚本 + 回归  

## 开放问题（已拍板）

| 问题 | 决定 |
|------|------|
| 是否做 4 步向导 | 否 |
| 观战是否单独官方包 | 是，`builtin:cs2_spectator` |
| 试跑是否入切片列表 | 否 |
| 试跑默认窗口 | 3 或 5 分钟可选，封顶建议 5 分钟 |
| 试用输入 | **三种互斥模式；删关键词子集** |
| 试跑超时 | **单次同步；客户端 > 服务端 deadline + 余量；关 UI 不取消后端** |
| 双向 busy | **是（分析↔试跑、重复试跑）** |
| 试跑 vs 正式状态机 | **不承诺节奏等同；只验包/词/边界语义** |
| 同帧 buy+result | **`matched_categories: string[]`** |
| `empty` 含义 | **仅 OCR 成功无字；失败走 success:false** |
| 观战「优于」 | **missed+merged+FP 更低，并报告 MAE** |
| 本轮 audio.* | **否** |
| 实施基线 | **`feat/fps-round-rule-pack`，先与 main 对齐** |
