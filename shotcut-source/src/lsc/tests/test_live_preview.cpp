#include <QCoreApplication>
#include <QTest>
#include "livestream/PreviewController.h"

class TestLivePreview : public QObject
{
    Q_OBJECT

private slots:
    void initTestCase()
    {
        // Create a minimal QCoreApplication if needed
        if (!QCoreApplication::instance()) {
            int argc = 0;
            char* argv[] = {nullptr};
            new QCoreApplication(argc, argv);
        }
    }

    void testPreviewControllerInitialState()
    {
        PreviewController controller;
        QVERIFY(!controller.isActive());
        QVERIFY(controller.currentSource().isEmpty());
    }

    void testSetPreviewSource()
    {
        PreviewController controller;
        bool sawPreviewStart = false;

        QObject::connect(&controller, &PreviewController::previewAvailable,
                         [&sawPreviewStart](const QString& path) {
                             sawPreviewStart = !path.isEmpty();
                         });

        controller.setPreviewSource("D:/temp/live_frag.mp4");

        QVERIFY(sawPreviewStart);
        QVERIFY(controller.isActive());
        QCOMPARE(controller.currentSource(), QString("D:/temp/live_frag.mp4"));
    }

    void testClearPreviewSource()
    {
        PreviewController controller;
        bool sawPreviewStop = false;

        QObject::connect(&controller, &PreviewController::previewCleared,
                         [&sawPreviewStop]() {
                             sawPreviewStop = true;
                         });

        controller.setPreviewSource("D:/temp/live_frag.mp4");
        controller.clearPreviewSource();

        QVERIFY(sawPreviewStop);
        QVERIFY(!controller.isActive());
        QVERIFY(controller.currentSource().isEmpty());
    }

    void testPreviewSourceChanged()
    {
        PreviewController controller;
        int signalCount = 0;

        QObject::connect(&controller, &PreviewController::previewAvailable,
                         [&signalCount]() {
                             signalCount++;
                         });

        controller.setPreviewSource("D:/temp/video1.mp4");
        controller.setPreviewSource("D:/temp/video2.mp4");

        QCOMPARE(signalCount, 2);
        QCOMPARE(controller.currentSource(), QString("D:/temp/video2.mp4"));
    }

    void testClearWhenAlreadyEmpty()
    {
        PreviewController controller;
        int signalCount = 0;

        QObject::connect(&controller, &PreviewController::previewCleared,
                         [&signalCount]() {
                             signalCount++;
                         });

        controller.clearPreviewSource(); // Should not emit signal

        QCOMPARE(signalCount, 0);
    }

    void cleanupTestCase()
    {
        // Cleanup
    }
};

int main(int argc, char* argv[])
{
    QCoreApplication app(argc, argv);
    TestLivePreview test;
    return QTest::qExec(&test, argc, argv);
}

#include "test_live_preview.moc"
