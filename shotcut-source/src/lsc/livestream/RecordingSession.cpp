#include "RecordingSession.h"
#include "core/LscDatabase.h"
#include "analyzer/HighlightEngine.h"
#include "analyzer/RealtimeStrategy.h"

#include <QDateTime>
#include <QFile>
#include <QFileInfo>
#include <QJsonDocument>
#include <QJsonArray>
#include <QTimer>
#include <QUuid>

namespace {
QString resolveRequestedQuality(const PlatformInfo& info, const QString& requestedQuality)
{
    const QString key = requestedQuality.trimmed().toLower();
    if (!key.isEmpty() && key != "best" && info.availableStreams.contains(key)) {
        return key;
    }
    if (!info.preferredQuality.isEmpty() && info.availableStreams.contains(info.preferredQuality)) {
        return info.preferredQuality;
    }
    if (!info.availableQualities.isEmpty()) {
        return info.availableQualities.first();
    }
    return key;
}

QString resolveStreamUrl(const PlatformInfo& info, const QString& requestedQuality)
{
    const QString quality = resolveRequestedQuality(info, requestedQuality);
    if (!quality.isEmpty() && info.availableStreams.contains(quality)) {
        return info.availableStreams.value(quality);
    }
    return info.streamUrl;
}

QString resolvePreviewSource(const PlatformInfo& info, const RecordingConfig& config)
{
    if (!info.streamUrl.isEmpty()) {
        return info.streamUrl;
    }
    return config.outputPath;
}

void persistRecordedProject(const QString& sourceUrl,
                            const PlatformInfo& platformInfo,
                            const RecordingConfig& config,
                            const QString& analysisProfileId,
                            qint64 durationMs,
                            qint64 fileSizeBytes)
{
    auto& db = lsc::LscDatabase::instance();
    if (!db.isOpen() && !db.initialize()) {
        return;
    }

    const QFileInfo recordedFile(config.outputPath);
    lsc::ProjectRecord project;
    project.id = QUuid::createUuid().toString(QUuid::WithoutBraces);
    project.name = platformInfo.title.isEmpty() ? recordedFile.completeBaseName() : platformInfo.title;
    project.platform = platformInfo.platform;
    project.streamerName = platformInfo.streamerName;
    project.sourceUrl = sourceUrl;
    project.videoPath = config.outputPath;
    project.recordedAt = QDateTime::currentDateTime();
    project.durationSec = qMax<qint64>(1, durationMs / 1000);
    project.fileSizeBytes = fileSizeBytes;
    project.analysisProfile = analysisProfileId;
    project.status = "recorded";
    project.metadata.insert("roomId", platformInfo.roomId);
    project.metadata.insert("selectedQuality", platformInfo.preferredQuality);
    project.metadata.insert("backupStreamUrl", platformInfo.backupStreamUrl);
    project.metadata.insert("outputFormat", recordedFile.suffix());

    db.insertProject(project);
}
}

RecordingSession::RecordingSession(QObject* parent)
    : QObject(parent)
    , m_parser(new PlatformParser(this))
    , m_capture(new StreamCapture(this))
    , m_engine(nullptr)
    , m_realtimeStrategy(new RealtimeStrategy(this))
    , m_reconnectCount(0)
{
    connect(m_parser, &PlatformParser::parseComplete, this, &RecordingSession::onPlatformParsed);
    connect(m_parser, &PlatformParser::parseError, this, &RecordingSession::onPlatformError);
    connect(m_capture, &StreamCapture::errorOccurred, this, &RecordingSession::onCaptureError);
    connect(m_capture, &StreamCapture::needsReconnect, this, &RecordingSession::onCaptureNeedsReconnect);
    connect(m_capture, &StreamCapture::statusChanged, this, &RecordingSession::onCaptureStatusChanged);
    connect(m_capture, &StreamCapture::progressUpdated, this, &RecordingSession::onCaptureProgress);

    // Realtime strategy: segments are forwarded as highlightFound for live cards.
    connect(m_realtimeStrategy, &RealtimeStrategy::segmentFound,
            this, [this](const HighlightSegment& segment) {
                // Realtime scans re-read the growing recording file. Forward only new ranges.
                if (segment.endSec <= m_lastRealtimeHighlightEndSec + 0.5) {
                    return;
                }
                if (segment.startSec < m_lastRealtimeHighlightEndSec - 0.5) {
                    return;
                }
                m_lastRealtimeHighlightEndSec = qMax(m_lastRealtimeHighlightEndSec, segment.endSec);
                emit highlightFound(segment);
            });
    // When realtime scan finishes, accumulate MaterialSignals and reset running flag.
    connect(m_realtimeStrategy, &RealtimeStrategy::finished, this, [this]() {
        m_materialSignals.voicePresence = qMax(m_materialSignals.voicePresence,
                                               m_realtimeStrategy->voicePresence());
        m_materialSignals.combatDensity = qMax(m_materialSignals.combatDensity,
                                               m_realtimeStrategy->combatDensity());
        m_materialSignals.burstReactionRate = qMax(m_materialSignals.burstReactionRate,
                                                    m_realtimeStrategy->burstReactionRate());
        m_realtimeAnalysisRunning = false;
    });

    m_analysisTimer.setInterval(10000);
    connect(&m_analysisTimer, &QTimer::timeout, this, &RecordingSession::onRealtimeAnalysisTimer);
}

