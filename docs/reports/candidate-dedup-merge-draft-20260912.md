# 草案：候选去重/合并（含更上游的「跨回合候选」根因）

> 状态：**L1（区间内边界自检）+ L2（定稿后组内择一）已落地（2026-09-12）**；L3（导出侧原因可辨）仍是草案。证据：`docs/reports/candidate-overlap-evidence-20260912.json`（含 1fps 标签时间线）。
> 前序：`finalize-round-scoping-draft-20260912.md`（Part 1 已落地）。

## 1. 结论先行

1. **重叠的成因不是「两条独立回合相撞」，而是 `round-000123` 本身是跨回合候选。**
   它的定稿区间 `[1232, 1420.75]` **内部含一个完整回合边界**：起点落在回合 A 内部
   （A 起点约 1223，1232 只是它的第 9 秒），出点却是回合 B 的结束（1420.75）。
2. 因此 **「去重」只是兜底，根因在上游：定稿阶段没有校验区间内部是否含回合边界。**
   仅做去重会掩盖问题（把跨回合片段当成"重复项"丢掉，而不是阻止它产生）。
3. 另有一个**待定位**缺陷：在**生产窗口**下 123 得到 `manual_review / no_exclusion_evidence`
   （`scan_end=1382`），而它区间内 `1315–1342` 有 **28 秒连续**的 `result / non_game / replay`
   终态证据——审计没有采用；宽窗口下又跳过这个较早的边界、取了更晚的 1420.75。

## 2. 回合结构（1fps 实测，只读）

| 回合 | 起点（约） | 终点（约） | 终点证据（逐秒标签） |
|---|---|---|---|
| A | 1223（1216-1221 buy → 1223 combat） | **1315** | 1315-1319 result / 1320-1330 non_game / 1331-1342 replay / 1343-1344 non_game / 1345-1349 buy |
| B | **1350**（1345-1349 buy → 1350 combat） | **1420.75** | 1421-1422 result / 1423-1434 non_game / 1436-1441 replay / 1444-1456 buy → 1457 起下回合 combat |

对照两条候选的定稿区间：

| 候选 | 定稿区间 | 与真实回合的关系 | 判定 |
|---|---|---|---|
| round-000123 | `[1232, 1420.75]` | 起点在回合 A 内（A 未结束）→ 出点是回合 B 的结束 | ❌ **跨回合**（内部含 A 的结束 1315→1349） |
| round-000135 | `[1352, 1423.25]` | 与回合 B 的起止吻合，内部无边界 | ✅ 干净的一回合 |

⇒ 所谓「重叠 68.75s」= **同一条回合 B 被认领两次**（123 的尾巴 + 135 全体）。
同类形态另有 `round-000070`（702.9–746.8，已定稿）与 `round-000071`（702.9–733.7，`END_NOT_FINAL`）：
同起点、一长一短，短的是被拆出的子块。

## 3. 现状（代码事实，为什么现有机制没拦住）

| 层 | 机制 | 为什么不覆盖本次问题 |
|---|---|---|
| 导出侧 | `jianying_draft.py` `script.add_segment` 抛 `SegmentOverlap` → 警告「与其它片段重叠，已跳过」；文本轨按时间去重 | 属**最后防线**且在导出时；原因是通用告警，用户无法分辨"重复"还是"跨回合" |
| 候选侧 | `valorant_plugin._candidate_merge_key`（同一次扫掠按粗 key 合）、`room_handler._merge_round_windows` / `_merge_highlights`（跨扫掠合） | 都在**审计之前**、按 **OCR 粗边界**工作；而重叠是**审计扩展出点之后**才产生的（123: 1346→1420.75，135: 1445→1423.25） |

## 4. 建议方案（三层：根因 → 兜底 → 最后防线）

### L1 区间内边界自检（根因层，建议做）

