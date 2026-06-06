# LSC Preview And AI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为独立 GUI 程序 `lsc_app.exe` 增加“开始录制即预览、停止即关闭预览”的直播预览能力，并把 AI 分析扩展为可用于舞蹈直播和《无畏契约》直播的可导出切片流程。

**Architecture:** 保持 `lsc_app.exe` 作为 Qt 桌面壳层，继续复用现有 `LivestreamDock -> RecordingSession -> StreamCapture -> HighlightEngine -> AnalysisDock` 主链路。新增一条独立的预览控制链路用于录制中画面播放，同时把 AI 分析从单策略执行器升级为“分析配置 + 多检测器编排 + 结果合并”的结构，优先在本地落地舞蹈片段、Valorant 每局分段、解说语义片段三种结果。

**Tech Stack:** C++17, Qt6 Widgets/Network/Multimedia/MultimediaWidgets, FFmpeg/ffprobe, Whisper CLI, 现有 LSC 测试可执行程序, CMake, MSBuild, CTest

---

### Task 1: 建立直播预览与分析配置的公共边界

**Files:**
- Modify: `shotcut-source/src/lsc/livestream/StreamCapture.h`
- Modify: `shotcut-source/src/lsc/livestream/RecordingSession.h`
- Modify: `shotcut-source/src/lsc/livestream/RecordingSession.cpp`
- Modify: `shotcut-source/src/lsc/docks/LivestreamDock.h`
- Modify: `shotcut-source/src/lsc/docks/LivestreamDock.cpp`
- Modify: `shotcut-source/src/lsc/app/MainWindow.cpp`
- Create: `shotcut-source/src/lsc/analyzer/AnalysisProfile.h`
- Test: `shotcut-source/src/lsc/tests/test_recording_session.cpp`

- [ ] **Step 1: 先写 `RecordingSession` 对预览和分析配置的失败测试**

```cpp
void runPreviewContractTests()
{
    RecordingSession session;
    runTest("preview disabled by default", !session.previewEnabled());
    runTest("analysis profile defaults to generic", session.analysisProfile().id == "generic");
}
```

- [ ] **Step 2: 运行录制会话测试确认新增断言先失败**

```powershell
cmake --build 'D:\Project\直播切片\shotcut-source\src\lsc\build' --config Release --target test_recording_session
ctest --test-dir 'D:\Project\直播切片\shotcut-source\src\lsc\build' -C Release --output-on-failure -R test_recording_session
```

Expected: `test_recording_session` 编译失败或断言失败，提示 `previewEnabled` / `analysisProfile` 尚未定义。

- [ ] **Step 3: 新增分析配置结构并把它挂到录制会话**

```cpp
struct AnalysisProfile {
    QString id;
    QString displayName;
    bool enableRealtimePreview = true;
    bool enableRealtimeHighlight = true;
    bool enableRoundSegmentation = false;
    bool enableCommentarySegmentation = false;
    bool enableDanceSegmentation = false;
    QString gameKey;
};
```

```cpp
class RecordingSession : public QObject
{
    Q_OBJECT
public:
    void setAnalysisProfile(const AnalysisProfile& profile);
    AnalysisProfile analysisProfile() const { return m_analysisProfile; }
    bool previewEnabled() const { return m_analysisProfile.enableRealtimePreview; }
signals:
    void previewSourceChanged(const QString& sourcePath);
    void previewStopped();
private:
    AnalysisProfile m_analysisProfile{QStringLiteral("generic"), QStringLiteral("通用")};
};
```

- [ ] **Step 4: 在 `LivestreamDock` 和 `MainWindow` 暴露内容类型选择并向 `RecordingSession` 注入配置**

```cpp
m_contentProfileCombo->addItem(QString::fromUtf8("通用直播"), QStringLiteral("generic"));
m_contentProfileCombo->addItem(QString::fromUtf8("舞蹈直播"), QStringLiteral("dance"));
m_contentProfileCombo->addItem(QString::fromUtf8("无畏契约"), QStringLiteral("valorant"));
m_contentProfileCombo->addItem(QString::fromUtf8("解说切片"), QStringLiteral("commentary"));
```

```cpp
AnalysisProfile LivestreamDock::buildAnalysisProfileFromUi() const
{
    if (m_contentProfileCombo->currentData().toString() == "valorant") {
        return { "valorant", QString::fromUtf8("无畏契约"), true, true, true, true, false, "valorant" };
    }
    if (m_contentProfileCombo->currentData().toString() == "dance") {
        return { "dance", QString::fromUtf8("舞蹈直播"), true, true, false, false, true, QString() };
    }
    if (m_contentProfileCombo->currentData().toString() == "commentary") {
        return { "commentary", QString::fromUtf8("解说切片"), true, true, false, true, false, QString() };
    }
    return { "generic", QString::fromUtf8("通用直播"), true, true, false, false, false, QString() };
}
```

