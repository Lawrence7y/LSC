# 无畏契约高光识别试点设计

## 版本信息
- 日期: 2026-06-06
- 修订: 2026-06-06（第三轮审查修订）
- 状态: 待评审
- 目标阶段: 试点首版

---

## 1. 目标概述

本试点的目标不是做一个泛化的"直播高光检测平台"，而是围绕无畏契约直播，先做出一套**工程可落地、可复盘、可迭代**的高光识别与切片能力。

首版聚焦以下场景：
- 素材类型: 主播第一视角排位/路人局、二路解说/陪看
- 输入形态: 直播录制文件，以及录制过程中持续增长的录制文件
- 输出形态: 回合级母片 + 短高光
- 运行模式: 实时粗检 + 录播后精修

首版成功标准不是"精确识别每一次击杀 UI"，而是：
- 能稳定从无畏契约录播中找到值得保留的高光回合
- 能从高光回合中切出适合直接发布的短片段
- 能解释推荐原因，支持人工复核与后续调参

---

## 2. 范围定义

### 2.1 首版纳入范围

- 无畏契约专用高光识别试点
- 自动判型:
  - 主播第一视角
  - 解说/陪看
  - 不确定时双 profile 兜底
- 实时粗检:
  - 录制中产出疑似高光预警卡片
  - 同步采集判型所需信号
- 录播后精修:
  - 全量分析
  - 候选合并
  - 去重
  - 重排序
  - 边界修正
- 双层切片:
  - 回合级母片
  - 母片内短高光
- 样本采集、标注、评估、反馈回流

### 2.2 首版不纳入范围

- 泛化到所有游戏或所有直播品类
- 依赖 Riot Replay 作为主输入链路
- 首版即引入完整 OCR/HUD 模型服务化方案（GameHudAnalyzer 的增强仅限启发式规则改进，不引入深度学习 OCR）
- 云端多服务拆分
- 对每一种直播模板做精细 UI 模板库

---

## 3. 输入与输出

### 3.1 输入

1. 录制完成的视频文件
2. 录制过程中持续增长中的单个视频文件
3. 后续人工标注结果
4. 用户在分析结果上的反馈行为

### 3.2 输出

#### A. 实时预警卡片
- 用途: 录制中提示"这里可能有高光"
- 特点: 高召回、低承诺，不作为最终结果

#### B. 回合级母片
- 时长目标: 45 到 120 秒
- 内容目标: 保留交战前上下文、高潮、收尾
- 用途: 人工复核、二次编辑、作为短高光来源

#### C. 短高光
- 时长目标: 15 到 45 秒
- 内容目标: 可直接发布或轻编辑发布
- 特点: 从母片内部再次裁切，不直接用实时边界

#### D. 分析元数据
每条结果附带 JSON 格式元数据，至少包含：
- 素材类型 (`materialType`)
- 判型置信度 (`classificationConfidence`)
- 命中信号列表 (`signals[]`)
- 六维特征分 (`features{}`)
- 综合得分 (`rankScore`)
- 回合信息 (`roundIndex`, `roundPhase`)
- 候选来源 (`sourceType`)
- 反馈状态 (`feedbackStatus`)
- 层级关系 (`parentClipId`, `isPrimary`)

---

## 4. 录制格式建议

为降低分析链路的不确定性，首版建议约束录制输入格式：

- 视频:
  - H.264 或 H.265
  - 1080p 或 720p
  - 固定帧率 30fps
- 音频:
  - AAC
  - 44.1kHz 或 48kHz
  - 立体声
  - 不低于 128kbps
- 分片:
  - 建议按 10 分钟左右自动分片，避免单文件过大
- 容器:
  - MP4 或 MKV，首版优先 MP4

首版实现应在分析入口增加格式检测：
- 若输入不满足最低规范，则给出明确提示
- 必要时自动转码为分析友好格式后再进入主链路

---

## 5. 设计原则

### 5.1 首版以"可用切片"为中心

系统的输出单位是可发布片段，而不是孤立事件点。高光识别必须服务最终切片，而不是服务某个单一检测指标。

### 5.2 实时与精修分离

- 实时阶段追求召回率
- 精修阶段追求可发布性

不要求实时结果一步到位，避免为实时精度引入过重复杂度。

### 5.3 双 profile，统一排序

主播局和解说局的高光信号不同，但最终输出层一致。首版通过不同 profile 抽取差异特征，最后统一进综合排序器。

两个 profile 不是独立的 `IHighlightStrategy`，而是**特征权重配置 + 特征提取规则集**，由 `HighlightRanker` 消费。策略层由现有 `GameStrategy` + `CommentaryStrategy` 负责产生原始候选。

### 5.4 可解释、可标注、可调参

首版先做可解释规则链路和反馈闭环，为后续引入更强模型保留空间。

