# B1 重训执行结果（2026-09-10）

对象：`docs/plans/valorant-broadcast-inpoint-workstream-20260910.md` 的 **B1**
（纠正 455 帧"水印确证回放"标签后重训广播档模型）。
配套：`docs/reports/valorant-broadcast-b1-retrain-runbook-20260910.md`（命令与口径）。

**一句话结论**：重训**跑通了**，`test` 水印确证回放的召回确实从 **34.4% 升到 62.5%**，
但代价是 `val` 严重退化（Macro F1 **0.8935 → 0.7821**、`combat` 召回 **0.9490 → 0.7937**、
`replay` 精确率 **1.0000 → 0.5000**），**五项帧级门禁无一通过，且 B2 阈值重推导救不回来**。
单变量对照证明这**不是配方问题，而是"标签纠正 + 当前输入契约"的组合问题**：
模型在 224×224 整帧下**看不到**那个标记，只能改抓画面内容。

---

## 0. 结论摘要

| 项 | 结果 |
| :--- | :--- |
| 重训是否可行（工程链路） | ✅ 通（onnx2torch → 微调 → 再导出 ONNX 往返忠实：argmax 32/32 一致、概率行和恰为 1） |
| B1 的首要判据（`test` replay 召回↑） | ✅ **0.3438 → 0.6250（+28.1pp）** |
| 但代价 | ❌ `val` Macro F1 **−0.1114**、`combat` 召回 **−0.1553**、`replay` 精确率 **−0.5000** |
| 门禁 | ❌ 五项帧级门禁全不过（与基线同）；`test` 的 `non_game` 召回 / `buy` 精确率仍 PASS |
| B2（阈值重推导）能否补救 | ❌ 不能（见 §5） |
| 根因 | 标记在 224×224 下只有 **20×8 px**；纠正后的样本只有 **35 个唯一源帧**且其**画面内容**与非回放帧同类 → 模型学到的是**内容/会话近路**而非标记 |
| 建议 | 先改**输入契约**（标记 ROI 支路 = B4 的一种），或在生产上直接用**已有 OCR 判据**；在此之前**不要**用本数据集重训广播档模型 |

---

## 1. 训练前发现的两个会让重训白做的坑（已修）

### 1.1 导出的元数据会**丢掉运行时契约**（已修脚本）

`train_onnx_finetune.py` 写出的 `valorant_phase_v1.json` **不含**教师模型的
`broadcast_input_fusion`（0.7/0.3）与 `class_stable_prob`（`{"replay": 0.77}`）。
这两个键是**推理侧行为声明**，`ValorantFrameClassifier.predict_broadcast_batch()`
与 `eval_source_dataset` 的 `broadcast_runtime` 口径都读它们。丢了它们的后果：
候选模型在运行时**静默退回"无融合 + 默认阈值"** → 与基线**不可比**（基线带融合，
候选不带），评测出来的差异会混进一个与权重无关的口径差。

→ 已给训练脚本加 `_inherit_runtime_meta()`：从教师 `valorant_phase_v1.json`
继承 `thresholds / class_stable_prob / broadcast_input_fusion / calibration_note`，
并打印实际继承到的键名。单测 `tests/test_train_onnx_finetune.py`（+3）。

### 1.2 `--new-manifest` 会让**蒸馏项反向拉扯**刚纠正的标签（已用 `--hard-manifest` 关掉）

教师 = 当前广播档模型（`..._v4_fused_20260907`）。实测它在**被纠正的 455 帧**上：

| 指标 | 值 |
| :--- | ---: |
| 判成 `non_game` 的帧 | **454 / 455** |
| 平均 `p(non_game)` | **0.983** |
| 平均 `p(replay)` | 0.015 |

而 `--new-manifest` 会给这些帧 `distill_weight = 0.50`，KL 项于是**把标签往回拉**。
→ 生成 `manifest_broadcast_relabel_hard_*.jsonl`（`hard_distill_weight = 0.0`
+ `hard_weight`），并把它固化进清单的 `predicted_label/confidence` 字段作为**可审计证据**。

