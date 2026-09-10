# B1 重训 Runbook 与复核脚本（2026-09-10）

对象：`docs/plans/valorant-broadcast-inpoint-workstream-20260910.md` 的 **B1**（补"回放"样本 / 纠正标签后重训）。
本文只给**可执行命令 + 判读标准 + 当前已知阻塞**；训练本身需在有 GPU 的环境执行。

---

## 0. 为什么这次重训可能一举生效（因果已改写）

2026-09-10 全量扫描（2512 帧，整帧 OCR）发现：

| 结果 | 数量 |
| :--- | ---: |
| 含字面 `REPLAY` 水印、但标签**不是** `replay` 的帧 | **455**（`non_game` 454 + `combat` 1） |
| 命中置信度 | **全部 ≥0.99**（中位 0.992） |
| 其中文件名自带 `replay_boost` / `rarex` | **360 / 375** |

即 `train/non_game` 原有 1061 帧里约 **43%** 其实是带 REPLAY 水印的回放帧。

**原判断**（根因文档 §1.3 与 B1 条目）：模型分不清回放，因为"训练集**缺**该类样本"。
**修正后**：训练集里本来就有 454 帧，只是被标成了 `non_game`——模型把水印确证回放判成
`non_game`，**是学对了标签**。故 B1 从"从零补一类"变为"**纠正已有 454 帧标签后重训**"。

标签已于 2026-09-10 改定（455 帧搬入 `replay`，回滚单：`relabel_rollback.jsonl`）：

| 目录 | 改前 | 改后 |
| :--- | ---: | ---: |
| `train/non_game` | 1061 | **610** |
| `train/replay` | 1135 | **1587** |
| `val/non_game` / `val/replay` | 170 / 27 | **170 / 30** |
| `test/replay`（水印确证，人工确认） | 0 | **32** |

---

## 1. 准备：核对数据集一致性（零风险）

```bash
# 确认没有"文件存在但 manifest label 与目录不符"的残留（应为 0）
python - <<'EOF'
import json, pathlib
rows = [json.loads(l) for l in open('scripts/valorant_vision/manifest_broadcast.jsonl', encoding='utf-8') if l.strip()]
bad = [r for r in rows if (p := pathlib.Path(r.get('frame_path') or '')) and p.is_file()
       and str(r.get('label')) != p.parent.name]
print('label/目录不一致:', len(bad))
EOF
```

---

## 2. 训练（增量蒸馏微调）

> ⚠️ **本节已被 2026-09-10 的实跑修正**——下面这条命令**不能直接照跑**，
> 会同时踩两个坑（详见 `docs/reports/valorant-broadcast-b1-retrain-result-20260910.md` §1）：
>
> 1. **导出元数据会丢掉运行时契约**：`train_onnx_finetune.py` 原实现写出的
>    `valorant_phase_v1.json` 不含教师的 `broadcast_input_fusion`（0.7/0.3）与
>    `class_stable_prob`（`{"replay":0.77}`）→ 候选在运行时**静默退回"无融合 + 默认阈值"**，
>    与基线**不可比**。→ 已加 `_inherit_runtime_meta()` 修好（脚本会自动继承并打印）。
> 2. **`--new-manifest` 会让蒸馏项反向拉扯刚纠正的标签**：教师在被纠正的 455 帧上
>    **454/455 判 `non_game`、平均 p(non_game)=0.983**，而 `--new-manifest` 给这些帧
>    `distill_weight=0.5` → KL 项把标签**往回拉**。→ 必须用 `--hard-manifest`
>    把这批帧的 `hard_distill_weight` 设为 **0.0**。
>
> 另注：`manifest_broadcast.jsonl` **没有** `label_source`/`coarse_confidence` 字段，
> 故 `--new-manifest` 下所有帧都落在 `pseudo_sample_weight(0.0)=0.05` 这一档，
> **只有相对权重有意义**（CE 是加权平均）。

