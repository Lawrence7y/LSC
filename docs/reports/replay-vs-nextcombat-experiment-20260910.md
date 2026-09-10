# 只读对照实验：「是否开启 `broadcast_mode`」的决策数据

实验日期：2026-09-10
目的：在真实官方解说录像上，量化**回放对 OCR 回合切分的干扰**，据此判断把 `broadcast_mode` 接入生产是否值得。
性质：**只读**。未修改任何源码、配置、录像或 sidecar。

---

## 0. 结论速览

| 判断 | 结论 |
| :--- | :--- |
| `broadcast_mode` 是否值得开启？ | **值得，但收益有限且需防副作用**——它能精准消除 `next_combat` 产生的**碎片回合**（实测碎片 100% 来自 next_combat），但不改善入点精度 |
| 为什么"加了回放标注入点仍不准"？ | 因为**模型的 replay 类对"直接播实战镜头"的回放几乎无效**：在 t=348 把确凿的回放**高置信判成 combat（p_replay 仅 0.001–0.041）**。所以回放标注并未真正获得可用能力 |
| 意外发现 | **OCR 自带的 `replay_segments` 比模型更可靠**（抓到模型漏掉的回放），但它有假阳性、且**全仓无消费者** |

---

## 1. 实验方法

| 项 | 做法 |
| :--- | :--- |
| 素材 | `D:\desktop\新建文件夹 (2)\新建文件夹\EDG夺冠回顾\` 下 2 段真实 EDG 夺冠回顾录像（617s / 1169s）+ 对应 `.analysis.json` sidecar |
| 抽帧 | **产品自带 `FrameProvider`**（ffmpeg 管道，1 fps），避免另造路径 |
| 推理 | **产品自带 `ValorantFrameClassifier(profile="broadcast")`** 融合模型（DML provider） |
| 标签 | **产品自带** `_stable_visual_label` + `_stabilize_broadcast_samples`（阈值 0.55/0.77/0.70/0.60 对比） |
| 真值 | ffmpeg 定点抽帧 + **人工目视判读**（回放水印 / 击杀配对叠加 / 观察者自由视角 / 实时 POV） |

**时间轴校准**：目视 `t=620` 为回放，与 sidecar 的 `replay_segments=[[615.067, 629.067]]` 一致 → 录像与 sidecar 时间轴对齐，逐回合交叉比对有效。

---

## 2. 证据一：短碎片回合 **100% 由 `next_combat` 产生**

录像 `2026-09-10_12-00-36`（1169.3s，9 个回合）：

| 回合区间 | 时长 | `end_by` | 模型回放重叠 | OCR `replay_segments` |
| :--- | ---: | :--- | ---: | :--- |
| 66.6–298.8 | 232.2s | next_prep | 0.0s | [[263.8,286.8],[288.8,297.8]] |
| 319.4–419.2 | 99.8s | broadcast_exclusion | 0.0s | — |
| 452.9–547.1 | 94.1s | next_prep | 0.0s | — |
| **550.1–576.1** | **26.0s** | **next_combat** | **6.0s** | — |
| 576.1–630.1 | 54.0s | next_prep | 0.0s | [[615.1,629.1]] |
| **668.1–687.1** | **19.0s** | **next_combat** | 0.0s | **[[675.1,686.1]]** |
| 695.1–741.1 | 46.0s | next_prep | 0.0s | [[724.1,737.1]] |
| **792.1–815.1** | **23.0s** | **next_combat** | 0.0s | — |
| 815.1–877.1 | 62.0s | next_prep | 0.0s | [[871.1,876.1]] |

**观察**：
- 正常长度回合（46–232s）的 `end_by` **全是** `next_prep` 或 `broadcast_exclusion`
- **3 个短碎片（19/23/26s）的 `end_by` 全是 `next_combat`** —— 完全对应，无一例外
- 短回合判定阈值 35s（Valorant 单回合交战段正常 ≥40s）

→ **`next_combat` 是碎片的唯一来源**，而这正是 `broadcast_mode` 在 `state==COMBAT` 时会抑制的分支（`valorant_ocr_rounds.py:455-462`）。
→ 抑制它**不会伤及正常回合**，因为正常回合不依赖 `next_combat` 闭合。

---

## 3. 证据二：碎片与回放确有因果关系（3 个中 2 个已确证）

| 碎片 | 证据 | 目视确认 |
| :--- | :--- | :--- |
| 550.1–576.1（26s） | 模型检出回放 **558–564 落在该回合内部**（重叠 6.0s） | — |
| 668.1–687.1（19s） | OCR 标注 `[[675.1, 686.1]]` 落在其内部；**整个"回合"都在回放块内** | **t=672 与 t=682 均目视确认为回放**：右下有大写 **"REPLAY"** 水印 + 金色回放边框；t=682 为全屏选手镜头、t=672 为回放的第一人称击杀片段 |
| 792.1–815.1（23s） | 模型与 OCR 均未标注 | 未定论（本次未逐帧核） |

---

## 4. 证据三（关键新发现）：模型与 OCR 的回放识别**互补，且模型对"实战镜头回放"失效**

在录像 `2026-09-10_09-49-28` 上逐帧对照（模型原始概率 vs 目视真值）：

| 时段 | 目视真值 | 模型判定 | OCR `replay_segments` |
| :--- | :--- | :--- | :--- |
| 68–88s | **实时交战**（POV + 计时器 1:02，无回放水印） | `combat` p=0.64–0.84 ✓ | 标为回放 ✗ **假阳性** |
| 311–317s | **回放**（REPLAY 水印 + 观察者转场） | **`replay` p=0.59–0.86 ✓** | 漏检 ✗ |
| 344–354s | **回放**（击杀配对叠加 `PRX mindfreak → EDG nobody` + 红色剪影） | **`combat` p=0.73–0.83，`p_replay` 仅 0.001–0.041** ✗ **高置信误判** | 回放 ✓ |
| 12-00-36 @620s | **回放**（观察者自由视角滑动 + 死亡标记） | 未检出 ✗ | 回放 ✓ |

**核心新结论**：
> 模型的 `replay` 类**只对"带 REPLAY 水印 / 转场 UI"的回放有效**；
> 当官方流**直接播实战镜头或观察者自由视角**（画面与实时交战几乎同构）时，
> 模型会**以高置信度判为 `combat``**（p_replay < 0.05，远低于任何阈值）。

