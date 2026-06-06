#include "livestream/StreamCapture.h"

#include <QCoreApplication>
#include <QDir>
#include <QEventLoop>
#include <QFile>
#include <QFileInfo>
#include <QProcess>
#include <QTimer>

#include <iostream>

static int g_testCount = 0;
static int g_passCount = 0;
static int g_failCount = 0;
static int g_skipCount = 0;

#define LOG(msg) std::cout << QString(msg).toStdString() << std::endl

void runTest(const QString& name, bool condition)
{
    ++g_testCount;
    if (condition) {
        ++g_passCount;
        LOG(QString("[PASS] %1").arg(name));
    } else {
        ++g_failCount;
        LOG(QString("[FAIL] %1").arg(name));
    }
}

void skipTest(const QString& name, const QString& reason)
{
    ++g_skipCount;
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
        "-y",
        "-f",
        "lavfi",
        "-i",
        "color=c=red:s=320x240:d=2",
        "-f",
        "lavfi",
        "-i",
        "color=c=blue:s=320x240:d=2",
        "-f",
        "lavfi",
        "-i",
        "color=c=green:s=320x240:d=2",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=880:sample_rate=44100:duration=6",
        "-filter_complex",
        "[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]",
        "-map",
        "[v]",
        "-map",
        "3:a",
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

    LOG("=== StreamCapture Tests ===");
    LOG("");

    StreamCapture capture;
    runTest("initial status is Stopped", capture.status() == RecordingStatus::Stopped);
    runTest("initial duration is 0", capture.duration() == 0);
    runTest("initial fileSize is 0", capture.fileSize() == 0);

    RecordingConfig config;
    runTest("default format is mp4", config.format == "mp4");
    runTest("default encodeMode is CRF", config.encodeMode == EncodeMode::CRF);
    runTest("default autoReconnect is true", config.autoReconnect);
    {
        RecordingConfig argsConfig;
        argsConfig.outputPath = QDir::tempPath() + "/lsc_args_probe.mp4";
        argsConfig.autoReconnect = true;
        const QStringList args =
            StreamCapture::buildEncoderArgsStatic("https://example.com/live.m3u8", argsConfig);
        const int reconnectIndex = args.indexOf("-reconnect");
        const int inputIndex = args.indexOf("-i");
        runTest("reconnect options are placed before input",
                reconnectIndex >= 0 && inputIndex > reconnectIndex);
    }

    bool emptyStartError = false;
    QObject::connect(&capture, &StreamCapture::errorOccurred, [&](const QString&) {
        emptyStartError = true;
    });
    runTest("start rejects empty url", !capture.start(QString(), config));
    runTest("empty url emits error", emptyStartError);

    const QString sampleVideo = ensureSampleVideo();
    if (sampleVideo.isEmpty()) {
        skipTest("offline capture flow", "ffmpeg sample generation unavailable");
    } else {
        const QString outputPath = QDir::tempPath() + "/lsc_test_capture.mp4";
        QFile::remove(outputPath);

        RecordingConfig offlineConfig;
        offlineConfig.outputPath = outputPath;
        offlineConfig.encodeMode = EncodeMode::CRF;
        offlineConfig.autoReconnect = false;

        bool sawStarting = false;
        bool sawRecording = false;
        bool sawStopped = false;
        bool sawProgress = false;

        QObject::connect(&capture, &StreamCapture::statusChanged, [&](RecordingStatus status) {
            sawStarting = sawStarting || status == RecordingStatus::Starting;
            sawRecording = sawRecording || status == RecordingStatus::Recording;
            sawStopped = sawStopped || status == RecordingStatus::Stopped;
        });
        QObject::connect(&capture, &StreamCapture::progressUpdated, [&](qint64 durationMs, qint64 bytes) {
            sawProgress = sawProgress || (durationMs > 0 && bytes >= 0);
        });

        runTest("start accepts local input", capture.start(sampleVideo, offlineConfig));

        QEventLoop loop;
        QObject::connect(&capture, &StreamCapture::statusChanged, &loop, [&](RecordingStatus status) {
            if (status == RecordingStatus::Recording) {
                QTimer::singleShot(1500, &capture, &StreamCapture::stop);
            }
            if (status == RecordingStatus::Stopped || status == RecordingStatus::Error) {
                loop.quit();
            }
        });
        QTimer::singleShot(15000, &loop, &QEventLoop::quit);
        loop.exec();

        const QFileInfo recorded(outputPath);
        runTest("entered Starting state", sawStarting);
        runTest("entered Recording state", sawRecording);
        runTest("returned to Stopped state", sawStopped);
        runTest("emitted progress", sawProgress);
        runTest("duration preserved after stop", capture.duration() > 0);
        runTest("output file exists", recorded.exists());
        runTest("output file has content", recorded.exists() && recorded.size() > 0);

    }

    LOG("");
    LOG(QString("=== Results: %1 passed, %2 failed, %3 skipped ===")
            .arg(g_passCount)
            .arg(g_failCount)
            .arg(g_skipCount));
    return g_failCount > 0 ? 1 : 0;
}
