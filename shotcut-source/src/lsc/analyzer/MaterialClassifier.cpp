// shotcut-source/src/lsc/analyzer/MaterialClassifier.cpp
#include "MaterialClassifier.h"
#include "../LscConfig.h"

#include <QtGlobal>

using lsc::LscConfig;

MaterialClassification MaterialClassifier::classify(const MaterialSignals& inputSignals) const
{
    MaterialClassification out;
    out.streamerScore = qMax(0.0, inputSignals.streamerScore);
    out.commentaryScore = qMax(0.0, inputSignals.commentaryScore);

    const double maxScore = qMax(out.streamerScore, out.commentaryScore);
    if (maxScore < LscConfig::instance().classificationSignalFloor) {
        out.fallbackActivated = true;
        return out;  // materialType stays "uncertain"
    }

    out.confidence = qAbs(out.streamerScore - out.commentaryScore) / maxScore;
    if (out.confidence < LscConfig::instance().classificationConfidenceThreshold) {
        out.fallbackActivated = true;
        return out;  // materialType stays "uncertain"
    }

    out.materialType = out.streamerScore >= out.commentaryScore
        ? QStringLiteral("streamer_pov")
        : QStringLiteral("commentary_watchparty");
    return out;
}
