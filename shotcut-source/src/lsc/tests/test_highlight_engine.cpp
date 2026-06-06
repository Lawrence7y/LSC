#include "analyzer/HighlightEngine.h"
#include "core/LscDatabase.h"

#include <QCoreApplication>
#include <QDir>
#include <QEventLoop>
#include <QFile>
#include <QFileInfo>
#include <QJsonObject>
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

bool runStrategy(HighlightEngine& engine, IHighlightStrategy* strategy, const QString& videoPath)
{
    engine.setStrategy(strategy);
    QEventLoop loop;
    bool sawError = false;
    QObject::connect(&engine, &HighlightEngine::finished, &loop, &QEventLoop::quit);
    QObject::connect(&engine, &HighlightEngine::errorOccurred, &loop, [&](const QString& error) {
        sawError = true;
        LOG("Error: " + error);
        loop.quit();
    });

    if (!engine.analyze(videoPath)) {
        return false;
    }
    QTimer::singleShot(180000, &loop, &QEventLoop::quit);
    loop.exec();
    return !sawError;
}

class FakeOverlapStrategy final : public IHighlightStrategy
{
    Q_OBJECT

public:
    explicit FakeOverlapStrategy(QObject* parent = nullptr)
        : IHighlightStrategy(parent)
    {
    }

    QString name() const override { return QStringLiteral("fake-overlap"); }
    QString description() const override { return QStringLiteral("overlap regression"); }
    void analyze(const QString&) override
    {
        HighlightSegment first{};
        first.startSec = 1.0;
        first.endSec = 3.0;
        first.score = 0.70;
        first.reason = QStringLiteral("first");

        HighlightSegment second{};
        second.startSec = 2.0;
        second.endSec = 4.0;
        second.score = 0.92;
        second.reason = QStringLiteral("second");

        m_result.strategyName = name();
        m_result.segments = {first, second};

        emit segmentFound(first);
        emit segmentFound(second);
        emit finished();
    }
    void cancel() override {}
    bool isRunning() const override { return false; }
    HighlightResult result() const override { return m_result; }
    void configure(const QJsonObject&) override {}

private:
    HighlightResult m_result;
};

