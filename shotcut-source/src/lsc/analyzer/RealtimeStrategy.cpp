// shotcut-source/src/lsc/analyzer/RealtimeStrategy.cpp
#include "RealtimeStrategy.h"
#include "../LscConfig.h"

#include <QFileInfo>

RealtimeStrategy::RealtimeStrategy(QObject* parent)
    : IHighlightStrategy(parent)
    , m_audioAnalyzer(new AudioAnalyzer(this))
    , m_videoAnalyzer(new VideoAnalyzer(this))
{
    connect(m_audioAnalyzer, &AudioAnalyzer::finished, this, &RealtimeStrategy::onAudioFinished);
    connect(m_videoAnalyzer, &VideoAnalyzer::finished, this, &RealtimeStrategy::onVideoFinished);
    connect(m_audioAnalyzer, &AudioAnalyzer::errorOccurred, this, &RealtimeStrategy::errorOccurred);
    connect(m_videoAnalyzer, &VideoAnalyzer::errorOccurred, this, &RealtimeStrategy::errorOccurred);
}

void RealtimeStrategy::analyze(const QString& videoPath)
{
    m_result = HighlightResult{{}, QStringLiteral("realtime"), {}};
    m_audioSegments.clear();
    m_motionSegments.clear();
    m_sceneChanges.clear();
    m_totalDurationSec = 0.0;
    m_pendingParts = 2;
    m_audioAnalyzer->analyze(videoPath);
    m_videoAnalyzer->analyze(videoPath);
}

bool RealtimeStrategy::isRunning() const
{
    return m_pendingParts > 0;
}

void RealtimeStrategy::cancel()
{
    m_audioAnalyzer->cancel();
    m_videoAnalyzer->cancel();
    m_pendingParts = 0;
}

void RealtimeStrategy::onAudioFinished()
{
    m_audioSegments = m_audioAnalyzer->segments();
    if (!m_audioSegments.isEmpty()) {
        m_totalDurationSec = qMax(m_totalDurationSec, m_audioSegments.last().endSec);
    }
    --m_pendingParts;
    if (m_pendingParts == 0) {
        flushRealtimeSegments();
    }
}

void RealtimeStrategy::onVideoFinished()
{
    m_motionSegments = m_videoAnalyzer->motionSegments();
    m_sceneChanges = m_videoAnalyzer->sceneChanges();
    if (!m_motionSegments.isEmpty()) {
        m_totalDurationSec = qMax(m_totalDurationSec, m_motionSegments.last().endSec);
    }
    if (!m_sceneChanges.isEmpty()) {
        m_totalDurationSec = qMax(m_totalDurationSec, m_sceneChanges.last().timestampSec);
    }
    --m_pendingParts;
    if (m_pendingParts == 0) {
        flushRealtimeSegments();
    }
}

void RealtimeStrategy::flushRealtimeSegments()
{
    if (m_totalDurationSec <= 0.0) {
        m_result = HighlightResult{{}, QStringLiteral("realtime"), {}};
        emit finished();
        return;
    }

    const auto& cfg = lsc::LscConfig::instance();
    const double windowSec = cfg.highlightWindowSec;
    const double stepSec = cfg.highlightStepSec;

    QVector<HighlightSegment> candidates;

    // Simple sliding-window: mark windows where both audio and video activity exist.
    for (double t = 0.0; t < m_totalDurationSec - windowSec; t += stepSec) {
        const double windowEnd = t + windowSec;

        double audioEnergy = 0.0;
        for (const AudioSegment& seg : std::as_const(m_audioSegments)) {
            if (seg.endSec < t || seg.startSec > windowEnd) continue;
            const double overlapStart = qMax(seg.startSec, t);
            const double overlapEnd = qMin(seg.endSec, windowEnd);
            audioEnergy = qMax(audioEnergy, seg.energy * (overlapEnd - overlapStart) / windowSec);
        }

        double motionLevel = 0.0;
        for (const MotionSegment& seg : std::as_const(m_motionSegments)) {
            if (seg.endSec < t || seg.startSec > windowEnd) continue;
            motionLevel = qMax(motionLevel, seg.motionLevel);
        }

        const double score = audioEnergy * 0.55 + motionLevel * 0.45;
        if (score < cfg.highlightMinScore) continue;

        HighlightSegment seg;
        seg.startSec = t;
        seg.endSec = windowEnd;
        seg.score = score;
        seg.audioScore = audioEnergy;
        seg.videoScore = motionLevel;
        seg.speechScore = 0.0;
        seg.reason = QStringLiteral("实时高光: audio=%1 motion=%2")
                         .arg(audioEnergy, 0, 'f', 2)
                         .arg(motionLevel, 0, 'f', 2);
        candidates.append(seg);
    }

    QVector<HighlightSegment> segments;
    const double mergeGapSec = cfg.highlightMergeGapSec;
    for (const HighlightSegment& seg : std::as_const(candidates)) {
        if (!segments.isEmpty() && seg.startSec <= segments.last().endSec + mergeGapSec) {
            HighlightSegment& merged = segments.last();
            merged.endSec = qMax(merged.endSec, seg.endSec);
            merged.score = qMax(merged.score, seg.score);
            merged.audioScore = qMax(merged.audioScore, seg.audioScore);
            merged.videoScore = qMax(merged.videoScore, seg.videoScore);
            continue;
        }
        segments.append(seg);
    }

    for (const HighlightSegment& seg : std::as_const(segments)) {
        emit segmentFound(seg);
    }

    m_result.segments = segments;
    m_result.metadata.insert(QStringLiteral("totalDuration"), m_totalDurationSec);
    m_result.metadata.insert(QStringLiteral("segmentCount"), segments.size());
    emit finished();
}

double RealtimeStrategy::voicePresence() const
{
    if (m_totalDurationSec <= 0.0) return 0.0;
    double voicedSec = 0.0;
    for (const AudioSegment& seg : std::as_const(m_audioSegments)) {
        voicedSec += seg.endSec - seg.startSec;
    }
    return qBound(0.0, voicedSec / m_totalDurationSec, 1.0);
}

double RealtimeStrategy::combatDensity() const
{
    if (m_totalDurationSec <= 0.0) return 0.0;
    // Count high-energy audio bursts + significant scene changes per minute.
    int burstCount = 0;
    for (const AudioSegment& seg : std::as_const(m_audioSegments)) {
        if (seg.energy >= 0.5) ++burstCount;
    }
    for (const SceneChange& sc : std::as_const(m_sceneChanges)) {
        if (sc.score >= 0.3) ++burstCount;
    }
    const double perMinute = burstCount / (m_totalDurationSec / 60.0);
    return qBound(0.0, perMinute / 20.0, 1.0);  // 20 events/min → 1.0
}

double RealtimeStrategy::burstReactionRate() const
{
    if (m_audioSegments.size() < 2) return 0.0;
    // Frequency of short (<2s) high-energy audio segments — proxies for reaction bursts.
    int shortBursts = 0;
    for (const AudioSegment& seg : std::as_const(m_audioSegments)) {
        const double dur = seg.endSec - seg.startSec;
        if (dur < 2.0 && seg.energy >= 0.5) ++shortBursts;
    }
    const double perMinute = shortBursts / (m_totalDurationSec / 60.0);
    return qBound(0.0, perMinute / 10.0, 1.0);  // 10 bursts/min → 1.0
}
