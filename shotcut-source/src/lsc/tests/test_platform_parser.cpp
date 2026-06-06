#include "livestream/PlatformParser.h"
#include "livestream/platforms/BilibiliParser.h"
#include "livestream/platforms/DouyinParser.h"
#include <QCoreApplication>
#include <QDir>
#include <QEventLoop>
#include <QFileInfo>
#include <QProcess>
#include <QTimer>
#include <iostream>

static int g_testCount = 0;
static int g_passCount = 0;
static int g_failCount = 0;

#define LOG(msg) std::cout << QString(msg).toStdString() << std::endl

void runTest(const QString& name, bool condition)
{
    g_testCount++;
    if (condition) {
        g_passCount++;
        LOG(QString("[PASS] %1").arg(name));
    } else {
        g_failCount++;
        LOG(QString("[FAIL] %1").arg(name));
    }
}

void runTest(const QString& name, const QString& expected, const QString& actual)
{
    runTest(name, expected == actual);
    if (expected != actual) {
        LOG(QString("  Expected: %1").arg(expected));
        LOG(QString("  Actual:   %1").arg(actual));
    }
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
    const QString path = QDir::tempPath() + "/lsc_platform_parser_sample.mp4";
    if (QFileInfo::exists(path)) {
        return path;
    }

    const QStringList args{
        "-y",
        "-f",
        "lavfi",
        "-i",
        "color=c=blue:s=320x240:d=2",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:sample_rate=44100:duration=2",
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

    LOG("=== PlatformParser Unit Tests ===");
    LOG("");

    runTest("detectPlatform douyin.com",
            "douyin",
            PlatformParser::detectPlatform(QUrl("https://www.douyin.com/follow/live/53682367755")));

    runTest("detectPlatform live.douyin.com",
            "douyin",
            PlatformParser::detectPlatform(QUrl("https://live.douyin.com/53682367755")));

    runTest("detectPlatform bilibili.com",
            "bilibili",
            PlatformParser::detectPlatform(QUrl("https://live.bilibili.com/12345")));

    runTest("detectPlatform b23.tv",
            "bilibili",
            PlatformParser::detectPlatform(QUrl("https://b23.tv/abc123")));

    runTest("detectPlatform youtube.com",
            "youtube",
            PlatformParser::detectPlatform(QUrl("https://www.youtube.com/watch?v=abc123")));

    runTest("detectPlatform twitch.tv",
            "twitch",
            PlatformParser::detectPlatform(QUrl("https://www.twitch.tv/somechannel")));

    runTest("detectPlatform kuaishou.com",
            "kuaishou",
            PlatformParser::detectPlatform(QUrl("https://live.kuaishou.com/u/abc123")));

    runTest("detectPlatform unknown",
            "",
            PlatformParser::detectPlatform(QUrl("https://example.com/live")));

    runTest("normalize offline platform error",
            PlatformParser::normalizeError("Room is not live", "bilibili")
                .contains("[not_live]"));
    runTest("normalize login platform error",
            PlatformParser::normalizeError("require login or cookie", "douyin")
                .contains("[login_required]"));
    runTest("normalize network platform error",
            PlatformParser::normalizeError("Connection timed out", "douyin")
                .contains("[network_error]"));

    lsc::DouyinParser douyinParser;
    lsc::BilibiliParser bilibiliParser;
    runTest("native douyin parser accepts douyin live URL",
            douyinParser.canParse("https://live.douyin.com/53682367755"));
    runTest("native bilibili parser accepts bilibili live URL",
            bilibiliParser.canParse("https://live.bilibili.com/12345"));
    runTest("native bilibili parser accepts b23 short URL",
            bilibiliParser.canParse("https://b23.tv/abc123"));

    const QString sampleVideo = ensureSampleVideo();
    if (sampleVideo.isEmpty()) {
        LOG("[FAIL] failed to generate local parser sample");
        return 1;
    }

    PlatformParser parser;
    QEventLoop loop;
    PlatformInfo parsedInfo;
    QString parseError;
    QObject::connect(&parser, &PlatformParser::parseComplete, [&](const PlatformInfo& info) {
        parsedInfo = info;
        loop.quit();
    });
    QObject::connect(&parser, &PlatformParser::parseError, [&](const QString& error) {
        parseError = error;
        loop.quit();
    });

    parser.parseUrl(QUrl::fromLocalFile(sampleVideo).toString());
    QTimer::singleShot(10000, &loop, &QEventLoop::quit);
    loop.exec();

    LOG("");
    LOG("=== Direct Input Parsing Test ===");
    runTest("parse local file without error", parseError.isEmpty());
    runTest("parseComplete - direct platform", "direct", parsedInfo.platform);
    runTest("parseComplete - valid direct input", parsedInfo.isValid);
    runTest("parseComplete - stream URL is local file", sampleVideo, parsedInfo.streamUrl);
    runTest("parseComplete - backup matches primary", parsedInfo.streamUrl, parsedInfo.backupStreamUrl);
    runTest("parseComplete - title is filename", QFileInfo(sampleVideo).fileName(), parsedInfo.title);
    runTest("parseComplete - room id is filename", QFileInfo(sampleVideo).fileName(), parsedInfo.roomId);
    runTest("parseComplete - streamer name is direct", "direct", parsedInfo.streamerName);
    runTest("parseComplete - preferred quality populated", "source", parsedInfo.preferredQuality);
    runTest("parseComplete - qualities include source",
            parsedInfo.availableQualities.contains("source"));
    runTest("parseComplete - stream map contains source",
            parsedInfo.availableStreams.value("source") == sampleVideo);

    LOG("");
    LOG(QString("=== Results: %1/%2 passed, %3 failed ===")
        .arg(g_passCount).arg(g_testCount).arg(g_failCount));
    return g_failCount > 0 ? 1 : 0;
}