- [ ] **Step 5: 重新运行录制会话测试确认公共边界可用**

```powershell
cmake --build 'D:\Project\直播切片\shotcut-source\src\lsc\build' --config Release --target test_recording_session lsc_app
ctest --test-dir 'D:\Project\直播切片\shotcut-source\src\lsc\build' -C Release --output-on-failure -R test_recording_session
```

Expected: `test_recording_session` 通过，且 `lsc_app` 编译通过。

- [ ] **Step 6: 提交这一层公共边界**

```powershell
git add 'D:\Project\直播切片\shotcut-source\src\lsc\analyzer\AnalysisProfile.h' 'D:\Project\直播切片\shotcut-source\src\lsc\livestream\RecordingSession.h' 'D:\Project\直播切片\shotcut-source\src\lsc\livestream\RecordingSession.cpp' 'D:\Project\直播切片\shotcut-source\src\lsc\docks\LivestreamDock.h' 'D:\Project\直播切片\shotcut-source\src\lsc\docks\LivestreamDock.cpp' 'D:\Project\直播切片\shotcut-source\src\lsc\app\MainWindow.cpp' 'D:\Project\直播切片\shotcut-source\src\lsc\tests\test_recording_session.cpp'
git commit -m "feat: add analysis profile contract for lsc app"
```

### Task 2: 实现录制即预览、停止即关闭的直播预览链路

**Files:**
- Create: `shotcut-source/src/lsc/docks/LivePreviewWidget.h`
- Create: `shotcut-source/src/lsc/docks/LivePreviewWidget.cpp`
- Create: `shotcut-source/src/lsc/livestream/PreviewController.h`
- Create: `shotcut-source/src/lsc/livestream/PreviewController.cpp`
- Modify: `shotcut-source/src/lsc/docks/LivestreamDock.h`
- Modify: `shotcut-source/src/lsc/docks/LivestreamDock.cpp`
- Modify: `shotcut-source/src/lsc/livestream/RecordingSession.h`
- Modify: `shotcut-source/src/lsc/livestream/RecordingSession.cpp`
- Modify: `shotcut-source/src/lsc/CMakeLists.txt`
- Create: `shotcut-source/src/lsc/tests/test_live_preview.cpp`

- [ ] **Step 1: 先写预览控制的失败测试**

```cpp
int main(int argc, char* argv[])
{
    QCoreApplication app(argc, argv);
    PreviewController controller;
    bool sawPreviewStart = false;
    bool sawPreviewStop = false;

    QObject::connect(&controller, &PreviewController::previewAvailable, [&](const QString& path) {
        sawPreviewStart = !path.isEmpty();
    });
    QObject::connect(&controller, &PreviewController::previewCleared, [&]() {
        sawPreviewStop = true;
    });

    controller.setPreviewSource("D:/temp/live_frag.mp4");
    controller.clearPreviewSource();

    return (!sawPreviewStart || !sawPreviewStop) ? 1 : 0;
}
```

- [ ] **Step 2: 运行新测试确认预览类尚不存在**

```powershell
cmake --build 'D:\Project\直播切片\shotcut-source\src\lsc\build' --config Release --target test_live_preview
```

Expected: CMake 或编译阶段失败，提示 `PreviewController` / `LivePreviewWidget` 未定义。

- [ ] **Step 3: 新增预览控制器和直播预览控件**

```cpp
class PreviewController : public QObject
{
    Q_OBJECT
public:
    void setPreviewSource(const QString& sourcePath);
    void clearPreviewSource();
signals:
    void previewAvailable(const QString& sourcePath);
    void previewCleared();
private:
    QString m_sourcePath;
};
```

```cpp
class LivePreviewWidget : public QWidget
{
    Q_OBJECT
public:
    void startPreview(const QString& sourcePath);
    void stopPreview();
    bool isPreviewing() const;
private slots:
    void onMediaStatusChanged(QMediaPlayer::MediaStatus status);
};
```

- [ ] **Step 4: 把开始录制与停止录制接到预览生命周期**

```cpp
connect(m_session, &RecordingSession::previewSourceChanged,
        m_previewController, &PreviewController::setPreviewSource);
connect(m_session, &RecordingSession::previewStopped,
        m_previewController, &PreviewController::clearPreviewSource);
connect(m_previewController, &PreviewController::previewAvailable,
        m_livePreviewWidget, &LivePreviewWidget::startPreview);
connect(m_previewController, &PreviewController::previewCleared,
        m_livePreviewWidget, &LivePreviewWidget::stopPreview);
```

