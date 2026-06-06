// shotcut-source/src/lsc/tests/test_feedback_store.cpp
#include "analyzer/FeedbackStore.h"

#include <QCoreApplication>
#include <QDir>
#include <QFileInfo>
#include <iostream>

int main(int argc, char* argv[])
{
    QCoreApplication app(argc, argv);

    const QString path = QDir::tempPath() + "/valorant.feedback.json";

    // Clean any leftover file.
    QFile::remove(path);

    FeedbackStore store;
    ClipFeedback feedback;
    feedback.clipId = "mom_001";
    feedback.action = "keep";
    feedback.importance = 5;
    feedback.highlightType = QStringLiteral("残局");
    feedback.adjustedStartSec = 12.0;
    feedback.adjustedEndSec = 68.0;

    const bool writeOk = store.save(path, {feedback});
    const QVector<ClipFeedback> loaded = store.load(path);
    const bool readOk = !loaded.isEmpty()
        && loaded.first().clipId == QStringLiteral("mom_001")
        && loaded.first().action == QStringLiteral("keep")
        && loaded.first().importance == 5
        && loaded.first().highlightType == QStringLiteral("残局");

    std::cout << ((writeOk && readOk) ? "[PASS]" : "[FAIL]") << " feedback store round-trip"
              << std::endl;

    QFile::remove(path);
    return (writeOk && readOk) ? 0 : 1;
}
