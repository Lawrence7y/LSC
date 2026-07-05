# 高光检测引擎 — 工程实现计划

## 版本：v2.0 | 日期：2026-06-05 | 状态：待评审

---

## 1. 目标概述

将当前单一的通用高光检测器（`HighlightDetector`）重构为**可插拔的多策略检测引擎**，支持用户按直播类型选择预设，输出精准的时间片段。

### 1.1 预设矩阵

| 预设 | 核心信号 | 检测策略 | 输出质量 |
|------|---------|---------|---------|
| **游戏轮次** | 画面模板 + 音效 + 场景骤变 | `GameDetector` | 回合起止时间 |
| **舞蹈卡点** | 节拍 onset × 帧间运动幅度互相关 | `DanceDetector` | 卡点高潮段 |
| **对话切片** | Whisper 语句边界 + 说话人切换 | `DialogDetector` | 完整语句段 |
| **通用高光** | 音频响度 + 场景切换 (现有) | `GenericDetector` | 高能片段 |

### 1.2 非功能需求

- 所有检测器共享统一接口 `IHighlightDetector`
- 核心分析不引入新外部依赖（全部基于 FFmpeg + Qt 实现）
- 单个 10 分钟视频分析耗时 < 30 秒
- 分析结果可叠加（同一视频跑多个预设）

---

## 2. 架构设计

### 2.1 当前架构（v1）

```
HighlightDetector
  ├── AudioAnalyzer   (silencedetect + volumedetect)
  ├── VideoAnalyzer   (scene_change + motion)
  ├── SpeechRecognizer (whisper-cli, 可选)
  └── computeHighlights() ← 硬编码评分公式
```

**问题**：评分公式一刀切，无法适配不同直播类型。

### 2.2 目标架构（v2）

```
HighlightEngine (新)
  │
  ├── IHighlightStrategy (接口)
  │     ├── GameStrategy      (游戏轮次)
  │     ├── DanceStrategy     (舞蹈卡点)
  │     ├── DialogStrategy    (对话切片)
  │     └── GenericStrategy   (通用高光)
  │
  ├── 共享分析基础设施
  │     ├── AudioAnalyzer     (增强: +onset +tempo)
  │     ├── VideoAnalyzer     (增强: +帧级运动幅度)
  │     ├── SpeechRecognizer  (增强: +diarization)
  │     └── BeatDetector      (新增: 节拍检测)
  │
  └── HighlightResult
        ├── segments: [HighlightSegment]
        ├── strategy: string
        └── metadata: QJsonObject
```

### 2.3 模块新增/修改清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `analyzer/IHighlightStrategy.h` | **新增** | 策略接口 |
| `analyzer/BeatDetector.h/.cpp` | **新增** | 节拍/BPM/onset 检测 |
| `analyzer/GameDetector.h/.cpp` | **新增** | 游戏轮次检测 |
| `analyzer/DanceDetector.h/.cpp` | **新增** | 舞蹈卡点检测 |
| `analyzer/DialogDetector.h/.cpp` | **新增** | 对话切片检测 |
| `analyzer/HighlightEngine.h/.cpp` | **新增** | 引擎入口（替代旧 HighlightDetector） |
| `analyzer/AudioAnalyzer.h/.cpp` | **修改** | 增加 onset/tempo 能力 |
| `analyzer/VideoAnalyzer.h/.cpp` | **修改** | 增加逐帧运动幅度输出 |
| `analyzer/SpeechRecognizer.h/.cpp` | **修改** | 增加说话人 diarization |
| `analyzer/HighlightDetector.h/.cpp` | **标记废弃** | 保留兼容，内部委托到引擎 |
| `docks/AnalysisDock.h/.cpp` | **修改** | 增加预设选择 UI |
| `CMakeLists.txt` | **修改** | 添加新文件 |
| `tests/test_beat_detector.cpp` | **新增** | 节拍检测测试 |
| `tests/test_dance_detector.cpp` | **新增** | 舞蹈检测测试 |
| `tests/test_highlight_engine.cpp` | **新增** | 引擎全量测试 |

---

## 3. 核心接口设计

### 3.1 IHighlightStrategy

