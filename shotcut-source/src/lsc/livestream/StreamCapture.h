#ifndef STREAMCAPTURE_H
#define STREAMCAPTURE_H

#include <QElapsedTimer>
#include <QObject>
#include <QProcess>
#include <QTimer>

enum class EncodeMode {
    StreamCopy,
    CRF,
    TargetBitrate,
    Hardware
};

struct RecordingConfig {
    QString outputPath;
    QString format = "mp4";
    QString sourceQuality = "best";
    EncodeMode encodeMode = EncodeMode::CRF;
    int crf = 23;
    QString preset = "medium";
    int videoBitrate = 4000;
    int audioBitrate = 128;
    QString hwEncoder = "auto";
    int maxWidth = 0;
    int maxHeight = 0;
    bool autoReconnect = true;
    int reconnectRetries = 5;
    int reconnectDelayMs = 3000;
    int maxReconnectDelayMs = 30000;
    int stallTimeoutSec = 30;
    int maxDurationSec = 0;
};

enum class RecordingStatus {
    Stopped,
    Starting,
    Recording,
    Reconnecting,
    Error
};

class StreamCapture : public QObject
{
    Q_OBJECT
public:
    explicit StreamCapture(QObject* parent = nullptr);
    ~StreamCapture();

    bool start(const QString& streamUrl, const RecordingConfig& config);
    void stop();

    RecordingStatus status() const { return m_status; }
    qint64 duration() const;
    qint64 fileSize() const;
    const RecordingConfig& config() const { return m_config; }
    QString currentStreamUrl() const { return m_currentStreamUrl; }

    /**
     * @brief Build encoder arguments for testing purposes
     * @param streamUrl The stream URL
     * @param config The recording configuration
     * @return The FFmpeg arguments that would be used
     *
     * This is a pure function that doesn't modify any state.
     */
    static QStringList buildEncoderArgsStatic(const QString& streamUrl, const RecordingConfig& config);

signals:
    void statusChanged(RecordingStatus status);
    void progressUpdated(qint64 durationMs, qint64 fileSizeBytes);
    void errorOccurred(const QString& error);
    void streamStalled();
    void needsReconnect(QString lastUrl);

private slots:
    void onFfmpegStarted();
    void onFfmpegError(QProcess::ProcessError error);
    void onFfmpegFinished(int exitCode, QProcess::ExitStatus exitStatus);
    void onFfmpegReadyRead();
    void updateProgress();
    void checkStall();
    void attemptReconnect();

private:
    QStringList buildEncoderArgs(const QString& streamUrl) const;
    void startFfmpeg(const QString& streamUrl);
    void setStatus(RecordingStatus status);
    void cleanupTimers();
    bool detectHardwareEncoder();

    QProcess* m_ffmpegProcess;
    RecordingConfig m_config;
    RecordingStatus m_status;
    QElapsedTimer m_elapsed;
    QTimer m_progressTimer;
    QTimer m_stallTimer;
    int m_reconnectCount;
    QString m_currentStreamUrl;
    qint64 m_lastFileSize;
    qint64 m_lastProgressBytes;
    QString m_ffmpegErrorBuffer;
    QString m_detectedHwEncoder;
    qint64 m_accumulatedDurationMs = 0;
    qint64 m_lastDurationMs = 0;
    bool m_stopRequested = false;
    qint64 m_stallStartMs = 0;
};

#endif // STREAMCAPTURE_H
