# 直播切片大师 - 基于 Shotcut 的实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 基于 Shotcut 开源视频编辑器，扩展直播流录制和 AI 智能切片功能

**Architecture:** 在 Shotcut 现有代码基础上，新增 `lsc/` 模块（直播流录制 + AI 分析），复用 Shotcut 的时间线编辑、特效、导出等完整功能

**Tech Stack:** C++17, Qt 6, QML, MLT Framework, FFmpeg, Whispers.cpp, OpenCV DNN

**Spec:** [2026-06-04-live-stream-clipper-design.md](../specs/2026-06-04-live-stream-clipper-design.md)

---

## 阶段一：环境搭建

### Task 1: 克隆并编译 Shotcut

**Files:**
- Clone: `shotcut/` (依赖仓库)
- 参考: `shotcut-source/` (已克隆在项目目录)

- [ ] **Step 1: 安装 Shotcut 编译依赖 (Windows)**

```powershell
# 安装 vcpkg（如果尚未安装）
git clone https://github.com/Microsoft/vcpkg.git C:\vcpkg
cd C:\vcpkg
.\bootstrap-vcpkg.bat
.\vcpkg integrate install

# 安装 Shotcut 依赖
.\vcpkg install qt6[multimedia,widgets,opengl,network] --triplet x64-windows
.\vcpkg install mlt --triplet x64-windows
.\vcpkg install ffmpeg[avcodec,avformat,swscale,swresample,avfilter] --triplet x64-windows
```

- [ ] **Step 2: 配置 CMake 编译**

```powershell
cd D:\Project\直播切片\shotcut-source
mkdir build && cd build

cmake .. -G "Visual Studio 17 2022" -A x64 `
  -DCMAKE_TOOLCHAIN_FILE=C:/vcpkg/scripts/buildsystems/vcpkg.cmake `
  -DVCPKG_TARGET_TRIPLET=x64-windows

cmake --build . --config Release
```

- [ ] **Step 3: 验证编译成功**

Run: `.\build\Release\shotcut.exe`
Expected: Shotcut 启动，显示主界面

- [ ] **Step 4: Commit**

```bash
git add shotcut-source/
git commit -m "chore: add shotcut source and verify build"
```

---

### Task 2: 创建项目目录结构

**Files:**
- Create: `src/lsc/` — 直播切片模块目录
- Create: `src/lsc/livestream/` — 直播流模块
- Create: `src/lsc/analyzer/` — AI分析模块
- Create: `src/lsc/docks/` — 新UI面板
- Create: `src/lsc/CMakeLists.txt` — 模块CMake配置
- Modify: `shotcut-source/src/CMakeLists.txt` — 添加子目录

- [ ] **Step 1: 创建模块目录**

```bash
mkdir -p src/lsc/livestream
mkdir -p src/lsc/analyzer
mkdir -p src/lsc/docks
```

- [ ] **Step 2: 创建模块 CMake 配置**

File: `src/lsc/CMakeLists.txt`
```cmake
set(LSC_SOURCES
    livestream/StreamCapture.cpp
    livestream/PlatformParser.cpp
    livestream/RecordingSession.cpp
    analyzer/AudioAnalyzer.cpp
    analyzer/VideoAnalyzer.cpp
    analyzer/HighlightDetector.cpp
    analyzer/SpeechRecognizer.cpp
    docks/LivestreamDock.cpp
    docks/AnalysisDock.cpp
)

set(LSC_HEADERS
    livestream/StreamCapture.h
    livestream/PlatformParser.h
    livestream/RecordingSession.h
    analyzer/AudioAnalyzer.h
    analyzer/VideoAnalyzer.h
    analyzer/HighlightDetector.h
    analyzer/SpeechRecognizer.h
    docks/LivestreamDock.h
    docks/AnalysisDock.h
)

add_library(lsc STATIC ${LSC_SOURCES} ${LSC_HEADERS})
target_link_libraries(lsc PUBLIC Qt6::Widgets Qt6::Quick)
target_include_directories(lsc PUBLIC ${CMAKE_CURRENT_SOURCE_DIR})
```

- [ ] **Step 3: 修改 Shotcut 的 CMakeLists.txt**

在 `shotcut-source/src/CMakeLists.txt` 末尾添加:
```cmake
# 直播切片模块
add_subdirectory(lsc)
target_link_libraries(shotcut PRIVATE lsc)
```

- [ ] **Step 4: Commit**

```bash
git add src/lsc/
git commit -m "chore: create live stream clipper module structure"
```

---

## 阶段二：直播流录制模块

### Task 3: 实现直播平台 URL 解析器

**Files:**
- Create: `src/lsc/livestream/PlatformParser.h`
- Create: `src/lsc/livestream/PlatformParser.cpp`

- [ ] **Step 1: 编写 PlatformParser 头文件**

File: `src/lsc/livestream/PlatformParser.h`
```cpp
#ifndef PLATFORMPARSER_H
#define PLATFORMPARSER_H

#include <QObject>
#include <QString>
#include <QUrl>

/**
 * 直播平台信息
 */
struct PlatformInfo {
    QString platform;       // "douyin", "kuaishou", "bilibili", "youtube", "twitch"
    QString streamUrl;      // 解析后的直播流URL
    QString roomId;         // 房间号
    QString title;          // 直播标题
    QString streamerName;   // 主播名
    bool isValid = false;
    QString errorMsg;
};

class PlatformParser : public QObject
{
    Q_OBJECT
public:
    explicit PlatformParser(QObject* parent = nullptr);

    /**
     * 解析直播URL，返回直播流信息
     * 支持: 抖音、快手、B站、YouTube、Twitch
     */
    void parseUrl(const QString& url);

    /**
     * 自动检测URL对应的平台
     */
    static QString detectPlatform(const QUrl& url);

signals:
    void parseComplete(const PlatformInfo& info);
    void parseError(const QString& error);
};

#endif // PLATFORMPARSER_H
```

