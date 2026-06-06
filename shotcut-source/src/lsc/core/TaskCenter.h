#ifndef TASKCENTER_H
#define TASKCENTER_H

// Thread safety: All public API methods must be called from the main thread
// (Qt event loop thread). This class is NOT thread-safe and is designed for
// single-threaded Qt GUI code, consistent with the rest of the codebase.

#include <QObject>
#include <QString>
#include <QVariantMap>
#include <QUuid>
#include <QDateTime>
#include <QVector>
#include <QMap>

namespace lsc {

enum class TaskType {
    Recording,
    Analysis,
    ASR,
    Export,
    Import
};

enum class TaskState {
    Queued,
    Running,
    Paused,
    Completed,
    Failed,
    Cancelling,
    Cancelled
};

struct TaskInfo {
    QString id;
    TaskType type;
    TaskState state;
    QString title;
    QString description;
    int progress = 0;
    QString statusText;
    QString errorText;
    QDateTime createdAt;
    QDateTime startedAt;
    QDateTime finishedAt;
    QVariantMap metadata;
    bool retryable = false;
    int retryCount = 0;
    int maxRetries = 3;
};

class TaskCenter : public QObject {
    Q_OBJECT

public:
    static TaskCenter& instance();

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
    int recoverInterruptedTasks(const QString& reason = QStringLiteral("Application restarted"));

    bool hasTask(const QString& taskId) const;
    // Returns a default-constructed TaskInfo if taskId does not exist.
    TaskInfo taskInfo(const QString& taskId) const;
    QVector<TaskInfo> allTasks() const;
    QVector<TaskInfo> tasksByType(TaskType type) const;
    QVector<TaskInfo> tasksByState(TaskState state) const;
    int activeTaskCount() const;
    bool hasActiveTasks() const;

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

    void persistTask(const TaskInfo& task, bool isNewTask = false);

    QMap<QString, TaskInfo> m_tasks;
};

} // namespace lsc

#endif // TASKCENTER_H
