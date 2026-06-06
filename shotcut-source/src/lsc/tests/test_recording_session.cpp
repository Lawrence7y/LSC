#include "analyzer/HighlightEngine.h"
#include "core/LscDatabase.h"
#include "livestream/RecordingSession.h"

#include <QCoreApplication>
#include <QDir>
#include <QEventLoop>
#include <QFile>
#include <QFileInfo>
#include <QJsonDocument>
#include <QJsonObject>
#include <QList>
#include <QProcess>
#include <QTimer>
#include <QUrl>

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
    lsc::LscDatabase::instance().initialize();

    LOG("=== RecordingSession Tests ===");
    LOG("");

    RecordingSession session;
    runTest("initial status is Stopped", session.status() == RecordingStatus::Stopped);
    runTest("initial not recording", !session.isRecording());
    runTest("preview enabled for default profile", session.previewEnabled());

    {
        RecordingSession profiledSession;
        HighlightEngine engine;

        profiledSession.setAnalysisProfile(AnalysisProfile::valorant());
        profiledSession.setHighlightEngine(&engine);

        runTest("session propagates profile to engine when engine is attached later",
                engine.analysisProfile().id == QStringLiteral("valorant"));
        runTest("valorant profile builds non-generic strategy",
                engine.currentStrategy() != nullptr
                    && engine.currentStrategy()->name().contains(QStringLiteral("valorant"),
                                                                 Qt::CaseInsensitive));
    }

    {
        RecordingSession profiledSession;
        HighlightEngine engine;

        profiledSession.setHighlightEngine(&engine);
        profiledSession.setAnalysisProfile(AnalysisProfile::dance());

        runTest("session propagates profile updates to attached engine",
                engine.analysisProfile().id == QStringLiteral("dance"));
        runTest("dance profile switches strategy",
                engine.currentStrategy() != nullptr
                    && engine.currentStrategy()->name() == QStringLiteral("dance"));
    }

    const QString sampleVideo = ensureSampleVideo();
    if (sampleVideo.isEmpty()) {
        skipTest("offline recording session flow", "ffmpeg sample generation unavailable");
    } else {
        const QString outputPath = QDir::tempPath()
            + "/lsc_recording_session_output_"
            + QString::number(QDateTime::currentMSecsSinceEpoch())
            + ".mp4";
        const QString metadataPath = outputPath + ".json";
        QFile::remove(outputPath);
        QFile::remove(metadataPath);

        HighlightEngine engine;
        engine.setStrategy(HighlightEngine::createGenericStrategy(&engine));
        session.setHighlightEngine(&engine);

        RecordingConfig config;
        config.outputPath = outputPath;
        config.autoReconnect = false;
        config.encodeMode = EncodeMode::CRF;

        bool sawPlatformInfo = false;
        bool sawRecordingStarted = false;
        bool sawRecordingStopped = false;
        bool sawReconnect = false;
        bool analysisFinished = false;
        bool sawProgress = false;
        bool sawError = false;
        bool sawPreviewStart = false;
        bool sawPreviewStop = false;
        QString previewSource;

        QObject::connect(&session, &RecordingSession::platformParsed, [&](const PlatformInfo& info) {
            sawPlatformInfo = info.isValid && info.platform == "direct";
        });
        QObject::connect(&session, &RecordingSession::recordingStarted, [&](const QString&) {
            sawRecordingStarted = true;
            QTimer::singleShot(2200, &session, &RecordingSession::stopRecording);
        });
        QObject::connect(&session, &RecordingSession::recordingStopped, [&](const QString&, qint64) {
            sawRecordingStopped = true;
        });
        QObject::connect(&session, &RecordingSession::progressUpdated, [&](qint64 durationMs, qint64 bytes) {
            sawProgress = sawProgress || (durationMs > 0 && bytes >= 0);
        });
        QObject::connect(&session, &RecordingSession::reconnecting, [&](int, int) {
            sawReconnect = true;
        });
        QObject::connect(&session, &RecordingSession::errorOccurred, [&](const QString& error) {
            sawError = true;
            LOG("Error: " + error);
        });
        QObject::connect(&session, &RecordingSession::previewSourceChanged, [&](const QString& path) {
            sawPreviewStart = !path.isEmpty();
            previewSource = path;
        });
        QObject::connect(&session, &RecordingSession::previewStopped, [&]() {
            sawPreviewStop = true;
        });
        QObject::connect(&engine, &HighlightEngine::finished, [&]() {
            analysisFinished = true;
        });

        QEventLoop loop;
        QObject::connect(&engine, &HighlightEngine::finished, &loop, &QEventLoop::quit);
        QObject::connect(&session, &RecordingSession::errorOccurred, &loop, &QEventLoop::quit);

        session.startRecording(QUrl::fromLocalFile(sampleVideo).toString(), config);
        QTimer::singleShot(20000, &loop, &QEventLoop::quit);
        loop.exec();

        runTest("platform parsed as direct input", sawPlatformInfo);
        runTest("recording started", sawRecordingStarted);
        runTest("recording stopped", sawRecordingStopped);
        runTest("no reconnect for healthy local file", !sawReconnect);
        runTest("no session error", !sawError);
        runTest("progress updated", sawProgress);
        runTest("preview started once output became readable", sawPreviewStart);
        runTest("preview uses stream source instead of recording output",
                previewSource == sampleVideo && previewSource != outputPath);
        runTest("preview stopped when recording stopped", sawPreviewStop);
        runTest("analysis triggered after stop", analysisFinished);
        runTest("duration preserved after stop", session.duration() > 0);
        runTest("output file exists", QFileInfo::exists(outputPath));

        QFile metadataFile(metadataPath);
        bool metadataOk = false;
        if (metadataFile.open(QIODevice::ReadOnly)) {
            const QJsonObject metadata = QJsonDocument::fromJson(metadataFile.readAll()).object();
            const qint64 startTime = metadata.value("startTime").toInteger();
            const qint64 stopTime = metadata.value("stopTime").toInteger();
            metadataOk = metadata.value("durationMs").toDouble() > 0
                && metadata.value("fileSizeBytes").toDouble() > 0
                && startTime > 0
                && stopTime > startTime
                && metadata.value("streamerName").toString() == "direct"
                && metadata.value("selectedQuality").toString() == "source";
        }
        runTest("metadata updated after stop", metadataOk);

        bool projectRecorded = false;
        const auto projects = lsc::LscDatabase::instance().allProjects();
        for (const auto& project : projects) {
            if (project.videoPath == outputPath) {
                projectRecorded = project.sourceUrl == QUrl::fromLocalFile(sampleVideo).toString()
                    && project.platform == "direct"
                    && (project.status == "recorded"
                        || project.status == "analyzed"
                        || project.status == "exported")
                    && project.fileSizeBytes > 0
                    && project.durationSec > 0;
                break;
            }
        }
        runTest("recording stop persists project history", projectRecorded);
    }

    {
        RecordingSession reconnectSession;
        RecordingConfig reconnectConfig;
        reconnectConfig.outputPath = QDir::tempPath() + "/lsc_reconnect_probe.mp4";
        reconnectConfig.autoReconnect = true;
        reconnectConfig.reconnectRetries = 2;
        reconnectConfig.reconnectDelayMs = 10;
        reconnectConfig.maxReconnectDelayMs = 10;

        const QString missingInput =
            QDir::tempPath() + "/lsc_missing_input_" +
            QString::number(QDateTime::currentMSecsSinceEpoch()) + ".mp4";

        bool sawReconnect = false;
        QStringList errors;
        int parseCount = 0;
        QList<int> reconnectAttempts;

        QObject::connect(&reconnectSession, &RecordingSession::platformParsed, [&](const PlatformInfo&) {
            ++parseCount;
        });
        QObject::connect(&reconnectSession, &RecordingSession::reconnecting, [&](int, int) {
            sawReconnect = true;
        });
        QObject::connect(&reconnectSession, &RecordingSession::reconnecting, [&](int attempt, int) {
            reconnectAttempts.append(attempt);
        });
        QObject::connect(&reconnectSession, &RecordingSession::errorOccurred, [&](const QString& error) {
            errors.append(error);
        });

        QEventLoop loop;
        QObject::connect(&reconnectSession, &RecordingSession::errorOccurred, &loop, [&](const QString& error) {
            if (error.contains("Reconnect failed") || error.contains(QString::fromUtf8("重连"))) {
                loop.quit();
            }
        });
        QTimer::singleShot(10000, &loop, &QEventLoop::quit);

        reconnectSession.startRecording(QUrl::fromLocalFile(missingInput).toString(), reconnectConfig);
        loop.exec();

        bool sawDuplicateStartError = false;
        for (const QString& error : std::as_const(errors)) {
            if (error.contains(QString::fromUtf8("已在录制中"))
                || error.contains("Failed to start recording")) {
                sawDuplicateStartError = true;
                break;
            }
        }

        runTest("reconnect flow emits reconnect signal", sawReconnect);
        runTest("reconnect flow reparses source", parseCount >= 2);
        runTest("reconnect attempts increase monotonically",
                reconnectAttempts.size() >= 2
                    && reconnectAttempts.at(0) == 1
                    && reconnectAttempts.at(1) == 2);
        runTest("reconnect flow does not fail with duplicate-start error", !sawDuplicateStartError);
    }

    LOG("");
    LOG(QString("=== Results: %1 passed, %2 failed, %3 skipped ===")
            .arg(g_passCount)
            .arg(g_failCount)
            .arg(g_skipCount));
    return g_failCount > 0 ? 1 : 0;
}
