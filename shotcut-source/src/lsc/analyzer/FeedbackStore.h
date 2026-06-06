// shotcut-source/src/lsc/analyzer/FeedbackStore.h
#ifndef FEEDBACKSTORE_H
#define FEEDBACKSTORE_H

#include <QMap>
#include <QObject>
#include <QString>
#include <QVector>

struct ClipFeedback {
    QString clipId;
    QString action;          // "keep" | "delete" | "adjust_boundary" | "export"
    int importance = 0;      // 0-5
    QString highlightType;   // "多杀" | "残局" | "翻盘" | "解说高能" | "情绪反应" | ""
    double adjustedStartSec = -1.0;
    double adjustedEndSec = -1.0;
};

struct FeedbackStats {
    int totalClips = 0;
    int keptClips = 0;
    int deletedClips = 0;
    int exportedClips = 0;
    double avgBoundaryAdjustment = 0;
    double avgUserRating = 0;
    QMap<QString, int> highlightTypeCounts;
    QMap<QString, int> actionCounts;
};

class FeedbackStore : public QObject
{
    Q_OBJECT

public:
    explicit FeedbackStore(QObject* parent = nullptr);

    bool save(const QString& filePath, const QVector<ClipFeedback>& feedback) const;
    QVector<ClipFeedback> load(const QString& filePath) const;

    FeedbackStats statsForProject(const QString& videoPath) const;
    FeedbackStats globalStats() const;
    void exportStatsReport(const QString& outputPath) const;

signals:
    void statsUpdated();

private:
    static FeedbackStats computeStats(const QVector<ClipFeedback>& feedback);
};

#endif // FEEDBACKSTORE_H
