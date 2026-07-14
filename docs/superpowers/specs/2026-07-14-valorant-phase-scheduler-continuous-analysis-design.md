# 无畏契约相位调度持续分析设计

> **日期：** 2026-07-14  
> **状态：** 待用户审阅后进入实施计划  
> **前置：** [2026-07-12-valorant-round-continuous-analysis-design.md](./2026-07-12-valorant-round-continuous-analysis-design.md)

## Goal

用无畏契约回合的时间结构先验，把持续分析从「固定回看窗均匀扫描」改为「相位状态机调度」：在转场窗口加密检测以保住边界准确度，在买枪/交战中段休眠以降低 OCR 与扫描占用；停录后再全量精修补漏。

## 决策摘要（已确认）

| 项 | 选择 |
|----|------|
| 准确 vs 召回 | 持续阶段优先边界；停录精修补召回 |
| 入列时机 | 等起点+终点都可信再自动入列导出（可等 10–30s） |
| 内容类型 | 游戏视角与赛事解说都要；规则可分两套 |
| Profile 切换 | 先手动二选一；自动判别不做（后续） |
| 方案 | 相位状态机调度（方案 1） |

## 非目标

- 不引入新模型或训练流程
- 不改 MSE / 录制 / 导出队列并发架构
- 不放宽「纯音频不得自动导出」
- 不做自动 profile 判别
- 不改变非无畏契约模式的持续分析行为

## Architecture

在现有无畏契约持续分析之上增加 **相位调度器（Phase Scheduler）**。不替换 `round_detector` 的 RMS / 钟声 / OCR 信号能力，只决定 **何时、对哪段、用多重算力** 调用它们。

```
录制增长
   │
   ▼
[相位调度器]  ← valorant_profile: pov | broadcast
   │
   ├─ 便宜常开：短窗 RMS 包络 + 低频钟声
   ├─ 贵信号按需：OCR 仅在转场窗加密
   └─ 确认门：start+end 都可信 → 去重 → 同步映射 → 入列导出
   │
   ▼
停录 → finalize 宽窗/OCR 精修（补漏 + 可选收紧边界）
```

检测与确认仍复用：

- `lsc/analyzer/round_detector.py`
- `python-backend/handlers/room_handler.py` 中 `_continuous_valorant_worker` 及确认/导出路径
- 现有多房间 sync 映射与导出队列

## 相位状态机

### 状态

| 状态 | 含义 | 主信号 | OCR |
|------|------|--------|-----|
| `unknown` | 刚启动或丢相位 | 短窗 RMS + 稀疏 OCR 找锚点 | 稀疏 |
| `buy` | 买枪/准备 | RMS 是否仍安静；时钟休眠 | 极稀或关 |
| `pre_combat` | 预计屏障解除窗 | RMS 抬升 + OCR「购买阶段」消失 | **加密** |
| `combat` | 交战中 | RMS 维持；听钟声 | 默认关 |
| `post_combat` | 收尾窗 | 钟声/能量塌陷触发；OCR 结算或下一买枪 | **加密** |
| `confirmed` | 瞬态：本回合刚闭合 | 写入确认集、入列导出后立即离开 | 关 |

`confirmed` **不是可驻留状态**：处理完入列后同一 tick 转入 `buy`（若已看到下一买枪）或 `unknown`（否则）。调度器持久相位只有 `unknown|buy|pre_combat|combat|post_combat`。

### 转移

```
unknown ──找锚点──► buy
buy ──到预计解除时刻 /（pov 下）能量抬升──► pre_combat
pre_combat ──锁到可信起点──► combat
combat ──钟声或能量塌陷──► post_combat
post_combat ──锁到可信终点──► [入列] ──► buy 或 unknown
任意状态超时无进展 ──► unknown（强制短窗重锚）
```

`rms_trust` 语义（消除歧义）：

