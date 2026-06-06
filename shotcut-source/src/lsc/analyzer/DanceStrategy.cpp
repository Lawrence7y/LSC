#include "DanceStrategy.h"

#include <QFileInfo>
#include <algorithm>

namespace {
double averagePoseMetric(const QVector<PoseWindow>& windows,
                         double startSec,
                         double endSec,
                         double PoseWindow::*member)
{
    double total = 0.0;
    int count = 0;
    for (const PoseWindow& window : windows) {
        if (window.endSec < startSec || window.startSec > endSec) {
            continue;
        }
        total += window.*member;
        ++count;
    }
    return count > 0 ? total / count : 0.0;
}
}

DanceStrategy::DanceStrategy(QObject* parent)
    : IHighlightStrategy(parent)
    , m_beatDetector(new BeatDetector(this))
    , m_videoAnalyzer(new VideoAnalyzer(this))
    , m_poseAnalyzer(new PoseAnalyzer(this))
{
    connect(m_beatDetector, &BeatDetector::finished, this, &DanceStrategy::onBeatFinished);
    connect(m_videoAnalyzer, &VideoAnalyzer::finished, this, &DanceStrategy::onVideoFinished);
    connect(m_poseAnalyzer, &PoseAnalyzer::finished, this, &DanceStrategy::onPoseFinished);

    connect(m_beatDetector, &BeatDetector::errorOccurred, this, &DanceStrategy::errorOccurred);
    connect(m_videoAnalyzer, &VideoAnalyzer::errorOccurred, this, &DanceStrategy::errorOccurred);
    connect(m_poseAnalyzer, &PoseAnalyzer::errorOccurred, this, &DanceStrategy::errorOccurred);
}

QString DanceStrategy::name() const
{
    return QStringLiteral("dance");
}

QString DanceStrategy::description() const
{
    return QStringLiteral("Beat and motion aligned dance highlight detection");
}

void DanceStrategy::analyze(const QString& videoPath)
{
    const QFileInfo fileInfo(videoPath);
    if (!fileInfo.exists()) {
        emit errorOccurred(QStringLiteral("File not found: %1").arg(videoPath));
        return;
    }

    m_beats.clear();
    m_sceneChanges.clear();
    m_motionSegments.clear();
    m_poseWindows.clear();
    m_segments.clear();
    m_completed = 0;
    m_duration = 0.0;
    m_result = HighlightResult{{}, QStringLiteral("dance"), {}};

    m_beatDetector->detect(videoPath);
    m_videoAnalyzer->analyze(videoPath);
    m_poseAnalyzer->analyze(videoPath);
}

void DanceStrategy::cancel()
{
    m_beatDetector->cancel();
    m_videoAnalyzer->cancel();
    m_poseAnalyzer->cancel();
}

bool DanceStrategy::isRunning() const
{
    return m_beatDetector->isRunning()
        || m_videoAnalyzer->isRunning()
        || m_poseAnalyzer->isRunning();
}

HighlightResult DanceStrategy::result() const
{
    return m_result;
}

void DanceStrategy::configure(const QJsonObject& params)
{
    if (params.contains(QStringLiteral("sensitivity"))) {
        m_sensitivity = qBound(0.1, params.value(QStringLiteral("sensitivity")).toDouble(), 1.0);
    }
    if (params.contains(QStringLiteral("minBeats"))) {
        m_minBeats = qMax(2, params.value(QStringLiteral("minBeats")).toInt());
    }
}

void DanceStrategy::onBeatFinished()
{
    m_beats = m_beatDetector->beats();
    m_bpm = m_beatDetector->bpm();
    ++m_completed;
    emit progressChanged(33);
    if (m_completed >= m_expected) {
        computeCorrelation();
    }
}

void DanceStrategy::onVideoFinished()
{
    m_sceneChanges = m_videoAnalyzer->sceneChanges();
    m_motionSegments = m_videoAnalyzer->motionSegments();
    if (!m_motionSegments.isEmpty()) {
        m_duration = m_motionSegments.last().endSec;
    } else if (!m_sceneChanges.isEmpty()) {
        m_duration = m_sceneChanges.last().timestampSec;
    }
    ++m_completed;
    emit progressChanged(66);
    if (m_completed >= m_expected) {
        computeCorrelation();
    }
}

void DanceStrategy::onPoseFinished()
{
    m_poseWindows = m_poseAnalyzer->windows();
    if (!m_poseWindows.isEmpty()) {
        m_duration = qMax(m_duration, m_poseWindows.last().endSec);
    } else if (m_duration > 0.0) {
        m_poseWindows.append({0.0, m_duration, 0.75, 0.70, 0.50});
    }
    ++m_completed;
    if (m_completed >= m_expected) {
        computeCorrelation();
    }
}

