// shotcut-source/src/lsc/analyzer/ShortClipRefiner.cpp
#include "ShortClipRefiner.h"
#include "../LscConfig.h"

#include <QtGlobal>
#include <algorithm>

double ShortClipRefiner::computeDensity(double windowStart, double windowEnd,
                                         const QVector<AudioSegment>& audio,
                                         const QVector<MotionSegment>& motion,
                                         const QVector<HighlightSegment>& speech) const
{
    const double windowLen = windowEnd - windowStart;
    if (windowLen <= 0.0) return 0.0;

    double audioDensity = 0.0;
    for (const AudioSegment& seg : audio) {
        if (seg.endSec < windowStart || seg.startSec > windowEnd) continue;
        const double overlap = qMin(seg.endSec, windowEnd) - qMax(seg.startSec, windowStart);
        audioDensity = qMax(audioDensity, seg.energy * (overlap / windowLen));
    }

    double motionDensity = 0.0;
    for (const MotionSegment& seg : motion) {
        if (seg.endSec < windowStart || seg.startSec > windowEnd) continue;
        motionDensity = qMax(motionDensity, seg.motionLevel);
    }

    double keywordDensity = 0.0;
    int keywordHits = 0;
    for (const HighlightSegment& seg : speech) {
        if (seg.startSec >= windowStart && seg.endSec <= windowEnd && !seg.keywords.isEmpty()) {
            ++keywordHits;
        }
    }
    keywordDensity = qBound(0.0, keywordHits / windowLen, 1.0);

    return audioDensity * 0.4 + motionDensity * 0.35 + keywordDensity * 0.25;
}

QVector<RankedClip> ShortClipRefiner::refine(const RankedClip& mother,
                                              const QVector<AudioSegment>& audioSegments,
                                              const QVector<MotionSegment>& motionSegments,
                                              const QVector<HighlightSegment>& speechSegments) const
{
    const auto& cfg = lsc::LscConfig::instance();
    const double motherLen = mother.endSec - mother.startSec;

    // If mother is short, do a quick scan first to see if it's uniformly dense.
    // Per spec: if mother <= 50s and the best-window density >= mother full density * 0.8,
    // use the mother directly (no need to cut).
    if (motherLen <= 50.0) {
        const double motherDensity = computeDensity(
            mother.startSec, mother.endSec, audioSegments, motionSegments, speechSegments);
        // Quick scan: check if any sub-window is significantly denser.
        // Use a small window (15s) to detect concentrated density spikes.
        double bestSubDensity = 0.0;
        const double quickLen = cfg.shortClipMinSec;
        for (double w = mother.startSec; w + quickLen <= mother.endSec; w += 1.0) {
            const double d = computeDensity(w, w + quickLen, audioSegments, motionSegments, speechSegments);
            bestSubDensity = qMax(bestSubDensity, d);
        }
        // Per spec: if mother density >= bestWindow density * 0.8, use mother directly.
        // This means: if the mother is uniformly dense enough, no need to cut.
        if (bestSubDensity <= 0.0 || motherDensity >= bestSubDensity * 0.8) {
            RankedClip direct = mother;
            direct.parentClipId = mother.clipId;
            direct.isPrimary = true;
            direct.metadata.insert(QStringLiteral("clipLevel"), QStringLiteral("short"));
            direct.metadata.insert(QStringLiteral("refineStrategy"), QStringLiteral("direct"));
            return {direct};
        }
    }

    // Sliding window density scan.
    struct WindowScore {
        double startSec;
        double density;
    };
    QVector<WindowScore> scores;

    const double scanStart = mother.startSec + cfg.shortClipPaddingSec;
    const double scanEnd = mother.endSec - cfg.shortClipPaddingSec;

    for (double len = cfg.shortClipMinSec; len <= cfg.shortClipMaxSec; len += 5.0) {
        for (double w = scanStart; w + len <= scanEnd; w += cfg.shortClipStepSec) {
            const double density = computeDensity(
                w, w + len, audioSegments, motionSegments, speechSegments);
            scores.append({w, density});
        }
    }

    if (scores.isEmpty()) {
        // Fallback: return a fixed window at the mother center.
        RankedClip fallback = mother;
        fallback.parentClipId = mother.clipId;
        fallback.isPrimary = true;
        const double center = (mother.startSec + mother.endSec) * 0.5;
        fallback.startSec = qMax(mother.startSec, center - cfg.shortClipMinSec * 0.5);
        fallback.endSec = qMin(mother.endSec, fallback.startSec + cfg.shortClipMinSec);
        fallback.metadata.insert(QStringLiteral("clipLevel"), QStringLiteral("short"));
        fallback.metadata.insert(QStringLiteral("refineStrategy"), QStringLiteral("fallback"));
        return {fallback};
    }

    std::sort(scores.begin(), scores.end(),
              [](const WindowScore& a, const WindowScore& b) {
                  return a.density > b.density;
              });

    // Pick the highest-density window as primary.
    const double bestStart = scores.first().startSec;
    const double bestLen = qBound(cfg.shortClipMinSec,
                                  motherLen * 0.4,  // reasonable proportion
                                  cfg.shortClipMaxSec);
    const double bestEnd = qMin(mother.endSec, bestStart + bestLen);

    RankedClip primary = mother;
    primary.parentClipId = mother.clipId;
    primary.isPrimary = true;
    primary.startSec = qMax(mother.startSec, bestStart - cfg.shortClipPaddingSec);
    primary.endSec = qMin(mother.endSec, bestEnd + cfg.shortClipPaddingSec);
    primary.metadata.insert(QStringLiteral("clipLevel"), QStringLiteral("short"));
    primary.metadata.insert(QStringLiteral("refineStrategy"), QStringLiteral("density_max"));

    QVector<RankedClip> result{primary};

    // If there's a second distinct dense window, add as alternate.
    if (scores.size() > 1 && scores[1].density >= scores[0].density * 0.9) {
        RankedClip alt = mother;
        alt.parentClipId = mother.clipId;
        alt.isPrimary = false;
        alt.startSec = qMax(mother.startSec, scores[1].startSec - cfg.shortClipPaddingSec);
        alt.endSec = qMin(mother.endSec, scores[1].startSec + bestLen + cfg.shortClipPaddingSec);
        alt.metadata.insert(QStringLiteral("clipLevel"), QStringLiteral("short"));
        alt.metadata.insert(QStringLiteral("refineStrategy"), QStringLiteral("density_runner_up"));
        primary.alternateIds.append(alt.clipId.isEmpty()
            ? mother.clipId + QStringLiteral("_alt") : alt.clipId);
        result.append(alt);
    }

    return result;
}
