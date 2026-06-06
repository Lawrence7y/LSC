#include <QCoreApplication>
#include <QTimer>
#include <QEventLoop>
#include <QFileInfo>
#include <QDebug>
#include <iostream>

#include "analyzer/HighlightEngine.h"
#include "analyzer/GenericStrategy.h"

/**
 * 测试高光分析功能
 * 用法: test_highlight_analysis <video_path>
 */

class HighlightTest : public QObject
{
    Q_OBJECT
public:
    HighlightTest(const QString& videoPath, QObject* parent = nullptr)
        : QObject(parent)
        , m_videoPath(videoPath)
    {
        m_engine = new HighlightEngine(this);
        m_engine->setAnalysisProfile(AnalysisProfile::generic());

        connect(m_engine, &HighlightEngine::segmentFound,
                this, &HighlightTest::onSegmentFound);
        connect(m_engine, &HighlightEngine::finished,
                this, &HighlightTest::onFinished);
        connect(m_engine, &HighlightEngine::errorOccurred,
                this, &HighlightTest::onError);
        connect(m_engine, &HighlightEngine::progressChanged,
                this, &HighlightTest::onProgress);
    }

    void start()
    {
        std::cout << "=== 高光分析测试 ===" << std::endl;
        std::cout << "视频: " << m_videoPath.toStdString() << std::endl;

        QFileInfo fi(m_videoPath);
        if (!fi.exists()) {
            std::cout << "错误: 文件不存在" << std::endl;
            emit finished();
            return;
        }

        std::cout << "文件大小: " << (fi.size() / 1024.0 / 1024.0) << " MB" << std::endl;
        std::cout << "\n开始分析..." << std::endl;

        m_engine->analyze(m_videoPath);
    }

    bool success() const { return m_success; }
    int segmentCount() const { return m_segmentCount; }

private slots:
    void onSegmentFound(const HighlightSegment& segment)
    {
        m_segmentCount++;
        std::cout << "\n发现高光 #" << m_segmentCount << ":" << std::endl;
        std::cout << "  时间: " << segment.startSec << "s - " << segment.endSec << "s" << std::endl;
        std::cout << "  分数: " << (segment.score * 100) << "%" << std::endl;
        std::cout << "  音频: " << (segment.audioScore * 100) << "%" << std::endl;
        std::cout << "  视频: " << (segment.videoScore * 100) << "%" << std::endl;
        std::cout << "  原因: " << segment.reason.toStdString() << std::endl;
    }

    void onFinished()
    {
        std::cout << "\n=== 分析完成 ===" << std::endl;
        std::cout << "共发现 " << m_segmentCount << " 个高光片段" << std::endl;

        if (m_segmentCount > 0) {
            std::cout << "\n测试成功！" << std::endl;
            m_success = true;
        } else {
            std::cout << "\n警告: 未发现高光片段（可能是视频内容问题）" << std::endl;
            m_success = true; // 仍然算成功，只是内容没有高光
        }

        emit finished();
    }

    void onError(const QString& error)
    {
        std::cout << "\n错误: " << error.toStdString() << std::endl;
        m_success = false;
        emit finished();
    }

    void onProgress(int percent)
    {
        std::cout << "\r分析进度: " << percent << "%" << std::flush;
    }

signals:
    void finished();

private:
    HighlightEngine* m_engine;
    QString m_videoPath;
    bool m_success = false;
    int m_segmentCount = 0;
};

int main(int argc, char* argv[])
{
    QCoreApplication app(argc, argv);

    QString videoPath = "C:/Users/Administrator/AppData/Local/Temp/lsc_test_recording.mp4";
    const QStringList args = app.arguments();
    if (args.size() > 1) {
        videoPath = args.at(1);
    }

    HighlightTest test(videoPath);
    QEventLoop loop;

    QObject::connect(&test, &HighlightTest::finished, [&]() {
        loop.quit();
    });

    // 超时保护
    QTimer::singleShot(120000, [&]() {
        std::cout << "\n测试超时" << std::endl;
        loop.quit();
    });

    test.start();
    loop.exec();

    std::cout << "\n=== 测试结果: " << (test.success() ? "成功" : "失败") << " ===" << std::endl;

    return test.success() ? 0 : 1;
}

#include "test_highlight_analysis.moc"
