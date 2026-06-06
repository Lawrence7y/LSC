#include "CommentaryStrategy.h"
#include "LscConfig.h"

#include <QJsonArray>
#include <QJsonObject>

namespace {
QStringList defaultGeneralKeywords()
{
    return {
        QStringLiteral("谢谢"),
        QStringLiteral("感谢"),
        QStringLiteral("兄弟"),
        QStringLiteral("老铁"),
        QStringLiteral("点赞"),
        QStringLiteral("关注"),
        QStringLiteral("礼物"),
        QStringLiteral("上车"),
        QStringLiteral("牛"),
        QStringLiteral("666"),
    };
}

QStringList defaultInteractionKeywords()
{
    QStringList keywords = defaultGeneralKeywords();
    // Append Valorant-specific hotwords from config for the pilot.
    // General streaming keywords remain available for non-Valorant content.
    const QStringList valorantWords = lsc::LscConfig::instance().valorantHotwords;
    for (const QString& word : valorantWords) {
        if (!keywords.contains(word)) {
            keywords.append(word);
        }
    }
    // Additional high-excitement commentary words.
    const QStringList excitementWords = {
        QStringLiteral("这波"),
        QStringLiteral("拿下"),
        QStringLiteral("翻盘"),
        QStringLiteral("赛点"),
        QStringLiteral("漂亮"),
        QStringLiteral("太帅了"),
        QStringLiteral("离谱"),
        QStringLiteral("绝杀"),
    };
    for (const QString& word : excitementWords) {
        if (!keywords.contains(word)) {
            keywords.append(word);
        }
    }
    return keywords;
}
}

CommentaryStrategy::CommentaryStrategy(QObject* parent)
    : IHighlightStrategy(parent)
    , m_recognizer(new SpeechRecognizer(this))
{
    connect(m_recognizer, &SpeechRecognizer::finished,
            this, &CommentaryStrategy::onTranscriptionFinished);
    connect(m_recognizer, &SpeechRecognizer::errorOccurred,
            this, &CommentaryStrategy::onTranscriptionError);
}

QString CommentaryStrategy::name() const
{
    return QStringLiteral("commentary");
}

QString CommentaryStrategy::description() const
{
    return QStringLiteral("解说/互动热点切片策略");
}

void CommentaryStrategy::analyze(const QString& videoPath)
{
    if (m_running) {
        return;
    }

    m_subtitles.clear();
    m_segments.clear();
    m_running = true;

    m_recognizer->setLanguage(lsc::LscConfig::instance().whisperDefaultLanguage);
    m_recognizer->transcribe(videoPath);
}

void CommentaryStrategy::cancel()
{
    m_recognizer->cancel();
    m_running = false;
}

bool CommentaryStrategy::isRunning() const
{
    return m_running;
}

HighlightResult CommentaryStrategy::result() const
{
    HighlightResult result;
    result.segments = m_segments;
    result.strategyName = name();

    QJsonObject metadata;
    metadata["subtitleCount"] = m_subtitles.size();
    metadata["segmentCount"] = m_segments.size();
    int interactionHotspots = 0;
    for (const HighlightSegment& segment : std::as_const(m_segments)) {
        if (segment.reason.contains(QStringLiteral("互动热点"))) {
            ++interactionHotspots;
        }
    }
    metadata["interactionHotspots"] = interactionHotspots;
    result.metadata = metadata;

    return result;
}

void CommentaryStrategy::configure(const QJsonObject& params)
{
    if (params.contains(QStringLiteral("sensitivity"))) {
        m_sensitivity = params.value(QStringLiteral("sensitivity")).toDouble(0.5);
        m_segmenter.setPauseThreshold(qMax(0.5, 2.5 - m_sensitivity));
    }

    if (params.contains(QStringLiteral("keywords"))) {
        m_keywords.clear();
        const QJsonArray keywords = params.value(QStringLiteral("keywords")).toArray();
        for (const auto& keyword : keywords) {
            m_keywords.append(keyword.toString());
        }
    }
}

void CommentaryStrategy::onTranscriptionFinished()
{
    m_subtitles = m_recognizer->subtitles();
    buildSegmentsFromSubtitles();
    m_running = false;
    emit finished();
}

void CommentaryStrategy::onTranscriptionError(const QString& error)
{
    m_running = false;
    emit errorOccurred(error);
}

void CommentaryStrategy::buildSegmentsFromSubtitles()
{
    if (m_subtitles.isEmpty()) {
        return;
    }

    const QVector<CommentarySegment> rawSegments =
        m_segmenter.buildSegments(m_subtitles, m_keywords);
    const double minScore = qBound(0.0, m_sensitivity * 0.35, 1.0);

    const QStringList interactionKeywords = defaultInteractionKeywords();

    for (const CommentarySegment& rawSegment : rawSegments) {
        if (rawSegment.score < minScore) {
            continue;
        }

        HighlightSegment segment;
        segment.startSec = rawSegment.startSec;
        segment.endSec = rawSegment.endSec;
        segment.score = rawSegment.score;
        segment.audioScore = 0.5;
        segment.videoScore = 0.0;
        segment.speechScore = 1.0;
        const QString previewText = rawSegment.texts.join(' ').left(100);
        bool interactionHotspot = false;
        QStringList combinedKeywords = rawSegment.keywords;
        for (const QString& keyword : interactionKeywords) {
            if (previewText.contains(keyword, Qt::CaseInsensitive)) {
                interactionHotspot = true;
                if (!combinedKeywords.contains(keyword)) {
                    combinedKeywords.append(keyword);
                }
            }
        }
        segment.reason = interactionHotspot
            ? QStringLiteral("互动热点: %1").arg(previewText)
            : previewText;
        if (interactionHotspot) {
            segment.score = qMin(1.0, segment.score + 0.15);
        }
        segment.keywords = combinedKeywords;
        m_segments.append(segment);
        emit segmentFound(segment);
    }
}
