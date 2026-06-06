#include <QCoreApplication>
#include <QTest>
#include "analyzer/DanceSegmentScorer.h"

class TestDanceSegmentScorer : public QObject
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

    void testHighScoreForGoodFeatures()
    {
        DanceSegmentScorer scorer;
        DanceFeatures features;
        features.beatAlignment = 0.82;
        features.motionStrength = 0.76;
        features.poseConfidence = 0.88;
        features.subjectCoverage = 0.71;

        double score = scorer.score(features);
        QVERIFY(score > 0.75);
    }

    void testLowScoreForPoorFeatures()
    {
        DanceSegmentScorer scorer;
        DanceFeatures features;
        features.beatAlignment = 0.1;
        features.motionStrength = 0.2;
        features.poseConfidence = 0.3;
        features.subjectCoverage = 0.1;

        double score = scorer.score(features);
        QVERIFY(score < 0.3);
    }

    void testScoreRange()
    {
        DanceSegmentScorer scorer;
        DanceFeatures features;

        // Test minimum
        features.beatAlignment = 0.0;
        features.motionStrength = 0.0;
        features.poseConfidence = 0.0;
        features.subjectCoverage = 0.0;
        double minScore = scorer.score(features);
        QCOMPARE(minScore, 0.0);

        // Test maximum
        features.beatAlignment = 1.0;
        features.motionStrength = 1.0;
        features.poseConfidence = 1.0;
        features.subjectCoverage = 1.0;
        double maxScore = scorer.score(features);
        QCOMPARE(maxScore, 1.0);
    }

    void testCustomWeights()
    {
        DanceSegmentScorer scorer;
        scorer.setWeights(0.5, 0.3, 0.1, 0.1);

        DanceFeatures features;
        features.beatAlignment = 1.0;
        features.motionStrength = 0.0;
        features.poseConfidence = 0.0;
        features.subjectCoverage = 0.0;

        double score = scorer.score(features);
        QCOMPARE(score, 0.5);
    }

    void testScoreBoundaries()
    {
        DanceSegmentScorer scorer;
        DanceFeatures features;

        // Test with very high values (should be clamped to 1.0)
        features.beatAlignment = 2.0;
        features.motionStrength = 2.0;
        features.poseConfidence = 2.0;
        features.subjectCoverage = 2.0;
        double score = scorer.score(features);
        QCOMPARE(score, 1.0);

        // Test with negative values (should be clamped to 0.0)
        features.beatAlignment = -1.0;
        features.motionStrength = -1.0;
        features.poseConfidence = -1.0;
        features.subjectCoverage = -1.0;
        score = scorer.score(features);
        QCOMPARE(score, 0.0);
    }

    void cleanupTestCase()
    {
        // Cleanup
    }
};

int main(int argc, char* argv[])
{
    QCoreApplication app(argc, argv);
    TestDanceSegmentScorer test;
    return QTest::qExec(&test, argc, argv);
}

#include "test_dance_segment_scorer.moc"
