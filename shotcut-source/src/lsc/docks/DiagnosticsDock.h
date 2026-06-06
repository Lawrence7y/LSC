// shotcut-source/src/lsc/docks/DiagnosticsDock.h
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

class DiagnosticsDock : public QDockWidget
{
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
