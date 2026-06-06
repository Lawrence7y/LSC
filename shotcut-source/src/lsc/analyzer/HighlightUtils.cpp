#include "HighlightUtils.h"

#include <algorithm>

namespace HighlightUtils {

QVector<HighlightSegment> normalizeSegments(const QVector<HighlightSegment>& segments)
{
    if (segments.size() <= 1) {
        return segments;
    }

    QVector<HighlightSegment> normalized = segments;
    std::sort(normalized.begin(), normalized.end(),
              [](const HighlightSegment& a, const HighlightSegment& b) {
                  if (!qFuzzyCompare(a.startSec, b.startSec)) {
                      return a.startSec < b.startSec;
                  }
                  return a.endSec < b.endSec;
              });

    QVector<HighlightSegment> merged;
    for (const HighlightSegment& segment : std::as_const(normalized)) {
        if (merged.isEmpty()) {
            merged.append(segment);
            continue;
        }

        HighlightSegment& last = merged.last();
        if (shouldMergeSegments(last, segment)) {
            mergeSegmentInto(last, segment);
        } else {
            merged.append(segment);
        }
    }

    return merged;
}

QVector<HighlightSegment> deduplicateSegments(const QVector<HighlightSegment>& segments,
                                              double overlapThreshold)
{
    QVector<HighlightSegment> result;

    for (const auto& segment : segments) {
        bool duplicate = false;
        for (auto& existing : result) {
            if (overlapRatio(existing, segment) >= overlapThreshold) {
                // Keep the one with higher score
                if (segment.score > existing.score) {
                    existing = segment;
                }
                duplicate = true;
                break;
            }
        }
        if (!duplicate) {
            result.append(segment);
        }
    }

    return result;
}

} // namespace HighlightUtils
