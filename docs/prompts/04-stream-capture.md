# Task 4: 直播流录制器

## 任务目标

实现基于 FFmpeg 的直播流录制器，支持开始/暂停/停止录制、自动重连、时长和文件大小监控。

## 创建文件

- `src/lsc/livestream/StreamCapture.h`
- `src/lsc/livestream/StreamCapture.cpp`

## 前置条件

- Task 2 已完成（模块目录结构）

## StreamCapture.h

```cpp
#ifndef STREAMCAPTURE_H
#define STREAMCAPTURE_H

#include <QObject>
#include <QProcess>
#include <QString>
#include <QTimer>
#include <QFileInfo>

struct RecordingConfig {
    QString outputPath;
    QString format = "mp4";
    QString codec = "h264";
    int width = 1920;
    int height = 1080;
    int fps = 30;
    int bitrate = 8000;          // kbps
    int audioBitrate = 192;      // kbps
    bool autoReconnect = true;
    int reconnectRetries = 10;
    int reconnectDelay = 5000;   // ms
};

enum class RecordingStatus {
    Stopped, Starting, Recording, Paused, Reconnecting, Error
};

class StreamCapture : public QObject
{
    Q_OBJECT
public:
    explicit StreamCapture(QObject* parent = nullptr);
    ~StreamCapture();

    bool start(const QString& streamUrl, const RecordingConfig& config);
    void pause();
    void resume();
    void stop();

    RecordingStatus status() const { return m_status; }
    qint64 duration() const;
    qint64 fileSize() const;
    const RecordingConfig& config() const { return m_config; }

signals:
    void statusChanged(RecordingStatus status);
    void durationChanged(qint64 ms);
    void errorOccurred(const QString& error);
    void reconnectAttempt(int attempt, int maxAttempts);

private slots:
    void onFfmpegError();
    void onFfmpegFinished(int exitCode, QProcess::ExitStatus exitStatus);
    void updateDuration();
    void attemptReconnect();

private:
    void startFfmpeg(const QString& streamUrl);

    QProcess* m_ffmpegProcess;
    RecordingConfig m_config;
    RecordingStatus m_status;
    qint64 m_startTime;
    QTimer m_durationTimer;
    int m_reconnectCount;
    QString m_currentStreamUrl;
};

#endif
```

## StreamCapture.cpp

