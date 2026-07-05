# 直播切片工具产品化闭环实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将直播切片工具从"功能模块堆砌"升级为"连续、稳定、可运营的产品"，打通 10 个关键闭环。

**Architecture:** 基于现有 LSC 模块（C++17/Qt6/FFmpeg/Whisper），新增任务中心、历史管理、诊断系统等核心组件，重构 UI 形成统一主流程。

**Tech Stack:** C++17, Qt6 (Widgets/Core/Sql), FFmpeg, whisper-cli, JSON, SQLite

---

## 优先级与依赖关系

```
Phase 1: 主流程闭环 (P0) ─────────────────────────────────┐
  ├─ Task 1: 统一任务中心 (TaskCenter)                      │
  ├─ Task 2: 主流程状态机 (WorkflowOrchestrator)            │
  └─ Task 3: 错误恢复与用户通知                             │
                                                           │
Phase 2: 数据持久化 (P0) ──────────────────────────────────┤
  ├─ Task 4: SQLite 数据库 (LscDatabase)                    │
  ├─ Task 5: 历史项目管理                                   │
  └─ Task 6: 反馈闭环统计                                   │
                                                           │
Phase 3: 结果修正与导出 (P1) ──────────────────────────────┤
  ├─ Task 7: 片段编辑器 (ClipEditor)                        │
  ├─ Task 8: 导出产品化                                     │
  └─ Task 9: 批量导出队列                                   │
                                                           │
Phase 4: 配置与诊断 (P1) ──────────────────────────────────┤
  ├─ Task 10: 设置面板 (SettingsPanel)                      │
  └─ Task 11: 诊断面板 (DiagnosticsPanel)                   │
                                                           │
Phase 5: 多平台抽象 (P2) ──────────────────────────────────┘
  └─ Task 12: 平台解析器重构
```

---

## Phase 1: 主流程闭环

### Task 1: 统一任务中心 (TaskCenter)

**目标:** 创建统一的任务管理中心，显示所有耗时任务的状态。

**Files:**
- Create: `src/lsc/core/TaskCenter.h`
- Create: `src/lsc/core/TaskCenter.cpp`
- Create: `src/lsc/docks/TaskDock.h`
- Create: `src/lsc/docks/TaskDock.cpp`
- Modify: `src/lsc/CMakeLists.txt`
- Modify: `src/lsc/app/MainWindow.h`
- Modify: `src/lsc/app/MainWindow.cpp`

#### Step 1: 定义任务数据结构

```cpp
// src/lsc/core/TaskCenter.h
#ifndef TASKCENTER_H
#define TASKCENTER_H

#include <QObject>
#include <QString>
#include <QVariantMap>
#include <QUuid>
#include <QDateTime>
#include <QVector>

namespace lsc {

enum class TaskType {
    Recording,      // 直播录制
    Analysis,       // 视频分析
    ASR,            // 语音识别
    Export,         // 片段导出
    Import          // 文件导入
};

enum class TaskState {
    Queued,         // 排队中
    Running,        // 运行中
    Paused,         // 已暂停
    Completed,      // 已完成
    Failed,         // 失败
    Cancelling,     // 取消中
    Cancelled       // 已取消
};

struct TaskInfo {
    QString id;             // 唯一标识
    TaskType type;          // 任务类型
    TaskState state;        // 当前状态
    QString title;          // 显示标题
    QString description;    // 详细描述
    int progress = 0;       // 进度 0-100
    QString statusText;     // 状态文本
    QString errorText;      // 错误信息
    QDateTime createdAt;    // 创建时间
    QDateTime startedAt;    // 开始时间
    QDateTime finishedAt;   // 完成时间
    QVariantMap metadata;   // 附加数据
    bool retryable = false; // 是否可重试
    int retryCount = 0;     // 已重试次数
    int maxRetries = 3;     // 最大重试次数
};

class TaskCenter : public QObject {
    Q_OBJECT

public:
    static TaskCenter& instance();

    // 任务生命周期
    QString createTask(TaskType type, const QString& title, 
                       const QString& description = {},
                       const QVariantMap& metadata = {});
    void startTask(const QString& taskId);
    void updateProgress(const QString& taskId, int progress, 
                        const QString& statusText = {});
    void completeTask(const QString& taskId);
    void failTask(const QString& taskId, const QString& error, 
                  bool retryable = false);
    void cancelTask(const QString& taskId);
    void pauseTask(const QString& taskId);
    void resumeTask(const QString& taskId);
    void retryTask(const QString& taskId);

    // 查询
    TaskInfo taskInfo(const QString& taskId) const;
    QVector<TaskInfo> allTasks() const;
    QVector<TaskInfo> tasksByType(TaskType type) const;
    QVector<TaskInfo> tasksByState(TaskState state) const;
    int activeTaskCount() const;
    bool hasActiveTasks() const;

    // 清理
    void clearCompleted();
    void clearAll();

signals:
    void taskCreated(const QString& taskId);
    void taskStarted(const QString& taskId);
    void taskProgressChanged(const QString& taskId, int progress);
    void taskCompleted(const QString& taskId);
    void taskFailed(const QString& taskId, const QString& error);
    void taskCancelled(const QString& taskId);
    void taskStateChanged(const QString& taskId, TaskState state);
    void activeTaskCountChanged(int count);

private:
    TaskCenter() = default;
    ~TaskCenter() = default;
    TaskCenter(const TaskCenter&) = delete;
    TaskCenter& operator=(const TaskCenter&) = delete;

    QMap<QString, TaskInfo> m_tasks;
};

} // namespace lsc

#endif // TASKCENTER_H
```

#### Step 2: 实现 TaskCenter

```cpp
// src/lsc/core/TaskCenter.cpp
#include "TaskCenter.h"
#include "LscLog.h"

namespace lsc {

TaskCenter& TaskCenter::instance() {
    static TaskCenter s_instance;
    return s_instance;
}

QString TaskCenter::createTask(TaskType type, const QString& title,
                               const QString& description,
                               const QVariantMap& metadata) {
    TaskInfo info;
    info.id = QUuid::createUuid().toString(QUuid::WithoutBraces);
    info.type = type;
    info.state = TaskState::Queued;
    info.title = title;
    info.description = description;
    info.createdAt = QDateTime::currentDateTime();
    info.metadata = metadata;

    m_tasks[info.id] = info;
    emit taskCreated(info.id);
    
    LSC_LOG_INFO(QString("Task created: %1 - %2").arg(info.id, title));
    return info.id;
}

void TaskCenter::startTask(const QString& taskId) {
    auto it = m_tasks.find(taskId);
    if (it == m_tasks.end()) return;

    it->state = TaskState::Running;
    it->startedAt = QDateTime::currentDateTime();
    emit taskStarted(taskId);
    emit taskStateChanged(taskId, it->state);
    emit activeTaskCountChanged(activeTaskCount());
}

void TaskCenter::updateProgress(const QString& taskId, int progress,
                                const QString& statusText) {
    auto it = m_tasks.find(taskId);
    if (it == m_tasks.end()) return;

    it->progress = qBound(0, progress, 100);
    if (!statusText.isEmpty()) {
        it->statusText = statusText;
    }
    emit taskProgressChanged(taskId, it->progress);
}

void TaskCenter::completeTask(const QString& taskId) {
    auto it = m_tasks.find(taskId);
    if (it == m_tasks.end()) return;

    it->state = TaskState::Completed;
    it->progress = 100;
    it->finishedAt = QDateTime::currentDateTime();
    emit taskCompleted(taskId);
    emit taskStateChanged(taskId, it->state);
    emit activeTaskCountChanged(activeTaskCount());
    
    LSC_LOG_INFO(QString("Task completed: %1").arg(taskId));
}

void TaskCenter::failTask(const QString& taskId, const QString& error,
                          bool retryable) {
    auto it = m_tasks.find(taskId);
    if (it == m_tasks.end()) return;

    it->state = TaskState::Failed;
    it->errorText = error;
    it->retryable = retryable;
    it->finishedAt = QDateTime::currentDateTime();
    emit taskFailed(taskId, error);
    emit taskStateChanged(taskId, it->state);
    emit activeTaskCountChanged(activeTaskCount());
    
    LSC_LOG_ERROR(QString("Task failed: %1 - %2").arg(taskId, error));
}

void TaskCenter::cancelTask(const QString& taskId) {
    auto it = m_tasks.find(taskId);
    if (it == m_tasks.end()) return;

    it->state = TaskState::Cancelled;
    it->finishedAt = QDateTime::currentDateTime();
    emit taskCancelled(taskId);
    emit taskStateChanged(taskId, it->state);
    emit activeTaskCountChanged(activeTaskCount());
}

void TaskCenter::pauseTask(const QString& taskId) {
    auto it = m_tasks.find(taskId);
    if (it == m_tasks.end()) return;

    if (it->state == TaskState::Running) {
        it->state = TaskState::Paused;
        emit taskStateChanged(taskId, it->state);
    }
}

void TaskCenter::resumeTask(const QString& taskId) {
    auto it = m_tasks.find(taskId);
    if (it == m_tasks.end()) return;

    if (it->state == TaskState::Paused) {
        it->state = TaskState::Running;
        emit taskStateChanged(taskId, it->state);
    }
}

void TaskCenter::retryTask(const QString& taskId) {
    auto it = m_tasks.find(taskId);
    if (it == m_tasks.end()) return;

    if (it->state == TaskState::Failed && it->retryable) {
        if (it->retryCount < it->maxRetries) {
            it->retryCount++;
            it->state = TaskState::Queued;
            it->errorText.clear();
            emit taskStateChanged(taskId, it->state);
            emit activeTaskCountChanged(activeTaskCount());
        }
    }
}

TaskInfo TaskCenter::taskInfo(const QString& taskId) const {
    return m_tasks.value(taskId);
}

QVector<TaskInfo> TaskCenter::allTasks() const {
    return m_tasks.values().toVector();
}

QVector<TaskInfo> TaskCenter::tasksByType(TaskType type) const {
    QVector<TaskInfo> result;
    for (const auto& task : m_tasks) {
        if (task.type == type) {
            result.append(task);
        }
    }
    return result;
}

QVector<TaskInfo> TaskCenter::tasksByState(TaskState state) const {
    QVector<TaskInfo> result;
    for (const auto& task : m_tasks) {
        if (task.state == state) {
            result.append(task);
        }
    }
    return result;
}

int TaskCenter::activeTaskCount() const {
    int count = 0;
    for (const auto& task : m_tasks) {
        if (task.state == TaskState::Running || 
            task.state == TaskState::Queued) {
            count++;
        }
    }
    return count;
}

bool TaskCenter::hasActiveTasks() const {
    return activeTaskCount() > 0;
}

void TaskCenter::clearCompleted() {
    auto it = m_tasks.begin();
    while (it != m_tasks.end()) {
        if (it->state == TaskState::Completed ||
            it->state == TaskState::Cancelled) {
            it = m_tasks.erase(it);
        } else {
            ++it;
        }
    }
}

void TaskCenter::clearAll() {
    m_tasks.clear();
}

} // namespace lsc
```