```cpp
struct HighlightResult {
    QVector<HighlightSegment> segments;
    QString strategyName;
    QJsonObject metadata;       // 策略自定义元数据
};

class IHighlightStrategy : public QObject
{
    Q_OBJECT
public:
    virtual ~IHighlightStrategy() = default;
    virtual QString name() const = 0;
    virtual QString description() const = 0;
    virtual void analyze(const QString& videoPath) = 0;
    virtual void cancel() = 0;
    virtual HighlightResult result() const = 0;
    virtual void configure(const QJsonObject& params) = 0;

signals:
    void progressChanged(int percent);
    void segmentFound(const HighlightSegment& seg);
    void finished();
    void errorOccurred(const QString& msg);
};
```

### 3.2 HighlightEngine（统一入口）

```cpp
class HighlightEngine : public QObject
{
    Q_OBJECT
public:
    void addStrategy(IHighlightStrategy* strategy);
    void analyze(const QString& videoPath);
    QVector<HighlightResult> results() const;
    // 便捷工厂
    static IHighlightStrategy* createGameStrategy(QObject* parent);
    static IHighlightStrategy* createDanceStrategy(QObject* parent);
    static IHighlightStrategy* createDialogStrategy(QObject* parent);
    static IHighlightStrategy* createGenericStrategy(QObject* parent);
};
```

---

## 4. 各策略技术方案

### 4.1 游戏轮次检测 (GameStrategy)

**输入信号**：
1. **场景骤变**：结算/加载画面 → 视频经 `scene_change` 检测阈值 0.4
2. **画面模板匹配**：用 FFmpeg `signature` 滤镜检测已知 UI 模板（击杀提示、胜利图标）
3. **音频尖峰**：特定频段的突发能量（击杀音效通常在 800-2000Hz）

**算法流程**：
```
video → 并行分析
  ├─ scene_change(delta>0.4) → 候选分割点
  ├─ astats(multiband) → 提取中高频段能量突变
  └─ 帧差法 → 屏幕大面积静止 = 加载/菜单
       ↓
多信号融合打分 → 输出 [回合开始, 回合结束] 时间段
```

**关键参数**：
- 场景切换阈值：0.4
- 音频尖峰窗口：0.5s 内能量提升 3x baseline
- 帧差静止阈值：连续 30 帧变化 < 5%

**难点与对策**：
- 不同游戏 UI 不同 → v2 先支持通用的"场景切换 + 音频尖峰"，v3 加游戏模板库
- 回合边界模糊 → 用加载画面（画面骤暗/静止）作为边界锚点

### 4.2 舞蹈卡点检测 (DanceStrategy)

**输入信号**：
1. **节拍 onset**：`BeatDetector` 提取 BPM + 每个节拍的时间戳
2. **帧级运动幅度**：相邻帧像素差的归一化值（0.0-1.0）

**算法流程**：
```
video → BeatDetector(TempoDetect + OnsetDetect)
      → 节拍时间序列: [t0, t1, t2, ... tn]
      → 每个节拍 ±0.3s 窗口内求运动幅度均值
      → 排序取 top-N 窗口作为候选
      → 相邻候选合并 → 输出高潮段
```

**BeatDetector 实现**：

使用 FFmpeg 内置能力，不引入新依赖：
```
ffmpeg -i video -af "aubio=tempo" -vn -f null NUL   → 提取 BPM
ffmpeg -i video -af "aubio=onset=100" -vn -f null NUL → 提取 onset 时间
```

解析 stderr 输出即可获得节拍位置。`aubio` 是 FFmpeg 编译时可选模块，需要在构建 FFmpeg 时启用 `--enable-libaubio`。若不可用，降级方案用 `astats` 的 RMS 突变来近似节拍。

**关键参数**：
- onset 检测阈值：delta > 5dB
- 节拍窗口：±300ms
- 高潮段最小长度：4 拍（约 3-5 秒）
- 合并间距 < 1 拍视为同一段

### 4.3 对话切片检测 (DialogStrategy)

**输入信号**：
1. **Whisper 转录文本**：已有 `SpeechRecognizer`
2. **静音段**：已有的 `silencedetect` 输出
3. **说话人切换**（v2.1）：diarization 识别不同说话人

