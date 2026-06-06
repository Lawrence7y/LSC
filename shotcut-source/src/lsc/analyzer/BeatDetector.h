#ifndef BEATDETECTOR_H
#define BEATDETECTOR_H

#include <QObject>
#include <QProcess>
#include <QVector>

struct BeatInfo {
    double timestampSec;
    double strength;
};

class BeatDetector : public QObject
{
    Q_OBJECT
public:
    explicit BeatDetector(QObject* parent = nullptr);

    void detect(const QString& audioPath);
    void cancel();
    bool isRunning() const;

    double bpm() const { return m_bpm; }
    const QVector<BeatInfo>& beats() const { return m_beats; }
    bool hasAubio() const { return m_hasAubio; }

    /**
     * @brief Check if aubio is available (non-blocking, uses cached result)
     * @return true if aubio filter was detected during initialization
     */
    bool isAubioAvailable() const { return m_aubioChecked && m_hasAubio; }

signals:
    void finished();
    void errorOccurred(const QString& error);

private slots:
    void onTempoFinished(int exitCode, QProcess::ExitStatus status);
    void onOnsetFinished(int exitCode, QProcess::ExitStatus status);
    void onFallbackFinished(int exitCode, QProcess::ExitStatus status);
    void onAubioProbeFinished(int exitCode, QProcess::ExitStatus status);

private:
    void checkAubioAvailability();
    void runTempoDetection();
    void runOnsetDetection();
    void runRmsFallback();
    void parseTempoOutput();
    void parseOnsetOutput();
    void parseRmsFallback();

    QProcess* m_process;
    QProcess* m_probeProcess;
    QVector<BeatInfo> m_beats;
    double m_bpm = 120.0;
    bool m_hasAubio = false;
    bool m_aubioChecked = false;
    bool m_running = false;
    bool m_probeRunning = false;
    QString m_audioPath;
};

#endif
