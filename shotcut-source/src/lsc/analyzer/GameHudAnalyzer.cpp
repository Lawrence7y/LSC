#include "GameHudAnalyzer.h"
#include "LscConfig.h"

#include <QFileInfo>
#include <QJsonDocument>
#include <QJsonObject>
#include <QProcess>
#include <QRegularExpression>

GameHudAnalyzer::GameHudAnalyzer(QObject* parent)
    : QObject(parent)
{
}

void GameHudAnalyzer::analyze(const QString& videoPath, const QString& gameKey)
{
    if (m_running) {
        return;
    }

    if (!QFileInfo::exists(videoPath)) {
        emit errorOccurred(QStringLiteral("File not found: %1").arg(videoPath));
        return;
    }

    m_events.clear();
    m_gameKey = gameKey;
    m_running = true;

    generateEventsFromVideoAnalysis(videoPath);
}

void GameHudAnalyzer::cancel()
{
    m_running = false;
}

void GameHudAnalyzer::generateEventsFromVideoAnalysis(const QString& videoPath)
{
    // Get video duration first
    QProcess probe;
    probe.start(lsc::LscConfig::instance().ffprobeProgram(),
                {QStringLiteral("-v"), QStringLiteral("quiet"),
                 QStringLiteral("-print_format"), QStringLiteral("json"),
                 QStringLiteral("-show_format"), videoPath});

    if (!probe.waitForFinished(30000)) {
        m_running = false;
        emit errorOccurred(QStringLiteral("Failed to probe video duration"));
        return;
    }

    const QJsonDocument document = QJsonDocument::fromJson(probe.readAllStandardOutput());
    const double duration =
        document.object().value(QStringLiteral("format")).toObject()
            .value(QStringLiteral("duration")).toString().toDouble();

    if (duration <= 0.0) {
        m_running = false;
        emit errorOccurred(QStringLiteral("Invalid video duration"));
        return;
    }

    // Use scene change detection to find round boundaries.
    // In competitive FPS games, round transitions typically cause large scene changes
    // (scoreboard, round end animation, buy phase screen).
    QProcess sceneProbe;
    const double sceneThreshold = lsc::LscConfig::instance().sceneChangeThreshold;
    sceneProbe.start(lsc::LscConfig::instance().ffmpegProgram(), {
        "-hide_banner", "-i", videoPath,
        "-vf", QString("select='gt(scene,%1)',metadata=print").arg(sceneThreshold),
        "-an", "-sn", "-f", "null", "NUL"
    });

    if (!sceneProbe.waitForFinished(120000)) {
        m_running = false;
        emit errorOccurred("Scene analysis timed out");
        return;
    }

    const QString output = QString::fromUtf8(sceneProbe.readAllStandardError());
    const QRegularExpression tsRegex(R"(pts_time:([\d.]+))");
    const QRegularExpression scoreRegex(R"(lavfi\.scene_score=([\d.]+))");

    struct ScenePoint {
        double timeSec;
        double score;
    };
    QVector<ScenePoint> scenePoints;

    const QStringList lines = output.split('\n', Qt::SkipEmptyParts);
    double currentTime = -1.0;
    for (const QString& line : lines) {
        const auto tsMatch = tsRegex.match(line);
        if (tsMatch.hasMatch()) {
            currentTime = tsMatch.captured(1).toDouble();
            continue;
        }
        const auto scoreMatch = scoreRegex.match(line);
        if (scoreMatch.hasMatch() && currentTime >= 0.0) {
            scenePoints.append({currentTime, scoreMatch.captured(1).toDouble()});
            currentTime = -1.0;
        }
    }

    if (scenePoints.isEmpty()) {
        // Fallback: generate events based on estimated round duration
        const double roundDuration = 110.0;
        const double buyPhaseDuration = 30.0;
        for (double t = 0.0; t < duration - 1.0; t += roundDuration) {
            m_events.append({t, QStringLiteral("buy_phase"),
                             QStringLiteral("Buy Phase"), 0.50});
            const double roundEnd = qMin(duration, t + buyPhaseDuration + 70.0);
            if (roundEnd > t + 5.0) {
                m_events.append({roundEnd, QStringLiteral("round_end"),
                                 QStringLiteral("Round End"), 0.50});
            }
        }
    } else {
        // Cluster scene changes into round boundaries.
        // A cluster of high-score scene changes within a short window = round transition.
        const double clusterWindow = 8.0;
        const double minClusterGap = 30.0;  // Minimum gap between rounds

        QVector<double> clusterCenters;
        double clusterStart = scenePoints.first().timeSec;
        double clusterEnd = scenePoints.first().timeSec;
        double clusterMaxScore = scenePoints.first().score;

        for (int i = 1; i < scenePoints.size(); ++i) {
            const double gap = scenePoints[i].timeSec - clusterEnd;
            if (gap > clusterWindow) {
                // Flush cluster
                if (clusterMaxScore >= 0.3) {
                    clusterCenters.append((clusterStart + clusterEnd) / 2.0);
                }
                clusterStart = scenePoints[i].timeSec;
                clusterEnd = scenePoints[i].timeSec;
                clusterMaxScore = scenePoints[i].score;
            } else {
                clusterEnd = scenePoints[i].timeSec;
                clusterMaxScore = qMax(clusterMaxScore, scenePoints[i].score);
            }
        }
        if (clusterMaxScore >= 0.3) {
            clusterCenters.append((clusterStart + clusterEnd) / 2.0);
        }

        // Filter clusters that are too close together
        QVector<double> filteredCenters;
        for (double center : clusterCenters) {
            if (filteredCenters.isEmpty() || center - filteredCenters.last() >= minClusterGap) {
                filteredCenters.append(center);
            }
        }

        // Generate buy_phase and round_end events from cluster centers
        for (int i = 0; i < filteredCenters.size(); ++i) {
            const double center = filteredCenters[i];
            // Buy phase is typically ~30s before round end
            const double buyPhase = qMax(0.0, center - 30.0);
            m_events.append({buyPhase, QStringLiteral("buy_phase"),
                             QStringLiteral("Buy Phase"), 0.70});
            m_events.append({center, QStringLiteral("round_end"),
                             QStringLiteral("Round End"), 0.75});
        }
    }

    m_running = false;
    emit progressChanged(100);
    emit finished();
}
