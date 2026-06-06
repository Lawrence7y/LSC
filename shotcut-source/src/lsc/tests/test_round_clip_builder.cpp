// shotcut-source/src/lsc/tests/test_round_clip_builder.cpp
#include "analyzer/RoundClipBuilder.h"

#include <QCoreApplication>
#include <iostream>

int main(int argc, char* argv[])
{
    QCoreApplication app(argc, argv);

    // Clip near the start — should pad rightward.
    RankedClip nearStart;
    nearStart.clipId = "test_001";
    nearStart.startSec = 10.0;
    nearStart.endSec = 30.0;
    nearStart.roundIndex = 1;

    RoundClipBuilder builder;
    const RankedClip startOut = builder.buildMotherClip(nearStart, 180.0);
    const double startDur = startOut.endSec - startOut.startSec;
    bool ok = startDur >= 45.0 && startOut.endSec <= 180.0 && startOut.startSec >= 0.0;
    std::cout << (ok ? "[PASS]" : "[FAIL]") << " near-start mother clip normalization"
              << " (start=" << startOut.startSec << " end=" << startOut.endSec << ")"
              << std::endl;

    // Clip near the end — should pad leftward.
    RankedClip nearEnd;
    nearEnd.clipId = "test_002";
    nearEnd.startSec = 150.0;
    nearEnd.endSec = 175.0;
    nearEnd.roundIndex = 24;
    const RankedClip endOut = builder.buildMotherClip(nearEnd, 180.0);
    const double endDur = endOut.endSec - endOut.startSec;
    ok = ok && endDur >= 45.0 && endOut.endSec <= 180.0 && endOut.startSec >= 0.0;
    std::cout << (ok ? "[PASS]" : "[FAIL]") << " near-end mother clip normalization"
              << " (start=" << endOut.startSec << " end=" << endOut.endSec << ")"
              << std::endl;

    return ok ? 0 : 1;
}
