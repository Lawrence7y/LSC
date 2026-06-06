// shotcut-source/src/lsc/analyzer/RankedClip.h
#ifndef RANKEDCLIP_H
#define RANKEDCLIP_H

#include <QJsonObject>
#include <QString>
#include <QStringList>

struct RankedClip {
    double startSec = 0.0;
    double endSec = 0.0;
    QString clipId;

    // Derived by HighlightRanker from strategy-level scores and segment metadata.
    double roundImportance = 0.0;
    double combatIntensity = 0.0;
    double reactionIntensity = 0.0;
    double semanticExcitement = 0.0;
    double novelty = 0.0;
    double clipCompleteness = 0.0;

    double rankScore = 0.0;
    QStringList signalNames;   // signal names: "audio_peak", "motion_surge", etc.
    QString explanation;
    QString sourceType;        // "round" | "combat" | "speech" | "anomaly"
    int roundIndex = -1;
    QString roundPhase;        // "buy" | "combat" | "post_round"
    QString parentClipId;      // empty for mother clips
    bool isPrimary = false;
    QStringList alternateIds;
    QJsonObject metadata;
};

#endif // RANKEDCLIP_H