#### Step 3: 创建 TaskDock UI

```cpp
// src/lsc/docks/TaskDock.h
#ifndef TASKDOCK_H
#define TASKDOCK_H

#include <QDockWidget>
#include <QTreeWidget>
#include <QPushButton>
#include <QLabel>
#include <QTimer>

namespace lsc {

class TaskCenter;
struct TaskInfo;

class TaskDock : public QDockWidget {
    Q_OBJECT

public:
    explicit TaskDock(QWidget* parent = nullptr);

private slots:
    void onTaskCreated(const QString& taskId);
    void onTaskProgressChanged(const QString& taskId, int progress);
    void onTaskCompleted(const QString& taskId);
    void onTaskFailed(const QString& taskId, const QString& error);
    void onTaskStateChanged(const QString& taskId, int state);
    void onRefreshTimer();
    void onCancelClicked();
    void onRetryClicked();
    void onClearCompletedClicked();

private:
    void setupUi();
    void refreshTaskList();
    QTreeWidgetItem* findOrCreateItem(const QString& taskId);
    void updateItem(QTreeWidgetItem* item, const TaskInfo& info);
    QString stateToString(int state) const;
    QString typeToString(int type) const;
    QIcon stateIcon(int state) const;

    QTreeWidget* m_treeWidget;
    QPushButton* m_cancelBtn;
    QPushButton* m_retryBtn;
    QPushButton* m_clearBtn;
    QLabel* m_statusLabel;
    QTimer* m_refreshTimer;
    TaskCenter& m_taskCenter;
};

} // namespace lsc

#endif // TASKDOCK_H
```

#### Step 4: 实现 TaskDock

```cpp
// src/lsc/docks/TaskDock.cpp
#include "TaskDock.h"
#include "core/TaskCenter.h"
#include <QVBoxLayout>
#include <QHBoxLayout>
#include <QHeaderView>

namespace lsc {

TaskDock::TaskDock(QWidget* parent)
    : QDockWidget(tr("任务中心"), parent)
    , m_taskCenter(TaskCenter::instance())
{
    setupUi();

    connect(&m_taskCenter, &TaskCenter::taskCreated,
            this, &TaskDock::onTaskCreated);
    connect(&m_taskCenter, &TaskCenter::taskProgressChanged,
            this, &TaskDock::onTaskProgressChanged);
    connect(&m_taskCenter, &TaskCenter::taskCompleted,
            this, &TaskDock::onTaskCompleted);
    connect(&m_taskCenter, &TaskCenter::taskFailed,
            this, &TaskDock::onTaskFailed);
    connect(&m_taskCenter, &TaskCenter::taskStateChanged,
            this, &TaskDock::onTaskStateChanged);

    m_refreshTimer = new QTimer(this);
    m_refreshTimer->setInterval(1000);
    connect(m_refreshTimer, &QTimer::timeout,
            this, &TaskDock::onRefreshTimer);
    m_refreshTimer->start();
}

void TaskDock::setupUi() {
    auto* widget = new QWidget(this);
    auto* layout = new QVBoxLayout(widget);

    // 工具栏
    auto* toolbar = new QHBoxLayout();
    m_cancelBtn = new QPushButton(tr("取消"), this);
    m_retryBtn = new QPushButton(tr("重试"), this);
    m_clearBtn = new QPushButton(tr("清除已完成"), this);
    
    connect(m_cancelBtn, &QPushButton::clicked,
            this, &TaskDock::onCancelClicked);
    connect(m_retryBtn, &QPushButton::clicked,
            this, &TaskDock::onRetryClicked);
    connect(m_clearBtn, &QPushButton::clicked,
            this, &TaskDock::onClearCompletedClicked);

    toolbar->addWidget(m_cancelBtn);
    toolbar->addWidget(m_retryBtn);
    toolbar->addStretch();
    toolbar->addWidget(m_clearBtn);
    layout->addLayout(toolbar);

    // 任务列表
    m_treeWidget = new QTreeWidget(this);
    m_treeWidget->setHeaderLabels({
        tr("状态"), tr("类型"), tr("标题"), 
        tr("进度"), tr("状态信息")
    });
    m_treeWidget->header()->setStretchLastSection(true);
    m_treeWidget->setRootIsDecorated(false);
    m_treeWidget->setSelectionMode(QAbstractItemView::SingleSelection);
    layout->addWidget(m_treeWidget);

    // 状态栏
    m_statusLabel = new QLabel(this);
    layout->addWidget(m_statusLabel);

    setWidget(widget);
}

void TaskDock::onTaskCreated(const QString& taskId) {
    auto info = m_taskCenter.taskInfo(taskId);
    auto* item = findOrCreateItem(taskId);
    updateItem(item, info);
}

void TaskDock::onTaskProgressChanged(const QString& taskId, int progress) {
    auto* item = findOrCreateItem(taskId);
    if (item) {
        item->setText(3, QString("%1%").arg(progress));
    }
}

void TaskDock::onTaskCompleted(const QString& taskId) {
    auto* item = findOrCreateItem(taskId);
    if (item) {
        auto info = m_taskCenter.taskInfo(taskId);
        updateItem(item, info);
    }
}

void TaskDock::onTaskFailed(const QString& taskId, const QString& error) {
    auto* item = findOrCreateItem(taskId);
    if (item) {
        auto info = m_taskCenter.taskInfo(taskId);
        updateItem(item, info);
    }
}

void TaskDock::onTaskStateChanged(const QString& taskId, int state) {
    Q_UNUSED(state);
    auto* item = findOrCreateItem(taskId);
    if (item) {
        auto info = m_taskCenter.taskInfo(taskId);
        updateItem(item, info);
    }
}

void TaskDock::onRefreshTimer() {
    m_statusLabel->setText(
        tr("活跃任务: %1").arg(m_taskCenter.activeTaskCount()));
}

void TaskDock::onCancelClicked() {
    auto* item = m_treeWidget->currentItem();
    if (item) {
        m_taskCenter.cancelTask(item->data(0, Qt::UserRole).toString());
    }
}

void TaskDock::onRetryClicked() {
    auto* item = m_treeWidget->currentItem();
    if (item) {
        m_taskCenter.retryTask(item->data(0, Qt::UserRole).toString());
    }
}

void TaskDock::onClearCompletedClicked() {
    m_taskCenter.clearCompleted();
    refreshTaskList();
}

void TaskDock::refreshTaskList() {
    m_treeWidget->clear();
    const auto tasks = m_taskCenter.allTasks();
    for (const auto& task : tasks) {
        auto* item = new QTreeWidgetItem(m_treeWidget);
        updateItem(item, task);
    }
}

QTreeWidgetItem* TaskDock::findOrCreateItem(const QString& taskId) {
    for (int i = 0; i < m_treeWidget->topLevelItemCount(); ++i) {
        auto* item = m_treeWidget->topLevelItem(i);
        if (item->data(0, Qt::UserRole).toString() == taskId) {
            return item;
        }
    }
    auto* item = new QTreeWidgetItem(m_treeWidget);
    item->setData(0, Qt::UserRole, taskId);
    return item;
}

void TaskDock::updateItem(QTreeWidgetItem* item, const TaskInfo& info) {
    item->setIcon(0, stateIcon(static_cast<int>(info.state)));
    item->setText(0, stateToString(static_cast<int>(info.state)));
    item->setText(1, typeToString(static_cast<int>(info.type)));
    item->setText(2, info.title);
    item->setText(3, QString("%1%").arg(info.progress));
    item->setText(4, info.state == TaskState::Failed ? info.errorText : info.statusText);
}

QString TaskDock::stateToString(int state) const {
    switch (static_cast<TaskState>(state)) {
        case TaskState::Queued: return tr("排队中");
        case TaskState::Running: return tr("运行中");
        case TaskState::Paused: return tr("已暂停");
        case TaskState::Completed: return tr("已完成");
        case TaskState::Failed: return tr("失败");
        case TaskState::Cancelling: return tr("取消中");
        case TaskState::Cancelled: return tr("已取消");
        default: return tr("未知");
    }
}

QString TaskDock::typeToString(int type) const {
    switch (static_cast<TaskType>(type)) {
        case TaskType::Recording: return tr("录制");
        case TaskType::Analysis: return tr("分析");
        case TaskType::ASR: return tr("语音识别");
        case TaskType::Export: return tr("导出");
        case TaskType::Import: return tr("导入");
        default: return tr("未知");
    }
}

QIcon TaskDock::stateIcon(int state) const {
    // 返回对应状态的图标
    return QIcon();
}

} // namespace lsc
```