---

## 6. 总体架构

整体流程如下：

```
录制输入 -> 实时粗检(同步采集判型信号) -> 判型确定 -> 录播精修(按判型选 profile) -> 母片生成 -> 短高光裁切 -> 统一排序 -> 人工复核/反馈
```

判型不是一次性前置操作：实时粗检与判型信号采集是**并行的**。实时粗检用固定通用信号（音频峰值 + 运动强度 + 场景变化），判型信号在录制过程中同步积累，录制完成后正式判型并路由到对应 profile 做精修。

### 6.1 核心模块

#### MaterialClassifier（新建）
- 责任: 判断素材类型
- 输入: 实时粗检阶段积累的语音占比、交火密度、关键词分布等信号
- 输出:
  - `streamer_pov`
  - `commentary_watchparty`
  - `uncertain`
- 附带 `confidence`
- 判型在录制完成后执行一次，结果用于精修阶段路由

#### RealtimeSignalScanner（新建，基于现有增量分析改造）
- 责任: 在录制过程中做轻量增量扫描
- 关键约束: **实时阶段不跑 Whisper 和 HUD 分析**，只用 `AudioAnalyzer` + `VideoAnalyzer` 的便宜信号
- 实现策略: 新建一个 `RealtimeStrategy`（实现 `IHighlightStrategy`），只包含 AudioAnalyzer + VideoAnalyzer，不做 ASR 和 HUD。录制中 `RecordingSession` 使用此 strategy 而非完整的 valorant composite strategy
- 输出: 实时预警卡片
- 同步产出: 语音占比、交火密度等判型所需信号

#### CompositeHighlightStrategy（现有，职责收窄）
- 责任:
  - 多策略并行执行（GameStrategy + CommentaryStrategy）
  - 收集原始片段候选
  - 做轻量去重（阈值 0.5，仅合并高度重叠的同一事件片段）
- 不负责:
  - 最终综合排序
  - 母片/短高光最终推荐
  - 试点级别的排序解释输出
- 注意: 本模块替代了初版设计中的 `RoundEventBuilder`。`RoundEventBuilder` 不再作为独立模块存在，其职责（组织回合/交火/语音候选进入统一池）由 CompositeHighlightStrategy 承担。

#### ValorantStreamerProfile（新建，配置对象）
- 类型: **特征权重配置 + 特征提取规则**，不是独立的 `IHighlightStrategy`
- 责任: 定义主播第一视角的特征权重、提取规则、候选加权策略

#### ValorantCommentaryProfile（新建，配置对象）
- 类型: **特征权重配置 + 特征提取规则**，不是独立的 `IHighlightStrategy`
- 责任: 定义解说/陪看的特征权重、提取规则、候选加权策略

#### HighlightRanker（新建）
- 责任:
  - 消费 CompositeHighlightStrategy 输出的候选片段集合
  - 按当前 profile 的权重配置计算六维特征分
  - 综合打分（加权加法，不用乘法，避免某特征接近 0 时直接杀死好内容）
  - 强去重
  - 排序
  - 输出母片推荐和可解释理由
- 输入: `QVector<HighlightSegment>`（来自 CompositeHighlightStrategy）
- 输出: `QVector<RankedClip>`（母片推荐列表，带六维特征分和解释理由）

#### RoundClipBuilder（新建）
- 责任: 接收 HighlightRanker 输出的母片候选，生成精确边界的回合级母片
- 输入: `RankedClip` 列表
- 输出: 边界修正后的母片片段

#### ShortClipRefiner（新建）
- 责任: 从母片内部生成短高光
- 裁切算法见 Section 11.3

#### FeedbackStore（新建）
- 责任: 记录用户行为反馈和标注信息
- 存储格式: 首版使用 JSON 文件（每个素材一个 `.feedback.json`），后续可升级为 SQLite
- 存储位置: 与源视频同目录或用户配置的输出目录

### 6.2 职责边界

首版必须明确区分 `CompositeHighlightStrategy` 与 `HighlightRanker`：

- `CompositeHighlightStrategy`
  - 面向策略执行层
  - 负责多策略并行、收集原始片段、做轻量去重
  - 输出是候选片段集合 (`QVector<HighlightSegment>`)

- `HighlightRanker`
  - 面向结果决策层
  - 消费候选片段集合
  - 负责六维特征计算、加权融合、强去重、排序、母片推荐、解释理由输出
  - 输出是排序后的推荐列表 (`QVector<RankedClip>`)

即：

```
CompositeHighlightStrategy 输出 -> HighlightRanker 输入 -> RoundClipBuilder -> ShortClipRefiner
```

### 6.3 去重分层

首版共三层去重。当 `HighlightRanker` 存在时，上游层降级为轻量合并：

