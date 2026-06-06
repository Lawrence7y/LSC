#ifndef GENERICSTRATEGY_H
#define GENERICSTRATEGY_H

#include "IHighlightStrategy.h"
#include "AudioAnalyzer.h"
#include "VideoAnalyzer.h"

class GenericStrategy : public IHighlightStrategy
{
    Q_OBJECT
public:
    explicit GenericStrategy(QObject* parent = nullptr);

    QString name() const override;
    QString description() const override;
    void analyze(const QString& videoPath) override;
    void cancel() override;
    bool isRunning() const override;
    HighlightResult result() const override;
    void configure(const QJsonObject& params) override;

private slots:
    void computeHighlights();

private:
    AudioAnalyzer* m_audioAnalyzer;
    VideoAnalyzer* m_videoAnalyzer;
    QVector<HighlightSegment> m_segments;
    HighlightResult m_result;
    int m_completed = 0;
    double m_threshold = 0.2;
};

#endif
