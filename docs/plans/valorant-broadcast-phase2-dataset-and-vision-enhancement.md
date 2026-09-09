# Valorant 官方赛事/解说流持续分析优化（阶段二）：数据与特征引擎重构

> **阶段定位**：解决“切片起止不准、慢动作回放被误切入、回合漏检误检”的算法与数据核心问题。
> **目标收益**：切片准确率达到生产发布标准（边界误差 P95 ≤ 0.8s，无回放/暂停杂质污染）；利用 Observer 赛事比分强特征实现 100% 确定性回合闭合。

---

## 一、准确度瓶颈与算法短板剖析

当前系统的五分类模型（`valorant_phase_v1.onnx`）与规则状态机无法在赛事流中精准工作，根本原因在于**特征与数据分布的严重失配（Domain Shift）**：

```
[官方赛事转播画面] 
  ├─ 特征 1: Observer HUD (两队大比分栏 7-5, 攻防标志) ──> 当前 OCR 锚点完全未覆盖 (只看个人客户端 1:40 药丸框)
  ├─ 特征 2: 慢动作回放 (画面是正常打枪，仅边框有赛事暗金动效) ──> 当前 224x224 模型全局缩放后丢失微小边框特征，误判为 Combat
  ├─ 特征 3: 导播极速切镜头 (击杀后 0.5s 切选手实拍或演播室) ──> 缺少个人客户端的大字 VICTORY/DEFEAT 横幅
  └─ 特征 4: 缺少开局买枪 (导播在买枪倒计时最后 2s 才切回) ──> 缺少 BUY PHASE 导致 FSM 错过下回合起点
```

### 数据事实（`lsc/analyzer/models/valorant_phase_v1.json`）
- 现有模型数据集：`train_count: 7342`，`val_count: 669`；
- 数据主体来源：`pov_beilie`、`pov_fish` 等第一视角玩家排位实录；
- 真实官方转播画面占比不足 15%，无法覆盖 VCT 太平洋、VCT CN 等不同转播包装、各二路解说的主播画中画以及多样化的慢动作 Replay 特效。

---

## 二、阶段目标与双轮驱动架构

阶段二通过**“强结构特征提取器（Observer HUD 比分状态机）”**与**“专属视觉分类模型（Phase V2）”**双轮驱动：

```
                              [赛事画面输入]
                                     │
           ┌─────────────────────────┴─────────────────────────┐
           ▼                                                   ▼
【通道 A: Observer 专用比分跳变提取器】             【通道 B: 赛事专用五分类视觉模型 V2】
- 针对顶部 30%~70% 宽度大比分区域               - 输入 224x224 全局帧 (重点识别 Replay 边框)
- 提取战队局分: Team A [7] - [5] Team B           - 重点识别: Replay / Non-game / Combat
- 比分单调 +1 跳变 (7->8 或 5->6)                 - 连续 2 帧 Replay 置信度 >= 0.75
           │                                                   │
           └─────────────────────────┬─────────────────────────┘
                                     ▼
                      【赛事专属精确边界定稿状态机】
- 入点: 交战首帧由比分板/计时器重置对齐 (Start PTS)
- 出点: 比分跳变时刻锁定回合结束基线 (T_score)
- 回放切除: T_score 后视窗中，若在 T_score + 1.5s 内出现 Replay 特效，精确在 Replay 前 0.25s 截断
- 最终产出: 100% 纯净、无慢动作污染、边界精度达到 ±0.5s 的高光切片
```

---

## 三、拆解任务清单（Work Breakdown Structure）

### 任务 2.1：官方赛事 Observer 比分跳变提取器（Scoreboard Delta Extractor）
- **目标**：摆脱对全屏结算横幅的脆弱依赖，利用官方赛事比分牌必然跳变的物理事实，提供 100% 可靠的回合终点强基线。
- **改动文件**：
  - 新增 `lsc/analyzer/valorant_observer_scoreboard.py`
  - 修改 `lsc/analyzer/valorant_ocr_rounds.py`
