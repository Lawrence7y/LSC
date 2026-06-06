#include "DanceSegmentScorer.h"

#include <QtGlobal>

double DanceSegmentScorer::score(const DanceFeatures& features) const
{
    return qBound(0.0,
                  features.beatAlignment * m_beatWeight
                      + features.motionStrength * m_motionWeight
                      + features.poseConfidence * m_poseWeight
                      + features.subjectCoverage * m_coverageWeight,
                  1.0);
}

void DanceSegmentScorer::setWeights(double beat, double motion, double pose, double coverage)
{
    m_beatWeight = beat;
    m_motionWeight = motion;
    m_poseWeight = pose;
    m_coverageWeight = coverage;
}
