#include "HighlightDetector.h"
#include "LscConfig.h"

#include <QFileInfo>

HighlightDetector::HighlightDetector(QObject* parent)
    : QObject(parent)
    , m_audioAnalyzer(new AudioAnalyzer(this))
    , m_videoAnalyzer(new VideoAnalyzer(this))
    , m_speechRecognizer(nullptr)
{
    connect(m_audioAnalyzer, &AudioAnalyzer::finished, this, &HighlightDetector::onAudioFinished);
    connect(m_videoAnalyzer, &VideoAnalyzer::finished, this, &HighlightDetector::onVideoFinished);
    connect(m_audioAnalyzer, &AudioAnalyzer::errorOccurred, this, &HighlightDetector::errorOccurred);
    connect(m_videoAnalyzer, &VideoAnalyzer::errorOccurred, this, &HighlightDetector::errorOccurred);
}

void HighlightDetector::setSpeechRecognizer(SpeechRecognizer* sr)
{
    if (m_speechRecognizer == sr) {
        return;
    }

    if (m_speechRecognizer) {
        disconnect(m_speechRecognizer, nullptr, this, nullptr);
    }

    m_speechRecognizer = sr;
    if (m_speechRecognizer) {
        connect(m_speechRecognizer, &SpeechRecognizer::finished, this, &HighlightDetector::onSpeechFinished);
        connect(m_speechRecognizer,
                &SpeechRecognizer::errorOccurred,
                this,
                &HighlightDetector::onSpeechError);
    }
}

void HighlightDetector::setKeywordList(const QStringList& keywords)
{
    m_keywords = keywords;
}

void HighlightDetector::analyze(const QString& videoPath)
{
    if (!QFileInfo::exists(videoPath)) {
        emit errorOccurred(QString("File not found: %1").arg(videoPath));
        return;
    }

    m_highlights.clear();
    m_audioSegments.clear();
    m_sceneChanges.clear();
    m_motionSegments.clear();
    m_subtitles.clear();
    m_duration = 0.0;
    m_completedCount = 0;
    m_expectedCount = m_speechRecognizer ? 3 : 2;

    emit progressChanged(QStringLiteral("audio"), 0);
    emit progressChanged(QStringLiteral("video"), 0);

    m_audioAnalyzer->analyze(videoPath);
    m_videoAnalyzer->analyze(videoPath);

    if (m_speechRecognizer) {
        emit progressChanged(QStringLiteral("speech"), 0);
        m_speechRecognizer->transcribe(videoPath);
    }
}

void HighlightDetector::cancel()
{
    m_audioAnalyzer->cancel();
    m_videoAnalyzer->cancel();
    if (m_speechRecognizer) {
        m_speechRecognizer->cancel();
    }
}

bool HighlightDetector::isRunning() const
{
    return m_audioAnalyzer->isRunning()
        || m_videoAnalyzer->isRunning()
        || (m_speechRecognizer && m_speechRecognizer->isRunning());
}

void HighlightDetector::onAudioFinished()
{
    m_audioSegments = m_audioAnalyzer->segments();
    if (!m_audioSegments.isEmpty()) {
        m_duration = qMax(m_duration, m_audioSegments.last().endSec);
    }

    ++m_completedCount;
    emit progressChanged(QStringLiteral("audio"), 100);
    if (m_completedCount >= m_expectedCount) {
        computeHighlights();
    }
}

void HighlightDetector::onVideoFinished()
{
    m_sceneChanges = m_videoAnalyzer->sceneChanges();
    m_motionSegments = m_videoAnalyzer->motionSegments();
    if (!m_motionSegments.isEmpty()) {
        m_duration = qMax(m_duration, m_motionSegments.last().endSec);
    } else if (!m_sceneChanges.isEmpty()) {
        m_duration = qMax(m_duration, m_sceneChanges.last().timestampSec);
    }

    ++m_completedCount;
    emit progressChanged(QStringLiteral("video"), 100);
    if (m_completedCount >= m_expectedCount) {
        computeHighlights();
    }
}

void HighlightDetector::onSpeechFinished()
{
    if (!m_speechRecognizer) {
        return;
    }

    m_subtitles = m_speechRecognizer->subtitles();
    if (!m_subtitles.isEmpty()) {
        m_duration = qMax(m_duration, m_subtitles.last().endSec);
    }

    ++m_completedCount;
    emit progressChanged(QStringLiteral("speech"), 100);
    if (m_completedCount >= m_expectedCount) {
        computeHighlights();
    }
}

void HighlightDetector::onSpeechError(const QString& error)
{
    Q_UNUSED(error)
    ++m_completedCount;
    emit progressChanged(QStringLiteral("speech"), 100);
    if (m_completedCount >= m_expectedCount) {
        computeHighlights();
    }
}