```cpp
void RecordingSession::onCaptureStatusChanged(RecordingStatus status)
{
    emit statusChanged(status);
    if (status == RecordingStatus::Recording && previewEnabled()) {
        emit previewSourceChanged(m_config.outputPath);
    } else if (status == RecordingStatus::Stopped || status == RecordingStatus::Error) {
        emit previewStopped();
    }
}
```

- [ ] **Step 5: 把新控件编入 `lsc` 静态库并运行预览测试**

```powershell
cmake -S 'D:\Project\直播切片\shotcut-source\src\lsc' -B 'D:\Project\直播切片\shotcut-source\src\lsc\build'
cmake --build 'D:\Project\直播切片\shotcut-source\src\lsc\build' --config Release --target test_live_preview lsc_app
ctest --test-dir 'D:\Project\直播切片\shotcut-source\src\lsc\build' -C Release --output-on-failure -R "test_live_preview|test_recording_session"
```

Expected: 两个测试通过，`lsc_app` 可编译。

- [ ] **Step 6: 提交预览主链路**

```powershell
git add 'D:\Project\直播切片\shotcut-source\src\lsc\docks\LivePreviewWidget.h' 'D:\Project\直播切片\shotcut-source\src\lsc\docks\LivePreviewWidget.cpp' 'D:\Project\直播切片\shotcut-source\src\lsc\livestream\PreviewController.h' 'D:\Project\直播切片\shotcut-source\src\lsc\livestream\PreviewController.cpp' 'D:\Project\直播切片\shotcut-source\src\lsc\docks\LivestreamDock.h' 'D:\Project\直播切片\shotcut-source\src\lsc\docks\LivestreamDock.cpp' 'D:\Project\直播切片\shotcut-source\src\lsc\livestream\RecordingSession.h' 'D:\Project\直播切片\shotcut-source\src\lsc\livestream\RecordingSession.cpp' 'D:\Project\直播切片\shotcut-source\src\lsc\CMakeLists.txt' 'D:\Project\直播切片\shotcut-source\src\lsc\tests\test_live_preview.cpp'
git commit -m "feat: add live preview lifecycle to lsc recorder"
```

### Task 3: 把录制中的实时高光流入分析面板

**Files:**
- Modify: `shotcut-source/src/lsc/docks/AnalysisDock.h`
- Modify: `shotcut-source/src/lsc/docks/AnalysisDock.cpp`
- Modify: `shotcut-source/src/lsc/app/MainWindow.cpp`
- Modify: `shotcut-source/src/lsc/analyzer/HighlightEngine.h`
- Modify: `shotcut-source/src/lsc/analyzer/HighlightEngine.cpp`
- Create: `shotcut-source/src/lsc/tests/test_analysis_dock.cpp`
- Modify: `shotcut-source/src/lsc/CMakeLists.txt`

- [ ] **Step 1: 先写分析面板接收实时片段的失败测试**

```cpp
AnalysisDock dock;
HighlightSegment seg{12.0, 24.0, 0.8, 0.5, 0.7, 0.0, QStringLiteral("realtime"), {}};
dock.ingestRealtimeSegment(seg, "D:/temp/live_frag.mp4");
check("analysis dock stores realtime card", dock.videoPath() == "D:/temp/live_frag.mp4");
```

- [ ] **Step 2: 运行面板测试确认 `ingestRealtimeSegment` 尚未实现**

```powershell
cmake --build 'D:\Project\直播切片\shotcut-source\src\lsc\build' --config Release --target test_analysis_dock
```

Expected: 编译失败，提示 `AnalysisDock::ingestRealtimeSegment` 不存在。

- [ ] **Step 3: 给 `AnalysisDock` 增加实时结果入口并标注来源**

```cpp
struct HighlightCardData {
    double startSec = 0.0;
    double endSec = 0.0;
    double score = 0.0;
    QString reason;
    QString sourceTag;
    bool realtime = false;
};
```

```cpp
void AnalysisDock::ingestRealtimeSegment(const HighlightSegment& segment, const QString& videoPath)
{
    if (m_videoPath != videoPath) {
        setVideoPath(videoPath);
    }
    HighlightCardData card;
    card.startSec = segment.startSec;
    card.endSec = segment.endSec;
    card.score = segment.score;
    card.reason = segment.reason;
    card.sourceTag = QStringLiteral("实时高光");
    card.realtime = true;
    m_cards.append(card);
    updateCardWidget(m_cards.size() - 1);
}
```

- [ ] **Step 4: 在主窗口里把录制中的高光直接推到分析面板**

```cpp
connect(m_livestreamDock, &LivestreamDock::highlightFound, this,
        [this](const HighlightSegment& seg) {
            m_analysisDock->ingestRealtimeSegment(seg, m_livestreamDock->session()->outputPath());
        });
```

