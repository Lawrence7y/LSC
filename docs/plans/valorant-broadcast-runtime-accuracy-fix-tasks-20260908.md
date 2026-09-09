# Valorant 官方解说持续分析运行准确度修复任务

> **状态**：实施中（运行时交付/收尾恢复/发布门禁代码已落地；真实一小时吞吐与独立人工测试集仍待执行）  
> **优先级**：P0/P1 生产缺陷  
> **创建日期**：2026-09-08  
> **适用范围**：官方赛事/官方解说 `broadcast` 持续分析、视觉审计、收尾恢复、模型发布门禁  
> **关联计划**：
> - `docs/plans/valorant-continuous-analysis-chain-optimization-20260908.md`
> - `docs/plans/valorant-stream-live-analysis-online-start-gate-20260908.md`

## 1. 任务目标

解决“模型阶段准确度较高，但实际持续分析结果准确度明显下降”的问题，使以下四个口径保持一致：

1. 模型离线评估结果；
2. 生产运行时实际推理方式；
3. 视觉审计已经接受的候选；
4. 用户最终在切片列表和收尾结果中看到的候选。

本任务完成后，必须保证：

- 已通过视觉审计的结果不会因同批后续候选超时而丢失；
- 应用关闭、录制停止或分析停止后，未完成收尾可以自动恢复；
- OCR 粗扫与视觉审计的调度不会令分析滞后持续发散；
- 模型只有通过与生产完全一致的评估门禁后才能替换生产模型；
- 测试验证真实行为，不再仅通过搜索源码关键字判断功能是否存在。

## 2. 已确认事实与故障结论

### 2.1 生产运行结果没有完整承接模型审计结果

2026-09-08 13:09–13:38 的真实运行中：

| 指标 | 实际值 |
|---|---:|
| 已录制时长 | 1753.750 秒 |
| 已分析时长 | 1376.437 秒 |
| 分析滞后 | 377.313 秒 |
| `audit_accepted_count` | 6 |
| `confirmed_rounds` | 1 |
| `pending_rounds` | 8 |

日志显示多个候选已得到 `audit=passed, status=vision_confirmed`，但审计批达到 120 秒预算后进入异常路径，最终列表只保留一个确认回合。

当前实现的问题是：

1. 线程内先把终态候选加入局部 `produced_audited`；
2. 只有整个审计循环正常返回时，才把它复制到 `_produced_ref`；
3. 若后续候选发生超时，异常路径读取到的 `_produced_ref` 仍为空；
4. 已接受候选已从 pending 队列消费，但又没有发布到主循环，形成不可恢复的数据丢失。

### 2.2 退出流程只保存 pending 检查点，随后终止后端

最近一次运行在 13:52:47 保存收尾检查点，13:52:48 即终止后端。检查点状态为：

```text
phase = pending
scan_cursor = 102.453
final_duration = 186.078
pending_candidates = 1
final_round_count = 0
```

后端已有 `resume_continuous_finalization`，`AnalysisProgress` 组件也声明了 `onResumeFinalization`，但 Workbench 没有传入该回调，因此用户无法从 UI 恢复检查点，应用重启时也不会自动恢复。

### 2.3 粗扫与审计名义解耦，实际仍串行

当前存在 `_analysis_semaphore` 和 `_refine_semaphore` 两个 asyncio semaphore，但粗扫和审计最终都持有同一个 `_analysis_thread_semaphore`。因此一个 30–120 秒的审计任务仍可阻塞下一轮粗扫。

当 backlog 超过阈值后，程序会暂停视觉审计并优先粗扫。结果是模型虽然单独推理很快，生产运行中却有大量候选长期停留在 `pending_lookahead`，用户看到的主要是 OCR 粗边界，而不是模型定稿结果。

### 2.4 模型发布评估与生产推理口径不一致

当前评估脚本使用 `predict_batch()`，生产 `broadcast` 审计使用 `predict_broadcast_batch()`，后者会执行：

```text
0.70 × full_frame + 0.30 × top_HUD
```

使用当前部署 ONNX 实测：

| 数据集 | 普通整帧推理 Macro F1 | 生产融合推理 Macro F1 |
|---|---:|---:|
| 现有 val，778 帧 | 0.902953 | 0.908405 |
| 现有 test，32 帧 | 0.691017 | 0.634116 |