* **规则**：在候选定稿区间 `(start, end)` 内部搜索「combat → 终态（result/non_game/replay）≥K 帧
  → buy → combat」形态，命中即视为**跨回合**。
* **处理**：
  * (a) **裁前缀**：`start` → 该边界之后的第一个 combat 起点（123 会被裁成 `[1350, 1420.75]`）；
  * (b) 若裁剪后时长 < `MIN_ACTIVE_SEC`，或边界距 `end` 过近（如 < 30s），或裁剪距离超过阈值
    （如 > 60s，意味着原起点离真实回合太远）→ **整条拒绝**，原因码 `INTERIOR_BOUNDARY`。
* **依据**：123 内部边界 1315→1349（28s 终态 + buy）；135 内部无边界（不误伤）。
* **成本**：复用审计已有的 1fps `samples`，**无需额外解码**。
* **与 Part 1 的关系**：Part 1 修的是「出点被误否决」，本项修的是「起点/区间跨回合」，两者互补。

### L2 定稿后组内择一（兜底层，建议做）

* **归组**：`refined` 区间重叠（IoU ≥ 0.2 或一方包含另一方）。
* **择一优先级**：① 出点定稿且 `end_quality=precise`；② 起点证据更强（`start_confidence`/密扫）；
  ③ 内部无回合边界（L1 结论）；④ 时长更长。
  * 123/135：**135 胜**（起点在回合边界、内部干净）。若 L1 已把 123 裁成 `[1350, 1420.75]`，
    两者几乎重合 → 留一条（等价）。
  * 070/071：**070 胜**（出点定稿）；071 已有 `END_NOT_FINAL`。
* **落点**：与终态投影（`_project_scan_audit_terminals`）同处——**终态投影后、入列前**。
* **不静默消失**：被并者写 `merged_into=<round_key>` + 原因码 `DUPLICATE_ROUND`，导出提示可见。

### L3 导出侧重叠跳过（保留为最后防线）

保留现状，但把 `warnings` 升级为带 `round_key + 区间 + reason_code`（与 v1.0.15 的跳过原因可辨一致）。

## 5. 原因码增补

`INTERIOR_BOUNDARY`（区间跨回合）、`DUPLICATE_ROUND`（被并入他条）——与既有
`END_NOT_FINAL` / `NEVER_AUDITED` / `NO_EXCLUSION_EVIDENCE` / `NOT_IN_AUTHORITY` / `REJECTED` 同族。

## 6. 回归与验收

* **L1 单测（无媒体）**：
  * 合成 samples：一条"区间内含边界"的候选 → `INTERIOR_BOUNDARY`（或裁剪结果断言）；
    一条干净候选 → 不受影响；
  * 两条重叠候选 → 断言择一结果、`merged_into` 与 `DUPLICATE_ROUND`。
* **L2（媒体，本场录像）**：123 → `INTERIOR_BOUNDARY`（裁剪后 `[1350, ~1420.75]`）或被并；
  135 保持 `[1352, 1423.25]`；070 保留、071 仍 `END_NOT_FINAL`；
  **成品草稿条数不得从 4 降到 3**（Part 1 后基线：045/055/070/105）。
* **验收数字**：`included` 集合在 Part 1 后为 `{045,055,070,105}`；若 Part 2 上线，
  123/135 去重后**只多 1 条**（不是 2 条）。

## 7. 待定位与未决

1. **已定位到具体一步（与本草案并列排期的独立缺陷）**：出点搜索在「回合内含回放/结算闪断」的候选上不稳定。
   按审计词表复算 1fps 标签：`_first_stable_exclusion(samples[1232..1372]) = 1276.0`，
   `audit_broadcast_phase_sequence(start=1232, end=1346, scan_end=1382) -> cutoff=1275.75` ——
   **1276 是回合 A 中段的结算/回放闪断（1261-1287），不是真实边界**（A 的结束在 1315，B 的结束在 1420.75）。
   机制：第 288-292 行的「强终态快路径」（`strong_terminal_count>=2 且 run_count>=2`，2 帧高置信即可定边界）
   正好被回合内闪断满足；且命中后**没有"回退到更长游程"的机制**，导致 1315 与 1420.75 都被跳过。
   同一候选三次运行结论互不一致（无结论 / `manual_review` / 1420.75）也印证不稳定。
   **不修它，123 类候选会持续把出点定在闪断处或退化为无结论，仅靠去重无法根治。**