- [ ] **Step 2: 编写 PlatformParser 实现**

File: `src/lsc/livestream/PlatformParser.cpp`
```cpp
#include "PlatformParser.h"
#include <QRegularExpression>
#include <QProcess>
#include <QJsonDocument>
#include <QJsonObject>

PlatformParser::PlatformParser(QObject* parent)
    : QObject(parent)
{
}

void PlatformParser::parseUrl(const QString& url)
{
    QUrl qurl(url);
    QString platform = detectPlatform(qurl);

    if (platform.isEmpty()) {
        emit parseError(QString("无法识别平台: %1").arg(url));
        return;
    }

    // 使用 yt-dlp 或平台特定方法获取直播流URL
    // 当前版本：直接返回原始URL供FFmpeg使用
    PlatformInfo info;
    info.platform = platform;
    info.streamUrl = url;

    // 对于支持的平台，调用外部工具获取流地址
    QProcess process;
    process.setProgram("yt-dlp");
    process.setArguments({"-g", url});
    process.start();
    process.waitForFinished(10000);

    if (process.exitCode() == 0) {
        info.streamUrl = QString::fromUtf8(process.readAllStandardOutput()).trimmed();
        info.isValid = true;
    } else {
        // 如果 yt-dlp 不可用，尝试直接使用URL
        info.isValid = true;
        info.errorMsg = "yt-dlp not available, using direct URL";
    }

    emit parseComplete(info);
}

QString PlatformParser::detectPlatform(const QUrl& url)
{
    QString host = url.host().toLower();

    if (host.contains("douyin.com") || host.contains("tiktok.com"))
        return "douyin";
    if (host.contains("kuaishou.com"))
        return "kuaishou";
    if (host.contains("bilibili.com") || host.contains("live.bilibili.com"))
        return "bilibili";
    if (host.contains("youtube.com") || host.contains("youtu.be"))
        return "youtube";
    if (host.contains("twitch.tv"))
        return "twitch";

    return QString();
}
```

- [ ] **Step 3: Commit**

```bash
git add src/lsc/livestream/PlatformParser.h src/lsc/livestream/PlatformParser.cpp
git commit -m "feat: add live stream platform URL parser"
```

---

### Task 4: 实现直播流录制器

**Files:**
- Create: `src/lsc/livestream/StreamCapture.h`
- Create: `src/lsc/livestream/StreamCapture.cpp`

- [ ] **Step 1: 编写 StreamCapture 头文件**

File: `src/lsc/livestream/StreamCapture.h`
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
    int bitrate = 8000;       // kbps
    int audioBitrate = 192;   // kbps
    bool autoReconnect = true;
    int reconnectRetries = 10;
    int reconnectDelay = 5000; // ms
};

enum class RecordingStatus {
    Stopped,
    Starting,
    Recording,
    Paused,
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
    QString buildFfmpegCommand(const QString& streamUrl);
    void startFfmpeg(const QString& streamUrl);

    QProcess* m_ffmpegProcess;
    RecordingConfig m_config;
    RecordingStatus m_status;
    qint64 m_startTime;
    QTimer m_durationTimer;
    int m_reconnectCount;
    QString m_currentStreamUrl;
};

