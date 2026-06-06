#ifndef DANCESTRATEGY_H
#define DANCESTRATEGY_H

#include "IHighlightStrategy.h"
#include "BeatDetector.h"
#include "VideoAnalyzer.h"
#include "DanceSegmentScorer.h"
#include "PoseAnalyzer.h"

class DanceStrategy : public IHighlightStrategy
{
    Q_OBJECT
public:
    explicit DanceStrategy(QObject* parent = nullptr);

    QString name() const override;
    QString description() const override;
    void analyze(const QString& videoPath) override;
    void cancel() override;
    bool isRunning() const override;
    HighlightResult result() const override;
    void configure(const QJsonObject& params) override;

private slots:
    void onBeatFinished();
    void onVideoFinished();
    void onPoseFinished();
    void computeCorrelation();

private:
    BeatDetector* m_beatDetector;
    VideoAnalyzer* m_videoAnalyzer;
    PoseAnalyzer* m_poseAnalyzer;

    QVector<BeatInfo> m_beats;
    QVector<SceneChange> m_sceneChanges;
    QVector<MotionSegment> m_motionSegments;
    QVector<PoseWindow> m_poseWindows;
    double m_bpm = 120.0;
    double m_duration = 0.0;

    QVector<HighlightSegment> m_segments;
    HighlightResult m_result;

    int m_completed = 0;
    const int m_expected = 3;
    double m_sensitivity = 0.5;
    int m_minBeats = 4;
    DanceSegmentScorer m_scorer;
};

#endif