> 两个坑都不是"训练不收敛"那种显性错误，而是**静默走偏**：前者让评测口径不一致，
> 后者让标签纠正被教师按旧标签抵消。

---

## 2. 数据集的真实结构（此前计划文档未记录）

`rarex<k>_` / `replay_boost<k>_` / `hardx<k>_` 是**增强/过采样副本**标签
（生成方：`build_broadcast_hard_dataset.py`；判定方：`rebuild_source_separated_datasets.py:38`），
**不是内容标签**。剥掉它们后：

| split/class | 帧数 | **唯一源帧** | 放大倍数 |
| :--- | ---: | ---: | ---: |
| `train/replay` | 1587 | **158** | **10.04×** |
| `train/result` | 89 | 25 | 3.56× |
| `train/non_game` | 610 | 314 | 1.94× |
| `train/buy` | 67 | 50 | 1.34× |
| `train/combat` | 511 | 511 | 1.00× |
| `val/*` / `test/*` | — | — | **全部 1.00×**（无重复污染，评估口径干净） |

**对本次重训的两点直接含义**：

1. 被纠正的 455 个文件背后只有 **35 个唯一源帧**（train 32 + val 3），平均 13 个副本/源；
2. 权重口径必须按**唯一源帧**算，不能按文件数算。按每源帧的 CE 权重：
   `hard_weight=4` 时新源帧的**每源帧** CE 权重是既有 replay 源帧的 **6.27 倍**
   （= **42.84%** 的 train CE 权重压在 **32 张唯一图**上），`hard_weight=1` 时是 **1.57 倍**
   （**15.78%**）。`build_retrain_manifests.py` 现已同时报告
   `ce_weight_share_of_relabeled` 与 `relabel_unique_sources` / `train_unique_sources`。

---

## 3. 根因：那 35 个源帧的"回放标记"在模型输入尺度下几乎不可见

OCR 几何实测（原分辨率 1920×1080，整帧 @2x 后取框并折回）：

| 帧组 | REPLAY 位置 | 原字高 | 距画面底部 | **224×224 输入下** |
| :--- | :--- | ---: | ---: | :--- |
| 被纠正的 455 帧 | **右上角** x∈[1688,1859] y∈[22,61] | 38.6px | 1019px | **≈20×8 px** |
| `val/replay`（原有 replay，同风格） | 右上角，**同一位置** | 38.6px | 1019px | ≈20×8 px |
| `test/replay`（水印确证，另一套广播风格） | **右下角** y∈[977,1044]（另有 1 帧居中 269px 大字） | 67.5px | 35.7px | ≈21×14 px |

**关键点**：训练/验证集里的回放标记在**右上角**，而 B5/B2 依赖的"回放水印在底部"结论
来自**另一套广播风格**（`test/replay`）。两者不是同一个东西——B5 的"顶部 HUD 裁剪看不到
水印"对右上角风格**不成立**（y=22..61 落在顶部 34% 裁剪内）。

标记信号强度（224×224 输入，右上角 ROI）：

| 组 | ROI `|Laplacian|` | 整帧 `|Laplacian|` |
| :--- | ---: | ---: |
| 带标记（被纠正帧） | **70.43** | 18.86 |
| 无标记（`train/non_game`） | **23.03** | 17.37 |

标记本身**可测**（ROI 差 3×），但在整帧里只占 **8%** 的边缘能量
（18.86 vs 17.37）——被整幅画面稀释。

**机制**：基线模型的 `replay` 概念本质是**内容**概念（`val/replay` 那些帧的画面本就
"长得像回放"），所以它在 `val` 上 `replay` 召回 0.90；而纠正后的 35 个源帧是
**"画面内容不像回放、只有右上角标记"** 的回放。要让模型改判它们，就必须让模型**学会看标记**；
在 20×8 px + 只有 32 个唯一带标源帧的条件下，它改抓了**内容/会话近路**，于是把**交战画面**
也判成 `replay`。这也解释了为什么基线在真实广播回放（`test/replay`，回放里放的是交战镜头）
上只有 34.4% 召回——它认的是"像回放的内容"，不是回放。