现有测试集仅 32 帧且没有 Replay 样本。仓库中的现有评估报告均为 `gates_passed: false`，不满足既定发布门槛，但当前默认模型元数据已经指向新权重和融合配置。

### 2.5 现有测试产生了假阳性

`test_refine_partial_results_survive_abort_and_preempt` 仅检查源码中是否包含 `_produced_ref` 和 `_publish_refine_results` 等字符串，没有执行“第一候选完成、第二候选超时”的真实控制流，因此测试通过但缺陷仍然存在。

## 3. 修复原则

1. **先修数据交付，再调模型**：模型正确结果不能可靠到达 UI 时，继续提升模型指标没有生产价值。
2. **先持久化，再消费 pending**：任何终态候选必须在从待审计队列删除前进入可靠结果通道。
3. **至少一次交付、幂等合并**：允许结果重复投递，但不允许丢失；以 `room_id + recording_id + round_key` 去重。
4. **收尾是正式业务阶段**：保存 checkpoint 不等于收尾成功，UI 不得把两者混淆。
5. **生产等价评估**：抽帧、裁剪、融合、类别阈值、时序稳定和边界门禁必须与生产一致。
6. **证据不足保持 pending**：禁止为了提高确认数量降低审计证据要求。

## 4. 任务拆分

### T1：修复视觉审计部分结果丢失（P0）

#### 涉及文件

- `python-backend/handlers/room_handler.py`
- `lsc/analyzer/valorant_broadcast.py`
- `tests/test_continuous_analysis_guards.py`
- 建议新增 `tests/test_continuous_refine_delivery.py`

#### 实施要求

1. 不再使用“审计函数全部返回后一次性复制”的 `_produced_ref` 作为异常恢复依据。
2. 增加线程安全的精修结果通道，例如：
   - 每任务 `refine_result_queue`；或
   - 受 `_analysis_jobs_lock` 保护的 deque。
3. 每个 `accepted` 结果在消费 pending 前完成以下顺序：

```text
生成终态结果
  → 写入可靠结果队列
  → 更新持久化 checkpoint
  → 从 broadcast_pending_rounds 删除
  → 唤醒主循环消费
```

4. `pending_lookahead` 不得作为终态从 pending 队列删除。
5. `pending_no_exclusion` 若仍需人工复核，应明确进入“已审计但待人工确认”的终态，避免与“尚未审计”混用同一状态。
6. `audit_accepted_count` 只能在结果已进入可靠队列后增加。
7. 主循环使用 `room_id + recording_id + round_key` 幂等合并；重复事件不得重复创建切片。
8. 精修发布不得覆盖更晚的粗扫结果容器，也不得回退 `last_analyzed`。

#### 必须新增的行为测试

1. 第一候选 `vision_confirmed`，第二候选抛 `TimeoutError`：第一候选发布一次，第二候选保留待重试。
2. 第一候选完成后触发 `refine_abort`：第一候选仍发布。
3. 发布事件重复两次：列表只有一个相同 `round_key`。
4. 线程在硬超时内未退出：已可靠入队结果仍可由主循环消费。
5. `audit_accepted_count` 与实际已发布/待消费终态数量一致。

#### 验收标准

- 任意审计批在任意候选处超时，之前已完成结果丢失数为 0；
- pending 队列、accepted 计数、主循环结果和前端列表数量可对账；
- 删除原有仅检查源码字符串的假阳性测试，或将其降级为辅助结构检查。

### T2：修复应用退出与收尾恢复闭环（P0）

#### 涉及文件

- `lsc-electron/src/utils/shutdownCleanup.ts`
- `lsc-electron/src/hooks/useWebSocket.ts`
- `lsc-electron/src/pages/Workbench/index.tsx`
- `lsc-electron/src/components/AnalysisProgress.tsx`
- `python-backend/handlers/analysis_handlers.py`
- `python-backend/continuous_finalization.py`
- `lsc-electron/src/utils/shutdownCleanup.test.ts`

#### 实施要求

1. 明确区分三个状态：

```text
checkpoint_saved：仅已保存，可恢复
finalizing：后端仍在执行收尾
completed：覆盖与审计全部完成
```

