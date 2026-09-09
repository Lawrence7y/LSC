# Valorant 官方赛事/解说流持续分析优化（阶段一）：架构非阻塞重构与实时体验落地

> **阶段定位**：解决“不出切片”与“输出严重滞后（慢）”的架构与调度核心问题。
> **目标收益**：切片在回合粗扫闭合后 **10~15 秒内即时上屏**（Pending 待复核状态），消除黑盒等待；自适应缩短物理等待窗，释放算力并发。

---

## 一、现状瓶颈深度剖析

通过对源码（`python-backend/handlers/room_handler.py`、`lsc/analyzer/valorant_broadcast.py`、`lsc/analyzer/valorant_plugin.py`）的执行链路追踪，当前阶段存在四大架构死锁：

```
[原始录像写入] ──> [粗扫产生候选] ──> [90s Lookahead 硬等待] ──> [Quota=1 串行排队] ──> [重度 OCR 阻塞] ──> [一票否决门禁过滤] ──> [用户端空空如也]
```

1. **一票否决式入列门禁（`room_handler.py:1579-1588`）**：
   - 规则：`source_profile == "broadcast"` 时，切片必须同时满足 `audit == "passed"` 且 `confirm_status == "vision_confirmed"` 且 `end_by in ("next_prep", "broadcast_exclusion")`。
   - 后果：任何因等待后视、缺少买枪横幅、或回放证据不足而处于 `pending` 的切片，全部被 `_is_listable_ocr_round` 直接 `return False` 抛弃，前端切片列表完全空白。
2. **硬编码 90 秒后视物理等待（`valorant_broadcast.py:490-515`）**：
   - 规则：`END_LOOKAHEAD_SEC = 90.0`。如果录像未向后写满 90 秒，或者没有抓到强结算横幅，候选直接打成 `pending_lookahead` 并挂起。
   - 后果：即使一个回合在第 100s 已经结束，系统必须等到录像录到第 190s 才能开始判定，造成现实时间 1.5 分钟以上的刚性延迟。
3. **极窄配额节流与单任务串行（`valorant_plugin.py:446-460`）**：
   - 规则：只要存在微小 backlog（>30s），`quota` 就被钳制为 `1`。
   - 后果：若比赛短时间内打完 3 个回合，系统每次扫描轮询（间隔 15~30s）只允许审 1 个候选，后续回合在队列中逐级累积，延迟滚雪球至数分钟。
4. **冗余双倍 ROI OCR 算力风暴（`valorant_ocr_rounds.py:76-80`, `valorant_broadcast.py:600-615`）**：
   - 规则：`_BROADCAST_TOP_BAND_RATIOS = (0.12, 0.18)` 导致每帧顶部 OCR 执行两次；120 秒抽帧中所有非 combat 帧及特定 stride 帧均跑 EasyOCR 单帧文字识别。
   - 后果：CPU 下单回合审计耗时 10~15 秒，事件循环被卡死，持续触发系统降级。

---

## 二、阶段目标与架构重构全景

```
[原始录像写入]
       │
       ▼
[轻量快速粗扫 (1~2s)] ──> [即时入列切片池] ──> [推送到前端 (黄色 Pending 待复核徽标)] ──> 用户可即时预览/手动微调
       │
       ▼
[后台异步审计 Worker]
       ├─ 自适应动态 Lookahead (15s~45s，命中转场/比分跳变立即截断)
       ├─ Batch 推理 (Batch Size=32 并发打标，跳过冗余 OCR)
       └─ 多候选并行消费 (解除 quota=1 限制)
       │
       ▼
[审计完成定稿] ──> [推送局部更新 (绿色 Confirmed 徽标)] ──> 具备精确双向物理证据，允许自动导出
```

### 核心不变量与安全防御
- **不变量 1（安全导出）**：未通过审计的 `pending` 切片绝不触发 `auto_export` 自动导出，必须经后台审计 `passed` 或用户手工确认后方可导出。
- **不变量 2（非破坏性）**：普通个人直播 `pov` 分支行为保持 100% 不变。
- **不变量 3（单调可恢复）**：`last_analyzed` 游标由粗扫快速单调推进，审计状态由跨窗口 `broadcast_pending_rounds` 安全持有，崩溃后可断点恢复。