2. **阈值取值**：裁剪上限（60s?）、内部边界的"距 end 过近"阈值（30s?）、IoU 阈值（0.2?）都需实测标定。
3. **不建议**用"起点距回合起点的偏移"作主判据：1232 距 A 起点仅 9s，按偏移会被误判为合法；
   **内部边界**才是稳健判据。

## 8. 边界声明与产物

* 本草案**未落地**（未改代码）。证据与原始时间线见
  `docs/reports/candidate-overlap-evidence-20260912.json`；探针脚本在 `%TEMP%`。
* 与前序交付的关系：Part 1（已落地）让 123/135 能定稿，从而**暴露**本草案的问题；
  L1/L2 落地前，导出侧仍会以"同轨重叠跳过"的方式丢一条（用户可见结果：定稿了但草稿少一条）。

---

## 9. L1 落地记录（2026-09-12）

### 9.1 改动点

| 文件 | 改动 |
|---|---|
| `lsc/analyzer/valorant_broadcast.py` | 新增常量 `INTERIOR_TRIM_MAX_SEC=20.0` / `INTERIOR_TRIM_MAX_FRACTION=0.5`；新增纯函数 `_interior_round_boundary()` 与 `_interior_boundary_verdict()`；在审计产出路径（`_stamp_broadcast_decision` 之后、写缓存之前）挂自检钩子：命中且裁决为 reject ⇒ `broadcast_audit='rejected_interior_boundary'` + `_record_audit_outcome(status='rejected')`；裁决为 trim ⇒ 起点前移到重开战点（`start_by='interior_boundary_trim'`，标 `start_review_required`） |
| `python-backend/handlers/jianying_handlers.py` | 新增 `SKIP_REASON_INTERIOR_BOUNDARY = "INTERIOR_BOUNDARY"`，并在 `_skip_reason_code()` 中**先于**通用 `rejected*` 分支判定（导出提示可辨） |
| `tests/test_valorant_broadcast.py` | 新增 5 条测试（见 9.3） |
| `scripts/valorant_vision/verify_finalize_round_scoping.py` | `core` profile 增加 123 的期望 |

判定规则：区间 `(start, end)` 内出现「终态游程（`result/non_game/replay`）≥ `EXCLUSION_STABLE_FRAMES(4)` 帧 → [buy/unknown] → **combat 重新开战**」即视为内部边界，取**最后一个**重开战点；裁剪量 > 20s 且 > 总时长 50%，或裁剪后剩余 < `MIN_ACTIVE_SEC(10)` ⇒ 拒绝，否则裁剪起点。

### 9.2 验收（全绿）

| 项 | 结果 |
|---|---|
| L1 `pytest tests/test_valorant_broadcast.py -q` | **63 passed**（+5 新增） |
| 全量 `pytest -q` | **1975 passed**，exit 0 |
| `ruff check`（4 个改动/新增文件） | All checks passed |
| L2 `--profile core`（真实录像） | **exit 0 全部达标**：045=514.25 / 055=672.75 / 070=746.813 / 076=rejected / 135=1423.25 precise；123=`manual_review`（见 9.4 第 1 条） |
| **宽窗口实测**（运行时放宽上限，模拟 Part 2） | 123 → **`rejected` / `rejected_interior_boundary`**，日志 `赛事回合区间跨回合拒绝: 1232.0-1420.8, 内部边界后重开战=1350.0, 前缀=118.0s 剩余=70.8s`；045=513.985、135=1423.25 **均无误伤** |

