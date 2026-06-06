// shotcut-source/src/lsc/analyzer/RoundClipBuilder.h
#ifndef ROUNDCLIPBUILDER_H
#define ROUNDCLIPBUILDER_H

#include "RankedClip.h"

class RoundClipBuilder
{
public:
    // Expand a ranked candidate into a mother clip respecting round boundaries.
    // totalDurationSec is the full video duration (clamp limit).
    RankedClip buildMotherClip(const RankedClip& source, double totalDurationSec) const;
};

#endif // ROUNDCLIPBUILDER_H