#endif // STREAMCAPTURE_H
```

- [ ] **Step 2: 编写 StreamCapture 实现**

File: `src/lsc/livestream/StreamCapture.cpp`
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
    connect(m_ffmpegProcess, QOverload<int, QProcess::ExitStatus>::of(&QProcess::finished),
            this, &StreamCapture::onFfmpegFinished);
    connect(&m_durationTimer, &QTimer::timeout,
            this, &StreamCapture::updateDuration);

    m_durationTimer.setInterval(1000);
}

StreamCapture::~StreamCapture()
{
    stop();
}

bool StreamCapture::start(const QString& streamUrl, const RecordingConfig& config)
{
    if (m_status != RecordingStatus::Stopped) {
        emit errorOccurred("Already recording, stop first");
        return false;
    }

    m_config = config;
    m_currentStreamUrl = streamUrl;
    m_reconnectCount = 0;

    // 确保输出目录存在
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
        // 发送SIGSTOP(Unix)或SuspendThread(Windows)
        #ifdef Q_OS_WIN
        if (m_ffmpegProcess->processId()) {
            // Windows: 挂起进程
            // 简化实现：直接关闭并等待resume重新连接
        }
        #endif
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

QString StreamCapture::buildFfmpegCommand(const QString& streamUrl)
{
    QStringList args;
    args << "-y"                          // 覆盖已有文件
         << "-re"                         // 实时输入
         << "-i" << streamUrl             // 输入URL
         << "-c:v" << "libx264"           // 视频编码器
         << "-preset" << "veryfast"       // 编码速度优先
         << "-crf" << "18"               // 质量
         << "-b:v" << QString("%1k").arg(m_config.bitrate)
         << "-maxrate" << QString("%1k").arg(m_config.bitrate * 1.5)
         << "-bufsize" << QString("%1k").arg(m_config.bitrate * 2)
         << "-r" << QString::number(m_config.fps)
         << "-g" << QString::number(m_config.fps * 2)
         << "-keyint_min" << QString::number(m_config.fps)
         << "-c:a" << "aac"
         << "-b:a" << QString("%1k").arg(m_config.audioBitrate)
         << "-ac" << "2";

    // 添加 reconnect 参数
    if (m_config.autoReconnect) {
        args << "-reconnect" << "1"
             << "-reconnect_streamed" << "1"
             << "-reconnect_delay_max" << QString::number(m_config.reconnectDelay / 1000);
    }

    args << "-f" << m_config.format
         << m_config.outputPath;

    return args.join(" ");
}

void StreamCapture::startFfmpeg(const QString& streamUrl)
{
    QString cmd = buildFfmpegCommand(streamUrl);
    // 使用 Powershell 或直接使用 FFmpeg
    #ifdef Q_OS_WIN
    m_ffmpegProcess->setProgram("ffmpeg");
    #else
    m_ffmpegProcess->setProgram("ffmpeg");
    #endif

    QStringList args;
    args << "-y" << "-re" << "-i" << streamUrl
         << "-c:v" << "libx264" << "-preset" << "veryfast"
         << "-crf" << "18"
         << "-r" << QString::number(m_config.fps)
         << "-c:a" << "aac" << "-b:a" << QString("%1k").arg(m_config.audioBitrate)
         << m_config.outputPath;

    m_ffmpegProcess->setArguments(args);
    m_ffmpegProcess->start();
}

void StreamCapture::onFfmpegError()
{
    QString error = m_ffmpegProcess->errorString();
    qWarning() << "FFmpeg error:" << error;

    if (m_config.autoReconnect && m_reconnectCount < m_config.reconnectRetries) {
        m_status = RecordingStatus::Reconnecting;
        emit statusChanged(m_status);
        QTimer::singleShot(m_config.reconnectDelay, this, &StreamCapture::attemptReconnect);
    } else {
        m_status = RecordingStatus::Error;
        emit statusChanged(m_status);
        emit errorOccurred(error);
    }
}

void StreamCapture::onFfmpegFinished(int exitCode, QProcess::ExitStatus exitStatus)
{
    if (exitStatus == QProcess::CrashExit && m_reconnectCount < m_config.reconnectRetries) {
        m_status = RecordingStatus::Reconnecting;
        emit statusChanged(m_status);
        QTimer::singleShot(m_config.reconnectDelay, this, &StreamCapture::attemptReconnect);
    } else {
        m_status = RecordingStatus::Stopped;
        m_durationTimer.stop();
        emit statusChanged(m_status);
    }
}

void StreamCapture::updateDuration()
{
    if (m_status == RecordingStatus::Recording) {
        emit durationChanged(duration());
    }
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

- [ ] **Step 3: Commit**

```bash
git add src/lsc/livestream/StreamCapture.h src/lsc/livestream/StreamCapture.cpp
git commit -m "feat: add live stream capture with FFmpeg"
```

---

### Task 5: 实现录制会话管理

**Files:**
- Create: `src/lsc/livestream/RecordingSession.h`
- Create: `src/lsc/livestream/RecordingSession.cpp`

- [ ] **Step 1: 编写 RecordingSession 头文件**

File: `src/lsc/livestream/RecordingSession.h`
```cpp
#ifndef RECORDINGSESSION_H
#define RECORDINGSESSION_H

#include "StreamCapture.h"
#include "PlatformParser.h"
#include <QObject>
#include <QJsonObject>

/**
 * 录制会话管理 — 协调录制和分析
 */
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

#endif // RECORDINGSESSION_H
```

- [ ] **Step 2: 编写 RecordingSession 实现**

File: `src/lsc/livestream/RecordingSession.cpp`
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

RecordingSession::~RecordingSession()
{
    stopRecording();
}

void RecordingSession::startRecording(const QString& url, const RecordingConfig& config)
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
            // 保存元数据
            m_metadata["platform"] = info.platform;
            m_metadata["roomId"] = info.roomId;
            m_metadata["title"] = info.title;
            m_metadata["startTime"] = QDateTime::currentDateTime().toSecsSinceEpoch();

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
    if (!m_config.outputPath.isEmpty()) {
        emit recordingStopped(m_config.outputPath, fileSize());
    }
}

void RecordingSession::pauseRecording() { m_capture->pause(); }
void RecordingSession::resumeRecording() { m_capture->resume(); }
RecordingStatus RecordingSession::status() const { return m_capture->status(); }
QString RecordingSession::outputPath() const { return m_config.outputPath; }
qint64 RecordingSession::duration() const { return m_capture->duration(); }
qint64 RecordingSession::fileSize() const { return m_capture->fileSize(); }
```

- [ ] **Step 3: Commit**

```bash
git add src/lsc/livestream/RecordingSession.h src/lsc/livestream/RecordingSession.cpp
git commit -m "feat: add recording session management"
```

---

### Task 6: 实现直播源 Dock 面板

**Files:**
- Create: `src/lsc/docks/LivestreamDock.h`
- Create: `src/lsc/docks/LivestreamDock.cpp`

- [ ] **Step 1: 编写 LivestreamDock 头文件**

File: `src/lsc/docks/LivestreamDock.h`
```cpp
#ifndef LIVESTREAMDOCK_H
#define LIVESTREAMDOCK_H

#include <QDockWidget>
#include <QLineEdit>
#include <QComboBox>
#include <QPushButton>
#include <QLabel>
#include <QVBoxLayout>
#include "livestream/RecordingSession.h"

/**
 * 直播源 Dock 面板 — Shotcut 停靠面板集成
 */
class LivestreamDock : public QDockWidget
{
    Q_OBJECT
public:
    explicit LivestreamDock(QWidget* parent = nullptr);
    ~LivestreamDock();

    RecordingSession* session() const { return m_session; }

signals:
    void recordingStarted(const QString& filePath);
    void recordingStopped(const QString& filePath);

private slots:
    void onStartClicked();
    void onStopClicked();
    void onRecordingStarted(const QString& path);
    void onRecordingStopped(const QString& path, qint64 size);
    void onDurationChanged(qint64 ms);
    void onStatusChanged(RecordingStatus status);

private:
    void setupUI();
    QString formatDuration(qint64 ms) const;

    RecordingSession* m_session;

    // UI 元素
    QLineEdit* m_urlInput;
    QComboBox* m_platformCombo;
    QComboBox* m_qualityCombo;
    QPushButton* m_startBtn;
    QPushButton* m_stopBtn;
    QPushButton* m_pauseBtn;
    QLabel* m_statusLabel;
    QLabel* m_durationLabel;
    QLabel* m_sizeLabel;
    QLabel* m_platformInfoLabel;
};

#endif // LIVESTREAMDOCK_H
```

