# 赛事草稿「已定稿切片被静默丢弃」修复计划（2026-09-11 20:45 现场）

> 状态：**待执行**（本文档只描述清单与验收口径，不改代码）。
> 同族报告：`valorant-broadcast-b1-retrain-result-20260910.md`、`docs/plans/broadcast-audit-liveness-fix-20260911.md`。
> 回归夹具：`tests/fixtures/broadcast_export_case_20260911_2045/`（含 provenance README）。

## 〇、红线

**全程不放宽 `_broadcast_gate_passed` 的判据**（唯一例外是 C5「标注入草稿」，默认关闭、需产品确认）。
所有改动只做三件事：已算出的结论不丢、没算完的有机会算完、算不出的说清楚。

## 一、现场（已核实）

| 事实 | 证据 |
|---|---|
| 请求 8 / 写入 3 / 跳过 5 | `backend.log` 20:45:21 `generate_jianying_draft_response` |
| 105 在 20:43:26 已定稿（passed / broadcast_exclusion / precise），仍被跳过 | 审计日志 + 请求报文里它就是 passed/precise |
| 135 被报「旧分析会话遗留切片」，真实原因是 20:45:17 才出结论且出点未定稿 | 审计日志 + 最终 sidecar 无 135 |
| durable 终态 4 条 vs 审计结论 8 条 | `finalization.json` accepted 3 + rejected 1；日志里 8 条「赛事回合审计完成」 |
| 改名后仍写旧名 sidecar：`录制中.analysis.json`(20:39) vs `至_….analysis.json`(20:37，无 135) | 输出目录文件 mtime + 内容 |

根因链：**任务态 pop（20:45:20）→ 权威回落 20:37 的旧分析 sidecar → 105 被改回 pending_lookahead →
门禁按「未确认」拒绝**；135 则因为不在任何权威集合里被归到「旧会话遗留」。
两条都是"结论丢失/失联"，不是门禁判错。

## 二、修改点清单

| ID | 位置 | 动作 | 证据 | 回归 |
|---|---|---|---|---|
| **C1** | `room_handler.py` 任务 pop 处 + `jianying_handlers._find_authority_task_state/_lookup_continuous_clip` + status 端点 | 终态快照跨会话保留：pop 前把 `listed_clips + accepted/rejected + recording_id` 存入 `_last_authority_snapshots[room_id]`；仅在**新录制 epoch** 或删房时清理 | pop 与导出相隔 1s；105 因权威不可达被旧快照回滚 | 夹具 A（105 回归入列） |
| **C2** | `_record_terminal_candidate_state` 复用 + 收尾扫描结果合并处 | 新增 `_project_scan_audit_terminals(state, all_highlights)`：扫描通路终态按与 `_record_audit_outcome` **同一映射**补齐 durable 投影（按 round_key 幂等） | 20:43 的 105 只进 listed；`audit_terminal_total` 恒为 4（8 条结论仅 4 条落盘） | 夹具 A + 夹具 C 不变量② |
| **C3** | `save_analysis_results` 调用点 + 改名检测分支 | 改名后同步 `state['video_path']`/游标；落盘前校验目标目录存在当前录像 | 改名后仍写 `录制中.analysis.json` | 新增守卫：改名后必须命中「至_」sidecar |
| **C4** | `jianying_handlers` 跳过分支 + 响应 `skipped:[…]` + 前端结果展示 | 跳过原因带 `round_key + 区间 + reason_code`：`END_NOT_FINAL` / `NEVER_AUDITED` / `NO_EXCLUSION_EVIDENCE` / `NOT_IN_AUTHORITY` / `REJECTED` | 4 条共用同一句告警、标签重名（R03/R05/R08 各两条） | 夹具 A + `test_broadcast_export_gate_parity.py` |
| **C6** | 收尾完成判定 | `pending_audit` 收紧为「pending 队列非空 **或** 仍有 listed 切片无终态归属」 | 20:45:20 完成瞬间 pending 刚清零（135 在 20:45:17 才出结论） | 夹具 C 不变量② |
| **C5**（可选·产品决策） | 前端列表 + `jianying_draft.py` 标签 + `_broadcast_gate_passed(annotate_provisional=False)` | 未定稿切片**允许入草稿但带标注** | 列表承诺 8、草稿只给 3、无任何提示 | 夹具 A 的 opt-in 用例；断言默认关闭 |

