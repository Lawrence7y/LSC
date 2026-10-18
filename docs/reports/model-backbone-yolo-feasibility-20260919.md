# 把两个视觉模型换成 YOLO 的可行性（内存 / 资源维度）

日期：2026-09-19
性质：**只读评估**。未改动任何生产源码、模型产物或配置；新增内容仅为 `artifacts/model_bench/` 下的三个测量脚本与本报告。

---

## 0. 结论速览

| 问题 | 结论 |
| :--- | :--- |
| 换成 YOLO 能省内存吗？ | **几乎不能**。两个模型权重合计 6.16 × 2 = **12.32 MB**，两个 ORT session 合计占 RSS **+28.0 MB**（本机实测）。YOLO-cls 的 n 档参数量与 MobileNetV3-Small 同量级，净省是**个位数 MB**；ONNX Runtime 每 session 的固定开销（≈14 MB）与用哪个 backbone 无关。 |
| 能省算力吗？ | **不能，反而显著上升**。MobileNetV3-Small ≈0.06 GFLOPs@224，YOLO11n-cls ≈3.9 GFLOPs@224（公开规格，本机无外网未复测）→ 每次前向约 **65×** 计算量；而生产每分析帧实际要跑 ≈**6 次前向**（整帧 + 顶部 HUD + 4 路 marker ROI），放大的是 6 次前向里的每一次。 |
| "保持当前性能"风险如何？ | **这是全部风险所在**。现有阈值/融合/门禁（stable_prob 0.55、high_prob 0.8、replay 0.77、融合 0.7:0.3、marker weight 1.0）与 `sha256` + `promotion_state=active ⇒ gates_passed` 契约全部作废；换 backbone = 重标注 + 重训 + 重标定 + 重过门禁。 |
| 那真正该动的杠杆是什么？ | 见 §5：**去掉重复前向**（广播档每帧 2 次）、**marker 支路按需触发**（现固定 4 ROI/帧）、**fp16/int8 量化**（权重 6 MB → 1.5–3 MB，CPU 延迟降 1.5–3×）、**帧驻留**与 **FFmpeg 派生闸**。 |

> 术语前提：`valorant_phase_v1.onnx` / `valorant_marker_v1.onnx` 都是 **5 分类分类器**（输出 `probs[N,5]`），不是检测器。所以"换成 YOLO"只能落到 YOLO11n-cls / YOLOv8n-cls 这类**分类 backbone**；用检测器（YOLO11n-det）则输入输出契约、标注方式、下游 FSM 全变，那是产品改造而不是等价替换。

---

## 1. "两个模型"到底是什么

| 项 | 事实 | 出处 |
| :--- | :--- | :--- |
| 主干 | torchvision `mobilenet_v3_small`（ImageNet 预训练权重初始化），替换最后分类层为 5 类 | `scripts/valorant_vision/train_export.py:158-164` |
| 输入契约 | 224×224、RGB、mean(0.485,0.456,0.406)/std(0.229,0.224,0.225)、NCHW | `lsc/analyzer/valorant_frame_classifier.py:259-305` |
| 类别 | `non_game / buy / combat / result / replay` | 同上 |
| 模型 A | `valorant_phase_v1.onnx`（广播档为融合微调版 v4_fused，6.16 MB） | `lsc/analyzer/models/.../valorant_phase_v1.onnx` |
| 模型 B | `valorant_marker_v1.onnx`（**同一主干**，只用于回放标记 ROI 支路，6.16 MB） | 同一目录；声明于模型元数据 `marker_roi_branch` |
| 运行方式 | onnxruntime，provider 由 `ocr_accel` 决定（生产 `dml`）；批推理 `predict_batch`；广播档 `predict_broadcast_batch` = 整帧 + 顶部 34% 两路融合，再取 4 路 ROI 的 replay 证据做逐帧 max | 同上 `:339-560` |
| 实际单帧调用次数 | 广播档 ≈ **6 次前向/帧**（2 路融合 + 4 路 ROI）；POV 档 1 次/帧 | `:501-560` |
| 采样节奏 | 广播计时采样 4 s/帧（`BROADCAST_TIMER_SAMPLE_INTERVAL_SEC = 4.0`）；边界密扫 5–10 fps 突发 | `lsc/analyzer/valorant_broadcast.py:34`、`valorant_ocr_rounds.py:55-60` |