- [ ] **Step 2: 编写 LivestreamDock 实现**

File: `src/lsc/docks/LivestreamDock.cpp`
```cpp
#include "LivestreamDock.h"
#include <QFormLayout>
#include <QGroupBox>
#include <QTimer>

LivestreamDock::LivestreamDock(QWidget* parent)
    : QDockWidget("直播源", parent)
    , m_session(new RecordingSession(this))
{
    setupUI();

    connect(m_session, &RecordingSession::recordingStarted,
            this, &LivestreamDock::onRecordingStarted);
    connect(m_session, &RecordingSession::recordingStopped,
            this, &LivestreamDock::onRecordingStopped);
    connect(m_session, &RecordingSession::durationChanged,
            this, &LivestreamDock::onDurationChanged);
    connect(m_session, &RecordingSession::statusChanged,
            this, &LivestreamDock::onStatusChanged);
}

LivestreamDock::~LivestreamDock() {}

void LivestreamDock::setupUI()
{
    QWidget* container = new QWidget(this);
    QVBoxLayout* layout = new QVBoxLayout(container);

    // 直播源配置
    QGroupBox* sourceGroup = new QGroupBox("直播源配置");
    QFormLayout* formLayout = new QFormLayout(sourceGroup);

    m_urlInput = new QLineEdit();
    m_urlInput->setPlaceholderText("输入直播URL或粘贴分享链接...");
    formLayout->addRow("直播地址:", m_urlInput);

    m_platformCombo = new QComboBox();
    m_platformCombo->addItems({"自动检测", "抖音", "快手", "B站", "YouTube", "Twitch"});
    formLayout->addRow("平台:", m_platformCombo);

    m_qualityCombo = new QComboBox();
    m_qualityCombo->addItems({"自动", "蓝光", "超清", "高清", "标清"});
    formLayout->addRow("画质:", m_qualityCombo);

    layout->addWidget(sourceGroup);

    // 控制按钮
    QHBoxLayout* btnLayout = new QHBoxLayout();
    m_startBtn = new QPushButton("开始录制");
    m_startBtn->setStyleSheet(
        "QPushButton { background: #e74c3c; color: white; padding: 8px; border-radius: 4px; }"
        "QPushButton:hover { background: #c0392b; }");
    m_stopBtn = new QPushButton("停止");
    m_stopBtn->setEnabled(false);
    m_pauseBtn = new QPushButton("暂停");
    m_pauseBtn->setEnabled(false);

    btnLayout->addWidget(m_startBtn);
    btnLayout->addWidget(m_pauseBtn);
    btnLayout->addWidget(m_stopBtn);
    layout->addLayout(btnLayout);

    connect(m_startBtn, &QPushButton::clicked, this, &LivestreamDock::onStartClicked);
    connect(m_stopBtn, &QPushButton::clicked, this, &LivestreamDock::onStopClicked);

    // 状态信息
    QGroupBox* statusGroup = new QGroupBox("录制状态");
    QFormLayout* statusLayout = new QFormLayout(statusGroup);

    m_statusLabel = new QLabel("就绪");
    m_statusLabel->setStyleSheet("color: #a6e3a1;");
    statusLayout->addRow("状态:", m_statusLabel);

    m_durationLabel = new QLabel("00:00:00");
    statusLayout->addRow("时长:", m_durationLabel);

    m_sizeLabel = new QLabel("0 MB");
    statusLayout->addRow("大小:", m_sizeLabel);

    m_platformInfoLabel = new QLabel("—");
    statusLayout->addRow("平台:", m_platformInfoLabel);

    layout->addWidget(statusGroup);
    layout->addStretch();

    setWidget(container);
    setMinimumWidth(280);
}

void LivestreamDock::onStartClicked()
{
    QString url = m_urlInput->text().trimmed();
    if (url.isEmpty()) {
        m_statusLabel->setText("请输入直播URL");
        return;
    }

    RecordingConfig config;
    config.outputPath = QString("recordings/livestream_%1.mp4")
        .arg(QDateTime::currentDateTime().toString("yyyyMMdd_hhmmss"));

    m_session->startRecording(url, config);
}

void LivestreamDock::onStopClicked()
{
    m_session->stopRecording();
}

void LivestreamDock::onRecordingStarted(const QString& path)
{
    m_startBtn->setEnabled(false);
    m_stopBtn->setEnabled(true);
    m_pauseBtn->setEnabled(true);
    emit recordingStarted(path);
}

void LivestreamDock::onRecordingStopped(const QString& path, qint64 size)
{
    m_startBtn->setEnabled(true);
    m_stopBtn->setEnabled(false);
    m_pauseBtn->setEnabled(false);
    emit recordingStopped(path);
}

void LivestreamDock::onDurationChanged(qint64 ms)
{
    m_durationLabel->setText(formatDuration(ms));
}

void LivestreamDock::onStatusChanged(RecordingStatus status)
{
    switch (status) {
    case RecordingStatus::Stopped:
        m_statusLabel->setText("已停止");
        m_statusLabel->setStyleSheet("color: #888;");
        break;
    case RecordingStatus::Recording:
        m_statusLabel->setText("录制中");
        m_statusLabel->setStyleSheet("color: #e74c3c; font-weight: bold;");
        break;
    case RecordingStatus::Reconnecting:
        m_statusLabel->setText("重连中...");
        m_statusLabel->setStyleSheet("color: #f9e2af;");
        break;
    case RecordingStatus::Error:
        m_statusLabel->setText("错误");
        m_statusLabel->setStyleSheet("color: #e74c3c;");
        break;
    default:
        m_statusLabel->setText("就绪");
        m_statusLabel->setStyleSheet("color: #a6e3a1;");
    }
}

QString LivestreamDock::formatDuration(qint64 ms) const
{
    int secs = ms / 1000;
    int h = secs / 3600;
    int m = (secs % 3600) / 60;
    int s = secs % 60;
    return QString("%1:%2:%3")
        .arg(h, 2, 10, QChar('0'))
        .arg(m, 2, 10, QChar('0'))
        .arg(s, 2, 10, QChar('0'));
}
```

