#include "analyzer/VideoAnalyzer.h"

#include <QCoreApplication>
#include <QDir>
#include <QEventLoop>
#include <QFileInfo>
#include <QProcess>
#include <QTimer>

#include <iostream>

static int g_pass = 0;
static int g_fail = 0;
static int g_skip = 0;
static int g_count = 0;

#define LOG(msg) std::cout << QString(msg).toStdString() << std::endl

void check(const QString& name, bool cond)
{
    ++g_count;
    if (cond) {
        ++g_pass;
        LOG(QString("[PASS] %1").arg(name));
    } else {
        ++g_fail;
        LOG(QString("[FAIL] %1").arg(name));
    }
}

void skip(const QString& name, const QString& reason)
{
    ++g_skip;
    LOG(QString("[SKIP] %1: %2").arg(name, reason));
}

bool runFfmpeg(const QStringList& args)
{
    QProcess process;
    process.setProgram("ffmpeg");
    process.setArguments(args);
    process.start();
    return process.waitForFinished(30000) && process.exitCode() == 0;
}

QString ensureSampleVideo()
{
    const QString path = QDir::tempPath() + "/lsc_motion_sample_v2.mp4";
    if (QFileInfo::exists(path)) {
        return path;
    }

    const QStringList args{
        "-y",
        "-f",
        "lavfi",
        "-i",
        "testsrc2=size=320x240:rate=25:duration=6",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=660:sample_rate=44100:duration=6",
        "-map",
        "0:v",
        "-map",
        "1:a",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-shortest",
        path};

    return runFfmpeg(args) ? path : QString();
}

int main(int argc, char* argv[])
{
    QCoreApplication app(argc, argv);

    LOG("=== VideoAnalyzer Tests ===");
    LOG("");

    VideoAnalyzer analyzer;
    check("initial not running", !analyzer.isRunning());

    const QString testVideo = ensureSampleVideo();
    if (testVideo.isEmpty()) {
        skip("offline video analysis", "ffmpeg sample generation unavailable");
    } else {
        QEventLoop loop;
        bool sawError = false;
        QObject::connect(&analyzer, &VideoAnalyzer::finished, &loop, &QEventLoop::quit);
        QObject::connect(&analyzer, &VideoAnalyzer::errorOccurred, [&](const QString& e) {
            sawError = true;
            LOG("Error: " + e);
            loop.quit();
        });

        analyzer.analyze(testVideo);
        QTimer::singleShot(120000, &loop, &QEventLoop::quit);
        loop.exec();

        check("analysis completed", !analyzer.isRunning());
        check("no analyzer error", !sawError);
        check("motion segments detected", !analyzer.motionSegments().isEmpty());
        check("average motion positive", analyzer.averageMotion() > 0.05);
        check("moving scene does not rely on scene cuts", analyzer.sceneChanges().size() <= 1);
        if (!analyzer.motionSegments().isEmpty()) {
            check("motion segment spans real time", analyzer.motionSegments().first().endSec - analyzer.motionSegments().first().startSec > 1.0);
            check("motion level reflects sustained movement", analyzer.motionSegments().first().motionLevel > 0.05);
        }
    }

    LOG("");
    LOG(QString("=== Results: %1 passed, %2 failed, %3 skipped ===").arg(g_pass).arg(g_fail).arg(g_skip));
    return g_fail > 0 ? 1 : 0;
}
