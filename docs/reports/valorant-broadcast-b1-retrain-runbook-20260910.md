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

## 4. 晋级（仅在 3.2 达标后执行）

```bash
# ① 生成 promotion report（必须带 --manifest 才有来源会话；--rounds 供回合级门禁）
python scripts/valorant_vision/eval_source_dataset.py \
  --model-dir <out-dir> \
  --data-dir datasets/valorant_phase_broadcast \
  --split val --mode broadcast_runtime \
  --manifest scripts/valorant_vision/manifest_broadcast.jsonl \
  --rounds <完整录像回合报告 json> \
  --output <report.json>

# ② 激活（失败保持当前模型不变并返回非零退出码）
python scripts/valorant_vision/promote_model.py \
  --candidate-dir <out-dir> \
  --production-dir lsc/analyzer/models/<目标目录> \
  --promotion-report <report.json>
```

`promote_model.py` 的硬要求（代码实测，非文档转述）：

1. `report["gates_passed"] is True`；
2. `data_summary.source_session_count >= 3`，且 `source_sessions_by_type[source] >= 3`；
3. 候选与生产目录的 ONNX **SHA-256 一致**。

分类器的加载契约：仅当元数据 `promotion_state == "active"` 时才要求
`gate_results.gates_passed is True`；否则（如 `null`）正常加载 —— 因此**重训出的模型可直接
用于复核**，无需先过门禁。

### ⚠️ 当前已知阻塞（晋级前必须先解）

1. **来源会话数不足**：`val` 只有 **2** 个来源会话（要求 ≥3）。需补第三个独立来源会话的帧
   （例如另一场赛事的录像）并在 manifest 中标注 `source_type`/`session_id`。
2. **回合级报告缺失**：`check_all_gates` 在 `rounds=None` 时直接给
   `rounds_missing` 失败 → 必须先产出完整录像的回合级 GT/预测 JSON（见
   `eval_source_dataset.py --rounds` 与 `eval_gates.compute_round_report`）。
3. **帧级门禁**：即使标签纠正，`Result Precision`（0.8889）与 `Macro F1`（0.8935）
   也需一并改善，否则 `gates_passed` 仍为 false。

---

## 5. 本轮未做（需你侧执行）

- **训练本身未执行**：需 torch + GPU 环境；本会话只完成"数据集纠正 + 复核脚本 + 基线取值"。
- 训练后请把 `--json` 产物回填到
  `docs/plans/valorant-broadcast-inpoint-workstream-20260910.md` 的 B 附注，
  并据其结果决定 **B2**（阈值重推导）——B2 必须在本测试集上重新推导，不可沿用 §1.5 的
  val 集结论（§1.5 的"0.70→召回 100%"在真实回放上仅 8.3%）。
