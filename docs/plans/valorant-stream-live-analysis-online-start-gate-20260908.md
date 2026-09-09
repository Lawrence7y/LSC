# Valorant 流上实时分析 + 在线入点门禁方案

> **版本**：v1.0 · 2026-09-08  
> **状态**：Step 1（在线入点门禁）代码已落地并通过回归；Step 2–4 待实施；真实强停验收待执行  
> **适用范围**：Valorant 官方赛事/解说 `broadcast` 持续分析、回放入点门禁、停止/强停定稿、吞吐与滞后优化  
> **关联文档**：
> - `docs/plans/valorant-broadcast-runtime-accuracy-fix-tasks-20260908.md`
> - `docs/plans/valorant-continuous-analysis-chain-optimization-20260908.md`

## 1. 背景与问题

### 1.1 本次强制停止验收结论（2026-09-08 23:03:32–23:25:38）

- 录制封装为 `23:03:32–23:25:38`，录制、分析、预览、Electron、Python、FFmpeg 均已停止。
- 新草稿共 6 条切片 + 6 条文字标记 + 1 条完整录像（13 个片段）。
- **6 条中有 5 条使用 `broadcast_exclusion` 截断**，出点侧改善明显。
- **入点侧仍未解决**：
  - 第 4 条 `425.1–497.1s`：入点后 3 秒仍是明显 `REPLAY`；
  - 第 5 条 `559.1–664.8s`：入点后 3 秒同样是 `REPLAY`；
  - 2/6 条把回放当成了新回合，比例没有比上一轮明显改善。
- 6 条全部仍为 `broadcast_review_required=true`；本次为了满足“立即导出”，用 `include_pending=true` 强制加入草稿。

### 1.2 已落地的修改（上一轮）与为何本次未生效

上一轮已在 `lsc/analyzer/valorant_broadcast.py` 实现**收尾/离线入点门禁**：

- 普通候选扫描开头 15s，replay/result/non_game 开头后移到连续 ≥2s 的真实 combat，找不到则拒绝；
- `split_from_oversize` 固定切块扫描整块重找真实 combat 锚点；
- 拒绝结论经 `start_gate_rejected` 持久化，跨重试不复活；
- 后移时保留 `start_delta=None`，`broadcast_review_required` 仍为 `true`。

**本次未生效的原因**：现有 `run_start_gate` 只在 `finalize=True` 或 `available_end is None` 时执行。本次是**立即强停**（未走收尾），在线审计阶段没执行入点门禁，所以回放入点问题依旧。

### 1.3 吞吐/滞后瓶颈

最新日志中一个约 53 秒、45 帧的窗口：

| 窗口 | 耗时 |
|---|---:|
| `340–393s` | 约 93 秒 |
| `565–618s` | 约 40 秒 |

多数窗口处理速度接近或慢于实时速度，滞后持续扩大；本次停止时只覆盖到 `888/1321s`，尾部约 433 秒未分析。

主要成本：

1. 对正在写入的录制文件反复 `seek + FFmpeg 解码`；
2. 每秒调用顶部计时器/比分 OCR；
3. 部分帧执行中央横幅 OCR；
4. 回放审计又会重新解码起点、尾部和 2fps 精修窗口。

## 2. 方案目标与结论摘要

### 2.1 目标

```text
实时性：端到端净覆盖速度稳定 ≥ 1.0x，滞后不持续增长，强停时无大段未分析尾部
入点准确性：replay/result/non_game 开头的候选不再作为新回合出现
定稿：停止时用流上已积累的观测结果定稿，只补扫缺口/低置信边界
```

### 2.2 方向结论（评审确认）

推荐架构（与整条链路优化计划一致）：

```text
直播流一次解码
  → 视觉模型持续判断相位（五分类，1fps 甚至 0.5s）
  → Replay 专用低成本检测（固定 ROI/模板/小分类器）
  → 状态变化时才调用 OCR（计时器 / 比分 / 准备横幅）
  → 实时生成候选并执行在线 start gate
  → 录制文件仅补扫缺口 / 低置信边界
```