- `pov`（高）：允许「能量抬升」单独把 `buy` 推进到 `pre_combat`；进入可导出确认仍须 OCR 可信起终点。
- `broadcast`（低）：禁止仅凭能量进入 `pre_combat`；须 OCR 显示离开买枪（或买枪字消失）才进入 `pre_combat`/`combat` 路径。

防抖：可驻留状态最短 3–5 秒，避免买枪↔交战抖动。

### 确认门（硬约束）

- 自动入列/导出 **仅**接受同时具备可信起点与可信终点的回合。
- 可信终点：OCR 结算（获胜/败北等）**或** 下一回合买枪出现（与既有 `ocr_confirmed` / `end_by in {ocr_result, next_buy}` 一致）。
- 钟声与能量塌陷 **只用于** 唤醒 `post_combat` 与校验，**不得单独**使片段具备自动导出资格。
- 开放尾（仅有起点）保持「等待回合结束」；录制停止时仍未闭合的回合交由 finalize，不在持续阶段强行导出。

## Profile 参数

启动持续分析时手动选择（默认 `pov`）：

- `valorant_profile = "pov"` — 游戏视角
- `valorant_profile = "broadcast"` — 赛事解说

两套规则共用同一状态机，仅参数与信号权重不同。

| 参数 | `pov` | `broadcast` | 作用 |
|------|-------|-------------|------|
| `buy_sleep_sec` | 22 | 12 | 进入买枪后主动休眠 |
| `pre_combat_window_sec` | 12 | 18 | 屏障解除前加密窗 |
| `combat_ocr` | 关 | 关（可选每 20s 探活） | 交战中段不识字 |
| `post_combat_window_sec` | 25 | 35 | 收尾加密窗（解说尾更长） |
| `rms_trust` | 高 | 低 | 能量是否可驱动转场 |
| `ocr_sparse_interval_sec` | 10–15 | 6–8 | 非转场探活 OCR |
| `ocr_dense_interval_sec` | 1.5–2 | 1–1.5 | 转场窗加密 |
| `chime_wakes_post_combat` | 是 | 是 | 钟声强制进入收尾 |
| `audio_alone_export` | 否 | 否 | 纯音频不得自动导出 |
| `unknown_reanchor_sec` | 45 | 30 | 无进展则强制重锚 |
| `max_combat_force_post_sec` | 130 | 130 | 交战过长强制开收尾 OCR |

相对关系锁定（实现可微调绝对数字）：

- `broadcast` 更不信 RMS、更早结束买枪休眠、收尾窗更长、稀疏 OCR 更密。
- `pov` 更敢休眠、更信能量抬升进入 `pre_combat`。

## 扫描预算（相对现网的变化）

现网：增量扫描常带约 240s 回看，OCR 采样约 2s 间隔较均匀。

本设计：

- **扫描窗** = 当前相位所需短窗 + 少量 overlap（例如 `post_combat_window`），而不是固定长窗密扫。
- **`unknown` / 超时重锚** 时才临时加宽扫描窗。
- OCR 帧率按相位在 sparse / dense / off 之间切换。
- 资源压力升高时：先拉长 tick → 再关稀疏 OCR → 最后只保留转场窗 OCR；**绝不**放开纯音频自动导出。

## 持续 Worker 数据流

在 `_continuous_valorant_worker` 循环内：

1. 读录制增长与资源压力 → `effective_interval`（可跳过本轮）。
2. 读 `phase`、`valorant_profile`、pending 起点。
3. 按相位决定本轮预算：`scan_range`、`need_audio`、`need_ocr`、`ocr_interval`。
4. 调用 `round_detector`（或轻量 RMS/chime 前探）获取候选标记。
5. 状态机转移；若出现双可信回合 → 去重 → 同步映射 → 入列导出。
6. 广播 `continuous_analysis_status`（含 phase、profile、pending、下一唤醒原因）。

任务内存状态（不强制新持久化文件）：

- `valorant_profile`
- `phase`、`phase_entered_at`
- `pending_start`（已锁起点、未锁终点）
- `last_chime_at`、`last_buy_ocr_at`
- `confirmed_round_keys`