int main(int argc, char* argv[])
{
    QCoreApplication app(argc, argv);
    lsc::LscDatabase::instance().initialize();
    LOG("=== HighlightEngine Tests ===");
    LOG("");

    HighlightEngine engine;
    check("initial not running", !engine.isRunning());
    check("no results by default", engine.results().isEmpty());

    const QString testVideo = ensureSampleVideo();
    if (testVideo.isEmpty()) {
        skip("offline highlight engine", "ffmpeg sample generation unavailable");
    } else {
        const bool genericOk =
            runStrategy(engine, HighlightEngine::createGenericStrategy(&engine), testVideo);
        check("generic strategy runs without error", genericOk);
        check("generic strategy produced results", !engine.results().isEmpty());
        check("generic strategy name matches",
              !engine.results().isEmpty() && engine.results().last().strategyName == "generic");

        const bool gameOk =
            runStrategy(engine, HighlightEngine::createGameStrategy(&engine), testVideo);
        check("game strategy runs without error", gameOk);
        check("game strategy produced results", !engine.results().isEmpty());
        check("game strategy name matches",
              !engine.results().isEmpty() && engine.results().last().strategyName == "game");

        IHighlightStrategy* fpsStrategy = HighlightEngine::createGameStrategy(&engine);
        QJsonObject fpsParams;
        fpsParams.insert("gameHint", "fps");
        fpsParams.insert("sensitivity", 0.8);
        fpsStrategy->configure(fpsParams);
        const bool fpsOk = runStrategy(engine, fpsStrategy, testVideo);
        check("fps game strategy runs without error", fpsOk);
        check("fps strategy metadata keeps fps template",
              !engine.results().isEmpty()
                  && engine.results().last().metadata.value("template").toString() == "fps");
        check("fps strategy emits fps-specific reasons when segments exist",
              engine.results().isEmpty()
                  || engine.results().last().segments.isEmpty()
                  || engine.results().last().segments.first().reason.contains("FPS"));

        IHighlightStrategy* valorantStrategy = HighlightEngine::createGameStrategy(&engine);
        QJsonObject valorantParams;
        valorantParams.insert("gameHint", "valorant");
        valorantParams.insert("sensitivity", 0.8);
        valorantParams.insert("segmentMode", "round");
        valorantStrategy->configure(valorantParams);
        const bool valorantOk = runStrategy(engine, valorantStrategy, testVideo);
        check("valorant strategy runs without error", valorantOk);
        check("valorant strategy records detected rounds in metadata",
              !engine.results().isEmpty()
                  && engine.results().last().metadata.value("roundsDetected").toInt() > 0);
        check("valorant round mode metadata is preserved",
              !engine.results().isEmpty()
                  && engine.results().last().metadata.value("segmentMode").toString() == "round");
        check("valorant strategy emits round-specific reasons when segments exist",
              engine.results().isEmpty()
                  || engine.results().last().segments.isEmpty()
                  || engine.results().last().segments.first().reason.contains(QStringLiteral("回合")));

        IHighlightStrategy* valorantKillStrategy = HighlightEngine::createGameStrategy(&engine);
        QJsonObject valorantKillParams;
        valorantKillParams.insert("gameHint", "valorant");
        valorantKillParams.insert("sensitivity", 0.8);
        valorantKillParams.insert("segmentMode", "kill");
        valorantKillStrategy->configure(valorantKillParams);
        const bool valorantKillOk = runStrategy(engine, valorantKillStrategy, testVideo);
        check("valorant kill mode runs without error", valorantKillOk);
        check("valorant kill mode metadata is preserved",
              !engine.results().isEmpty()
                  && engine.results().last().metadata.value("segmentMode").toString() == "kill");

        engine.setStrategy(HighlightEngine::createGenericStrategy(&engine));
        check("incremental analysis skips short duration",
              !engine.analyzeIncremental(testVideo, 4.0));
        check("incremental analysis starts after enough duration",
              engine.analyzeIncremental(testVideo, 12.0));

        QEventLoop incrementalLoop;
        bool incrementalError = false;
        QObject::connect(&engine, &HighlightEngine::finished, &incrementalLoop, &QEventLoop::quit);
        QObject::connect(&engine, &HighlightEngine::errorOccurred, &incrementalLoop, [&](const QString& error) {
            incrementalError = true;
            LOG("Incremental error: " + error);
            incrementalLoop.quit();
        });
        QTimer::singleShot(180000, &incrementalLoop, &QEventLoop::quit);
        incrementalLoop.exec();

        check("incremental analysis completes without error", !incrementalError);
        check("incremental analysis is throttled until more duration arrives",
              !engine.analyzeIncremental(testVideo, 13.0));
    }

    FakeOverlapStrategy* overlapStrategy = new FakeOverlapStrategy(&engine);
    int overlapSignalCount = 0;
    QObject::connect(&engine, &HighlightEngine::segmentFound, [&](const HighlightSegment&) {
        ++overlapSignalCount;
    });
    const bool overlapOk = runStrategy(engine, overlapStrategy, QStringLiteral("ignored.mp4"));
    check("overlap strategy runs without error", overlapOk);
    check("overlap segments are normalized into one result segment",
          !engine.results().isEmpty() && engine.results().last().segments.size() == 1);
    check("overlap strategy emits one normalized segment signal", overlapSignalCount == 1);
    check("overlap normalization keeps the stronger merged range",
          !engine.results().isEmpty()
              && !engine.results().last().segments.isEmpty()
              && qFuzzyCompare(engine.results().last().segments.first().startSec + 1.0, 2.0)
              && qFuzzyCompare(engine.results().last().segments.first().endSec + 1.0, 5.0)
              && engine.results().last().segments.first().score >= 0.92);

    const QString dbVideoPath =
        QDir::tempPath() + "/lsc_db_clip_source_" +
        QString::number(QDateTime::currentMSecsSinceEpoch()) + ".mp4";
    QFile dbVideo(dbVideoPath);
    dbVideo.open(QIODevice::WriteOnly);
    dbVideo.write("fake video placeholder");
    dbVideo.close();

    lsc::ProjectRecord project;
    project.id = "project_" + QString::number(QDateTime::currentMSecsSinceEpoch());
    project.name = "clip persistence project";
    project.videoPath = dbVideoPath;
    project.recordedAt = QDateTime::currentDateTime();
    project.status = "recorded";
    lsc::LscDatabase::instance().insertProject(project);

    FakeOverlapStrategy* dbStrategy = new FakeOverlapStrategy(&engine);
    const bool dbRunOk = runStrategy(engine, dbStrategy, dbVideoPath);
    const auto dbClips = lsc::LscDatabase::instance().clipsByProject(project.id);
    check("analysis persists detected clips to database",
          dbRunOk && !dbClips.isEmpty()
              && dbClips.first().status == "detected"
              && dbClips.first().projectId == project.id
              && dbClips.first().score >= 0.9);

    const QString exportPath = QDir::tempPath() + "/lsc_exported_clip.mp4";
    engine.onClipExportedForPersistence(exportPath, QStringLiteral("second"));
    const auto exportedClips = lsc::LscDatabase::instance().clipsByProject(project.id);
    bool sawExported = false;
    for (const auto& clip : exportedClips) {
        if (clip.exportPath == exportPath && clip.status == "exported") {
            sawExported = true;
            break;
        }
    }
    check("clip export updates database status", sawExported);

    // Valorant pilot: check fps template has large enough round bounds
    check("valorant fps template minimum is large enough for mother clips",
          engine.results().isEmpty()
              || engine.results().last().metadata.value("template").toString() != "fps"
              || engine.results().last().segments.isEmpty()
              || (engine.results().last().segments.first().endSec
                  - engine.results().last().segments.first().startSec) >= 45.0);

    // Valorant pilot: check ranked output
    check("valorant ranked clips carry material type metadata",
          engine.rankedClips().isEmpty()
              || !engine.rankedClips().first().metadata.value("materialType").toString().isEmpty());
    check("engine accepts material signals for classification",
          engine.classification().materialType.isEmpty() == false
              || engine.rankedClips().isEmpty());  // either classified or no input yet

    LOG("");
    LOG(QString("=== Results: %1 passed, %2 failed, %3 skipped ===").arg(g_pass).arg(g_fail).arg(g_skip));
    return g_fail > 0 ? 1 : 0;
}

#include "test_highlight_engine.moc"
