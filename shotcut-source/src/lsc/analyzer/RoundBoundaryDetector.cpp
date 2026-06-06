#include "RoundBoundaryDetector.h"

QVector<RoundSegment> RoundBoundaryDetector::buildRounds(const QVector<HudEvent>& events) const
{
    QVector<RoundSegment> rounds;
    double currentStart = -1.0;

    for (const HudEvent& event : events) {
        if (event.type == m_buyPhaseType) {
            currentStart = event.timestampSec;
        } else if (event.type == m_roundEndType && currentStart >= 0.0) {
            RoundSegment round;
            round.startSec = currentStart;
            round.endSec = event.timestampSec;
            round.title = QStringLiteral("回合片段");
            rounds.append(round);
            currentStart = -1.0;
        }
    }

    return rounds;
}
