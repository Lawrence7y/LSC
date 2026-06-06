#ifndef VIDEOANALYZER_H
#define VIDEOANALYZER_H

#include <QObject>
#include <QProcess>
#include <QVector>

struct SceneChange {
    double timestampSec;
    double score;
};

struct MotionSegment {
    double startSec;
    double endSec;
    double motionLevel;
};

class VideoAnalyzer : public QObject
{
    Q_OBJECT
public:
    explicit VideoAnalyzer(QObject* parent = nullptr);
    void analyze(const QString& videoPath);
    void cancel();
    bool isRunning() const;
    const QVector<SceneChange>& sceneChanges() const { return m_sceneChanges; }
    const QVector<MotionSegment>& motionSegments() const { return m_motionSegments; }
    double averageMotion() const { return m_averageMotion; }

signals:
    void progressChanged(int percent);
    void finished();
    void errorOccurred(const QString& error);

private:
    void parseSceneChanges(const QByteArray& output);
    void parseMotionData(const QByteArray& output);

    QProcess* m_sceneProcess;
    QProcess* m_motionProcess;
    QVector<SceneChange> m_sceneChanges;
    QVector<MotionSegment> m_motionSegments;
    double m_averageMotion = 0.0;
    int m_runningCount = 0;
    int m_finishedCount = 0;
};

#endif
