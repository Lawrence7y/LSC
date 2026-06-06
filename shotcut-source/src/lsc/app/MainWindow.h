#ifndef MAINWINDOW_H
#define MAINWINDOW_H

#include <QMainWindow>
#include "docks/LivestreamDock.h"
#include "docks/AnalysisDock.h"
#include "docks/PlayerDock.h"
#include "docks/TaskDock.h"
#include "docks/HistoryDock.h"
#include "docks/FeedbackStatsDock.h"
#include "docks/DiagnosticsDock.h"
#include "docks/SettingsDock.h"
#include "core/WorkflowOrchestrator.h"

class MainWindow : public QMainWindow
{
    Q_OBJECT
public:
    explicit MainWindow(QWidget* parent = nullptr);
    ~MainWindow();

private slots:
    void onWorkflowStateChanged(lsc::WorkflowState newState, lsc::WorkflowState oldState);
    void onWorkflowError(const QString& error);
    void onWorkflowCompleted();

private:
    LivestreamDock* m_livestreamDock;
    AnalysisDock* m_analysisDock;
    PlayerDock* m_playerDock;
    lsc::TaskDock* m_taskDock;
    lsc::HistoryDock* m_historyDock;
    FeedbackStatsDock* m_feedbackStatsDock;
    lsc::DiagnosticsDock* m_diagnosticsDock;
    lsc::SettingsDock* m_settingsDock;
    QString m_recordingTaskId;
    QString m_analysisTaskId;
};

#endif // MAINWINDOW_H