## 三、执行顺序与验收门

| 阶段 | 内容 | 验收门 |
|---|---|---|
| **P0** | 固化夹具 A/C，跑出"改前必红"基线；sidecar 与录像路径登记进 `docs/reports/` | 夹具 A 报 3 条；夹具 C 报「8 结论 vs 4 落盘」 |
| **P1** | C1 + C2 + C4 | 夹具 A：included 3→**4**，105 入列，135 原因变 `END_NOT_FINAL`；全量 pytest 无回归 |
| **P2** | C3 + C6 | 夹具 C 不变量①②在归档日志上转绿 |
| **P3** | 夹具 B：离线复算 076/123/135（真实录像，只读） | 出 `docs/reports/reaudit-2045-20260911.json`：135 能否在 254s 后视内定稿 |
| **P4** | 依 P3 结论决定「收尾终点按剩余时长继续扫描」是否排期 | 数字有据 |
| **P5** | C5 | 产品确认默认关闭 |

依赖：C2 会改变 finalization sidecar 的 `accepted_candidates` 内容 → 必须与
`tests/test_continuous_finalization.py`、`test_broadcast_draft_failclosed.py` 同批改，
避免"测试按旧语义写死"。C5 依赖 C4 的 reason_code。

## 四、验收数字

| 指标 | 改前 | P1 后 | P3/P4 后（视结论） |
|---|---|---|---|
| requested / included / skipped | 8 / 3 / 5 | 8 / **4** / 4 | 8 / 4~6 / 4~2 |
| 105（已定稿却丢） | 跳过「未确认…」 | **入草稿** | 入草稿 |
| 135 原因 | 「旧分析会话遗留切片」❌ | `END_NOT_FINAL(next_prep)` ✅ | 可修则入草稿（标注兜底） |
| 123 原因 | 「未确认…」 | `NO_EXCLUSION_EVIDENCE` ✅ | 同左 |
| 076 原因 | 「未确认…」 | `NEVER_AUDITED` + 触发补审 ✅ | 补审后定稿则入草稿 |
| durable 终态 / 审计结论 | 4 / 8 ❌ | 8 / 8 ✅ | 8 / 8 |

## 五、回归手册（统一用这一份录像）

- **L1（每次提交，秒级）**：`pytest tests/test_broadcast_export_authority_lifetime.py tests/test_broadcast_export_gate_parity.py tests/test_broadcast_draft_failclosed.py tests/test_continuous_finalization.py tests/test_jianying_draft.py -q`
- **L2（合并前，分钟级，只读录像）**：夹具 C 跑归档日志 + 夹具 B reaudit，报告落 `docs/reports/`，与上次 diff（结论条数与耗时不得劣化）
- **L3（发版前，真实环境 30–40 分钟同一房间）**：录制→持续分析→停录→等 `finalization_state=completed`→导出；
  校验 `requested == included + skipped`、每条 skipped 有 `reason_code`、**无「已定稿却跳过」**、
  `completed` 时 `pending_queue_depth == 0` 且全部 listed 有终态归属、草稿明文备份的切片轨段数 == included。

## 六、风险与回滚

- C1 只加一个**只读**快照注册表，不改读路径优先级（活跃任务仍第一权威），可单独回滚；
- C2 幂等（按 round_key 去重，与既有 `rejected_round_keys` tombstone 兼容），但会**增加** sidecar accepted 条数 → 同步复核测试期望；
- C3 只影响落盘路径选择，不动录像与既有 sidecar；
- C5 默认关闭且不参与自动草稿路径（`include_pending:false` 不变）。

## 八、执行记录

### 已完成（P0 + P1）

*   **夹具 A**：`tests/fixtures/broadcast_export_case_20260911_2045/`（真实录像 0 字节占位 + 两份真实 sidecar +
    请求原文 clips.json + 派生的 authority_snapshot.json + expected.json + README provenance）。
    回归 `tests/test_broadcast_export_authority_lifetime.py`（8 条）。
