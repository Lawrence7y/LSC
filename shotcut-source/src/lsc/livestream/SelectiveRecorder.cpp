#include "SelectiveRecorder.h"
#include "LscLog.h"

#include <QFileInfo>
#include <QDir>
#include <QDateTime>

#define MODULE_NAME "SelectiveRecorder"

SelectiveRecorder::SelectiveRecorder(QObject* parent)
    : QObject(parent)
    , m_capture(new StreamCapture(this))
    , m_detector(new GameplayDetector(this))
{
    connect(m_capture, &StreamCapture::progressUpdated,
            this, &SelectiveRecorder::onCaptureProgress);
    connect(m_capture, &StreamCapture::statusChanged,
            this, &SelectiveRecorder::onCaptureStatusChanged);
    connect(m_capture, &StreamCapture::errorOccurred,
            this, &SelectiveRecorder::onCaptureError);

    connect(m_detector, &GameplayDetector::gameplayStarted,
            this, &SelectiveRecorder::onGameplayStarted);
    connect(m_detector, &GameplayDetector::gameplayEnded,
            this, &SelectiveRecorder::onGameplayEnded);
}

void SelectiveRecorder::startRecording(const QString& streamUrl, const RecordingConfig& config,
                                       const QString& gameKey)
{
    m_streamUrl = streamUrl;
    m_baseConfig = config;
    m_segments.clear();
    m_segmentIndex = 0;
    m_currentSegmentActive = false;
    m_totalRecordedMs = 0;

    // Prepare output directory
    const QFileInfo fi(config.outputPath);
    m_baseOutputDir = fi.absolutePath() + "/segments";
    QDir().mkpath(m_baseOutputDir);
    m_baseFileName = fi.completeBaseName();

    m_detector->setGameKey(gameKey);

    // Start the first segment immediately (assume gameplay at start)
    startNewSegment();

    // Start monitoring game state
    m_detector->startMonitoring(m_baseConfig.outputPath);
}

void SelectiveRecorder::stopRecording()
{
    m_detector->stopMonitoring();
    if (m_currentSegmentActive) {
        endCurrentSegment();
    }
    m_capture->stop();
    LSC_INFO(MODULE_NAME) << "选择性录制完成, 片段数:" << m_segments.size();
    emit allSegmentsReady(m_segments);
}

bool SelectiveRecorder::isRecording() const
{
    return m_capture->status() == RecordingStatus::Recording
        || m_capture->status() == RecordingStatus::Starting;
}

void SelectiveRecorder::onGameplayStarted()
{
    LSC_INFO(MODULE_NAME) << "检测到游戏开始, 开始录制片段";
    emit gameStateChanged(GameState::Gameplay);
    if (!m_currentSegmentActive) {
        startNewSegment();
    }
}

void SelectiveRecorder::onGameplayEnded()
{
    LSC_INFO(MODULE_NAME) << "检测到游戏结束 (买局/等待)";
    emit gameStateChanged(m_detector->currentState());
    if (m_currentSegmentActive) {
        endCurrentSegment();
    }
}

void SelectiveRecorder::onCaptureProgress(qint64 durationMs, qint64 fileSizeBytes)
{
    Q_UNUSED(fileSizeBytes)
    m_totalRecordedMs = durationMs;
}

void SelectiveRecorder::onCaptureStatusChanged(RecordingStatus status)
{
    if (status == RecordingStatus::Stopped || status == RecordingStatus::Error) {
        if (m_currentSegmentActive) {
            endCurrentSegment();
        }
    }
}

void SelectiveRecorder::onCaptureError(const QString& error)
{
    emit errorOccurred(error);
}

void SelectiveRecorder::startNewSegment()
{
    if (m_currentSegmentActive) {
        return;
    }

    const QString segmentPath = generateSegmentPath();
    RecordingConfig segConfig = m_baseConfig;
    segConfig.outputPath = segmentPath;

    m_capture->start(m_streamUrl, segConfig);
    m_currentSegmentActive = true;
    m_currentSegmentPath = segmentPath;
    m_currentSegmentStartMs = m_totalRecordedMs;

    emit segmentStarted(segmentPath);
    LSC_INFO(MODULE_NAME) << "开始片段:" << segmentPath;
}

void SelectiveRecorder::endCurrentSegment()
{
    if (!m_currentSegmentActive) {
        return;
    }

    m_capture->stop();

    RecordedSegment segment;
    segment.filePath = m_currentSegmentPath;
    segment.startSec = m_currentSegmentStartMs / 1000.0;
    segment.endSec = m_totalRecordedMs / 1000.0;
    segment.state = m_detector->currentState();
    m_segments.append(segment);

    m_currentSegmentActive = false;
    m_currentSegmentPath.clear();
    m_segmentIndex++;

    emit segmentEnded(segment.filePath, segment.endSec - segment.startSec);
    LSC_INFO(MODULE_NAME) << "结束片段, 时长:" << (segment.endSec - segment.startSec) << "秒";
}

QString SelectiveRecorder::generateSegmentPath() const
{
    const QString timestamp = QDateTime::currentDateTime().toString("yyyyMMdd_HHmmss");
    return m_baseOutputDir + "/" + m_baseFileName
        + "_seg" + QString::number(m_segmentIndex)
        + "_" + timestamp + ".mp4";
}

#undef MODULE_NAME
