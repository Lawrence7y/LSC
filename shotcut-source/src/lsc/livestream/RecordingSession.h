#ifndef RECORDINGSESSION_H
#define RECORDINGSESSION_H

#include "StreamCapture.h"
#include "PlatformParser.h"
#include "analyzer/IHighlightStrategy.h"
#include "analyzer/AnalysisProfile.h"
#include "analyzer/MaterialClassifier.h"
#include <QObject>
#include <QJsonObject>
#include <QMutex>
#include <QTimer>

class HighlightEngine;
class RealtimeStrategy;

class RecordingSession : public QObject
{
    Q_OBJECT
public:
    explicit RecordingSession(QObject* parent = nullptr);
    ~RecordingSession();

    void startRecording(const QString& url, const RecordingConfig& config);
    void stopRecording();

    HighlightEngine* highlightEngine() const { return m_engine; }
    void setHighlightEngine(HighlightEngine* engine);

    void setAnalysisProfile(const AnalysisProfile& profile);
    AnalysisProfile analysisProfile() const { return m_analysisProfile; }
    bool previewEnabled() const { return m_analysisProfile.enableRealtimePreview; }

    PlatformInfo platformInfo() const { return m_platformInfo; }
    RecordingStatus status() const;
    QString outputPath() const;
    qint64 duration() const;
    qint64 fileSize() const;
    bool isRecording() const;
    double lastAnalysisTime() const { return m_lastAnalysisTime; }

signals:
    void recordingStarted(const QString& outputPath);
    void recordingStopped(const QString& outputPath, qint64 fileSizeBytes);
    void statusChanged(RecordingStatus status);
    void progressUpdated(qint64 durationMs, qint64 fileSizeBytes);
    void errorOccurred(const QString& error);
    void platformParsed(const PlatformInfo& info);
    void reconnecting(int attempt, int maxAttempts);
    void highlightFound(const HighlightSegment& segment);
    void clipExported(const QString& filePath, const QString& title);
    void previewSourceChanged(const QString& sourcePath);
    void previewStopped();

private slots:
    void onPlatformParsed(const PlatformInfo& info);
    void onPlatformError(const QString& error);
    void onCaptureError(const QString& error);
    void onCaptureNeedsReconnect(const QString& lastUrl);
    void onCaptureStatusChanged(RecordingStatus status);
    void onCaptureProgress(qint64 durationMs, qint64 fileSizeBytes);
    void onRealtimeAnalysisTimer();
    void onEngineFinished();

private:
    void saveMetadata();
    void updateMetadata(qint64 durationMs, qint64 fileSizeBytes);
    void startRealtimeAnalysis();
    void stopRealtimeAnalysis();

    QString m_sourceUrl;
    PlatformParser* m_parser;
    StreamCapture* m_capture;
    HighlightEngine* m_engine;
    PlatformInfo m_platformInfo;
    RecordingConfig m_config;
    int m_reconnectCount;

    // Real-time analysis - protected by m_analysisMutex
    mutable QMutex m_analysisMutex;
    QTimer m_analysisTimer;
    double m_lastAnalysisTime = 0.0;
    bool m_analysisRunning = false;
    bool m_stopRequested = false;
    bool m_recordingStarted = false;
    bool m_analysisQueued = false;
    bool m_finalizeAnalysisPending = false;
    qint64 m_lastAnalysisFileSize = 0;
    bool m_previewActive = false;
    qint64 m_recordingStartTimeSecs = 0;
    AnalysisProfile m_analysisProfile{AnalysisProfile::generic()};

    // Valorant pilot: realtime strategy and material signal accumulation
    RealtimeStrategy* m_realtimeStrategy = nullptr;
    MaterialSignals m_materialSignals;
    bool m_realtimeAnalysisRunning = false;
    double m_lastRealtimeHighlightEndSec = 0.0;

public:
    MaterialSignals materialSignals() const { return m_materialSignals; }
};

#endif
