// shotcut-source/src/lsc/analyzer/RoundClipBuilder.cpp
#include "RoundClipBuilder.h"
#include "../LscConfig.h"

#include <QtGlobal>

RankedClip RoundClipBuilder::buildMotherClip(const RankedClip& source,
                                              double totalDurationSec) const
{
    RankedClip out = source;
    const auto& cfg = lsc::LscConfig::instance();
    const double minLen = cfg.motherClipMinSecValorant;
    const double maxLen = cfg.motherClipMaxSecValorant;
    const double currentLen = source.endSec - source.startSec;

    // If the source clip is already within bounds, keep it.
    if (currentLen >= minLen && currentLen <= maxLen) {
        out.metadata.insert(QStringLiteral("clipLevel"), QStringLiteral("mother"));
        out.metadata.insert(QStringLiteral("boundaryStrategy"), QStringLiteral("keep"));
        return out;
    }

    const double desired = qBound(minLen, currentLen, maxLen);
    const double center = (source.startSec + source.endSec) * 0.5;

    // Prefer expanding leftward for late-round clips, rightward for early-round clips.
    // Use roundIndex as a heuristic: later rounds bias leftward expansion.
    const double leftBias = (source.roundIndex > 12) ? 0.6 : 0.4;
    const double availableLeft = center;
    const double availableRight = totalDurationSec - center;
    const double halfDesired = desired * 0.5;

    double expandLeft = qMin(halfDesired, availableLeft);
    double expandRight = qMin(halfDesired, availableRight);

    // If one side is constrained, give the remainder to the other side.
    if (expandLeft < halfDesired) {
        expandRight = qMin(desired - expandLeft, availableRight);
    } else if (expandRight < halfDesired) {
        expandLeft = qMin(desired - expandRight, availableLeft);
    }

    // Apply left bias: shift expansion from one side to the other.
    const double totalExpand = expandLeft + expandRight;
    expandLeft = qMin(totalExpand * leftBias, availableLeft);
    expandRight = qMin(totalExpand * (1.0 - leftBias), availableRight);
    // Redistribute remainder from constrained side.
    if (expandLeft + expandRight < desired) {
        if (expandLeft < totalExpand * leftBias) {
            expandRight = qMin(desired - expandLeft, availableRight);
        } else {
            expandLeft = qMin(desired - expandRight, availableLeft);
        }
    }

    out.startSec = qMax(0.0, center - expandLeft);
    out.endSec = qMin(totalDurationSec, center + expandRight);

    // Ensure minimum length.
    if (out.endSec - out.startSec < minLen) {
        out.endSec = qMin(totalDurationSec, out.startSec + minLen);
    }

    out.metadata.insert(QStringLiteral("clipLevel"), QStringLiteral("mother"));
    out.metadata.insert(QStringLiteral("boundaryStrategy"), QStringLiteral("expand"));
    return out;
}