```bash
# ① 先生成两份清单（uniform 基线权重 + 455 帧纠正样本 distill=0 + 教师错误证据）
python scripts/valorant_vision/build_retrain_manifests.py --hard-weight 1.0 --date-tag 20260910_k1

# ② 再训练
python scripts/valorant_vision/train_onnx_finetune.py \
  --data-dir datasets/valorant_phase_broadcast \
  --new-manifest scripts/valorant_vision/manifest_broadcast_retrain_20260910_k1.jsonl \
  --hard-manifest scripts/valorant_vision/manifest_broadcast_relabel_hard_20260910_k1.jsonl \
  --teacher-dir lsc/analyzer/models/valorant_phase_broadcast_finetune_v4_fused_20260907 \
  --out-dir   C:/lsc_models/broadcast_retrain_k1_20260910 \
  --teacher-cache C:/lsc_models/teacher_cache_broadcast_retrain_20260910.json \
  --epochs 10 --seed 20260907
```

- `--teacher-dir` 指向**当前广播档模型**（蒸馏教师）。
- 训练样本来自 `data_dir/{train}/{类}/*.jpg` → **目录即标签**，故标签纠正直接生效；
  `--new-manifest`/`--hard-manifest` 只影响**样本权重**。
- **`hard_weight` 不是杠杆**（实测 1 与 4 几乎无差）；权重口径应按**唯一源帧**算
  （`train/replay` 1587 帧 = 158 唯一源，10.04×；被纠正的 455 帧 = 35 唯一源）。
- 输出：`<out-dir>/valorant_phase_v1.onnx` + `.json`。
- 备选：`train_export.py --data-dir ... --out-dir ...`（非蒸馏路线）。

**实跑结果（2026-09-10）**：`val` Macro F1 **0.8935 → 0.7821**、`combat` 召回
0.9490 → 0.7937、`replay` 精确率 1.0000 → 0.5000；`test` replay 召回 0.3438 → **0.6250**。
**净收益为负，五项帧级门禁全不过** → 见结果报告 §4–§6。

---

## 3. 复核（复用官方口径 + 补上官方原先无覆盖的测试集）

```bash
python scripts/valorant_vision/reeval_replay_verified.py \
  --candidate-dir <上文 out-dir> \
  --baseline-dir lsc/analyzer/models/valorant_phase_broadcast_finetune_v4_fused_20260907 \
  --manifest scripts/valorant_vision/manifest_broadcast.jsonl \
  --json docs/reports/reeval_replay_verified_20260910.json
```

脚本行为：对 `val` 与 `test` 两个 split，各跑**基线 vs 候选**，输出 5 项帧级门禁的对照表，
并单列 `replay` 类的 precision/recall/support。它**复用** `eval_source_dataset.evaluate()`
（`--mode broadcast_runtime`），因此口径与官方晋级完全一致。

### 3.1 基线（2026-09-10 实测，当前广播模型）

| 指标 | val（官方口径） | test（水印确证回放） |
| :--- | ---: | ---: |
| Macro F1（门禁 ≥0.94） | 0.8935 ❌ | 0.5336 ❌ |
| **Replay Recall（门禁 ≥0.95）** | 0.900 ❌ | **0.3438 ❌** |
| Non-Game Recall（门禁 ≥0.95） | 0.8882 ❌ | 1.0000 ✅ |
| Buy Precision（门禁 ≥0.97） | 0.9835 ✅ | 1.0000 ✅ |
| Result Precision（门禁 ≥0.97） | 0.8889 ❌ | 0.8000 ❌ |
| replay support | 30 | 32 |
| 来源会话数（晋级需 ≥3） | **2** ❌ | 1 |

> 注意：当前广播模型 **5 项帧级门禁有 4 项不合格**，且来源会话数不足——
> 它自身也不是"已晋级"状态（元数据 `promotion_state: null`，故分类器不做门禁校验）。

### 3.2 判读标准（2026-09-10 实跑后修订）