#### Step 5: 集成到 MainWindow

```cpp
// src/lsc/app/MainWindow.h 添加
#include "docks/TaskDock.h"

// 成员变量添加
TaskDock* m_taskDock;

// src/lsc/app/MainWindow.cpp 在构造函数中添加
m_taskDock = new TaskDock(this);
addDockWidget(Qt::BottomDockWidgetArea, m_taskDock);
```

#### Step 6: 更新 CMakeLists.txt

```cmake
# src/lsc/CMakeLists.txt 添加
core/TaskCenter.cpp
docks/TaskDock.cpp
```

#### Step 7: 验证编译

```bash
cd D:\Project\直播切片\shotcut-source\build
cmake --build . --target lsc
```

---

### Task 2: 主流程状态机 (WorkflowOrchestrator)

**目标:** 创建主流程编排器，将录制→分析→导出串联为连续流程。

**Files:**
- Create: `src/lsc/core/WorkflowOrchestrator.h`
- Create: `src/lsc/core/WorkflowOrchestrator.cpp`
- Modify: `src/lsc/app/MainWindow.h`
- Modify: `src/lsc/app/MainWindow.cpp`

#### Step 1: 定义工作流状态机

```cpp
// src/lsc/core/WorkflowOrchestrator.h
#ifndef WORKFLOWORCHESTRATOR_H
#define WORKFLOWORCHESTRATOR_H

#include <QObject>
#include <QString>
#include <QVariantMap>

namespace lsc {

enum class WorkflowState {
    Idle,               // 空闲
    ParsingUrl,         // 解析 URL
    Recording,          // 录制中
    StoppingRecording,  // 停止录制
    AutoAnalyzing,      // 自动分析中
    ReviewingHighlights,// 预览高光
    Exporting,          // 导出中
    Completed,          // 完成
    Error               // 错误
};

class WorkflowOrchestrator : public QObject {
    Q_OBJECT

public:
    static WorkflowOrchestrator& instance();

    // 主流程控制
    void startWorkflow(const QString& url);
    void stopRecording();
    void startAnalysis(const QString& videoPath = {});
    void reviewHighlights();
    void exportSelected();
    void cancelWorkflow();
    void reset();

    // 状态查询
    WorkflowState currentState() const;
    QString currentStateName() const;
    QString currentUrl() const;
    QString currentVideoPath() const;
    bool isActive() const;

signals:
    void stateChanged(WorkflowState newState, WorkflowState oldState);
    void workflowStarted(const QString& url);
    void workflowCompleted();
    void workflowError(const QString& error);
    void requestUiUpdate(const QString& action, const QVariantMap& data);

public slots:
    void onRecordingStarted();
    void onRecordingStopped(const QString& outputPath);
    void onAnalysisCompleted();
    void onExportCompleted();

private:
    WorkflowOrchestrator() = default;
    ~WorkflowOrchestrator() = default;

    void setState(WorkflowState newState);
    void handleError(const QString& error);

    WorkflowState m_state = WorkflowState::Idle;
    QString m_currentUrl;
    QString m_currentVideoPath;
    QString m_lastError;
};

} // namespace lsc

#endif // WORKFLOWORCHESTRATOR_H
```

#### Step 2: 实现状态机

```cpp
// src/lsc/core/WorkflowOrchestrator.cpp
#include "WorkflowOrchestrator.h"
#include "TaskCenter.h"
#include "LscLog.h"

namespace lsc {

WorkflowOrchestrator& WorkflowOrchestrator::instance() {
    static WorkflowOrchestrator s_instance;
    return s_instance;
}

void WorkflowOrchestrator::startWorkflow(const QString& url) {
    if (isActive()) {
        LSC_LOG_WARNING("Workflow already active, ignoring start request");
        return;
    }

    m_currentUrl = url;
    setState(WorkflowState::ParsingUrl);
    emit workflowStarted(url);
    
    // TODO: 触发 PlatformParser 解析
    // 解析完成后自动进入 Recording 状态
}

void WorkflowOrchestrator::stopRecording() {
    if (m_state != WorkflowState::Recording) return;
    
    setState(WorkflowState::StoppingRecording);
    // TODO: 触发 RecordingSession::stopRecording
}

void WorkflowOrchestrator::startAnalysis(const QString& videoPath) {
    m_currentVideoPath = videoPath;
    setState(WorkflowState::AutoAnalyzing);
    // TODO: 触发 HighlightEngine::analyze
}

void WorkflowOrchestrator::reviewHighlights() {
    if (m_state != WorkflowState::AutoAnalyzing) return;
    setState(WorkflowState::ReviewingHighlights);
    emit requestUiUpdate("show_highlights", {});
}

void WorkflowOrchestrator::exportSelected() {
    if (m_state != WorkflowState::ReviewingHighlights) return;
    setState(WorkflowState::Exporting);
    // TODO: 触发批量导出
}

void WorkflowOrchestrator::cancelWorkflow() {
    // 取消当前正在进行的操作
    setState(WorkflowState::Idle);
    emit workflowError(tr("用户取消"));
}

void WorkflowOrchestrator::reset() {
    m_currentUrl.clear();
    m_currentVideoPath.clear();
    m_lastError.clear();
    setState(WorkflowState::Idle);
}

WorkflowState WorkflowOrchestrator::currentState() const {
    return m_state;
}

QString WorkflowOrchestrator::currentStateName() const {
    switch (m_state) {
        case WorkflowState::Idle: return tr("空闲");
        case WorkflowState::ParsingUrl: return tr("解析链接");
        case WorkflowState::Recording: return tr("录制中");
        case WorkflowState::StoppingRecording: return tr("停止录制");
        case WorkflowState::AutoAnalyzing: return tr("自动分析");
        case WorkflowState::ReviewingHighlights: return tr("预览高光");
        case WorkflowState::Exporting: return tr("导出中");
        case WorkflowState::Completed: return tr("完成");
        case WorkflowState::Error: return tr("错误");
        default: return tr("未知");
    }
}

QString WorkflowOrchestrator::currentUrl() const {
    return m_currentUrl;
}

QString WorkflowOrchestrator::currentVideoPath() const {
    return m_currentVideoPath;
}

bool WorkflowOrchestrator::isActive() const {
    return m_state != WorkflowState::Idle && 
           m_state != WorkflowState::Completed &&
           m_state != WorkflowState::Error;
}

void WorkflowOrchestrator::onRecordingStarted() {
    setState(WorkflowState::Recording);
}

void WorkflowOrchestrator::onRecordingStopped(const QString& outputPath) {
    m_currentVideoPath = outputPath;
    // 自动进入分析阶段
    startAnalysis(outputPath);
}

void WorkflowOrchestrator::onAnalysisCompleted() {
    reviewHighlights();
}

void WorkflowOrchestrator::onExportCompleted() {
    setState(WorkflowState::Completed);
    emit workflowCompleted();
}

void WorkflowOrchestrator::setState(WorkflowState newState) {
    if (m_state == newState) return;
    
    auto oldState = m_state;
    m_state = newState;
    emit stateChanged(newState, oldState);
    
    LSC_LOG_INFO(QString("Workflow state: %1 -> %2")
        .arg(currentStateName(), 
             WorkflowOrchestrator::instance().currentStateName()));
}

void WorkflowOrchestrator::handleError(const QString& error) {
    m_lastError = error;
    setState(WorkflowState::Error);
    emit workflowError(error);
    LSC_LOG_ERROR(QString("Workflow error: %1").arg(error));
}

} // namespace lsc
```

#### Step 3: 集成到 MainWindow

在 MainWindow 中连接 WorkflowOrchestrator 的信号，实现自动流程。

---

### Task 3: 错误恢复与用户通知

**目标:** 将所有错误转换为用户可理解、可操作的状态。

**Files:**
- Create: `src/lsc/core/ErrorManager.h`
- Create: `src/lsc/core/ErrorManager.cpp`
- Modify: 各模块的错误处理代码

#### Step 1: 定义错误类型和恢复策略

