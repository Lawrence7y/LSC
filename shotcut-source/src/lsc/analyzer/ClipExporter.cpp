#include "ClipExporter.h"
#include "LscConfig.h"
#include <QDir>
#include <QFileInfo>
#include <QDateTime>
#include <QDebug>
#ifdef Q_OS_WIN
#include <windows.h>
#endif

ClipExporter::ClipExporter(QObject* parent)
    : QObject(parent)
    , m_process(new QProcess(this))
    , m_outputDir(QDir::homePath() + "/Videos/LiveClips")
{
    connect(m_process, QOverload<int, QProcess::ExitStatus>::of(&QProcess::finished),
            this, &ClipExporter::onProcessFinished);
}

QString ClipExporter::defaultHighlightDirForSource(const QString& sourcePath)
{
    const QFileInfo sourceInfo(sourcePath);
    if (sourceInfo.absolutePath().isEmpty()) {
        return QDir::homePath() + QStringLiteral("/Videos/LiveClips/highlights");
    }
    return sourceInfo.dir().filePath(QStringLiteral("highlights"));
}

void ClipExporter::setOutputDir(const QString& dir)
{
    m_outputDir = dir;
    QDir().mkpath(m_outputDir);
}

void ClipExporter::setExportConfig(const ExportConfig& config)
{
    m_exportConfig = config;
    if (!config.outputDir.isEmpty()) {
        setOutputDir(config.outputDir);
    }
}

void ClipExporter::exportClip(const ClipJob& job)
{
    m_queue.append(job);
    if (!m_running) processNext();
}

void ClipExporter::cancel()
{
    m_queue.clear();
    if (m_process->state() == QProcess::Running) {
        m_process->kill();
        m_process->waitForFinished(3000);
    }
    m_running = false;
    m_currentIndex = -1;
}

void ClipExporter::exportBatch(const QVector<ClipJob>& jobs, const ExportConfig& config)
{
    if (jobs.isEmpty()) return;

    m_batchQueue = jobs;
    m_batchConfig = config;
    m_batchCompleted = 0;
    m_batchFailed = 0;
    m_batchCancelled = false;
    m_batchRetryAttempt = 0;

    if (!config.outputDir.isEmpty()) {
        setOutputDir(config.outputDir);
    }

    for (auto& job : m_batchQueue) {
        if (job.outputPath.isEmpty()) {
            job.outputPath = m_outputDir + "/" + job.title + ".mp4";
        }
    }

    emit batchProgress(0, m_batchQueue.size(), 0);
    processBatchNext();
}

void ClipExporter::cancelBatch()
{
    m_batchCancelled = true;
    m_batchQueue.clear();
    if (m_process->state() == QProcess::Running) {
        m_process->kill();
        m_process->waitForFinished(3000);
    }
    emit allBatchFinished(m_batchCompleted, m_batchFailed);
}

int ClipExporter::pendingCount() const
{
    return m_batchQueue.size();
}

int ClipExporter::completedCount() const
{
    return m_batchCompleted;
}

int ClipExporter::failedCount() const
{
    return m_batchFailed;
}

void ClipExporter::processBatchNext()
{
    if (m_batchCancelled || m_batchQueue.isEmpty()) {
        if (!m_batchCancelled) {
            emit allBatchFinished(m_batchCompleted, m_batchFailed);
        }
        return;
    }

    ClipJob& job = m_batchQueue.first();
    QDir().mkpath(QFileInfo(job.outputPath).absolutePath());

    const QStringList args = buildFfmpegArgs(job);

    m_process->setProgram(lsc::LscConfig::instance().ffmpegProgram());
    m_process->setArguments(args);
    m_process->setProcessChannelMode(QProcess::ForwardedErrorChannel);
    m_process->start();
#ifdef Q_OS_WIN
    if (m_process->processId()) {
        HANDLE h = OpenProcess(PROCESS_SET_INFORMATION, FALSE, m_process->processId());
        if (h) { SetPriorityClass(h, BELOW_NORMAL_PRIORITY_CLASS); CloseHandle(h); }
    }
#endif
}

