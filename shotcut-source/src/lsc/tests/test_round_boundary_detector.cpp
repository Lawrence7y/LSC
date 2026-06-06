#include <QCoreApplication>
#include <QTest>
#include "analyzer/RoundBoundaryDetector.h"

class TestRoundBoundaryDetector : public QObject
{
    Q_OBJECT

private slots:
    void initTestCase()
    {
        if (!QCoreApplication::instance()) {
            int argc = 0;
            char* argv[] = {nullptr};
            new QCoreApplication(argc, argv);
        }
    }

    void testBuildRoundsFromEvents()
    {
        RoundBoundaryDetector detector;
        QVector<HudEvent> events{
            {5.0, "buy_phase"},
            {105.0, "round_end"},
            {120.0, "buy_phase"},
            {215.0, "round_end"},
        };

        const auto rounds = detector.buildRounds(events);
        QCOMPARE(rounds.size(), 2);

        QCOMPARE(rounds[0].startSec, 5.0);
        QCOMPARE(rounds[0].endSec, 105.0);
        QCOMPARE(rounds[1].startSec, 120.0);
        QCOMPARE(rounds[1].endSec, 215.0);
    }

    void testNoRoundsWhenIncomplete()
    {
        RoundBoundaryDetector detector;
        QVector<HudEvent> events{
            {5.0, "buy_phase"},
            // No round_end
        };

        const auto rounds = detector.buildRounds(events);
        QCOMPARE(rounds.size(), 0);
    }

    void testCustomEventTypes()
    {
        RoundBoundaryDetector detector;
        detector.setBuyPhaseType("preparation");
        detector.setRoundEndType("victory");

        QVector<HudEvent> events{
            {10.0, "preparation"},
            {90.0, "victory"},
        };

        const auto rounds = detector.buildRounds(events);
        QCOMPARE(rounds.size(), 1);
        QCOMPARE(rounds[0].startSec, 10.0);
        QCOMPARE(rounds[0].endSec, 90.0);
    }

    void testEmptyEvents()
    {
        RoundBoundaryDetector detector;
        QVector<HudEvent> events;

        const auto rounds = detector.buildRounds(events);
        QCOMPARE(rounds.size(), 0);
    }

    void testMultipleBuyPhasesBeforeRoundEnd()
    {
        RoundBoundaryDetector detector;
        QVector<HudEvent> events{
            {5.0, "buy_phase"},
            {10.0, "buy_phase"}, // Second buy phase should overwrite first
            {100.0, "round_end"},
        };

        const auto rounds = detector.buildRounds(events);
        QCOMPARE(rounds.size(), 1);
        QCOMPARE(rounds[0].startSec, 10.0); // Uses last buy_phase
        QCOMPARE(rounds[0].endSec, 100.0);
    }

    void testRoundTitle()
    {
        RoundBoundaryDetector detector;
        QVector<HudEvent> events{
            {0.0, "buy_phase"},
            {60.0, "round_end"},
        };

        const auto rounds = detector.buildRounds(events);
        QCOMPARE(rounds.size(), 1);
        QVERIFY(!rounds[0].title.isEmpty());
    }

    void cleanupTestCase()
    {
        // Cleanup
    }
};

int main(int argc, char* argv[])
{
    QCoreApplication app(argc, argv);
    TestRoundBoundaryDetector test;
    return QTest::qExec(&test, argc, argv);
}

#include "test_round_boundary_detector.moc"