| 层 | 模块 | 阈值 | 职责 | HighlightRanker 存在时 |
|---|---|---|---|---|
| 第 1 层 | `CompositeHighlightStrategy` | overlap ≥ 0.5 | 合并同一策略内的高度重叠片段 | 降为 0.7（仅合并几乎完全重叠者） |
| 第 2 层 | `HighlightRanker` | overlap ≥ 0.35 | 跨候选源的强去重，保留最高分 | 始终生效 |
| 第 3 层 | `AnalysisDock` | 不做去重 | 直接展示 Ranker 输出，不再自行合并 | 去重逻辑禁用 |

---

## 7. 数据结构设计

### 7.1 候选片段（策略层输出，沿用现有结构）

```cpp
// IHighlightStrategy.h — 保持不变
struct HighlightSegment {
    double startSec;
    double endSec;
    double score;        // 策略层原始综合分（仅供参考）
    double audioScore;
    double videoScore;
    double speechScore;
    QString reason;
    QStringList keywords;
};
```

策略层**不填充六维特征**。`HighlightSegment` 保持轻量，只携带时间边界、策略层原始评分和关键词。

### 7.2 排序候选（Ranker 层输出，新增结构）

```cpp
// 新增于 HighlightRanker 或独立头文件
struct RankedClip {
    // 基本信息
    double startSec;
    double endSec;
    QString clipId;          // 唯一标识

    // 六维特征
    double roundImportance;     // 0.0-1.0
    double combatIntensity;     // 0.0-1.0
    double reactionIntensity;   // 0.0-1.0
    double semanticExcitement;  // 0.0-1.0
    double novelty;             // 0.0-1.0（1.0=完全新颖，0.0=完全重复）
    double clipCompleteness;    // 0.0-1.0

    // 综合
    double rankScore;           // 加权融合后的最终排序分
    QStringList signals;        // 命中的信号列表
    QString explanation;        // 可解释的推荐理由
    QString sourceType;         // "round" | "combat" | "speech" | "anomaly"
    int roundIndex;             // 回合序号（如适用）
    QString roundPhase;         // "buy" | "combat" | "post_round"

    // 层级
    QString parentClipId;       // 所属母片 ID（短高光专用，母片为空）
    bool isPrimary;             // 是否主推
    QStringList alternateIds;   // 备选片段 ID 列表

    // 元数据
    QJsonObject metadata;
};
```

### 7.3 分析元数据（输出文件格式）

每个素材分析完成后，输出一个 `<视频文件名>.analysis.json`：

```json
{
  "materialType": "streamer_pov",
  "classificationConfidence": 0.85,
  "profileUsed": "valorant_streamer",
  "fallbackActivated": false,
  "totalDurationSec": 7200.0,
  "roundsDetected": 24,
  "motherClips": [
    {
      "clipId": "mom_001",
      "startSec": 125.3,
      "endSec": 210.5,
      "rankScore": 0.89,
      "features": {
        "roundImportance": 0.7,
        "combatIntensity": 0.9,
        "reactionIntensity": 0.85,
        "semanticExcitement": 0.4,
        "novelty": 0.95,
        "clipCompleteness": 0.8
      },
      "explanation": "高交火 + 主播爆发 + 回合终结",
      "signals": ["audio_peak", "motion_surge", "round_end"],
      "isPrimary": true,
      "shortClips": [
        {
          "clipId": "short_001_1",
          "startSec": 160.0,
          "endSec": 195.0,
          "rankScore": 0.92,
          "isPrimary": true,
          "alternateIds": ["short_001_2"]
        }
      ]
    }
  ],
  "feedbackStatus": "none"
}
```

---

## 8. 双 Profile 设计

### 8.1 主播第一视角 Profile（ValorantStreamerProfile）

目标:
- 更偏个人操作爽点
- 更偏击杀、残局、多杀、主播强反应

重点信号:
- 高频交火和高运动窗口
- 音频爆点和瞬时情绪抬升
- 场景变化密集窗口
- 长残局与回合终结时段

排序权重（默认值，可调）:
- `combatIntensity`: 0.35
- `reactionIntensity`: 0.35
- `roundImportance`: 0.10
- `semanticExcitement`: 0.10
- `novelty`: -0.05 (轻微惩罚重复)
- `clipCompleteness`: 0.15

### 8.2 解说/陪看 Profile（ValorantCommentaryProfile）

目标:
- 更偏故事点和观赛理解
- 更偏团战转折、关键回合、解说高能语义

重点信号:
- 解说语速和语音热度上升
- 高能语义词
- 回合后半段关键对决
- 明显转折和收尾

排序权重（默认值，可调）:
- `combatIntensity`: 0.15
- `reactionIntensity`: 0.15
- `roundImportance`: 0.30
- `semanticExcitement`: 0.30
- `novelty`: -0.05 (轻微惩罚重复)
- `clipCompleteness`: 0.15