不直接“对直播流做原样 OCR”：那样只降低延迟，不会减少 OCR 调用次数，吞吐瓶颈仍在。

### 2.3 合并语义（评审确认）

对于第 4、5 条这类“回放开头”的候选：

> **删除该候选（拒绝），前一回合保持不变**，不把回放尾段并入前一回合切片，也不后移生成“下一条真回合”切片。

理由：本次验收里“好切片”的出点位于回合结束至选手镜头/结算画面的交界，把回放尾段并入切片会违背该标准；后移生成新切片则可能与后续候选重复。

## 3. 分步实施

按“先小后大、先解决准确度后解决吞吐”的顺序推进。每步独立可验收、可回滚。

### Step 1：把入点门禁前移到在线审计（P0，✅ 已实现）

- **改动文件**：`lsc/analyzer/valorant_broadcast.py`
- **目标**：候选形成后（`available_end` 已存在、尚未收尾）立即执行入点门禁，使强停/实时阶段也能拦截“回放开头”候选。
- **已落地**：
  1. 放宽 `run_start_gate` 条件：`finalize or available_end is None` → **始终执行**（在线、收尾、离线统一）。
  2. 在线阶段扫描开头 `START_GATE_SCAN_LIMIT_SEC=15s`；`split_from_oversize` 仍整块扫描。
  3. 判定复用 `_stable_combat_run_start` / `_start_gate_decision`：
     - 前几帧是 replay/result/non_game → 后移到连续 ≥2s 的真实 combat；
     - 找不到稳定 combat → `rejected_no_stable_combat_start`，按第 2.3 节语义删除（拒绝），前一回合保持不变。
  4. 在线拒绝写入 `start_gate_rejected` 缓存，收尾/重试不复活。
  5. 在线后移结论（`start_gate_moved_to`）写入缓存，`pending_lookahead` 重试时复用后移后的起点，不退回原始错误起点。
  6. 在线安全边界：头部 15s 尚未被当前录制覆盖时跳过门禁、返回 `pending_lookahead` 等待重试，避免拿截断头部误拒。
  7. **头部样本与尾部审计隔离**：起点门禁的头部样本单独存放于 `start_gate_samples`，不再写入尾部审计的 `samples/scanned_end`。否则在线首轮会因缓存“已扫过头部”而从头部开始连续扫完整回合，暴露回合中段 replay/result 转场，导致出点被过早截断、回合不完整。
  8. 保留 `start_delta=None`，不伪造精修证据；`broadcast_review_required` 语义不变。
- **回归测试**：新增在线入点门禁 4 项（后移 / 拒绝 / 头部未写满时延迟 / pending 重试复用后移起点）及 split 整块扫描，全部通过。
- **验收（待真实强停）**：
  - 强停（不触发收尾）后草稿中不再出现“入点后 3 秒仍是 REPLAY”的切片；
  - 回放开头候选被删除，前一回合切片保持原有出点；
  - 在线滞后不因新增 15s 头部扫描而明显恶化（对比本轮基线）。

### Step 2：Replay 专用低成本检测（P1）

- **改动文件**：新增 `lsc/analyzer/replay_detector.py` 或并入 `valorant_broadcast.py`
- **目标**：右下角 `REPLAY` 标识非常稳定，用固定 ROI 模板匹配 / 边缘颜色特征 / 小型二分类模型，比通用 OCR 快一个量级；连续两帧命中即判定回放。
- **用途**：
  - 直接拦截“候选开头就是 REPLAY”；
  - 替代/强化入点门禁与尾部审计中对 replay 的视觉判定。
- **注意**：`replay` 仍只允许 `broadcast` 分支，POV 路径不引入。

### Step 3：流上观测 sidecar（P1）