bool ClipExporter::isRunning() const { return m_running; }

QStringList ClipExporter::buildFfmpegArgs(const ClipJob& job) const
{
    const ExportConfig& cfg = job.config.outputDir.isEmpty() ? m_exportConfig : job.config;
    const double duration = job.endSec - job.startSec;

    QStringList args;
    args << QStringLiteral("-y")
         << QStringLiteral("-ss") << QString::number(job.startSec, 'f', 3)
         << QStringLiteral("-i") << job.sourcePath
         << QStringLiteral("-t") << QString::number(duration, 'f', 3);

    // 视频编码
    if (cfg.codec == QStringLiteral("copy") && !cfg.verticalCrop
        && cfg.width == 0 && cfg.height == 0) {
        args << QStringLiteral("-c") << QStringLiteral("copy")
             << QStringLiteral("-avoid_negative_ts") << QStringLiteral("make_zero");
    } else {
        QString vcodec = cfg.codec;
        if (vcodec == QStringLiteral("copy")) vcodec = QStringLiteral("libx264");
        if (vcodec == QStringLiteral("h264")) vcodec = QStringLiteral("libx264");
        if (vcodec == QStringLiteral("h265")) vcodec = QStringLiteral("libx265");

        args << QStringLiteral("-c:v") << vcodec
             << QStringLiteral("-crf") << QString::number(cfg.crf);

        if (cfg.bitrate > 0) {
            args << QStringLiteral("-b:v") << (QString::number(cfg.bitrate) + QStringLiteral("k"));
        }

        // 分辨率
        if (cfg.width > 0 && cfg.height > 0) {
            args << QStringLiteral("-s") << (QString::number(cfg.width) + QStringLiteral("x") + QString::number(cfg.height));
        }

        // 竖屏裁切
        if (cfg.verticalCrop) {
            const QString cropFilter = QString("crop=%1:%2:%3:%4")
                .arg(cfg.cropWidth).arg(cfg.cropHeight)
                .arg(cfg.cropX).arg(cfg.cropY);
            args << QStringLiteral("-vf") << cropFilter;
        }

        args << QStringLiteral("-c:a") << QStringLiteral("aac")
             << QStringLiteral("-b:a") << QStringLiteral("128k");
    }

    // 字幕烧录
    if (cfg.burnSubtitles && !cfg.subtitlePath.isEmpty()) {
        QString subFilter = QStringLiteral("subtitles=") + cfg.subtitlePath;
        if (!cfg.subtitleStyle.isEmpty()) {
            subFilter += QStringLiteral(":force_style='") + cfg.subtitleStyle + QStringLiteral("'");
        }
        // 如果已有 -vf，需要追加
        int vfIdx = args.indexOf(QStringLiteral("-vf"));
        if (vfIdx >= 0 && vfIdx + 1 < args.size()) {
            args[vfIdx + 1] = args[vfIdx + 1] + QStringLiteral(",") + subFilter;
        } else {
            args << QStringLiteral("-vf") << subFilter;
        }
    }

    // 元数据
    if (!cfg.title.isEmpty()) {
        args << QStringLiteral("-metadata") << (QStringLiteral("title=") + cfg.title);
    }
    if (!cfg.description.isEmpty()) {
        args << QStringLiteral("-metadata") << (QStringLiteral("description=") + cfg.description);
    }
    for (const QString& tag : cfg.tags) {
        args << QStringLiteral("-metadata") << (QStringLiteral("tag=") + tag);
    }

    args << QStringLiteral("-movflags") << QStringLiteral("+faststart");
    args << job.outputPath;

    return args;
}