---

## 4. 实验设计：单变量对照锁死归因

| 运行 | 数据 | 标签 | 清单权重 | epochs | best `val` Macro F1（纯整帧口径） |
| :--- | :--- | :--- | :--- | ---: | ---: |
| **baseline**（未训练） | 现数据集 | 已纠正 | — | — | **0.8950** |
| **k4** | 现数据集 | 已纠正 | uniform + `hard_weight=4`、这批 distill=0 | 10 | 0.7934（ep9） |
| **k1** | 现数据集 | 已纠正 | uniform + `hard_weight=1`、这批 distill=0 | 10 | 0.7986（ep8） |
| **control**（对照） | 对照集（455 帧**放回** `non_game`） | **不纠正** | uniform + 同批 distill=0 | 10 | **0.8995**（ep1） |

对照集用硬链接按回滚单重建，精确复现纠正前分布
（`train`：`non_game` 1061 / `combat` 512 / `replay` 1135；`val`：173 / 412 / 27）。

**读法**：
- **control ≈ baseline（0.8995 vs 0.8950）** → **训练配方没问题**（学习率、类权重、
  KD、epoch 数、onnx2torch 往返都没问题）；
- **k1 ≈ k4（0.7986 vs 0.7934）** → **`hard_weight` 不是杠杆**（4 倍 vs 1 倍几乎无差）；
- 因此掉点**唯一**来自**标签纠正**。

### 4.1 官方口径终表（`eval_source_dataset --mode broadcast_runtime`，可复现脚本见 §7）

| 模型 | split | Macro F1 | replay 召回 | replay 精确 | non_game 召回 | buy 精确 | result 精确 | **combat 召回** |
| :--- | :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| baseline | val | **0.8935** | 0.9000 | **1.0000** | 0.8882 | 0.9835 | 0.8889 | **0.9490** |
| control | val | 0.8838 | 0.9000 | 0.9643 | 0.8824 | 0.9746 | 0.8846 | **0.9612** |
| **k1** | val | 0.7821 | 0.9000 | 0.5000 | 0.8882 | 0.9652 | 0.8000 | **0.7937** |
| **k4** | val | 0.7771 | 0.9000 | 0.4426 | 0.8882 | 0.9652 | 0.8065 | **0.7864** |
| baseline | test | 0.5336 | 0.3438 | 1.0000 | 1.0000 | 1.0000 | 0.8000 | 0.3125 |
| control | test | 0.5491 | 0.4062 | 1.0000 | 1.0000 | 1.0000 | 0.8000 | 0.3125 |
| **k1** | test | 0.5340 | **0.6250** | 0.8333 | 1.0000 | 1.0000 | 0.5714 | 0.1875 |
| **k4** | test | 0.5158 | **0.6250** | 0.8333 | 1.0000 | 1.0000 | 0.4444 | 0.1875 |

**两点必须一起读**：
1. **对照也涨**（`test` replay 召回 0.3438 → 0.4062）——这说明"多微调几轮"本身就能带来
   一部分召回，**不能全记到标签纠正头上**；标签纠正的净增量是 **0.4062 → 0.6250（+21.9pp）**。
2. `val` 的 `replay` 召回三行**都是 0.9000**、`non_game` 召回**都是 0.8882**——纠正标签
   **完全没有**提高模型在 `val` 上的回放识别，只是把 34 个非回放帧判成了 `replay`。

### 4.2 混淆矩阵（val，k4 vs baseline）：代价几乎全在 `combat`

| 真值 → 预测 | baseline | k4 | 变化 |
| :--- | ---: | ---: | ---: |
| `combat → combat` | 391 | **324** | **−67** |
| `combat → replay` | 0 | **31** | +31 |
| `combat → non_game` | 18 | **51** | **+33** |
| `replay → replay` | 27 | 27 | 0 |
| `non_game → non_game` | 151 | 151 | 0 |

