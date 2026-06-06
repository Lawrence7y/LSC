#include "ThumbnailGenerator.h"
#include "LscConfig.h"
#include <QDebug>

ThumbnailGenerator::ThumbnailGenerator(QObject* parent)
    : QObject(parent)
    , m_process(new QProcess(this))
{
    connect(m_process, QOverload<int, QProcess::ExitStatus>::of(&QProcess::finished),
            this, &ThumbnailGenerator::onProcessFinished);
    connect(m_process, &QProcess::readyReadStandardOutput,
            this, &ThumbnailGenerator::onProcessReadyRead);
}

ThumbnailGenerator::~ThumbnailGenerator()
{
    cancel();
}

void ThumbnailGenerator::generate(const QString& videoPath, const QVector<double>& timestamps,
                                  int width, int height)
{
    if (m_running) {
        cancel();
    }

    m_videoPath = videoPath;
    m_timestamps = timestamps;
    m_width = width;
    m_height = height;
    m_currentIndex = -1;
    m_running = true;

    processNext();
}

void ThumbnailGenerator::cancel()
{
    if (m_process->state() == QProcess::Running) {
        m_process->kill();
        m_process->waitForFinished(3000);
    }
    m_running = false;
    m_currentIndex = -1;
    m_timestamps.clear();
    m_outputBuffer.clear();
}

void ThumbnailGenerator::processNext()
{
    m_currentIndex++;
    m_outputBuffer.clear();

    if (m_currentIndex >= m_timestamps.size()) {
        m_running = false;
        emit allFinished();
        return;
    }

    emit progressUpdated(m_currentIndex, m_timestamps.size());

    double timestamp = m_timestamps[m_currentIndex];

    // 使用 FFmpeg 提取单帧
    // -ss 放在 -i 之前：快速定位（输入寻址）
    // -frames:v 1：只取一帧
    // -f image2pipe：输出 PNG 到 stdout
    QStringList args;
    args << "-hide_banner" << "-loglevel" << "error"
         << "-ss" << QString::number(timestamp, 'f', 3)
         << "-i" << m_videoPath
         << "-frames:v" << "1"
         << "-vf" << QString("scale=%1:%2:force_original_aspect_ratio=decrease,pad=%1:%2:(ow-iw)/2:(oh-ih)/2")
                .arg(m_width).arg(m_height)
         << "-f" << "image2pipe"
         << "-c:v" << "png"
         << "pipe:1";

    m_process->setProgram(lsc::LscConfig::instance().ffmpegProgram());
    m_process->setArguments(args);
    m_process->start();
}

void ThumbnailGenerator::onProcessReadyRead()
{
    m_outputBuffer.append(m_process->readAllStandardOutput());
}

void ThumbnailGenerator::onProcessFinished(int exitCode, QProcess::ExitStatus status)
{
    if (m_currentIndex < 0 || m_currentIndex >= m_timestamps.size()) return;

    double timestamp = m_timestamps[m_currentIndex];

    if (exitCode == 0 && status == QProcess::NormalExit && !m_outputBuffer.isEmpty()) {
        QImage thumbnail;
        if (thumbnail.loadFromData(m_outputBuffer, "PNG")) {
            emit thumbnailReady(timestamp, thumbnail);
        } else {
            emit errorOccurred(QString("Failed to decode thumbnail at %1s").arg(timestamp));
        }
    } else {
        QString err = QString::fromUtf8(m_process->readAllStandardError());
        emit errorOccurred(QString("FFmpeg error at %1s: %2").arg(timestamp).arg(err));
    }

    // 继续处理下一个
    processNext();
}
