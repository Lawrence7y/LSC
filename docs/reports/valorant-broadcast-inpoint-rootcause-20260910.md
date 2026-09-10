# 持续分析双分支深度分析：官方解说分支「入点不准」根因

分析日期：2026-09-10
范围：`pov`（第一人称）与 `broadcast`（官方解说/二路转播）两条持续分析分支的入点判定链路
方法：源码逐行核对 + 本地模型实测 + 数据集统计 + 前序现场证据交叉验证

> **📌 追加修正（同日，见 `replay-vs-nextcombat-experiment-20260910.md`）**
>
> 在真实录像上做完只读对照实验后，本文档有两处需要修正/加强：
>
> 1. **R4 的定位需上移为"模型能力缺陷"**：实验证明模型的 `replay` 类**只对"带 REPLAY 水印/转场 UI 的回放"有效**；
>    当官方流**直接播实战镜头或观察者自由视角**时，模型会**高置信判为 `combat`**
>    （实测 t=348 确凿回放，`p_replay` 仅 **0.001–0.041**）。这比"阈值 0.77 偏高"严重得多——
>    **调阈值救不了这类回放**。因此"加了回放标注入点仍不准"的主因是**训练数据里缺少"回放中的实战镜头"这一类样本**。
> 2. **R5 的表述需精确**：`replay_segments` **确实会写入分析产物**（真实 sidecar 中可见，如 `[[675.067, 686.067]]`），
>    但**没有任何逻辑消费者**——它是"已落盘但无人使用"的死数据，而非"未落盘"。同时实测它有**假阳性**
>    （把实时交战的 68–88s 标为回放），需与模型信号配合使用。
>
> 另新增实证：**`next_combat` 是碎片回合的唯一来源**（真实录像 9 回合中，3 个 19/23/26s 碎片 100% 由 `next_combat` 闭合，
> 而 46–232s 的正常回合全部由 `next_prep`/`broadcast_exclusion` 闭合）→ 支持接入 `broadcast_mode`，
> 但**它只消除碎片、不改善入点精度**，入点仍须走 R2/R3 的修复路线。
>
> **精确化**：`broadcast_mode=True` 抑制的是 **COMBAT 态**的 fresh-clock 假切分
> （`valorant_ocr_rounds.py:455-462`）；**SETTLE 态**的合法 `next_combat` 闭合**仍然保留**
> （`:550`/`:582`）。因此本文 §5.1 P0-① 里"关闭 next_combat 会加重回合合并"的副作用被**高估**，
> 合并风险主要限于"交战中途被回放打断且此后 prep 也漏检"的情形。
>
> **影子模式已实施**：环境变量 `LSC_VALORANT_BROADCAST_MODE_SHADOW=1` 可在**不改变生效结果**的
> 前提下并行产出两种模式的回合差异（摘要 + 累计统计 + INFO 日志），已通过真实录像端到端验证
> （生效列表完全一致）与 24 条源码守卫测试。取数方法与判读规则见
> `replay-vs-nextcombat-experiment-20260910.md` §6。

---

## 0. 结论速览

用户观察成立：**第一人称分支切片质量高，官方解说分支入点不准**。根因**不是**"回放标注训练得不够好"，而是**回放标注根本没有接到入点这条链路上**，同时**为回放设计的 OCR 级防护在正式链路里没被启用**。

按贡献度排序的 5 条根因：

| # | 根因 | 性质 | 证据 |
| :--- | :--- | :--- | :--- |
| **R1** | OCR FSM 的「赛事回放保护」是**未接线的死代码**——`broadcast_mode` 生产链路从不传 `True` | 🔴 缺陷 | `feed()` 默认 `False`（`valorant_ocr_rounds.py:401`）；唯一 `broadcast_mode=True` 出现在 `tests/test_valorant_ocr_rounds.py:159,183` |
| **R2** | 入点证据链 **100% 由 OCR 交战钟驱动**，模型回放标签对入点**零影响**（只用于出点截断） | 🔴 设计缺口 | `refine_valorant_round_boundaries:858` 硬编码 `start_confidence=0.95`；`valorant_broadcast.py` 中 5 处改写 `start` 无一处来自回放证据 |
| **R3** | `start_delta` 是**自洽性**指标（粗扫与密扫之差），不是**准确性**指标 → 两个都错也能评为 "precise" | 🔴 缺陷 | `continuous_finalization.py:187-190` 用 `start_delta <= 3.0` 判 `precise`；两者同源同盲 |
| **R4** | `replay` 类阈值 **0.77 高于模型自身均值 0.760** → 短回放大量漏检 | 🟠 标定错误 | 实测：单帧过阈值率 **42.6%**；5s 回放段检出率仅 **66.5%**；降到 0.70 即 **100%** |
| **R5** | 回放标注只作用于**出点侧**；OCR 层自产的回放标注 `replay_segments` **无任何消费者** | 🟠 死数据 | `valorant_ocr_rounds.py:790` 写入，全仓仅测试断言读取 |