多房间：只在主房间跑调度与检测；确认后用现有 sync 映射为每个已选已同步房间生成切片。调度成本不随房间数线性放大。

## 前端

1. 无畏契约持续分析启动处增加 Profile 二选一（`游戏视角` / `赛事解说`），随启动消息下发；缺省 `pov`。可选：记住上次选择。
2. `AnalysisProgress`（或等价状态条）展示：`phase` 中文、`valorant_profile`、是否有未闭合回合；可选展示下一动作提示（如「买枪休眠中，约 Ns 后侦测屏障」）。
3. 不新开页面。

### WebSocket 增量字段

`continuous_analysis_status` 增加可选字段（旧客户端忽略即可）：

- `valorant_profile`: `"pov" | "broadcast"`
- `phase`: 上表状态名
- `phase_detail`: 短中文
- `pending_round`: bool

启动消息增加：

- `valorant_profile`（缺省 `"pov"`）

## Finalize（停录精修）

| 职责 | 行为 |
|------|------|
| 补漏 | 宽窗/OCR 精修主房间；与持续确认集按时间窗去重后，漏检回合补入列 |
| 收紧 | 仅对**尚未导出成功**的切片，允许用精修结果更新起终点后继续导出 |
| 已导出成功项 | 不删除、不改磁盘上已写出的文件、不自动重导；精修若发现边界更优只记日志 |
| 精修失败 | 保留持续阶段已确认结果；状态报失败原因；不回滚已导出 |

持续取消：丢弃未确认 pending，不导出开放尾。

## 错误处理

| 情况 | 行为 |
|------|------|
| OCR 暂不可用 | 保持相位；不纯音频导出；`broadcast` 可延长 `post_combat`；超时 → `unknown` |
| 音频不可用 | OCR 双可信仍可确认；状态标明音频校验缺失 |
| 买枪休眠过头 | sleep 到期必醒；过预计解除仍无起点 → `unknown` 加密重锚 |
| 交战过长无终点 | 达 `max_combat_force_post_sec` → 强制 `post_combat` OCR；仍无则未确认不导出 |
| 相位抖动 | 最短驻留 + 去重键合并 |
| 同步/文件无效 | 该房间导出失败，其他房间继续 |

## 验收标准

1. `pov`：买枪休眠期间 OCR 调用显著少于现网均匀 ~2s 扫描；屏障解除附近加密能锁起点。
2. `broadcast`：解说抬高 RMS 时，不以能量单独进入可导出确认；转场依赖 OCR。
3. 仅双可信入列；开放尾等待；钟声单独不触发自动导出。
4. 确认后可在合理延迟内闭合并入列；重复扫描不重复入列。
5. 多房间：主房确认 → 各已选已同步房间各一条切片与导出任务。
6. 停录精修能补上持续漏检回合；已成功导出项不被破坏性覆盖。
7. 非无畏契约模式行为不变；缺省 profile 为 `pov`。
8. 状态事件含 `phase` / `valorant_profile`，前端可展示。

## 主要改动面（实施时）

- `python-backend/handlers/room_handler.py`：相位调度接入 continuous worker、扫描预算、状态广播、启动参数。
- `lsc/analyzer/round_detector.py`：按需暴露/接受更细的采样间隔与短窗扫描（避免为调度器复制一套检测）。
- 前端：Workbench 启动选项、`AnalysisProgress`、types、websocket 启动字段。
- 测试：`tests/test_continuous_analysis_guards.py`、`tests/test_synced_continuous_analysis.py`、`tests/test_round_detector.py` 及必要的相位调度单测。

## 回合先验（设计依据，非运行时配置）

标准回合阶段顺序几乎固定：买枪(~30s) → 屏障解除 → 交战(20–100s+) → 结算钟声/结算字 → 尾部垃圾(10–30s) → 下一买枪。真正需要密算的只有起、终点两个转场；中段假安静（架枪/转点）不值得密集 OCR。本设计用该先验做调度，而不是再堆更重的识别模型。