---

## 三、拆解任务清单（Work Breakdown Structure）

### 任务 1.1：入列门禁放宽与 Pending 切片全链路贯通
- **目标**：让粗扫发现的赛事回合能够即时展示在时间轴和切片列表中，标明“待复核（Pending）”，消除黑盒感。
- **改动文件**：
  - `python-backend/handlers/room_handler.py`
  - `lsc-electron/src/pages/Workbench/components/ClipList.tsx`
  - `lsc-electron/src/components/Timeline/index.tsx`
- **实现细节**：
  1. 修改 `room_handler.py:_is_listable_ocr_round`：
     - 当 `source_profile == "broadcast"` 时，若 `confirm_status == "pending"` 且 `start_by in _OCR_VALID_START_BY`，只要时长在合理区间（5s ~ 150s），**允许入列**（返回 `True`）。
     - 保留 `_is_auto_exportable_valorant_round` 门禁：自动导出依然必须要求 `broadcast_audit == "passed"` 且 `confirm_status == "vision_confirmed"`。
  2. 在推送给前端的切片元数据中，显式携带 `audit_status: "pending" | "passed" | "rejected"` 以及 `confirm_status`。
  3. 前端界面呈现：
     - `ClipList`：未完成审计的切片打上橙色「待审计 / 粗筛」Tag，提供即时播放头预览与手动微调端点；
     - `Timeline`：时间轴上方以半透明/虚线硬色带呈现，审计定稿后平滑过渡为实体色带。

### 任务 1.2：转场感知与自适应 Lookahead 动态短路
- **目标**：将 90 秒无脑物理等待转变为由导播转场与视觉事件驱动的动态 Lookahead（15s ~ 45s），极大压缩定稿延迟。
- **改动文件**：
  - `lsc/analyzer/valorant_broadcast.py`
- **实现细节**：
  1. 引入导播镜头突变检测器（Scene Change / Blackout Cut）：
     - 赛事导播在最后一杀结束后通常在 1~3 秒内切黑屏转场、切战队胜场动效或全景机位；
     - 计算连续帧差（`frame_delta > 35.0`）或全暗帧（`mean_intensity < 15.0`）；
     - 若在初步出点（OCR end）后 2~10 秒内检测到确凿的导播切镜头事件，且视觉模型识别为 `non_game` 或 `result`，**立即触发 `broadcast_exclusion` 截断，提前定稿**。
  2. 自适应后视窗口收敛：
     - 将基础 `END_LOOKAHEAD_SEC` 从 90.0 秒缩窄为 **45.0 秒**（足以覆盖最长 15s 慢动作回放 + 10s 观察缓冲）；
     - 若在初步 end 后 15 秒内已经探测到稳定的 `replay` 或 `non_game`，直接在此截断，**不再等待剩余时长**；
     - 只有在尾部完全未观测到任何截断信号且录像仍在进行时，才等待至 45 秒。

### 任务 1.3：粗扫推进与异步后台审计并发扩容
- **目标**：粗扫只用 1 秒跑完并推进进度；后台审计 Worker 批量并发处理候选，解除 `quota=1` 串行瓶颈。
- **改动文件**：
  - `lsc/analyzer/valorant_plugin.py`
  - `python-backend/handlers/room_handler.py`
- **实现细节**：
  1. 在 `valorant_plugin.py` 中彻底贯彻 `deferred_audit`：
     - 增量扫描 `scan_window` 仅执行顶部轻量 OCR 锚点检测，生成粗候选，全部 push 进 `runtime_state["broadcast_pending_rounds"]`；
     - `scan_window` 立即返回粗切片（用于即时入列），并将 `last_analyzed` 游标推至 `window.end_sec`；
     - 严禁在 `scan_window` 内部同步进行深抽帧与视觉审计。
  2. 重构 `room_handler.py` 后台精修 Worker：
     - 调高消费上限：`max_audit_quota` 提升至 4~6；
     - 引入条件就绪调度：优先挑选已经满足 Lookahead 录制时长条件的候选，一次性打包传入 `audit_broadcast_rounds_with_outcomes` 批量执行；
     - 定稿后通过 `ws_send('round_updated')` 局部刷新切片属性，无缝更新前端。