void HighlightDetector::computeHighlights()
{
    const auto& cfg = lsc::LscConfig::instance();
    m_highlights.clear();

    for (const AudioSegment& audioSegment : std::as_const(m_audioSegments)) {
        HighlightSegment segment;
        segment.startSec = audioSegment.startSec;
        segment.endSec = qMax(audioSegment.endSec, audioSegment.startSec + 1.0);
        segment.audioScore = computeAudioScore(audioSegment);
        segment.videoScore = computeVideoScore((segment.startSec + segment.endSec) / 2.0);
        segment.speechScore = computeSpeechScore(segment.startSec, segment.endSec);
        segment.score = qBound(
            0.0,
            segment.audioScore * cfg.weightAudioDetector
                + segment.videoScore * cfg.weightVideoDetector
                + segment.speechScore * cfg.weightSpeechDetector,
            1.0);

        if (segment.score < cfg.highlightMinScore) {
            continue;
        }

        segment.reason = QString::fromUtf8("音频能量 %.2f, 画面变化 %.2f, 语音信号 %.2f")
                             .arg(segment.audioScore, 0, 'f', 2)
                             .arg(segment.videoScore, 0, 'f', 2)
                             .arg(segment.speechScore, 0, 'f', 2);
        m_highlights.append(segment);
    }

    if (m_highlights.isEmpty() && m_duration > 0.0) {
        HighlightSegment fallback;
        fallback.startSec = 0.0;
        fallback.endSec = qMin(m_duration, cfg.highlightWindowSec);
        fallback.audioScore = 0.3;
        fallback.videoScore = 0.3;
        fallback.speechScore = 0.0;
        fallback.score = cfg.highlightMinScore;
        fallback.reason = QString::fromUtf8("默认片段");
        m_highlights.append(fallback);
    }

    mergeAdjacentHighlights();
    for (const HighlightSegment& segment : std::as_const(m_highlights)) {
        emit highlightFound(segment);
    }
    emit finished();
}

double HighlightDetector::computeAudioScore(const AudioSegment& seg)
{
    const double loudnessRange = qMax(1.0, m_audioAnalyzer->peakDb() - m_audioAnalyzer->overallLoudness());
    const double peakDelta = seg.maxDb - m_audioAnalyzer->overallLoudness();
    return qBound(0.0, peakDelta / loudnessRange, 1.0);
}

double HighlightDetector::computeVideoScore(double timestamp)
{
    double score = 0.0;

    for (const SceneChange& change : std::as_const(m_sceneChanges)) {
        if (qAbs(change.timestampSec - timestamp) <= 2.0) {
            score = qMax(score, qBound(0.0, change.score, 1.0));
        }
    }

    for (const MotionSegment& segment : std::as_const(m_motionSegments)) {
        if (timestamp >= segment.startSec && timestamp <= segment.endSec) {
            score = qMax(score, qBound(0.0, segment.motionLevel / 3.0, 1.0));
        }
    }

    return score;
}

double HighlightDetector::computeSpeechScore(double startSec, double endSec)
{
    if (m_subtitles.isEmpty()) {
        return 0.0;
    }

    double score = 0.0;
    for (const SubtitleEntry& subtitle : std::as_const(m_subtitles)) {
        if (subtitle.endSec < startSec || subtitle.startSec > endSec) {
            continue;
        }

        score = qMax(score, subtitle.confidence > 0.0 ? subtitle.confidence : 0.5);
        for (const QString& keyword : m_keywords) {
            if (subtitle.text.contains(keyword, Qt::CaseInsensitive)) {
                score = qMin(1.0, score + 0.3);
                break;
            }
        }
    }

    return score;
}

void HighlightDetector::mergeAdjacentHighlights()
{
    if (m_highlights.isEmpty()) {
        return;
    }

    const auto& cfg = lsc::LscConfig::instance();

    std::sort(m_highlights.begin(), m_highlights.end(), [](const HighlightSegment& a, const HighlightSegment& b) {
        return a.startSec < b.startSec;
    });

    QVector<HighlightSegment> merged;
    merged.append(m_highlights.first());

    for (int i = 1; i < m_highlights.size(); ++i) {
        HighlightSegment& last = merged.last();
        const HighlightSegment& current = m_highlights.at(i);
        if (current.startSec - last.endSec <= cfg.highlightMergeGapSec) {
            last.endSec = qMax(last.endSec, current.endSec);
            last.score = qMax(last.score, current.score);
            last.audioScore = qMax(last.audioScore, current.audioScore);
            last.videoScore = qMax(last.videoScore, current.videoScore);
            last.speechScore = qMax(last.speechScore, current.speechScore);
            if (last.reason != current.reason && !current.reason.isEmpty()) {
                last.reason += QStringLiteral(" | ") + current.reason;
            }
        } else {
            merged.append(current);
        }
    }

    m_highlights = merged;
}