```cpp
// src/lsc/core/ErrorManager.h
#ifndef ERRORMANAGER_H
#define ERRORMANAGER_H

#include <QObject>
#include <QString>
#include <QQueue>

namespace lsc {

enum class ErrorSeverity {
    Info,       // 信息
    Warning,    // 警告
    Error,      // 错误
    Critical    // 严重错误
};

enum class RecoveryAction {
    None,           // 无恢复动作
    Retry,          // 重试
    Reconnect,      // 重连
    Fallback,       // 降级处理
    UserAction      // 需要用户操作
};

struct ErrorInfo {
    QString code;               // 错误码
    ErrorSeverity severity;     // 严重程度
    QString message;            // 用户可见消息
    QString technicalDetail;    // 技术细节
    RecoveryAction recovery;    // 恢复动作
    QString recoveryHint;       // 恢复提示
    QDateTime timestamp;        // 发生时间
    QString source;             // 来源模块
};

class ErrorManager : public QObject {
    Q_OBJECT

public:
    static ErrorManager& instance();

    // 报告错误
    void reportError(const QString& code, const QString& message,
                     ErrorSeverity severity = ErrorSeverity::Error,
                     RecoveryAction recovery = RecoveryAction::None,
                     const QString& technicalDetail = {});

    // 获取最近错误
    QVector<ErrorInfo> recentErrors(int count = 10) const;
    ErrorInfo lastError() const;
    bool hasErrors() const;
    void clearErrors();

    // 获取用户友好的错误消息
    QString userMessage(const QString& code) const;
    QString recoveryHint(const QString& code) const;

signals:
    void errorReported(const ErrorInfo& error);
    void errorCountChanged(int count);

private:
    ErrorManager();
    ~ErrorManager() = default;

    void initErrorDefinitions();

    QQueue<ErrorInfo> m_errors;
    int m_maxErrors = 100;
    
    // 预定义的错误码和消息
    QMap<QString, QString> m_userMessages;
    QMap<QString, QString> m_recoveryHints;
};

} // namespace lsc

#endif // ERRORMANAGER_H
```

#### Step 2: 实现错误管理器

```cpp
// src/lsc/core/ErrorManager.cpp
#include "ErrorManager.h"

namespace lsc {

ErrorManager::ErrorManager() {
    initErrorDefinitions();
}

void ErrorManager::initErrorDefinitions() {
    // 直播流相关错误
    m_userMessages["STREAM_EXPIRED"] = tr("直播流已过期");
    m_recoveryHints["STREAM_EXPIRED"] = tr("请刷新页面获取新的直播链接");
    
    m_userMessages["STREAM_DISCONNECTED"] = tr("直播连接断开");
    m_recoveryHints["STREAM_DISCONNECTED"] = tr("正在自动重连...");
    
    m_userMessages["STREAM_403"] = tr("访问被拒绝");
    m_recoveryHints["STREAM_403"] = tr("可能需要登录或使用其他清晰度");
    
    m_userMessages["STREAM_OFFLINE"] = tr("主播已下播");
    m_recoveryHints["STREAM_OFFLINE"] = tr("录制已自动停止");
    
    // FFmpeg 相关错误
    m_userMessages["FFMPEG_ERROR"] = tr("视频处理失败");
    m_recoveryHints["FFMPEG_ERROR"] = tr("请检查文件格式或尝试重新处理");
    
    m_userMessages["FFMPEG_DISK_FULL"] = tr("磁盘空间不足");
    m_recoveryHints["FFMPEG_DISK_FULL"] = tr("请清理磁盘空间后重试");
    
    // ASR 相关错误
    m_userMessages["ASR_TIMEOUT"] = tr("语音识别超时");
    m_recoveryHints["ASR_TIMEOUT"] = tr("可以尝试使用更小的模型或跳过语音识别");
    
    m_userMessages["ASR_MODEL_NOT_FOUND"] = tr("语音识别模型未找到");
    m_recoveryHints["ASR_MODEL_NOT_FOUND"] = tr("请下载 Whisper 模型文件");
    
    // 文件相关错误
    m_userMessages["FILE_CORRUPTED"] = tr("文件损坏");
    m_recoveryHints["FILE_CORRUPTED"] = tr("请尝试重新录制");
    
    m_userMessages["FILE_NOT_FOUND"] = tr("文件未找到");
    m_recoveryHints["FILE_NOT_FOUND"] = tr("文件可能已被移动或删除");
}

ErrorManager& ErrorManager::instance() {
    static ErrorManager s_instance;
    return s_instance;
}

void ErrorManager::reportError(const QString& code, const QString& message,
                               ErrorSeverity severity,
                               RecoveryAction recovery,
                               const QString& technicalDetail) {
    ErrorInfo info;
    info.code = code;
    info.severity = severity;
    info.message = message;
    info.technicalDetail = technicalDetail;
    info.recovery = recovery;
    info.recoveryHint = m_recoveryHints.value(code);
    info.timestamp = QDateTime::currentDateTime();
    info.source = QObject::sender() ? 
                  QObject::sender()->metaObject()->className() : 
                  "Unknown";

    m_errors.enqueue(info);
    while (m_errors.size() > m_maxErrors) {
        m_errors.dequeue();
    }

    emit errorReported(info);
    emit errorCountChanged(m_errors.size());
}

QVector<ErrorInfo> ErrorManager::recentErrors(int count) const {
    QVector<ErrorInfo> result;
    int start = qMax(0, m_errors.size() - count);
    for (int i = start; i < m_errors.size(); ++i) {
        result.append(m_errors.at(i));
    }
    return result;
}

ErrorInfo ErrorManager::lastError() const {
    return m_errors.isEmpty() ? ErrorInfo{} : m_errors.last();
}

bool ErrorManager::hasErrors() const {
    return !m_errors.isEmpty();
}

void ErrorManager::clearErrors() {
    m_errors.clear();
    emit errorCountChanged(0);
}

QString ErrorManager::userMessage(const QString& code) const {
    return m_userMessages.value(code, tr("未知错误"));
}

QString ErrorManager::recoveryHint(const QString& code) const {
    return m_recoveryHints.value(code);
}

} // namespace lsc
```

---

## Phase 2: 数据持久化

### Task 4: SQLite 数据库 (LscDatabase)

**目标:** 创建统一的 SQLite 数据库，存储项目、任务、反馈等数据。

**Files:**
- Create: `src/lsc/core/LscDatabase.h`
- Create: `src/lsc/core/LscDatabase.cpp`

#### Step 1: 定义数据库接口

```cpp
// src/lsc/core/LscDatabase.h
#ifndef LSCDATABASE_H
#define LSCDATABASE_H

#include <QObject>
#include <QSqlDatabase>
#include <QString>
#include <QVariantMap>

namespace lsc {

struct ProjectRecord {
    QString id;
    QString name;
    QString platform;
    QString streamerName;
    QString sourceUrl;
    QString videoPath;
    QDateTime recordedAt;
    qint64 durationSec = 0;
    qint64 fileSizeBytes = 0;
    QString analysisProfile;
    QString status; // "recording", "analyzed", "exported"
    QVariantMap metadata;
};

struct ClipRecord {
    QString id;
    QString projectId;
    double startSec = 0;
    double endSec = 0;
    double score = 0;
    QString reason;
    QString keywords;
    QString title;
    QString thumbnailPath;
    QString exportPath;
    QString status; // "detected", "approved", "rejected", "exported"
    int userRating = 0;
    QString userNote;
    QDateTime createdAt;
};

struct TaskRecord {
    QString id;
    QString type;
    QString status;
    QString title;
    QString error;
    int progress = 0;
    QDateTime createdAt;
    QDateTime startedAt;
    QDateTime finishedAt;
    QVariantMap metadata;
};

class LscDatabase : public QObject {
    Q_OBJECT

public:
    static LscDatabase& instance();

    bool initialize();
    bool isOpen() const;

    // 项目操作
    bool insertProject(const ProjectRecord& project);
    bool updateProject(const ProjectRecord& project);
    bool deleteProject(const QString& projectId);
    ProjectRecord project(const QString& projectId) const;
    QVector<ProjectRecord> allProjects() const;
    QVector<ProjectRecord> projectsByPlatform(const QString& platform) const;
    QVector<ProjectRecord> projectsByStreamer(const QString& streamer) const;
    QVector<ProjectRecord> projectsByDateRange(const QDateTime& start, 
                                                const QDateTime& end) const;

    // 片段操作
    bool insertClip(const ClipRecord& clip);
    bool updateClip(const ClipRecord& clip);
    bool deleteClip(const QString& clipId);
    ClipRecord clip(const QString& clipId) const;
    QVector<ClipRecord> clipsByProject(const QString& projectId) const;
    QVector<ClipRecord> approvedClips(const QString& projectId) const;

    // 任务记录
    bool insertTask(const TaskRecord& task);
    bool updateTask(const TaskRecord& task);
    QVector<TaskRecord> recentTasks(int count = 50) const;

    // 统计
    int totalProjects() const;
    int totalClips() const;
    int totalExportedClips() const;
    qint64 totalStorageUsed() const;

signals:
    void projectAdded(const QString& projectId);
    void projectUpdated(const QString& projectId);
    void projectDeleted(const QString& projectId);
    void clipAdded(const QString& clipId);
    void clipUpdated(const QString& clipId);

private:
    LscDatabase();
    ~LscDatabase();

    bool createTables();
    QString dbPath() const;

    QSqlDatabase m_db;
};

} // namespace lsc

#endif // LSCDATABASE_H
```

#### Step 2: 实现数据库操作