```cpp
#include "StreamCapture.h"
#include <QDateTime>
#include <QDebug>
#include <QDir>

StreamCapture::StreamCapture(QObject* parent)
    : QObject(parent)
    , m_ffmpegProcess(new QProcess(this))
    , m_status(RecordingStatus::Stopped)
    , m_startTime(0)
    , m_reconnectCount(0)
{
    connect(m_ffmpegProcess, &QProcess::errorOccurred,
            this, &StreamCapture::onFfmpegError);
    connect(m_ffmpegProcess,
            QOverload<int, QProcess::ExitStatus>::of(&QProcess::finished),
            this, &StreamCapture::onFfmpegFinished);
    connect(&m_durationTimer, &QTimer::timeout,
            this, &StreamCapture::updateDuration);
    m_durationTimer.setInterval(1000);
}

StreamCapture::~StreamCapture() { stop(); }

bool StreamCapture::start(const QString& streamUrl, const RecordingConfig& config)
{
    if (m_status != RecordingStatus::Stopped) {
        emit errorOccurred("Already recording, stop first");
        return false;
    }

    m_config = config;
    m_currentStreamUrl = streamUrl;
    m_reconnectCount = 0;

    QFileInfo fi(m_config.outputPath);
    QDir().mkpath(fi.absolutePath());

    m_status = RecordingStatus::Starting;
    emit statusChanged(m_status);
    startFfmpeg(streamUrl);
    m_status = RecordingStatus::Recording;
    m_startTime = QDateTime::currentMSecsSinceEpoch();
    m_durationTimer.start();
    emit statusChanged(m_status);
    return true;
}

void StreamCapture::pause()
{
    if (m_ffmpegProcess->state() == QProcess::Running) {
        m_status = RecordingStatus::Paused;
        emit statusChanged(m_status);
    }
}

void StreamCapture::resume()
{
    if (m_status == RecordingStatus::Paused) {
        m_status = RecordingStatus::Recording;
        m_durationTimer.start();
        emit statusChanged(m_status);
    }
}

void StreamCapture::stop()
{
    m_durationTimer.stop();
    m_reconnectCount = 0;
    if (m_ffmpegProcess->state() == QProcess::Running) {
        m_ffmpegProcess->write("q");
        if (!m_ffmpegProcess->waitForFinished(5000)) {
            m_ffmpegProcess->kill();
            m_ffmpegProcess->waitForFinished(3000);
        }
    }
    m_status = RecordingStatus::Stopped;
    emit statusChanged(m_status);
}

qint64 StreamCapture::duration() const
{
    if (m_startTime == 0) return 0;
    return QDateTime::currentMSecsSinceEpoch() - m_startTime;
}

qint64 StreamCapture::fileSize() const
{
    QFileInfo fi(m_config.outputPath);
    return fi.exists() ? fi.size() : 0;
}

void StreamCapture::startFfmpeg(const QString& streamUrl)
{
    m_ffmpegProcess->setProgram("ffmpeg");
    QStringList args;
    // 覆盖已有文件，实时输入
    args << "-y" << "-re" << "-i" << streamUrl
         << "-c:v" << "libx264" << "-preset" << "veryfast" << "-crf" << "18"
         << "-r" << QString::number(m_config.fps)
         << "-c:a" << "aac" << "-b:a" << QString("%1k").arg(m_config.audioBitrate);

    // 自动重连
    if (m_config.autoReconnect) {
        args << "-reconnect" << "1"
             << "-reconnect_streamed" << "1"
             << "-reconnect_delay_max"
             << QString::number(m_config.reconnectDelay / 1000);
    }

    args << m_config.outputPath;
    m_ffmpegProcess->setArguments(args);
    m_ffmpegProcess->start();
}

void StreamCapture::onFfmpegError()
{
    QString err = m_ffmpegProcess->errorString();
    if (m_config.autoReconnect && m_reconnectCount < m_config.reconnectRetries) {
        m_status = RecordingStatus::Reconnecting;
        emit statusChanged(m_status);
        QTimer::singleShot(m_config.reconnectDelay,
                           this, &StreamCapture::attemptReconnect);
    } else {
        m_status = RecordingStatus::Error;
        emit statusChanged(m_status);
        emit errorOccurred(err);
    }
}

void StreamCapture::onFfmpegFinished(int exitCode, QProcess::ExitStatus exitStatus)
{
    if (exitStatus == QProcess::CrashExit
        && m_reconnectCount < m_config.reconnectRetries) {
        m_status = RecordingStatus::Reconnecting;
        emit statusChanged(m_status);
        QTimer::singleShot(m_config.reconnectDelay,
                           this, &StreamCapture::attemptReconnect);
    } else {
        m_status = RecordingStatus::Stopped;
        m_durationTimer.stop();
        emit statusChanged(m_status);
    }
}

void StreamCapture::updateDuration()
{
    if (m_status == RecordingStatus::Recording)
        emit durationChanged(duration());
}

void StreamCapture::attemptReconnect()
{
    m_reconnectCount++;
    emit reconnectAttempt(m_reconnectCount, m_config.reconnectRetries);
    startFfmpeg(m_currentStreamUrl);
    m_status = RecordingStatus::Recording;
    emit statusChanged(m_status);
}
```

## 验证

编译项目：
```bash
cd shotcut-source/build
cmake --build . --config Release
```

预期：编译通过。

## 依赖

- FFmpeg 需在系统 PATH 中