### 9.3 新增测试

1. `test_interior_round_boundary_detected_for_cross_round_candidate`（123 形态 → 返回重开战点）；
2. `test_interior_round_boundary_ignores_tail_exclusion_and_next_round`（135 形态：出点即排除点 + 下一回合在区间**之后** → None，防误伤）；
3. `test_interior_boundary_verdict_policy`（小幅裁剪 / 大面积拒绝 / 剩余过短拒绝）；
4. `test_interior_boundary_check_is_wired_into_audit_emit`（源码守卫：钩子在盖章之后、写缓存之前）；
5. `test_interior_round_boundary_detects_when_window_starts_at_terminal_run`（**首版 bug 的回归**：见 9.4 第 2 条）。

### 9.4 两条关键 nuance（实测得出，必须知道）

1. **生产窗口下 L1 对 123 不触发**：123 的出点根本没被延长（`end=1346`），重开战点 1350 落在区间之外 ⇒ 自检不命中，既有门禁以 `NO_EXCLUSION_EVIDENCE` 拒之。**L1 守护的是「出点被延长到后一回合」的路径**（宽窗口 / 将来任何 end 延长）——这也说明它必须与 Part 2 一起评估，而不是替代它。
2. **审计按"尾部窗口"取样本**：123 的样本从 `1316`（回合 A 的终态游程）开始，**区间内没有前缀 combat**。首版实现要求「先见 combat 才记重开战」，于是永远判不出边界（实测 `interior_resume=null`）；现已去掉该门控，并由第 5 条测试锁住。（诊断脚本在 `%TEMP%`，日志锚点：`SPAN=[1232.0,1420.8]` 从 `1316:result` 起。）

### 9.5 结论调整（对路线图的影响）

* **本夹具的重叠来源已被 L1 消除**：宽窗口下 123 不再变成跨回合切片；生产口径下 123 本就未定稿（既有门禁已拒）。⇒ **L2（组内择一）从"必需"降级为"防御性兜底"**，且本夹具里**没有**「两条都定稿且重叠」的组合（071/123 都在门禁处被拒）——L2 若要做，需要**自造**该形态的回归用例。
* **Part 2（放宽上限）在本夹具上不再是 123 的前提**：123 已被 L1 正确拒绝；Part 2 的动机只剩「区间干净但真实出点超出 +45s」的将来候选（本场未观测到）。

---

## 10. L2 落地记录（2026-09-12）

### 10.1 改动点

| 文件 | 改动 |
|---|---|
| `python-backend/handlers/room_handler.py` | 新增纯函数 `_dedupe_overlapping_rounds()`（归组+择一）与 `_round_dedupe_rank()` / `_round_overlap_ratio()` / `_round_span_sec()` / `_is_finalized_broadcast_round()` / `_round_dedupe_key()`；新增 `_mark_merged_rounds()`（写权威快照 + durable 拒绝账本 + 广播）；在 `_auto_export_highlights()` 的**终态投影之后、列循环之前**挂钩子 |
| `python-backend/handlers/jianying_handlers.py` | 新增 `SKIP_REASON_DUPLICATE_ROUND = "DUPLICATE_ROUND"`（先于通用 `rejected*` 判定） |
| `tests/test_round_overlap_dedup.py`（新） | 11 条测试（自造用例，见 10.2） |

规则：仅对**出点已定稿的 broadcast 切片**归组（`audit=passed` + `end_quality=precise` + `end_by∈{next_prep,broadcast_exclusion}` + 无 end 复核/时长异常 + 非 rejected/refining）；**同 room_id** 且重叠度（交集/较短区间，包含=1.0）≥ 0.2 归为一组（连通分量，允许传递）；组内择一优先级：**起点证据强 → 无内部边界（L1 标记）→ 区间更长 → 起点更早 → round_key**；被并项写 `merged_into` / `duplicate_round` / `merge_reason`，并在 `listed_clips` 标 `confirm_status=rejected` + `broadcast_audit=rejected_duplicate_round`，同时进 `rejected_round_keys`/`rejected_candidates`，并广播 `clip_confirm_status(reason=duplicate_round, merged_into=…)`。

