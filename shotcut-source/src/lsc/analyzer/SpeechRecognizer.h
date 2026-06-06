#ifndef SPEECHRECOGNIZER_H
#define SPEECHRECOGNIZER_H

/**
 * @file SpeechRecognizer.h
 * @brief 语音识别器 — 通过 whisper.cpp 将音频转录为文字
 *
 * 工作流程：
 * 1. 调用 whisper-cli 对视频文件进行语音识别
 * 2. whisper-cli 生成 SRT 字幕文件
 * 3. 解析 SRT 文件，提取时间戳和文字
 *
 * 依赖：
 * - whisper-cli 必须在系统 PATH 中
 * - 模型文件（默认 models/ggml-base.bin）必须存在
 *
 * 注意：
 * - whisper-cli 的信息输出在 stderr 中，不是 stdout
 * - SRT 格式不含置信度信息，使用默认值
 * - 支持中文、英文等多种语言
 */

#include <QObject>
#include <QProcess>
#include <QVector>
#include <QString>

/**
 * @brief 字幕条目
 *
 * 表示一段识别出的语音，包含时间区间和文字内容。
 */
struct SubtitleEntry {
    double startSec;     ///< 开始时间（秒）
    double endSec;       ///< 结束时间（秒）
    QString text;        ///< 识别出的文字
    double confidence;   ///< 置信度 (0.0-1.0)，SRT 格式使用默认值
};

class SpeechRecognizer : public QObject
{
    Q_OBJECT
public:
    explicit SpeechRecognizer(QObject* parent = nullptr);

    void setModelPath(const QString& path);
    void setLanguage(const QString& lang);

    /**
     * @brief 开始语音识别
     * @param audioPath 音频/视频文件路径
     *
     * 识别完成后通过 finished 信号通知。
     * SRT 文件会生成在 audioPath + ".srt"。
     */
    void transcribe(const QString& audioPath);

    /** @brief 取消识别 */
    void cancel();

    bool isRunning() const;

    const QVector<SubtitleEntry>& subtitles() const { return m_subtitles; }

signals:
    void progressChanged(int percent);
    void finished();
    void errorOccurred(const QString& error);

private slots:
    void onProcessFinished(int exitCode, QProcess::ExitStatus status);
    void onProcessError(QProcess::ProcessError error);

private:
    void parseSrtOutput(const QString& srtContent);
    void cleanupSrtFile();

    QProcess* m_process;
    QVector<SubtitleEntry> m_subtitles;
    QString m_modelPath;
    QString m_language;
    QString m_srtPath;
    bool m_running = false;
};

#endif // SPEECHRECOGNIZER_H