### 8.3 加权融合公式

首版采用加法模型（不用乘法，避免某特征接近 0 时直接杀死好内容）：

```
rankScore = Σ (featureValue_i × profileWeight_i)

每项 featureValue 有最小保底分 0.1（novelty 除外）。
clipCompleteness 作为最终修正项，可对 rankScore 施加 ±0.1 的修正。
```

### 8.4 自动判型策略

首版采用规则判型，不引入单独模型。

判型时机: 录制完成后，基于实时粗检阶段积累的信号执行一次。

判型信号:
- 语音占比: ASR 有效文本时长 / 总时长
- 连续讲话长度分布: 中位段长、最大段长
- 解说口吻词命中率: "这波"、"拿下"、"翻盘"等词在总词汇中的占比
- 交火密度: 音频爆点 + 场景变化密度
- 画面运动剧烈程度: 高运动帧占比
- 主播情绪峰值: 音频瞬时响度峰值的频率

判型规则:
- 若连续讲话占比高、平均段长长、解说口吻词命中率高、交火密度适中 → 倾向 `commentary_watchparty`
- 若交火密度高、画面运动剧烈、主播瞬时反应多（短促高音量）、语音分散 → 倾向 `streamer_pov`
- 若两侧得分接近，则输出 `uncertain`

置信度定义:

```
// 先检查最低信号强度
if max(streamerScore, commentaryScore) < 0.2:
    → uncertain（素材信号太弱，无法判型）

// 计算相对差异
confidence = |streamerScore - commentaryScore| / max(streamerScore, commentaryScore)

if confidence < 0.2:
    → uncertain（双 profile 兜底）
else:
    → 按高分类型判型
```

该阈值应做成可调参数（`LscConfig::instance().classificationConfidenceThreshold`），供样本验证后调整。

当判型结果为 `uncertain` 时：
- 同时运行两套 profile 的权重配置
- 最终 rankScore 取两套得分的加权平均（权重 = 各自原始判型分 / 总分）

---

## 9. 时间对齐策略

多信号融合前，必须先解决时间轴不一致问题。首版统一采用**视频 PTS 时间轴**作为主时间基准。

### 9.1 对齐基准

- 所有模块最终输出统一以视频 PTS 秒数表示
- 禁止各模块使用各自内部时间基准直接互相融合

### 9.2 各类信号的对齐要求

- 视频信号:
  - 直接使用视频帧时间戳
- 音频信号:
  - 使用 FFmpeg 解码时产生的时间戳对齐到视频 PTS
- ASR 文本:
  - 使用 Whisper 输出的词级或片段级时间戳对齐到视频 PTS

### 9.3 统一时间粒度

- 首版统一采用 100ms 时间槽作为融合粒度
- 各模块输出可保留更细时间，但 HighlightRanker 融合时按 100ms 聚合

### 9.4 工程要求

- 所有候选和特征输出必须注明其时间基准
- 若模块无法保证时间对齐，应在进入排序前做对齐修正
- 时间对齐工具函数放在 `HighlightUtils` 中

---

## 10. 综合推荐排序器

### 10.1 候选来源

候选片段来源包括：
- 回合边界候选（来自 `RoundBoundaryDetector`，通过 `GameStrategy` 产出）
- 交火候选（来自 `GameStrategy` 的 anchor clustering）
- 语音候选（来自 `CommentaryStrategy` 的语义分段）
- 异常高密度事件候选（多信号同时抬升的窗口）

### 10.2 统一特征集合

六维特征由 `HighlightRanker` 根据候选片段的原始信号计算，不由策略层填充。

#### roundImportance
- 是否为关键回合（赛点、加时、上下半场末尾）
- 是否为回合后半段决胜
- 是否具备残局特征（长时间少人对多人）
- 计算方式: 基于回合序号、比分推断、HUD 事件类型共同判定

#### combatIntensity
- 运动强度（来自 `VideoAnalyzer::motionSegments` 的 motionLevel）
- 场景切换密度（来自 `VideoAnalyzer::sceneChanges` 的频次）
- 音频爆发密度（来自 `AudioAnalyzer::segments` 的 maxDb 超阈值次数）
- 计算方式: 窗口内三个子项的归一化加权和

#### reactionIntensity
- 主播或解说的音量峰值（来自 `AudioAnalyzer` 的 peakDb）
- 连续高能说话时段（来自 ASR 词级时间戳与音频能量的交叉）
- 计算方式: 窗口内音量峰值归一化 + 连续高能段占比

#### semanticExcitement
- 语义关键词命中（无畏契约高光词库: "这波"、"太帅了"、"1vX"、"拿下"、"翻了"、"绝杀"、"离谱"、"漂亮"、"残局"、"翻盘"）
- 高光描述性语句命中（ASR 文本中包含 "这波"、"太"等修饰词的句子）
- 计算方式: 窗口内命中关键词的加权和 / 窗口时长

