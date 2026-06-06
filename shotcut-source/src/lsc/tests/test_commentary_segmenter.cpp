#include <QCoreApplication>
#include <QTest>
#include "analyzer/CommentarySegmenter.h"
#include "analyzer/SpeechRecognizer.h"

class TestCommentarySegmenter : public QObject
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

    void testEmptySubtitles()
    {
        CommentarySegmenter segmenter;
        QVector<SubtitleEntry> subtitles;
        QStringList keywords;

        const auto segments = segmenter.buildSegments(subtitles, keywords);
        QCOMPARE(segments.size(), 0);
    }

    void testSingleSubtitle()
    {
        CommentarySegmenter segmenter;
        segmenter.setMinSegmentDuration(1.0);
        QVector<SubtitleEntry> subtitles{
            {0.0, 5.0, QStringLiteral("这是一段解说内容"), 0.9},
        };
        QStringList keywords;

        const auto segments = segmenter.buildSegments(subtitles, keywords);
        QCOMPARE(segments.size(), 1);
    }

    void testMultipleSubtitlesNoPause()
    {
        CommentarySegmenter segmenter;
        segmenter.setMinSegmentDuration(1.0);
        QVector<SubtitleEntry> subtitles{
            {0.0, 2.0, QStringLiteral("第一句话"), 0.9},
            {2.1, 4.0, QStringLiteral("第二句话"), 0.9},
            {4.1, 6.0, QStringLiteral("第三句话"), 0.9},
        };
        QStringList keywords;

        const auto segments = segmenter.buildSegments(subtitles, keywords);
        QCOMPARE(segments.size(), 1); // All merged into one segment
    }

    void testSplitOnLongPause()
    {
        CommentarySegmenter segmenter;
        segmenter.setMinSegmentDuration(1.0);
        QVector<SubtitleEntry> subtitles{
            {0.0, 2.0, QStringLiteral("第一段内容"), 0.9},
            {5.0, 7.0, QStringLiteral("第二段内容"), 0.9}, // 3 second pause
        };
        QStringList keywords;

        const auto segments = segmenter.buildSegments(subtitles, keywords);
        QCOMPARE(segments.size(), 2); // Split on pause
    }

    void testMinSegmentDuration()
    {
        CommentarySegmenter segmenter;
        segmenter.setMinSegmentDuration(10.0); // Require at least 10 seconds

        QVector<SubtitleEntry> subtitles{
            {0.0, 2.0, QStringLiteral("短片段"), 0.9},
        };
        QStringList keywords;

        const auto segments = segmenter.buildSegments(subtitles, keywords);
        QCOMPARE(segments.size(), 0); // Too short, should be filtered
    }

    void cleanupTestCase()
    {
        // Cleanup
    }
};

int main(int argc, char* argv[])
{
    QCoreApplication app(argc, argv);
    TestCommentarySegmenter test;
    return QTest::qExec(&test, argc, argv);
}

#include "test_commentary_segmenter.moc"