- **首要**：`test` 的 **Replay Recall 从 0.3438 显著上升**（这是标签纠正是否奏效的直接证据）；
- ~~次要：`val` 的 Replay Recall / Non-Game Recall 不得因搬运样本而下降~~
  → **改为硬停止条件：`val` Macro F1 不得退化**。原表述只盯 replay / non_game 两类召回，
  而本次实跑恰恰是**这两类召回一模一样（0.9000 / 0.8882）**、代价全在 **`combat` 召回**
  （0.9490 → 0.7937）与由此而来的 **Macro F1 −0.1114** —— 按原标准会被误判为"达标"。
- 目标：`val` 五项全过（Macro F1 ≥0.94、Replay/Non-game Recall ≥0.95、Buy/Result Precision ≥0.97）。

**必做的两个对照（本次经验）**：
1. **不换标签的对照**（同配方/同权重/同关蒸馏，仅 455 帧保持原标签）——否则无法区分
   "配方问题"与"标签问题"。本次对照 ≈ baseline（0.8838 vs 0.8935），据此才敢把归因钉在标签上；
2. **基线同口径取值**：runbook 里的 0.8935 是 `broadcast_runtime` 口径，而训练脚本内部的
   `_metrics` 是**纯整帧 argmax** 口径（基线同口径 = **0.8950**）。两者混用会把口径差
   误读成掉点/涨点。

实测终表（官方 `broadcast_runtime` 口径）：

| 模型 | split | Macro F1 | replay 召回 | replay 精确 | combat 召回 |
| :--- | :--- | ---: | ---: | ---: | ---: |
| baseline | val | **0.8935** | 0.9000 | 1.0000 | 0.9490 |
| 对照（不换标签，同配方） | val | 0.8838 | 0.9000 | 0.9643 | 0.9612 |
| 重训（`hard_weight=1`） | val | 0.7821 | 0.9000 | 0.5000 | 0.7937 |
| 重训（`hard_weight=4`） | val | 0.7771 | 0.9000 | 0.4426 | 0.7864 |
| baseline | test | 0.5336 | 0.3438 | 1.0000 | 0.3125 |
| 对照（不换标签，同配方） | test | 0.5491 | 0.4062 | 1.0000 | 0.3125 |
| 重训（`hard_weight=1`） | test | 0.5340 | **0.6250** | 0.8333 | 0.1875 |
| 重训（`hard_weight=4`） | test | 0.5158 | **0.6250** | 0.8333 | 0.1875 |

> 读法：`test` 召回的 **+0.0624** 来自纯微调（对照也涨），标签纠正的**净增量**是
> **0.4062 → 0.6250**；但 `val` 一侧是实打实的退化。**结论见结果报告 §6：先改输入契约（B4），
> 在此之前不要用该数据集重训广播档模型。**

---

## 4. 晋级：**口径分两套**（2026-09-10 按决策 (A) 改写）

### 4.1 关键结论：现有门禁是**两来源**门禁，只适用于通用/POV 系模型

代码实测（非文档转述）：

- `eval_gates.SOURCE_TYPES = ("broadcast", "pov")`，并对**每个**来源分别要求
  `by_source_type[st].macro_f1 >= 0.94`（缺该来源评估块 → `{st}_missing` 直接失败）；
- `promote_model.promotion_failures()` 要求
  `source_session_count >= 3` **且** `source_sessions_by_type["broadcast"] >= 3`
  **且** `source_sessions_by_type["pov"] >= 3`；
- 还要求 `class_support` 五类齐全、`evaluation_mode == "broadcast_runtime"`、
  `gate_failures` 为空、候选 ONNX SHA-256 与生产目录一致。

→ 这套门禁**按设计面向"广播+POV 双来源通用模型"**（生产根目录
`lsc/analyzer/models/valorant_phase_v1.*`）。而本工作流要重训的是**广播档模型**
（带 `full 0.7 + top_HUD 0.3` 融合、独立目录）——把它放到含 POV 帧的合并集上评，
Macro F1 仅 **0.7635**（它没在 POV 上训练过）。