2. 退出时采用“有界等待 + 自动恢复”策略：
   - 在退出总预算内等待短收尾任务完成；
   - 超出预算则确认最终路径 checkpoint 已原子落盘后退出；
   - 下次启动自动发现 `phase=pending/error` 的 sidecar 并恢复，或明确提示用户恢复。
3. 给 Workbench 实现并传入 `onResumeFinalization`，实际发送 `resume_continuous_finalization`。
4. 应用启动后查询当前房间对应的可恢复 sidecar；不能只在当前内存任务进入 `phase=error` 时显示按钮。
5. 恢复前验证：源录像存在、`recording_id` 一致、文件时长未回退、模型契约可用。
6. checkpoint 必须绑定收尾后的最终录像路径；路径改名后立即更新并原子写入。
7. `cleanup-all-rooms-complete.success=true` 只能表示资源与 checkpoint 安全，不得暗示分析已经完成；返回值需携带明确的 `finalization_state`。

#### 必须新增的行为测试

1. 退出时收尾在预算内完成：后端完成后再退出。
2. 退出时收尾超预算：checkpoint 为 pending，重启后自动恢复。
3. 恢复按钮从 Workbench 可达并发出正确 WS 请求。
4. sidecar 指向旧“录制中”文件名：录制封尾改名后恢复到最终路径。
5. 重复恢复同一 job：幂等返回现有任务，不创建第二个 worker。
6. 源文件缺失或 recording epoch 不匹配：给出明确错误，不静默丢弃。

#### 验收标准

- 关闭应用后存在 pending checkpoint 时，下次启动用户一定能看到并恢复；
- 正常关闭造成的尾部候选永久丢失数为 0；
- `phase=pending` 不得被展示成“已完成”。

### T3：重构粗扫与视觉审计调度（P1）

#### 涉及文件

- `python-backend/handlers/room_handler.py`
- `lsc/analyzer/valorant_plugin.py`
- `lsc/analyzer/valorant_broadcast.py`
- `scripts/audit_continuous_analysis.py`
- `tests/test_continuous_analysis_guards.py`

#### 实施要求

1. 不允许粗扫和审计用两个异步 semaphore，却在底层继续长时间持有同一个线程 semaphore。
2. 将资源拆成至少两类：
   - FFmpeg 抽帧/解码资源；
   - OCR/ONNX 推理资源。
3. 若 DirectML 不支持安全并行，不强行双推理；改为可抢占的小批次时间片调度，而不是单任务持锁 120 秒。
4. 单个审计候选采用有界步骤：抽帧、推理、结果落盘后释放资源，再处理下一候选。
5. backlog 较大时采用加权公平策略，例如连续 N 个粗扫窗口后至少处理一个已具备后视窗口的审计候选，避免审计永久饥饿。
6. `max_audit_quota=2` 只能作为临时保护，不能替代可度量的时间预算。
7. 更新 `scripts/audit_continuous_analysis.py`，兼容当前包含 `reason/backlog/new_media/throughput_avg` 的 kick 日志格式；当前脚本对真实日志返回 0 个扫描窗口。

#### 性能验收

使用至少 1 小时官方解说录像或等长直播回放：

| 指标 | 门槛 |
|---|---:|
| 连续 5 个窗口净覆盖速度 | ≥ 1.0x |
| 稳态分析滞后 P90 | ≤ 60 秒 |
| 最大持续增长时长 | ≤ 5 分钟 |
| 单候选审计 P90 | ≤ 40 秒 |
| 审计队列增长 | 有界且可回落 |
| 已接受结果丢失 | 0 |

性能门槛按 CPU、DirectML、CUDA 分别记录，不得用模型裸推理 FPS代替端到端吞吐。

### T4：统一离线评估与生产推理口径（P1）

#### 涉及文件

- `scripts/valorant_vision/eval_source_dataset.py`
- `scripts/valorant_vision/eval_gates.py`
- `lsc/analyzer/valorant_frame_classifier.py`
- `lsc/analyzer/models/valorant_phase_v1.json`
- `scripts/valorant_vision/manifest_schema.md`

#### 实施要求

1. 评估脚本增加明确模式：
   - `plain_frame`；
   - `broadcast_runtime`。
2. `broadcast_runtime` 必须复用生产代码的：
   - 全帧/顶部 HUD 融合；
   - 每类别阈值；
   - unknown 判定；
   - 邻帧稳定器；
   - 审计阶段规则。
