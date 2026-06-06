#include "GenericStrategy.h"
#include "LscConfig.h"

#include <QFileInfo>

GenericStrategy::GenericStrategy(QObject* parent)
    : IHighlightStrategy(parent)
    , m_audioAnalyzer(new AudioAnalyzer(this))
    , m_videoAnalyzer(new VideoAnalyzer(this))
{
    connect(m_audioAnalyzer, &AudioAnalyzer::finished, this, [this]() {
        ++m_completed;
        if (m_completed >= 2) {
            computeHighlights();
        }
    });
    connect(m_videoAnalyzer, &VideoAnalyzer::finished, this, [this]() {
        ++m_completed;
        if (m_completed >= 2) {
            computeHighlights();
        }
    });
}

QString GenericStrategy::name() const
{
    return "generic";
}

QString GenericStrategy::description() const
{
    return QString::fromUtf8("通用高光检测：结合音频能量与画面变化进行综合评分。");
}

void GenericStrategy::analyze(const QString& videoPath)
{
    const QFileInfo fi(videoPath);
    if (!fi.exists()) {
        emit errorOccurred("File not found: " + videoPath);
        return;
    }

    m_segments.clear();
    m_completed = 0;
    m_result = HighlightResult{{}, "generic", {}};

    m_audioAnalyzer->analyze(videoPath);
    m_videoAnalyzer->analyze(videoPath);
}

void GenericStrategy::cancel()
{
    m_audioAnalyzer->cancel();
    m_videoAnalyzer->cancel();
}

bool GenericStrategy::isRunning() const
{
    return m_audioAnalyzer->isRunning() || m_videoAnalyzer->isRunning();
}

HighlightResult GenericStrategy::result() const
{
    return m_result;
}

void GenericStrategy::configure(const QJsonObject& params)
{
    if (params.contains("threshold")) {
        m_threshold = params["threshold"].toDouble();
    }
}

void GenericStrategy::computeHighlights()
{
    const auto audioSegments = m_audioAnalyzer->segments();
    const auto sceneChanges = m_videoAnalyzer->sceneChanges();
    const auto motionSegments = m_videoAnalyzer->motionSegments();
    const auto& cfg = lsc::LscConfig::instance();

    double duration = 0.0;
    if (!audioSegments.isEmpty()) {
        duration = qMax(duration, audioSegments.last().endSec);
    }
    if (!motionSegments.isEmpty()) {
        duration = qMax(duration, motionSegments.last().endSec);
    }
    if (!sceneChanges.isEmpty()) {
        duration = qMax(duration, sceneChanges.last().timestampSec);
    }

    if (duration <= 0.0) {
        m_result.metadata["segments"] = 0;
        emit finished();
        return;
    }

    const double audioRange = qMax(1.0, cfg.audioScoreCeilDb - cfg.audioScoreFloorDb);

    for (double startSec = 0.0; startSec < duration; startSec += cfg.highlightStepSec) {
        const double endSec = qMin(startSec + cfg.highlightWindowSec, duration);

        double audioSum = 0.0;
        int audioCount = 0;
        for (const AudioSegment& segment : audioSegments) {
            if (segment.endSec > startSec && segment.startSec < endSec) {
                const double score = segment.energy > 0.0
                    ? qBound(0.0, segment.energy, 1.0)
                    : qBound(0.0, (segment.rmsDb - cfg.audioScoreFloorDb) / audioRange, 1.0);
                audioSum += score;
                ++audioCount;
            }
        }
        const double audioScore = audioCount > 0 ? audioSum / audioCount : 0.0;

        double videoScore = 0.0;
        const double center = (startSec + endSec) / 2.0;
        for (const SceneChange& scene : sceneChanges) {
            if (qAbs(scene.timestampSec - center) <= cfg.sceneChangeMatchWindowSec) {
                videoScore = qMax(videoScore, scene.score);
            }
        }
        for (const MotionSegment& motion : motionSegments) {
            if (center >= motion.startSec && center <= motion.endSec) {
                videoScore = qMax(
                    videoScore,
                    qBound(0.0, motion.motionLevel / cfg.motionNormalizationBase, 1.0));
            }
        }

        const double combined = audioScore * cfg.weightAudio + videoScore * cfg.weightVideo;
        if (combined < m_threshold) {
            continue;
        }

        HighlightSegment segment{};
        segment.startSec = startSec;
        segment.endSec = endSec;
        segment.score = combined;
        segment.audioScore = audioScore;
        segment.videoScore = videoScore;
        segment.reason = QString("High energy: audio=%1 video=%2")
                             .arg(audioScore, 0, 'f', 2)
                             .arg(videoScore, 0, 'f', 2);
        m_segments.append(segment);
    }

    QVector<HighlightSegment> merged;
    for (const HighlightSegment& segment : m_segments) {
        if (!merged.isEmpty() && segment.startSec - merged.last().endSec < cfg.highlightMergeGapSec) {
            HighlightSegment& last = merged.last();
            last.endSec = segment.endSec;
            last.score = qMax(last.score, segment.score);
            last.audioScore = qMax(last.audioScore, segment.audioScore);
            last.videoScore = qMax(last.videoScore, segment.videoScore);
        } else {
            merged.append(segment);
        }
    }

    m_segments = merged;
    m_result.segments = m_segments;
    m_result.metadata["segments"] = static_cast<int>(m_segments.size());
    m_result.metadata["analyzedDuration"] = duration;
    emit finished();
}