```cpp
// src/lsc/core/LscDatabase.cpp
#include "LscDatabase.h"
#include "LscLog.h"
#include <QSqlQuery>
#include <QSqlError>
#include <QStandardPaths>
#include <QDir>

namespace lsc {

LscDatabase::LscDatabase() = default;

LscDatabase::~LscDatabase() {
    if (m_db.isOpen()) {
        m_db.close();
    }
}

LscDatabase& LscDatabase::instance() {
    static LscDatabase s_instance;
    return s_instance;
}

QString LscDatabase::dbPath() const {
    QString path = QStandardPaths::writableLocation(
        QStandardPaths::AppDataLocation);
    QDir().mkpath(path);
    return path + "/lsc.db";
}

bool LscDatabase::initialize() {
    m_db = QSqlDatabase::addDatabase("QSQLITE");
    m_db.setDatabaseName(dbPath());

    if (!m_db.open()) {
        LSC_LOG_ERROR(QString("Failed to open database: %1")
            .arg(m_db.lastError().text()));
        return false;
    }

    return createTables();
}

bool LscDatabase::isOpen() const {
    return m_db.isOpen();
}

bool LscDatabase::createTables() {
    QSqlQuery query(m_db);
    
    // 项目表
    bool ok = query.exec(
        "CREATE TABLE IF NOT EXISTS projects ("
        "  id TEXT PRIMARY KEY,"
        "  name TEXT,"
        "  platform TEXT,"
        "  streamer_name TEXT,"
        "  source_url TEXT,"
        "  video_path TEXT,"
        "  recorded_at DATETIME,"
        "  duration_sec INTEGER,"
        "  file_size_bytes INTEGER,"
        "  analysis_profile TEXT,"
        "  status TEXT,"
        "  metadata TEXT"
        ")"
    );

    // 片段表
    ok = query.exec(
        "CREATE TABLE IF NOT EXISTS clips ("
        "  id TEXT PRIMARY KEY,"
        "  project_id TEXT,"
        "  start_sec REAL,"
        "  end_sec REAL,"
        "  score REAL,"
        "  reason TEXT,"
        "  keywords TEXT,"
        "  title TEXT,"
        "  thumbnail_path TEXT,"
        "  export_path TEXT,"
        "  status TEXT,"
        "  user_rating INTEGER,"
        "  user_note TEXT,"
        "  created_at DATETIME,"
        "  FOREIGN KEY(project_id) REFERENCES projects(id)"
        ")"
    );

    // 任务记录表
    ok = query.exec(
        "CREATE TABLE IF NOT EXISTS task_history ("
        "  id TEXT PRIMARY KEY,"
        "  type TEXT,"
        "  status TEXT,"
        "  title TEXT,"
        "  error TEXT,"
        "  progress INTEGER,"
        "  created_at DATETIME,"
        "  started_at DATETIME,"
        "  finished_at DATETIME,"
        "  metadata TEXT"
        ")"
    );

    // 索引
    query.exec("CREATE INDEX IF NOT EXISTS idx_clips_project ON clips(project_id)");
    query.exec("CREATE INDEX IF NOT EXISTS idx_projects_platform ON projects(platform)");
    query.exec("CREATE INDEX IF NOT EXISTS idx_projects_streamer ON projects(streamer_name)");

    return ok;
}

bool LscDatabase::insertProject(const ProjectRecord& project) {
    QSqlQuery query(m_db);
    query.prepare(
        "INSERT INTO projects "
        "(id, name, platform, streamer_name, source_url, video_path, "
        " recorded_at, duration_sec, file_size_bytes, analysis_profile, "
        " status, metadata) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
    );
    query.addBindValue(project.id);
    query.addBindValue(project.name);
    query.addBindValue(project.platform);
    query.addBindValue(project.streamerName);
    query.addBindValue(project.sourceUrl);
    query.addBindValue(project.videoPath);
    query.addBindValue(project.recordedAt);
    query.addBindValue(project.durationSec);
    query.addBindValue(project.fileSizeBytes);
    query.addBindValue(project.analysisProfile);
    query.addBindValue(project.status);
    // metadata 需要序列化为 JSON

    if (!query.exec()) {
        LSC_LOG_ERROR(QString("Failed to insert project: %1")
            .arg(query.lastError().text()));
        return false;
    }

    emit projectAdded(project.id);
    return true;
}

// ... 其他 CRUD 操作类似实现

} // namespace lsc
```

---

### Task 5: 历史项目管理

**目标:** 创建历史项目面板，支持筛选、搜索、恢复。

**Files:**
- Create: `src/lsc/docks/HistoryDock.h`
- Create: `src/lsc/docks/HistoryDock.cpp`
- Modify: `src/lsc/app/MainWindow.h`
- Modify: `src/lsc/app/MainWindow.cpp`

#### Step 1: 定义 HistoryDock

```cpp
// src/lsc/docks/HistoryDock.h
#ifndef HISTORYDOCK_H
#define HISTORYDOCK_H

#include <QDockWidget>
#include <QTreeWidget>
#include <QLineEdit>
#include <QComboBox>
#include <QDateTimeEdit>
#include <QPushButton>

namespace lsc {

class LscDatabase;

class HistoryDock : public QDockWidget {
    Q_OBJECT

public:
    explicit HistoryDock(QWidget* parent = nullptr);

signals:
    void projectSelected(const QString& projectId);
    void projectDoubleClicked(const QString& projectId);
    void requestDeleteProject(const QString& projectId);
    void requestReanalyze(const QString& projectId);

private slots:
    void onSearchTextChanged(const QString& text);
    void onPlatformFilterChanged(int index);
    void onDateRangeChanged();
    void onItemDoubleClicked(QTreeWidgetItem* item, int column);
    void onDeleteClicked();
    void onReanalyzeClicked();
    void onRefreshClicked();

private:
    void setupUi();
    void loadProjects();
    void applyFilters();

    QTreeWidget* m_treeWidget;
    QLineEdit* m_searchEdit;
    QComboBox* m_platformCombo;
    QDateTimeEdit* m_dateFrom;
    QDateTimeEdit* m_dateTo;
    QPushButton* m_deleteBtn;
    QPushButton* m_reanalyzeBtn;
    QPushButton* m_refreshBtn;

    LscDatabase& m_db;
};

} // namespace lsc

#endif // HISTORYDOCK_H
```

#### Step 2: 实现 HistoryDock

```cpp
// src/lsc/docks/HistoryDock.cpp
#include "HistoryDock.h"
#include "core/LscDatabase.h"
#include <QVBoxLayout>
#include <QHBoxLayout>
#include <QHeaderView>

namespace lsc {

HistoryDock::HistoryDock(QWidget* parent)
    : QDockWidget(tr("历史项目"), parent)
    , m_db(LscDatabase::instance())
{
    setupUi();
    loadProjects();
}

void HistoryDock::setupUi() {
    auto* widget = new QWidget(this);
    auto* layout = new QVBoxLayout(widget);

    // 搜索和筛选
    auto* filterLayout = new QHBoxLayout();
    
    m_searchEdit = new QLineEdit(this);
    m_searchEdit->setPlaceholderText(tr("搜索主播、平台..."));
    connect(m_searchEdit, &QLineEdit::textChanged,
            this, &HistoryDock::onSearchTextChanged);
    
    m_platformCombo = new QComboBox(this);
    m_platformCombo->addItem(tr("全部平台"), "");
    m_platformCombo->addItem(tr("抖音"), "douyin");
    m_platformCombo->addItem(tr("B站"), "bilibili");
    m_platformCombo->addItem(tr("YouTube"), "youtube");
    connect(m_platformCombo, QOverload<int>::of(&QComboBox::currentIndexChanged),
            this, &HistoryDock::onPlatformFilterChanged);

    filterLayout->addWidget(m_searchEdit);
    filterLayout->addWidget(m_platformCombo);
    layout->addLayout(filterLayout);

    // 日期范围
    auto* dateLayout = new QHBoxLayout();
    m_dateFrom = new QDateTimeEdit(this);
    m_dateFrom->setCalendarPopup(true);
    m_dateFrom->setDateTime(QDateTime::currentDateTime().addMonths(-1));
    m_dateTo = new QDateTimeEdit(this);
    m_dateTo->setCalendarPopup(true);
    m_dateTo->setDateTime(QDateTime::currentDateTime());
    
    connect(m_dateFrom, &QDateTimeEdit::dateTimeChanged,
            this, &HistoryDock::onDateRangeChanged);
    connect(m_dateTo, &QDateTimeEdit::dateTimeChanged,
            this, &HistoryDock::onDateRangeChanged);

    dateLayout->addWidget(m_dateFrom);
    dateLayout->addWidget(new QLabel(" - ", this));
    dateLayout->addWidget(m_dateTo);
    layout->addLayout(dateLayout);

    // 项目列表
    m_treeWidget = new QTreeWidget(this);
    m_treeWidget->setHeaderLabels({
        tr("日期"), tr("主播"), tr("平台"), 
        tr("时长"), tr("片段数"), tr("状态")
    });
    m_treeWidget->header()->setStretchLastSection(true);
    m_treeWidget->setRootIsDecorated(false);
    m_treeWidget->setSelectionMode(QAbstractItemView::SingleSelection);
    connect(m_treeWidget, &QTreeWidget::itemDoubleClicked,
            this, &HistoryDock::onItemDoubleClicked);
    layout->addWidget(m_treeWidget);

    // 操作按钮
    auto* btnLayout = new QHBoxLayout();
    m_deleteBtn = new QPushButton(tr("删除"), this);
    m_reanalyzeBtn = new QPushButton(tr("重新分析"), this);
    m_refreshBtn = new QPushButton(tr("刷新"), this);
    
    connect(m_deleteBtn, &QPushButton::clicked,
            this, &HistoryDock::onDeleteClicked);
    connect(m_reanalyzeBtn, &QPushButton::clicked,
            this, &HistoryDock::onReanalyzeClicked);
    connect(m_refreshBtn, &QPushButton::clicked,
            this, &HistoryDock::onRefreshClicked);

    btnLayout->addWidget(m_deleteBtn);
    btnLayout->addWidget(m_reanalyzeBtn);
    btnLayout->addStretch();
    btnLayout->addWidget(m_refreshBtn);
    layout->addLayout(btnLayout);

    setWidget(widget);
}

void HistoryDock::loadProjects() {
    m_treeWidget->clear();
    const auto projects = m_db.allProjects();
    for (const auto& project : projects) {
        auto* item = new QTreeWidgetItem(m_treeWidget);
        item->setData(0, Qt::UserRole, project.id);
        item->setText(0, project.recordedAt.toString("yyyy-MM-dd hh:mm"));
        item->setText(1, project.streamerName);
        item->setText(2, project.platform);
        item->setText(3, QString("%1 分钟").arg(project.durationSec / 60));
        // TODO: 查询片段数量
        item->setText(5, project.status);
    }
}

void HistoryDock::onSearchTextChanged(const QString& text) {
    Q_UNUSED(text);
    applyFilters();
}

void HistoryDock::onPlatformFilterChanged(int index) {
    Q_UNUSED(index);
    applyFilters();
}

void HistoryDock::onDateRangeChanged() {
    applyFilters();
}

void HistoryDock::onItemDoubleClicked(QTreeWidgetItem* item, int column) {
    Q_UNUSED(column);
    emit projectDoubleClicked(item->data(0, Qt::UserRole).toString());
}

void HistoryDock::onDeleteClicked() {
    auto* item = m_treeWidget->currentItem();
    if (item) {
        emit requestDeleteProject(item->data(0, Qt::UserRole).toString());
    }
}

void HistoryDock::onReanalyzeClicked() {
    auto* item = m_treeWidget->currentItem();
    if (item) {
        emit requestReanalyze(item->data(0, Qt::UserRole).toString());
    }
}

void HistoryDock::onRefreshClicked() {
    loadProjects();
}

void HistoryDock::applyFilters() {
    // 实现筛选逻辑
    loadProjects(); // 简化实现，实际应根据筛选条件查询
}

} // namespace lsc
```

