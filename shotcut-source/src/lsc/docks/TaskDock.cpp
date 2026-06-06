#include "TaskDock.h"
#include "core/TaskCenter.h"

#include <QVBoxLayout>
#include <QHBoxLayout>
#include <QHeaderView>

#define MODULE_NAME "TaskDock"

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
    connect(&m_taskCenter, &TaskCenter::activeTaskCountChanged,
            this, &TaskDock::onActiveTaskCountChanged);
}

void TaskDock::setupUi() {
    auto* widget = new QWidget(this);
    auto* layout = new QVBoxLayout(widget);

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

    m_treeWidget = new QTreeWidget(this);
    m_treeWidget->setHeaderLabels({
        tr("状态"), tr("类型"), tr("标题"),
        tr("进度"), tr("状态信息")
    });
    m_treeWidget->header()->setStretchLastSection(true);
    m_treeWidget->setRootIsDecorated(false);
    m_treeWidget->setSelectionMode(QAbstractItemView::SingleSelection);
    layout->addWidget(m_treeWidget);

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
    Q_UNUSED(error);
    auto* item = findOrCreateItem(taskId);
    if (item) {
        auto info = m_taskCenter.taskInfo(taskId);
        updateItem(item, info);
    }
}

void TaskDock::onTaskStateChanged(const QString& taskId, TaskState state) {
    Q_UNUSED(state);
    auto* item = findOrCreateItem(taskId);
    if (item) {
        auto info = m_taskCenter.taskInfo(taskId);
        updateItem(item, info);
    }
}

void TaskDock::onActiveTaskCountChanged(int count) {
    m_statusLabel->setText(tr("活跃任务: %1").arg(count));
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
    item->setIcon(0, stateIcon(info.state));
    item->setText(0, stateToString(info.state));
    item->setText(1, typeToString(info.type));
    item->setText(2, info.title);
    item->setText(3, QString("%1%").arg(info.progress));
    item->setText(4, info.state == TaskState::Failed ? info.errorText : info.statusText);
}

QString TaskDock::stateToString(TaskState state) const {
    switch (state) {
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

QString TaskDock::typeToString(TaskType type) const {
    switch (type) {
        case TaskType::Recording: return tr("录制");
        case TaskType::Analysis: return tr("分析");
        case TaskType::ASR: return tr("语音识别");
        case TaskType::Export: return tr("导出");
        case TaskType::Import: return tr("导入");
        default: return tr("未知");
    }
}

QIcon TaskDock::stateIcon(TaskState state) const {
    Q_UNUSED(state);
    return QIcon();
}

} // namespace lsc

#undef MODULE_NAME
