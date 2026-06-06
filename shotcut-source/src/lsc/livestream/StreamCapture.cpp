#include "StreamCapture.h"
#include "LscConfig.h"
#include "LscLog.h"

#include <QDir>
#include <QFileInfo>

#define MODULE_NAME "StreamCapture"

StreamCapture::StreamCapture(QObject* parent)
    : QObject(parent)
    , m_ffmpegProcess(new QProcess(this))
    , m_status(RecordingStatus::Stopped)
    , m_reconnectCount(0)
    , m_lastFileSize(0)
    , m_lastProgressBytes(0)
{
    connect(m_ffmpegProcess, &QProcess::started, this, &StreamCapture::onFfmpegStarted);
    connect(m_ffmpegProcess,
            &QProcess::errorOccurred,
            this,
            &StreamCapture::onFfmpegError);
    connect(m_ffmpegProcess,
            QOverload<int, QProcess::ExitStatus>::of(&QProcess::finished),
            this,
            &StreamCapture::onFfmpegFinished);
    connect(m_ffmpegProcess,
            &QProcess::readyReadStandardError,
            this,
            &StreamCapture::onFfmpegReadyRead);

    const auto& cfg = lsc::LscConfig::instance();
    m_progressTimer.setInterval(cfg.progressIntervalMs);
    connect(&m_progressTimer, &QTimer::timeout, this, &StreamCapture::updateProgress);
    m_stallTimer.setInterval(cfg.stallCheckIntervalMs);
    connect(&m_stallTimer, &QTimer::timeout, this, &StreamCapture::checkStall);
}

StreamCapture::~StreamCapture()
{
    stop();
}

bool StreamCapture::start(const QString& streamUrl, const RecordingConfig& config)
{
    if (m_status != RecordingStatus::Stopped
        && m_status != RecordingStatus::Error
        && m_status != RecordingStatus::Reconnecting) {
        emit errorOccurred(QString::fromUtf8("已在录制中，请先停止当前任务。"));
        return false;
    }
    if (streamUrl.isEmpty()) {
        emit errorOccurred(QString::fromUtf8("流地址不能为空。"));
        return false;
    }

    m_config = config;
    m_currentStreamUrl = streamUrl;
    m_reconnectCount = 0;
    m_lastFileSize = 0;
    m_lastProgressBytes = 0;
    m_accumulatedDurationMs = 0;
    m_lastDurationMs = 0;
    m_stopRequested = false;
    m_ffmpegErrorBuffer.clear();
    m_stallStartMs = 0;

    QFileInfo fi(m_config.outputPath);
    QDir().mkpath(fi.absolutePath());

    LSC_INFO(MODULE_NAME) << "开始录制, 输出:" << m_config.outputPath
                          << ", 编码模式:" << static_cast<int>(m_config.encodeMode);
    startFfmpeg(streamUrl);
    return true;
}

void StreamCapture::stop()
{
    if (m_status == RecordingStatus::Recording) {
        m_lastDurationMs = m_accumulatedDurationMs + m_elapsed.elapsed();
    } else {
        m_lastDurationMs = m_accumulatedDurationMs;
    }

    cleanupTimers();
    m_reconnectCount = 0;
    m_stopRequested = true;

    if (m_ffmpegProcess->state() == QProcess::Running) {
        LSC_DEBUG(MODULE_NAME) << "发送停止命令到 FFmpeg";
        m_ffmpegProcess->write("q");
        m_ffmpegProcess->closeWriteChannel();
        if (!m_ffmpegProcess->waitForFinished(8000)) {
            LSC_WARNING(MODULE_NAME) << "FFmpeg 未响应，执行强制结束";
            m_ffmpegProcess->kill();
            m_ffmpegProcess->waitForFinished(5000);
        }
    }

    setStatus(RecordingStatus::Stopped);
}

qint64 StreamCapture::duration() const
{
    if (m_status == RecordingStatus::Recording) {
        return m_accumulatedDurationMs + m_elapsed.elapsed();
    }
    if (m_status == RecordingStatus::Reconnecting) {
        return m_lastDurationMs > 0 ? m_lastDurationMs : m_accumulatedDurationMs;
    }
    return m_lastDurationMs;
}

qint64 StreamCapture::fileSize() const
{
    QFileInfo fi(m_config.outputPath);
    return fi.exists() ? fi.size() : 0;
}

void StreamCapture::setStatus(RecordingStatus status)
{
    if (m_status != status) {
        m_status = status;
        emit statusChanged(m_status);
    }
}