**算法流程**：
```
video → SpeechRecognizer → 带时间戳的语句列表
      → silencedetect → 静音>1.5s = 自然断句点
      → 合并：同一段连续话语（静音间隔<1.5s）→ 一个切片
      → 关键词匹配（用户预设词库）→ 标记为推荐切片
```

**关键参数**：
- 静音阈值：-40dB，持续 > 1.5s
- 最短切片长度：3 秒
- 最长切片长度：60 秒

**v2.1 扩展**：说话人分离。用 Whisper 的 `--diarize` 或 pyannote，但这是可选增强，v2 先用静音边界切分。

### 4.4 通用高光 (GenericStrategy) — 保持不变

现有逻辑不变，封装为 `GenericStrategy`。

---

## 5. 实施阶段

### Phase 1：基础设施（2天）

| 任务 | 产出 |
|------|------|
| 创建 `IHighlightStrategy` + `HighlightEngine` | 接口 + 引擎框架 |
| 增强 `AudioAnalyzer`：添加 onset/tempo 能力 | `BeatDetector`（封装 AudioAnalyzer 的节拍扩展） |
| 增强 `VideoAnalyzer`：添加逐帧运动幅度 | `getFrameMotion()` 接口 |
| 将现有 `HighlightDetector` 封装为 `GenericStrategy` | 向后兼容 |

### Phase 2：策略实现（3天）

| 任务 | 产出 |
|------|------|
| 实现 `DanceStrategy` | 节拍+运动互相关检测 |
| 实现 `DialogStrategy` | 语句边界切片 |
| 实现 `GameStrategy` | 场景切换+音频尖峰融合 |

### Phase 3：UI 与集成（1天）

| 任务 | 产出 |
|------|------|
| 改造 `AnalysisDock`：添加预设选择下拉框 | 用户可选游戏/舞蹈/对话/通用 |
| 添加策略参数配置面板 | 阈值滑块、关键词输入等 |
| 连接 `LivestreamDock.recordingFinished` → 自动分析 | 录制完自动启动分析 |

### Phase 4：测试与打磨（2天）

| 任务 | 产出 |
|------|------|
| `test_beat_detector` | 节拍检测准确率测试 |
| `test_dance_detector` | 舞蹈卡点测试 |
| `test_highlight_engine` | 引擎多策略并发测试 |
| 性能测试 | 10 分钟视频分析 < 30s |
| 端到端录制+分析 | 全流程验证 |

---

## 6. 关键决策待确认

| # | 决策点 | 选项 | 推荐 |
|---|--------|------|------|
| 1 | `BeatDetector` 依赖 | A: FFmpeg aubio（需 FFmpeg 支持） B: 纯 RMS 突变近似 | **A**，运行时检测，无 aubio 降级到 B |
| 2 | 说话人分离 | A: 暂不做 B: **v2 就要做** | **B**，引入 pyannote 或 whisper diarize |
| 3 | 游戏模板 | A: 内置 3-5 款 B: **无畏契约 + CS2 + 通用** | **B**，两款 FPS + 通用检测框架 |
| 4 | 旧 `HighlightDetector` | A: 删除 B: 保留并标记 deprecated | **B**，保持兼容 |

---

## 7. 风险矩阵

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|----------|
| FFmpeg 不含 aubio | 中 | 舞蹈策略降级 | 自动检测+降级到 RMS 方案 |
| 游戏 UI 多样导致误检 | 高 | 准确率下降 | 先用通用模板，后续扩展 |
| Whisper 中文准确率不够 | 中 | 对话切片质量 | 提供置信度过滤阈值 |
| 多策略并行导致性能问题 | 低 | 分析变慢 | 串行执行，按需运行 |

---

## 8. 成功指标

| 指标 | 目标值 |
|------|--------|
| 舞蹈切片卡点准确率 | > 85%（人工抽样 20 个切片） |
| 对话切片语句完整性 | > 90% 不截断句子 |
| 游戏轮次检测召回率 | > 70% |
| 10 分钟视频全策略分析时间 | < 30 秒 |
| 单元测试覆盖率 | > 80% |
