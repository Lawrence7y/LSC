# 官方解说（broadcast）分支持续分析：审计存活性与结论交付修复方案（2026-09-11）

对象：`docs/reports/valorant-broadcast-marker-roi-branch-20260911.md` 之后暴露的**在线链路存活问题**——
切片列表里长期停留未审计的粗边界切片（含纯回放片段、跨回合超长片段、半路截断片段）。

## 1. 现场证据（2026-09-11 两次真实会话）

| 会话 | 规模 | 审计结果 | 列表产物 |
| :--- | :--- | :--- | :--- |
| 11:52–12:24（room `d523ebd`） | 32 min | `audit_terminal_total = 0`；`边界审计超过预算` 12 次、每次 `queued=0` | 8 条 `pending_lookahead` 粗切片，其中 2 条纯回放、多条尾部带回放、多条半路截断 |
| 12:38–12:49（room `fa030ebc`） | 11 min | `audit_terminal_total = 4`、交付 2 | 2 条边界正确 + 1 条未及审计（含 18s 回放、提前 60s 截断） |

关键机制（实测，非推断）：

1. **单步成本 > 墙钟预算**：对一个 551.8s 超长候选（被 `_expand_oversize_candidates` 切成 4 块），
   一次审计调用实测 **41.8s（冷）/ 21.3–22.3s（热）**，而运行时预算是
   `_BCAST_REFINE_STEP_MAX_SEC = 20.0`（`room_handler.py:379`）。其中 `prefetch_ranges`
   一次性解码 **≈514 帧 ≈ 23.2s**（`valorant_broadcast.py:1011-1019`，块门禁窗口按
   `MAX_BROADCAST_ROUND_SEC=150s` 预取）。
2. **超时即丢弃结论**：该次调用实际已判定 **4 个子块全部 rejected**（`audit_cache` 落盘可见），
   但运行时超时置 `refine_abort` → `cancel_check` 抛 `FFmpegCancelled` → 结果随 executor 线程丢弃
   （日志 `queued=0`）。这是对既有契约「审计结果永不丢失（2026-09-08）」的违反。
3. **连锁：粗扫被拖到 <1x**：每周期白付 20s 审计时间，扫描吞吐从 2.1x（空载实测）掉到
   0.81–1.15，滞后 20s → 220s，之后审计被 `backlog > 60s` 策略性抢占 → 永久无法定稿。
4. **队首阻塞**：`_pending.sort(key=start)` + 每批 `max_audit_quota = 1`，未定稿的超长候选永远排第一，
   其后的候选一个也轮不到。

## 2. 结论：问题在「在线视觉审计」环节的**存活性与交付**，不在模型与边界算法

- 审计跑完的会话里，边界正确（逐秒真值核验：R01 38.3–108.7 覆盖整回合；R02 216.6–259.5 止于
  ROUND WIN 出现处；R01 的候选 12.0–41.3 被 A2 入点回放否决）。
- 审计跑不完的会话里，列表留下的就是粗扫原始边界——两者差异 100% 由「审计是否完成」解释。

## 3. 修复设计（本轮范围）

### A. 审计结论在取消路径上必须交付（`valorant_broadcast.py` + `room_handler.py`）

1. `audit_broadcast_rounds_with_outcomes(..., outcome_sink=None)`：把内部 outcome 列表暴露给调用方；
   取消（`cancel_check` 抛 `FFmpegCancelled`）时已记录的 outcome 仍留在该列表。
2. **批次完整性**：审计在取消路径上为「尚未记录的候选」补发 `pending` outcome 后再抛异常，
   使调用方拿到的批次始终完整——否则消费端会把「只看到 1 条拒绝」误判为整批终态而弹掉槽位，
   未审计的子候选被静默丢弃（**这是必须避免的新问题**）。
