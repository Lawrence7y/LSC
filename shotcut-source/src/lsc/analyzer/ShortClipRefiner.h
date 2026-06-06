// shotcut-source/src/lsc/analyzer/ShortClipRefiner.h
#ifndef SHORTCLIPREFINER_H
#define SHORTCLIPREFINER_H

#include "AudioAnalyzer.h"
#include "IHighlightStrategy.h"
#include "RankedClip.h"
#include "VideoAnalyzer.h"

#include <QVector>

class ShortClipRefiner
{
public:
    // Refine a mother clip into 1-N short clips using sliding-window energy density.
    // Returns primary first, then alternates.
    QVector<RankedClip> refine(const RankedClip& mother,
                               const QVector<AudioSegment>& audioSegments,
                               const QVector<MotionSegment>& motionSegments,
                               const QVector<HighlightSegment>& speechSegments) const;

private:
    double computeDensity(double windowStart, double windowEnd,
                          const QVector<AudioSegment>& audio,
                          const QVector<MotionSegment>& motion,
                          const QVector<HighlightSegment>& speech) const;
};

#endif // SHORTCLIPREFINER_H
