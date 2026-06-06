#include "AudioAnalyzer.h"
#include "LscConfig.h"
#include "LscLog.h"

#include <QFileInfo>
#include <QRegularExpression>
#include <limits>

#define MODULE_NAME "AudioAnalyzer"

namespace {
struct VolumeStats {
    double meanDb = -70.0;
    double maxDb = -70.0;
    bool valid = false;
};

double probeDurationSeconds(const QString& videoPath)
{
    QProcess probe;
    probe.setProgram(lsc::LscConfig::instance().ffprobeProgram());
    probe.setArguments({
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        videoPath,
    });

    probe.start();
    if (!probe.waitForFinished(10000) || probe.exitCode() != 0) {
        return 0.0;
    }

    bool ok = false;
    const double duration = QString::fromUtf8(probe.readAllStandardOutput()).trimmed().toDouble(&ok);
    return ok ? qMax(0.0, duration) : 0.0;
}

VolumeStats parseVolumeStats(const QString& text)
{
    VolumeStats stats;
    const QRegularExpression meanRegex(QStringLiteral("mean_volume:\\s*(-?[0-9.]+)\\s*dB"));
    const QRegularExpression maxRegex(QStringLiteral("max_volume:\\s*(-?[0-9.]+)\\s*dB"));

    const auto meanMatch = meanRegex.match(text);
    if (meanMatch.hasMatch()) {
        stats.meanDb = meanMatch.captured(1).toDouble();
        stats.valid = true;
    }

    const auto maxMatch = maxRegex.match(text);
    if (maxMatch.hasMatch()) {
        stats.maxDb = maxMatch.captured(1).toDouble();
        stats.valid = true;
    }

    return stats;
}

VolumeStats probeSegmentVolume(const QString& videoPath, double startSec, double endSec)
{
    const double durationSec = qMax(0.05, endSec - startSec);

    QProcess process;
    process.setProgram(lsc::LscConfig::instance().ffmpegProgram());
    process.setArguments({
        "-hide_banner",
        "-ss",
        QString::number(qMax(0.0, startSec), 'f', 3),
        "-t",
        QString::number(durationSec, 'f', 3),
        "-i",
        videoPath,
        "-vn",
        "-af",
        "volumedetect",
        "-f",
        "null",
        "NUL",
    });

    process.start();
    if (!process.waitForFinished(15000) || process.exitCode() != 0) {
        return {};
    }

    return parseVolumeStats(QString::fromUtf8(process.readAllStandardError()));
}
}

AudioAnalyzer::AudioAnalyzer(QObject* parent)
    : QObject(parent)
    , m_silenceProcess(new QProcess(this))
    , m_volumeProcess(new QProcess(this))
{
    connect(m_silenceProcess,
            QOverload<int, QProcess::ExitStatus>::of(&QProcess::finished),
            this,
            &AudioAnalyzer::onSilenceDetectFinished);
    connect(m_volumeProcess,
            QOverload<int, QProcess::ExitStatus>::of(&QProcess::finished),
            this,
            &AudioAnalyzer::onVolumeDetectFinished);
    connect(m_silenceProcess,
            &QProcess::errorOccurred,
            this,
            &AudioAnalyzer::onProcessError);
    connect(m_volumeProcess,
            &QProcess::errorOccurred,
            this,
            &AudioAnalyzer::onProcessError);
}

void AudioAnalyzer::analyze(const QString& videoPath, double intervalSec)
{
    Q_UNUSED(intervalSec)

    if (m_running) {
        cancel();
    }

    if (!QFileInfo::exists(videoPath)) {
        emit errorOccurred(QString("File not found: %1").arg(videoPath));
        return;
    }

    m_videoPath = videoPath;
    m_segments.clear();
    m_overallLoudness = -70.0;
    m_peakDb = -70.0;
    m_running = true;

    m_silenceProcess->setProgram(lsc::LscConfig::instance().ffmpegProgram());
    const auto& cfg = lsc::LscConfig::instance();
    m_silenceProcess->setArguments({
        "-hide_banner",
        "-i",
        videoPath,
        "-af",
        QString("silencedetect=noise=%1dB:d=%2")
            .arg(cfg.silenceThresholdDb)
            .arg(cfg.minSilenceDurationSec),
        "-f",
        "null",
        "NUL",
    });
    m_silenceProcess->start();
}

void AudioAnalyzer::cancel()
{
    for (QProcess* process : {m_silenceProcess, m_volumeProcess}) {
        if (process->state() == QProcess::Running) {
            LSC_DEBUG(MODULE_NAME) << "Terminating process:" << process->program();

            // Try graceful termination first
            process->terminate();
            if (!process->waitForFinished(1000)) {
                LSC_WARNING(MODULE_NAME) << "Process did not terminate gracefully, forcing kill";
                process->kill();
                if (!process->waitForFinished(2000)) {
                    LSC_ERROR(MODULE_NAME) << "Failed to kill process after 2 seconds";
                }
            }
        }
    }
    m_running = false;
}

bool AudioAnalyzer::isRunning() const
{
    return m_running;
}

void AudioAnalyzer::onSilenceDetectFinished(int exitCode, QProcess::ExitStatus status)
{
    if (!m_running) {
        return;
    }

    if (status != QProcess::NormalExit || exitCode != 0) {
        const QString error = QString::fromUtf8(m_silenceProcess->readAllStandardError()).trimmed();
        m_running = false;
        emit errorOccurred(error.isEmpty() ? QStringLiteral("silencedetect failed") : error);
        return;
    }

    parseSilenceOutput(m_silenceProcess->readAllStandardError());
    emit progressChanged(50);
    startVolumeDetect(m_videoPath);
}

