#ifndef TASKDOCK_H
#define TASKDOCK_H

#include <QDockWidget>
#include <QTreeWidget>
#include <QPushButton>
#include <QLabel>

#include "core/TaskCenter.h"

namespace lsc {

class TaskDock : public QDockWidget {
    Q_OBJECT

public:
    explicit TaskDock(QWidget* parent = nullptr);

private slots:
    void onTaskCreated(const QString& taskId);
    void onTaskProgressChanged(const QString& taskId, int progress);
    void onTaskCompleted(const QString& taskId);
    void onTaskFailed(const QString& taskId, const QString& error);
    void onTaskStateChanged(const QString& taskId, TaskState state);
    void onActiveTaskCountChanged(int count);
    void onCancelClicked();
    void onRetryClicked();
    void onClearCompletedClicked();

private:
    void setupUi();
    void refreshTaskList();
    QTreeWidgetItem* findOrCreateItem(const QString& taskId);
    void updateItem(QTreeWidgetItem* item, const TaskInfo& info);
    QString stateToString(TaskState state) const;
    QString typeToString(TaskType type) const;
    QIcon stateIcon(TaskState state) const;

    QTreeWidget* m_treeWidget;
    QPushButton* m_cancelBtn;
    QPushButton* m_retryBtn;
    QPushButton* m_clearBtn;
    QLabel* m_statusLabel;
    TaskCenter& m_taskCenter;
};

} // namespace lsc

#endif // TASKDOCK_H
