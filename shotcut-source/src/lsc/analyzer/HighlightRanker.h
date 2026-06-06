// shotcut-source/src/lsc/analyzer/HighlightRanker.h
#ifndef HIGHLIGHTRANKER_H
#define HIGHLIGHTRANKER_H

#include "IHighlightStrategy.h"
#include "RankedClip.h"
#include "ValorantProfileConfig.h"

#include <QVector>

class HighlightRanker
{
public:
    QVector<RankedClip> rankCandidates(const QVector<HighlightSegment>& candidates,
                                       const ValorantProfileConfig& profile,
                                       const QString& materialType) const;

private:
    // FIXME(Phase 2): replace with real signal-based extraction per spec Section 10.2
    void computeFeatures(RankedClip& clip, const HighlightSegment& input) const;
    QVector<RankedClip> deduplicateByOverlap(QVector<RankedClip>& ranked) const;
};

#endif // HIGHLIGHTRANKER_H