- **实现细节**：
  1. Observer HUD 区域精准定位：
     - 分辨率标准化（1920x1080 坐标空间）：顶部比分区域固定在 `y: [0, 100]`，`x: [680, 1240]`；
     - 左右两侧分别为红蓝阵营小局得分（如 `08` 与 `05`）。
  2. 轻量化数字识别（不调用庞大通用 OCR）：
     - 数字区域极小且背景固定，使用针对数字特化的二值化 + 轮廓识别（或仅识别 `0~15` 数字模版匹配）；
     - 单帧计算耗时 ≤ 5ms，可在粗扫时以 1fps 连续读取。
  3. 比分递增状态机（Score Tracker）：
     - 记录比分序列：`[(ts, score_a, score_b), ...]`；
     - 防抖规则：比分增加必须在后续至少 2 秒内保持稳定（防导播比分板闪烁或特效遮挡）；
     - 当确认 `(score_a + score_b)` 发生 `+1` 时，**将跳变首帧的时间戳直接记录为 `score_end_ts`**。

### 任务 2.2：赛事专属标注数据集扩充与难样本挖掘（Hard Mining）
- **目标**：构建包含各赛区 VCT 官方流与代表性二路解说的专项数据集，解决模型对 Replay 和复杂演播室的域偏识别问题。
- **改动文件**：
  - `scripts/valorant_vision/build_broadcast_hard_dataset.py`
  - `scripts/valorant_vision/manifest_schema.md`
  - `scripts/valorant_vision/extract_frames.py`
- **数据集构建规范**：
  1. 视频来源采集（不少于 8 场独立完整赛事录像）：
     - VCT CN 官方赛事流（2 场）
     - VCT 太平洋 / 大师赛官方赛事流（2 场）
     - 头部主播二路解说流（4 场，包含画中画摄像头、弹幕互动栏）
  2. 样本分类与难样本覆盖目标：
     - **总样本量从 7,342 扩充至 20,000+**（其中 `broadcast` 样本占比 ≥ 60%）；
     - `replay` 标签扩充至 3,500+：重点覆盖带有金色转场、回放小标、分屏慢镜头的真交战回放；
     - `non_game` 标签扩充至 5,000+：重点覆盖演播室全景、选手实拍席、战术地图、技术暂停广告；
     - `buy` 标签扩充至 3,000+：覆盖赛事开局时小地图俯瞰战术图与买枪界面。
  3. 难样本挖掘流水线：
     - 运行当前模型对赛事全录像推理，筛选出置信度低（0.40 < conf < 0.65）及相邻帧标签反复跳变（Flip）的帧；
     - 将这些疑难帧导出为标注队列，通过 `serve_label_ui.py` 进行人工复核并打标。

### 任务 2.3：五分类模型重训、量化与发布门禁验证
- **目标**：训练产出 `valorant_phase_v2.onnx`，各项评估指标达到或超过发布门槛要求。
- **改动文件**：
  - `scripts/valorant_vision/train_export.py`
  - `scripts/valorant_vision/eval_gates.py`
  - `lsc/analyzer/models/valorant_phase_v2.onnx`
  - `lsc/analyzer/models/valorant_phase_v2.json`
- **模型演进与训练方案**：
  1. 骨干网络选型：
     - 保持轻量高吞吐：采用 MobileNetV3-Large 或 FastViT-T8，输入分辨率提升至 256x256（增强小字“REPLAY”与边框特征的分辨能力）；
  2. 损失函数与采样策略：
     - 引入 Focal Loss（针对 Replay 与 Non-game 难分类别增加权重）；
     - 针对 Replay 类别实施 4x 过采样；
  3. 模型导出与 DirectML 硬件加速验证：
     - 导出为 ONNX opset 17；
     - 验证在 CPUExecutionProvider 与 DmlExecutionProvider 上的推理一致性；
     - 运行 `eval_gates.py` 进行盲测验收。