#### novelty
- 与前后候选的重叠率（使用 `HighlightUtils::overlapRatio`）
- 是否只是同一波高光的碎片
- 计算方式: `1.0 - max_overlap_with_neighbors`

#### clipCompleteness
- 是否有完整起承转合
- 检查规则（规则检查，不是模型打分）:
  - 开头是否在买枪期结束后（不完整扣分）
  - 结尾是否有 2 秒以上静音或画面骤降（戛然而止扣分）
  - 中间是否有超过 3 秒的能量塌陷（节奏断裂扣分）
- 计算方式: 三项检查的通过率（0/3 → 0.0, 3/3 → 1.0）

### 10.3 Profile 权重倾向

#### 主播局
- 更看重 `combatIntensity` (0.35)
- 更看重 `reactionIntensity` (0.35)

#### 解说局
- 更看重 `semanticExcitement` (0.30)
- 更看重 `roundImportance` (0.30)

#### 共同修正项
- `clipCompleteness` (0.15)
- `novelty` (−0.05)

### 10.4 排序输出要求

每条结果必须可解释，输出格式参考：
- "高交火(0.9) + 主播爆发(0.85) + 回合终结 → rankScore=0.89"
- "解说连续高能(0.92) + 关键回合翻盘(0.8) → rankScore=0.87"
- "长残局(0.75) + 高完成度(1.0) → rankScore=0.82"

---

## 11. 实时粗检与录播精修

### 11.1 实时粗检

**关键约束**: 实时阶段不跑 Whisper ASR 和 HUD 分析。这两个是重操作（Whisper 需要数分钟处理全长视频，HUD 需要逐帧图像分析），在录制中跑会拖垮性能。

实现策略:
- 新建 `RealtimeStrategy`（实现 `IHighlightStrategy`），只包含 `AudioAnalyzer` + `VideoAnalyzer`
- 录制中 `RecordingSession` 使用 `RealtimeStrategy`，而不是完整的 valorant composite strategy
- `RecordingSession::onRealtimeAnalysisTimer()` 已有 10 秒间隔的增量分析调度，保持不变
- `HighlightEngine::analyzeIncremental()` 已有增量分析入口，保持不变

实时阶段采用低成本、稳定的信号：
- 音频峰值（来自 `AudioAnalyzer`）
- 运动强度（来自 `VideoAnalyzer::motionSegments`）
- 场景变化（来自 `VideoAnalyzer::sceneChanges`）

同步采集判型信号但不做判型决策:
- 语音占比和关键词分布 → 录制完成后由 `MaterialClassifier` 消费

目标:
- 快速发现疑似高光
- 不阻塞录制
- 不承诺最终边界

输出要求:
- 以卡片方式提示（标注 `sourceTag = "实时高光"`）
- 允许误报
- 重点保证不要错过明显爆点

### 11.2 录播精修

录播完成后对整段素材重新分析：
- 执行 `MaterialClassifier` 判型
- 按判型结果路由到对应 profile 权重配置
- 用完整的 `CompositeHighlightStrategy(GameStrategy + CommentaryStrategy)` 全量跑
  - GameStrategy 包含 AudioAnalyzer + VideoAnalyzer + GameHudAnalyzer
  - CommentaryStrategy 包含 SpeechRecognizer(Whisper)
- 合并并修正候选
- 去重和重排序
- 生成最终切片

录播精修是最终结果唯一可信来源。

---

## 12. 双层切片策略

### 12.1 回合级母片

生成原则：
- 尽量从交战前的合理铺垫开始
- 覆盖高潮和收尾
- 避免只剩爆点瞬间

目标时长:
- 45 到 120 秒

实现要点:
- 调整 `GameStrategy` 中 Valorant fpsTemplate 的 `minRoundSec` 为 45、`maxRoundSec` 为 120
- 当前代码中 fpsTemplate 的 `minRoundSec = 6.0`、`maxRoundSec = 30.0`，远小于无畏契约实际回合长度

### 12.2 短高光

从母片内部二次裁切：
- 起点略早于爆发开始
- 保留 1 到 3 秒铺垫（首版固定 2 秒，不做动态调整，避免动态前置缓冲算出奇怪结果）
- 保留反应后的简短收尾

目标时长:
- 15 到 45 秒

特殊情况:
- 若母片本身 ≤ 50 秒且全段能量密度超过母片均值的 80%，直接用母片作为短高光，不硬切

### 12.3 裁切算法

`ShortClipRefiner` 使用滑动窗口 + 能量密度最大化策略：

