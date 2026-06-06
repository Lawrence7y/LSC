#ifndef IHIGHLIGHTSTRATEGY_H
#define IHIGHLIGHTSTRATEGY_H

#include <QObject>
#include <QString>
#include <QVector>
#include <QJsonObject>

struct HighlightSegment {
    double startSec;
    double endSec;
    double score;
    double audioScore;
    double videoScore;
    double speechScore;
    QString reason;
    QStringList keywords;
};

struct HighlightResult {
    QVector<HighlightSegment> segments;
    QString strategyName;
    QJsonObject metadata;
};

class IHighlightStrategy : public QObject
{
    Q_OBJECT
public:
    explicit IHighlightStrategy(QObject* parent = nullptr) : QObject(parent) {}
    virtual ~IHighlightStrategy() = default;

    virtual QString name() const = 0;
    virtual QString description() const = 0;
    virtual void analyze(const QString& videoPath) = 0;
    virtual void cancel() = 0;
    virtual bool isRunning() const = 0;
    virtual HighlightResult result() const = 0;
    virtual void configure(const QJsonObject& params) = 0;

signals:
    void progressChanged(int percent);
    void segmentFound(const HighlightSegment& segment);
    void finished();
    void errorOccurred(const QString& message);
};

#endif