**因此：广播档模型不能走 `promote_model.py`**，需要一套尚未定义的广播档晋级口径（见 4.3）。

### 4.2 通用模型（若要晋级）——命令与前置已就绪

```bash
# ① 生成 promotion report（必须带 --manifest 才有来源会话；--rounds 供回合级门禁）
python scripts/valorant_vision/eval_source_dataset.py   --model-dir <候选模型目录>   --data-dir datasets/valorant_phase   --split val --mode broadcast_runtime   --manifest scripts/valorant_vision/manifest_phase_combined.jsonl   --rounds <完整录像回合报告 json>   --output <report.json>

# ② 激活（失败保持当前模型不变并返回非零退出码）
python scripts/valorant_vision/promote_model.py   --candidate-dir <候选模型目录>   --production-dir lsc/analyzer/models   --promotion-report <report.json>
```

**本轮已把两个前置障碍清掉**（实测）：

| 项 | 之前 | 现在 |
| :--- | :--- | :--- |
| 合并集溯源清单 | **不存在**（合并集用前缀式命名 `ann_broadcast_<会话>_<ts>`，与既有清单的后缀式命名 basename 完全不重叠 → 直接评估得到 `source_session_count = 0`） | **`scripts/valorant_vision/manifest_phase_combined.jsonl`**（7875 条，从文件名派生；未归属帧仅 247 个且全在 train） |
| `pov` 来源会话数 | val 只有 **2**（`ling_*` / `tangqihua_*`） | **3**：新增 `fish_live`（111 帧，取自 `valorant_phase_pov/test/`，**复制**而非移动，原处保持完整；该会话不在任何 train 中，无泄漏） |

官方脚本实测结果：`source_session_count: 6`、`source_sessions_by_type: {'pov': 3, 'broadcast': 3}`
、`class_support` 五类齐全 → **会话相关门禁全部 PASS**。

**剩余阻塞**（与来源会话无关）：

1. **帧级指标未达标**：`macro_f1`、`buy_precision`、`result_precision`、`replay_recall`、
   `non_game_recall`，以及 `broadcast_macro_f1` / `pov_macro_f1` 两条分来源门；
2. **回合级报告缺失**：`check_all_gates` 在 `rounds=None` 时给 `rounds_missing` 失败
   （见 `eval_source_dataset.py --rounds` 与 `eval_gates.compute_round_report`）。

### 4.3 广播档模型（B1 的目标）——晋级口径**待定义**

建议的广播档口径（与现有门禁同构，但只保留 broadcast 一侧）：

1. 帧级五项门禁在**广播专用、≥3 个广播来源会话**的评估集上判定
   （现有 broadcast 侧已有 3 个会话：`hanghang_20260721` / `valorant_esports_20260721` /
   `yuezi_20260720_202557`）；
2. **`test/replay`（32 帧水印确证）单独设门**——这是"模型能否认出回放"的唯一直接证据，
   而它在 2026-09-10 之前**完全没有测试覆盖**（`test/replay` 为 0 帧）；
3. 保留回合级门禁；
4. 实现方式二选一：
   - **(i)** 给 `eval_gates` / `promote_model` 增加 `--source-scope broadcast`（更正规，动官方脚本）；
   - **(ii)** 广播档不纳入 `promote_model`，以本文 §3 的 `reeval_replay_verified.py`
     输出作为验收凭据（零改动，但缺"激活/回滚"的机制）。

**在 4.3 定下来之前，广播档模型只能"训练 + 复核"，不能"晋级"**；但 §2–§3 不受影响。

### 4.4 分类器加载契约（与晋级无关）

仅当元数据 `promotion_state == "active"` 时才要求 `gate_results.gates_passed is True`；
否则（如当前广播档模型为 `null`）正常加载 —— 因此**重训出的模型可直接用于复核**，
无需先过门禁。

---

## 5. 本轮未做（需你侧执行）