```
输入: 母片 [M_start, M_end]
参数: windowMin = 15s, windowMax = 45s, step = 0.5s, paddingSec = 2s

1. 在 [M_start + paddingSec, M_end - paddingSec] 范围内，按 step 步长
   滑动 [w, w+len] 窗口，其中 len ∈ [windowMin, windowMax]

2. 每个窗口计算能量密度:
   density(window) = audioEnergyDensity × 0.4
                   + motionIntensityDensity × 0.35
                   + keywordDensity × 0.25

   其中:
   - audioEnergyDensity = 窗口内 AudioSegment.energy 的均值
   - motionIntensityDensity = 窗口内 MotionSegment.motionLevel 的均值
   - keywordDensity = 窗口内 ASR 关键词命中数 / 窗口时长

3. 取 density 最高的窗口作为候选

4. 若母片 ≤ 50s 且候选窗口的 density ≥ 母片全段 density × 0.8:
   直接用母片作为短高光，跳过裁切

5. 向左右各扩展 paddingSec（2 秒）作为缓冲

6. 边界对齐到最近的 100ms 时间槽

7. 若母片内有多个高密度窗口（density 差距 < 0.1），可产出:
   - 1 个主推（density 最高）
   - 1-2 个备选（标记为 alternateIds）
```

### 12.4 去重规则

- 母片之间高度重叠时 → 保留 rankScore 最高的作为主推荐，其他作为备选
- 短高光之间高度重叠时 → 同理
- 备选片段不丢弃，折叠在主推下方，用户可展开查看

---

## 13. 样本、标注与评估

### 13.1 样本池

首版必须同时建设样本池，至少分为：
- 主播第一视角
- 解说/陪看

每池至少进一步覆盖：
- 普通局
- 高光密集局
- 噪声局（长时间闲聊、排队、切桌面、广告、低能量对局）

### 13.2 标注维度

#### 素材级
- 素材类型
- 语言
- 噪声类型

#### 回合级
- 是否值得保留
- 重要度等级（0-5）
- 高光类型: `多杀 / 残局 / 翻盘 / 解说高能 / 情绪反应 / 其他`

#### 短高光级
- 推荐起点
- 推荐终点
- 是否适合直接发布

### 13.3 标注工具

首版标注工具直接在 `AnalysisDock` 内实现，不单独开发外部工具：

- 时间轴拖动: 在 Shotcut 主时间轴上拖动入点/出点调整边界
- 标注面板: 在 AnalysisDock 中增加标注区域
  - 快捷按钮: "保留" / "删除" / "调整边界"
  - 下拉选择: 高光类型、重要度等级
- 标注存储: 每次标注操作写入 `<视频文件名>.feedback.json`，与 `.analysis.json` 并列存放
- 标注格式: 同 Section 7.3 的分析元数据格式，追加 `feedbackStatus` 和 `manualAdjustment` 字段

### 13.4 评估指标

- 素材判型准确率
- 母片召回率
- 短高光可用率
- Top-N 命中率
- 边界满意度
- 实时预警有效率
- **假阳性驱动率**: 用户看过多少个假高光，才找到一个真的
- **边界修正消耗**: 用户拿到切片后，平均需要手动调整多少秒才能发布

首版目标值:

| 指标 | 首版目标 | 备注 |
|------|----------|------|
| 素材判型准确率 | > 80% | 允许一部分 `uncertain` 兜底 |
| 母片召回率 | > 70% | 人工标注高光回合至少 70% 被召回 |
| 短高光可用率 | > 50% | 可直接发布或少量修改即可发布 |
| Top-10 命中率 | > 80% | 用户看前 10 条能快速找到目标高光 |
| 边界满意度 | 平均偏移 < 3s | 起止点偏移控制在可接受范围 |
| 实时预警有效率 | > 50% | 实时卡片至少一半最终进入精修结果 |
| 假阳性驱动率 | < 3 | 平均看 3 个假高光以内找到 1 个真的 |
| 边界修正消耗 | < 5s | 平均手动调整不超过 5 秒即可发布 |

### 13.5 反馈回流

必须记录用户的以下行为：
- 保留 → 该切片特征权重微增
- 删除 → 该切片特征权重微降
- 调整边界 → 记录手动边界作为标注样本
- 导出 → 强正向信号

这些行为写入 `FeedbackStore`，作为后续调权重和调阈值的依据。

---

## 14. 与当前代码的衔接

首版不是从零搭建，而是在当前 `shotcut-source/src/lsc` 的基础上做无畏契约试点升级。

### 14.1 完整模块映射

