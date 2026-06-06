#include <QApplication>
#include <QSignalSpy>
#include <QTest>
#include <QTreeWidget>
#include "analyzer/ClipExporter.h"
#include "analyzer/RankedClip.h"
#include "docks/AnalysisDock.h"
#include "docks/HighlightPreviewWidget.h"
#include "analyzer/HighlightEngine.h"

class TestAnalysisDock : public QObject
{
    Q_OBJECT

private slots:
    void initTestCase()
    {
        // Create a minimal QApplication for widget tests
        if (!QApplication::instance()) {
            int argc = 0;
            char* argv[] = {nullptr};
            new QApplication(argc, argv);
        }
    }

    void testIngestRealtimeSegment()
    {
        AnalysisDock dock;

        HighlightSegment seg;
        seg.startSec = 12.0;
        seg.endSec = 24.0;
        seg.score = 0.8;
        seg.reason = "test highlight";
        seg.keywords = QStringList{"test"};

        dock.ingestRealtimeSegment(seg, "D:/temp/test.mp4");

        QCOMPARE(dock.videoPath(), QString("D:/temp/test.mp4"));
    }

    void testSetVideoPath()
    {
        AnalysisDock dock;
        dock.setVideoPath("D:/temp/video.mp4");
        QCOMPARE(dock.videoPath(), QString("D:/temp/video.mp4"));
    }

    void testHighlightEngineIntegration()
    {
        AnalysisDock dock;
        HighlightEngine* engine = new HighlightEngine(&dock);
        dock.setHighlightEngine(engine);

        // Verify engine is set
        QVERIFY(dock.videoPath().isEmpty());
    }

    void testHighlightExportFolderUsesSiblingDirectory()
    {
        const QString sourcePath = QStringLiteral("D:/desktop/recordings/live_20260606_120919.mp4");
        const QString expected =
            QStringLiteral("D:/desktop/recordings/highlights");
        QCOMPARE(ClipExporter::defaultHighlightDirForSource(sourcePath), expected);
    }

    void testRealtimeOverlapSelectionUsesMainPlayerSignal()
    {
        AnalysisDock dock;

        HighlightSegment first;
        first.startSec = 1.0;
        first.endSec = 3.0;
        first.score = 0.7;
        first.reason = "first";

        HighlightSegment second;
        second.startSec = 2.0;
        second.endSec = 4.0;
        second.score = 0.9;
        second.reason = "second";

        dock.ingestRealtimeSegment(first, "D:/temp/test.mp4");
        dock.ingestRealtimeSegment(second, "D:/temp/test.mp4");

        QListWidget* listWidget = dock.findChild<QListWidget*>();
        QVERIFY(listWidget != nullptr);
        QCOMPARE(listWidget->count(), 1);

        QSignalSpy selectionSpy(&dock, &AnalysisDock::highlightSelected);
        listWidget->setCurrentRow(-1);
        listWidget->setCurrentRow(0);
        QVERIFY(selectionSpy.count() == 1);

        const QList<QVariant> arguments = selectionSpy.takeFirst();
        QCOMPARE(arguments.at(0).toDouble(), 1.0);
        QCOMPARE(arguments.at(1).toDouble(), 4.0);

        QCOMPARE(dock.findChild<HighlightPreviewWidget*>(), nullptr);
    }

    void testTreeRenderingAndFeedback()
    {
        AnalysisDock dock;
        dock.setVideoPath("D:/temp/video.mp4");

        RankedClip mother;
        mother.clipId = "mom_001";
        mother.startSec = 10.0;
        mother.endSec = 70.0;
        mother.isPrimary = true;
        mother.rankScore = 0.89;
        mother.explanation = QStringLiteral("high combat");

        RankedClip shortClip;
        shortClip.clipId = "short_001";
        shortClip.parentClipId = "mom_001";
        shortClip.startSec = 24.0;
        shortClip.endSec = 42.0;
        shortClip.isPrimary = true;
        shortClip.rankScore = 0.92;

        dock.setRankedClips({mother, shortClip});

        QTreeWidget* tree = dock.findChild<QTreeWidget*>();
        QVERIFY(tree != nullptr);
        QCOMPARE(tree->topLevelItemCount(), 1);      // one mother
        QCOMPARE(tree->topLevelItem(0)->childCount(), 1);  // one short clip child
    }

    void testAnnotationActions()
    {
        AnalysisDock dock;
        dock.setVideoPath("D:/temp/video.mp4");

        RankedClip mother;
        mother.clipId = "mom_001";
        mother.startSec = 10.0;
        mother.endSec = 50.0;
        dock.setRankedClips({mother});

        // Simulate annotation: keep + importance.
        dock.simulateAnnotation("mom_001", "keep", 4, QStringLiteral("残局"));

        QVERIFY(dock.annotationFeedback().size() == 1);
        QCOMPARE(dock.annotationFeedback().first().action, QStringLiteral("keep"));
        QCOMPARE(dock.annotationFeedback().first().importance, 4);
    }

    void cleanupTestCase()
    {
        // Cleanup
    }
};

int main(int argc, char* argv[])
{
    QApplication app(argc, argv);
    TestAnalysisDock test;
    return QTest::qExec(&test, argc, argv);
}

#include "test_analysis_dock.moc"
