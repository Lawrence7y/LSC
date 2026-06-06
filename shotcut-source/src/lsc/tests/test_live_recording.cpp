#include <QCoreApplication>
#include <QTimer>
#include <QEventLoop>
#include <QDir>
#include <QFileInfo>
#include <QDebug>
#include <iostream>

#include "livestream/RecordingSession.h"
#include "livestream/StreamCapture.h"

/**
 * 集成测试：直播流录制功能
 * 用法: test_live_recording <url> [duration_sec]
 */

class RecordingTest : public QObject
{
    Q_OBJECT
public:
    RecordingTest(const QString& url, int durationSec, QObject* parent = nullptr)
        : QObject(parent)
        , m_url(url)
        , m_durationSec(durationSec)
    {
        m_session = new RecordingSession(this);

        connect(m_session, &RecordingSession::recordingStarted,
                this, &RecordingTest::onRecordingStarted);
        connect(m_session, &RecordingSession::recordingStopped,
                this, &RecordingTest::onRecordingStopped);
        connect(m_session, &RecordingSession::progressUpdated,
                this, &RecordingTest::onProgress);
        connect(m_session, &RecordingSession::errorOccurred,
                this, &RecordingTest::onError);
        connect(m_session, &RecordingSession::platformParsed,
                this, &RecordingTest::onPlatformParsed);
        connect(m_session, &RecordingSession::statusChanged,
                this, &RecordingTest::onStatusChanged);
    }

    void start()
    {
        std::cout << "=== 开始录制测试 ===" << std::endl;
        std::cout << "URL: " << m_url.toStdString() << std::endl;
        std::cout << "录制时长: " << m_durationSec << " 秒" << std::endl;

        RecordingConfig config;
        config.outputPath = QDir::tempPath() + "/lsc_test_recording.mp4";
        config.format = "mp4";
        config.encodeMode = EncodeMode::CRF;
        config.crf = 23;
        config.autoReconnect = true;
        config.reconnectRetries = 3;

        m_outputPath = config.outputPath;
        m_session->startRecording(m_url, config);
    }

    bool success() const { return m_success; }

private slots:
    void onPlatformParsed(const PlatformInfo& info)
    {
        std::cout << "\n平台解析完成:" << std::endl;
        std::cout << "  平台: " << info.platform.toStdString() << std::endl;
        std::cout << "  标题: " << info.title.toStdString() << std::endl;
        std::cout << "  主播: " << info.streamerName.toStdString() << std::endl;
        std::cout << "  首选画质: " << info.preferredQuality.toStdString() << std::endl;
    }

    void onRecordingStarted(const QString& path)
    {
        std::cout << "\n录制已开始: " << path.toStdString() << std::endl;
        m_recordingStarted = true;

        // 设置定时器在指定时长后停止录制
        QTimer::singleShot(m_durationSec * 1000, this, [this]() {
            std::cout << "\n达到目标时长，停止录制..." << std::endl;
            m_session->stopRecording();
        });
    }

    void onRecordingStopped(const QString& path, qint64 sizeBytes)
    {
        std::cout << "\n录制已停止" << std::endl;
        std::cout << "  文件: " << path.toStdString() << std::endl;
        std::cout << "  大小: " << (sizeBytes / 1024.0 / 1024.0) << " MB" << std::endl;

        // 验证文件存在
        QFileInfo fi(path);
        if (fi.exists() && fi.size() > 0) {
            std::cout << "\n=== 测试成功 ===" << std::endl;
            std::cout << "录制文件有效，大小: " << fi.size() << " 字节" << std::endl;
            m_success = true;
        } else {
            std::cout << "\n=== 测试失败 ===" << std::endl;
            std::cout << "录制文件不存在或为空" << std::endl;
            m_success = false;
        }

        emit finished();
    }

    void onProgress(qint64 durationMs, qint64 fileSizeBytes)
    {
        static int lastSecond = 0;
        int currentSecond = durationMs / 1000;
        if (currentSecond > lastSecond) {
            lastSecond = currentSecond;
            if (currentSecond % 5 == 0) {
                std::cout << "\r录制中: " << currentSecond << "秒, "
                          << (fileSizeBytes / 1024.0 / 1024.0) << " MB" << std::flush;
            }
        }
    }

    void onError(const QString& error)
    {
        std::cout << "\n错误: " << error.toStdString() << std::endl;
        m_success = false;
        emit finished();
    }

    void onStatusChanged(RecordingStatus status)
    {
        switch (status) {
        case RecordingStatus::Starting:
            std::cout << "\n状态: 启动中..." << std::endl;
            break;
        case RecordingStatus::Recording:
            std::cout << "\n状态: 录制中" << std::endl;
            break;
        case RecordingStatus::Reconnecting:
            std::cout << "\n状态: 重连中..." << std::endl;
            break;
        case RecordingStatus::Stopped:
            std::cout << "\n状态: 已停止" << std::endl;
            break;
        case RecordingStatus::Error:
            std::cout << "\n状态: 错误" << std::endl;
            break;
        }
    }

signals:
    void finished();

private:
    RecordingSession* m_session;
    QString m_url;
    QString m_outputPath;
    int m_durationSec;
    bool m_success = false;
    bool m_recordingStarted = false;
};

int main(int argc, char* argv[])
{
    QCoreApplication app(argc, argv);

    QString testUrl = "https://www.douyin.com/follow/live/53682367755?anchor_id=1566126704427592";
    int durationSec = 30; // 默认录制 30 秒

    if (argc > 1) {
        testUrl = argv[1];
    }
    if (argc > 2) {
        durationSec = std::atoi(argv[2]);
    }

    RecordingTest test(testUrl, durationSec);
    QEventLoop loop;

    QObject::connect(&test, &RecordingTest::finished, [&]() {
        loop.quit();
    });

    // 超时保护
    QTimer::singleShot((durationSec + 30) * 1000, [&]() {
        std::cout << "\n测试超时" << std::endl;
        loop.quit();
    });

    test.start();
    loop.exec();

    return test.success() ? 0 : 1;
}

#include "test_live_recording.moc"
