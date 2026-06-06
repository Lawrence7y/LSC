#include "analyzer/HighlightDetector.h"

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
    const QString path = QDir::tempPath() + "/lsc_offline_sample.mp4";
    if (QFileInfo::exists(path)) {
        return path;
    }

    const QStringList args{
        "-y", "-f", "lavfi", "-i", "color=c=red:s=320x240:d=2",
        "-f", "lavfi", "-i", "color=c=blue:s=320x240:d=2",
        "-f", "lavfi", "-i", "color=c=green:s=320x240:d=2",
        "-f", "lavfi", "-i", "sine=frequency=880:sample_rate=44100:duration=6",
        "-filter_complex", "[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]",
        "-map", "[v]", "-map", "3:a", "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest", path};

    return runFfmpeg(args) ? path : QString();
}

int main(int argc, char* argv[])
{
    QCoreApplication app(argc, argv);

    LOG("=== HighlightDetector Tests ===");
    LOG("");

    HighlightDetector detector;
    check("initial not running", !detector.isRunning());
    check("initial highlights empty", detector.highlights().isEmpty());

    const QString testVideo = ensureSampleVideo();
    if (testVideo.isEmpty()) {
        skip("offline highlight detection", "ffmpeg sample generation unavailable");
    } else {
        QEventLoop loop;
        bool sawError = false;
        QObject::connect(&detector, &HighlightDetector::finished, &loop, &QEventLoop::quit);
        QObject::connect(&detector, &HighlightDetector::errorOccurred, [&](const QString& e) {
            sawError = true;
            LOG("Error: " + e);
            loop.quit();
        });

        detector.analyze(testVideo);
        QTimer::singleShot(180000, &loop, &QEventLoop::quit);
        loop.exec();

        check("analysis completed", !detector.isRunning());
        check("no detector error", !sawError);
        check("found at least one highlight", !detector.highlights().isEmpty());
        check("first highlight has positive score",
              !detector.highlights().isEmpty() && detector.highlights().first().score > 0.0);
    }

    LOG("");
    LOG(QString("=== Results: %1 passed, %2 failed, %3 skipped ===").arg(g_pass).arg(g_fail).arg(g_skip));
    return g_fail > 0 ? 1 : 0;
}