- **训练本身未执行**：需 torch + GPU 环境；本会话只完成"数据集纠正 + 复核脚本 + 基线取值"。
- 训练后请把 `--json` 产物回填到
  `docs/plans/valorant-broadcast-inpoint-workstream-20260910.md` 的 B 附注，
  并据其结果决定 **B2**（阈值重推导）——B2 必须在本测试集上重新推导，不可沿用 §1.5 的
  val 集结论（§1.5 的"0.70→召回 100%"在真实回放上仅 8.3%）。

---

## 6. 补"第三来源会话"——候选清单与实测缺口（2026-09-10）

`promote_model.py` 对**被评估 split 的 `data_summary`** 的硬要求：

```python
source_session_count >= 3
source_sessions_by_type["broadcast"] >= 3   且   source_sessions_by_type["pov"] >= 3
class_support 必须五类齐全（non_game/buy/combat/result/replay）
```

### 6.1 实测缺口（用合并数据集 `datasets/valorant_phase`）

来源与会话从**文件名**派生（该数据集命名法为 `ann_broadcast_<会话>_<ts>.jpg` /
`bc_broadcast_…` / `ann_pov_…`；与 `manifest_broadcast.jsonl` 的**后缀式**命名
（`<会话>_<ts>_ann_broadcast_<会话>_<ts>.jpg`）**完全不重叠**，故既有清单对合并集
**不提供任何溯源**——这一点本身是个坑，见 6.3）。

| 来源 | val 现有会话 | 是否达标（≥3） |
| :--- | :--- | :--- |
| `broadcast` | `hanghang_20260721` / `valorant_esports_20260721` / `yuezi_20260720_202557` = **3** | ✅ 达标 |
| `pov` | `ling_20260720_134749`(187 帧) / `tangqihua_20260721_141301`(32 帧) = **2** | ❌ **差 1 个** |

**即：只差一个 POV 来源会话**（broadcast 侧已经够了）。

### 6.2 候选（POV 会话清单，来自 `manifest_pov.jsonl`，共 4 个）

| 会话 | 帧数 | 当前位置 | 可用性判断 |
| :--- | ---: | :--- | :--- |
| `ling_20260720_134749` | 2771 | `valorant_phase_pov/train/` | 已用于合并集 val |
| `tangqihua_20260721_141301` | 169 | `valorant_phase_pov/train/` | 已用于合并集 val |
| **`fish_live`** | **111** | **`valorant_phase_pov/` 的 `test/` 分片** | ⭐ **首选**：训练从未使用（天然留出），无泄漏风险 |
| `hard_pov_mined` | 325 | `valorant_phase_pov/train/`（non_game 168 / combat 153 / buy 4） | 次选：需从训练集**搬走**（复制会造成 train/val 泄漏） |

**建议：取 `fish_live`（111 帧）**，从 `valorant_phase_pov/test/{类}/` 搬入
`datasets/valorant_phase/val/{类}/`，并在清单中标注 `source_type: pov`、
`session_id: fish_live`。搬动量小（111 帧）、来源本就留出、无需改动 pov 训练集。

### 6.3 必须先解决的前置问题：合并集**没有可用的溯源清单**

`evaluate()` 的 `source_type`/`session_id` 只来自 `--manifest`
（`_load_manifest_index` 按**完整路径与 basename 双键**）。而合并集的文件名与既有两份清单
不重叠 → 直接在其上评估会得到 `source_session_count = 0`（本会话已实测复现）。

**解法（二选一）**：

1. **生成合并集专用清单**（推荐）：从文件名派生
   `ann_|bc_ + broadcast|pov + <会话>` → 输出
   `scripts/valorant_vision/manifest_phase_combined.jsonl`（逐帧含
   `frame_path/label/split/source_type/session_id`），再追加 `fish_live` 的搬入条目；
   评估时 `--data-dir datasets/valorant_phase --manifest <该清单>`。
