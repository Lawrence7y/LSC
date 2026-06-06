#ifndef DIALOGSTRATEGY_H
#define DIALOGSTRATEGY_H

#include "IHighlightStrategy.h"
#include "SpeechRecognizer.h"
#include "AudioAnalyzer.h"

class DialogStrategy : public IHighlightStrategy
{
    Q_OBJECT
public:
    explicit DialogStrategy(QObject* parent = nullptr);

    QString name() const override;
    QString description() const override;
    void analyze(const QString& videoPath) override;
    void cancel() override;
    bool isRunning() const override;
    HighlightResult result() const override;
    void configure(const QJsonObject& params) override;

private slots:
    void onSpeechFinished();
    void onSpeechError(const QString& error);
    void onSilenceFinished();
    void onSilenceError(const QString& error);
    void computeDialogSegments();

private:
    SpeechRecognizer* m_recognizer;
    AudioAnalyzer* m_audioAnalyzer;

    QVector<SubtitleEntry> m_subtitles;
    QVector<AudioSegment> m_silences;

    QVector<HighlightSegment> m_segments;
    HighlightResult m_result;

    QStringList m_keywords;
    double m_minSegmentSec = 3.0;
    double m_maxSegmentSec = 60.0;
    double m_silenceGapSec = 1.5;
    int m_completed = 0;
    const int m_expected = 2;
    bool m_speechError = false;
    bool m_silenceError = false;
};

#endif