- [ ] **Step 5: 运行分析面板和引擎测试**

```powershell
cmake --build 'D:\Project\直播切片\shotcut-source\src\lsc\build' --config Release --target test_analysis_dock test_highlight_engine lsc_app
ctest --test-dir 'D:\Project\直播切片\shotcut-source\src\lsc\build' -C Release --output-on-failure -R "test_analysis_dock|test_highlight_engine"
```

Expected: 分析面板测试和引擎测试通过。

- [ ] **Step 6: 提交实时结果流入分析面板的改造**

```powershell
git add 'D:\Project\直播切片\shotcut-source\src\lsc\docks\AnalysisDock.h' 'D:\Project\直播切片\shotcut-source\src\lsc\docks\AnalysisDock.cpp' 'D:\Project\直播切片\shotcut-source\src\lsc\app\MainWindow.cpp' 'D:\Project\直播切片\shotcut-source\src\lsc\analyzer\HighlightEngine.h' 'D:\Project\直播切片\shotcut-source\src\lsc\analyzer\HighlightEngine.cpp' 'D:\Project\直播切片\shotcut-source\src\lsc\tests\test_analysis_dock.cpp' 'D:\Project\直播切片\shotcut-source\src\lsc\CMakeLists.txt'
git commit -m "feat: surface realtime highlight cards in analysis dock"
```

### Task 4: 把单策略引擎升级为可编排的分析管线

**Files:**
- Create: `shotcut-source/src/lsc/analyzer/CompositeHighlightStrategy.h`
- Create: `shotcut-source/src/lsc/analyzer/CompositeHighlightStrategy.cpp`
- Create: `shotcut-source/src/lsc/analyzer/CommentaryStrategy.h`
- Create: `shotcut-source/src/lsc/analyzer/CommentaryStrategy.cpp`
- Modify: `shotcut-source/src/lsc/analyzer/HighlightEngine.h`
- Modify: `shotcut-source/src/lsc/analyzer/HighlightEngine.cpp`
- Modify: `shotcut-source/src/lsc/docks/AnalysisDock.cpp`
- Modify: `shotcut-source/src/lsc/app/MainWindow.cpp`
- Modify: `shotcut-source/src/lsc/CMakeLists.txt`
- Modify: `shotcut-source/src/lsc/tests/test_highlight_engine.cpp`

- [ ] **Step 1: 先写引擎编排行为的失败测试**

```cpp
HighlightEngine engine;
engine.setAnalysisProfile({ "valorant", QStringLiteral("无畏契约"), true, true, true, true, false, "valorant" });
check("engine can build composite strategy", engine.currentStrategy() != nullptr);
check("composite strategy exposes profile name",
      engine.currentStrategy()->name().contains("valorant"));
```

- [ ] **Step 2: 运行引擎测试确认 `setAnalysisProfile` 尚不存在**

```powershell
cmake --build 'D:\Project\直播切片\shotcut-source\src\lsc\build' --config Release --target test_highlight_engine
ctest --test-dir 'D:\Project\直播切片\shotcut-source\src\lsc\build' -C Release --output-on-failure -R test_highlight_engine
```

Expected: 测试编译失败，提示 `HighlightEngine::setAnalysisProfile` 不存在。

- [ ] **Step 3: 为 `HighlightEngine` 增加基于配置的策略装配入口**

```cpp
class HighlightEngine : public QObject
{
    Q_OBJECT
public:
    void setAnalysisProfile(const AnalysisProfile& profile);
    AnalysisProfile analysisProfile() const { return m_profile; }
private:
    AnalysisProfile m_profile{QStringLiteral("generic"), QStringLiteral("通用")};
};
```

```cpp
void HighlightEngine::setAnalysisProfile(const AnalysisProfile& profile)
{
    m_profile = profile;
    if (profile.id == QStringLiteral("valorant")) {
        auto* composite = new CompositeHighlightStrategy(this);
        composite->addStrategy(createGameStrategy(composite));
        composite->addStrategy(new CommentaryStrategy(composite));
        setStrategy(composite);
        return;
    }
    if (profile.id == QStringLiteral("dance")) {
        setStrategy(createDanceStrategy(this));
        return;
    }
    if (profile.id == QStringLiteral("commentary")) {
        setStrategy(new CommentaryStrategy(this));
        return;
    }
    setStrategy(createGenericStrategy(this));
}
```

- [ ] **Step 4: 用 `CompositeHighlightStrategy` 合并多个检测器结果并按时间去重**

