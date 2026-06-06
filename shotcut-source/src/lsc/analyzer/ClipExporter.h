#ifndef CLIPEXPORTER_H
#define CLIPEXPORTER_H

#include <QObject>
#include <QProcess>
#include <QString>
#include <QStringList>
#include <QVector>

struct ExportConfig {
    // 输出设置
    QString outputDir;
    QString filenameTemplate = QStringLiteral("{streamer}_{date}_{index}");
    QString format = QStringLiteral("mp4");

    // 视频设置
    int width = 0;   // 0 表示保持原始
    int height = 0;
    int bitrate = 0; // 0 表示保持原始
    QString codec = QStringLiteral("copy"); // "copy" 或 "h264"/"h265"
    int crf = 23;

    // 竖屏裁切
    bool verticalCrop = false;
    double cropX = 0.1;
    double cropY = 0.0;
    double cropWidth = 0.8;
    double cropHeight = 1.0;

    // 字幕
    bool burnSubtitles = false;
    QString subtitlePath;
    QString subtitleStyle;

    // 封面
    bool generateThumbnail = true;
    int thumbnailTimeSec = 0; // 0 表示取中间
    int thumbnailWidth = 1280;
    int thumbnailHeight = 720;

    // 元数据
    QString title;
    QString description;
    QStringList tags;

    // 批量导出
    int retryCount = 2;
};

struct ClipJob {
    QString sourcePath;
    double startSec;
    double endSec;
    QString outputPath;
    QString title;
    bool useCopy = true;
    ExportConfig config;
};

class ClipExporter : public QObject
{
    Q_OBJECT
public:
    explicit ClipExporter(QObject* parent = nullptr);

    static QString defaultHighlightDirForSource(const QString& sourcePath);

    void exportClip(const ClipJob& job);
    void cancel();
    bool isRunning() const;

    void exportBatch(const QVector<ClipJob>& jobs, const ExportConfig& config);
    void cancelBatch();
    int pendingCount() const;
    int completedCount() const;
    int failedCount() const;

    QString outputDir() const { return m_outputDir; }
    void setOutputDir(const QString& dir);

    ExportConfig exportConfig() const { return m_exportConfig; }
    void setExportConfig(const ExportConfig& config);

signals:
    void clipExported(const QString& filePath, const QString& title);
    void exportError(const QString& filePath, const QString& error);
    void allFinished();
    void thumbnailGenerated(const QString& clipPath, const QString& thumbnailPath);

    void batchProgress(int completed, int total, int failed);
    void allBatchFinished(int successCount, int failCount);

private slots:
    void onProcessFinished(int exitCode, QProcess::ExitStatus status);

private:
    QStringList buildFfmpegArgs(const ClipJob& job) const;
    void generateThumbnail(const ClipJob& job);
    void processNext();
    void processBatchNext();

    QProcess* m_process;
    QVector<ClipJob> m_queue;
    int m_currentIndex = -1;
    QString m_outputDir;
    ExportConfig m_exportConfig;
    bool m_running = false;

    QVector<ClipJob> m_batchQueue;
    ExportConfig m_batchConfig;
    int m_batchCompleted = 0;
    int m_batchFailed = 0;
    bool m_batchCancelled = false;
    int m_batchRetryAttempt = 0;
};

#endif