---

## 1. 两分支的真实差异（代码事实）

`source_profile` 是唯一分界（`lsc/analyzer/valorant_profile.py`）。解析优先级：显式选择 > 标题关键词（`_BROADCAST_HINTS`）> 回退 `pov`。

| 维度 | `pov` | `broadcast` | 是否造成入点差异 |
| :--- | :--- | :--- | :--- |
| OCR 顶部条 ROI | 单高度 `_TOP_BAND_RATIO` | 双高度 `(0.12, 0.18)` | 否（仅提升读数鲁棒性） |
| OCR 中央横幅 ROI | 单 ROI | 双 ROI（含更宽候选） | 否 |
| **FSM 判定逻辑** | 相同 | **相同** | ⚠️ **本应有差异，实为相同（R1）** |
| 入点密扫 `_refine_boundary_ts` | 相同实现、相同 `±3s` | 同左 | 否 |
| 视觉分类器 | 整帧模型 | 融合模型（full 0.7 + top_HUD 0.3） | 仅影响**出点**与**门禁** |
| 出点侧回放截断 | 无（不需要） | `_first_stable_exclusion` | 否（只动出点） |
| 入点质量门禁 | `DEFAULT_PRECISE_BOUNDARY_DELTA_SEC = 1.0` | `BROADCAST_PRECISE_BOUNDARY_DELTA_SEC = 3.0` + 强制审计通过 | 是（更严，误判为 coarse） |
| 回放后置标注 | 不执行 | `_annotate_replay`（`ocr_rounds.py:1414-1416`），产物无人消费 | ⚠️ 无效（R5） |

**关键点**：两分支在**决定入点的代码路径上完全一致**；`broadcast` 只多了三道**事后**约束（视觉门禁、出点截断、更严的质量门槛）。这解释了为什么"给 broadcast 单独训练模型"没有改善入点——**模型不参与入点决策**。

---

## 2. 入点的完整证据链（7 步）

```
① OCR 粗扫（1fps）           timer > 45s（BUY_TIMER_MAX_SEC）→ label="combat"
   valorant_ocr_rounds.py:1299 / 1328 / 1332
       ↓  FSM 开回合，起点取 combat_cand_ts（回填）
   _open_combat() :593   start_by="ocr_combat"，start_confidence 初始 0.70
       ↓
② 候选闭合（_close :604）    end_by=next_prep → confirm_status="vision_confirmed"
                            否则 "pending"（next_combat/open_tail/……）
       ↓
③ broadcast 视觉门禁（若 profile=broadcast）
   _start_gate_decision() valorant_broadcast.py:669
       ├─ new_start ≤ start        → 接受原 OCR 起点（:697）
       ├─ new_start > start 且非切块 → 整条拒绝 rejected_no_stable_combat_start（:702）
       └─ new_start > start 且是切块 → 前移到块内首个视觉 combat 游程（:1228）
                                     ⚠️ 此时 start_delta 被显式清空
       ↓
④ 物理密扫 ±3s @5fps（audit 之后执行）
   refine_valorant_round_boundaries() :793 → _refine_boundary_ts(target="combat") :644
       命中条件：交战钟 > 45s 连续 2 帧（_REFINE_WINDOW_SEC=3.0, _REFINE_RUN_FRAMES=2）
       → 写 start_refined / start_delta，**start_confidence 硬编码 0.95**（:858）
       ↓
⑤ 质量裁决
   room_handler._set_boundary_quality() :1527 → continuous_finalization.classify_boundary_quality() :116
   broadcast 判 "precise" 的必要条件（:155-166, :187-210）：
     confirm_status=vision_confirmed ∧ end_by ∉{next_combat,open_tail}
     ∧ broadcast_audit="passed" ∧ reason≠none
     ∧ start_delta、end_delta、start_confidence、end_confidence 全非空
     ∧ start_delta ≤ 3.0 ∧ confidence ≥ 0.8 ∧ boundary_refined
       ↓
⑥ boundary_review_required = (quality != "precise")   room_handler.py:1544
       ↓
⑦ 导出/草稿门禁 + 人工复核 UI
```

