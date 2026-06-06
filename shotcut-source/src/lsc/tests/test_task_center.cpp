#include "core/LscDatabase.h"
#include "core/TaskCenter.h"

#include <QCoreApplication>
#include <QDateTime>
#include <iostream>

static int g_testCount = 0;
static int g_passCount = 0;
static int g_failCount = 0;

#define LOG(msg) std::cout << QString(msg).toStdString() << std::endl

void runTest(const QString& name, bool condition)
{
    ++g_testCount;
    if (condition) {
        ++g_passCount;
        LOG(QString("[PASS] %1").arg(name));
    } else {
        ++g_failCount;
        LOG(QString("[FAIL] %1").arg(name));
    }
}

int main(int argc, char* argv[])
{
    QCoreApplication app(argc, argv);

    LOG("=== TaskCenter Tests ===");
    LOG("");

    lsc::LscDatabase::instance().initialize();
    lsc::TaskCenter::instance().clearAll();

    const QString title = "persistent task " + QString::number(QDateTime::currentMSecsSinceEpoch());
    const QString taskId = lsc::TaskCenter::instance().createTask(
        lsc::TaskType::Analysis,
        title,
        "verify database-backed task history",
        {{"videoPath", "unit-test.mp4"}});

    lsc::TaskCenter::instance().startTask(taskId);
    lsc::TaskCenter::instance().updateProgress(taskId, 42, "halfway");
    lsc::TaskCenter::instance().completeTask(taskId);

    bool foundPersistedTask = false;
    const auto tasks = lsc::LscDatabase::instance().recentTasks(100);
    for (const auto& task : tasks) {
        if (task.id == taskId) {
            foundPersistedTask = task.type == "analysis"
                && task.status == "completed"
                && task.title == title
                && task.progress == 100
                && task.metadata.value("videoPath").toString() == "unit-test.mp4"
                && task.startedAt.isValid()
                && task.finishedAt.isValid();
            break;
        }
    }

    runTest("task lifecycle persists to database history", foundPersistedTask);

    const QString interruptedTitle =
        "interrupted task " + QString::number(QDateTime::currentMSecsSinceEpoch());
    const QString interruptedTaskId = lsc::TaskCenter::instance().createTask(
        lsc::TaskType::Recording,
        interruptedTitle,
        "simulate app restart while running",
        {{"path", "interrupted.mp4"}});
    lsc::TaskCenter::instance().startTask(interruptedTaskId);
    lsc::TaskCenter::instance().clearAll();
    lsc::TaskCenter::instance().recoverInterruptedTasks("unit-test restart");

    bool recoveredAsInterrupted = false;
    const auto recoveredTasks = lsc::LscDatabase::instance().recentTasks(100);
    for (const auto& task : recoveredTasks) {
        if (task.id == interruptedTaskId) {
            recoveredAsInterrupted = task.status == "failed"
                && task.error.contains("unit-test restart")
                && task.finishedAt.isValid();
            break;
        }
    }
    runTest("running persisted task is marked interrupted on recovery",
            recoveredAsInterrupted);

    LOG("");
    LOG(QString("=== Results: %1/%2 passed, %3 failed ===")
        .arg(g_passCount).arg(g_testCount).arg(g_failCount));
    return g_failCount > 0 ? 1 : 0;
}
