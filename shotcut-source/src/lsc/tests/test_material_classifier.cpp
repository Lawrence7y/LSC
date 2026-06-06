// shotcut-source/src/lsc/tests/test_material_classifier.cpp
#include "analyzer/MaterialClassifier.h"

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

    MaterialSignals weak{};
    weak.streamerScore = 0.05;
    weak.commentaryScore = 0.04;

    MaterialClassifier classifier;
    const MaterialClassification weakResult = classifier.classify(weak);
    check("low-signal input becomes uncertain",
          weakResult.materialType == QStringLiteral("uncertain"));
    check("low-signal activates fallback",
          weakResult.fallbackActivated);

    MaterialSignals commentary{};
    commentary.streamerScore = 0.25;
    commentary.commentaryScore = 0.60;
    commentary.voicePresence = 0.80;

    const MaterialClassification commentaryResult = classifier.classify(commentary);
    check("commentary score wins when confidence is high enough",
          commentaryResult.materialType == QStringLiteral("commentary_watchparty"));
    check("classifier emits non-zero confidence",
          commentaryResult.confidence > 0.2);
    check("high-confidence result does not activate fallback",
          !commentaryResult.fallbackActivated);

    MaterialSignals uncertain{};
    uncertain.streamerScore = 0.50;
    uncertain.commentaryScore = 0.55;

    const MaterialClassification uncertainResult = classifier.classify(uncertain);
    check("close scores become uncertain",
          uncertainResult.materialType == QStringLiteral("uncertain"));

    return g_fail == 0 ? 0 : 1;
}