**链条的关键性质**：第 ④ 步是唯一给入点提供"物理证据"的环节，而它**只看 OCR 交战钟**。第 ③ 步的视觉模型有能力识别回放，但它对起点只有「接受 / 整条拒绝 / 切块前移」三种动作，**没有"因回放而前移"这一项**。

---

## 3. 根因详述

### R1（最高优先级）：OCR 级回放防护是未接线的死代码

`OcrRoundFSM.feed()` 有专门的 `broadcast_mode` 参数（`:401`，默认 `False`），两处分支实现了设计意图：

```python
# valorant_ocr_rounds.py:455-462
if self._state == _State.COMBAT:
    if label == "combat" and fresh_clock:
        if broadcast_mode:
            _log.info("赛事回放保护：忽略未伴随准备阶段的新交战钟 ...")
            return closed          # ← 忽略回放引起的"新交战钟"
        close = self._close(end=..., end_by="next_combat")   # ← POV 走这里，会切分
```

但**正式链路调用 `feed()` 时不传该参数**：

```python
# valorant_ocr_rounds.py:1365
closed = fsm.feed(label, ts, timer, timer_raw=timer_raw, cand_ts=cand_ts, prep_banner=prep_banner)
```

全仓检索确认：`broadcast_mode=True` **只出现在 `tests/test_valorant_ocr_rounds.py:159,183`**（两条测试专门验证该保护有效）。即：**保护逻辑被测试覆盖，却在生产未启用**。

后果：官方解说流中，回放画面同样带游戏 HUD（顶部计时器 >45s）→ OCR 判 `label="combat"` → FSM 在**回放中**开出新回合，或把回放误当新回合切割 → 入点（含 `combat_cand_ts` 回填）落在回放画面里。

> 现场证据（`docs/reports/valorant-broadcast-runtime-investigation-20260908.md`）：
> **「R27 的切入位于 `REPLAY` 转场之后」「R135 起点仍在买枪/比分板画面」** —— 与上述机制吻合。

### R2：入点与模型完全解耦

- `refine_valorant_round_boundaries:858` 写入 `start_confidence = 0.95`（固定高置信度，注释自述"没有分类器置信度时使用固定高置信度表示该边界由物理密扫确认"）。
- `valorant_broadcast.py` 中所有改写 `start` 的位置：`:746`/`:760`（切块算术）、`:981`（复用缓存精修值）、`:1014`（复用门禁后移值）、`:1228`（门禁后移）。**无一处由回放标签驱动**。
- `_TIMER_OCR_LABELS = {"non_game","buy","result"}`（`:60`）**不含 replay**，即回放帧的计时器 OCR 被主动跳过——但入点密扫（第 ④ 步）是**独立 OCR 调用**，不受该集合约束，照常在回放帧上读到交战钟。

结论：**新训练的回放标注只在"出点侧"被当作截断证据使用；入点侧既不用它前移起点，也不用它否决 OCR 的"精确"结论。**

### R3：`start_delta` 度量的不是准确性

```python
# refine_valorant_round_boundaries:851-858
start_ts = _call_refine(float(r["start"]), "combat")
r["start_refined"] = round(start_ts, 3)
r["start"] = r["start_refined"]
r["start_delta"] = round(abs(r["start_refined"] - start_coarse), 3)   # ← 粗扫与密扫之差
```

`start_delta = |密扫值 − 粗扫值|`，两者**同源**（都来自"顶部交战钟 >45s"）。若粗扫与密扫**双双锚定在回放画面内的交战钟**上，两者高度一致 → `start_delta` 很小 → 第 ⑤ 步评为 **`precise`**、`boundary_review_required=False`。

