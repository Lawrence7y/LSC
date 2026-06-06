#define MODULE_NAME "WorkflowOrchestrator"
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
        LSC_LOG_WARNING << "Workflow already active, ignoring start request";
        return;
    }

    m_currentUrl = url;
    setState(WorkflowState::ParsingUrl);
    emit workflowStarted(url);
}

void WorkflowOrchestrator::stopRecording() {
    if (m_state != WorkflowState::Recording) return;

    setState(WorkflowState::StoppingRecording);
}

void WorkflowOrchestrator::startAnalysis(const QString& videoPath) {
    m_currentVideoPath = videoPath;
    setState(WorkflowState::AutoAnalyzing);
}

void WorkflowOrchestrator::reviewHighlights() {
    if (m_state != WorkflowState::AutoAnalyzing) return;
    setState(WorkflowState::ReviewingHighlights);
    emit requestUiUpdate("show_highlights", {});
}

void WorkflowOrchestrator::exportSelected() {
    if (m_state != WorkflowState::ReviewingHighlights) return;
    setState(WorkflowState::Exporting);
}

void WorkflowOrchestrator::cancelWorkflow() {
    setState(WorkflowState::Idle);
    emit workflowError(tr("User cancelled"));
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
    case WorkflowState::Idle:                return tr("Idle");
    case WorkflowState::ParsingUrl:          return tr("Parsing URL");
    case WorkflowState::Recording:           return tr("Recording");
    case WorkflowState::StoppingRecording:   return tr("Stopping Recording");
    case WorkflowState::AutoAnalyzing:       return tr("Analyzing");
    case WorkflowState::ReviewingHighlights: return tr("Review Highlights");
    case WorkflowState::Exporting:           return tr("Exporting");
    case WorkflowState::Completed:           return tr("Completed");
    case WorkflowState::Error:               return tr("Error");
    default:                                 return tr("Unknown");
    }
}

QString WorkflowOrchestrator::currentUrl() const {
    return m_currentUrl;
}

QString WorkflowOrchestrator::currentVideoPath() const {
    return m_currentVideoPath;
}

bool WorkflowOrchestrator::isActive() const {
    return m_state != WorkflowState::Idle
        && m_state != WorkflowState::Completed
        && m_state != WorkflowState::Error;
}

void WorkflowOrchestrator::onRecordingStarted() {
    setState(WorkflowState::Recording);
}

void WorkflowOrchestrator::onRecordingStopped(const QString& outputPath) {
    m_currentVideoPath = outputPath;
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

    LSC_LOG_INFO << "Workflow state: " << currentStateName();
}

void WorkflowOrchestrator::handleError(const QString& error) {
    m_lastError = error;
    setState(WorkflowState::Error);
    emit workflowError(error);
    LSC_LOG_ERROR << "Workflow error: " << error;
}

} // namespace lsc
#undef MODULE_NAME
