// shotcut-source/src/lsc/analyzer/HighlightRanker.cpp
#include "HighlightRanker.h"
#include "HighlightUtils.h"
#include "../LscConfig.h"

#include <algorithm>

void HighlightRanker::computeFeatures(RankedClip& clip, const HighlightSegment& input) const
{
    // Strategy-level candidates already carry normalized signal scores; the ranker
    // maps them into comparable feature dimensions before profile weighting.
    clip.roundImportance = qBound(0.1, input.score, 1.0);
    clip.combatIntensity = qBound(0.1, input.videoScore, 1.0);
    clip.reactionIntensity = qBound(0.1, input.audioScore, 1.0);
    clip.semanticExcitement = qBound(0.1, input.speechScore, 1.0);
    clip.novelty = 1.0;  // computed during deduplicateByOverlap
    clip.clipCompleteness = qBound(0.1, (input.endSec - input.startSec) / 45.0, 1.0);
}

QVector<RankedClip> HighlightRanker::deduplicateByOverlap(QVector<RankedClip>& ranked) const
{
    const double threshold = lsc::LscConfig::instance().rankerMergeOverlapThreshold;
    QVector<RankedClip> kept;
    // Input is already sorted by rankScore descending.
    for (int i = 0; i < ranked.size(); ++i) {
        bool overlapped = false;
        for (int j = 0; j < kept.size(); ++j) {
            const double overlapStart = qMax(ranked[i].startSec, kept[j].startSec);
            const double overlapEnd = qMin(ranked[i].endSec, kept[j].endSec);
            const double overlap = overlapEnd - overlapStart;
            if (overlap <= 0.0) {
                continue;
            }
            const double minLen = qMax(0.1,
                qMin(ranked[i].endSec - ranked[i].startSec,
                     kept[j].endSec - kept[j].startSec));
            if (overlap / minLen >= threshold) {
                overlapped = true;
                if (ranked[i].rankScore > kept[j].rankScore) {
                    // Replace lower-score entry with higher-score one.
                    kept[j].alternateIds.append(kept[j].clipId);
                    ranked[i].alternateIds.append(kept[j].alternateIds);
                    kept[j] = ranked[i];
                } else {
                    kept[j].alternateIds.append(ranked[i].clipId);
                }
                break;
            }
        }
        if (!overlapped) {
            kept.append(ranked[i]);
        }
    }
    // Mark highest-score clip as primary.
    if (!kept.isEmpty()) {
        kept.first().isPrimary = true;
    }
    // Compute novelty: 1.0 - max overlap with any higher-ranked clip.
    for (int i = 0; i < kept.size(); ++i) {
        double maxOverlap = 0.0;
        for (int j = 0; j < i; ++j) {
            const double overlapStart = qMax(kept[i].startSec, kept[j].startSec);
            const double overlapEnd = qMin(kept[i].endSec, kept[j].endSec);
            const double overlap = qMax(0.0, overlapEnd - overlapStart);
            const double minLen = qMax(0.1,
                qMin(kept[i].endSec - kept[i].startSec,
                     kept[j].endSec - kept[j].startSec));
            maxOverlap = qMax(maxOverlap, overlap / minLen);
        }
        kept[i].novelty = qBound(0.0, 1.0 - maxOverlap, 1.0);
    }
    return kept;
}

QVector<RankedClip> HighlightRanker::rankCandidates(const QVector<HighlightSegment>& candidates,
                                                    const ValorantProfileConfig& profile,
                                                    const QString& materialType) const
{
    QVector<RankedClip> ranked;
    for (int i = 0; i < candidates.size(); ++i) {
        const HighlightSegment& input = candidates.at(i);
        RankedClip clip;
        clip.startSec = input.startSec;
        clip.endSec = input.endSec;
        clip.clipId = QStringLiteral("%1_%2").arg(materialType).arg(i + 1);
        const QString reasonLower = input.reason.toLower();
        clip.sourceType = reasonLower.contains(QStringLiteral("回合"))
                            || reasonLower.contains(QStringLiteral("round"))
                        ? QStringLiteral("round")
                        : input.speechScore > 0.5 ? QStringLiteral("speech")
                        : QStringLiteral("combat");

        computeFeatures(clip, input);

        clip.rankScore =
            clip.roundImportance * profile.roundImportanceWeight +
            clip.combatIntensity * profile.combatIntensityWeight +
            clip.reactionIntensity * profile.reactionIntensityWeight +
            clip.semanticExcitement * profile.semanticExcitementWeight +
            clip.novelty * profile.noveltyWeight +
            clip.clipCompleteness * profile.clipCompletenessWeight;

        clip.explanation = QStringLiteral("audio=%1 video=%2 speech=%3 score=%4")
            .arg(clip.reactionIntensity, 0, 'f', 2)
            .arg(clip.combatIntensity, 0, 'f', 2)
            .arg(clip.semanticExcitement, 0, 'f', 2)
            .arg(clip.rankScore, 0, 'f', 2);

        // Store signal names (derived from input), not keywords.
        QStringList signalNames;
        if (input.audioScore >= 0.6) signalNames.append(QStringLiteral("audio_peak"));
        if (input.videoScore >= 0.6) signalNames.append(QStringLiteral("motion_surge"));
        if (input.speechScore >= 0.5) signalNames.append(QStringLiteral("speech_high"));
        clip.signalNames = signalNames;

        ranked.append(clip);
    }

    // Sort by rankScore descending.
    std::sort(ranked.begin(), ranked.end(), [](const RankedClip& a, const RankedClip& b) {
        return a.rankScore > b.rankScore;
    });

    return deduplicateByOverlap(ranked);
}
