#include "GameStrategy.h"

#include <QFileInfo>
#include <algorithm>

namespace {
GameTemplate makeDefaultTemplate()
{
    return {
        QStringLiteral("generic"),
        0.25,
        -12.0,
        80.0,
        3000.0,
        8.0,
        45.0,
        1.5,
    };
}
}

GameStrategy::GameStrategy(QObject* parent)
    : IHighlightStrategy(parent)
    , m_audioAnalyzer(new AudioAnalyzer(this))
    , m_videoAnalyzer(new VideoAnalyzer(this))
    , m_hudAnalyzer(new GameHudAnalyzer(this))
    , m_template(makeDefaultTemplate())
{
    connect(m_audioAnalyzer, &AudioAnalyzer::finished, this, &GameStrategy::onAudioFinished);
    connect(m_videoAnalyzer, &VideoAnalyzer::finished, this, &GameStrategy::onVideoFinished);
    connect(m_hudAnalyzer, &GameHudAnalyzer::finished, this, &GameStrategy::onHudFinished);

    connect(m_audioAnalyzer, &AudioAnalyzer::errorOccurred, this, &GameStrategy::errorOccurred);
    connect(m_videoAnalyzer, &VideoAnalyzer::errorOccurred, this, &GameStrategy::errorOccurred);
    connect(m_hudAnalyzer, &GameHudAnalyzer::errorOccurred, this, &GameStrategy::errorOccurred);
}

QString GameStrategy::name() const
{
    return QStringLiteral("game");
}

QString GameStrategy::description() const
{
    return QStringLiteral("Game highlight detection with round-aware segmentation");
}

void GameStrategy::analyze(const QString& videoPath)
{
    if (!QFileInfo::exists(videoPath)) {
        emit errorOccurred(QStringLiteral("File not found: %1").arg(videoPath));
        return;
    }

    m_segments.clear();
    m_audioSegments.clear();
    m_sceneChanges.clear();
    m_motionSegments.clear();
    m_hudEvents.clear();
    m_result = HighlightResult{{}, QStringLiteral("game"), {}};
    m_completed = 0;
    m_expected = m_gameHint.contains(QStringLiteral("valorant"), Qt::CaseInsensitive) ? 3 : 2;

    m_audioAnalyzer->analyze(videoPath);
    m_videoAnalyzer->analyze(videoPath);
    if (m_expected == 3) {
        m_hudAnalyzer->analyze(videoPath, m_gameHint);
    }
}

void GameStrategy::cancel()
{
    m_audioAnalyzer->cancel();
    m_videoAnalyzer->cancel();
    m_hudAnalyzer->cancel();
}

bool GameStrategy::isRunning() const
{
    return m_audioAnalyzer->isRunning()
        || m_videoAnalyzer->isRunning()
        || m_hudAnalyzer->isRunning();
}

HighlightResult GameStrategy::result() const
{
    return m_result;
}

void GameStrategy::configure(const QJsonObject& params)
{
    if (params.contains(QStringLiteral("gameHint"))) {
        m_gameHint = params.value(QStringLiteral("gameHint")).toString();
    }
    if (params.contains(QStringLiteral("sensitivity"))) {
        m_sensitivity = qBound(0.1, params.value(QStringLiteral("sensitivity")).toDouble(), 1.0);
    }
    if (params.contains(QStringLiteral("segmentMode"))) {
        m_segmentMode = params.value(QStringLiteral("segmentMode")).toString();
    }

    if (GameTemplate* custom = selectTemplate(m_gameHint)) {
        m_template = *custom;
    } else {
        m_template = makeDefaultTemplate();
    }
}

void GameStrategy::onAudioFinished()
{
    m_audioSegments = m_audioAnalyzer->segments();
    ++m_completed;
    if (m_completed >= m_expected) {
        detectRounds();
    }
}

void GameStrategy::onVideoFinished()
{
    m_sceneChanges = m_videoAnalyzer->sceneChanges();
    m_motionSegments = m_videoAnalyzer->motionSegments();
    ++m_completed;
    if (m_completed >= m_expected) {
        detectRounds();
    }
}

void GameStrategy::onHudFinished()
{
    m_hudEvents = m_hudAnalyzer->events();
    ++m_completed;
    if (m_completed >= m_expected) {
        detectRounds();
    }
}