2. 扩展 `evaluate()` 支持"无清单时从文件名派生溯源"——改动面更大，且影响官方脚本，
   不如方案 1 干净。

### 6.4 ⚠️ 一个需要你拍板的语义问题

本工作流要重训的是**广播档模型**（带 `full 0.7 + top_HUD 0.3` 融合、目录
`valorant_phase_broadcast_finetune_v4_fused_*`），而晋级门禁却要求
**`pov` 来源会话 ≥3**。实测把该广播模型放在含 POV 帧的合并集上评，Macro F1 只有
**0.7635**（它在 POV 上本就没训练过）。

所以二者必居其一：

- **(A) 门禁本意针对通用/POV 系模型**（生产根目录 `lsc/analyzer/models/valorant_phase_v1.*`）
  → 那么广播档模型需要**另一套**（尚未定义的）晋级口径，本 runbook 第 4 节需改写；
- **(B) 门禁确实要求模型对两种来源都稳健** → 那广播档模型也必须纳入 POV 训练数据，
  重训范围要扩大。

**在 (A)/(B) 定下来之前，第 4 节"晋级"这条路对广播档模型是不可用的**（帧级门禁 + 来源会话
双未达标）。但**第 2–3 节（重训 + 复核）不受影响**，仍可先跑出 `replay` 召回的变化。

---

## 7. 实跑记录（2026-09-10 执行完毕）

第 2–3 节**已实跑四次**（baseline / 不换标签对照 / `hard_weight=1` / `hard_weight=4`），
结论与全部证据见 **`docs/reports/valorant-broadcast-b1-retrain-result-20260910.md`**。

**要点（详细推导见该报告）**：

| 问题 | 结论 |
| :--- | :--- |
| 训练链路能跑通吗 | ✅ 能。onnx2torch → 微调 → 再导出 ONNX **往返数值忠实**（argmax 32/32 一致、概率行和恰为 1）；RTX 3060 Laptop 6GB 上 10 epoch ≈ 20 分钟 |
| B1 首要判据（`test` replay 召回↑） | ✅ **0.3438 → 0.6250**（其中纯微调贡献到 0.4062，标签纠正净增到 0.6250） |
| 代价 | ❌ `val` Macro F1 **0.8935 → 0.7821**、`combat` 召回 **0.9490 → 0.7937**、`replay` 精确 **1.0000 → 0.5000**；五项帧级门禁全不过 |
| 是配方问题还是标签问题 | **标签问题**（单变量对照：不换标签的同配方对照 = 0.8838 ≈ baseline 0.8935） |
| 调 `hard_weight` 有用吗 | ❌ 1 与 4 几乎无差（0.7986 vs 0.7934 纯整帧口径） |
| B2（阈值重推导）能补救吗 | ❌ 不能。门扫到 0.98 时 `val` Macro F1 只回到 0.8274，`combat` 召回**一直是 0.79 不动**（降级成 `unknown` 在召回里同样算错），且 `test` 真实回放召回反掉到 0.5312 |
| 根因 | 标记在 224×224 下只有 **≈20×8 px**（右上角风格）/ **≈21×14 px**（右下角风格），整帧里只占 **8%** 的边缘能量；纠正后的样本只有 **35 个唯一源帧** → 模型改抓**内容/会话近路**，把交战画面判成 `replay` |
| 下一步 | **先改输入契约**（标记 ROI 独立支路 = B4）；或生产上直接用**已有的 OCR 判据**（两种风格都已实测可读，72/72、≥0.99）。**在此之前不要重训/挂载广播档模型** |

**产物**（仓库外）：`C:/lsc_models/broadcast_retrain_20260910`（k4）、
`..._k1_20260910`（k1）、`..._control_20260910`（对照）；
对照集硬链接 `D:/lsc_models/control_broadcast/`（精确复现纠正前分布）。

**§6.4 的语义问题（A)/(B) 仍未定**，且**不因本次实跑而解除**：
即使门禁口径问题解决了，本次重训的模型也**过不了帧级门禁**（含 `combat` 侧退化）。