| 模块 | 当前状态 | 试点中的用途 | 处理方式 |
|------|------|------|------|
| `HighlightEngine` | 已有 | 总分析入口与链路编排核心 | **修改** — 增加判型后的 profile 路由逻辑 |
| `HighlightEngine::analyzeIncremental()` | 已有 | 实时粗检的调度入口 | **复用** |
| `CompositeHighlightStrategy` | 已有 | 多策略并行、原始候选收集、轻量去重 | **修改** — 去重阈值从 0.5 升至 0.7（当 Ranker 存在时） |
| `GameStrategy` | 已有 | 主播局候选来源（回合、交火） | **修改** — 调整 Valorant fpsTemplate 参数(45-120s)；追加无畏契约关键词 |
| `CommentaryStrategy` | 已有 | 解说局候选来源（语义分段） | **修改** — 追加无畏契约高光词库 |
| `RoundBoundaryDetector` | 已有 | 回合母片边界候选 | **复用** |
| `GameHudAnalyzer` | 已有 | 回合候选、买枪期/回合结束事件 | **复用并增强** — 仅限启发式规则改进，不引入深度学习 OCR |
| `CommentarySegmenter` | 已有 | 语音候选分段 | **复用** |
| `HighlightUtils` | 已有 | 片段合并、重叠率、归一化基础 | **复用并增强** — 增加时间对齐工具函数 |
| `BeatDetector` | 已有 | 音频爆发密度来源（可选） | **复用** |
| `AudioAnalyzer` | 已有 | 实时粗检与录播精修的音频信号 | **复用** |
| `VideoAnalyzer` | 已有 | 交火强度、场景变化特征 | **复用** |
| `SpeechRecognizer` | 已有 | 语音候选、ASR 关键词时间戳 | **复用并增强** — 追加无畏契约专用热词表 |
| `AnalysisProfile` | 已有 | 用户可见分析预设入口 | **修改语义** — `valorant()` 内部路由到自动判型 |
| `AnalysisDock` | 已有 | 展示母片/短高光树形列表、解释理由、标注面板、反馈入口 | **重写** — 平铺列表 → 树形结构 + 标注 + 反馈 |
| `ThumbnailGenerator` | 已有 | 结果缩略图 | **复用** |
| `ClipExporter` | 已有 | 母片和短高光导出 | **复用** |
| `LscConfig` | 已有 | 全局可调参数 | **修改** — 增加试点专用参数（词库、阈值、权重） |
| `RecordingSession` | 已有 | 录制调度 + 实时分析定时器 | **修改** — 实时阶段切换到 RealtimeStrategy |
| `MaterialClassifier` | 不存在 | 素材判型 | **新建** |
| `RealtimeSignalScanner` / `RealtimeStrategy` | 不存在 | 实时轻量扫描 | **新建** |
| `ValorantStreamerProfile` | 不存在 | 主播局特征权重配置 | **新建** |
| `ValorantCommentaryProfile` | 不存在 | 解说局特征权重配置 | **新建** |
| `HighlightRanker` | 不存在 | 六维特征打分、强去重、排序、解释 | **新建** |
| `RoundClipBuilder` | 不存在 | 母片生成与边界修正 | **新建** |
| `ShortClipRefiner` | 不存在 | 短高光裁切 | **新建** |
| `FeedbackStore` | 不存在 | 反馈记录与标注存储 | **新建** |

### 14.2 `AnalysisProfile::valorant()` 的首版语义

当前代码中 `AnalysisProfile::valorant()` 已存在，并在 `HighlightEngine::setAnalysisProfile()` 中直接创建 `CompositeHighlightStrategy(GameStrategy + CommentaryStrategy)`。

首版改造:
- 保留 `AnalysisProfile::valorant()` 作为**用户可见的统一无畏契约入口**
- 不要求 UI 首版直接暴露 `valorant_streamer` / `valorant_commentary` 两个独立选项
- `HighlightEngine::setAnalysisProfile()` 中 valorant 分支的逻辑改为:
  1. 检查是否已有判型结果
  2. 如有 → 按判型结果路由到 streamer 或 commentary 权重配置
  3. 如无（首次）→ 先用 RealtimeStrategy 做轻量扫描，积累信号后判型
  4. 判型为 uncertain → 双 profile 权重融合

试点首版不是"替换掉 valorant 预设"，而是**细化它的内部语义与执行路径**。

### 14.3 职责分工结论

- `RoundBoundaryDetector` → 负责回合边界，不负责交火和语音
- `CommentarySegmenter` → 负责语义分段，不负责回合边界
- `GameStrategy` → 负责游戏向候选（回合 + 交火），不负责语音语义
- `CommentaryStrategy` → 负责语音语义候选，不负责回合/交火
- `CompositeHighlightStrategy` → 并行调度策略、收集原始候选、轻量合并
- `HighlightRanker` → 消费候选、六维打分、强去重、排序、解释输出
- `RealtimeSignalScanner` → 基于 RealtimeStrategy，只跑便宜的 AudioAnalyzer + VideoAnalyzer

---

## 15. 首版阶段划分

