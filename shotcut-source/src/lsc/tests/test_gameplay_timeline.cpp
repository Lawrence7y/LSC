#include "livestream/GameplayTimeline.h"

#include <QCoreApplication>
#include <iostream>

static int g_pass = 0;
static int g_fail = 0;

void check(const char* name, bool condition)
{
    if (condition) {
        ++g_pass;
        std::cout << "[PASS] " << name << std::endl;
    } else {
        ++g_fail;
        std::cout << "[FAIL] " << name << std::endl;
    }
}

int main(int argc, char* argv[])
{
    QCoreApplication app(argc, argv);

    GameplayTimeline timeline;
    timeline.start(0);
    timeline.recordState(GameState::BuyPhase, 0);
    timeline.recordState(GameState::Gameplay, 10'000);
    timeline.recordState(GameState::RoundEnd, 70'000);
    timeline.recordState(GameState::Gameplay, 90'000);
    timeline.finish(130'000);

    const auto segments = timeline.gameplaySegments();
    check("timeline extracts gameplay segments", segments.size() == 2);
    check("first gameplay segment boundaries",
          segments.size() >= 1
              && qAbs(segments.at(0).startSec - 10.0) < 0.001
              && qAbs(segments.at(0).endSec - 70.0) < 0.001);
    check("second gameplay segment closes at finish",
          segments.size() >= 2
              && qAbs(segments.at(1).startSec - 90.0) < 0.001
              && qAbs(segments.at(1).endSec - 130.0) < 0.001);

    std::cout << "=== Results: " << g_pass << " passed, " << g_fail << " failed ===" << std::endl;
    return g_fail == 0 ? 0 : 1;
}
