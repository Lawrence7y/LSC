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

```bash
python scripts/valorant_vision/train_onnx_finetune.py \
  --data-dir datasets/valorant_phase_broadcast \
  --new-manifest scripts/valorant_vision/manifest_broadcast.jsonl \
  --teacher-dir lsc/analyzer/models/valorant_phase_broadcast_finetune_v4_fused_20260907 \
  --out-dir   <输出目录，如 C:/lsc_models/broadcast_retrain_20260910> \
  --teacher-cache <缓存目录，如 C:/lsc_models/teacher_cache> \
  --epochs 10 --seed 20260907
```

- `--teacher-dir` 指向**当前广播档模型**（蒸馏教师）。
- 训练样本来自 `data_dir/{train}/{类}/*.jpg` → **目录即标签**，故标签纠正直接生效；
  `--new-manifest` 只用于**样本权重**（`human`=2.0 / 伪标按置信度）。
- `--hard-manifest` 可选（难例加权）。
- 输出：`<out-dir>/valorant_phase_v1.onnx` + `.json`。
- 备选：`train_export.py --data-dir ... --out-dir ...`（非蒸馏路线）。

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

### 3.2 判读标准

- **首要**：`test` 的 **Replay Recall 从 0.3438 显著上升**（这是标签纠正是否奏效的直接证据）；
- 次要：`val` 的 Replay Recall / Non-Game Recall 不得因搬运样本而下降；
- 目标：`val` 五项全过（Macro F1 ≥0.94、Replay/Non-game Recall ≥0.95、Buy/Result Precision ≥0.97）。

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