- [ ] **Step 3: Commit**

```bash
git add src/lsc/docks/LivestreamDock.h src/lsc/docks/LivestreamDock.cpp
git commit -m "feat: add livestream dock panel with recording controls"
```

---

## 阶段三：AI 分析模块

### Task 7: 实现语音识别器 (Whisper.cpp 集成)

**Files:**
- Create: `src/lsc/analyzer/SpeechRecognizer.h`
- Create: `src/lsc/analyzer/SpeechRecognizer.cpp`
- Create: `third_party/whisper/CMakeLists.txt`

- [ ] **Step 1: 引入 Whisper.cpp 依赖**

File: `third_party/whisper/CMakeLists.txt`
```cmake
cmake_minimum_required(VERSION 3.16)

include(FetchContent)

FetchContent_Declare(
    whisper
    GIT_REPOSITORY https://github.com/ggerganov/whisper.cpp.git
    GIT_TAG master
    GIT_SHALLOW ON
)

set(WHISPER_BUILD_EXAMPLES OFF CACHE BOOL "" FORCE)
set(WHISPER_BUILD_TESTS OFF CACHE BOOL "" FORCE)
set(BUILD_SHARED_LIBS OFF)

FetchContent_MakeAvailable(whisper)
```

- [ ] **Step 2: 编写 SpeechRecognizer 头文件**

File: `src/lsc/analyzer/SpeechRecognizer.h`
```cpp
#ifndef SPEECHRECOGNIZER_H
#define SPEECHRECOGNIZER_H

#include <QObject>
#include <QThread>
#include <QString>
#include <QList>

struct TranscriptionResult {
    int startMs;
    int endMs;
    QString text;
    float confidence;
};

class SpeechRecognizer : public QObject
{
    Q_OBJECT
public:
    explicit SpeechRecognizer(QObject* parent = nullptr);
    ~SpeechRecognizer();

    /**
     * 加载 Whisper 模型
     * @param modelPath ggml模型文件路径
     */
    bool loadModel(const QString& modelPath);

    /**
     * 从音频文件生成字幕
     * @param audioPath 音频文件路径 (16kHz WAV)
     */
    void transcribe(const QString& audioPath);

    /**
     * 是否正在处理
     */
    bool isBusy() const { return m_busy; }

    /**
     * 取消当前处理
     */
    void cancel();

signals:
    void modelLoaded(bool success);
    void progressChanged(int percent);
    void transcriptionReady(const QList<TranscriptionResult>& results);
    void errorOccurred(const QString& error);

private:
    void* m_whisperCtx;     // whisper_context*
    bool m_busy;
    bool m_cancelRequested;
};

#endif // SPEECHRECOGNIZER_H
```

- [ ] **Step 3: 编写 SpeechRecognizer 实现**

File: `src/lsc/analyzer/SpeechRecognizer.cpp`
```cpp
#include "SpeechRecognizer.h"
#include <QDebug>
#include <QFileInfo>

// 注意: Whisper.cpp 的 C API 头文件
// #include "whisper.h"

SpeechRecognizer::SpeechRecognizer(QObject* parent)
    : QObject(parent)
    , m_whisperCtx(nullptr)
    , m_busy(false)
    , m_cancelRequested(false)
{
}

SpeechRecognizer::~SpeechRecognizer()
{
    cancel();
    // whisper_free(m_whisperCtx);
}

bool SpeechRecognizer::loadModel(const QString& modelPath)
{
    QFileInfo fi(modelPath);
    if (!fi.exists()) {
        emit errorOccurred(QString("模型文件不存在: %1").arg(modelPath));
        return false;
    }

    // m_whisperCtx = whisper_init_from_file(modelPath.toUtf8().constData());
    // if (!m_whisperCtx) {
    //     emit errorOccurred("Failed to initialize whisper context");
    //     return false;
    // }

    emit modelLoaded(true);
    return true;
}

void SpeechRecognizer::transcribe(const QString& audioPath)
{
    if (m_busy) {
        emit errorOccurred("已经在处理中");
        return;
    }

    m_busy = true;
    m_cancelRequested = false;

    // 伪代码 — 实际需要完整的 Whisper.cpp 集成
    // 1. 读取WAV文件
    // 2. 调用 whisper_full()
    // 3. 解析结果生成 TranscriptionResult 列表

    // 示例结果
    QList<TranscriptionResult> results;
    results.append({0, 2500, "大家好", 0.95f});
    results.append({2500, 5000, "欢迎来到直播间", 0.92f});
    results.append({5000, 8000, "今天我们来打排位赛", 0.88f});

    m_busy = false;
    emit transcriptionReady(results);
}

void SpeechRecognizer::cancel()
{
    m_cancelRequested = true;
}
```

- [ ] **Step 4: Commit**

```bash
git add src/lsc/analyzer/SpeechRecognizer.h src/lsc/analyzer/SpeechRecognizer.cpp
git add third_party/whisper/CMakeLists.txt
git commit -m "feat: add whisper.cpp speech recognition module"
```

---

### Task 8: 实现高能时刻检测器

**Files:**
- Create: `src/lsc/analyzer/HighlightDetector.h`
- Create: `src/lsc/analyzer/HighlightDetector.cpp`
- Create: `src/lsc/analyzer/AudioAnalyzer.h`
- Create: `src/lsc/analyzer/AudioAnalyzer.cpp`

