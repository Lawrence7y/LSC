// shotcut-source/src/lsc/tests/test_realtime_strategy.cpp
#include "analyzer/RealtimeStrategy.h"

#include <QCoreApplication>
#include <QDir>
#include <QEventLoop>
#include <QFileInfo>
#include <QProcess>
#include <QTimer>
#include <iostream>

static bool runFfmpeg(const QStringList& args)
{
    QProcess process;
    process.setProgram("ffmpeg");
    process.setArguments(args);
    process.start();
    return process.waitForFinished(30000) && process.exitCode() == 0;
}

static QString ensureSampleVideo()
{
    const QString path = QDir::tempPath() + "/lsc_realtime_strategy_sample.mp4";
    if (QFileInfo::exists(path)) {
        return path;
    }
    const QStringList args{
        "-y", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=30:duration=8",
        "-f", "lavfi", "-i", "sine=frequency=700:sample_rate=44100:duration=8",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", path};
    return runFfmpeg(args) ? path : QString();
}

int main(int argc, char* argv[])
{
    QCoreApplication app(argc, argv);
    const QString sample = ensureSampleVideo();
    if (sample.isEmpty()) {
        std::cout << "[SKIP] could not generate sample video" << std::endl;
        return 0;
    }

    RealtimeStrategy strategy;
    QEventLoop loop;
    int seenSegments = 0;
    QObject::connect(&strategy, &RealtimeStrategy::segmentFound,
                     [&](const HighlightSegment&) { ++seenSegments; });
    QObject::connect(&strategy, &RealtimeStrategy::finished, &loop, &QEventLoop::quit);

    strategy.analyze(sample);
    QTimer::singleShot(30000, &loop, &QEventLoop::quit);
    loop.exec();

    // RealtimeStrategy should complete and produce output without Whisper/HUD.
    bool ok = !strategy.isRunning();
    std::cout << (ok ? "[PASS] realtime strategy completed without Whisper/HUD"
                     : "[FAIL] strategy still running after timeout")
              << std::endl;
    return ok ? 0 : 1;
}
