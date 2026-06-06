#ifndef AUDIOANALYZER_H
#define AUDIOANALYZER_H

/**
 * @file AudioAnalyzer.h
 * @brief 音频分析器 — 通过 FFmpeg 检测音频能量和静音段
 *
 * 分析流程（两阶段，均为异步）：
 * 1. silencedetect：识别静音段，反推出有声段（活动音频区域）
 * 2. volumedetect：获取整体响度统计（均值、峰值）
 *
 * 输出：
 * - segments: 有声段时间区间列表
 * - overallLoudness: 整体均值响度 (dB)
 * - peakDb: 峰值响度 (dB)
 *
 * 注意：两个阶段是串行的（volumedetect 在 silencedetect 完成后启动），
 * 以避免同时启动两个 FFmpeg 进程读同一文件。
 */

#include <QObject>
#include <QProcess>
#include <QVector>

/**
 * @brief 音频段信息
 *
 * 表示一个有声（非静音）的时间区间。
 * energy 字段用于后续高光评分。
 */
struct AudioSegment {
    double startSec;   ///< 开始时间（秒）
    double endSec;     ///< 结束时间（秒）
    double maxDb;      ///< 最大响度 (dB)
    double rmsDb;      ///< 均方根响度 (dB)
    double energy;     ///< 归一化能量 (0.0-1.0)
};

class AudioAnalyzer : public QObject
{
    Q_OBJECT
public:
    explicit AudioAnalyzer(QObject* parent = nullptr);

    /**
     * @brief 开始音频分析
     * @param videoPath 视频文件路径
     * @param intervalSec 分析间隔（秒），保留参数，当前未使用
     */
    void analyze(const QString& videoPath, double intervalSec = 1.0);

    /** @brief 取消正在进行的分析 */
    void cancel();

    bool isRunning() const;

    const QVector<AudioSegment>& segments() const { return m_segments; }
    double overallLoudness() const { return m_overallLoudness; }
    double peakDb() const { return m_peakDb; }

signals:
    void progressChanged(int percent);
    void finished();
    void errorOccurred(const QString& error);

private slots:
    void onSilenceDetectFinished(int exitCode, QProcess::ExitStatus status);
    void onVolumeDetectFinished(int exitCode, QProcess::ExitStatus status);
    void onProcessError(QProcess::ProcessError error);

private:
    void parseSilenceOutput(const QByteArray& output);
    void parseVolumeOutput(const QByteArray& output);
    void startVolumeDetect(const QString& videoPath);

    QProcess* m_silenceProcess;   ///< silencedetect 进程
    QProcess* m_volumeProcess;    ///< volumedetect 进程
    QVector<AudioSegment> m_segments;
    double m_overallLoudness = -70.0;
    double m_peakDb = -70.0;
    QString m_videoPath;          ///< 保存路径供第二阶段使用
    bool m_running = false;
};

#endif // AUDIOANALYZER_H