- **改动文件**：`lsc/analyzer/` 新增观测写入，`python-backend/handlers/room_handler.py`、`python-backend/continuous_finalization.py` 消费
- **目标**：持续写入轻量 sidecar，停止时用已积累观测定稿，只补扫缺口。

```text
timestamp
visual_phase
phase_confidence
timer
score
replay_logo
stream_epoch
```

- **收益**：
  - 不再为入点门禁、尾部审计、2fps 精修分别重新解码；
  - 强停时尾部（如本次 433s）不再整段丢失；
  - 在线 start gate 能直接消费流上观测，无需额外抽帧。
- **注意**：sidecar 必须幂等、可增量合并；录制文件改名时保留观测（复用现有“改名保留 OCR/审计状态”机制）。

### Step 4：流上分析订阅者（P2，工作量最大）

- **改动文件**：`lsc/core/services/shared_ingest.py`（`SharedRoomIngest`）、`lsc/core/services/ingest_supervisor.py`、`lsc/core/services/recording_service.py`
- **目标**：在共享直播流的 MPEG-TS 数据上增加**分析订阅者**，与录制 sink、预览 sink 并行。

```text
共享直播流
   ├─ 录制 sink
   ├─ 预览 sink
   └─ 分析 sink → 低分辨率 1fps 帧
```

- **注意**：
  - 分析队列必须**有界、丢旧保新**，绝不能让 OCR 慢反向阻塞录制；
  - 不新拉一路直播流，复用共享解码；
  - 与 Step 3 的 sidecar 衔接：分析订阅者产生观测，录制文件只做补漏。
- **验收**：端到端净覆盖速度 ≥1.0x，强停尾部缺口显著缩小。

## 4. 涉及文件汇总

| 阶段 | 文件 | 作用 |
|---|---|---|
| Step 1 | `lsc/analyzer/valorant_broadcast.py` | 在线入点门禁、拒绝缓存 |
| Step 1 | `tests/test_valorant_broadcast.py` | 在线入点门禁回归测试 |
| Step 2 | `lsc/analyzer/replay_detector.py`（新增） | Replay 标识专用检测 |
| Step 3 | `lsc/analyzer/`（观测写入） | sidecar 观测产生 |
| Step 3 | `python-backend/handlers/room_handler.py` | 观测消费、候选合并、强停定稿 |
| Step 3 | `python-backend/continuous_finalization.py` | 收尾时用观测定稿 + 补扫缺口 |
| Step 4 | `lsc/core/services/shared_ingest.py` | 分析订阅者接入共享流 |

## 5. 风险与边界

1. **在线入点门禁增加抽帧**：15s 头部扫描约 15 帧，需验证不会显著扩大滞后；若尾部审计已缓存头部样本，应复用不重复解码。
2. **删除语义**：回放开头候选删除后，前一回合保持不变；若未来需要“保留击杀回放”，可另开开关，不默认合并进切片。
3. **Replay 只限 broadcast**：POV 分支不得引入 replay 语义。
4. **sidecar 一致性**：观测写入必须与录制时间戳/epoch 对齐，改名、重启、断流恢复后不产生错位。
5. **不降低证据门禁**：所有后移/拒绝都保留 `broadcast_review_required` 语义，自动导出资格不变。

## 6. 验收清单（最终）

- [ ] 强停（不触发收尾）后草稿不再出现“入点后 3 秒仍是 REPLAY”的切片（待真实强停验收）；
- [ ] 回放开头候选被删除，前一回合切片保持原有出点（待真实强停验收）；
- [x] 在线审计阶段即可看到 `broadcast_start_gate` 字段（moved / ok / no_stable_combat）（代码与单测已覆盖）；
- [ ] 端到端净覆盖速度稳定 ≥1.0x，强停尾部缺口显著缩小；
- [x] 拒绝/后移不降低 `broadcast_review_required` 与自动导出门禁（保留 `start_delta=None`，单测覆盖）；
- [x] POV 路径完全不受影响（门禁仅 `source_profile=broadcast` 审计内生效）。