```cpp
class CompositeHighlightStrategy : public IHighlightStrategy
{
    Q_OBJECT
public:
    void addStrategy(IHighlightStrategy* strategy);
    void analyze(const QString& videoPath) override;
private slots:
    void onChildFinished();
    void onChildSegment(const HighlightSegment& segment);
private:
    QVector<IHighlightStrategy*> m_strategies;
    QVector<HighlightSegment> m_segments;
};
```

- [ ] **Step 5: 运行引擎测试确认多策略编排可工作**

```powershell
cmake --build 'D:\Project\直播切片\shotcut-source\src\lsc\build' --config Release --target test_highlight_engine lsc_app
ctest --test-dir 'D:\Project\直播切片\shotcut-source\src\lsc\build' -C Release --output-on-failure -R test_highlight_engine
```

Expected: `test_highlight_engine` 通过，现有 generic / game / incremental 流程不回归。

- [ ] **Step 6: 提交分析引擎编排层**

```powershell
git add 'D:\Project\直播切片\shotcut-source\src\lsc\analyzer\CompositeHighlightStrategy.h' 'D:\Project\直播切片\shotcut-source\src\lsc\analyzer\CompositeHighlightStrategy.cpp' 'D:\Project\直播切片\shotcut-source\src\lsc\analyzer\CommentaryStrategy.h' 'D:\Project\直播切片\shotcut-source\src\lsc\analyzer\CommentaryStrategy.cpp' 'D:\Project\直播切片\shotcut-source\src\lsc\analyzer\HighlightEngine.h' 'D:\Project\直播切片\shotcut-source\src\lsc\analyzer\HighlightEngine.cpp' 'D:\Project\直播切片\shotcut-source\src\lsc\docks\AnalysisDock.cpp' 'D:\Project\直播切片\shotcut-source\src\lsc\app\MainWindow.cpp' 'D:\Project\直播切片\shotcut-source\src\lsc\CMakeLists.txt' 'D:\Project\直播切片\shotcut-source\src\lsc\tests\test_highlight_engine.cpp'
git commit -m "feat: add profile-driven composite highlight engine"
```

### Task 5: 落地舞蹈直播片段识别 v1

**Files:**
- Create: `shotcut-source/src/lsc/analyzer/PoseAnalyzer.h`
- Create: `shotcut-source/src/lsc/analyzer/PoseAnalyzer.cpp`
- Create: `shotcut-source/src/lsc/analyzer/DanceSegmentScorer.h`
- Create: `shotcut-source/src/lsc/analyzer/DanceSegmentScorer.cpp`
- Modify: `shotcut-source/src/lsc/analyzer/DanceStrategy.h`
- Modify: `shotcut-source/src/lsc/analyzer/DanceStrategy.cpp`
- Modify: `shotcut-source/src/lsc/CMakeLists.txt`
- Create: `shotcut-source/src/lsc/tests/test_dance_segment_scorer.cpp`

- [ ] **Step 1: 先写舞蹈评分器的失败测试**

```cpp
DanceSegmentScorer scorer;
DanceFeatures features;
features.beatAlignment = 0.82;
features.motionStrength = 0.76;
features.poseConfidence = 0.88;
features.subjectCoverage = 0.71;
check("dance score stays high for stable dancer segment", scorer.score(features) > 0.75);
```

- [ ] **Step 2: 运行舞蹈评分测试确认新类尚不存在**

```powershell
cmake --build 'D:\Project\直播切片\shotcut-source\src\lsc\build' --config Release --target test_dance_segment_scorer
```

Expected: 编译失败，提示 `DanceSegmentScorer` / `DanceFeatures` 未定义。

- [ ] **Step 3: 新增姿态分析器和舞蹈评分器**

```cpp
struct PoseWindow {
    double startSec = 0.0;
    double endSec = 0.0;
    double poseConfidence = 0.0;
    double subjectCoverage = 0.0;
    double limbVelocity = 0.0;
};
```

```cpp
struct DanceFeatures {
    double beatAlignment = 0.0;
    double motionStrength = 0.0;
    double poseConfidence = 0.0;
    double subjectCoverage = 0.0;
};

class DanceSegmentScorer
{
public:
    double score(const DanceFeatures& features) const
    {
        return qBound(0.0,
                      features.beatAlignment * 0.35
                          + features.motionStrength * 0.25
                          + features.poseConfidence * 0.20
                          + features.subjectCoverage * 0.20,
                      1.0);
    }
};
```

- [ ] **Step 4: 用新特征重写 `DanceStrategy` 的高光判定**

