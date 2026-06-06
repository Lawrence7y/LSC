#include "DialogStrategy.h"
#include "LscConfig.h"

#include <QFileInfo>
#include <QJsonArray>
#include <algorithm>

DialogStrategy::DialogStrategy(QObject* parent)
    : IHighlightStrategy(parent)
    , m_recognizer(new SpeechRecognizer(this))
    , m_audioAnalyzer(new AudioAnalyzer(this))
{
    connect(m_recognizer, &SpeechRecognizer::finished, this, &DialogStrategy::onSpeechFinished);
    connect(m_recognizer, &SpeechRecognizer::errorOccurred, this, &DialogStrategy::onSpeechError);
    connect(m_audioAnalyzer, &AudioAnalyzer::finished, this, &DialogStrategy::onSilenceFinished);
    connect(m_audioAnalyzer, &AudioAnalyzer::errorOccurred, this, &DialogStrategy::onSilenceError);
}

QString DialogStrategy::name() const
{
    return "dialog";
}

QString DialogStrategy::description() const
{
    return QString::fromUtf8("对话切片：结合字幕、停顿和伪说话人切换边界生成片段。");
}

void DialogStrategy::analyze(const QString& videoPath)
{
    const QFileInfo fi(videoPath);
    if (!fi.exists()) {
        emit errorOccurred("File not found: " + videoPath);
        return;
    }

    m_subtitles.clear();
    m_silences.clear();
    m_segments.clear();
    m_completed = 0;
    m_speechError = false;
    m_silenceError = false;
    m_result = HighlightResult{{}, "dialog", {}};

    m_recognizer->setLanguage(lsc::LscConfig::instance().whisperDefaultLanguage);
    m_recognizer->transcribe(videoPath);
    m_audioAnalyzer->analyze(videoPath);

    emit progressChanged(10);
}

void DialogStrategy::cancel()
{
    m_recognizer->cancel();
    m_audioAnalyzer->cancel();
}

bool DialogStrategy::isRunning() const
{
    return m_recognizer->isRunning() || m_audioAnalyzer->isRunning();
}

HighlightResult DialogStrategy::result() const
{
    return m_result;
}

void DialogStrategy::configure(const QJsonObject& params)
{
    if (params.contains("keywords")) {
        m_keywords.clear();
        for (const QJsonValue& value : params["keywords"].toArray()) {
            m_keywords.append(value.toString());
        }
    }
    if (params.contains("minSegmentSec")) {
        m_minSegmentSec = params["minSegmentSec"].toDouble();
    }
    if (params.contains("maxSegmentSec")) {
        m_maxSegmentSec = params["maxSegmentSec"].toDouble();
    }
}

void DialogStrategy::onSpeechFinished()
{
    m_subtitles = m_recognizer->subtitles();
    ++m_completed;
    emit progressChanged(60);
    if (m_completed >= m_expected) {
        computeDialogSegments();
    }
}

void DialogStrategy::onSpeechError(const QString& error)
{
    m_speechError = true;
    ++m_completed;
    emit progressChanged(60);

    // Speech recognition is critical for dialog strategy
    // If it fails, we should report the error but still try to provide partial results
    if (m_completed >= m_expected) {
        computeDialogSegments();
    }
}

void DialogStrategy::onSilenceFinished()
{
    m_silences = m_audioAnalyzer->segments();
    ++m_completed;
    emit progressChanged(40);
    if (m_completed >= m_expected) {
        computeDialogSegments();
    }
}

void DialogStrategy::onSilenceError(const QString& error)
{
    m_silenceError = true;
    ++m_completed;
    emit progressChanged(40);

    // Silence detection is secondary - we can still work without it
    if (m_completed >= m_expected) {
        computeDialogSegments();
    }
}