RecordingSession::~RecordingSession()
{
    stopRecording();
}

void RecordingSession::setHighlightEngine(HighlightEngine* engine)
{
    if (m_engine) {
        disconnect(m_engine, nullptr, this, nullptr);
    }

    m_engine = engine;
    if (!m_engine) {
        return;
    }

    connect(m_engine, &HighlightEngine::segmentFound, this, &RecordingSession::highlightFound);
    connect(m_engine, &HighlightEngine::clipExported, this, &RecordingSession::clipExported);
    connect(m_engine, &HighlightEngine::finished, this, &RecordingSession::onEngineFinished);
    m_engine->setAnalysisProfile(m_analysisProfile);
}

void RecordingSession::setAnalysisProfile(const AnalysisProfile& profile)
{
    m_analysisProfile = profile;
    if (m_engine) {
        m_engine->setAnalysisProfile(m_analysisProfile);
    }
}

void RecordingSession::startRecording(const QString& url, const RecordingConfig& config)
{
    if (isRecording()) {
        stopRecording();
    }

    m_sourceUrl = url;
    m_config = config;
    m_platformInfo = PlatformInfo();
    m_reconnectCount = 0;
    m_lastAnalysisTime = 0.0;
    m_analysisRunning = false;
    m_stopRequested = false;
    m_recordingStarted = false;
    m_analysisQueued = false;
    m_finalizeAnalysisPending = false;
    m_lastAnalysisFileSize = 0;
    m_previewActive = false;
    m_materialSignals = MaterialSignals{};
    m_realtimeAnalysisRunning = false;
    m_lastRealtimeHighlightEndSec = 0.0;
    m_recordingStartTimeSecs = 0;

    m_parser->parseUrl(url);
}

void RecordingSession::stopRecording()
{
    QMutexLocker locker(&m_analysisMutex);
    if (m_stopRequested && status() == RecordingStatus::Stopped) {
        return;
    }

    m_stopRequested = true;
    locker.unlock();

    stopRealtimeAnalysis();

    const bool hadOutputTarget = !m_config.outputPath.isEmpty();
    const qint64 finalDurationMs = m_capture->duration();
    m_capture->stop();

    if (!hadOutputTarget || !m_recordingStarted) {
        return;
    }

    const qint64 finalFileSizeBytes = fileSize();
    updateMetadata(finalDurationMs, finalFileSizeBytes);
    persistRecordedProject(m_sourceUrl, m_platformInfo, m_config, m_analysisProfile.id,
                           finalDurationMs, finalFileSizeBytes);
    emit recordingStopped(m_config.outputPath, finalFileSizeBytes);

    const QFileInfo recordedFile(m_config.outputPath);
    if (m_engine && recordedFile.exists() && recordedFile.size() > 0) {
        QMutexLocker analysisLocker(&m_analysisMutex);
        if (m_engine->isRunning()) {
            m_finalizeAnalysisPending = true;
            m_analysisQueued = true;
            return;
        }

        m_analysisQueued = m_engine->analyze(m_config.outputPath);
        m_analysisRunning = m_analysisQueued;
    }
}

