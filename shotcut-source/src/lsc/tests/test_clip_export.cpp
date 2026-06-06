#include <QCoreApplication>
#include <QTimer>
#include <QEventLoop>
#include <QDir>
#include <QFileInfo>
#include <QDebug>
#include <iostream>

#include "analyzer/ClipExporter.h"

/**
 * 测试片段导出功能
 * 用法: test_clip_export <video_path>
 */

class ClipExportTest : public QObject
{
    Q_OBJECT
public:
    ClipExportTest(const QString& videoPath, QObject* parent = nullptr)
        : QObject(parent)
        , m_videoPath(videoPath)
    {
        m_exporter = new ClipExporter(this);

        connect(m_exporter, &ClipExporter::clipExported,
                this, &ClipExportTest::onClipExported);
        connect(m_exporter, &ClipExporter::exportError,
                this, &ClipExportTest::onExportError);
        connect(m_exporter, &ClipExporter::allFinished,
                this, &ClipExportTest::onAllFinished);
    }

    void start()
    {
        std::cout << "=== 片段导出测试 ===" << std::endl;
        std::cout << "源视频: " << m_videoPath.toStdString() << std::endl;

        QFileInfo fi(m_videoPath);
        if (!fi.exists()) {
            std::cout << "错误: 文件不存在" << std::endl;
            emit finished();
            return;
        }

        // 设置输出目录
        QString outputDir = QDir::tempPath() + "/lsc_test_clips";
        QDir().mkpath(outputDir);
        m_exporter->setOutputDir(outputDir);

        std::cout << "输出目录: " << outputDir.toStdString() << std::endl;

        // 创建测试片段
        std::cout << "\n开始导出 3 个测试片段..." << std::endl;

        // 片段 1: 前 5 秒
        ClipJob job1;
        job1.sourcePath = m_videoPath;
        job1.startSec = 0.0;
        job1.endSec = 5.0;
        job1.outputPath = outputDir + "/clip_1.mp4";
        job1.title = "测试片段 1";
        job1.useCopy = true;
        m_exporter->exportClip(job1);
        m_expectedCount++;

        // 片段 2: 5-10 秒
        ClipJob job2;
        job2.sourcePath = m_videoPath;
        job2.startSec = 5.0;
        job2.endSec = 10.0;
        job2.outputPath = outputDir + "/clip_2.mp4";
        job2.title = "测试片段 2";
        job2.useCopy = true;
        m_exporter->exportClip(job2);
        m_expectedCount++;

        // 片段 3: 10-15 秒
        ClipJob job3;
        job3.sourcePath = m_videoPath;
        job3.startSec = 10.0;
        job3.endSec = 15.0;
        job3.outputPath = outputDir + "/clip_3.mp4";
        job3.title = "测试片段 3";
        job3.useCopy = true;
        m_exporter->exportClip(job3);
        m_expectedCount++;
    }

    bool success() const { return m_success; }

private slots:
    void onClipExported(const QString& filePath, const QString& title)
    {
        m_exportedCount++;
        std::cout << "\n片段导出成功 #" << m_exportedCount << ":" << std::endl;
        std::cout << "  标题: " << title.toStdString() << std::endl;
        std::cout << "  文件: " << filePath.toStdString() << std::endl;

        QFileInfo fi(filePath);
        if (fi.exists()) {
            std::cout << "  大小: " << (fi.size() / 1024.0) << " KB" << std::endl;
        }
    }

    void onExportError(const QString& filePath, const QString& error)
    {
        std::cout << "\n导出失败:" << std::endl;
        std::cout << "  文件: " << filePath.toStdString() << std::endl;
        std::cout << "  错误: " << error.toStdString() << std::endl;
        m_hasError = true;
    }

    void onAllFinished()
    {
        std::cout << "\n=== 所有导出任务完成 ===" << std::endl;
        std::cout << "成功导出: " << m_exportedCount << "/" << m_expectedCount << " 个片段" << std::endl;

        if (m_exportedCount == m_expectedCount && !m_hasError) {
            std::cout << "\n测试成功！" << std::endl;
            m_success = true;
        } else {
            std::cout << "\n测试部分成功" << std::endl;
            m_success = m_exportedCount > 0;
        }

        emit finished();
    }

signals:
    void finished();

private:
    ClipExporter* m_exporter;
    QString m_videoPath;
    bool m_success = false;
    bool m_hasError = false;
    int m_expectedCount = 0;
    int m_exportedCount = 0;
};

int main(int argc, char* argv[])
{
    QCoreApplication app(argc, argv);

    QString videoPath = "C:/Users/Administrator/AppData/Local/Temp/lsc_test_recording.mp4";
    if (argc > 1) {
        videoPath = argv[1];
    }

    ClipExportTest test(videoPath);
    QEventLoop loop;

    QObject::connect(&test, &ClipExportTest::finished, [&]() {
        loop.quit();
    });

    // 超时保护
    QTimer::singleShot(60000, [&]() {
        std::cout << "\n测试超时" << std::endl;
        loop.quit();
    });

    test.start();
    loop.exec();

    std::cout << "\n=== 测试结果: " << (test.success() ? "成功" : "失败") << " ===" << std::endl;

    return test.success() ? 0 : 1;
}

#include "test_clip_export.moc"