void StreamCapture::cleanupTimers()
{
    m_progressTimer.stop();
    m_stallTimer.stop();
}

bool StreamCapture::detectHardwareEncoder()
{
    if (m_config.hwEncoder == "none") {
        return false;
    }
    if (m_config.hwEncoder != "auto") {
        m_detectedHwEncoder = m_config.hwEncoder;
        return true;
    }

    QProcess probe;
    probe.setProgram(lsc::LscConfig::instance().ffmpegProgram());
    probe.setArguments({"-hide_banner", "-encoders"});
    probe.start();
    if (probe.waitForFinished(10000)) {
        const QString out = QString::fromUtf8(probe.readAllStandardOutput());
        // Check in order of preference: NVENC > QSV > AMF
        if (out.contains("h264_nvenc")) {
            m_detectedHwEncoder = "h264_nvenc";
            return true;
        }
        if (out.contains("h264_qsv")) {
            m_detectedHwEncoder = "h264_qsv";
            return true;
        }
        if (out.contains("h264_amf")) {
            m_detectedHwEncoder = "h264_amf";
            return true;
        }
    }
    return false;
}

QStringList StreamCapture::buildEncoderArgsStatic(const QString& streamUrl,
                                                 const RecordingConfig& config)
{
    QStringList args;
    args << "-y";

    if (config.autoReconnect) {
        args << "-reconnect" << "1"
             << "-reconnect_streamed" << "1"
             << "-reconnect_delay_max" << QString::number(qMax(1, config.reconnectDelayMs / 1000))
             << "-reconnect_at_eof" << "1"
             << "-rw_timeout" << "50000000";
    }

    args << "-re" << "-i" << streamUrl;

    QString vf;
    if (config.maxWidth > 0 && config.maxHeight > 0) {
        vf = QString("scale='min(%1,iw)':'min(%2,ih)':force_original_aspect_ratio=decrease")
                 .arg(config.maxWidth)
                 .arg(config.maxHeight);
    }

    switch (config.encodeMode) {
    case EncodeMode::StreamCopy:
        args << "-c:v" << "copy";
        break;
    case EncodeMode::Hardware:
        // For static build, we assume hardware encoder detection would be done separately
        // Default to libx264 as fallback
        args << "-c:v" << "libx264"
             << "-crf" << QString::number(config.crf)
             << "-preset" << config.preset;
        if (!vf.isEmpty()) {
            args << "-vf" << vf;
        }
        break;
    case EncodeMode::TargetBitrate:
        args << "-c:v" << "libx264"
             << "-b:v" << QString("%1k").arg(config.videoBitrate)
             << "-maxrate" << QString("%1k").arg(config.videoBitrate * 2)
             << "-bufsize" << QString("%1k").arg(config.videoBitrate * 4)
             << "-preset" << config.preset;
        if (!vf.isEmpty()) {
            args << "-vf" << vf;
        }
        break;
    case EncodeMode::CRF:
    default:
        args << "-c:v" << "libx264"
             << "-crf" << QString::number(config.crf)
             << "-preset" << config.preset;
        if (!vf.isEmpty()) {
            args << "-vf" << vf;
        }
        break;
    }

    args << "-c:a" << "aac"
         << "-b:a" << QString("%1k").arg(config.audioBitrate)
         << "-ac" << "2";

    args << "-f" << config.format;
    if (config.format.compare(QStringLiteral("mp4"), Qt::CaseInsensitive) == 0) {
        // Fragmented MP4 keeps the file readable while ffmpeg is still writing,
        // which is required for incremental highlight analysis during recording.
        // Note: +faststart is intentionally omitted — it conflicts with empty_moov
        // because faststart moves the moov atom to the start after encoding, but
        // fragmented MP4 uses empty_moov and has no traditional moov atom.
        args << "-movflags" << "+frag_keyframe+empty_moov+default_base_moof";
    }
    args << config.outputPath;

    return args;
}

QStringList StreamCapture::buildEncoderArgs(const QString& streamUrl) const
{
    return buildEncoderArgsStatic(streamUrl, m_config);
}

void StreamCapture::startFfmpeg(const QString& streamUrl)
{
    m_ffmpegErrorBuffer.clear();
    m_lastFileSize = 0;

    const QStringList args = buildEncoderArgs(streamUrl);
    LSC_DEBUG(MODULE_NAME) << "FFmpeg 启动参数: ffmpeg" << args.join(" ");

    m_ffmpegProcess->setProgram(lsc::LscConfig::instance().ffmpegProgram());
    m_ffmpegProcess->setArguments(args);
    setStatus(RecordingStatus::Starting);
    m_ffmpegProcess->start();
}