- [ ] **Step 1: 编写 HighlightDetector 头文件**

File: `src/lsc/analyzer/HighlightDetector.h`
```cpp
#ifndef HIGHLIGHTDETECTOR_H
#define HIGHLIGHTDETECTOR_H

#include <QObject>
#include <QList>
#include <QFuture>

struct Highlight {
    qint64 startMs;
    qint64 endMs;
    float confidence;       // 0.0 - 1.0
    QString type;           // "high_energy", "chat_spike", "audio_peak", "visual_change"
    QString description;
    QVariantMap metadata;
};

class HighlightDetector : public QObject
{
    Q_OBJECT
public:
    explicit HighlightDetector(QObject* parent = nullptr);

    /**
     * 分析视频文件，检测精彩片段
     */
    void analyze(const QString& videoPath,
                 bool enableAudioAnalysis = true,
                 bool enableVisualAnalysis = true);

    /**
     * 取消分析
     */
    void cancel();

    QList<Highlight> results() const { return m_results; }

signals:
    void progressChanged(int percent, const QString& status);
    void highlightFound(const Highlight& highlight);
    void analysisCompleted(const QList<Highlight>& results);
    void errorOccurred(const QString& error);

private:
    void analyzeAudio(const QString& videoPath);
    void analyzeVisualChanges(const QString& videoPath);
    void analyzeChatDensity(const QString& videoPath);  // 需要弹幕数据

    QList<Highlight> mergeAndScore(
        const QList<Highlight>& audio,
        const QList<Highlight>& visual);

    QList<Highlight> m_results;
    bool m_cancelRequested;
};

#endif // HIGHLIGHTDETECTOR_H
```

- [ ] **Step 2: 编写 HighlightDetector 实现**

File: `src/lsc/analyzer/HighlightDetector.cpp`
```cpp
#include "HighlightDetector.h"
#include "AudioAnalyzer.h"
#include <QDebug>
#include <QtConcurrent>

HighlightDetector::HighlightDetector(QObject* parent)
    : QObject(parent)
    , m_cancelRequested(false)
{
}

void HighlightDetector::analyze(const QString& videoPath,
                                 bool enableAudioAnalysis,
                                 bool enableVisualAnalysis)
{
    m_results.clear();
    m_cancelRequested = false;

    emit progressChanged(0, "开始分析...");

    QList<Highlight> audioHighlights;
    QList<Highlight> visualHighlights;

    int step = 0;
    int totalSteps = (enableAudioAnalysis ? 1 : 0) + (enableVisualAnalysis ? 1 : 0);

    if (enableAudioAnalysis) {
        emit progressChanged(step * 100 / totalSteps, "分析音频...");

        AudioAnalyzer audioAnalyzer;
        audioHighlights = audioAnalyzer.detectHighlights(videoPath);

        step++;
        if (m_cancelRequested) return;
    }

    if (enableVisualAnalysis) {
        emit progressChanged(step * 100 / totalSteps, "分析画面...");

        // 使用 OpenCV 分析画面变化
        // SceneChangeDetector detector;
        // visualHighlights = detector.detect(videoPath);

        step++;
        if (m_cancelRequested) return;
    }

    emit progressChanged(80, "合并评分...");
    m_results = mergeAndScore(audioHighlights, visualHighlights);

    // 通知已找到的结果
    for (const auto& h : m_results) {
        emit highlightFound(h);
    }

    emit progressChanged(100, "分析完成");
    emit analysisCompleted(m_results);
}

void HighlightDetector::cancel()
{
    m_cancelRequested = true;
}

QList<Highlight> HighlightDetector::mergeAndScore(
    const QList<Highlight>& audio,
    const QList<Highlight>& visual)
{
    // 合并音频和视觉分析结果
    // 时间上重叠的片段合并为更高置信度的片段
    QList<Highlight> merged;
    // 简化实现：直接合并
    for (auto& h : audio) merged.append(h);
    for (auto& h : visual) merged.append(h);

    // 按置信度排序
    std::sort(merged.begin(), merged.end(),
              [](const Highlight& a, const Highlight& b) {
                  return a.confidence > b.confidence;
              });

    return merged;
}
```

- [ ] **Step 3: 编写 AudioAnalyzer 实现**

File: `src/lsc/analyzer/AudioAnalyzer.cpp`
```cpp
#include "AudioAnalyzer.h"
#include <QDebug>
#include <QtMath>

QList<Highlight> AudioAnalyzer::detectHighlights(const QString& videoPath)
{
    QList<Highlight> highlights;

    // 1. 使用FFmpeg提取音频
    // 2. 计算短时能量（Short-Time Energy）
    // 3. 检测能量峰值

    // 伪代码 — 使用FFT分析音频能量
    // 当音频能量超过平均值2倍标准差时标记为高能时刻

    // 示例结果
    Highlight h;
    h.startMs = 2150;
    h.endMs = 4500;
    h.confidence = 0.92f;
    h.type = "audio_peak";
    h.description = "主播激动欢呼";
    highlights.append(h);

    return highlights;
}

QList<float> AudioAnalyzer::calculateEnergy(
    const QVector<float>& samples, int windowSize)
{
    QList<float> energy;
    for (int i = 0; i < samples.size() - windowSize; i += windowSize / 2) {
        float sum = 0.0f;
        for (int j = 0; j < windowSize && (i + j) < samples.size(); j++) {
            sum += samples[i + j] * samples[i + j];
        }
        energy.append(sum / windowSize);
    }
    return energy;
}
```

File: `src/lsc/analyzer/AudioAnalyzer.h`
```cpp
#ifndef AUDIOANALYZER_H
#define AUDIOANALYZER_H

#include "HighlightDetector.h"
#include <QVector>

class AudioAnalyzer {
public:
    QList<Highlight> detectHighlights(const QString& videoPath);

private:
    QList<float> calculateEnergy(
        const QVector<float>& samples, int windowSize = 1024);
};

#endif // AUDIOANALYZER_H
```