`val` 的 `combat` 有 412 帧（占 778 帧的 53%），它的召回塌陷是 Macro F1 掉 0.11 的主因。

---

## 5. B2（阈值重推导）救不回来——已实测

把候选（k1）的 `class_stable_prob["replay"]` 从 0.77 扫到 0.98，
其余一切不变，仍走官方 `broadcast_runtime` 口径：

| replay 门 | split | Macro F1 | replay 精确 | replay 召回 | **combat 召回** | result 精确 |
| ---: | :--- | ---: | ---: | ---: | ---: | ---: |
| 0.77 | val | 0.7821 | 0.5000 | 0.9000 | **0.7937** | 0.8000 |
| 0.85 | val | 0.7999 | 0.6429 | 0.9000 | 0.7961 | 0.8000 |
| 0.90 | val | 0.8146 | 0.7714 | 0.9000 | 0.7961 | 0.8000 |
| 0.95 | val | 0.8248 | 0.8710 | 0.9000 | 0.7985 | 0.8000 |
| **0.98** | val | **0.8274** | 0.9000 | 0.9000 | **0.7985** | 0.8000 |
| 0.98 | test | 0.5187 | 1.0000 | **0.5312** | 0.1875 | 0.5714 |

**为什么救不了（机制）**：门只把低置信度的 `replay` 预测降级成 `unknown`，
而 `unknown` **在召回里同样算错** —— 所以 `combat` 召回**一直是 0.79 不动**
（0.7937 → 0.7985，等于没变），只是 `replay` 精确率好看了。
同时门一收紧，`test` 的真实回放召回**反而从 0.6250 掉到 0.5312**。
**即使取最优门 0.98，`val` Macro F1 = 0.8274 仍远低于基线 0.8935。**

→ **B2 的前提（"模型已经会了，只是阈值不对"）在本例不成立**，应挂起到 §6 建议落地之后。

---

## 6. 建议（按性价比排序）

### 6.1 首选：把"标记 ROI"作为**独立输入支路**（= B4 的一个具体形态）

现有 `predict_broadcast_batch()` 已有一套**双路融合**骨架（整帧 0.7 + 顶部 HUD 0.3，
且 B5 已给 `replay` 做了豁免）。最小改动是**再加一路**：把"右上角 + 右下角标记区"
按**原生分辨率**裁出、缩放到 224×224，作为第二/第三路参与融合。这样标记不再是
整帧里 8% 的边缘能量，而是**整幅输入的 100%**。

- 优点：不动训练集语义、不改标签、可与现有融合/豁免机制共存；
- 代价：需改 `predict_broadcast_batch` 与推理侧的输入构造，并把该 ROI 固化进模型元数据
  （沿用 `broadcast_input_fusion` 这类键，天然可继承——见 §1.1 的机制）；
- 注意：**两种标记位置都要覆盖**（右上角 = 训练/验证风格，右下角 = `test/replay` 风格）。

### 6.2 备选：生产上**不依赖模型**判回放

OCR 对两种风格的标记都已实测可读（72/72 帧、置信度 ≥0.99；本报告 §3 的几何也来自同一通道）。
`REPLAY 标记存在 + 落在两个已知位置区间` 本身就是一条**确定性判据**，
比"让 224×224 的 CNN 去认 20×8 px 的字"稳得多。
适合的场景：入点侧的"回放否决"（A2 已做显式标注）与 `replay_segments` 消费（A5）。

### 6.3 明确**不建议**现在做的事

- ❌ **不要把这三个候选模型挂到生产路径**（`_DEFAULT_BROADCAST_MODEL_DIR`）：门禁全不过，
  且 `combat` 退化会直接伤入点判定；
- ❌ 不要用 `--new-manifest manifest_broadcast.jsonl` + 直接跑 runbook §2 的命令：
  会同时踩 §1.1（丢运行时契约）与 §1.2（蒸馏反向拉扯）两个坑；
- ❌ 不要为了刷 `val` Macro F1 去调 `hard_weight`：实测 1 与 4 几乎无差（§4）。

---

## 7. 复现

