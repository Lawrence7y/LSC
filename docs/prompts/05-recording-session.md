# Task 5: 录制会话管理

## 任务目标

实现 `RecordingSession` 类，协调 `PlatformParser` 和 `StreamCapture`，管理从 URL 输入到录制完成的完整生命周期，并保存录制元数据（JSON）。

## 创建文件

- `src/lsc/livestream/RecordingSession.h`
- `src/lsc/livestream/RecordingSession.cpp`

## 前置条件

- Task 3 已完成 (PlatformParser)
- Task 4 已完成 (StreamCapture)

## RecordingSession.h

```cpp
#ifndef RECORDINGSESSION_H
#define RECORDINGSESSION_H

#include "StreamCapture.h"
#include "PlatformParser.h"
#include <QObject>
#include <QJsonObject>

class RecordingSession : public QObject
{
    Q_OBJECT
public:
    explicit RecordingSession(QObject* parent = nullptr);
    ~RecordingSession();

    void startRecording(const QString& url, const RecordingConfig& config);
    void stopRecording();
    void pauseRecording();
    void resumeRecording();

    PlatformInfo platformInfo() const { return m_platformInfo; }
    RecordingStatus status() const;
    QString outputPath() const;
    qint64 duration() const;
    qint64 fileSize() const;

signals:
    void recordingStarted(const QString& outputPath);
    void recordingStopped(const QString& outputPath, qint64 fileSize);
    void recordingPaused();
    void recordingResumed();
    void statusChanged(RecordingStatus status);
    void durationChanged(qint64 ms);
    void errorOccurred(const QString& error);
    void platformParsed(const PlatformInfo& info);

private slots:
    void onPlatformParsed(const PlatformInfo& info);
    void onPlatformError(const QString& error);

private:
    PlatformParser* m_parser;
    StreamCapture* m_capture;
    PlatformInfo m_platformInfo;
    RecordingConfig m_config;
    QJsonObject m_metadata;
};

#endif
```

## RecordingSession.cpp

```cpp
#include "RecordingSession.h"
#include <QDateTime>
#include <QJsonDocument>
#include <QFile>

RecordingSession::RecordingSession(QObject* parent)
    : QObject(parent)
    , m_parser(new PlatformParser(this))
    , m_capture(new StreamCapture(this))
{
    connect(m_parser, &PlatformParser::parseComplete,
            this, &RecordingSession::onPlatformParsed);
    connect(m_parser, &PlatformParser::parseError,
            this, &RecordingSession::onPlatformError);
    connect(m_capture, &StreamCapture::statusChanged,
            this, &RecordingSession::statusChanged);
    connect(m_capture, &StreamCapture::durationChanged,
            this, &RecordingSession::durationChanged);
    connect(m_capture, &StreamCapture::errorOccurred,
            this, &RecordingSession::errorOccurred);
}

RecordingSession::~RecordingSession() { stopRecording(); }

void RecordingSession::startRecording(const QString& url,
                                       const RecordingConfig& config)
{
    m_config = config;
    m_parser->parseUrl(url);
}

void RecordingSession::onPlatformParsed(const PlatformInfo& info)
{
    m_platformInfo = info;
    emit platformParsed(info);

    if (info.isValid) {
        bool ok = m_capture->start(info.streamUrl, m_config);
        if (ok) {
            m_metadata["platform"] = info.platform;
            m_metadata["roomId"] = info.roomId;
            m_metadata["title"] = info.title;
            m_metadata["startTime"] =
                QDateTime::currentDateTime().toSecsSinceEpoch();

            QJsonDocument doc(m_metadata);
            QString metaPath = m_config.outputPath + ".json";
            QFile file(metaPath);
            if (file.open(QIODevice::WriteOnly)) {
                file.write(doc.toJson());
                file.close();
            }

            emit recordingStarted(m_config.outputPath);
        }
    }
}

void RecordingSession::onPlatformError(const QString& error)
{
    emit errorOccurred(error);
}

void RecordingSession::stopRecording()
{
    m_capture->stop();
    if (!m_config.outputPath.isEmpty())
        emit recordingStopped(m_config.outputPath, fileSize());
}

void RecordingSession::pauseRecording() { m_capture->pause(); }
void RecordingSession::resumeRecording() { m_capture->resume(); }
RecordingStatus RecordingSession::status() const { return m_capture->status(); }
QString RecordingSession::outputPath() const { return m_config.outputPath; }
qint64 RecordingSession::duration() const { return m_capture->duration(); }
qint64 RecordingSession::fileSize() const { return m_capture->fileSize(); }
```

## 验证

```bash
cd shotcut-source/build
cmake --build . --config Release
```

预期：编译通过。