```cpp
DanceFeatures features;
features.beatAlignment = beatAlignment;
features.motionStrength = avgMotion;
features.poseConfidence = poseWindow.poseConfidence;
features.subjectCoverage = poseWindow.subjectCoverage;

const double score = m_segmentScorer.score(features);
if (score >= scoreThreshold) {
    seg.reason = QString::fromUtf8("舞蹈片段: beat=%1 motion=%2 pose=%3 cover=%4")
        .arg(features.beatAlignment, 0, 'f', 2)
        .arg(features.motionStrength, 0, 'f', 2)
        .arg(features.poseConfidence, 0, 'f', 2)
        .arg(features.subjectCoverage, 0, 'f', 2);
}
```

- [ ] **Step 5: 运行舞蹈相关测试**

```powershell
cmake --build 'D:\Project\直播切片\shotcut-source\src\lsc\build' --config Release --target test_dance_detector test_dance_segment_scorer test_highlight_engine
ctest --test-dir 'D:\Project\直播切片\shotcut-source\src\lsc\build' -C Release --output-on-failure -R "test_dance_detector|test_dance_segment_scorer|test_highlight_engine"
```

Expected: 舞蹈检测测试和引擎回归测试通过。

- [ ] **Step 6: 提交舞蹈识别 v1**

```powershell
git add 'D:\Project\直播切片\shotcut-source\src\lsc\analyzer\PoseAnalyzer.h' 'D:\Project\直播切片\shotcut-source\src\lsc\analyzer\PoseAnalyzer.cpp' 'D:\Project\直播切片\shotcut-source\src\lsc\analyzer\DanceSegmentScorer.h' 'D:\Project\直播切片\shotcut-source\src\lsc\analyzer\DanceSegmentScorer.cpp' 'D:\Project\直播切片\shotcut-source\src\lsc\analyzer\DanceStrategy.h' 'D:\Project\直播切片\shotcut-source\src\lsc\analyzer\DanceStrategy.cpp' 'D:\Project\直播切片\shotcut-source\src\lsc\CMakeLists.txt' 'D:\Project\直播切片\shotcut-source\src\lsc\tests\test_dance_segment_scorer.cpp'
git commit -m "feat: add dance segment scoring for livestream clips"
```

### Task 6: 落地《无畏契约》每局切片和解说内容切片

**Files:**
- Create: `shotcut-source/src/lsc/analyzer/GameHudAnalyzer.h`
- Create: `shotcut-source/src/lsc/analyzer/GameHudAnalyzer.cpp`
- Create: `shotcut-source/src/lsc/analyzer/RoundBoundaryDetector.h`
- Create: `shotcut-source/src/lsc/analyzer/RoundBoundaryDetector.cpp`
- Create: `shotcut-source/src/lsc/analyzer/CommentarySegmenter.h`
- Create: `shotcut-source/src/lsc/analyzer/CommentarySegmenter.cpp`
- Modify: `shotcut-source/src/lsc/analyzer/GameStrategy.h`
- Modify: `shotcut-source/src/lsc/analyzer/GameStrategy.cpp`
- Modify: `shotcut-source/src/lsc/analyzer/DialogStrategy.cpp`
- Modify: `shotcut-source/src/lsc/analyzer/SpeechRecognizer.cpp`
- Modify: `shotcut-source/src/lsc/tests/test_highlight_engine.cpp`
- Create: `shotcut-source/src/lsc/tests/test_round_boundary_detector.cpp`
- Create: `shotcut-source/src/lsc/tests/test_commentary_segmenter.cpp`
- Modify: `shotcut-source/src/lsc/CMakeLists.txt`

- [ ] **Step 1: 先写回合分界和解说语义切片的失败测试**

```cpp
RoundBoundaryDetector detector;
QVector<HudEvent> events{
    {5.0, "buy_phase"},
    {105.0, "round_end"},
    {120.0, "buy_phase"},
    {215.0, "round_end"},
};
const auto rounds = detector.buildRounds(events);
check("valorant detector outputs two rounds", rounds.size() == 2);
```

```cpp
CommentarySegmenter segmenter;
QVector<SubtitleEntry> subtitles{
    {0.0, 2.0, QStringLiteral("这一把开局先控中路"), 0.9},
    {2.2, 6.0, QStringLiteral("A点两个人已经进去了"), 0.9},
};
const auto segments = segmenter.buildSegments(subtitles, {});
check("commentary segmenter merges adjacent theme lines", segments.size() == 1);
```

- [ ] **Step 2: 运行新测试确认相关类型尚不存在**

```powershell
cmake --build 'D:\Project\直播切片\shotcut-source\src\lsc\build' --config Release --target test_round_boundary_detector test_commentary_segmenter
```

Expected: 编译失败，提示 `RoundBoundaryDetector` / `CommentarySegmenter` 尚未实现。

- [ ] **Step 3: 新增 Valorant HUD 事件和回合边界检测器**