3. `room_handler._do_boundary_refine` 取消路径：消费该批次——`rejected` 照常交付（剪除已入列脏切片、
   计数、tombstone）；非拒绝终态（accepted/manual_review）**降级为 pending 留在队列**，
   下一轮由审计缓存复现后走完整路径（含入点密扫），保持「accepted ⇒ 边界已密扫」的既有不变量。

### B. 在线预取受媒体微步骤预算约束（`valorant_broadcast.py`）

`max_media_step_sec is not None`（在线）时，把每个候选的门禁预取窗口也截到
`start + max_media_step_sec`（现仅截尾部窗口）。预取只是加速手段，门禁本身仍按需抽帧并经
`FrameProvider` 缓存 ⇒ 语义不变、单步有界。

**本轮不做**：粗扫 FSM 回放盲（9 分钟候选）、入点密扫口径、`realtime_fast_mode`——前者涉及
产品语义（用户已否决过一版门控方案），单独走 `docs/plans/valorant-broadcast-inpoint-workstream-20260910.md`。

## 4. 逐项风险分析（为什么不会引入新问题）

| 变更 | 潜在风险 | 论证 / 防护 |
| :--- | :--- | :--- |
| A1 暴露 sink | 调用方误用为「增量发布」造成半成品入列 | sink 仅在**异常路径**读取；正常路径返回值不变（`outcomes is sink`） |
| A2 取消补发 pending | 补发的 pending 覆盖真实终态 | 仅对 `round_key` 未出现在 sink 中的候选补发；`_expand_oversize_candidates` 已保证子键唯一（`-sN`） |
| A3 降级 accepted | 已通过审计的回合被延迟一轮才入列 | 延迟 ≤ 一个扫描周期；不参与定稿的丢失（缓存复现）；保持「accepted 必带密扫边界」 |
| A3 rejected 交付 | 交付了「本会被推翻」的拒绝 | 拒绝结论均落在**已完整扫描的窗口**上：门禁结论经 `gate_window_covered` 校验并跨重试固化（`start_gate_rejected`），尾部结论「截断后仍递减交战钟」有硬否决 | 
| A3 重复交付 | `audit_terminal_total`/`audit_rejected_count` 虚增 | 消费端 `audit_cache` 每子键终态唯一：全部终态时弹槽位，不再复现；`_enqueue_refine_result` 按 `room:recording:round_key` 幂等 |
| B 预取截断 | 门禁抽帧变慢 / 重复解码 | 只减少一次性预取；`_extract` 命中 `FrameProvider` 缓存，语义与判定窗口无关 |

**必须保持不变的既有契约（守卫）**：`_BCAST_REFINE_STEP_MAX_SEC = 20.0`、
`_BCAST_REFINE_STEP_MEDIA_SEC = 18.0`、`max_audit_quota = 1`、`timeout_state['refine_abort'] = True`、
日志串「边界审计超过预算」、「refined = [] … finally」区间内不得出现 `while True:`
（`tests/test_continuous_analysis_guards.py:201-220` 逐字钉住）。

## 5. 验证计划

1. **前置基线**：改动前用生产 audit 函数对 3 类真实候选（会话B 两条常规候选、会话A 超长候选）落盘
   判定与耗时（`C:/lsc_tmp/audit_verdicts_before.json`），改动后同输入逐字对比：常规候选结论必须完全一致。
2. **新增单测**（不需要模型/视频，沿用 `tests/test_valorant_broadcast.py` 的 fake 分类器风格）：
   - sink 在取消路径保留已记录 outcome；
   - 取消路径补齐 pending ⇒ 批次完整（未审计子候选不被丢弃）；
   - 在线预取窗口受 `max_media_step_sec` 约束；
   - `room_handler` 取消路径交付拒绝结论（源守卫）。
3. **运行时仿真**：以 20s 墙钟预算逐步驱动审计（含取消语义），确认超长候选在有限轮次内收敛为
   全部终态、且常规候选不受影响。
4. **全量 pytest**（broadcast/continuous 相关文件）+ `test_continuous_analysis_guards.py`。