void AudioAnalyzer::onVolumeDetectFinished(int exitCode, QProcess::ExitStatus status)
{
    if (!m_running) {
        return;
    }

    if (status != QProcess::NormalExit || exitCode != 0) {
        const QString error = QString::fromUtf8(m_volumeProcess->readAllStandardError()).trimmed();
        m_running = false;
        emit errorOccurred(error.isEmpty() ? QStringLiteral("volumedetect failed") : error);
        return;
    }

    parseVolumeOutput(m_volumeProcess->readAllStandardError());

    QVector<VolumeStats> segmentStats;
    segmentStats.reserve(m_segments.size());

    double minRmsDb = std::numeric_limits<double>::max();
    double maxRmsDb = -std::numeric_limits<double>::max();
    double minPeakDb = std::numeric_limits<double>::max();
    double maxPeakDb = -std::numeric_limits<double>::max();

    // Limit per-segment probing to avoid spawning hundreds of FFmpeg processes.
    // Segments beyond this limit will use overall volume stats as fallback.
    constexpr int kMaxProbeSegments = 20;
    const bool probeAll = m_segments.size() <= kMaxProbeSegments;
    const int step = probeAll ? 1 : qMax(1, m_segments.size() / kMaxProbeSegments);

    for (int i = 0; i < m_segments.size(); ++i) {
        const AudioSegment& segment = m_segments[i];
        VolumeStats stats;

        if (probeAll || (i % step == 0)) {
            // Skip probing very short segments (< 0.3s) — not worth a process spawn
            if (segment.endSec - segment.startSec >= 0.3) {
                stats = probeSegmentVolume(m_videoPath, segment.startSec, segment.endSec);
            }
        }

        if (!stats.valid) {
            stats.meanDb = m_overallLoudness;
            stats.maxDb = m_peakDb;
        }

        segmentStats.append(stats);
        minRmsDb = qMin(minRmsDb, stats.meanDb);
        maxRmsDb = qMax(maxRmsDb, stats.meanDb);
        minPeakDb = qMin(minPeakDb, stats.maxDb);
        maxPeakDb = qMax(maxPeakDb, stats.maxDb);
    }

    const double rmsRange = qMax(1.0, maxRmsDb - minRmsDb);
    const double peakRange = qMax(1.0, maxPeakDb - minPeakDb);

    for (int i = 0; i < m_segments.size(); ++i) {
        AudioSegment& segment = m_segments[i];
        const VolumeStats& stats = segmentStats[i];
        segment.rmsDb = stats.meanDb;
        segment.maxDb = stats.maxDb;

        const double rmsNorm = qBound(0.0, (stats.meanDb - minRmsDb) / rmsRange, 1.0);
        const double peakNorm = qBound(0.0, (stats.maxDb - minPeakDb) / peakRange, 1.0);
        segment.energy = qBound(0.0, rmsNorm * 0.65 + peakNorm * 0.35, 1.0);
    }

    m_running = false;
    emit progressChanged(100);
    emit finished();
}

void AudioAnalyzer::onProcessError(QProcess::ProcessError error)
{
    Q_UNUSED(error)

    QProcess* process = qobject_cast<QProcess*>(sender());
    const QString message = process ? process->errorString() : QStringLiteral("Unknown process error");
    m_running = false;
    emit errorOccurred(message);
}

void AudioAnalyzer::parseSilenceOutput(const QByteArray& output)
{
    m_segments.clear();

    const QString text = QString::fromUtf8(output);
    const QRegularExpression startRegex(QStringLiteral("silence_start:\\s*([0-9.]+)"));
    const QRegularExpression endRegex(QStringLiteral("silence_end:\\s*([0-9.]+)"));
    const double durationSec = probeDurationSeconds(m_videoPath);

    double cursorSec = 0.0;
    for (const QString& line : text.split('\n', Qt::SkipEmptyParts)) {
        const auto startMatch = startRegex.match(line);
        if (startMatch.hasMatch()) {
            const double silenceStartSec = startMatch.captured(1).toDouble();
            if (silenceStartSec > cursorSec) {
                m_segments.append({cursorSec, silenceStartSec, m_peakDb, m_overallLoudness, 0.0});
            }
            continue;
        }

        const auto endMatch = endRegex.match(line);
        if (endMatch.hasMatch()) {
            cursorSec = qMax(cursorSec, endMatch.captured(1).toDouble());
        }
    }

    if (durationSec > cursorSec) {
        m_segments.append({cursorSec, durationSec, m_peakDb, m_overallLoudness, 0.0});
    }

    if (m_segments.isEmpty() && durationSec > 0.0) {
        m_segments.append({0.0, durationSec, m_peakDb, m_overallLoudness, 0.0});
    }
}

void AudioAnalyzer::parseVolumeOutput(const QByteArray& output)
{
    const VolumeStats stats = parseVolumeStats(QString::fromUtf8(output));
    if (stats.valid) {
        m_overallLoudness = stats.meanDb;
        m_peakDb = stats.maxDb;
    }
}

void AudioAnalyzer::startVolumeDetect(const QString& videoPath)
{
    m_volumeProcess->setProgram(lsc::LscConfig::instance().ffmpegProgram());
    m_volumeProcess->setArguments({
        "-hide_banner",
        "-i",
        videoPath,
        "-af",
        "volumedetect",
        "-f",
        "null",
        "NUL",
    });
    m_volumeProcess->start();
}

#undef MODULE_NAME
