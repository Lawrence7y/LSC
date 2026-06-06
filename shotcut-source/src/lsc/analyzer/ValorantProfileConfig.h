// shotcut-source/src/lsc/analyzer/ValorantProfileConfig.h
#ifndef VALORANTPROFILECONFIG_H
#define VALORANTPROFILECONFIG_H

struct ValorantProfileConfig {
    double roundImportanceWeight = 0.0;
    double combatIntensityWeight = 0.0;
    double reactionIntensityWeight = 0.0;
    double semanticExcitementWeight = 0.0;
    double noveltyWeight = 0.0;
    double clipCompletenessWeight = 0.0;

    static ValorantProfileConfig streamer()
    {
        return {0.10, 0.35, 0.35, 0.10, -0.05, 0.15};
    }

    static ValorantProfileConfig commentary()
    {
        return {0.30, 0.15, 0.15, 0.30, -0.05, 0.15};
    }

    // Weighted fusion of two profiles for dual-run (uncertain classification).
    // weightA is the proportion of profileA scores in the final rankScore.
    static ValorantProfileConfig fuse(const ValorantProfileConfig& a,
                                       const ValorantProfileConfig& b,
                                       double weightA)
    {
        const double wB = 1.0 - weightA;
        return {
            a.roundImportanceWeight * weightA + b.roundImportanceWeight * wB,
            a.combatIntensityWeight * weightA + b.combatIntensityWeight * wB,
            a.reactionIntensityWeight * weightA + b.reactionIntensityWeight * wB,
            a.semanticExcitementWeight * weightA + b.semanticExcitementWeight * wB,
            a.noveltyWeight * weightA + b.noveltyWeight * wB,
            a.clipCompletenessWeight * weightA + b.clipCompletenessWeight * wB,
        };
    }
};

#endif // VALORANTPROFILECONFIG_H