---

### Task 6: 反馈闭环统计

**目标:** 扩展 FeedbackStore，实现统计和可视化。

**Files:**
- Modify: `src/lsc/analyzer/FeedbackStore.h`
- Modify: `src/lsc/analyzer/FeedbackStore.cpp`
- Create: `src/lsc/docks/FeedbackStatsDock.h`
- Create: `src/lsc/docks/FeedbackStatsDock.cpp`

#### Step 1: 扩展 FeedbackStore

```cpp
// 在 FeedbackStore.h 中添加统计方法

struct FeedbackStats {
    int totalClips = 0;
    int keptClips = 0;
    int deletedClips = 0;
    int exportedClips = 0;
    double avgBoundaryAdjustment = 0; // 平均边界调整秒数
    double avgUserRating = 0;
    QMap<QString, int> highlightTypeCounts;
    QMap<QString, int> actionCounts;
};

class FeedbackStore : public QObject {
    // ... 现有代码 ...

    // 新增统计方法
    FeedbackStats statsForProject(const QString& videoPath) const;
    FeedbackStats globalStats() const;
    void exportStatsReport(const QString& outputPath) const;
    
signals:
    void statsUpdated();
};
```

#### Step 2: 实现统计功能

```cpp
// FeedbackStore.cpp 中添加

FeedbackStats FeedbackStore::statsForProject(const QString& videoPath) const {
    FeedbackStats stats;
    const auto feedbacks = loadFeedbacks(videoPath);
    
    for (const auto& fb : feedbacks) {
        stats.totalClips++;
        
        if (fb.action == "keep") stats.keptClips++;
        else if (fb.action == "delete") stats.deletedClips++;
        else if (fb.action == "export") stats.exportedClips++;
        
        if (fb.importance > 0) {
            stats.avgUserRating += fb.importance;
        }
        
        if (!fb.highlightType.isEmpty()) {
            stats.highlightTypeCounts[fb.highlightType]++;
        }
        
        stats.actionCounts[fb.action]++;
        
        if (fb.adjustedStartSec >= 0 || fb.adjustedEndSec >= 0) {
            // 计算边界调整
            stats.avgBoundaryAdjustment += 1.0; // 简化
        }
    }
    
    if (stats.totalClips > 0) {
        stats.avgUserRating /= stats.totalClips;
        stats.avgBoundaryAdjustment /= stats.totalClips;
    }
    
    return stats;
}

FeedbackStats FeedbackStore::globalStats() const {
    FeedbackStats total;
    // 遍历所有反馈文件累加
    return total;
}
```

---

## Phase 3: 结果修正与导出

### Task 7: 片段编辑器 (ClipEditor)

**目标:** 创建可视化的片段编辑器，支持拖动边界、合并、删除。

**Files:**
- Create: `src/lsc/widgets/ClipEditorWidget.h`
- Create: `src/lsc/widgets/ClipEditorWidget.cpp`
- Modify: `src/lsc/docks/AnalysisDock.h`
- Modify: `src/lsc/docks/AnalysisDock.cpp`

#### Step 1: 定义编辑器接口

```cpp
// src/lsc/widgets/ClipEditorWidget.h
#ifndef CLIPEDITORWIDGET_H
#define CLIPEDITORWIDGET_H

#include <QWidget>
#include <QVector>

namespace lsc {

struct EditableClip {
    QString id;
    double startSec;
    double endSec;
    double score;
    QString title;
    bool selected = true;
    bool modified = false;
};

class ClipEditorWidget : public QWidget {
    Q_OBJECT

public:
    explicit ClipEditorWidget(QWidget* parent = nullptr);

    void setClips(const QVector<EditableClip>& clips);
    void setVideoDuration(double durationSec);
    QVector<EditableClip> clips() const;

signals:
    void clipBoundaryChanged(const QString& clipId, 
                             double newStart, double newEnd);
    void clipsMerged(const QString& clipId1, const QString& clipId2);
    void clipDeleted(const QString& clipId);
    void clipSelected(const QString& clipId);
    void selectionChanged();

protected:
    void paintEvent(QPaintEvent* event) override;
    void mousePressEvent(QMouseEvent* event) override;
    void mouseMoveEvent(QMouseEvent* event) override;
    void mouseReleaseEvent(QMouseEvent* event) override;

private:
    enum class DragMode {
        None,
        MoveStart,
        MoveEnd,
        MoveClip
    };

    int clipAtPosition(const QPoint& pos) const;
    QRect clipRect(int index) const;
    double posToTime(int x) const;
    int timeToPos(double sec) const;

    QVector<EditableClip> m_clips;
    double m_durationSec = 0;
    int m_dragIndex = -1;
    DragMode m_dragMode = DragMode::None;
    int m_dragStartX = 0;
    double m_dragOriginalStart = 0;
    double m_dragOriginalEnd = 0;
    int m_timelineHeight = 60;
    int m_clipHeight = 40;
};

} // namespace lsc

#endif // CLIPEDITORWIDGET_H
```

#### Step 2: 实现可视化编辑器