void RecordingSession::onPlatformParsed(const PlatformInfo& info)
{
    m_platformInfo = info;
    const QString selectedQuality = resolveRequestedQuality(info, m_config.sourceQuality);
    const QString selectedStreamUrl = resolveStreamUrl(info, m_config.sourceQuality);
    if (!selectedQuality.isEmpty()) {
        m_platformInfo.preferredQuality = selectedQuality;
    }
    if (!selectedStreamUrl.isEmpty()) {
        m_platformInfo.streamUrl = selectedStreamUrl;
    }
    emit platformParsed(m_platformInfo);

    if (!info.isValid) {
        emit errorOccurred(QString::fromUtf8("Platform parse failed: %1").arg(info.errorMsg));
        return;
    }

    if (m_recordingStartTimeSecs <= 0) {
        m_recordingStartTimeSecs = QDateTime::currentDateTime().toSecsSinceEpoch();
    }
    saveMetadata();

    if (!m_capture->start(m_platformInfo.streamUrl, m_config)) {
        emit errorOccurred("Failed to start recording");
        return;
    }
}

void RecordingSession::onPlatformError(const QString& error)
{
    emit errorOccurred(error);
}

void RecordingSession::onCaptureError(const QString& error)
{
    emit errorOccurred(error);
}

void RecordingSession::onCaptureNeedsReconnect(const QString& /*lastUrl*/)
{
    if (m_stopRequested) {
        return;
    }

    if (m_reconnectCount >= m_config.reconnectRetries) {
        stopRealtimeAnalysis();
        emit errorOccurred(QString::fromUtf8("Reconnect failed after %1 attempts")
                               .arg(m_config.reconnectRetries));
        return;
    }

    ++m_reconnectCount;
    emit reconnecting(m_reconnectCount, m_config.reconnectRetries);

    // Reset analysis file size so incremental analysis doesn't skip new content
    m_lastAnalysisFileSize = 0;

    const int delay = qMin(m_config.reconnectDelayMs * (1 << (m_reconnectCount - 1)),
                           m_config.maxReconnectDelayMs);
    QTimer::singleShot(delay, this, [this]() {
        if (!m_stopRequested) {
            m_parser->parseUrl(m_sourceUrl);
        }
    });
}

void RecordingSession::onCaptureStatusChanged(RecordingStatus status)
{
    emit statusChanged(status);

    if (status == RecordingStatus::Recording) {
        const bool firstStart = !m_recordingStarted;
        m_recordingStarted = true;
        if (firstStart) {
            m_reconnectCount = 0;
            emit recordingStarted(m_config.outputPath);
        }
        startRealtimeAnalysis();
        const QString previewSource = resolvePreviewSource(m_platformInfo, m_config);
        if (previewEnabled() && !previewSource.isEmpty()) {
            emit previewSourceChanged(previewSource);
            m_previewActive = true;
        }
    } else if (status == RecordingStatus::Reconnecting) {
        stopRealtimeAnalysis();
    } else if (status == RecordingStatus::Stopped || status == RecordingStatus::Error) {
        stopRealtimeAnalysis();
        m_previewActive = false;
        emit previewStopped();
    }
}

void RecordingSession::onCaptureProgress(qint64 durationMs, qint64 fileSizeBytes)
{
    const QString previewSource = resolvePreviewSource(m_platformInfo, m_config);
    if (previewEnabled() && !m_previewActive && !previewSource.isEmpty()) {
        emit previewSourceChanged(previewSource);
        m_previewActive = true;
    }
    emit progressUpdated(durationMs, fileSizeBytes);
}

void RecordingSession::startRealtimeAnalysis()
{
    if (!m_engine) {
        return;
    }
    m_lastAnalysisFileSize = 0;
    m_analysisTimer.start();
}

void RecordingSession::stopRealtimeAnalysis()
{
    m_analysisTimer.stop();
    if (m_realtimeStrategy && m_realtimeStrategy->isRunning()) {
        m_realtimeStrategy->cancel();
    }
    m_realtimeAnalysisRunning = false;
    m_analysisRunning = false;
}

void RecordingSession::onRealtimeAnalysisTimer()
{
    QMutexLocker locker(&m_analysisMutex);
    if (m_stopRequested || m_realtimeAnalysisRunning) {
        return;
    }
    locker.unlock();

    const QString currentOutputPath = m_config.outputPath;
    const QFileInfo outputInfo(currentOutputPath);
    if (currentOutputPath.isEmpty() || !outputInfo.exists() || outputInfo.size() <= 0) {
        return;
    }
    if (outputInfo.size() == m_lastAnalysisFileSize) {
        return;
    }

    const double currentDurationSec = m_capture->duration() / 1000.0;
    if (currentDurationSec < 8.0) {
        return;
    }

    locker.relock();
    if (m_realtimeAnalysisRunning || m_stopRequested) {
        return;
    }

    m_realtimeAnalysisRunning = true;
    m_lastAnalysisFileSize = outputInfo.size();
    m_realtimeStrategy->analyze(currentOutputPath);
}

