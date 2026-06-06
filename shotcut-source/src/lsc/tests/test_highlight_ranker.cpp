// shotcut-source/src/lsc/tests/test_highlight_ranker.cpp
#include "analyzer/HighlightRanker.h"

#include <QCoreApplication>
#include <iostream>

static int g_pass = 0;
static int g_fail = 0;

static void check(const char* name, bool condition)
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

    // Two overlapping candidates — should collapse to one primary
    HighlightSegment combat{};
    combat.startSec = 10.0;
    combat.endSec = 30.0;
    combat.score = 0.6;
    combat.audioScore = 0.8;
    combat.videoScore = 0.9;
    combat.speechScore = 0.1;
    combat.reason = QStringLiteral("combat");
    combat.keywords = {QStringLiteral("ace")};

    HighlightSegment speech{};
    speech.startSec = 12.0;
    speech.endSec = 28.0;
    speech.score = 0.7;
    speech.audioScore = 0.4;
    speech.videoScore = 0.2;
    speech.speechScore = 0.95;
    speech.reason = QStringLiteral("commentary");
    speech.keywords = {QStringLiteral("翻盘")};

    HighlightRanker ranker;
    const auto rankedOverlapping = ranker.rankCandidates({combat, speech},
                                                          ValorantProfileConfig::streamer(),
                                                          QStringLiteral("streamer_pov"));

    check("ranker collapses overlapping candidates", rankedOverlapping.size() == 1);
    check("streamer profile prefers combat-heavy clip",
          rankedOverlapping.first().combatIntensity >= 0.8);
    check("ranker emits explanation text",
          !rankedOverlapping.first().explanation.isEmpty());
    check("ranker marks survived clip as primary",
          rankedOverlapping.first().isPrimary);

    // Two non-overlapping candidates — should both survive
    HighlightSegment early{};
    early.startSec = 0.0;
    early.endSec = 20.0;
    early.score = 0.5;
    early.audioScore = 0.5;
    early.videoScore = 0.5;
    early.speechScore = 0.0;
    early.reason = QStringLiteral("early round");

    HighlightSegment late{};
    late.startSec = 120.0;
    late.endSec = 150.0;
    late.score = 0.8;
    late.audioScore = 0.9;
    late.videoScore = 0.8;
    late.speechScore = 0.2;
    late.reason = QStringLiteral("late round clutch");

    const auto rankedSeparate = ranker.rankCandidates({early, late},
                                                       ValorantProfileConfig::streamer(),
                                                       QStringLiteral("streamer_pov"));
    check("non-overlapping candidates all survive", rankedSeparate.size() == 2);
    check("english round reason is classified as round source",
          !rankedSeparate.isEmpty() && rankedSeparate.first().sourceType == QStringLiteral("round"));

    return g_fail == 0 ? 0 : 1;
}