**即：一个落在回放里的入点，可以被系统盖章为"精确、无需复核"。** 这是"入点判断不清"表现为**静默错误**（而非报错/待确认）的直接原因。

### R4：`replay` 阈值 0.77 高于模型自身均值

模型元数据：`class_stable_prob = {"replay": 0.77}`，`broadcast_input_fusion = {full_frame 0.7, top_hud 0.3}`，标定集 `broadcast-fused-calibration-778v`（仅 778 帧）。

**本地实测**（`val`/`train` 的 replay 与 combat 帧，DmlExecutionProvider）：

| 集合 | n | argmax=replay | 过 0.77 阈值 |
| :--- | ---: | ---: | ---: |
| val/replay | 27 | **100%** | 85.2% |
| train/replay | 400 | **100%** | **47.0%** |
| val/combat | 400 | 1.0%（误判） | 89.5% combat |
| train/combat | 400 | 0% | 95.0% combat |

`replay` 概率分布：**mean 0.760 / p50 0.738 / p90 0.847**。阈值 0.77 卡在均值**之上**。

**阈值敏感性模拟**（用真实概率分布模拟 1fps 回放段，套用产品代码的三段逻辑）：

| 阈值 | 单帧过阈值率 | 5s 回放检出率 | 10s | 20s |
| :--- | ---: | ---: | ---: | ---: |
| **0.77（现状）** | 42.6% | **66.5%** | 89.2% | 99.8% |
| 0.70 | 87.8% | **100%** | 100% | 100% |
| 0.60 | 100% | 100% | 100% | 100% |

即：**≤5 秒的短回放有约 1/3 概率完全漏检**。漏检后出点无法被截断 → 出点拖入回放 → 相邻回合边界互相污染。

**佐证**：`_stable_visual_label`（`:63`）在未过阈值时返回 **`"unknown"`**（不是次优类），而 `_stable_visual_label`→`_first_stable_exclusion` 的终端游程只容忍 `replay_gap_frames < 2` 个 unknown（`:232-244`）。当约 57% 的回放帧变成 unknown 时，游程被频繁打断。

**另注数据质量问题**：`datasets/valorant_phase_broadcast` 的 `train/replay` 有 **1116** 帧，而 `val/replay` 仅 **27**、`test/replay` 为 **0**。**回放类别没有测试集覆盖**，无法在训练流程中回归验证——这与 R4 的标定失误互为因果。

### R5：回放标注只写不读

```python
# valorant_ocr_rounds.py:790（_annotate_replay 内）
round_data["replay_segments"] = segs
```

全仓检索 `replay_segments`：**仅该写入点 + `tests/test_valorant_ocr_rounds.py:875,880` 的断言**。生产无消费者。OCR 层已经能定位"结算后 ≥5s 的 neutral 段=回放"，但这个结论**没有传给入点密扫、也没有传给门禁**。

### 附带发现（同源缺陷）

- **横幅关键词含解说高光词**：`_END_BANNER_KEYWORDS` 含 `"clutch" / "ace" / "triple"`（`:92`）——这些恰是解说流**高光回放叠加字样**，可让回放画面被判为"回合结束"。两张关键词表（`:79-94`）**都不含"回放/replay/重播"**，即回放既不被正面识别，其文本又可能命中结束/准备词。
- **单候选入点无前移机制**：非切块候选一旦 `new_start > start` 就整条拒绝（`:702`）。因此"头部含上一回合回放尾段"的候选，只能在**接受错入点**与**丢失整回合**之间二选一。
- **超长切块 → 系统性 coarse 入点**：`MAX_BROADCAST_ROUND_SEC = 150.0`（`:22`）。多回合合并后的切块，入点取块内首个视觉 combat 游程（1fps，±0.5s 量化），且代码注释明确"不伪造精修 delta"（`:1230-1235`）→ `start_delta=None` → 质量必为 `coarse`。

---

## 4. 影响范围

