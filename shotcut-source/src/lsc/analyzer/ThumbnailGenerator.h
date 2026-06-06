#ifndef THUMBNAILGENERATOR_H
#define THUMBNAILGENERATOR_H

/**
 * @file ThumbnailGenerator.h
 * @brief 缩略图生成器 — 从视频中提取指定时间点的关键帧
 *
 * 使用 FFmpeg 提取单帧 PNG 数据，转换为 QImage。
 * 逐个处理时间戳列表，每完成一个发出 thumbnailReady 信号。
 */

#include <QObject>
#include <QProcess>
#include <QImage>
#include <QVector>

class ThumbnailGenerator : public QObject
{
    Q_OBJECT
public:
    explicit ThumbnailGenerator(QObject* parent = nullptr);
    ~ThumbnailGenerator();

    /**
     * @brief 为指定时间点列表生成缩略图
     * @param videoPath 视频文件路径
     * @param timestamps 需要提取帧的时间点（秒）
     * @param width 缩略图宽度（默认160）
     * @param height 缩略图高度（默认90）
     */
    void generate(const QString& videoPath, const QVector<double>& timestamps,
                  int width = 160, int height = 90);

    /** @brief 取消当前所有生成任务 */
    void cancel();

    /** @brief 是否正在生成中 */
    bool isRunning() const { return m_running; }

signals:
    /** @brief 单个缩略图生成完成 */
    void thumbnailReady(double timestamp, const QImage& thumbnail);

    /** @brief 所有缩略图生成完成 */
    void allFinished();

    /** @brief 生成过程中发生错误 */
    void errorOccurred(const QString& message);

    /** @brief 进度更新 (current, total) */
    void progressUpdated(int current, int total);

private slots:
    void onProcessFinished(int exitCode, QProcess::ExitStatus status);
    void onProcessReadyRead();

private:
    void processNext();

    QProcess* m_process;
    QString m_videoPath;
    QVector<double> m_timestamps;
    int m_width = 160;
    int m_height = 90;
    int m_currentIndex = -1;
    bool m_running = false;
    QByteArray m_outputBuffer;
};

#endif // THUMBNAILGENERATOR_H