void ClipExporter::processNext()
{
    if (m_queue.isEmpty() || m_currentIndex + 1 >= m_queue.size()) {
        m_queue.clear();
        m_currentIndex = -1;
        m_running = false;
        emit allFinished();
        return;
    }

    m_currentIndex++;
    const ClipJob& job = m_queue[m_currentIndex];
    m_running = true;

    QDir().mkpath(QFileInfo(job.outputPath).absolutePath());

    const QStringList args = buildFfmpegArgs(job);

    m_process->setProgram(lsc::LscConfig::instance().ffmpegProgram());
    m_process->setArguments(args);
    m_process->setProcessChannelMode(QProcess::ForwardedErrorChannel);
    m_process->start();
#ifdef Q_OS_WIN
    if (m_process->processId()) {
        HANDLE h = OpenProcess(PROCESS_SET_INFORMATION, FALSE, m_process->processId());
        if (h) { SetPriorityClass(h, BELOW_NORMAL_PRIORITY_CLASS); CloseHandle(h); }
    }
#endif
}

void ClipExporter::onProcessFinished(int exitCode, QProcess::ExitStatus status)
{
    // 批量模式
    if (!m_batchQueue.isEmpty() && m_currentIndex < 0) {
        ClipJob& job = m_batchQueue.first();
        int total = m_batchQueue.size() + m_batchCompleted + m_batchFailed;

        if (exitCode == 0 && status == QProcess::NormalExit) {
            m_batchCompleted++;
            emit clipExported(job.outputPath, job.title);
            m_batchRetryAttempt = 0;
            m_batchQueue.removeFirst();
            emit batchProgress(m_batchCompleted, total, m_batchFailed);
            processBatchNext();
        } else {
            if (m_batchRetryAttempt < m_batchConfig.retryCount) {
                m_batchRetryAttempt++;
                job.config.codec = QStringLiteral("h264");
                processBatchNext();
            } else {
                m_batchFailed++;
                QString err = QString::fromUtf8(m_process->readAllStandardError());
                emit exportError(job.outputPath, err);
                m_batchRetryAttempt = 0;
                m_batchQueue.removeFirst();
                emit batchProgress(m_batchCompleted, total, m_batchFailed);
                processBatchNext();
            }
        }
        return;
    }

    // 单个模式
    if (m_currentIndex < 0 || m_currentIndex >= m_queue.size()) return;

    const ClipJob& job = m_queue[m_currentIndex];

    if (exitCode == 0 && status == QProcess::NormalExit) {
        emit clipExported(job.outputPath, job.title);

        const ExportConfig& cfg = job.config.outputDir.isEmpty() ? m_exportConfig : job.config;
        if (cfg.generateThumbnail) {
            generateThumbnail(job);
        }
    } else {
        QString err = QString::fromUtf8(m_process->readAllStandardError());
        emit exportError(job.outputPath, err);
    }

    processNext();
}

void ClipExporter::generateThumbnail(const ClipJob& job)
{
    const ExportConfig& cfg = job.config.outputDir.isEmpty() ? m_exportConfig : job.config;
    const double duration = job.endSec - job.startSec;
    const double thumbTime = cfg.thumbnailTimeSec > 0
        ? cfg.thumbnailTimeSec
        : duration / 2.0;

    const QFileInfo fi(job.outputPath);
    const QString thumbPath = fi.absolutePath() + QStringLiteral("/")
        + fi.completeBaseName() + QStringLiteral("_thumb.jpg");

    const QStringList thumbArgs = {
        QStringLiteral("-y"),
        QStringLiteral("-ss"), QString::number(job.startSec + thumbTime, 'f', 3),
        QStringLiteral("-i"), job.sourcePath,
        QStringLiteral("-vframes"), QStringLiteral("1"),
        QStringLiteral("-s"), QString::number(cfg.thumbnailWidth) + QStringLiteral("x") + QString::number(cfg.thumbnailHeight),
        thumbPath
    };

    QProcess thumbProc;
    thumbProc.setProgram(lsc::LscConfig::instance().ffmpegProgram());
    thumbProc.setArguments(thumbArgs);
    thumbProc.start();
    thumbProc.waitForFinished(10000);

    if (thumbProc.exitCode() == 0) {
        emit thumbnailGenerated(job.outputPath, thumbPath);
    }
}