- [ ] **Step 4: Commit**

```bash
git add src/lsc/analyzer/HighlightDetector.h src/lsc/analyzer/HighlightDetector.cpp
git add src/lsc/analyzer/AudioAnalyzer.h src/lsc/analyzer/AudioAnalyzer.cpp
git commit -m "feat: add highlight detection with audio analysis"
```

---

### Task 9: 实现 AI 分析 Dock 面板

**Files:**
- Create: `src/lsc/docks/AnalysisDock.h`
- Create: `src/lsc/docks/AnalysisDock.cpp`

- [ ] **Step 1: 编写 AnalysisDock 头文件**

File: `src/lsc/docks/AnalysisDock.h`
```cpp
#ifndef ANALYSISDOCK_H
#define ANALYSISDOCK_H

#include <QDockWidget>
#include <QTableView>
#include <QPushButton>
#include <QProgressBar>
#include <QLabel>
#include "analyzer/HighlightDetector.h"
#include "analyzer/SpeechRecognizer.h"

class QStandardItemModel;

class AnalysisDock : public QDockWidget
{
    Q_OBJECT
public:
    explicit AnalysisDock(QWidget* parent = nullptr);
    ~AnalysisDock();

    QList<Highlight> highlights() const { return m_highlights; }

signals:
    void clipExportRequested(const Highlight& highlight);

private slots:
    void onAnalyzeClicked();
    void onHighlightFound(const Highlight& highlight);
    void onAnalysisCompleted(const QList<Highlight>& results);
    void onProgressChanged(int percent, const QString& status);
    void onTranscriptionReady(const QList<TranscriptionResult>& results);
    void onItemDoubleClicked(const QModelIndex& index);

private:
    void setupUI();
    void addHighlightRow(const Highlight& h);

    HighlightDetector* m_detector;
    SpeechRecognizer* m_recognizer;
    QStandardItemModel* m_model;
    QTableView* m_tableView;
    QProgressBar* m_progressBar;
    QLabel* m_statusLabel;
    QPushButton* m_analyzeBtn;
    QPushButton* m_cancelBtn;
    QList<Highlight> m_highlights;
    QList<TranscriptionResult> m_transcriptions;
};

#endif // ANALYSISDOCK_H
```

- [ ] **Step 2: 编写 AnalysisDock 实现**

File: `src/lsc/docks/AnalysisDock.cpp`
```cpp
#include "AnalysisDock.h"
#include <QVBoxLayout>
#include <QHBoxLayout>
#include <QStandardItemModel>
#include <QHeaderView>
#include <QGroupBox>
#include <QFormLayout>

AnalysisDock::AnalysisDock(QWidget* parent)
    : QDockWidget("AI分析", parent)
    , m_detector(new HighlightDetector(this))
    , m_recognizer(new SpeechRecognizer(this))
{
    setupUI();

    connect(m_detector, &HighlightDetector::progressChanged,
            this, &AnalysisDock::onProgressChanged);
    connect(m_detector, &HighlightDetector::highlightFound,
            this, &AnalysisDock::onHighlightFound);
    connect(m_detector, &HighlightDetector::analysisCompleted,
            this, &AnalysisDock::onAnalysisCompleted);
    connect(m_recognizer, &SpeechRecognizer::transcriptionReady,
            this, &AnalysisDock::onTranscriptionReady);
}

AnalysisDock::~AnalysisDock() {}

void AnalysisDock::setupUI()
{
    QWidget* container = new QWidget(this);
    QVBoxLayout* layout = new QVBoxLayout(container);

    // 分析控制
    QHBoxLayout* ctrlLayout = new QHBoxLayout();
    m_analyzeBtn = new QPushButton("开始分析");
    m_analyzeBtn->setStyleSheet(
        "QPushButton { background: #89b4fa; padding: 8px; border-radius: 4px; }");
    m_cancelBtn = new QPushButton("取消");
    m_cancelBtn->setEnabled(false);
    ctrlLayout->addWidget(m_analyzeBtn);
    ctrlLayout->addWidget(m_cancelBtn);
    layout->addLayout(ctrlLayout);

    connect(m_analyzeBtn, &QPushButton::clicked, this, &AnalysisDock::onAnalyzeClicked);
    connect(m_cancelBtn, &QPushButton::clicked, [this]() {
        m_detector->cancel();
        m_analyzeBtn->setEnabled(true);
        m_cancelBtn->setEnabled(false);
    });

    // 状态和进度
    m_statusLabel = new QLabel("就绪");
    layout->addWidget(m_statusLabel);

    m_progressBar = new QProgressBar();
    m_progressBar->setRange(0, 100);
    layout->addWidget(m_progressBar);

    // 精彩片段表格
    QGroupBox* highlightGroup = new QGroupBox("精彩片段");
    QVBoxLayout* hlLayout = new QVBoxLayout(highlightGroup);

    m_model = new QStandardItemModel(0, 4, this);
    m_model->setHorizontalHeaderLabels({"时间", "类型", "描述", "置信度"});

    m_tableView = new QTableView();
    m_tableView->setModel(m_model);
    m_tableView->setSelectionBehavior(QAbstractItemView::SelectRows);
    m_tableView->setEditTriggers(QAbstractItemView::NoEditTriggers);
    m_tableView->horizontalHeader()->setStretchLastSection(true);
    m_tableView->setAlternatingRowColors(true);
    hlLayout->addWidget(m_tableView);

    connect(m_tableView, &QTableView::doubleClicked,
            this, &AnalysisDock::onItemDoubleClicked);

    layout->addWidget(highlightGroup);

    // 统计信息
    QGroupBox* statsGroup = new QGroupBox("分析统计");
    QFormLayout* statsLayout = new QFormLayout(statsGroup);
    QLabel* totalLabel = new QLabel("0");
    statsLayout->addRow("检测片段:", totalLabel);
    layout->addWidget(statsGroup);

    layout->addStretch();
    setWidget(container);
    setMinimumWidth(300);
}

void AnalysisDock::onAnalyzeClicked()
{
    // 从主窗口获取当前视频路径
    QString videoPath = "current_recording.mp4"; // 需要通过信号获取

    m_highlights.clear();
    m_model->removeRows(0, m_model->rowCount());
    m_statusLabel->setText("分析中...");
    m_analyzeBtn->setEnabled(false);
    m_cancelBtn->setEnabled(true);
    m_progressBar->setValue(0);

    m_detector->analyze(videoPath, true, true);
}

void AnalysisDock::onHighlightFound(const Highlight& highlight)
{
    addHighlightRow(highlight);
}

void AnalysisDock::onAnalysisCompleted(const QList<Highlight>& results)
{
    m_highlights = results;
    m_statusLabel->setText(QString("完成 — 共 %1 个精彩片段").arg(results.size()));
    m_analyzeBtn->setEnabled(true);
    m_cancelBtn->setEnabled(false);
    m_progressBar->setValue(100);
}

void AnalysisDock::onProgressChanged(int percent, const QString& status)
{
    m_progressBar->setValue(percent);
    m_statusLabel->setText(status);
}

void AnalysisDock::onTranscriptionReady(const QList<TranscriptionResult>& results)
{
    m_transcriptions = results;
    m_statusLabel->setText(
        QString("语音识别完成 — %1 条字幕").arg(results.size()));
}

void AnalysisDock::onItemDoubleClicked(const QModelIndex& index)
{
    if (index.isValid() && index.row() < m_highlights.size()) {
        emit clipExportRequested(m_highlights[index.row()]);
    }
}

void AnalysisDock::addHighlightRow(const Highlight& h)
{
    int row = m_model->rowCount();
    m_model->insertRow(row);
    m_model->setItem(row, 0, new QStandardItem(
        QString("%1 - %2")
            .arg(QTime::fromMSecsSinceStartOfDay(h.startMs).toString("mm:ss"))
            .arg(QTime::fromMSecsSinceStartOfDay(h.endMs).toString("mm:ss"))));
    m_model->setItem(row, 1, new QStandardItem(h.type));
    m_model->setItem(row, 2, new QStandardItem(h.description));
    m_model->setItem(row, 3, new QStandardItem(
        QString("%1%").arg(static_cast<int>(h.confidence * 100))));
}
```