### Phase 1: 数据结构 + MaterialClassifier + Profile 配置 + HighlightRanker 骨架
- 新增 `RankedClip` 和 `HighlightRanker` 接口定义
- 新增 `ValorantStreamerProfile` / `ValorantCommentaryProfile` 权重配置
- 新增 `MaterialClassifier` 判型骨架
- `HighlightRanker` 接受 mock 候选可跑通排序
- 新增 `FeedbackStore` JSON 读写骨架
- 在 `LscConfig` 中增加试点参数

交付物: 新数据结构编译通过、HighlightRanker 单元测试通过（mock 候选输入）

### Phase 2: RealtimeStrategy + 候选源接入 + 判型信号采集
- 新建 `RealtimeStrategy`（AudioAnalyzer + VideoAnalyzer only）
- `RecordingSession` 实时阶段切换到 `RealtimeStrategy`
- `GameStrategy` Valorant fpsTemplate 参数调整（45-120s）
- `CommentaryStrategy` 追加无畏契约高光词库
- `GameHudAnalyzer` 启发式规则增强
- `MaterialClassifier` 接入真实判型信号
- `HighlightRanker` 接入 CompositeHighlightStrategy 的真实候选

交付物: 实时粗检可产出预警卡片、录播精修可产出带六维特征的排序结果

### Phase 3: RoundClipBuilder + ShortClipRefiner + AnalysisDock 重写
- `RoundClipBuilder` 母片边界修正
- `ShortClipRefiner` 滑动窗口裁切算法
- `AnalysisDock` 重写为树形结构（母片 → 短高光）+ 标注面板 + 反馈按钮
- 去重分层参数调优

交付物: 端到端可产出母片和短高光、UI 可展示树形结果并支持标注

### Phase 4: 标注、评估、反馈回流
- 样本目录结构建立
- 标注面板功能完善（快捷标注、类型选择、重要度评级）
- `FeedbackStore` 写入用户反馈
- 评估报表（判型准确率、召回率、可用率、修正消耗）
- 反馈回流链路打通

交付物: 可标注、可评估、可调参的完整闭环

### Phase 5: 精度强化与重能力决策
- 基于第一批样本和评估数据，决定:
  - 哪些规则继续保留
  - 哪些需要模型增强（判型？HUD 识别？语义分析？）
  - 是否需要引入更重的 ASR（更大 Whisper 模型？热词增强？）
- 不做预判，等数据说话

---

## 16. 风险与缓解

### 判型不准
- 缓解: `uncertain -> 双跑合并`；且判型公式要求 `max(streamerScore, commentaryScore) >= 0.2`

### 母片边界不稳
- 缓解: 录播精修统一修边界，不信任实时边界

### 短高光过碎
- 缓解: 先保证母片质量，再做二次裁切；≤50s 且高能密集的母片直接用，不硬切

### 没有样本无法调优
- 缓解: 首版同步建设样本、标注、反馈链路

### 过早引入重模型导致工程失控
- 缓解: 先做可解释、可复盘规则链路，再决定升级点

### ASR 质量波动
- 缓解:
  - 继续使用离线 Whisper
  - 引入无畏契约热词表（技能名、地图名、枪械名、常见口播词）
  - 热词表作为 `LscConfig` 可配置项

### 多信号时间戳不同步
- 缓解:
  - 建立统一时间对齐层（`HighlightUtils`）
  - 以视频 PTS 为唯一融合基准

### 录制格式兼容性
- 缓解:
  - 明确最小录制规范
  - 在分析入口做格式检测
  - 必要时自动转码后进入主链路

### 实时分析性能开销
- 缓解:
  - 实时阶段使用 `RealtimeStrategy`（仅 AudioAnalyzer + VideoAnalyzer）
  - 不跑 Whisper 和 HUD
  - 10 秒间隔增量分析，不阻塞录制

---

## 17. 成功标准

当首版满足以下条件时，可视为试点成功：
- 能自动区分大多数主播局与解说局素材，低置信度时能稳定兜底
- 能稳定给出一批可复核的回合级母片（45-120s）
- 能从母片中切出可发布或少量修改即可发布的短高光（15-45s）
- 结果具备推荐理由和六维信号明细
- AnalysisDock 展示母片→短高光的层级结构
- 用户反馈能够回流到后续调参
- 有一套可持续扩充的样本和标注机制
- 假阳性驱动率 < 3，边界修正消耗 < 5s

---

## 18. 当前结论

本项目的首版方向确定为：

**无畏契约专用、自动判型、双 profile、实时轻量粗检 + 录播全量精修、回合母片 + 短高光、六维特征排序 + 可解释推荐、带标注与反馈闭环的工程化高光识别试点。**

后续实施计划将围绕该试点范围展开，不在首版扩展到其他游戏或更重的平台化方案。