---

## 2. 本机实测（CPU EP，Python 3.12 / onnxruntime 1.24.4 / RTX 3060 Laptop）

测量脚本：`artifacts/model_bench/bench_onnx.py`、`bench_mem.py`、`bench_ocr.py`

### 2.1 装载与常驻内存

| 指标 | 实测 |
| :--- | :--- |
| 两个模型权重（磁盘） | 12.32 MB |
| RSS：无 session → 仅 phase session → 再加 marker session | 53.4 → 70.0 → **81.4 MB**（合计 **+28.0 MB**） |
| session 创建耗时 | phase 0.142 s / marker 0.082 s / POV 0.049 s |
| 单帧输入张量（fp32 224²） | 0.602 MB |
| marker 支路分块上限 256 crops 的瞬时输入 | ≈154 MB（这就是 `_MARKER_MAX_CROPS_PER_RUN = 256` 的存在理由；3544 crops ≈2 GB 会让 DML `E_OUTOFMEMORY`） |

### 2.2 推理延迟

| 模型 | batch1 | batch16（ms/帧） | batch64（ms/帧） |
| :--- | :--- | :--- | :--- |
| phase（广播融合主干，CPU） | 4.60 ms | 3.52（284 fps） | 5.17（193 fps） |
| marker（CPU） | 3.64 ms | 3.48（288 fps） | 5.08（197 fps） |
| phase（POV，CPU） | 3.05 ms | 2.76（363 fps） | 5.27（190 fps） |
| phase（**DML**） | 6.11 ms | 3.57 | **1.86（537 fps）** |

结论：模型侧本来就是**亚毫秒–数毫秒/帧**的量级，在 4 s/帧的稳态采样下总占用可以忽略；只有边界密扫（5–10 fps × 6 次前向）才会形成可见的推理负载。

---

## 3. 资源到底被谁吃掉了

| 项 | 量级 | 依据 |
| :--- | :--- | :--- |
| 视觉模型（本次讨论对象） | 权重 12 MB / RSS +28 MB | §2.1 实测 |
| 抽帧帧驻留 | 追赶窗 480 s @1 fps ≈ **330 MB** 帧数组；拆 60 s 子窗后峰值 ≈40 MB | `valorant_ocr_rounds.py:50-52` |
| RapidOCR（det 4.75 + rec 10.86 + cls 0.59 MB） | 装载 0.53 s，RSS **+72.9 MB**（三个 session） | `bench_ocr.py` 实测 |
| FFmpeg 进程族 | 最多 12 路录制 + 4 路预览转码 + 导出 + probe + OCR 抽帧，**无全局上界** | CLAUDE.md §7.4 |
| 单次大 batch 张量 | 256 crops ≈154 MB；3544 crops ≈2 GB（DML OOM 事故） | 代码注释 + §2.1 |

即：**内存的大头是视频帧与 FFmpeg 进程，不是这两个 6 MB 的分类器。** 换 backbone 影响不到前两项。

---

## 4. 换成 YOLO 的逐项后果

| 维度 | 评估 |
| :--- | :--- |
| 内存 | YOLO11n-cls 参数量 ≈1.6 M（比 MobileNetV3-Small ≈2.5 M 少）→ 权重省下约 2–3 MB；**ORT session 固定开销不变**。相对 330 MB 的帧驻留与数十 MB/session 的 FFmpeg，属于噪声。若换检测器，还要加 NMS/后处理与更大的特征图峰值。 |
| 算力 | ≈0.06 → ≈3.9 GFLOPs@224（公开规格）：单次前向约 65×；生产广播档每帧 6 次前向，等于把可见推理负载整体抬一个数量级。CPU 兜底路径（文档记录基线 49.8 fps）会最先顶不住。 |
| 准确率 | 现有模型是经 v2→v7 多轮微调 + 融合权重 + 标记支路才达到 96.88% 准确率 / 92.02% 宏 F1 的候选（且**仍未过门禁、未替换生产**）。换 backbone 需要：重标注（回放标记 / 边界样本）、重训、重标定阈值、重跑门禁（`promotion_state=active` 强制要求 `gate_results.gates_passed`）。训练集约 4446 / 验证 778 帧，**更重的 backbone 在这个数据量上更容易过拟合**。 |
| 契约破坏面 | `sha256` 校验、`class_names` 顺序、`input_size/color_order/normalize` 契约、`broadcast_input_fusion`、`marker_roi_branch`、前端遥测（`model_infer_fps` 等）全部要随之改动；`tests/test_valorant_frame_classifier.py` 里大量结构断言也会连带重写。 |
| 净结论 | **"省内存"不成立；"省算力"反向；"保持性能"是唯一真风险。** |