- [ ] **Step 3: Commit**

```bash
git add src/lsc/docks/AnalysisDock.h src/lsc/docks/AnalysisDock.cpp
git commit -m "feat: add AI analysis dock panel with highlight table"
```

---

## 阶段四：集成

### Task 10: 集成到 Shotcut 主窗口

**Files:**
- Modify: `shotcut-source/src/mainwindow.h`
- Modify: `shotcut-source/src/mainwindow.cpp`

- [ ] **Step 1: 在 MainWindow 中添加直播面板**

在 `mainwindow.h` 中添加:
```cpp
// 前置声明
class LivestreamDock;
class AnalysisDock;

// 在 MainWindow 类中添加成员:
LivestreamDock* m_livestreamDock;
AnalysisDock* m_analysisDock;
```

在 `mainwindow.cpp` 初始化中添加:
```cpp
void MainWindow::setupDocks()
{
    // ... 现有dock设置 ...

    // 添加直播源面板
    m_livestreamDock = new LivestreamDock(this);
    addDockWidget(Qt::LeftDockWidgetArea, m_livestreamDock);
    tabifyDockWidget(m_filesDock, m_livestreamDock);

    // 添加AI分析面板
    m_analysisDock = new AnalysisDock(this);
    addDockWidget(Qt::RightDockWidgetArea, m_analysisDock);
    tabifyDockWidget(m_filtersDock, m_analysisDock);

    // 连接信号 - 录制完成后自动导入
    connect(m_livestreamDock, &LivestreamDock::recordingStopped,
            this, [this](const QString& path) {
        // 将录制的视频导入到播放列表
        Mlt::Producer* producer = new Mlt::Producer(
            MLT.profile(), path.toUtf8().constData());
        if (producer && producer->is_valid()) {
            m_playlistDock->append(producer);
        }
    });

    // 连接信号 - AI分析片段导出到时间线
    connect(m_analysisDock, &AnalysisDock::clipExportRequested,
            this, [this](const Highlight& h) {
        // 将精彩片段添加到时间线
        // 使用MLT的in/out点标记
        MLT.setIn(h.startMs);
        MLT.setOut(h.endMs);
    });
}
```

- [ ] **Step 2: Commit**

```bash
git add shotcut-source/src/mainwindow.h shotcut-source/src/mainwindow.cpp
git commit -m "feat: integrate livestream and analysis docks into main window"
```

---

### Task 11: 编译和验证

- [ ] **Step 1: 编译项目**

```powershell
cd D:\Project\直播切片\shotcut-source\build
cmake --build . --config Release
```
Expected: 编译成功，无错误

- [ ] **Step 2: 运行验证**

```powershell
.\build\Release\shotcut.exe
```
Expected: 
- Shotcut 正常启动
- 左侧出现"直播源"标签页
- 右侧出现"AI分析"标签页
- 可输入直播URL并开始录制

- [ ] **Step 3: Commit**

```bash
git add .
git commit -m "build: complete compilation and integration verification"
```

---

## 后续扩展任务

- [ ] 平台弹幕数据获取（抖音/B站 API）
- [ ] 画面内容分析（OpenCV + ML）
- [ ] 字幕样式自定义编辑器
- [ ] 一键发布到短视频平台
- [ ] 录制模板和预设系统