void GameStrategy::detectRounds()
{
    int roundsDetected = 0;

    if (m_gameHint.contains(QStringLiteral("valorant"), Qt::CaseInsensitive)) {
        const QVector<RoundSegment> rounds = m_roundDetector.buildRounds(m_hudEvents);
        roundsDetected = rounds.size();

        if (m_segmentMode != QStringLiteral("kill")) {
            for (const RoundSegment& round : rounds) {
                HighlightSegment segment;
                segment.startSec = round.startSec;
                segment.endSec = round.endSec;
                segment.audioScore = 0.6;
                segment.videoScore = 0.7;
                segment.speechScore = 0.0;
                segment.score = qBound(0.0, 0.70 + (m_sensitivity * 0.15), 1.0);
                segment.reason = QStringLiteral("回合片段: %1").arg(round.title);
                m_segments.append(segment);
                emit segmentFound(segment);
            }
        }

        if (m_segmentMode == QStringLiteral("kill") || m_segmentMode == QStringLiteral("smart")) {
            detectFpsRounds();
        }

        if (m_segments.isEmpty()) {
            double startSec = 0.0;
            double endSec = 0.0;

            if (!m_audioSegments.isEmpty()) {
                endSec = qMax(endSec, m_audioSegments.last().endSec);
            }
            if (!m_motionSegments.isEmpty()) {
                endSec = qMax(endSec, m_motionSegments.last().endSec);
            }
            if (!m_sceneChanges.isEmpty()) {
                endSec = qMax(endSec, m_sceneChanges.last().timestampSec);
            }
            if (endSec <= startSec) {
                endSec = qMax(8.0, m_template.minRoundSec);
            }

            HighlightSegment segment;
            segment.startSec = startSec;
            segment.endSec = endSec;
            segment.audioScore = 0.55;
            segment.videoScore = 0.65;
            segment.speechScore = 0.0;
            segment.score = qBound(0.0, 0.65 + (m_sensitivity * 0.15), 1.0);
            segment.reason = QStringLiteral("回合片段: 估计分段");
            m_segments.append(segment);
            emit segmentFound(segment);
            roundsDetected = qMax(roundsDetected, 1);
        }
    } else if (m_gameHint.contains(QStringLiteral("fps"), Qt::CaseInsensitive)
               || m_gameHint.contains(QStringLiteral("shoot"), Qt::CaseInsensitive)) {
        detectFpsRounds();
    } else {
        detectGenericRounds();
    }

    m_result.segments = m_segments;
    m_result.metadata.insert(QStringLiteral("segments"), static_cast<int>(m_segments.size()));
    m_result.metadata.insert(QStringLiteral("roundsDetected"), roundsDetected);
    m_result.metadata.insert(QStringLiteral("template"), m_template.gameName);
    m_result.metadata.insert(QStringLiteral("segmentMode"), m_segmentMode);
    m_result.metadata.insert(QStringLiteral("sensitivity"), m_sensitivity);
    emit finished();
}

GameTemplate* GameStrategy::selectTemplate(const QString& gameHint)
{
    static GameTemplate fpsTemplate{
        QStringLiteral("fps"),
        0.18,    // sceneChangeThreshold
        -15.0,   // audioBurstThresholdDb
        120.0,   // audioBurstFreqLow
        5000.0,  // audioBurstFreqHigh
        45.0,    // minRoundSec (was 6.0)
        120.0,   // maxRoundSec (was 30.0)
        1.0,     // preRoundSilenceSec
    };

    if (gameHint.contains(QStringLiteral("valorant"), Qt::CaseInsensitive)
        || gameHint.contains(QStringLiteral("fps"), Qt::CaseInsensitive)
        || gameHint.contains(QStringLiteral("shoot"), Qt::CaseInsensitive)) {
        return &fpsTemplate;
    }

    return nullptr;
}

void GameStrategy::detectGenericRounds()
{
    const double scoreThreshold = 0.55 - (m_sensitivity * 0.30);

    for (const AudioSegment& audio : std::as_const(m_audioSegments)) {
        double sceneScore = 0.0;
        double motionScore = 0.0;

        for (const SceneChange& change : std::as_const(m_sceneChanges)) {
            if (change.timestampSec >= audio.startSec && change.timestampSec <= audio.endSec) {
                sceneScore = qMax(sceneScore, change.score);
            }
        }

        for (const MotionSegment& motion : std::as_const(m_motionSegments)) {
            if (motion.endSec < audio.startSec || motion.startSec > audio.endSec) {
                continue;
            }
            motionScore = qMax(motionScore, qBound(0.0, motion.motionLevel / 0.35, 1.0));
        }

        const double audioScore =
            qBound(0.0, (audio.maxDb - m_template.audioBurstThresholdDb) / 20.0, 1.0);
        const double score =
            qBound(0.0, audioScore * 0.35 + sceneScore * 0.35 + motionScore * 0.30, 1.0);
        if (score < scoreThreshold) {
            continue;
        }

        HighlightSegment segment;
        segment.startSec = audio.startSec;
        segment.endSec = audio.endSec;
        segment.audioScore = audioScore;
        segment.videoScore = qMax(sceneScore, motionScore);
        segment.score = score;
        segment.reason = QStringLiteral("游戏高光: audio=%1 scene=%2 motion=%3")
                             .arg(audioScore, 0, 'f', 2)
                             .arg(sceneScore, 0, 'f', 2)
                             .arg(motionScore, 0, 'f', 2);
        m_segments.append(segment);
        emit segmentFound(segment);
    }
}