3. 报告必须同时输出帧级指标与回合级指标：
   - Macro F1；
   - Replay Recall；
   - Non-game Recall；
   - Buy/Result Precision；
   - 回合召回率；
   - 列表精确率；
   - 起点/终点误差 P95；
   - `vision_confirmed` 错误率。
4. 建立真正独立的测试集：
   - 至少 3 个未参与训练和阈值选择的完整赛事会话；
   - 必须包含 Replay、Result、Buy、Non-game 和 Combat；
   - 禁止用同一模型粗标后直接作为真值；
   - 人工确认记录必须可追踪。
5. 模型激活必须由机器可读的 promotion report 控制；任一门禁失败不得覆盖默认模型。
6. 元数据增加：评估模式、评估数据摘要、门禁结果、promotion report 路径和回滚模型 SHA。
7. 更新现有计划中“生产模型是否替换”的状态，避免文档和默认模型 SHA 冲突。

#### 发布门禁

| 指标 | 最低要求 |
|---|---:|
| 五分类 Macro F1 | ≥ 0.9400 |
| Replay Recall | ≥ 0.9500 |
| Non-game Recall | ≥ 0.9500 |
| Buy Precision | ≥ 0.9700 |
| Result Precision | ≥ 0.9700 |
| 回合召回率 | ≥ 0.9000 |
| 列表精确率 | ≥ 0.9700 |
| `vision_confirmed` 边界误差 P95 | ≤ 0.8 秒 |
| `vision_confirmed` 最大边界误差 | ≤ 2.0 秒 |

#### 验收标准

- 同一批帧在评估工具和生产审计入口得到完全一致的概率、标签和阈值结果；
- `gates_passed=false` 的模型无法通过激活脚本覆盖生产目录；
- 测试集各类别 support 非零，并在报告中显示来源会话数量。

### T5：修复边界质量与状态语义对账（P1）

#### 涉及文件

- `python-backend/continuous_finalization.py`
- `python-backend/handlers/room_handler.py`
- `lsc/analyzer/valorant_broadcast.py`
- `lsc-electron/src/types/index.ts`
- `lsc-electron/src/components/AnalysisProgress.tsx`

#### 实施要求

1. 明确以下状态的唯一含义：

| 状态 | 含义 |
|---|---|
| `pending_lookahead` | 尚未取得足够后视窗口，必须重试 |
| `pending_no_exclusion` | 已审计但无可靠排除证据，需人工确认 |
| `passed + vision_confirmed` | 审计通过且具备终态证据 |
| `precise` | 双边界证据满足自动导出门禁 |
| `invalid` | 时间戳、置信度或证据字段非法 |

2. `audit=passed`、`confirm_status=vision_confirmed`、`boundary_quality=invalid` 的组合必须给出具体机器可读原因，不能只返回“边界证据或时间误差异常”。
3. `boundary_quality` 的计算不得意外修改 `confirm_status`；确认状态和自动导出资格分开表达。
4. 列表快照需保留诊断需要的 `start_delta/end_delta/start_by/end_by`，或增加结构化 `boundary_quality_reason_code`。
5. 状态计数必须从权威候选集合计算，不能把同一 pending 同时计入 `pending_rounds` 和审计队列后产生歧义。
6. 收尾/离线审计增加**入点门禁**（start gating）：候选开头连续 replay/non_game/result 时必须把入点后移到首个稳定 combat 或拒绝；`split_from_oversize` 固定切块的块头不得盲用作入点，需在块内重新寻找真实交战锚点。

#### 必须新增的测试

覆盖 `next_prep`、`broadcast_exclusion`、大幅结构性截断、缺失 start/end 证据、负 delta、低置信度、重复 upsert，以及入点门禁（后移/拒绝/固定切块块头）。

#### 验收标准

- 任一不可自动导出的候选都能从状态字段直接判断原因；
- 日志、状态接口、切片列表对同一 `round_key` 的状态一致。

### T6：补齐生产级回归测试与可观测性（P1）

#### 涉及文件

- `tests/`
- `lsc-electron/src/**/*.test.ts(x)`
- `scripts/audit_continuous_analysis.py`
- `python-backend/handlers/room_handler.py`

