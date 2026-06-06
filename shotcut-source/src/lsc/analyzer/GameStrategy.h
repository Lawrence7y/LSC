#ifndef GAMESTRATEGY_H
#define GAMESTRATEGY_H

#include "IHighlightStrategy.h"
#include "AudioAnalyzer.h"
#include "VideoAnalyzer.h"
#include "RoundBoundaryDetector.h"
#include "GameHudAnalyzer.h"

struct GameTemplate {
    QString gameName;
    double sceneChangeThreshold;
    double audioBurstThresholdDb;
    double audioBurstFreqLow;
    double audioBurstFreqHigh;
    double minRoundSec;
    double maxRoundSec;
    double preRoundSilenceSec;
};

class GameStrategy : public IHighlightStrategy
{
    Q_OBJECT
public:
    explicit GameStrategy(QObject* parent = nullptr);

    QString name() const override;
    QString description() const override;
    void analyze(const QString& videoPath) override;
    void cancel() override;
    bool isRunning() const override;
    HighlightResult result() const override;
    void configure(const QJsonObject& params) override;

private slots:
    void onAudioFinished();
    void onVideoFinished();
    void onHudFinished();
    void detectRounds();

private:
    GameTemplate* selectTemplate(const QString& gameHint);
    void detectGenericRounds();
    void detectFpsRounds();

    AudioAnalyzer* m_audioAnalyzer;
    VideoAnalyzer* m_videoAnalyzer;
    GameHudAnalyzer* m_hudAnalyzer;
    GameTemplate m_template;
    RoundBoundaryDetector m_roundDetector;

    QVector<AudioSegment> m_audioSegments;
    QVector<SceneChange> m_sceneChanges;
    QVector<MotionSegment> m_motionSegments;
    QVector<HudEvent> m_hudEvents;

    QVector<HighlightSegment> m_segments;
    HighlightResult m_result;

    int m_completed = 0;
    int m_expected = 2;
    QString m_gameHint;
    QString m_segmentMode = QStringLiteral("round");
    double m_sensitivity = 0.5;
};

#endif