void DialogStrategy::computeDialogSegments()
{
    // If speech recognition failed completely, report error and return
    if (m_speechError && m_subtitles.isEmpty()) {
        m_result.metadata["warning"] = "Speech recognition failed";
        m_result.metadata["speechError"] = true;
        emit errorOccurred("Speech recognition failed - cannot generate dialog segments");
        emit finished();
        return;
    }

    if (m_subtitles.isEmpty()) {
        m_result.metadata["warning"] = "No speech detected";
        if (m_silenceError) {
            m_result.metadata["silenceDetectionError"] = true;
        }
        emit finished();
        return;
    }

    struct SilenceWindow {
        double startSec = 0.0;
        double endSec = 0.0;
    };

    QVector<SilenceWindow> silenceWindows;
    for (int i = 0; i + 1 < m_silences.size(); ++i) {
        const double gapStart = m_silences[i].endSec;
        const double gapEnd = m_silences[i + 1].startSec;
        if (gapEnd - gapStart >= 0.25) {
            silenceWindows.append({gapStart, gapEnd});
        }
    }

    auto overlapsVoice = [this](double startSec, double endSec) {
        for (const AudioSegment& audio : m_silences) {
            if (audio.endSec > startSec && audio.startSec < endSec) {
                return true;
            }
        }
        return false;
    };

    auto crossesSilenceBoundary = [&silenceWindows](double startSec, double endSec) {
        for (const SilenceWindow& silence : silenceWindows) {
            if (silence.startSec >= startSec && silence.endSec <= endSec) {
                return true;
            }
            if (silence.endSec > startSec && silence.startSec < endSec
                && (silence.endSec - silence.startSec) >= 0.25) {
                return true;
            }
        }
        return false;
    };

    auto looksLikeSpeakerTurn = [this](int previousIndex, int currentIndex) {
        if (previousIndex < 0 || currentIndex < 0
            || previousIndex >= m_subtitles.size()
            || currentIndex >= m_subtitles.size()) {
            return false;
        }

        const SubtitleEntry& previous = m_subtitles[previousIndex];
        const SubtitleEntry& current = m_subtitles[currentIndex];
        const double gap = current.startSec - previous.endSec;
        if (gap >= 1.0) {
            return true;
        }

        const bool previousStrongEnding =
            previous.text.endsWith(QStringLiteral("？"))
            || previous.text.endsWith(QStringLiteral("?"))
            || previous.text.endsWith(QStringLiteral("！"))
            || previous.text.endsWith(QStringLiteral("!"))
            || previous.text.endsWith(QStringLiteral("。"));
        const bool currentQuoted =
            current.text.startsWith(QStringLiteral("“"))
            || current.text.startsWith(QStringLiteral("\""))
            || current.text.startsWith(QStringLiteral("-"));
        return previousStrongEnding && (gap > 0.35 || currentQuoted);
    };

    QVector<HighlightSegment> rawSegments;
    HighlightSegment current{};
    current.startSec = m_subtitles.first().startSec;
    current.endSec = m_subtitles.first().endSec;
    QStringList utterances{m_subtitles.first().text};
    int estimatedSpeakerTurns = 0;

    for (int i = 1; i < m_subtitles.size(); ++i) {
        const SubtitleEntry& previous = m_subtitles[i - 1];
        const SubtitleEntry& currentSubtitle = m_subtitles[i];
        const double gap = currentSubtitle.startSec - previous.endSec;
        const bool audioBoundary = crossesSilenceBoundary(previous.endSec, currentSubtitle.startSec);
        const bool speakerBoundary = looksLikeSpeakerTurn(i - 1, i);
        const bool shouldSplit = gap > m_silenceGapSec
            || audioBoundary
            || speakerBoundary
            || (!overlapsVoice(previous.endSec, currentSubtitle.startSec) && gap > 0.35)
            || (currentSubtitle.endSec - current.startSec) > m_maxSegmentSec;

        if (shouldSplit) {
            if (speakerBoundary) {
                ++estimatedSpeakerTurns;
            }

            const double length = current.endSec - current.startSec;
            if (length >= m_minSegmentSec && length <= m_maxSegmentSec) {
                current.reason = utterances.join(' ').left(120);
                current.speechScore = 1.0;
                current.audioScore = audioBoundary
                    ? 0.85
                    : (overlapsVoice(current.startSec, current.endSec) ? 0.65 : 0.30);
                current.videoScore = 0.0;
                current.score = qBound(
                    0.0,
                    0.30 + current.audioScore * 0.35 + length / m_maxSegmentSec * 0.35,
                    1.0);
                rawSegments.append(current);
            }

            current = HighlightSegment{};
            current.startSec = currentSubtitle.startSec;
            current.endSec = currentSubtitle.endSec;
            utterances = {currentSubtitle.text};
        } else {
            current.endSec = currentSubtitle.endSec;
            utterances.append(currentSubtitle.text);
        }
    }

    const double length = current.endSec - current.startSec;
    if (length >= m_minSegmentSec && length <= m_maxSegmentSec) {
        current.reason = utterances.join(' ').left(120);
        current.speechScore = 1.0;
        current.audioScore = overlapsVoice(current.startSec, current.endSec) ? 0.65 : 0.30;
        current.videoScore = 0.0;
        current.score = qBound(
            0.0,
            0.30 + current.audioScore * 0.35 + length / m_maxSegmentSec * 0.35,
            1.0);
        rawSegments.append(current);
    }

    for (HighlightSegment& segment : rawSegments) {
        for (const QString& keyword : m_keywords) {
            if (segment.reason.contains(keyword, Qt::CaseInsensitive)) {
                segment.score = qMin(1.0, segment.score + 0.3);
                segment.keywords.append(keyword);
            }
        }
    }

    std::sort(rawSegments.begin(), rawSegments.end(), [](const HighlightSegment& a, const HighlightSegment& b) {
        return a.score > b.score;
    });

    const int count = qMin(rawSegments.size(), 20);
    m_segments = rawSegments.mid(0, count);
    std::sort(m_segments.begin(), m_segments.end(), [](const HighlightSegment& a, const HighlightSegment& b) {
        return a.startSec < b.startSec;
    });

    for (const HighlightSegment& segment : m_segments) {
        emit segmentFound(segment);
    }

    m_result.segments = m_segments;
    m_result.metadata["totalUtterances"] = static_cast<int>(m_subtitles.size());
    m_result.metadata["segments"] = static_cast<int>(m_segments.size());
    m_result.metadata["usedAudioBoundaries"] = !silenceWindows.isEmpty();
    m_result.metadata["diarization"] = QStringLiteral("pseudo_turn_detection");
    m_result.metadata["speakerTurnsEstimated"] = estimatedSpeakerTurns;
    if (!m_segments.isEmpty()) {
        m_result.metadata["analyzedDuration"] = m_segments.last().endSec;
    }

    emit progressChanged(100);
    emit finished();
}