void RecordingSession::onEngineFinished()
{
    QMutexLocker locker(&m_analysisMutex);
    m_analysisRunning = false;
    m_analysisQueued = false;
    m_lastAnalysisTime = m_capture->duration() / 1000.0;

    if (!m_finalizeAnalysisPending || !m_engine) {
        return;
    }

    m_finalizeAnalysisPending = false;
    locker.unlock();

    const QFileInfo recordedFile(m_config.outputPath);
    if (!recordedFile.exists() || recordedFile.size() <= 0) {
        return;
    }

    locker.relock();
    m_analysisQueued = m_engine->analyze(m_config.outputPath);
    m_analysisRunning = m_analysisQueued;
}

RecordingStatus RecordingSession::status() const
{
    return m_capture->status();
}

QString RecordingSession::outputPath() const
{
    return m_config.outputPath;
}

qint64 RecordingSession::duration() const
{
    return m_capture->duration();
}

qint64 RecordingSession::fileSize() const
{
    return m_capture->fileSize();
}

bool RecordingSession::isRecording() const
{
    const auto s = status();
    return s == RecordingStatus::Recording
        || s == RecordingStatus::Starting
        || s == RecordingStatus::Reconnecting;
}

void RecordingSession::saveMetadata()
{
    QJsonObject meta;
    meta["platform"] = m_platformInfo.platform;
    meta["roomId"] = m_platformInfo.roomId;
    meta["title"] = m_platformInfo.title;
    meta["streamerName"] = m_platformInfo.streamerName;
    meta["streamUrl"] = m_platformInfo.streamUrl;
    meta["backupStreamUrl"] = m_platformInfo.backupStreamUrl;
    meta["selectedQuality"] = m_platformInfo.preferredQuality;
    meta["sourceUrl"] = m_sourceUrl;
    meta["startTime"] = m_recordingStartTimeSecs > 0
        ? m_recordingStartTimeSecs
        : QDateTime::currentDateTime().toSecsSinceEpoch();
    QJsonArray qualities;
    for (const QString& quality : m_platformInfo.availableQualities) {
        qualities.append(quality);
    }
    meta["availableQualities"] = qualities;

    QFile file(m_config.outputPath + ".json");
    if (file.open(QIODevice::WriteOnly)) {
        file.write(QJsonDocument(meta).toJson(QJsonDocument::Indented));
        file.close();
    }
}

void RecordingSession::updateMetadata(qint64 durationMs, qint64 fileSizeBytes)
{
    QFile file(m_config.outputPath + ".json");
    QJsonObject meta;
    if (file.open(QIODevice::ReadOnly)) {
        meta = QJsonDocument::fromJson(file.readAll()).object();
        file.close();
    } else {
        meta["platform"] = m_platformInfo.platform;
        meta["roomId"] = m_platformInfo.roomId;
        meta["title"] = m_platformInfo.title;
        meta["streamerName"] = m_platformInfo.streamerName;
        meta["streamUrl"] = m_platformInfo.streamUrl;
        meta["backupStreamUrl"] = m_platformInfo.backupStreamUrl;
        meta["selectedQuality"] = m_platformInfo.preferredQuality;
        meta["sourceUrl"] = m_sourceUrl;
        const qint64 stopTime = QDateTime::currentDateTime().toSecsSinceEpoch();
        meta["startTime"] = m_recordingStartTimeSecs > 0
            ? m_recordingStartTimeSecs
            : qMax<qint64>(0, stopTime - durationMs / 1000);
        QJsonArray qualities;
        for (const QString& quality : m_platformInfo.availableQualities) {
            qualities.append(quality);
        }
        meta["availableQualities"] = qualities;
    }
    meta["stopTime"] = QDateTime::currentDateTime().toSecsSinceEpoch();
    meta["durationMs"] = durationMs;
    meta["fileSizeBytes"] = fileSizeBytes;

    if (file.open(QIODevice::WriteOnly | QIODevice::Truncate)) {
        file.write(QJsonDocument(meta).toJson(QJsonDocument::Indented));
        file.close();
    }
}