```bash
# ① 生成清单（uniform 基线权重 + 455 帧纠正样本 distill=0 + 教师错误证据）
python scripts/valorant_vision/build_retrain_manifests.py --hard-weight 1.0 --date-tag 20260910_k1

# ② 重训（GPU；训练脚本已自动继承教师运行时契约）
python scripts/valorant_vision/train_onnx_finetune.py \
  --data-dir datasets/valorant_phase_broadcast \
  --new-manifest scripts/valorant_vision/manifest_broadcast_retrain_20260910_k1.jsonl \
  --hard-manifest scripts/valorant_vision/manifest_broadcast_relabel_hard_20260910_k1.jsonl \
  --teacher-dir lsc/analyzer/models/valorant_phase_broadcast_finetune_v4_fused_20260907 \
  --out-dir C:/lsc_models/broadcast_retrain_k1_20260910 \
  --teacher-cache C:/lsc_models/teacher_cache_broadcast_retrain_20260910.json \
  --epochs 10 --seed 20260907

# ③ 官方口径复核（本项目内脚本）
python scripts/valorant_vision/reeval_replay_verified.py \
  --candidate-dir C:/lsc_models/broadcast_retrain_k1_20260910 \
  --baseline-dir lsc/analyzer/models/valorant_phase_broadcast_finetune_v4_fused_20260907 \
  --data-dir datasets/valorant_phase_broadcast \
  --manifest scripts/valorant_vision/manifest_broadcast.jsonl

# ④ §4.1 那张四方对照表的复现（同样复用官方 evaluate()）
python scripts/valorant_vision/compare_models_official.py \
  --models baseline=lsc/analyzer/models/valorant_phase_broadcast_finetune_v4_fused_20260907 \
           "control(no-relabel)=C:/lsc_models/broadcast_retrain_control_20260910" \
           "k1(relabeled)=C:/lsc_models/broadcast_retrain_k1_20260910" \
           "k4(relabeled)=C:/lsc_models/broadcast_retrain_20260910" \
  --json docs/reports/b1-retrain-compare-20260910.json
```

> `docs/reports/*.json` 按 `.gitignore:203` 是**本地产物**（可由上面第 ④ 步重新生成）；
> `manifest_*.jsonl` 同理（`.gitignore:173`）。

**产物位置**（均在仓库外，未污染工作树）：
`C:/lsc_models/broadcast_retrain_20260910`（k4）、`..._k1_20260910`（k1）、
`..._control_20260910`（对照）；对照集硬链接于 `D:/lsc_models/control_broadcast/`。

**环境**：RTX 3060 Laptop 6GB / torch 2.5.1+cu121 / onnxruntime 1.24.4（DML）；
单次 10 epoch 约 20 分钟（教师目标缓存首次约 40 秒）。

---

## 8. 对既有文档的修正

| 文档/位置 | 原表述 | 应为 |
| :--- | :--- | :--- |
| runbook §2 命令 | 可直接照跑 | ⚠️ 需先补 §1.1 的元数据继承 + §1.2 的 `--hard-manifest`，否则结果不可比/被抵消 |
| runbook §3.2 判读标准 | "首要：`test` 召回显著上升；次要：`val` 不得下降" | 需把 **`val` 不退化设为硬停止条件**：本次首要判据达标（+28.1pp）而整体是**负收益** |
| 计划文档 §B附注 | "其中文件名自带 `replay_boost`/`rarex` 的 360/375" 被当作挖掘证据 | 这两个标签是**增强/过采样副本**标记（`train/non_game` 里也有 216 帧带它），**不构成 replay 的内容证据** |
| 计划文档 §B附注 | （未记录） | 补：`train/replay` **1587 帧 = 158 唯一源（10.04×）**；`val`/`test` 无重复；455 帧 = **35 唯一源** |
| B5 的前提陈述 | "回放水印位于画面**底部**" | 仅对 `test/replay` 那套广播风格成立；训练/验证集的标记在**右上角**（y∈[22,61]，仍在顶部裁剪内） |