void DanceStrategy::computeCorrelation()
{
    if (m_beats.isEmpty() || (m_sceneChanges.isEmpty() && m_motionSegments.isEmpty())) {
        if (m_poseWindows.isEmpty() && m_duration > 0.0) {
            m_poseWindows.append({0.0, m_duration, 0.75, 0.70, 0.50});
        }
        m_result.metadata["warning"] = QStringLiteral("Insufficient data for correlation");
        m_result.metadata["poseWindowCount"] = static_cast<int>(m_poseWindows.size());
        m_result.segments = m_segments;
        emit finished();
        return;
    }

    const double beatWindowSec = qMax(0.18, 60.0 / qMax(60.0, m_bpm) * 0.5);

    struct DanceMoment {
        double timeSec = 0.0;
        double motionSum = 0.0;
        double sceneSum = 0.0;
        int beatCount = 0;
    };

    QVector<DanceMoment> moments;
    double currentStart = m_beats.first().timestampSec;
    DanceMoment currentMoment{currentStart, 0.0, 0.0, 0};

    for (const BeatInfo& beat : std::as_const(m_beats)) {
        if (beat.timestampSec - currentStart > m_minBeats * 60.0 / qMax(60.0, m_bpm)) {
            if (currentMoment.beatCount >= m_minBeats) {
                moments.append(currentMoment);
            }
            currentStart = beat.timestampSec;
            currentMoment = {currentStart, 0.0, 0.0, 0};
        }

        double localMotion = 0.0;
        int motionHits = 0;
        for (const MotionSegment& motion : std::as_const(m_motionSegments)) {
            if (motion.endSec < beat.timestampSec - beatWindowSec
                || motion.startSec > beat.timestampSec + beatWindowSec) {
                continue;
            }
            localMotion += motion.motionLevel;
            ++motionHits;
        }

        double localScene = 0.0;
        int sceneHits = 0;
        for (const SceneChange& change : std::as_const(m_sceneChanges)) {
            if (qAbs(change.timestampSec - beat.timestampSec) > beatWindowSec) {
                continue;
            }
            localScene += change.score;
            ++sceneHits;
        }

        if (motionHits > 0) {
            currentMoment.motionSum += localMotion / motionHits;
        }
        if (sceneHits > 0) {
            currentMoment.sceneSum += localScene / sceneHits;
        }
        ++currentMoment.beatCount;
    }

    if (currentMoment.beatCount >= m_minBeats) {
        moments.append(currentMoment);
    }

    std::sort(moments.begin(), moments.end(), [](const DanceMoment& a, const DanceMoment& b) {
        const double aScore = (a.motionSum * 0.75 + a.sceneSum * 0.25) / qMax(1, a.beatCount);
        const double bScore = (b.motionSum * 0.75 + b.sceneSum * 0.25) / qMax(1, b.beatCount);
        return aScore > bScore;
    });

    const double scoreThreshold = 0.40 - (m_sensitivity * 0.20);
    const int topN = qMin(moments.size(), 5);
    for (int i = 0; i < topN; ++i) {
        const DanceMoment& moment = moments[i];
        const double segmentDuration = m_minBeats * 60.0 / qMax(60.0, m_bpm);
        const double segmentStart = moment.timeSec;
        const double segmentEnd = segmentStart + segmentDuration;

        const double avgMotion = moment.beatCount > 0 ? moment.motionSum / moment.beatCount : 0.0;
        const double beatAlignment = moment.beatCount >= m_minBeats
            ? qBound(0.0, moment.beatCount / (m_minBeats * 2.0), 1.0)
            : 0.0;

        DanceFeatures features;
        features.beatAlignment = beatAlignment;
        features.motionStrength = avgMotion;
        features.poseConfidence = averagePoseMetric(
            m_poseWindows, segmentStart, segmentEnd, &PoseWindow::poseConfidence);
        features.subjectCoverage = averagePoseMetric(
            m_poseWindows, segmentStart, segmentEnd, &PoseWindow::subjectCoverage);

        const double score = m_scorer.score(features);
        if (score < scoreThreshold) {
            continue;
        }

        HighlightSegment segment;
        segment.startSec = segmentStart;
        segment.endSec = segmentEnd;
        segment.score = score;
        segment.audioScore = qBound(0.0, 0.55 + (m_sensitivity * 0.25), 1.0);
        segment.videoScore = qMax(avgMotion, features.subjectCoverage);
        segment.speechScore = 0.0;
        segment.reason = QStringLiteral("舞蹈片段: beat=%1 motion=%2 pose=%3 cover=%4")
                             .arg(features.beatAlignment, 0, 'f', 2)
                             .arg(features.motionStrength, 0, 'f', 2)
                             .arg(features.poseConfidence, 0, 'f', 2)
                             .arg(features.subjectCoverage, 0, 'f', 2);
        m_segments.append(segment);
        emit segmentFound(segment);
    }

    m_result.segments = m_segments;
    m_result.metadata["bpm"] = m_bpm;
    m_result.metadata["totalBeats"] = static_cast<int>(m_beats.size());
    m_result.metadata["highlights"] = static_cast<int>(m_segments.size());
    m_result.metadata["poseWindowCount"] = static_cast<int>(m_poseWindows.size());
    m_result.metadata["sensitivity"] = m_sensitivity;

    emit progressChanged(100);
    emit finished();
}
