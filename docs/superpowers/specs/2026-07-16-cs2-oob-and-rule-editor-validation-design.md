# CS2 开箱精调与规则编辑器验收增强设计

> **日期：** 2026-07-16  
> **状态：** 待用户审阅后进入实施计划  
> **方案：** A — 引擎优先（多官方预设 + 编辑器验收；不重做向导）  
> **前置：**
> - [2026-07-16-fps-round-rule-pack-design.md](./2026-07-16-fps-round-rule-pack-design.md)
> - [2026-07-14-valorant-phase-scheduler-continuous-analysis-design.md](./2026-07-14-valorant-phase-scheduler-continuous-analysis-design.md)
> - [2026-07-15-continuous-analysis-quality-first-design.md](./2026-07-15-continuous-analysis-quality-first-design.md)

## Goal

在无畏契约持续分析已达标的前提下：

1. 把 **CS2** 官方规则包拉到接近无畏的开箱体验（切回合、默认框/词、相位节奏）。
2. 在**不重做**「复制→编辑→保存→启动」主流程的前提下，补齐自定义规则的**引导 checklist** 与 **试读/试跑验收**，降低「不知道框哪里、改完不知道够不够好」的成本。

## 决策摘要（已确认）

| 项 | 选择 |
|----|------|
| 总体方案 | A：引擎优先；编辑器只补验收，不做 4 步向导 |
| CS2 开箱期望 | 常见 HUD 开箱即用；特殊皮肤/分辨率再手调 |
| HUD 覆盖 | 中英双语关键词 + 选手包 + 观战/赛事包 |
| 自定义痛点 | 步骤多、缺引导、缺验收（预览标定可接受） |
| 验收形态 | 单帧试读增强（绿/黄/红）+ 短片段试跑时间轴预览 |
| 无畏契约 | 行为与参数不改 |

## 非目标

- 重做成多页向导 / 大改持续分析 Modal 信息架构
- 自动探框、规则云市场、分析中热换包
- 改变确认门默认（仍要求 OCR 起止才可升格）
- 改变 pending → 精修 → 手动导出产品链路
- 改变「只分析主房录制」、预览与录制分离、导出队列
- 无畏 `builtin:valorant` 行为回归变更

## Architecture

```
builtin:cs2 / builtin:cs2_spectator / 用户包
        │
        ▼
┌──────────────────┐
│ fps_round 解释器  │  ← 唯一策略入口（已有）
└────────┬─────────┘
         │
    ┌────┴────┬────────────┐
    ▼         ▼            ▼
  OCR信号   音频信号    CS2 phase 参数
         │
         ├── 正式：continuous worker → pending clips
         └── 试跑：trial_scan_rounds → 仅预览，不入队
```

编辑器路径（骨架不变）：

```
选包 → 管理规则 → 复制/编辑
         │
         ├─ 顶部 checklist（框 → 试读绿 → 试跑有回合 → 保存）
         ├─ 单帧试读（命中色）
         └─ 短片段试跑（时间轴预览）
         │
         ▼
保存 →（启动前可再试跑）→ start_continuous_analysis
```

## CS2 引擎

### 官方包

| id | 用途 |
|----|------|
| `builtin:cs2` | 选手第一人称 HUD（主流 16:9） |
| `builtin:cs2_spectator` | 观战 / 赛事解说 HUD |

- 关键词：**中英双语**同一列表（买枪/Freeze/暂停/胜负等常见文案）。
- `combat` 可空：开始仍以「离开买枪」为主。
- `confirm`：与现网一致，`require_ocr_bounds=true`，`start_by=ocr_buy_exit`，`end_by=[ocr_result, next_buy]`。

### 相对当前 `builtin:cs2` 的缺口

当前包缺少 `phase`，会回退到无畏 profile（买枪 30s 等先验不适配 CS2）。本轮必须：

1. **写入 CS2 专用 `phase`**：买枪约 20s、更长半场/休息、回合上限与 lookback 等以真实样例标定（不得再静默回退 Valorant）。
2. **精调 `ocr_regions.status`**：选手顶部回合条 vs 观战顶部信息条分开标定。
3. **扩展 `keywords.buy` / `keywords.result`**：覆盖 Freeze Time、Buy Time、中英胜负/回合结束等变体。
4. **按需微调内置 `audio.*`**：不强求钟声对等无畏；OCR 边界仍为权威。
5. **`duration.min_sec` / `max_sec`**：按 CS2 回合经验区间用样例收紧（量级约 15–200s，最终以样例为准）。

