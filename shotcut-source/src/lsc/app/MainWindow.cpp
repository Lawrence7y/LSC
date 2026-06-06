#include "MainWindow.h"

#include <QApplication>
#include <QDir>
#include <QDockWidget>
#include <QFileInfo>
#include <QMenuBar>
#include <QMessageBox>
#include <QStatusBar>

#include "analyzer/HighlightEngine.h"
#include "core/TaskCenter.h"
#include "core/WorkflowOrchestrator.h"
#include "core/LscDatabase.h"

MainWindow::MainWindow(QWidget* parent)
    : QMainWindow(parent)
    , m_livestreamDock(new LivestreamDock(this))
    , m_analysisDock(new AnalysisDock(this))
    , m_playerDock(new PlayerDock(this))
    , m_taskDock(new lsc::TaskDock(this))
    , m_historyDock(new lsc::HistoryDock(this))
    , m_feedbackStatsDock(new FeedbackStatsDock(this))
    , m_diagnosticsDock(new lsc::DiagnosticsDock(this))
    , m_settingsDock(new lsc::SettingsDock(this))
{
    setWindowTitle("LSC - Live Stream Clipper");
    resize(1400, 900);

    HighlightEngine* engine = new HighlightEngine(this);
    engine->setAutoExport(true, QDir::homePath() + "/Videos/LiveClips");
    engine->setAnalysisProfile(AnalysisProfile::generic());

    m_livestreamDock->session()->setHighlightEngine(engine);
    m_analysisDock->setHighlightEngine(engine);
    connect(m_settingsDock, &lsc::SettingsDock::settingsChanged,
            m_livestreamDock, &LivestreamDock::applyRuntimeConfig);

    auto completeAnalysisTask = [this]() {
        if (!m_analysisTaskId.isEmpty()) {
            lsc::TaskCenter::instance().completeTask(m_analysisTaskId);
            m_analysisTaskId.clear();
        }
    };
    auto failAnalysisTask = [this](const QString& error) {
        if (!m_analysisTaskId.isEmpty()) {
            lsc::TaskCenter::instance().failTask(m_analysisTaskId, error, true);
            m_analysisTaskId.clear();
        }
    };

    connect(engine, &HighlightEngine::finished, this, completeAnalysisTask);
    connect(engine, &HighlightEngine::errorOccurred, this, failAnalysisTask);

    const auto dockFeatures = QDockWidget::DockWidgetMovable
        | QDockWidget::DockWidgetFloatable
        | QDockWidget::DockWidgetClosable;
    m_livestreamDock->setFeatures(dockFeatures);
    m_analysisDock->setFeatures(dockFeatures);
    m_playerDock->setFeatures(dockFeatures);
    m_taskDock->setFeatures(dockFeatures);
    m_historyDock->setFeatures(dockFeatures);
    m_feedbackStatsDock->setFeatures(dockFeatures);
    m_diagnosticsDock->setFeatures(dockFeatures);
    m_settingsDock->setFeatures(dockFeatures);

    // 设置初始布局 - 先添加底部播放器，再添加左右面板
    // 播放器面板：用户可自由调整高度，只设置合理范围
    m_playerDock->setMinimumHeight(150);
    m_playerDock->setMaximumHeight(800);
    addDockWidget(Qt::BottomDockWidgetArea, m_playerDock);

    m_livestreamDock->setMinimumWidth(350);
    addDockWidget(Qt::LeftDockWidgetArea, m_livestreamDock);

    m_analysisDock->setMinimumWidth(400);
    addDockWidget(Qt::RightDockWidgetArea, m_analysisDock);

    m_taskDock->setMinimumWidth(350);
    addDockWidget(Qt::RightDockWidgetArea, m_taskDock);

    m_historyDock->setMinimumWidth(350);
    addDockWidget(Qt::LeftDockWidgetArea, m_historyDock);

    m_feedbackStatsDock->setMinimumWidth(300);
    addDockWidget(Qt::RightDockWidgetArea, m_feedbackStatsDock);

    m_diagnosticsDock->setMinimumWidth(350);
    addDockWidget(Qt::BottomDockWidgetArea, m_diagnosticsDock);

    m_settingsDock->setMinimumWidth(350);
    addDockWidget(Qt::RightDockWidgetArea, m_settingsDock);

    // 设置左右面板的大小比例
    resizeDocks({m_livestreamDock, m_analysisDock}, {450, 550}, Qt::Horizontal);
    resizeDocks({m_playerDock}, {280}, Qt::Vertical);

    connect(m_livestreamDock, &LivestreamDock::recordingFinished,
            m_analysisDock, &AnalysisDock::onRecordingComplete);
    connect(m_livestreamDock->session(), &RecordingSession::previewSourceChanged,
            m_playerDock, &PlayerDock::playLivePreview);
    connect(m_livestreamDock->session(), &RecordingSession::previewStopped,
            m_playerDock, &PlayerDock::clearPlayer);

    // 录制开始/停止时更新 PlayerDock 的录制路径（用于"返回直播"功能）
    connect(m_livestreamDock->session(), &RecordingSession::recordingStarted,
            m_playerDock, &PlayerDock::setRecordingPath);
    connect(m_livestreamDock->session(), &RecordingSession::recordingStopped,
            m_playerDock, [this](const QString&, qint64) {
                m_playerDock->clearRecordingPath();
            });

    connect(m_livestreamDock, &LivestreamDock::highlightFound,
            [this](const HighlightSegment& seg) {
                statusBar()->showMessage(
                    QString("高光检测: %1s - %2s (评分: %3%)")
                        .arg(static_cast<int>(seg.startSec))
                        .arg(static_cast<int>(seg.endSec))
                        .arg(static_cast<int>(seg.score * 100)),
                    5000);

                const QString videoPath = m_livestreamDock->session()->outputPath();
                m_analysisDock->ingestRealtimeSegment(seg, videoPath);
            });

    connect(m_analysisDock, &AnalysisDock::highlightSelected,
            [this](double startSec, double endSec) {
                const QString videoPath = m_analysisDock->videoPath();
                if (!videoPath.isEmpty()) {
                    m_playerDock->playSegment(videoPath, startSec, endSec);
                }
            });
    connect(m_playerDock, &PlayerDock::exportRequested,
            m_analysisDock, &AnalysisDock::requestPreviewExport);

    connect(m_historyDock, &lsc::HistoryDock::projectDoubleClicked,
            [this](const QString& projectId) {
                auto& db = lsc::LscDatabase::instance();
                auto project = db.project(projectId);
                if (!project.videoPath.isEmpty()) {
                    m_analysisDock->setVideoPath(project.videoPath);
                    statusBar()->showMessage(
                        tr("已加载项目: %1").arg(project.name), 5000);
                }
            });
    connect(m_historyDock, &lsc::HistoryDock::requestDeleteProject,
            [this](const QString& projectId) {
                auto& db = lsc::LscDatabase::instance();
                db.deleteProject(projectId);
                statusBar()->showMessage(tr("项目已删除"), 3000);
            });

    connect(m_livestreamDock, &LivestreamDock::clipExported,
            [this](const QString& path, const QString&) {
                statusBar()->showMessage(
                    QString("片段已导出: %1").arg(QFileInfo(path).fileName()), 5000);
            });

    // WorkflowOrchestrator integration
    auto& orchestrator = lsc::WorkflowOrchestrator::instance();

    connect(&orchestrator, &lsc::WorkflowOrchestrator::stateChanged,
            this, &MainWindow::onWorkflowStateChanged);
    connect(&orchestrator, &lsc::WorkflowOrchestrator::workflowError,
            this, &MainWindow::onWorkflowError);
    connect(&orchestrator, &lsc::WorkflowOrchestrator::workflowCompleted,
            this, &MainWindow::onWorkflowCompleted);

    connect(m_livestreamDock->session(), &RecordingSession::recordingStarted,
            &orchestrator, &lsc::WorkflowOrchestrator::onRecordingStarted);
    connect(m_livestreamDock->session(), &RecordingSession::recordingStarted,
            this, [this](const QString& path) {
                QVariantMap metadata;
                metadata.insert(QStringLiteral("path"), path);
                m_recordingTaskId = lsc::TaskCenter::instance().createTask(
                    lsc::TaskType::Recording,
                    QString::fromUtf8("直播录制"),
                    QFileInfo(path).fileName(),
                    metadata);
                lsc::TaskCenter::instance().startTask(m_recordingTaskId);
            });
    connect(m_livestreamDock->session(), &RecordingSession::recordingStopped,
            &orchestrator, [this, &orchestrator](const QString& path, qint64) {
                orchestrator.onRecordingStopped(path);
            });
    connect(m_livestreamDock->session(), &RecordingSession::recordingStopped,
            this, [this](const QString& path, qint64) {
                if (!m_recordingTaskId.isEmpty()) {
                    lsc::TaskCenter::instance().completeTask(m_recordingTaskId);
                    m_recordingTaskId.clear();
                }
                QVariantMap metadata;
                metadata.insert(QStringLiteral("path"), path);
                m_analysisTaskId = lsc::TaskCenter::instance().createTask(
                    lsc::TaskType::Analysis,
                    QString::fromUtf8("高光分析"),
                    QFileInfo(path).fileName(),
                    metadata);
                lsc::TaskCenter::instance().startTask(m_analysisTaskId);
            });

    connect(m_analysisDock, &AnalysisDock::analysisCompleted,
            &orchestrator, &lsc::WorkflowOrchestrator::onAnalysisCompleted);
    connect(m_analysisDock, &AnalysisDock::analysisCompleted,
            this, [this]() {
                if (!m_analysisTaskId.isEmpty()) {
                    lsc::TaskCenter::instance().completeTask(m_analysisTaskId);
                    m_analysisTaskId.clear();
                }
            });
    connect(m_livestreamDock->session(), &RecordingSession::errorOccurred,
            this, [this](const QString& error) {
                if (!m_analysisTaskId.isEmpty()) {
                    lsc::TaskCenter::instance().failTask(m_analysisTaskId, error, true);
                    m_analysisTaskId.clear();
                }
                if (!m_recordingTaskId.isEmpty()) {
                    lsc::TaskCenter::instance().failTask(m_recordingTaskId, error, true);
                    m_recordingTaskId.clear();
                }
            });

    connect(&orchestrator, &lsc::WorkflowOrchestrator::requestUiUpdate,
            [this](const QString& action, const QVariantMap&) {
                if (action == "show_highlights") {
                    m_analysisDock->show();
                    raise();
                }
            });

    statusBar()->showMessage("就绪 - 模块化录制、分析与预览");

    QMenu* fileMenu = menuBar()->addMenu(QString::fromUtf8("文件(&F)"));
    fileMenu->addAction(QString::fromUtf8("退出(&Q)"), qApp, &QApplication::quit,
                        QKeySequence::Quit);

    QMenu* moduleMenu = menuBar()->addMenu(QString::fromUtf8("模块(&M)"));
    moduleMenu->addAction(m_livestreamDock->toggleViewAction());
    moduleMenu->addAction(m_analysisDock->toggleViewAction());
    moduleMenu->addAction(m_playerDock->toggleViewAction());
    moduleMenu->addAction(m_taskDock->toggleViewAction());
    moduleMenu->addAction(m_historyDock->toggleViewAction());
    moduleMenu->addAction(m_feedbackStatsDock->toggleViewAction());
    moduleMenu->addAction(m_diagnosticsDock->toggleViewAction());
    moduleMenu->addAction(m_settingsDock->toggleViewAction());
    moduleMenu->addSeparator();
    moduleMenu->addAction(QString::fromUtf8("重置模块布局"), [this]() {
        m_playerDock->setMinimumHeight(150);
        m_playerDock->setMaximumHeight(800);
        addDockWidget(Qt::LeftDockWidgetArea, m_livestreamDock);
        addDockWidget(Qt::LeftDockWidgetArea, m_historyDock);
        addDockWidget(Qt::RightDockWidgetArea, m_analysisDock);
        addDockWidget(Qt::RightDockWidgetArea, m_taskDock);
        addDockWidget(Qt::RightDockWidgetArea, m_feedbackStatsDock);
        addDockWidget(Qt::BottomDockWidgetArea, m_diagnosticsDock);
        addDockWidget(Qt::RightDockWidgetArea, m_settingsDock);
        addDockWidget(Qt::BottomDockWidgetArea, m_playerDock);
        m_livestreamDock->show();
        m_historyDock->show();
        m_analysisDock->show();
        m_taskDock->show();
        m_feedbackStatsDock->show();
        m_diagnosticsDock->show();
        m_settingsDock->show();
        m_playerDock->show();
        resizeDocks({m_livestreamDock, m_analysisDock}, {450, 550}, Qt::Horizontal);
        resizeDocks({m_playerDock}, {280}, Qt::Vertical);
    });

    QMenu* helpMenu = menuBar()->addMenu(QString::fromUtf8("帮助(&H)"));
    helpMenu->addAction(QString::fromUtf8("关于(&A)"), [this]() {
        QMessageBox::about(this, QString::fromUtf8("关于 LSC"),
                           QString::fromUtf8("直播切片大师 v2.1\n\n"
                                             "模块化直播录制 + AI 分析 + 独立播放器预览"));
    });
}

MainWindow::~MainWindow() {}

void MainWindow::onWorkflowStateChanged(lsc::WorkflowState newState, lsc::WorkflowState /*oldState*/)
{
    statusBar()->showMessage(
        QString("工作流: %1").arg(lsc::WorkflowOrchestrator::instance().currentStateName()));
}

void MainWindow::onWorkflowError(const QString& error)
{
    statusBar()->showMessage(QString("工作流错误: %1").arg(error), 10000);
}

void MainWindow::onWorkflowCompleted()
{
    statusBar()->showMessage("工作流完成", 5000);
}