```cpp
struct HudEvent {
    double timestampSec = 0.0;
    QString type;
    QString text;
    double confidence = 0.0;
};

struct RoundSegment {
    double startSec = 0.0;
    double endSec = 0.0;
    QString title;
};
```

```cpp
QVector<RoundSegment> RoundBoundaryDetector::buildRounds(const QVector<HudEvent>& events) const
{
    QVector<RoundSegment> rounds;
    double currentStart = -1.0;
    for (const HudEvent& event : events) {
        if (event.type == "buy_phase") {
            currentStart = event.timestampSec;
        } else if (event.type == "round_end" && currentStart >= 0.0) {
            rounds.append({currentStart, event.timestampSec, QStringLiteral("Valorant Round")});
            currentStart = -1.0;
        }
    }
    return rounds;
}
```

- [ ] **Step 4: 用回合分界 + 语音转写重写 FPS 和解说切片逻辑**

```cpp
if (m_profile.gameKey == QStringLiteral("valorant")) {
    const auto rounds = m_roundDetector.buildRounds(m_hudAnalyzer->events());
    for (const RoundSegment& round : rounds) {
        HighlightSegment seg;
        seg.startSec = round.startSec;
        seg.endSec = round.endSec;
        seg.reason = QString::fromUtf8("无畏契约对局片段");
        seg.score = 0.80;
        emit segmentFound(seg);
    }
}
```

```cpp
const auto commentarySegments = m_commentarySegmenter.buildSegments(m_subtitles, m_keywords);
for (const HighlightSegment& segment : commentarySegments) {
    emit segmentFound(segment);
}
```

- [ ] **Step 5: 运行游戏与语义切片测试**

```powershell
cmake --build 'D:\Project\直播切片\shotcut-source\src\lsc\build' --config Release --target test_round_boundary_detector test_commentary_segmenter test_highlight_engine lsc_app
ctest --test-dir 'D:\Project\直播切片\shotcut-source\src\lsc\build' -C Release --output-on-failure -R "test_round_boundary_detector|test_commentary_segmenter|test_highlight_engine"
```

Expected: 新增两个测试和引擎回归测试通过。

- [ ] **Step 6: 提交 Valorant 与解说切片能力**

```powershell
git add 'D:\Project\直播切片\shotcut-source\src\lsc\analyzer\GameHudAnalyzer.h' 'D:\Project\直播切片\shotcut-source\src\lsc\analyzer\GameHudAnalyzer.cpp' 'D:\Project\直播切片\shotcut-source\src\lsc\analyzer\RoundBoundaryDetector.h' 'D:\Project\直播切片\shotcut-source\src\lsc\analyzer\RoundBoundaryDetector.cpp' 'D:\Project\直播切片\shotcut-source\src\lsc\analyzer\CommentarySegmenter.h' 'D:\Project\直播切片\shotcut-source\src\lsc\analyzer\CommentarySegmenter.cpp' 'D:\Project\直播切片\shotcut-source\src\lsc\analyzer\GameStrategy.h' 'D:\Project\直播切片\shotcut-source\src\lsc\analyzer\GameStrategy.cpp' 'D:\Project\直播切片\shotcut-source\src\lsc\analyzer\DialogStrategy.cpp' 'D:\Project\直播切片\shotcut-source\src\lsc\analyzer\SpeechRecognizer.cpp' 'D:\Project\直播切片\shotcut-source\src\lsc\tests\test_highlight_engine.cpp' 'D:\Project\直播切片\shotcut-source\src\lsc\tests\test_round_boundary_detector.cpp' 'D:\Project\直播切片\shotcut-source\src\lsc\tests\test_commentary_segmenter.cpp' 'D:\Project\直播切片\shotcut-source\src\lsc\CMakeLists.txt'
git commit -m "feat: add valorant round and commentary segmentation"
```

### Task 7: 完成界面收口、导出联动和整体验证

**Files:**
- Modify: `shotcut-source/src/lsc/docks/LivestreamDock.cpp`
- Modify: `shotcut-source/src/lsc/docks/AnalysisDock.cpp`
- Modify: `shotcut-source/src/lsc/docks/HighlightPreviewWidget.cpp`
- Modify: `shotcut-source/src/lsc/app/MainWindow.cpp`
- Modify: `shotcut-source/src/lsc/analyzer/ClipExporter.cpp`
- Modify: `shotcut-source/src/lsc/CMakeLists.txt`
- Modify: `shotcut-source/src/lsc/tests/test_recording_session.cpp`
- Modify: `shotcut-source/src/lsc/tests/test_highlight_engine.cpp`
- Modify: `shotcut-source/src/lsc/tests/test_video_analyzer.cpp`
- Modify: `shotcut-source/src/lsc/tests/test_live_preview.cpp`
- Modify: `shotcut-source/src/lsc/tests/test_analysis_dock.cpp`

