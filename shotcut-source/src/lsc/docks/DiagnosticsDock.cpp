// shotcut-source/src/lsc/docks/DiagnosticsDock.cpp
#include "DiagnosticsDock.h"
#include "LscConfig.h"
#include "LscLog.h"
#include "core/LscDatabase.h"
#include "core/ErrorManager.h"
#include "core/TaskCenter.h"

#include <QApplication>
#include <QDateTime>
#include <QDir>
#include <QFileDialog>
#include <QHBoxLayout>
#include <QJsonObject>
#include <QJsonDocument>
#include <QProcess>
#include <QStorageInfo>
#include <QSysInfo>
#include <QVBoxLayout>
#include <QStandardPaths>

#ifdef Q_OS_WIN
#include <windows.h>
#endif

namespace lsc {

namespace {

QString taskTypeName(TaskType type)
{
    switch (type) {
    case TaskType::Recording: return QStringLiteral("Recording");
    case TaskType::Analysis: return QStringLiteral("Analysis");
    case TaskType::ASR: return QStringLiteral("ASR");
    case TaskType::Export: return QStringLiteral("Export");
    case TaskType::Import: return QStringLiteral("Import");
    }
    return QStringLiteral("Unknown");
}

QString taskStateName(TaskState state)
{
    switch (state) {
    case TaskState::Queued: return QStringLiteral("Queued");
    case TaskState::Running: return QStringLiteral("Running");
    case TaskState::Paused: return QStringLiteral("Paused");
    case TaskState::Completed: return QStringLiteral("Completed");
    case TaskState::Failed: return QStringLiteral("Failed");
    case TaskState::Cancelling: return QStringLiteral("Cancelling");
    case TaskState::Cancelled: return QStringLiteral("Cancelled");
    }
    return QStringLiteral("Unknown");
}

} // namespace

DiagnosticsDock::DiagnosticsDock(QWidget* parent)
    : QDockWidget(QString::fromUtf8("诊断面板"), parent)
{
    setupUi();
    onRefreshClicked();
}

void DiagnosticsDock::setupUi()
{
    auto* central = new QWidget;
    auto* mainLayout = new QVBoxLayout(central);

    m_infoText = new QTextEdit;
    m_infoText->setReadOnly(true);
    m_infoText->setFontFamily("Consolas");
    mainLayout->addWidget(m_infoText);

    auto* btnLayout = new QHBoxLayout;
    m_refreshBtn = new QPushButton(QString::fromUtf8("刷新"));
    m_exportBtn = new QPushButton(QString::fromUtf8("导出诊断包"));
    m_clearBtn = new QPushButton(QString::fromUtf8("清除日志"));
    btnLayout->addWidget(m_refreshBtn);
    btnLayout->addWidget(m_exportBtn);
    btnLayout->addWidget(m_clearBtn);
    mainLayout->addLayout(btnLayout);

    connect(m_refreshBtn, &QPushButton::clicked, this, &DiagnosticsDock::onRefreshClicked);
    connect(m_exportBtn, &QPushButton::clicked, this, &DiagnosticsDock::onExportClicked);
    connect(m_clearBtn, &QPushButton::clicked, this, &DiagnosticsDock::onClearLogsClicked);

    setWidget(central);
}

DiagnosticInfo DiagnosticsDock::collectDiagnostics() const
{
    DiagnosticInfo info;
    info.appVersion = QApplication::applicationVersion();
    if (info.appVersion.isEmpty())
        info.appVersion = "2.1";

    info.osInfo = QSysInfo::prettyProductName();
    info.qtVersion = qVersion();

    auto& cfg = LscConfig::instance();
    QProcess ffmpeg;
    ffmpeg.start(cfg.ffmpegProgram(), {"-version"});
    if (ffmpeg.waitForFinished(3000)) {
        QString output = QString::fromUtf8(ffmpeg.readAllStandardOutput());
        info.ffmpegVersion = output.split('\n', Qt::SkipEmptyParts).value(0, "unknown");
    } else {
        info.ffmpegVersion = "not found";
    }

    info.whisperVersion = cfg.whisperDefaultModel;

#ifdef Q_OS_WIN
    MEMORYSTATUSEX memStatus;
    memStatus.dwLength = sizeof(memStatus);
    if (GlobalMemoryStatusEx(&memStatus)) {
        info.totalMemoryBytes = static_cast<qint64>(memStatus.ullTotalPhys);
    } else {
        info.totalMemoryBytes = 0;
    }
#else
    info.totalMemoryBytes = 0;
#endif

    QStorageInfo storage = QStorageInfo::root();
    info.diskFreeBytes = storage.bytesAvailable();

    info.gpuInfo = "N/A";

    auto& errMgr = ErrorManager::instance();
    auto errors = errMgr.recentErrors(20);
    for (const auto& e : errors)
        info.recentErrors.append(QString("[%1] %2: %3")
            .arg(e.timestamp.toString(Qt::ISODate))
            .arg(e.code, e.message));

    const auto tasks = TaskCenter::instance().allTasks();
    const int limit = qMin(10, tasks.size());
    for (int i = 0; i < limit; ++i) {
        const auto& t = tasks[i];
        QVariantMap task;
        task["id"] = t.id;
        task["name"] = t.title;
        task["type"] = taskTypeName(t.type);
        task["state"] = taskStateName(t.state);
        task["progress"] = t.progress;
        task["path"] = t.metadata.value(QStringLiteral("path"));
        info.recentTasks.append(task);
    }

    QVariantMap configMap;
    configMap["silenceThresholdDb"] = cfg.silenceThresholdDb;
    configMap["sceneChangeThreshold"] = cfg.sceneChangeThreshold;
    configMap["highlightThreshold"] = cfg.highlightThreshold;
    configMap["whisperModel"] = cfg.whisperDefaultModel;
    configMap["whisperLanguage"] = cfg.whisperDefaultLanguage;
    configMap["defaultFormat"] = cfg.defaultFormat;
    configMap["defaultUseStreamCopy"] = cfg.defaultUseStreamCopy;
    info.currentConfig = configMap;

    return info;
}

QString DiagnosticsDock::formatDiagnostics(const DiagnosticInfo& info) const
{
    QString text;
    text += "=== LSC Diagnostics Report ===\n";
    text += QString("Generated: %1\n").arg(QDateTime::currentDateTime().toString(Qt::ISODate));
    text += "\n";

    text += "--- System ---\n";
    text += QString("App Version: %1\n").arg(info.appVersion);
    text += QString("OS: %1\n").arg(info.osInfo);
    text += QString("Qt: %1\n").arg(info.qtVersion);
    text += QString("FFmpeg: %1\n").arg(info.ffmpegVersion);
    text += QString("Whisper Model: %1\n").arg(info.whisperVersion);
    text += QString("GPU: %1\n").arg(info.gpuInfo);
    text += QString("Disk Free: %1 GB\n").arg(info.diskFreeBytes / (1024.0 * 1024.0 * 1024.0), 0, 'f', 2);
    text += QString("Total Memory: %1 GB\n").arg(info.totalMemoryBytes / (1024.0 * 1024.0 * 1024.0), 0, 'f', 2);
    text += "\n";

    text += "--- Configuration ---\n";
    for (auto it = info.currentConfig.constBegin(); it != info.currentConfig.constEnd(); ++it)
        text += QString("  %1: %2\n").arg(it.key()).arg(it.value().toString());
    text += "\n";

    text += QString("--- Recent Errors (%1) ---\n").arg(info.recentErrors.size());
    if (info.recentErrors.isEmpty()) {
        text += "  (none)\n";
    } else {
        for (const auto& err : info.recentErrors)
            text += QString("  %1\n").arg(err);
    }
    text += "\n";

    text += QString("--- Recent Tasks (%1) ---\n").arg(info.recentTasks.size());
    if (info.recentTasks.isEmpty()) {
        text += "  (none)\n";
    } else {
        for (const auto& task : info.recentTasks)
            text += QString("  [%1] %2 %3 %4%% - %5 (%6)\n")
                        .arg(task["id"].toString())
                        .arg(task["type"].toString())
                        .arg(task["state"].toString())
                        .arg(task["progress"].toInt())
                        .arg(task["name"].toString())
                        .arg(task["path"].toString());
    }

    return text;
}

void DiagnosticsDock::exportDiagnosticPackage(const QString& outputPath) const
{
    DiagnosticInfo info = collectDiagnostics();
    QString report = formatDiagnostics(info);

    QDir dir(outputPath);
    dir.mkpath(".");

    QFile reportFile(dir.filePath("diagnostics.txt"));
    if (reportFile.open(QIODevice::WriteOnly | QIODevice::Text)) {
        reportFile.write(report.toUtf8());
        reportFile.close();
    }

    QFile configFile(dir.filePath("config.json"));
    if (configFile.open(QIODevice::WriteOnly | QIODevice::Text)) {
        QJsonObject configObj = QJsonObject::fromVariantMap(info.currentConfig);
        QJsonDocument doc(configObj);
        configFile.write(doc.toJson(QJsonDocument::Indented));
        configFile.close();
    }
}

void DiagnosticsDock::onRefreshClicked()
{
    DiagnosticInfo info = collectDiagnostics();
    m_infoText->setText(formatDiagnostics(info));
}

void DiagnosticsDock::onExportClicked()
{
    const QString dir = QFileDialog::getExistingDirectory(
        this, QString::fromUtf8("选择导出目录"));
    if (!dir.isEmpty()) {
        QString packageDir = QDir(dir).filePath(
            QString("lsc_diagnostics_%1")
                .arg(QDateTime::currentDateTime().toString("yyyyMMdd_HHmmss")));
        exportDiagnosticPackage(packageDir);
        m_infoText->append(QString::fromUtf8("\n[已导出到: %1]").arg(packageDir));
    }
}

void DiagnosticsDock::onClearLogsClicked()
{
    ErrorManager::instance().clearErrors();
    m_infoText->append(QString::fromUtf8("\n[日志已清除]"));
    onRefreshClicked();
}

} // namespace lsc