#### 实施要求

1. 增加可控 fake clock、fake audit worker 和 fake finalization worker。
2. 行为测试必须实际运行任务状态迁移，不允许只搜索源码字符串。
3. 每个候选输出统一结构化日志：

```text
room_id
recording_id
round_key
candidate_state_before
audit_outcome
delivery_state
listed_state_after
elapsed_sec
```

4. 增加运行对账指标：

```text
audit_terminal_total
audit_delivered_total
audit_delivery_gap
pending_queue_depth
refine_result_queue_depth
finalization_pending_jobs
```

5. 当 `audit_delivery_gap != 0` 时记录 ERROR，并在前端标记分析结果不完整。
6. 自动生成单次持续分析审计报告，至少包含扫描窗口、覆盖账本、候选状态迁移和最终列表对账。

#### 验收标准

- 可用自动化测试稳定复现本次两个 P0 故障；
- 修复后相同测试通过；
- 日志报告可以直接回答“哪个候选在哪个阶段丢失或等待”。

## 5. 实施顺序与依赖

```text
T1 审计结果可靠交付 ─────┐
                         ├─→ T5 状态对账 ─→ T6 端到端回归
T2 收尾恢复闭环 ─────────┘

T3 调度与吞吐优化 ─────────→ T6 长时压测

T4 生产等价评估与发布门禁 ─→ 模型重新评估/是否激活
```

推荐执行顺序：

1. T1：先消除已确认结果丢失；
2. T2：保证退出和重启不丢尾部；
3. T5：统一用户可见状态；
4. T6：建立真实控制流回归；
5. T3：优化吞吐和调度；
6. T4：重新评估模型，最后决定模型是否继续作为生产默认。

T1、T2 完成前，禁止用“提高确认数量”或“降低阈值”掩盖运行准确度问题。

## 6. 最终验收场景

### 场景 A：审计中途超时

- 连续提供 3 个候选；
- 第 1 个确认通过；
- 第 2 个处理中超时；
- 第 1 个必须出现在列表且状态为 confirmed；
- 第 2、3 个仍可恢复；
- 所有计数能够对账。

### 场景 B：运行中关闭应用

- 持续分析落后 60 秒并存在 pending 候选；
- 用户关闭应用；
- checkpoint 绑定最终录像路径；
- 重启后自动恢复或显示明确恢复入口；
- 收尾完成后 coverage 覆盖到录像尾部；
- pending 不因进程退出而永久丢失。

### 场景 C：一小时官方解说持续分析

- 使用未参与训练的完整赛事录像；
- DirectML 环境连续运行至少一小时；
- 净覆盖速度、滞后、审计队列符合 T3 门槛；
- `audit_delivery_gap=0`；
- 人工核对回合召回、列表精确率和边界误差满足 T4 门槛。

### 场景 D：模型发布阻断

- 提供 `gates_passed=false` 的候选模型；
- 激活流程必须拒绝替换生产模型；
- 默认模型 SHA 保持不变；
- UI 与日志明确显示阻断原因。

## 7. 交付物

1. T1–T6 对应代码修改；
2. 行为级后端/前端自动化测试；
3. 一小时真实运行报告；
4. 生产等价模型评估报告；
5. 模型 promotion/rollback 元数据；
6. 更新后的 `CLAUDE.md` 持续分析约束和 WebSocket 状态协议；
7. 修复前后对账表，包括每个 `round_key` 的粗扫、审计、发布、列表和收尾状态。

## 8. 完成定义

只有同时满足以下条件，才能关闭本修复任务：

- [ ] 审计批超时和抢占不再丢失已完成结果；
- [ ] 应用退出后的 pending 收尾可自动或手动恢复；
- [ ] Workbench 已接通恢复收尾入口；
- [ ] 一小时运行中净覆盖速度与滞后达标；
- [ ] 生产推理和离线评估输出一致；
- [ ] 独立测试集覆盖全部五类且模型通过发布门禁；
- [ ] `audit_accepted_count`、结果队列、列表和 sidecar 数量完全对账；
- [ ] 所有新增行为测试与相关现有测试通过；
- [ ] 不降低 broadcast 证据门禁，不影响 POV 路径；
- [ ] 未覆盖、未确认或未通过门禁的风险均有明确记录。