---

## 8. ⚠️ 本 runbook 的路线已被取代（2026-09-11）：改标签重训无效，正解是「标记 ROI 支路」

第 2 节的"纠正标签后重训"**已实跑并证明是负收益**（见 §7），根因也不是它想解决的那个。
**不要再按第 2 节重训主模型**。正解与完整证据见
`docs/reports/valorant-broadcast-marker-roi-branch-20260911.md`。

### 8.1 为什么是负收益

| 模型 | val Macro F1 | val replay 召回 | val replay 精确 | combat 召回 | test replay 召回 |
| :--- | ---: | ---: | ---: | ---: | ---: |
| baseline | 0.8935 | 0.9000 | 1.0000 | 0.9490 | 0.3438 |
| 纠正标签后重训 | 0.7821 | 0.9000 | 0.5000 | **0.7937** | 0.6250 |

单变量对照（**不换标签**、同配方/同权重/同关蒸馏）≈ baseline（0.8838）→ 掉点来自标签纠正本身；
而纠正标签之所以训不动，是因为**标记在模型输入尺度下几乎不可见**：整帧压到 224×224 后
标记只剩 ≈20×8 px、仅占 **8%** 的边缘能量；把标记区按原生分辨率裁出放大后喂给现有模型，
`p_replay` 只有 **0.002**（比无标记对照 0.10 还低）——**信号没进模型**。

### 8.2 正解（已达标）

在模型元数据里声明 `marker_roi_branch`，把标记区按原生分辨率裁成**独立输入支路**，
回放证据取 `max(整帧, 各 ROI 最大值)`；**主模型一个字不改**（这正是零附带损伤的来源）。

| 要求 | baseline | 标记支路 | 判定 |
| :--- | ---: | ---: | :--- |
| `test/replay` 召回（≥0.95） | 0.3438 | **0.9688** | ✅ |
| `test/replay` 精确 | 1.0000 | **1.0000** | ✅ |
| `val` Macro F1（不得退化） | 0.8935 | **0.9054** | ✅ +1.2pp |
| `val` 逐类（combat / non_game / buy / result） | — | 与基线**逐位相同** | ✅ |

生产/复核用的命令见该报告 §7；工具链：
`build_marker_roi_dataset.py` → `mine_marker_roi_from_videos.py`（可对任意录像/B站素材扩样）
→ `merge_marker_roi_datasets.py` → `train_onnx_finetune.py --no-flip`（关水平翻转，否则字形被镜像）
→ `compose_marker_roi_model.py` → `compare_models_official.py` → `verify_broadcast_replay.py`（直播端到端验收）。

### 8.3 沿用有效的两条

- §1（数据集一致性核对）与 §3（复核脚本/口径）**仍然有效**，且 §3.2 的
  "**`val` Macro F1 不得退化**"硬停止条件已被证明是必要的——若只盯 replay 召回，
  会把这个负收益的模型判成"达标"；
- §1.1/§1.2 记的两个"静默走偏"的坑（导出元数据丢运行时契约、蒸馏反向拉扯）
  在**任何**微调场景下都成立，仍然必须防。

### 8.4 关于"补样本"的方向性修正

- 补**标记位置**要覆盖全：实测有**四种**互斥位置（右上角 / 顶部居中 / 顶部偏左小字 / 右下角），
  少一个就会整段素材命中 0（2026 进化者杯的"顶部居中"就曾整段 1200 个裁剪全负）；
- 补**会话**比补**帧数**重要：只加支路时 val 的 7 个误报**全部来自同一个会话**，
  加 12 个本地录像后全部消失；`val`/`test` 分片本身无重复，但 `train/replay` 有
  **10.04× 的过采样副本**（1587 帧 = 158 唯一源），权重口径应按唯一源帧算；
- 标注口径必须是**逐裁剪 OCR**；按"帧标签"给每一路裁图打标会让约一半正样本里根本没有标记。