*   **改前/改后实测对照**（同一夹具、同一代码路径）：

    | | included | skipped 与原因码 |
    |---|---|---|
    | 改前 | 3（45/55/70） | 71 NEVER_AUDITED、76 NEVER_AUDITED、**105 NEVER_AUDITED**、123 NEVER_AUDITED、135 NOT_IN_AUTHORITY |
    | 改后 | **4（45/55/70/105）** | 71 END_NOT_FINAL、76 NEVER_AUDITED、123 NO_EXCLUSION_EVIDENCE、135 END_NOT_FINAL |

*   **C1 终态权威快照**：`_last_authority_snapshots` + `_preserve_authority_snapshot()`（stop 路径 pop 前保留，
    新录制 epoch / 删房时清除）；`jianying_handlers._find_authority_task_state/_lookup_continuous_clip`
    在活跃任务之后回落读取它。
*   **C2 扫描通路 durable 投影**：`_project_scan_audit_terminals()`（与 `_record_audit_outcome` 同映射、
    按 round_key 幂等、扫描结果已入列故同时计 delivered），挂在 `_auto_export_highlights()` 入口。
*   **C4 跳过原因码**：`_skip_reason_code()` + 响应 `skipped:[{round_key,label,start,end,reason_code,reason,…}]`
    + 告警带上编码 + 前端草稿结果弹窗逐条展示。
*   **附带修复（原清单未列，实测发现）**：`register_jianying_handlers` 的调用点从未注入
    `_continuous_tasks/_analysis_jobs`，两者在生产里一直是各自模块里的空 dict ⇒ `listed_clips`
    权威补全与 `_merge_authoritative_clip` 全是死路径（草稿只靠 sidecar 回落）。本次一并接上
    三个注册表，并加了守卫断言注册参数。

### 已完成（P2 + P3 + 夹具 C）

*   **C3 归档改名后落盘路径同步**（`_sync_analysis_save_path`）：落盘前以房间**当前**录像为准
    （新名存在且与旧名不同即切换并记日志），两边都不存在时保留入参。
    证据：20:38:42 `sidecar 已随录像定稿改名` → 20:39:27 那次落盘仍写
    `…_录制中.analysis.json`，「至_」文件的 sidecar 被冻在 20:37 快照。
*   **C6 收尾完成判定收紧**：`pending_audit` 现为「待审计队列非空 **或** `_listed_items_without_terminal()`
    非空」；有界兜底对后者落 `manual_review` 终态（不删除、不放宽门禁），避免
    「队列空 + listed 无归属」让收尾无限重跑。
*   **夹具 C 不变量**：`scripts/audit_continuous_analysis.py` 新增
    `--finalization`、`parse_audit_conclusions()`、`parse_draft_responses()`、
    `build_invariants()`，不变量失败时脚本返回非零（L2 门禁可直接失败）。
    在本次归档日志上的结果（改前基线，`docs/reports/live-verify-2045-20260911.json`）：

    | 不变量 | 结果 |
    |---|---|
    | 每条审计结论都有 durable 终态 | ❌ 6 条结论 vs 4 条终态 |
    | 每个分析候选都有终态归属 | ❌ 71 / 76 / 105 / 123 |
    | 草稿口径一致且跳过可辨 | ❌ 5 条跳过但响应只有聚合告警 |

*   **夹具 B 离线复算**（`scripts/valorant_vision/reaudit_broadcast_candidates.py`，只读，
    报告 `docs/reports/reaudit-2045-20260911.json`，录像 1698.7s）：

    | 候选 | 离线结论（finalize=True，后视给足） | 耗时 |
    |---|---|---|
    | round-000076 | **rejected / no_stable_combat_start**（无效候选，不是"待审计"） | 2.3s |
    | round-000123 | **manual_review / no_exclusion_evidence**（`end_quality=coarse`） | 12.7s |
    | round-000135 | **accepted / next_prep**，但 `end_quality=coarse`、`end_refined=1445`（=未定稿） | 15.4s |

    **P3/P4 结论**：135 即便拿到 254s 后视素材，审计也只能给出 `next_prep/coarse` 出点
    （日志里能看到它在 1423.2 触发「终点硬否决：截断后交战计时器仍递减」）⇒ **"按剩余时长继续扫描"
    不需要排期**；正确出口是人工确认（或 C5 标注）。076 则应被拒绝终态收口而不是长期 pending。

### L3 真实环境验收（2026-09-12 08:44–09:01，room `633dbf182b254333adc3d63d39a933eb`）