### 任务 1.4：审计计算减负（消除 90% 冗余 OCR 与降本）
- **目标**：消除单回合 10s+ 的 CPU 消耗，使单回合审计耗时压缩至 1.5s 以内。
- **改动文件**：
  - `lsc/analyzer/valorant_broadcast.py`
  - `lsc/analyzer/valorant_ocr_rounds.py`
- **实现细节**：
  1. 剪除不必要的二次 OCR 裁剪：
     - 审查 `_BROADCAST_TOP_BAND_RATIOS`，合并重叠 ROI，利用单次图像裁剪统一送入 OCR 模型。
  2. 计时器 OCR 惰性触发：
     - 视觉分类模型（ONNX）推理极快（Batch 批量处理 60 帧仅需 100~200ms）；
     - 计时器 OCR（EasyOCR/PaddleOCR）单帧需 1.2s；
     - 优化触发策略：仅在视觉模型分类结果置信度模糊（0.45 < conf < 0.65）或疑似官方技术暂停（连续 3 帧 static combat）时才触发计时器验证；常规的明确 `replay` 和 `non_game` 游程直接根据视觉分类截断，**不跑单帧计时器 OCR**。

---

## 四、分步实施计划（Execution Plan）

### Step 1：放宽入列门禁与前端展示对齐（Day 1）
1. 修改 `room_handler.py` 中 `_is_listable_ocr_round`：放行 `source_profile == "broadcast"` 且 `confirm_status == "pending"` 的候选。
2. 运行 `tests/test_valorant_broadcast.py` 与 `tests/test_continuous_analysis_guards.py`，新增守卫用例：验证 pending 切片可以正常入列，但不可自动导出。
3. 验证前端切片列表对 `confirm_status="pending"` 切片的徽标渲染。

### Step 2：自适应 Lookahead 与导播镜头截断实现（Day 2）
1. 在 `valorant_broadcast.py` 中重构 `audit_broadcast_phase_sequence` 与 `audit_broadcast_rounds`：
   - 增加帧差转场与短路逻辑；
   - 将 `END_LOOKAHEAD_SEC` 降至 45.0s；
   - 增加即时截断短路分支（Early-Exit Cutoff）。
2. 编写单元测试验证：在第 5s 出现 Replay 或转场时，算法能够在 6s 处截断定稿，而非等待到 90s。

### Step 3：后台 Worker 批量化与配额扩容（Day 3）
1. 改造 `room_handler.py` 内部的 `_do_boundary_refine` 逻辑：
   - 支持多候选批量审计；
   - 优化 `_consume_broadcast_audit_outcome` 状态流转与持久化。
2. 改造 `valorant_plugin.py:plan_scan_window`，确保粗扫完全解耦，`scan_window` 不再发生同步阻塞。

### Step 4：OCR 惰性触发与性能压测（Day 4）
1. 在 `valorant_broadcast.py` 中收敛 `_read_top_anchors` 触发频次；
2. 本地回放录像压测：测量从回合打完到切片入列（Pending）的耗时，以及从入列到定稿（Confirmed）的耗时。

---

## 五、验收标准与质量门禁

| 验收项 | 当前现状 | 阶段一目标 | 验证手段 |
| :--- | :--- | :--- | :--- |
| **首帧切片上屏延迟** | 90s ~ 数分钟（甚至不出现） | **≤ 15 秒**（以 Pending 呈现） | 真实比赛回放推流实测 |
| **最终精修定稿时延** | 120s ~ 超时丢弃 | **≤ 40 秒**（动态转场截断） | 自动化时间戳断言测试 |
| **单回合审计 CPU 耗时** | 10s ~ 15s | **≤ 2.0 秒**（消除冗余 OCR） | Python cProfile 性能采样 |
| **切片入列召回率** | < 40%（大量被门禁丢弃） | **≥ 90%**（未被拦截） | 盲测录像 GT 回合匹配率 |
| **测试套件回归** | - | 全部单测通过，0 回归失败 | `pytest tests/` & `npm test` |