```cpp
// src/lsc/widgets/ClipEditorWidget.cpp
#include "ClipEditorWidget.h"
#include <QPainter>
#include <QMouseEvent>

namespace lsc {

ClipEditorWidget::ClipEditorWidget(QWidget* parent)
    : QWidget(parent)
{
    setMinimumHeight(100);
    setMouseTracking(true);
}

void ClipEditorWidget::setClips(const QVector<EditableClip>& clips) {
    m_clips = clips;
    update();
}

void ClipEditorWidget::setVideoDuration(double durationSec) {
    m_durationSec = durationSec;
    update();
}

QVector<EditableClip> ClipEditorWidget::clips() const {
    return m_clips;
}

void ClipEditorWidget::paintEvent(QPaintEvent* event) {
    Q_UNUSED(event);
    
    QPainter painter(this);
    painter.setRenderHint(QPainter::Antialiasing);
    
    // 绘制时间轴背景
    painter.fillRect(rect(), QColor(40, 40, 40));
    
    // 绘制时间刻度
    if (m_durationSec > 0) {
        painter.setPen(Qt::gray);
        for (int sec = 0; sec <= m_durationSec; sec += 10) {
            int x = timeToPos(sec);
            painter.drawLine(x, 0, x, 20);
            painter.drawText(x + 2, 15, QString::number(sec) + "s");
        }
    }
    
    // 绘制片段
    for (int i = 0; i < m_clips.size(); ++i) {
        QRect r = clipRect(i);
        
        // 选中状态
        QColor color = m_clips[i].selected ? 
                       QColor(66, 133, 244) : QColor(100, 100, 100);
        painter.fillRect(r, color);
        
        // 边界手柄
        painter.setPen(Qt::white);
        painter.drawRect(r.left(), r.top(), 3, r.height());
        painter.drawRect(r.right() - 3, r.top(), 3, r.height());
        
        // 标题
        painter.drawText(r.adjusted(5, 5, -5, -5), 
                        Qt::AlignLeft | Qt::AlignTop,
                        m_clips[i].title);
    }
}

void ClipEditorWidget::mousePressEvent(QMouseEvent* event) {
    if (event->button() != Qt::LeftButton) return;
    
    for (int i = 0; i < m_clips.size(); ++i) {
        QRect r = clipRect(i);
        
        // 检查左边界
        if (QRect(r.left(), r.top(), 5, r.height()).contains(event->pos())) {
            m_dragIndex = i;
            m_dragMode = DragMode::MoveStart;
            m_dragStartX = event->pos().x();
            m_dragOriginalStart = m_clips[i].startSec;
            return;
        }
        
        // 检查右边界
        if (QRect(r.right() - 5, r.top(), 5, r.height()).contains(event->pos())) {
            m_dragIndex = i;
            m_dragMode = DragMode::MoveEnd;
            m_dragStartX = event->pos().x();
            m_dragOriginalEnd = m_clips[i].endSec;
            return;
        }
        
        // 检查片段内部
        if (r.contains(event->pos())) {
            m_clips[i].selected = !m_clips[i].selected;
            emit clipSelected(m_clips[i].id);
            emit selectionChanged();
            update();
            return;
        }
    }
}

void ClipEditorWidget::mouseMoveEvent(QMouseEvent* event) {
    if (m_dragIndex < 0) return;
    
    double deltaSec = (event->pos().x() - m_dragStartX) * 
                      m_durationSec / width();
    
    switch (m_dragMode) {
        case DragMode::MoveStart:
            m_clips[m_dragIndex].startSec = 
                qMax(0.0, m_dragOriginalStart + deltaSec);
            break;
        case DragMode::MoveEnd:
            m_clips[m_dragIndex].endSec = 
                qMin(m_durationSec, m_dragOriginalEnd + deltaSec);
            break;
        default:
            break;
    }
    
    m_clips[m_dragIndex].modified = true;
    update();
}

void ClipEditorWidget::mouseReleaseEvent(QMouseEvent* event) {
    if (m_dragIndex >= 0 && m_dragMode != DragMode::None) {
        emit clipBoundaryChanged(
            m_clips[m_dragIndex].id,
            m_clips[m_dragIndex].startSec,
            m_clips[m_dragIndex].endSec
        );
    }
    
    m_dragIndex = -1;
    m_dragMode = DragMode::None;
}

int ClipEditorWidget::clipAtPosition(const QPoint& pos) const {
    for (int i = 0; i < m_clips.size(); ++i) {
        if (clipRect(i).contains(pos)) {
            return i;
        }
    }
    return -1;
}

QRect ClipEditorWidget::clipRect(int index) const {
    int x1 = timeToPos(m_clips[index].startSec);
    int x2 = timeToPos(m_clips[index].endSec);
    return QRect(x1, m_timelineHeight, x2 - x1, m_clipHeight);
}

double ClipEditorWidget::posToTime(int x) const {
    return x * m_durationSec / width();
}

int ClipEditorWidget::timeToPos(double sec) const {
    return sec * width() / m_durationSec;
}

} // namespace lsc
```

---

### Task 8: 导出产品化

**目标:** 完善导出功能，支持命名模板、分辨率、竖屏裁切、封面、字幕。

**Files:**
- Modify: `src/lsc/analyzer/ClipExporter.h`
- Modify: `src/lsc/analyzer/ClipExporter.cpp`
- Create: `src/lsc/dialogs/ExportSettingsDialog.h`
- Create: `src/lsc/dialogs/ExportSettingsDialog.cpp`

#### Step 1: 定义导出配置

```cpp
// 在 ClipExporter.h 中添加

struct ExportConfig {
    // 输出设置
    QString outputDir;
    QString filenameTemplate = "{streamer}_{date}_{index}";
    QString format = "mp4";
    
    // 视频设置
    int width = 0;  // 0 表示保持原始
    int height = 0;
    int bitrate = 0;  // 0 表示保持原始
    QString codec = "copy";  // "copy" 或 "h264"/"h265"
    int crf = 23;
    
    // 竖屏裁切
    bool verticalCrop = false;
    double cropX = 0.1;  // 裁切区域 (比例)
    double cropY = 0.0;
    double cropWidth = 0.8;
    double cropHeight = 1.0;
    
    // 字幕
    bool burnSubtitles = false;
    QString subtitlePath;
    QString subtitleStyle;
    
    // 封面
    bool generateThumbnail = true;
    int thumbnailTimeSec = 0;  // 0 表示取中间
    int thumbnailWidth = 1280;
    int thumbnailHeight = 720;
    
    // 元数据
    QString title;
    QString description;
    QStringList tags;
};
```

#### Step 2: 实现导出设置对话框

```cpp
// src/lsc/dialogs/ExportSettingsDialog.h
#ifndef EXPORTSETTINGSDIALOG_H
#define EXPORTSETTINGSDIALOG_H

#include <QDialog>
#include "analyzer/ClipExporter.h"

class QLineEdit;
class QComboBox;
class QSpinBox;
class QCheckBox;

namespace lsc {

class ExportSettingsDialog : public QDialog {
    Q_OBJECT

public:
    explicit ExportSettingsDialog(QWidget* parent = nullptr);

    ExportConfig config() const;
    void setConfig(const ExportConfig& config);

private slots:
    void onBrowseOutputDir();

private:
    void setupUi();

    QLineEdit* m_outputDirEdit;
    QLineEdit* m_filenameTemplateEdit;
    QComboBox* m_formatCombo;
    QSpinBox* m_widthSpin;
    QSpinBox* m_heightSpin;
    QComboBox* m_codecCombo;
    QSpinBox* m_crfSpin;
    QCheckBox* m_verticalCropCheck;
    QCheckBox* m_burnSubtitlesCheck;
    QCheckBox* m_generateThumbnailCheck;
};

} // namespace lsc

#endif // EXPORTSETTINGSDIALOG_H
```

---

### Task 9: 批量导出队列

**目标:** 实现批量导出，支持队列管理、失败重试。

**Files:**
- Modify: `src/lsc/analyzer/ClipExporter.h`
- Modify: `src/lsc/analyzer/ClipExporter.cpp`

#### Step 1: 实现批量导出

```cpp
// ClipExporter.h 中添加

class ClipExporter : public QObject {
    Q_OBJECT

public:
    // ... 现有接口 ...

    // 批量导出
    void exportBatch(const QVector<ClipJob>& jobs, 
                     const ExportConfig& config);
    void cancelBatch();
    int pendingCount() const;
    int completedCount() const;
    int failedCount() const;

signals:
    void batchProgress(int completed, int total, int failed);
    void allBatchFinished(int successCount, int failCount);

private:
    QVector<ClipJob> m_batchQueue;
    ExportConfig m_batchConfig;
    int m_batchCompleted = 0;
    int m_batchFailed = 0;
};
```

---

## Phase 4: 配置与诊断

### Task 10: 设置面板 (SettingsPanel)

**目标:** 创建用户友好的设置界面，替代直接编辑配置文件。

**Files:**
- Create: `src/lsc/docks/SettingsDock.h`
- Create: `src/lsc/docks/SettingsDock.cpp`

#### Step 1: 创建设置面板

```cpp
// src/lsc/docks/SettingsDock.h
#ifndef SETTINGSDOCK_H
#define SETTINGSDOCK_H

#include <QDockWidget>

class QTabWidget;
class QComboBox;
class QSlider;
class QSpinBox;
class QCheckBox;
class QLineEdit;

namespace lsc {

class SettingsDock : public QDockWidget {
    Q_OBJECT

public:
    explicit SettingsDock(QWidget* parent = nullptr);

signals:
    void settingsChanged();

private slots:
    void onSettingChanged();
    void onResetDefaults();
    void onImportSettings();
    void onExportSettings();

private:
    void setupUi();
    void loadSettings();
    void saveSettings();

    // 分析模式
    QComboBox* m_analysisProfileCombo;
    QComboBox* m_gameTypeCombo;
    QSlider* m_sensitivitySlider;
    QSpinBox* m_minClipLengthSpin;
    QSpinBox* m_maxClipLengthSpin;

    // 录制设置
    QComboBox* m_defaultQualityCombo;
    QComboBox* m_defaultFormatCombo;
    QCheckBox* m_autoAnalyzeCheck;
    QCheckBox* m_enableASRCheck;

    // 导出设置
    QLineEdit* m_defaultOutputDirEdit;
    QLineEdit* m_filenameTemplateEdit;
    QComboBox* m_defaultResolutionCombo;

    // Whisper 设置
    QComboBox* m_whisperModelCombo;
    QComboBox* m_whisperLanguageCombo;
};

} // namespace lsc

#endif // SETTINGSDOCK_H
```

---

### Task 11: 诊断面板 (DiagnosticsPanel)

**目标:** 创建诊断面板，支持导出诊断包。

**Files:**
- Create: `src/lsc/docks/DiagnosticsDock.h`
- Create: `src/lsc/docks/DiagnosticsDock.cpp`

#### Step 1: 定义诊断信息收集

```cpp
// src/lsc/docks/DiagnosticsDock.h
#ifndef DIAGNOSTICSDOCK_H
#define DIAGNOSTICSDOCK_H

#include <QDockWidget>
#include <QTextEdit>
#include <QPushButton>

namespace lsc {

struct DiagnosticInfo {
    QString appVersion;
    QString osInfo;
    QString qtVersion;
    QString ffmpegVersion;
    QString whisperVersion;
    QString gpuInfo;
    qint64 diskFreeBytes;
    qint64 totalMemoryBytes;
    QVector<QString> recentLogs;
    QVector<QString> recentErrors;
    QVariantMap currentConfig;
    QVector<QVariantMap> recentTasks;
};

class DiagnosticsDock : public QDockWidget {
    Q_OBJECT

public:
    explicit DiagnosticsDock(QWidget* parent = nullptr);

private slots:
    void onRefreshClicked();
    void onExportClicked();
    void onClearLogsClicked();

private:
    void setupUi();
    DiagnosticInfo collectDiagnostics() const;
    QString formatDiagnostics(const DiagnosticInfo& info) const;
    void exportDiagnosticPackage(const QString& outputPath) const;

    QTextEdit* m_infoText;
    QPushButton* m_refreshBtn;
    QPushButton* m_exportBtn;
    QPushButton* m_clearBtn;
};

} // namespace lsc

#endif // DIAGNOSTICSDOCK_H
```