### 样例与标定

- 至少各准备选手视角、观战/赛事视角样例（中英文 HUD 尽量各覆盖）。
- 参数写入官方 JSON 前，用同一解释器扫样例，人工确认漏切/粘连/边界可接受。
- 观战验收标准：选观战包明显好于误用选手包。

## 编辑器验收 UX

### 单帧试读增强

- 入口：编辑页已有「从这帧试读」，不另开页面。
- 展示：识别文字 + 状态色  
  - **绿**：命中 `buy` 或 `result` 关键词  
  - **黄**：有字但未命中  
  - **红**：空白 / OCR 失败  
- 协议：在现有 `trial_ocr_region` 响应上增加  
  - `matched_category`: `"buy" | "result" | "none"`  
  - `status`: `"hit" | "partial" | "empty"`

### 短片段试跑

- 入口：编辑页 + 持续分析启动前各一处。
- 默认扫描主房**最近 3 或 5 分钟**已录内容（可切换；封顶避免过慢，具体上限实施计划定，建议 ≤ 5 分钟）。
- 新 WebSocket：`trial_scan_rounds`  
  - 入参：`rule_pack_id`、房间 id、时间窗（或 `last_n_sec`）  
  - 行为：加载规则包 → 调 `fps_round` 解释器扫描录制文件区间 → 返回回合 `{start,end,...}` 列表  
  - **不**写入切片列表，**不**触发导出  
- UI：简易时间轴标出回合；点选可看起止秒。
- 0 回合：提示检查「选手 vs 观战预设 / 状态框 / 关键词」。
- 无足够录制：提示先录制或缩短窗口。

### 轻引导（非向导）

编辑页顶部短 checklist：

1. 标定状态区  
2. 试读为绿（或至少黄且用户知悉）  
3. 试跑检出 ≥1 回合  
4. 保存  

不强制阻断保存（弱引导）；**缺有效状态区仍禁止启动持续分析**（与规则包既有约束一致）。

## 与现有契约的关系

- 正式持续分析仍：`confirm_status=pending`、`export_deferred=true`，精修确认后再导出。
- `round_key` 继续含 `rule_pack_id`。
- 旧 `game=valorant` 映射 `builtin:valorant` 不变。
- 用户包仍只能「复制官方包 → 改可编辑字段」。

## Testing / 验收

1. **CS2 选手开箱**：默认 `builtin:cs2` 在样例上切出合理回合；漏切/粘连相对现状明显改善；边界目标误差 ≤ 1.0s。
2. **CS2 观战**：`builtin:cs2_spectator` 在观战样例上明显优于误用选手包。
3. **双语关键词**：中英常见买枪/结算文案能被默认词表命中（试读为绿）。
4. **试读**：返回 `status`/`matched_category`；UI 色态正确。
5. **试跑**：返回预览回合且切片列表不变；0 回合与无录制有明确提示。
6. **无畏回归**：`builtin:valorant` parity / continuous / confirm 相关测试通过。
7. **防呆**：无状态区用户包仍不能启动。

## 成功标准（产品体感）

- 选「CS2」官方包，常见选手 HUD **不改框也能**持续分析出可用回合（进 pending）。
- 观战局改选观战包后质量可接受。
- 自定义时：试读看色 + 试跑看轴，能判断规则是否够用，无需先正式跑完整场。
- 无畏用户无感。

## 实施顺序（供计划拆解）

1. CS2 `phase` + 双语关键词 + 选手框样例精调 → 更新 `builtin:cs2`
2. 新增 `builtin:cs2_spectator` 并挂到规则列表
3. `trial_ocr_region` 响应扩展 + 前端命中色
4. `trial_scan_rounds` 后端 + 编辑页/启动前时间轴 UI
5. 编辑页 checklist 文案
6. 样例回归 + 无畏回归测试

## 开放问题（已拍板）

| 问题 | 决定 |
|------|------|
| 是否做 4 步向导 | 否（方案 A） |
| 观战是否单独官方包 | 是，`builtin:cs2_spectator` |
| 试跑是否入切片列表 | 否 |
| 试跑默认窗口 | 3 或 5 分钟可选，封顶建议 5 分钟 |
| 用户能否关 OCR 确认门 | 否（沿用规则包 v1） |