- [ ] **Step 1: 先补最终验收测试断言**

```cpp
runTest("preview stops after recording stops", sawPreviewStop);
runTest("realtime highlight appears in dock", sawRealtimeCard);
runTest("valorant profile enables round segmentation", engine.analysisProfile().enableRoundSegmentation);
runTest("dance profile enables dance segmentation", engine.analysisProfile().enableDanceSegmentation);
```

- [ ] **Step 2: 运行相关测试确认还有哪些最终收口缺失**

```powershell
cmake --build 'D:\Project\直播切片\shotcut-source\src\lsc\build' --config Release --target test_recording_session test_highlight_engine test_video_analyzer test_live_preview test_analysis_dock lsc_app
ctest --test-dir 'D:\Project\直播切片\shotcut-source\src\lsc\build' -C Release --output-on-failure -R "test_recording_session|test_highlight_engine|test_video_analyzer|test_live_preview|test_analysis_dock"
```

Expected: 如仍有失败，集中在 UI 状态、导出状态或 profile 联动上。

- [ ] **Step 3: 收口 UI 状态、导出标签和片段来源展示**

```cpp
m_statusLabel->setText(QString::fromUtf8("录制中预览已开启，AI 正在增量分析"));
m_exportStatusLabel->setText(QString::fromUtf8("已导出 %1 个片段").arg(exportedCount));
item->setText(QStringLiteral("[%1][%2 - %3] %4% %5")
                  .arg(card.sourceTag)
                  .arg(formatSegmentTime(card.startSec))
                  .arg(formatSegmentTime(card.endSec))
                  .arg(static_cast<int>(card.score * 100.0))
                  .arg(card.reason));
```

- [ ] **Step 4: 运行完整构建和全量测试**

```powershell
cmake -S 'D:\Project\直播切片\shotcut-source\src\lsc' -B 'D:\Project\直播切片\shotcut-source\src\lsc\build'
cmake --build 'D:\Project\直播切片\shotcut-source\src\lsc\build' --config Release --target lsc_app
ctest --test-dir 'D:\Project\直播切片\shotcut-source\src\lsc\build' -C Release --output-on-failure
```

Expected: `lsc_app` 构建成功，`ctest` 全量通过。

- [ ] **Step 5: 手动验证 `lsc_app.exe`**

```powershell
Start-Process -FilePath 'D:\Project\直播切片\shotcut-source\src\lsc\build\Release\lsc_app.exe' -WindowStyle Hidden
```

Expected:
- 点击“开始录制”后预览区域开始播放。
- 点击“停止录制”后预览立即停止，状态恢复空闲。
- 舞蹈直播 profile 下能看到舞蹈片段候选。
- 无畏契约 profile 下能看到“每局片段”和“解说片段”。

- [ ] **Step 6: 提交最终收口**

```powershell
git add 'D:\Project\直播切片\shotcut-source\src\lsc\docks\LivestreamDock.cpp' 'D:\Project\直播切片\shotcut-source\src\lsc\docks\AnalysisDock.cpp' 'D:\Project\直播切片\shotcut-source\src\lsc\docks\HighlightPreviewWidget.cpp' 'D:\Project\直播切片\shotcut-source\src\lsc\app\MainWindow.cpp' 'D:\Project\直播切片\shotcut-source\src\lsc\analyzer\ClipExporter.cpp' 'D:\Project\直播切片\shotcut-source\src\lsc\CMakeLists.txt' 'D:\Project\直播切片\shotcut-source\src\lsc\tests\test_recording_session.cpp' 'D:\Project\直播切片\shotcut-source\src\lsc\tests\test_highlight_engine.cpp' 'D:\Project\直播切片\shotcut-source\src\lsc\tests\test_video_analyzer.cpp' 'D:\Project\直播切片\shotcut-source\src\lsc\tests\test_live_preview.cpp' 'D:\Project\直播切片\shotcut-source\src\lsc\tests\test_analysis_dock.cpp'
git commit -m "feat: finish preview and ai clipping workflow for lsc app"
```

---

## Plan Self-Review

- Spec coverage: 已覆盖录制预览、实时高光流入、舞蹈直播切片、无畏契约按局切片、解说语义切片、导出与验证。
- Placeholder scan: 文档未使用未落地的占位描述。
- Type consistency: `AnalysisProfile`、`PreviewController`、`LivePreviewWidget`、`RoundBoundaryDetector`、`CommentarySegmenter` 在所有任务中命名保持一致。
- Scope check: 该计划仍围绕 `lsc_app.exe` 一条主线，虽然功能较大，但每个任务都可独立提交并验证。