这解释了用户的疑问"**已在新模型加了回放画面标注，切片质量依旧不佳**"：
**标注数据里的 `replay` 样本主要是"回放转场/水印"画面，缺少"回放中的实战镜头"这一类**——
后者在像素上与实时交战无法区分，模型学不到可判别的特征。

**同时**：OCR 的 `replay_segments`（基于"结算后 ≥5s 无可读计时器的 neutral 段"）**反而能抓到这类回放**，
因为它利用的是"计时器不可读"这一间接证据，而非画面外观。但它有假阳性（62–88s）。

---

## 5. 决策建议

### 5.1 关于 `broadcast_mode`：**建议开启，但要配合 3 个前提**

**支持开启的依据**：
- 实测 `next_combat` **只**产生碎片（19/23/26s），**从未**产生正常长度回合 → 抑制它不损失正常回合
- 3 个碎片中 2 个已确证与回放重合
- 抑制后这些碎片将不再产生；对应回合会改由 `next_prep` 闭合（或被 `_expand_oversize_candidates` 切成子块）

**风险与前提**：
1. ⚠️ ~~关闭 `next_combat` 后可能加重回合合并~~ —— **此风险经复核后被下调**。
   `broadcast_mode=True` **并非抑制全部 `next_combat`**，而是**只抑制 COMBAT 态**的
   fresh-clock 假切分（`valorant_ocr_rounds.py:455-462` 直接 `return closed`）；
   **SETTLE 态**的合法 `next_combat` 闭合**仍然保留**（`:550` / `:582`，条件是
   `_fresh_clock or 满钟≥85 or 距结算≥45s 的迟到原始交战钟`）。即"结算后错过准备、
   直接见到新回合满钟"这条正常路径不受影响 —— 已有测试
   `test_broadcast_late_fresh_clock_closes_missing_prep_as_pending` 覆盖该行为。
   因此合并风险主要限于"交战中途被回放打断且此后 prep 也漏检"的情形。
2. ⚠️ 仍需监控合并率（`_expand_oversize_candidates` 触发次数）与长回合占比，作为切换后的回归指标。
3. ✅ **影子模式已实施**（见 §7），可直接用它取数，无需手工搭环境。

### 5.2 关于真正的主因（入点不准）：**开启 `broadcast_mode` 不解决**

上一轮报告中的 R2/R3 未被本次实验推翻，反而被现场数据加固：
- 真实 sidecar 中所有回合的 `start_confidence` **恒为 0.95**、`start_delta` 恒为 0.0–2.0（自洽但未必正确）
- `start_by` 只有 `ocr_combat` / `refined_combat` 两种，**从无模型来源**

