#ifndef COMMENTARYSTRATEGY_H
#define COMMENTARYSTRATEGY_H

#include "IHighlightStrategy.h"
#include "SpeechRecognizer.h"
#include "CommentarySegmenter.h"

#include <QJsonObject>
#include <QTimer>

/**
 * @brief 解说切片策略 - 基于语音识别的解说内容切片
 *
 * 通过语音识别获取字幕，然后根据语义和关键词
 * 将连续的解说内容分割成有意义的片段。
 */
class CommentaryStrategy : public IHighlightStrategy
{
    Q_OBJECT
public:
    explicit CommentaryStrategy(QObject* parent = nullptr);

    QString name() const override;
    QString description() const override;
    void analyze(const QString& videoPath) override;
    void cancel() override;
    bool isRunning() const override;
    HighlightResult result() const override;
    void configure(const QJsonObject& params) override;

private slots:
    void onTranscriptionFinished();
    void onTranscriptionError(const QString& error);

private:
    void buildSegmentsFromSubtitles();

    SpeechRecognizer* m_recognizer;
    CommentarySegmenter m_segmenter;
    QVector<SubtitleEntry> m_subtitles;
    QVector<HighlightSegment> m_segments;
    QStringList m_keywords;
    double m_sensitivity = 0.5;
    bool m_running = false;
};

#endif // COMMENTARYSTRATEGY_H
