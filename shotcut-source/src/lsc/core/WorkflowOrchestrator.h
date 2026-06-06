#ifndef WORKFLOWORCHESTRATOR_H
#define WORKFLOWORCHESTRATOR_H

#include <QObject>
#include <QString>
#include <QVariantMap>

namespace lsc {

enum class WorkflowState {
    Idle,
    ParsingUrl,
    Recording,
    StoppingRecording,
    AutoAnalyzing,
    ReviewingHighlights,
    Exporting,
    Completed,
    Error
};

class WorkflowOrchestrator : public QObject {
    Q_OBJECT

public:
    static WorkflowOrchestrator& instance();

    void startWorkflow(const QString& url);
    void stopRecording();
    void startAnalysis(const QString& videoPath = {});
    void reviewHighlights();
    void exportSelected();
    void cancelWorkflow();
    void reset();

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
    WorkflowOrchestrator(const WorkflowOrchestrator&) = delete;
    WorkflowOrchestrator& operator=(const WorkflowOrchestrator&) = delete;

    void setState(WorkflowState newState);
    void handleError(const QString& error);

    WorkflowState m_state = WorkflowState::Idle;
    QString m_currentUrl;
    QString m_currentVideoPath;
    QString m_lastError;
};

} // namespace lsc

Q_DECLARE_METATYPE(lsc::WorkflowState)

#endif // WORKFLOWORCHESTRATOR_H