驱动：`C:\lsc_tmp\l3_run.py`（走程序自身 WS API：连接→预览→起录→broadcast 持续分析 16 分钟→
停录→等收尾→按前端视角的切片清单导出草稿）；报告 `C:\lsc_tmp\l3_run_20260912_084400.json`。

**夹具 C 三条不变量全部转绿**（`docs/reports/live-verify-0901-20260912.json`）：

| 不变量 | 本场结果 |
|---|---|
| 每条审计结论都有 durable 终态 | ✅（结论 2 条 / 收尾 sidecar 终态 11 条；结论数少是 backend.log 在跑途中轮转） |
| 每个分析候选都有终态归属 | ✅ |
| 草稿口径一致且跳过可辨 | ✅ 两次草稿响应都带 `skipped` 明细 |

**链路标记（日志原文）**：

```
09:01:08 扫描通路终态已补投影: 新增 2 条, terminal_total=8, accepted=4, rejected=3     ← C2
09:01:38 收尾补扫达到上限，仍未定稿的已入列切片落 manual_review: round-000047 / round-000063  ← C6 有界收口
09:01:38 收尾补扫达到上限，强制终止 1 个无法定稿的候选（另 2 条已入列切片落 manual_review）
09:01:48 持续分析收尾完成: 累计 7 段                                                   ← 收尾收敛（无无限补扫）
09:01:48 终态权威快照已保留: listed=7, source=continuous_finalize                      ← C1
```

**C3**：本场输出目录里**没有**新的 `…_录制中.analysis.json`（唯一一条是 09-11 现场遗留），
「至_」sidecar 的 mtime = 09:01（最新）⇒ 归档后落盘路径正确。

**草稿**：程序自身的自动草稿 7/4/3、驱动脚本的 9/3/6，两次都带逐条 `skipped` 与 `reason_code`；
被拒/未审计的条目继续被拒（红线未动），只是原因可定位。

**L3 暴露并当轮修掉的两个小问题**：

1. 权威校验阶段的拒绝（文案「非当前录制权威切片：已拒绝(…)」）被 `_skip_reason_code` 误判成
   `NEVER_AUDITED`（切片 dict 自身仍带前端陈旧的 `pending_lookahead`）→ 增加文案判据，现为 `REJECTED`。
2. 墓碑原因取 `broadcast_start_gate`，入点门禁通过时会得到「已拒绝(ok)」这种无信息量文案
   → 新增 `_rejection_reason()`：优先审计结论（如 `rejected_no_stable_combat`），其次门禁，
   最后兜底 `rejected`。

### P1 追加：静默丢弃清零（2026-09-12，09:01 现场 065 门）

夹具 `tests/fixtures/broadcast_export_case_20260912_0901/`（L3 实跑请求 + 真 sidecar + 权威快照差异），
回归 `tests/test_broadcast_draft_silent_drop.py`（5 条）。三个缺陷：

1. **identity 静默丢弃**：`_merge_authoritative_clip` 用权威侧 `clip_id` 覆盖前端值，而权威
   `clip_id` 是按边界派生的（065 定稿把 end 801.5→789.75 ⇒ `…_6515_7898`），随后
   `honor_clip_ids` 只按请求里的旧 id 比对 ⇒ 整条静默丢弃，且丢的正是刚精修好的那条。
   修法：`honor_clip_ids` 同时接受**合并前**的原始 id；真的不在清单里时补逐条留痕
   （`_record_skip`）。
2. **录制轴别名不同步**：`resolve_common_range` 优先读 `recording_start_sec/recording_end_sec`
   （前端 payload 就带这两个），而 reconcile 只改 `start/end` ⇒ 权威精修出点被前端旧值顶掉，
   草稿带进 12s 赛后内容且零告警。修法：merge 时按同一录制轴回填这两个别名。
3. **对账残差不可见**：导出器 `clip_source_usable` 过滤只累加本地计数、从不输出 ⇒
   requested−included 的差额无法逐条对账（现场 6 vs 5）。修法：`JianyingDraftResult.excluded_clips`
   逐条记录 + 逐条 warning；响应新增 `skipped_unaccounted`
   （= 计数 − 明细数，恒等 0 才算对账干净）。