#### Step 2: 实现诊断收集

```cpp
// src/lsc/docks/DiagnosticsDock.cpp
#include "DiagnosticsDock.h"
#include "LscConfig.h"
#include "LscLog.h"
#include "core/ErrorManager.h"
#include "core/TaskCenter.h"
#include <QSysInfo>
#include <QProcess>
#include <QFileDialog>
#include <QJsonDocument>

namespace lsc {

DiagnosticInfo DiagnosticsDock::collectDiagnostics() const {
    DiagnosticInfo info;
    
    // 系统信息
    info.osInfo = QSysInfo::prettyProductName();
    info.qtVersion = qVersion();
    
    // FFmpeg 版本
    QProcess ffmpeg;
    ffmpeg.start(LscConfig::instance().ffmpegProgram(), {"-version"});
    ffmpeg.waitForFinished(5000);
    info.ffmpegVersion = ffmpeg.readAllStandardOutput().split('\n').first();
    
    // 磁盘空间
    QStorageInfo storage(QStandardPaths::writableLocation(
        QStandardPaths::AppDataLocation));
    info.diskFreeBytes = storage.bytesAvailable();
    
    // 最近日志
    // TODO: 实现日志收集
    
    // 最近错误
    const auto errors = ErrorManager::instance().recentErrors(20);
    for (const auto& err : errors) {
        info.recentErrors.append(
            QString("[%1] %2: %3")
                .arg(err.timestamp.toString("hh:mm:ss"))
                .arg(err.code)
                .arg(err.message)
        );
    }
    
    // 最近任务
    const auto tasks = TaskCenter::instance().allTasks();
    for (const auto& task : tasks) {
        QVariantMap map;
        map["id"] = task.id;
        map["title"] = task.title;
        map["state"] = static_cast<int>(task.state);
        map["error"] = task.errorText;
        info.recentTasks.append(map);
    }
    
    return info;
}

void DiagnosticsDock::exportDiagnosticPackage(const QString& outputPath) const {
    DiagnosticInfo info = collectDiagnostics();
    
    QJsonObject root;
    root["appVersion"] = info.appVersion;
    root["osInfo"] = info.osInfo;
    root["qtVersion"] = info.qtVersion;
    root["ffmpegVersion"] = info.ffmpegVersion;
    root["diskFreeGB"] = info.diskFreeBytes / (1024.0 * 1024 * 1024);
    
    QJsonArray errors;
    for (const auto& err : info.recentErrors) {
        errors.append(err);
    }
    root["recentErrors"] = errors;
    
    QJsonDocument doc(root);
    
    QFile file(outputPath);
    if (file.open(QIODevice::WriteOnly)) {
        file.write(doc.toJson());
    }
}

} // namespace lsc
```

---

## Phase 5: 多平台抽象

### Task 12: 平台解析器重构

**目标:** 将平台解析抽象为统一接口，支持扩展。

**Files:**
- Modify: `src/lsc/livestream/PlatformParser.h`
- Modify: `src/lsc/livestream/PlatformParser.cpp`
- Create: `src/lsc/livestream/platforms/DouyinParser.h`
- Create: `src/lsc/livestream/platforms/DouyinParser.cpp`
- Create: `src/lsc/livestream/platforms/BilibiliParser.h`
- Create: `src/lsc/livestream/platforms/BilibiliParser.cpp`

#### Step 1: 定义平台接口

```cpp
// src/lsc/livestream/IPlatformParser.h
#ifndef IPLATFORMPARSER_H
#define IPLATFORMPARSER_H

#include <QObject>
#include <QString>

namespace lsc {

struct StreamInfo {
    QString platform;
    QString streamUrl;
    QString backupStreamUrl;
    QString roomId;
    QString title;
    QString streamerName;
    QStringList availableQualities;
    QString selectedQuality;
    QVariantMap cookies;
    QVariantMap headers;
    bool isLive = false;
};

class IPlatformParser : public QObject {
    Q_OBJECT

public:
    virtual ~IPlatformParser() = default;

    virtual QString platformName() const = 0;
    virtual bool canParse(const QString& url) const = 0;
    virtual void parse(const QString& url) = 0;
    virtual void cancel() = 0;

signals:
    void parseComplete(const StreamInfo& info);
    void parseError(const QString& error);
};

} // namespace lsc

#endif // IPLATFORMPARSER_H
```

#### Step 2: 实现平台特定解析器

```cpp
// src/lsc/livestream/platforms/DouyinParser.h
#ifndef DOUYINPARSER_H
#define DOUYINPARSER_H

#include "../IPlatformParser.h"

namespace lsc {

class DouyinParser : public IPlatformParser {
    Q_OBJECT

public:
    explicit DouyinParser(QObject* parent = nullptr);

    QString platformName() const override { return "douyin"; }
    bool canParse(const QString& url) const override;
    void parse(const QString& url) override;
    void cancel() override;

private:
    void parseFromSSR(const QString& html);
    QString extractRoomId(const QString& url) const;

    bool m_cancelled = false;
};

} // namespace lsc

#endif // DOUYINPARSER_H
```

---

## 实施建议

### 优先级排序

1. **Phase 1 (P0)**: 主流程闭环 — 这是最核心的用户体验问题
2. **Phase 2 (P0)**: 数据持久化 — 没有数据，历史管理和反馈闭环无法实现
3. **Phase 3 (P1)**: 结果修正与导出 — 提升用户效率
4. **Phase 4 (P1)**: 配置与诊断 — 提升可维护性
5. **Phase 5 (P2)**: 多平台抽象 — 扩展性

### 每个 Task 的验收标准

1. **Task 1 (TaskCenter)**: 
   - 可以创建、更新、查询任务
   - TaskDock 显示所有任务状态
   - 可以取消、重试失败任务

2. **Task 2 (WorkflowOrchestrator)**:
   - 输入 URL 后自动完成 录制→分析→预览→导出 流程
   - 状态变化有 UI 反馈
   - 可以取消流程

3. **Task 3 (ErrorManager)**:
   - 所有错误都有用户友好的提示
   - 可重试的错误有重试按钮
   - 错误历史可查询

4. **Task 4 (LscDatabase)**:
   - 项目、片段、任务数据持久化
   - 支持按平台、主播、日期筛选
   - 统计数据正确

5. **Task 5 (HistoryDock)**:
   - 显示历史项目列表
   - 支持搜索和筛选
   - 可以删除、重新分析项目

6. **Task 6 (FeedbackStats)**:
   - 统计数据正确
   - 可导出报表

7. **Task 7 (ClipEditor)**:
   - 可视化显示片段
   - 可以拖动边界
   - 可以选择、删除片段

8. **Task 8 (ExportConfig)**:
   - 支持命名模板
   - 支持分辨率、码率设置
   - 支持竖屏裁切

9. **Task 9 (BatchExport)**:
   - 可以批量导出
   - 显示进度
   - 失败可重试

10. **Task 10 (SettingsDock)**:
    - 所有配置项都在 UI 中
    - 有合理默认值
    - 可以导入/导出配置

11. **Task 11 (DiagnosticsDock)**:
    - 显示系统信息
    - 可以导出诊断包

12. **Task 12 (PlatformParsers)**:
    - 平台解析器可插拔
    - 抖音、B站解析器工作正常

### 依赖关系

```
Task 1 (TaskCenter) ─────────┬──→ Task 2 (WorkflowOrchestrator)
                              │
                              ├──→ Task 3 (ErrorManager)
                              │
Task 4 (LscDatabase) ────────┼──→ Task 5 (HistoryDock)
                              │
                              └──→ Task 6 (FeedbackStats)

Task 7 (ClipEditor) ─────────┴──→ Task 8 (ExportConfig)
                                    │
                                    └──→ Task 9 (BatchExport)

Task 10 (SettingsDock) ─────────── 独立

Task 11 (DiagnosticsDock) ──────── 依赖 Task 1, Task 3

Task 12 (PlatformParsers) ──────── 独立
```

### 预估工时

| Phase | Tasks | 预估工时 |
|-------|-------|----------|
| Phase 1 | Task 1-3 | 3-4 天 |
| Phase 2 | Task 4-6 | 2-3 天 |
| Phase 3 | Task 7-9 | 3-4 天 |
| Phase 4 | Task 10-11 | 2 天 |
| Phase 5 | Task 12 | 2 天 |
| **总计** | 12 Tasks | **12-15 天** |

---

## 执行方式选择

计划已保存到 `docs/superpowers/plans/2026-06-06-product-closed-loops.md`

**两种执行方式：**

1. **Subagent-Driven (推荐)** - 每个 Task 分派独立 subagent，任务间审查，快速迭代

2. **Inline Execution** - 在当前会话中执行，批量执行+检查点审查

**选择哪种方式？**
