#include "TaskCenter.h"
#include "LscDatabase.h"
#include "LscLog.h"

#define MODULE_NAME "TaskCenter"

namespace lsc {

namespace {
QString taskTypeToString(TaskType type)
{
    switch (type) {
    case TaskType::Recording: return "recording";
    case TaskType::Analysis: return "analysis";
    case TaskType::ASR: return "asr";
    case TaskType::Export: return "export";
    case TaskType::Import: return "import";
    }
    return "unknown";
}

QString taskStateToString(TaskState state)
{
    switch (state) {
    case TaskState::Queued: return "queued";
    case TaskState::Running: return "running";
    case TaskState::Paused: return "paused";
    case TaskState::Completed: return "completed";
    case TaskState::Failed: return "failed";
    case TaskState::Cancelling: return "cancelling";
    case TaskState::Cancelled: return "cancelled";
    }
    return "unknown";
}

TaskRecord toTaskRecord(const TaskInfo& info)
{
    TaskRecord record;
    record.id = info.id;
    record.type = taskTypeToString(info.type);
    record.status = taskStateToString(info.state);
    record.title = info.title;
    record.error = info.errorText;
    record.progress = info.progress;
    record.createdAt = info.createdAt;
    record.startedAt = info.startedAt;
    record.finishedAt = info.finishedAt;
    record.metadata = info.metadata;
    record.metadata.insert("description", info.description);
    record.metadata.insert("statusText", info.statusText);
    record.metadata.insert("retryable", info.retryable);
    record.metadata.insert("retryCount", info.retryCount);
    record.metadata.insert("maxRetries", info.maxRetries);
    return record;
}
}

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
    persistTask(info, true);
    emit taskCreated(info.id);

    LSC_LOG_INFO << "Task created:" << info.id << "-" << title;
    return info.id;
}

void TaskCenter::startTask(const QString& taskId) {
    auto it = m_tasks.find(taskId);
    if (it == m_tasks.end()) return;

    if (it->state != TaskState::Queued && it->state != TaskState::Paused) {
        LSC_LOG_ERROR << "Cannot start task" << taskId << "- invalid state";
        return;
    }

    it->state = TaskState::Running;
    it->startedAt = QDateTime::currentDateTime();
    persistTask(*it);
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
    persistTask(*it);
    emit taskProgressChanged(taskId, it->progress);
}

void TaskCenter::completeTask(const QString& taskId) {
    auto it = m_tasks.find(taskId);
    if (it == m_tasks.end()) return;

    if (it->state != TaskState::Running) {
        LSC_LOG_ERROR << "Cannot complete task" << taskId << "- invalid state";
        return;
    }

    it->state = TaskState::Completed;
    it->progress = 100;
    it->finishedAt = QDateTime::currentDateTime();
    persistTask(*it);
    emit taskCompleted(taskId);
    emit taskStateChanged(taskId, it->state);
    emit activeTaskCountChanged(activeTaskCount());

    LSC_LOG_INFO << "Task completed:" << taskId;
}

void TaskCenter::failTask(const QString& taskId, const QString& error,
                          bool retryable) {
    auto it = m_tasks.find(taskId);
    if (it == m_tasks.end()) return;

    if (it->state != TaskState::Running) {
        LSC_LOG_ERROR << "Cannot fail task" << taskId << "- invalid state";
        return;
    }

    it->state = TaskState::Failed;
    it->errorText = error;
    it->retryable = retryable;
    it->finishedAt = QDateTime::currentDateTime();
    persistTask(*it);
    emit taskFailed(taskId, error);
    emit taskStateChanged(taskId, it->state);
    emit activeTaskCountChanged(activeTaskCount());

    LSC_LOG_ERROR << "Task failed:" << taskId << "-" << error;
}

void TaskCenter::cancelTask(const QString& taskId) {
    auto it = m_tasks.find(taskId);
    if (it == m_tasks.end()) return;

    if (it->state != TaskState::Running &&
        it->state != TaskState::Paused &&
        it->state != TaskState::Queued) {
        LSC_LOG_ERROR << "Cannot cancel task" << taskId << "- invalid state";
        return;
    }

    it->state = TaskState::Cancelled;
    it->finishedAt = QDateTime::currentDateTime();
    persistTask(*it);
    emit taskCancelled(taskId);
    emit taskStateChanged(taskId, it->state);
    emit activeTaskCountChanged(activeTaskCount());
}

void TaskCenter::pauseTask(const QString& taskId) {
    auto it = m_tasks.find(taskId);
    if (it == m_tasks.end()) return;

    if (it->state == TaskState::Running) {
        it->state = TaskState::Paused;
        persistTask(*it);
        emit taskStateChanged(taskId, it->state);
    }
}

void TaskCenter::resumeTask(const QString& taskId) {
    auto it = m_tasks.find(taskId);
    if (it == m_tasks.end()) return;

    if (it->state == TaskState::Paused) {
        it->state = TaskState::Running;
        persistTask(*it);
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
            it->progress = 0;
            it->errorText.clear();
            persistTask(*it);
            emit taskStateChanged(taskId, it->state);
            emit activeTaskCountChanged(activeTaskCount());
        }
    }
}

int TaskCenter::recoverInterruptedTasks(const QString& reason)
{
    auto& db = LscDatabase::instance();
    if (!db.isOpen() && !db.initialize()) {
        LSC_LOG_ERROR << "Cannot recover interrupted tasks - database unavailable";
        return 0;
    }

    int recovered = 0;
    const auto tasks = db.recentTasks(500);
    for (TaskRecord task : tasks) {
        const QString status = task.status.toLower();
        if (status != "queued" && status != "running" && status != "paused"
            && status != "cancelling") {
            continue;
        }

        task.status = "failed";
        task.error = reason;
        task.finishedAt = QDateTime::currentDateTime();
        task.metadata.insert("interrupted", true);
        task.metadata.insert("interruptedReason", reason);
        if (db.updateTask(task)) {
            ++recovered;
        }
    }

    if (recovered > 0) {
        LSC_LOG_INFO << "Recovered interrupted tasks:" << recovered;
    }
    return recovered;
}

bool TaskCenter::hasTask(const QString& taskId) const {
    return m_tasks.contains(taskId);
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
    bool anyCleared = false;
    while (it != m_tasks.end()) {
        if (it->state == TaskState::Completed ||
            it->state == TaskState::Cancelled) {
            it = m_tasks.erase(it);
            anyCleared = true;
        } else {
            ++it;
        }
    }
    if (anyCleared) {
        emit activeTaskCountChanged(activeTaskCount());
    }
}

void TaskCenter::clearAll() {
    m_tasks.clear();
    emit activeTaskCountChanged(0);
}

void TaskCenter::persistTask(const TaskInfo& task, bool isNewTask)
{
    auto& db = LscDatabase::instance();
    if (!db.isOpen() && !db.initialize()) {
        LSC_LOG_ERROR << "Cannot persist task - database unavailable:" << task.id;
        return;
    }

    const TaskRecord record = toTaskRecord(task);
    if (isNewTask) {
        db.insertTask(record);
    } else {
        db.updateTask(record);
    }
}

} // namespace lsc

#undef MODULE_NAME