| 影响面 | 说明 |
| :--- | :--- |
| **切片入点偏移** | 落在回放画面内，或被盖章 `precise` 而不触发人工复核（R2+R3） |
| **短回放漏检** | ≤5s 回放约 1/3 漏检，出点拖尾、相邻回合互相污染（R4） |
| **回合丢失** | 头部含回放尾段的候选被整条拒绝（R1+附带发现） |
| **coarse 泛滥** | 超长切块的入点结构性为 coarse → `broadcast_review_required=True` → 人工复核负担 |
| **模型迭代失灵** | 继续提升 replay 类精度**不会**改善入点（R2），因为入点不消费该信号 |

---

## 5. 改进建议（按优先级）

### P0 — 让回放信号真正进入入点链路

1. **接线 `broadcast_mode`**：`detect_valorant_rounds_ocr` 在 `source_profile=="broadcast"` 时传 `broadcast_mode=True`（`ocr_rounds.py:1365`）。
   ⚠️ **须同时评估副作用**：该模式会忽略 `fresh_clock`，关闭 `next_combat` 切分，可能**加剧回合合并**（→ 超长切块 → coarse 入点）。建议作为**受控实验**开启，对比合并率与入点精度后再定。
2. **入点密扫接入模型回放证据**：`_refine_boundary_ts(target="combat")` 增加"该帧是否被判为 replay"的否决条件——即在回放帧上读到的交战钟**不得**作为入点锚点。这是 R2 的最小改动点。
3. **给"因回放前移"开一条通路**：`_start_gate_decision` 对非切块候选，允许在**前导段为 replay** 时把起点前移到其后的首个稳定 combat 游程（而非整条拒绝），理由是回放属于非游戏内容而非"下一条真回合"，不会与后续 OCR 候选重复。

### P1 — 修正标定与质量裁决

4. **下调 `replay` 阈值**：0.77 → **0.60~0.70**。实测 0.70 即可把 5s 回放检出率从 66.5% 提到 100%，且 combat 误判率仍低（val/combat argmax=replay 仅 1.0%）。建议用 val 集重新标定并补 replay 的 test 集。
5. **`start_delta` 改为交叉证据**：`precise` 不能只靠"粗扫与密扫自洽"，须叠加**视觉证据**（如起点 ±2s 内模型判为 combat 的比例 ≥ 阈值）。否则 keep as coarse。
6. **`start_confidence` 去硬编码**：`:858` 的 `0.95` 应改为基于视觉一致性的实测值。

### P2 — 清理与一致性

7. **消费或删除 `replay_segments`**：把 OCR 层的回放段传给门禁/密扫做排除，或直接删除该死数据。
8. **横幅关键词剔除高光词**：`"clutch"/"ace"/"triple"` 从 `_END_BANNER_KEYWORDS` 移除（它们是解说叠加词，非回合结束信号）；并考虑新增 `"回放"/"replay"/"重播"` 正面识别。
9. **补 replay 测试集**：`test/replay` 当前为 0，训练流程无法回归。

---

## 6. 建议的验收标准

| 项 | 验收方式 |
| :--- | :--- |
| R4 修复 | 用 val+test 的 replay 帧实测：过阈值率 ≥90%，5s 段检出率 ≥95%；同时 combat 误判率（argmax=replay）≤3% |
| R1/R2/R3 修复 | 用已知含前置回放的真实录像跑分析，核对每个 `start_quality="precise"` 的切片：起点帧**不含**回放转场/回放叠加字样；人工抽检 10 条 |
| 无回归 | 现有 `tests/test_valorant_broadcast.py`、`test_valorant_ocr_rounds.py` 全绿；新增"入点不落在回放段"的守卫测试 |

---

## 附：本次分析使用的一手证据

- 源码核对：`lsc/analyzer/valorant_ocr_rounds.py`、`valorant_broadcast.py`、`valorant_frame_classifier.py`、`valorant_profile.py`、`python-backend/continuous_finalization.py`、`python-backend/handlers/room_handler.py`
- 模型实测：`ValorantFrameClassifier(profile="broadcast")`（DmlExecutionProvider）在 `datasets/valorant_phase_broadcast/{val,train}/{replay,combat}` 上的批量推理
- 阈值模拟：以真实帧概率分布模拟 1fps 回放段，套用 `_stable_visual_label` → `_stabilize_broadcast_samples` → `_first_stable_exclusion` 三段产品逻辑
- 现场证据：`docs/reports/valorant-broadcast-runtime-investigation-20260908.md`