### 任务 2.4：双模融合精修决策引擎（Fuse & Refine）
- **目标**：将 Observer 比分跳变锚点与视觉分类模型紧密编织，实现无缝边界精修。
- **改动文件**：
  - `lsc/analyzer/valorant_broadcast.py`
- **实现细节**：
  1. 决策级联：
     - **首选基线**：若存在 `score_end_ts`（比分跳变时间），以此作为绝对结束参考基准；
     - **回放检测**：在 `[score_end_ts - 2s, score_end_ts + 20s]` 区间内，由 Phase V2 视觉模型扫描 Replay 起点；
     - 若在 `score_end_ts` 之前就已进入慢动作回放，则在模型识别的首个 Replay 帧前截断；
     - 若模型未见明显回放，则安全以 `score_end_ts` 作为最终出点。
  2. 证据完整性印章：
     - 凡经由“比分跳变 + 视觉回放过滤”确认的回合，盖上 `boundary_refined = True`、`confirm_status = "vision_confirmed"`、`end_by = "observer_score_and_replay_exclusion"`。
     - 此时完全满足 `_is_auto_exportable_valorant_round` 门禁，直接触发高优先级全自动导出！

---

## 四、分步实施计划（Execution Plan）

### Step 1：Observer 顶部比分跳变提取器研发（Week 1, Day 1-3）
1. 采集 3 段 1080p 典型赛事转播样本；
2. 编写 `valorant_observer_scoreboard.py`，实现数字二值化与比分状态机；
3. 单元测试覆盖：正常局分递增、平局加时、闪烁防抖、非赛事画面抗干扰。

### Step 2：赛事转播视频抽帧与难样本标注（Week 1, Day 4 - Week 2, Day 2）
1. 运行 `extract_frames.py` 对采集的 8 场赛事录像抽取样本；
2. 使用 `build_broadcast_hard_dataset.py` 构建标注队列；
3. 针对 Replay 边界、导播切人、暂停画面完成 13,000+ 帧人工标注打标并入库。

### Step 3：Phase V2 模型训练与量化评估（Week 2, Day 3-5）
1. 执行 `train_export.py` 启动多卡/单卡训练，调整 Focal Loss 权重；
2. 导出 `valorant_phase_v2.onnx` 及元数据 `valorant_phase_v2.json`；
3. 运行 `eval_domain_val.py` 与 `eval_gates.py` 执行全指标审查。

### Step 4：决策状态机编织与端到端实测（Week 3, Day 1-3）
1. 将 `ObserverScoreboardDetector` 与 `audit_broadcast_phase_sequence` 在 `valorant_broadcast.py` 中融合；
2. 在本地录制回放环境中，用 1 小时真实解说流跑持续分析端到端压测；
3. 验证切片导出质量（核对剪映草稿导出时间轴与视频画面）。

---

## 五、验收标准与质量门禁（发布门）

严格执行 `scripts/valorant_vision/eval_gates.py` 中的发布门限：

| 指标项 | 门槛要求 | 预期达成 | 验证脚本 |
| :--- | :--- | :--- | :--- |
| **五分类 Macro F1** | ≥ 0.9400 | **≥ 0.9650** | `eval_gates.py:check_classification_gates` |
| **Replay 召回率 (Recall)** | ≥ 0.9500 | **≥ 0.9700** | 防慢动作污染关键门禁 |
| **Non-game 召回率 (Recall)** | ≥ 0.9500 | **≥ 0.9800** | 防演播室/广告污染门禁 |
| **回合检出率 (Round Recall)** | ≥ 0.9000 | **≥ 0.9500** | 比分提取器赋能后的总召回 |
| **切片入列精确率 (Precision)** | ≥ 0.9700 | **≥ 0.9850** | 零非游戏切片误入列 |
| **精修边界误差 P95** | ≤ 0.80 秒 | **≤ 0.45 秒** | 与人工标注真实 GT 毫秒级对齐 |
| **精修边界最大误差 Max** | ≤ 2.00 秒 | **≤ 1.20 秒** | 绝不允许切片跨回合或长截断 |