**实跑复核（离线复算 09:01 请求）**：修复前 `kept=3 / 残差 1`；修复后 `kept=4`（065 入列）、
065 出点取权威 **789.75**（不再是前端 801.5）、`4 + 5 = 9` 残差 **0**。

### P2 追加：草稿覆盖保护 + 「列表同步」的复核更正（2026-09-12）

**① 同名草稿覆盖**（真实缺陷，已修）：自动命名只精确到分钟（`LSC_<房间>_YYYYMMDD_HHMM`），
09:01:48 的自动草稿（4 段）被 09:01:54 的手动导出（3 段）同名覆盖。现自动命名一律避让
（`_2`、`_3`…，并给出「本次写入 … 以免覆盖上一份」告警）；**显式命名仍覆盖**——那是前端
"重试生成草稿"的既定语义，`tests/test_jianying_draft.py::test_build_overwrite_same_name` 锁着它。

**②「前端列表没删被拒切片」是误判 —— 已用日志更正**：

```
09:01:48 程序自身请求 7 条: 000000 / 000010-s0 / 000028 / 000047 / 000063 / 000065 / 000093
           → 不含任何被拒回合（007/034/036）✓ 前端 appStore 的 clip_confirm_status=rejected
             移除逻辑（index.tsx:3848-3856）本来就生效
09:01:54 驱动脚本请求 9 条: 多出 007 / 000034 / 000036（脚本自己不会在 rejected 事件上删除）
           → 那三条"又出现"是**验收脚本的账本**，不是前端缺陷
```

守卫：`tests/test_broadcast_draft_silent_drop.py::test_frontend_removes_rejected_clips_from_list`
（源码级，防回退）。

### P3 追加：列表逐条标注「为什么没进草稿」（2026-09-12，用户选定方案 1）

审计吞吐 ≈2 分钟/条 > 收尾可用时间（91s）是"好几条没通过"的真因；离线复算已证明
拉长收尾救不回 135 那类候选（给足 254s 后视素材仍只能拿到 `next_prep/coarse`），
因此选择把**状态直接标在列表里**，不再让用户到导出时才发现少了条目。

*   `utils/clipExportPolicy.ts`：新增 `clipExportState()`（`EXPORTABLE` / `PENDING_AUDIT` /
    `NEEDS_CONFIRM` / `REJECTED` / `BLOCKED`）与 `CLIP_EXPORT_STATE_LABEL/HINT`；
    判定顺序：拒绝终态 → 时长异常/近似定位 → 可导出 → 未审计 → 需确认。
*   `ClipList.tsx`：非「可导出」状态在行内加彩色标签（待审计=蓝 / 需确认=琥珀 / 已排除=红 /
    不可导出=灰红）+ tooltip 说明；**色轨同步**——此前待审计的切片也亮青色 `rail-ready`
    （写的是"可导出"），是用户误判的直接来源，现改用 `rail-pending` 并补 `RAIL_LEGEND`。
*   守卫：`clipExportPolicy.test.ts` 5 个状态用例（真实形状）；
  `test_broadcast_export_gate_parity.py::test_frontend_export_state_vocabulary_tracks_backend_reason_codes`
  钉住「前端状态码 ↔ 后端原因码」的对应关系（防止两边各说各话）。

### 待做

*   **P5**：C5 标注入草稿（需产品确认，默认关闭）——与上面的列表标注是同一件事的
    "草稿侧"补充；要不要做由产品决定。
*   注意：本仓库同日有另一会话在改 `jianying_handlers.py` / `room_handler.py`（新增
  `INTERIOR_BOUNDARY` / `DUPLICATE_ROUND` 原因码与去重逻辑），本次改动是在其版本之上叠加的。

## 七、与原始清单的差异（执行时记录）

1. 夹具 A 增加 `authority_snapshot.json`：原始清单的三件套（mp4/analysis/finalization）无法表达
   "pop 时的 listed_clips"，而 C1 的修复对象正是它；该文件以前端 20:45:21 请求为底，按 backend.log
   的 20:43/20:44/20:45 三条审计结论修正 105/123/135（详见夹具 README）。
2. 夹具 A 的回归测试用 `getattr` 探测 C1 的新入口：改前探测不到 → 快照不保留 → included=3（红）；
   改后保留 → included=4（绿），保证"红/绿"都是**行为**差异而不是导入错误。
