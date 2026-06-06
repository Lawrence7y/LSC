#include "CommentarySegmenter.h"

#include <algorithm>

QVector<CommentarySegment> CommentarySegmenter::buildSegments(
    const QVector<SubtitleEntry>& subtitles,
    const QStringList& keywords) const
{
    QVector<CommentarySegment> segments;

    if (subtitles.isEmpty()) {
        return segments;
    }

    CommentarySegment currentSegment;
    currentSegment.startSec = subtitles.first().startSec;

    for (int i = 0; i < subtitles.size(); ++i) {
        const SubtitleEntry& entry = subtitles.at(i);
        const bool hasKeyword = containsKeyword(entry.text, keywords);
        const bool isLongPause = (i > 0) &&
                                  (entry.startSec - subtitles.at(i - 1).endSec > m_pauseThreshold);

        if (isLongPause || hasKeyword) {
            // End current segment
            currentSegment.endSec = subtitles.at(i - 1).endSec;
            const double duration = currentSegment.endSec - currentSegment.startSec;
            if (!currentSegment.texts.isEmpty() && duration >= m_minSegmentDuration) {
                currentSegment.score = scoreSegment(currentSegment, keywords);
                segments.append(currentSegment);
            }

            // Start new segment
            currentSegment = CommentarySegment();
            currentSegment.startSec = entry.startSec;
        }

        currentSegment.texts.append(entry.text);
        if (hasKeyword) {
            currentSegment.keywords.append(entry.text);
        }
    }

    // Add last segment
    currentSegment.endSec = subtitles.last().endSec;
    const double duration = currentSegment.endSec - currentSegment.startSec;
    if (!currentSegment.texts.isEmpty() && duration >= m_minSegmentDuration) {
        currentSegment.score = scoreSegment(currentSegment, keywords);
        segments.append(currentSegment);
    }

    return segments;
}

bool CommentarySegmenter::containsKeyword(const QString& text, const QStringList& keywords) const
{
    for (const QString& keyword : keywords) {
        if (text.contains(keyword, Qt::CaseInsensitive)) {
            return true;
        }
    }
    return false;
}

double CommentarySegmenter::scoreSegment(const CommentarySegment& segment,
                                          const QStringList& keywords) const
{
    double score = 0.0;

    // Base score from duration (prefer 10-60 second segments)
    const double duration = segment.endSec - segment.startSec;
    if (duration >= 10.0 && duration <= 60.0) {
        score += 0.3;
    } else if (duration >= 5.0 && duration <= 120.0) {
        score += 0.15;
    }

    // Score from keyword density
    if (!segment.keywords.isEmpty()) {
        const double keywordDensity = static_cast<double>(segment.keywords.size()) /
                                       segment.texts.size();
        score += keywordDensity * 0.4;
    }

    // Score from text density (more text = more content)
    const double textDensity = qMin(1.0, segment.texts.size() / 10.0);
    score += textDensity * 0.3;

    return qBound(0.0, score, 1.0);
}