### 10.2 为什么必须"自造用例"

真实夹具里**没有**「两条都定稿且重叠」的组合（071 `END_NOT_FINAL`、123 跨回合，都在门禁处被拒），
所以 L2 的回归只能自造。`tests/test_round_overlap_dedup.py` 覆盖：
① 重叠定稿对留起点证据强者（123↔135 形态）；② 包含关系归组；③ 低于阈值不并；
④ 未定稿邻条不参与；⑤ 跨房间永不并；⑥ 传递链只留一条；⑦ 有内部边界者优先被并（即使更长）；
⑧ 二次运行幂等；⑨ `_mark_merged_rounds` 的标记/账本/广播三件事；⑩ 接线源码守卫；⑪ 原因码映射。

### 10.3 验收（全绿）

| 项 | 结果 |
|---|---|
| `pytest tests/test_round_overlap_dedup.py -q` | **11 passed** |
| 相关套件（6 文件） | **121 passed** |
| 全量 `pytest -q` | **1986 passed**（+11），exit 0 |
| `ruff check`（L2 相关 5 文件） | All checks passed |
| L2 媒体级 `--profile core`（真实录像） | **exit 0 且逐条与改前一致**（045=514.25 / 055=672.75 / 070=746.813 / 076=rejected / 135=1423.25 precise）——L2 未干扰审计口径 |

### 10.4 局限（必须知道）

1. **集成级只覆盖到 `_mark_merged_rounds`**：`_auto_export_highlights` 是 `register_room_handlers` 内的闭包，
   媒体验收脚本走的是审计接口（不经 room_handler），因此"整条 room_handler 路径 + 广播 + 前端列表"仍**没有**端到端用例
   ⇒ 需要 L3 真实环境跑一轮确认（计划 §五 的 L3 项）。
2. **仅 broadcast 档参与 L2**：POV 无 `broadcast_audit`/`end_quality`，保持既有行为（导出侧同轨重叠跳过仍是最后防线）。这是刻意的范围决策，不是遗漏。
3. 前端对 `clip_confirm_status(reason=duplicate_round)` 的文案展示未做（沿用既有 rejected 处理）；如需"已与 R0x 合并"的提示要单独排期。

### 10.5 顺手发现（与 L2 无关，但属同一文件，提请处理）

`ruff` 对 `room_handler.py` 报 `F821 Undefined name 'delivery_complete'`（现 9235 行）：
收尾判定块里 `delivery_complete = delivery_complete and not bool(...)`，
而**初始化行 `delivery_complete = not bool(_peek_refine_results(state))` 在 HEAD 存在（8494 行）、在当前工作树已丢失**
⇒ 该分支一旦进入会抛 `UnboundLocalError`。这属于在途的 C6 改动引入的回归（不是 L2 引入），
**已修（2026-09-12）**：补回初始化行 `delivery_complete = not bool(_peek_refine_results(state))`
（位置与 HEAD 一致，在 `if _finalization_job is not None:` 之前），并新增
`tests/test_finalize_decision_guards.py` 两道守卫：定向（初始化必须早于自引用；先剥注释行再匹配，
否则说明性注释会骗过 find）+ 全类（`ruff check --select F821` 于 `room_handler.py`，ruff 不可用时 skip）。
双向验证：删掉初始化行 ⇒ 两条守卫**全红**；恢复 ⇒ 全绿。验收：相关套件 48 passed、全量
**1988 passed**、`ruff --select F821` 清零（该文件其余 9 条 ruff 提示为既有导入排序/SIM，未触及）。
