// shotcut-source/src/lsc/analyzer/MaterialClassifier.h
#ifndef MATERIALCLASSIFIER_H
#define MATERIALCLASSIFIER_H

#include <QString>
#include <QStringList>

struct MaterialSignals {
    double streamerScore = 0.0;
    double commentaryScore = 0.0;
    double voicePresence = 0.0;      // ratio of non-silence audio to total duration
    double combatDensity = 0.0;      // audio burst + scene change density
    double burstReactionRate = 0.0;  // frequency of short high-volume peaks
};

struct MaterialClassification {
    QString materialType = QStringLiteral("uncertain");
    double confidence = 0.0;
    double streamerScore = 0.0;
    double commentaryScore = 0.0;
    bool fallbackActivated = false;
};

class MaterialClassifier
{
public:
    MaterialClassification classify(const MaterialSignals& inputSignals) const;
};

#endif // MATERIALCLASSIFIER_H