void StreamCapture::onFfmpegStarted()
{
    setStatus(RecordingStatus::Recording);
    m_elapsed.start();
    m_stopRequested = false;
    m_progressTimer.start();
    m_stallTimer.start();
}

void StreamCapture::onFfmpegError(QProcess::ProcessError error)
{
    Q_UNUSED(error)

    const QString err = m_ffmpegProcess->errorString();
    LSC_ERROR(MODULE_NAME) << "FFmpeg 进程错误:" << err;

    cleanupTimers();
    if (m_status == RecordingStatus::Recording) {
        m_accumulatedDurationMs += m_elapsed.elapsed();
    }
    m_lastDurationMs = m_accumulatedDurationMs;

    if (m_stopRequested) {
        setStatus(RecordingStatus::Stopped);
        return;
    }

    if (m_config.autoReconnect && m_reconnectCount < m_config.reconnectRetries) {
        setStatus(RecordingStatus::Reconnecting);
        emit needsReconnect(m_currentStreamUrl);
        return;
    }

    setStatus(RecordingStatus::Error);
    emit errorOccurred(err);
}

void StreamCapture::onFfmpegFinished(int exitCode, QProcess::ExitStatus exitStatus)
{
    cleanupTimers();

    if (m_status == RecordingStatus::Recording) {
        m_accumulatedDurationMs += m_elapsed.elapsed();
    }
    m_lastDurationMs = m_accumulatedDurationMs;

    LSC_DEBUG(MODULE_NAME) << "FFmpeg 结束, exitCode:" << exitCode
                           << "exitStatus:" << exitStatus;

    if (m_status == RecordingStatus::Stopped || m_stopRequested) {
        setStatus(RecordingStatus::Stopped);
        return;
    }

    if ((exitStatus == QProcess::CrashExit || exitCode != 0)
        && m_config.autoReconnect
        && m_reconnectCount < m_config.reconnectRetries) {
        setStatus(RecordingStatus::Reconnecting);
        emit needsReconnect(m_currentStreamUrl);
        return;
    }

    if (exitStatus == QProcess::CrashExit || exitCode != 0) {
        setStatus(RecordingStatus::Error);
        const QString message = m_ffmpegErrorBuffer.trimmed().isEmpty()
            ? QString("ffmpeg exited with code %1").arg(exitCode)
            : m_ffmpegErrorBuffer.trimmed();
        emit errorOccurred(message);
        return;
    }

    LSC_INFO(MODULE_NAME) << "FFmpeg 正常结束";
    setStatus(RecordingStatus::Stopped);
}

void StreamCapture::onFfmpegReadyRead()
{
    m_ffmpegErrorBuffer += QString::fromUtf8(m_ffmpegProcess->readAllStandardError());
}

void StreamCapture::updateProgress()
{
    if (m_status == RecordingStatus::Recording) {
        emit progressUpdated(duration(), fileSize());
    }
}

void StreamCapture::checkStall()
{
    if (m_status != RecordingStatus::Recording) {
        m_stallStartMs = 0;
        return;
    }

    const qint64 currentSize = fileSize();
    if (m_lastFileSize > 0 && currentSize == m_lastFileSize) {
        // File size hasn't changed since last check
        if (m_stallStartMs == 0) {
            m_stallStartMs = m_elapsed.elapsed();
        } else {
            const qint64 stallDurationMs = m_elapsed.elapsed() - m_stallStartMs;
            if (stallDurationMs >= m_config.stallTimeoutSec * 1000) {
                LSC_WARNING(MODULE_NAME) << "流停滞 " << stallDurationMs / 1000
                                         << " 秒，文件大小未继续增长";
                emit streamStalled();
                m_stallStartMs = 0;
            }
        }
    } else {
        // File is growing, reset stall timer
        m_stallStartMs = 0;
    }
    m_lastFileSize = currentSize;
}

void StreamCapture::attemptReconnect()
{
    if (m_status != RecordingStatus::Reconnecting || m_stopRequested) {
        return;
    }

    ++m_reconnectCount;
    LSC_INFO(MODULE_NAME) << "重连尝试" << m_reconnectCount
                          << "/" << m_config.reconnectRetries;
    startFfmpeg(m_currentStreamUrl);
}

#undef MODULE_NAME
