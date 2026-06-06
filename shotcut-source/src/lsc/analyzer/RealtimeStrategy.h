// shotcut-source/src/lsc/analyzer/RealtimeStrategy.h
#ifndef REALTIMESTRATEGY_H
#define REALTIMESTRATEGY_H

#include "AudioAnalyzer.h"
#include "IHighlightStrategy.h"
#include "VideoAnalyzer.h"

class RealtimeStrategy : public IHighlightStrategy
{
    Q_OBJECT
public:
    explicit RealtimeStrategy(QObject* parent = nullptr);

    QString name() const override { return QStringLiteral("realtime"); }
    QString description() const override { return QStringLiteral("Low-cost realtime highlight scan"); }
    void analyze(const QString& videoPath) override;
    void cancel() override;
    bool isRunning() const override;
    HighlightResult result() const override { return m_result; }
    void configure(const QJsonObject& params) override { m_params = params; }

    // Signal accumulation for MaterialClassifier — call after finished().
    double voicePresence() const;
    double combatDensity() const;
    double burstReactionRate() const;

private slots:
    void onAudioFinished();
    void onVideoFinished();

private:
    void flushRealtimeSegments();

    AudioAnalyzer* m_audioAnalyzer = nullptr;
    VideoAnalyzer* m_videoAnalyzer = nullptr;
    QVector<AudioSegment> m_audioSegments;
    QVector<MotionSegment> m_motionSegments;
    QVector<SceneChange> m_sceneChanges;
    HighlightResult m_result;
    QJsonObject m_params;
    int m_pendingParts = 0;
    double m_totalDurationSec = 0.0;
};

#endif // REALTIMESTRATEGY_H