→ 入点精度的提升必须走 **R2（入点密扫引入模型回放否决）** 与 **R3（`precise` 需交叉证据）**，
而不是靠 `broadcast_mode`。

### 5.3 一条被本次实验加强的建议

**把 OCR 的 `replay_segments` 用起来**（R5）：实测它比模型更可靠地抓到"实战镜头回放"，
而当前它是**只写不读的死数据**。最小改动：在 `_start_gate_decision` 与边界密扫中，
把 OCR 回放段作为"起点/终点不得落入"的排除区间。

---

## 6. 影子模式（已实施）

已按 §5.1 的前提 3 落地**只记录、不生效**的影子模式，用于正式切换前持续取数。

| 项 | 内容 |
| :--- | :--- |
| 开关 | 环境变量 `LSC_VALORANT_BROADCAST_MODE_SHADOW=1`（`1/true/yes/on`） |
| 生效条件 | 开关打开 **且** `source_profile == "broadcast"`（该分支语义只对赛事流成立） |
| 做什么 | 用**同一批 OCR 标签**并行喂一份 `broadcast_mode=True` 的 FSM，产出两份回合列表的差异 |
| 记录到哪 | 运行时状态 `state["broadcast_mode_shadow"]`（本次摘要）、`state["broadcast_mode_shadow_totals"]`（累计）、`state["ocr_fsm_broadcast_shadow"]`（影子 FSM，供增量续扫）；同时输出 INFO 日志 |
| 摘要字段 | `primary_rounds` / `shadow_rounds` / `shadow_only` / `primary_only` / `resized` / `primary_next_combat` / `shadow_next_combat` |
| **不改变** | 返回的生效回合列表、`round_key`、边界密扫、回放标注——影子结果绝不混入 |

### 验证结果

| 验证 | 方法 | 结果 |
| :--- | :--- | :--- |
| 行为中性 | 真实录像同一时间范围（`280–390s` / `660–700s`）分别跑影子关/开两次，比对生效列表 | **完全一致** ✓ |
| 影子产出 | 同上 | 摘要与累计统计正常产出，影子 FSM 正确持久化 ✓ |
| 机制有效 | FSM 级单测：同一标签序列在两种模式下产生不同结果 | 生效 `[1.0,35.0] next_combat pending`（26s 碎片）vs 影子 `[1.0,50.0] next_prep vision_confirmed`（49s 完整回合）✓ |
| 源码守卫 | AST 级断言：生效 `feed()` 不传 `broadcast_mode`；`broadcast_mode=True` 全模块**仅一处**代码调用；影子块由开关+档位双重把关 | `tests/test_broadcast_mode_shadow.py` 24 条全过 ✓ |
| 无回归 | `test_valorant_ocr_rounds` / `test_valorant_broadcast` / `test_broadcast_mode_shadow` / `test_continuous_analysis_guards` / `test_continuous_epoch_hygiene` | 233 passed ✓ |

### 取数方法（下一步）

在正式比赛场景开启该环境变量跑 1–2 场，然后从日志/状态里读累计统计：

- **`primary_next_combat` 显著大于 `shadow_next_combat`** → 证实 COMBAT 态 fresh-clock 切分（碎片）被大量消除 → 支持切换
- **`shadow_only` 明显偏多** → 影子模式额外产出了回合，需人工确认这些是"被碎片切分掩盖的真实回合"还是"过度合并的伪回合"
- **`resized` 中 `shadow_sec` 普遍显著大于 `primary_sec`** → 说明生效路径在把回合切短 → 支持切换

---

## 7. 实验的局限

- 只做了 **2 段录像**（617s + 1169s）的逐帧/定点目视核验，样本有限；结论中的比例（如"3/9 碎片"）不应当作全量统计
- `792.1–815.1`（23s 碎片）未逐帧确认是否回放
- 模型回放召回率未做全量标注，仅做了定点真值核验
- 未在真实录像上回放式验证"开启 `broadcast_mode` 后的回合列表"（属第 5.1 条建议的影子模式工作）

## 附：复现方式

```bash
# 逐帧概率导出（只读）
python C:/lsc_tmp/dump_probs.py
# 逐回合 × 回放交叉比对（只读）
python C:/lsc_tmp/exp_final.py
```

> ⚠️ 复现注意：Windows 中文路径下 `cv2.imread` 会**静默失败返回 None**，
> 必须用 `cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)`。
> ffmpeg 为 Windows 原生程序，路径须用 `C:/...` 而非 Git-Bash 的 `/c/...`。
