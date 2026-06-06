#include "analyzer/AudioAnalyzer.h"

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
    const QString path = QDir::tempPath() + "/lsc_audio_segments_sample.mp4";
    if (QFileInfo::exists(path)) {
        return path;
    }

    const QStringList args{
        "-y",
        "-f",
        "lavfi",
        "-i",
        "color=c=black:s=320x240:d=5",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=880:sample_rate=44100:duration=2",
        "-f",
        "lavfi",
        "-i",
        "anullsrc=channel_layout=stereo:sample_rate=44100:d=1",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:sample_rate=44100:duration=2",
        "-filter_complex",
        "[1:a]volume=0.90[a_loud];"
        "[3:a]volume=0.15[a_soft];"
        "[a_loud][2:a][a_soft]concat=n=3:v=0:a=1[a]",
        "-map",
        "0:v",
        "-map",
        "[a]",
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

    LOG("=== AudioAnalyzer Tests ===");
    LOG("");

    AudioAnalyzer analyzer;
    check("initial not running", !analyzer.isRunning());
    check("initial segments empty", analyzer.segments().isEmpty());

    const QString testVideo = ensureSampleVideo();
    if (testVideo.isEmpty()) {
        skip("offline audio analysis", "ffmpeg sample generation unavailable");
    } else {
        QEventLoop loop;
        bool sawError = false;
        QObject::connect(&analyzer, &AudioAnalyzer::finished, &loop, &QEventLoop::quit);
        QObject::connect(&analyzer, &AudioAnalyzer::errorOccurred, [&](const QString& e) {
            sawError = true;
            LOG("Error: " + e);
            loop.quit();
        });

        analyzer.analyze(testVideo);
        QTimer::singleShot(60000, &loop, &QEventLoop::quit);
        loop.exec();

        check("analysis completed", !analyzer.isRunning());
        check("no analyzer error", !sawError);
        check("segments not empty", !analyzer.segments().isEmpty());
        check("multiple voiced segments detected", analyzer.segments().size() >= 2);
        check("segment duration positive", !analyzer.segments().isEmpty() && analyzer.segments().first().endSec > analyzer.segments().first().startSec);
        check("overall loudness captured", analyzer.overallLoudness() > -100.0);
        check("peak db captured", analyzer.peakDb() > -100.0);
        if (analyzer.segments().size() >= 2) {
            const AudioSegment& first = analyzer.segments().first();
            const AudioSegment& last = analyzer.segments().last();
            LOG(QString("first segment: rms=%1 max=%2 energy=%3")
                    .arg(first.rmsDb, 0, 'f', 2)
                    .arg(first.maxDb, 0, 'f', 2)
                    .arg(first.energy, 0, 'f', 2));
            LOG(QString("last segment: rms=%1 max=%2 energy=%3")
                    .arg(last.rmsDb, 0, 'f', 2)
                    .arg(last.maxDb, 0, 'f', 2)
                    .arg(last.energy, 0, 'f', 2));
            check("loud and soft segments have different RMS", qAbs(first.rmsDb - last.rmsDb) > 1.0);
            check("loud and soft segments have different peak levels", qAbs(first.maxDb - last.maxDb) > 1.0);
            check("louder segment has higher normalized energy", first.energy > last.energy);
        }
    }

    LOG("");
    LOG(QString("=== Results: %1 passed, %2 failed, %3 skipped ===").arg(g_pass).arg(g_fail).arg(g_skip));
    return g_fail > 0 ? 1 : 0;
}
