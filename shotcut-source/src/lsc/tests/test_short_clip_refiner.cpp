// shotcut-source/src/lsc/tests/test_short_clip_refiner.cpp
#include "analyzer/ShortClipRefiner.h"

#include <QCoreApplication>
#include <iostream>

int main(int argc, char* argv[])
{
    QCoreApplication app(argc, argv);

    // Mother clip with a clear dense window at 118–126s.
    RankedClip mother;
    mother.clipId = "mom_001";
    mother.startSec = 100.0;
    mother.endSec = 150.0;

    QVector<AudioSegment> audio{
        {100.0, 108.0, -12.0, -8.0, 0.3},
        {118.0, 126.0, -6.0, -4.0, 0.9},   // dense
        {135.0, 142.0, -10.0, -7.0, 0.4},
    };
    QVector<MotionSegment> motion{
        {101.0, 107.0, 0.25},
        {119.0, 126.0, 0.85},               // dense
        {136.0, 141.0, 0.35},
    };

    ShortClipRefiner refiner;
    const QVector<RankedClip> shortClips = refiner.refine(mother, audio, motion, {});

    bool ok = !shortClips.isEmpty();
    if (ok) {
        const double dur = shortClips.first().endSec - shortClips.first().startSec;
        ok = dur >= 15.0 && dur <= 45.0;
        // The highest-density window (118-126) should be near the center of the short clip.
        ok = ok && shortClips.first().startSec <= 118.0
            && shortClips.first().endSec >= 126.0;
    }
    std::cout << (ok ? "[PASS]" : "[FAIL]") << " short clip density-based refinement"
              << " (count=" << shortClips.size()
              << " start=" << (shortClips.isEmpty() ? 0.0 : shortClips.first().startSec)
              << " end=" << (shortClips.isEmpty() ? 0.0 : shortClips.first().endSec) << ")"
              << std::endl;
    return ok ? 0 : 1;
}