void GameStrategy::detectFpsRounds()
{
    struct Anchor {
        double timeSec = 0.0;
        double sceneScore = 0.0;
        double motionScore = 0.0;
    };

    QVector<Anchor> anchors;
    anchors.reserve(m_sceneChanges.size() + m_motionSegments.size());

    for (const SceneChange& change : std::as_const(m_sceneChanges)) {
        if (change.score >= m_template.sceneChangeThreshold) {
            anchors.append({change.timestampSec, change.score, 0.0});
        }
    }
    for (const MotionSegment& motion : std::as_const(m_motionSegments)) {
        if (motion.motionLevel < 0.20) {
            continue;
        }
        anchors.append(
            {(motion.startSec + motion.endSec) * 0.5, 0.0, qBound(0.0, motion.motionLevel, 1.0)});
    }

    if (anchors.isEmpty()) {
        detectGenericRounds();
        return;
    }

    std::sort(anchors.begin(), anchors.end(), [](const Anchor& a, const Anchor& b) {
        return a.timeSec < b.timeSec;
    });

    const double mergeGapSec =
        qMax(1.5, m_template.preRoundSilenceSec + (1.0 - m_sensitivity) * 2.0);
    const double scoreThreshold = 0.45 - (m_sensitivity * 0.20);

    double clusterStart = anchors.first().timeSec;
    double clusterEnd = anchors.first().timeSec;
    double clusterScene = anchors.first().sceneScore;
    double clusterMotion = anchors.first().motionScore;

    auto flushCluster = [&, this]() {
        const double startSec = qMax(0.0, clusterStart - m_template.preRoundSilenceSec);
        double endSec = qMax(clusterEnd + 2.0, startSec + m_template.minRoundSec);
        endSec = qMin(endSec, startSec + m_template.maxRoundSec);

        double audioScore = 0.0;
        for (const AudioSegment& audio : std::as_const(m_audioSegments)) {
            if (audio.endSec < startSec || audio.startSec > endSec) {
                continue;
            }
            audioScore = qMax(audioScore,
                              qBound(0.0,
                                     (audio.maxDb - m_template.audioBurstThresholdDb) / 20.0,
                                     1.0));
        }

        const double videoScore = qMax(clusterScene, clusterMotion);
        const double score =
            qBound(0.0, audioScore * 0.25 + clusterScene * 0.35 + clusterMotion * 0.40, 1.0);
        if (score < scoreThreshold) {
            return;
        }

        HighlightSegment segment;
        segment.startSec = startSec;
        segment.endSec = endSec;
        segment.audioScore = audioScore;
        segment.videoScore = videoScore;
        segment.score = score;
        segment.reason = QStringLiteral("FPS 团战/击杀爆发: scene=%1 motion=%2 audio=%3")
                             .arg(clusterScene, 0, 'f', 2)
                             .arg(clusterMotion, 0, 'f', 2)
                             .arg(audioScore, 0, 'f', 2);
        m_segments.append(segment);
        emit segmentFound(segment);
    };

    for (int i = 1; i < anchors.size(); ++i) {
        const Anchor& anchor = anchors[i];
        if (anchor.timeSec - clusterEnd > mergeGapSec) {
            flushCluster();
            clusterStart = anchor.timeSec;
            clusterEnd = anchor.timeSec;
            clusterScene = anchor.sceneScore;
            clusterMotion = anchor.motionScore;
            continue;
        }

        clusterEnd = anchor.timeSec;
        clusterScene = qMax(clusterScene, anchor.sceneScore);
        clusterMotion = qMax(clusterMotion, anchor.motionScore);
    }

    flushCluster();

    if (m_segments.isEmpty()) {
        detectGenericRounds();
    }
}