---

## 5. 如果目标真是省资源：按性价比排序的真实杠杆

1. **去掉重复前向（零风险，收益最大）**：广播档同一帧跑了整帧 + 顶部 34% 两次 `predict_batch`，marker 支路再跑 4 路 ROI。可先用"整帧结果已高置信则跳过其余各路"的短路策略，或把双路融合改成单次前向的双输入/空间加权模型。
2. **量化**：MobileNetV3-Small 是量化最友好的结构之一，fp16/int8 可把权重从 6 MB 压到 1.5–3 MB，并降低 CPU 延迟；改动面仅限模型产物与 `sha256` 元数据，**不动任何阈值标定与 FSM**。
3. **marker 支路按需触发**：现在无论整帧怎么判都跑 4 路 ROI；只在 `replay` 处于灰区（例如整帧 `replay` 概率落在 0.3–0.77）时补跑，稳态下可省掉绝大部分。
4. **帧驻留**：`_SUB_WINDOW_SEC` 子窗继续收窄 / 复用 `FrameProvider` 缓存；OCR 已有 ROI 差分预筛（`_ROI_DIFF_THRESHOLD`），可扩大命中率以减少 det+rec 调用。
5. **进程侧**：CLAUDE.md §7.4 的中央派生闸（`LSC_MAX_CONCURRENT_SPAWNS` / `LSC_MAX_LIVE_CHILDREN`）才是总量控制手段，比换模型更能决定"资源占用"。

---

## 6. 如果确实想用 YOLO：它合理的落点是"检测"，不是"替代分类"

| 落点 | 为什么合理 | 为什么不是省资源 |
| :--- | :--- | :--- |
| 回放标记 / 水印定位 | 现在靠 4 个硬编码 ROI + 分类器近似；整帧压到 224² 后标记只剩 ≈20×8 px（占 8% 边缘能量），已因此被迫加独立 ROI 支路。检测器可在任意位置直接给出标记框。 | 新增一个模型（+session、+标注集），内存只增不减。 |
| 广播版式自适应（击杀信息框 / 回合横幅） | 现在裁剪比是硬编码（如 `_KILL_FEED_CROP_RATIO=(0.75,0.04,0.23,0.20)`、`_ROUND_MARKER_CROP_RATIO=(0.35,0.01,0.30,0.06)`），赛事流已需要"多组候选 ROI"兜底；检测器可替代这套枚举。 | 收益是鲁棒性，不是内存。 |
| OCR 的 **det** 段 | PP-OCRv4-det（DB）4.75 MB 可换成文本检测器。 | **rec 段（10.86 MB）无法用 YOLO 替代**——YOLO 不认字；现在是 0.5–1 fps 的 ROI 小图 OCR，det 本来就便宜。 |

---

## 7. 若要继续推进，建议的验证路径（不阻塞现有生产）

1. 用现成脚本在同一验证集上出对照指标：`scripts/valorant_vision/eval_codex_broadcast.py` / `eval_domain_val.py` / `run_broadcast_acceptance.py`；
2. 影子模式并行（`scripts/valorant_vision/live_concurrent_shadow.py`），只记录分歧，不替换生产；
3. 通过门禁（Macro F1 / Replay Recall / Non-game Recall / Buy Precision / Result Precision）后再谈替换——当前 v4_fused 候选正是卡在这一步；
4. 先做 §5-1 与 §5-2（短路 + 量化），用实测 FPS/RSS 作为基线，再判断是否还需要换 backbone。
