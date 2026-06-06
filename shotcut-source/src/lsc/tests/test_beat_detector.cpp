#include "analyzer/BeatDetector.h"

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

#define LOG(msg) std::cout << QString(msg).toStdString() << std::endl

void check(const QString& name, bool cond)
{
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

QString ensureBeatSample()
{
    const QString path = QDir::tempPath() + "/lsc_beat_sample_v2.mp4";
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
        "aevalsrc=if(lt(mod(t\\,0.5)\\,0.1)\\,0.9*sin(2*PI*880*t)\\,0):s=44100:d=6",
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

    LOG("=== BeatDetector Tests ===");
    LOG("");

    BeatDetector detector;
    check("initial not running", !detector.isRunning());

    const QString sample = ensureBeatSample();
    if (sample.isEmpty()) {
        skip("beat detection sample", "ffmpeg sample generation unavailable");
    } else {
        QEventLoop loop;
        bool sawError = false;
        QObject::connect(&detector, &BeatDetector::finished, &loop, &QEventLoop::quit);
        QObject::connect(&detector, &BeatDetector::errorOccurred, [&](const QString& error) {
            sawError = true;
            LOG("Error: " + error);
            loop.quit();
        });

        detector.detect(sample);
        QTimer::singleShot(120000, &loop, &QEventLoop::quit);
        loop.exec();

        check("beat detector completed", !detector.isRunning());
        check("beat detector no error", !sawError);
        check("beat detector found beats", !detector.beats().isEmpty());
        check("beat detector bpm positive", detector.bpm() > 0.0);
    }

    LOG("");
    LOG(QString("=== Results: %1 passed, %2 failed, %3 skipped ===").arg(g_pass).arg(g_fail).arg(g_skip));
    return g_fail > 0 ? 1 : 0;
}
