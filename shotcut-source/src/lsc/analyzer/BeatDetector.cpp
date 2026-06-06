#include "BeatDetector.h"
#include "LscConfig.h"
#include "LscLog.h"
#include <QDebug>
#include <QFileInfo>
#include <QRegularExpression>

#define MODULE_NAME "BeatDetector"

BeatDetector::BeatDetector(QObject* parent)
    : QObject(parent)
    , m_process(new QProcess(this))
    , m_probeProcess(new QProcess(this))
{
    connect(m_probeProcess,
            QOverload<int, QProcess::ExitStatus>::of(&QProcess::finished),
            this,
            &BeatDetector::onAubioProbeFinished);

    // Start async aubio availability check
    checkAubioAvailability();
}

void BeatDetector::checkAubioAvailability()
{
    if (m_aubioChecked || m_probeRunning) {
        return;
    }

    m_probeRunning = true;
    m_probeProcess->setProgram(lsc::LscConfig::instance().ffmpegProgram());
    m_probeProcess->setArguments({"-hide_banner", "-filters"});
    m_probeProcess->start();
}

void BeatDetector::onAubioProbeFinished(int exitCode, QProcess::ExitStatus status)
{
    m_probeRunning = false;
    m_aubioChecked = true;

    if (exitCode == 0 && status == QProcess::NormalExit) {
        m_hasAubio = QString::fromUtf8(m_probeProcess->readAllStandardOutput()).contains("aubio");
        LSC_DEBUG(MODULE_NAME) << "Aubio availability:" << (m_hasAubio ? "yes" : "no");
    } else {
        m_hasAubio = false;
        LSC_WARNING(MODULE_NAME) << "Failed to check aubio availability, assuming unavailable";
    }
}

void BeatDetector::detect(const QString& audioPath)
{
    QFileInfo fi(audioPath);
    if (!fi.exists()) {
        emit errorOccurred(QString("Audio file not found: %1").arg(audioPath));
        return;
    }
    m_audioPath = audioPath;
    m_beats.clear();
    m_bpm = 120.0;
    m_running = true;

    // If aubio check hasn't completed yet, wait for it or use fallback
    if (m_probeRunning) {
        LSC_DEBUG(MODULE_NAME) << "Aubio probe still running, waiting...";
        // Connect to probe completion to start detection
        connect(m_probeProcess, &QProcess::finished, this, [this]() {
            if (m_running && !m_audioPath.isEmpty()) {
                if (m_hasAubio) {
                    runTempoDetection();
                } else {
                    runRmsFallback();
                }
            }
        }, Qt::SingleShotConnection);
        return;
    }

    if (m_hasAubio) {
        runTempoDetection();
    } else {
        runRmsFallback();
    }
}

void BeatDetector::cancel()
{
    if (m_process->state() == QProcess::Running) {
        m_process->kill();
        m_process->waitForFinished(3000);
    }
    m_running = false;
}

bool BeatDetector::isRunning() const { return m_running; }

void BeatDetector::runTempoDetection()
{
    disconnect(m_process, nullptr, this, nullptr);
    connect(m_process, QOverload<int, QProcess::ExitStatus>::of(&QProcess::finished),
            this, &BeatDetector::onTempoFinished);

    m_process->setProgram(lsc::LscConfig::instance().ffmpegProgram());
    m_process->setArguments({
        "-i", m_audioPath,
        "-af", "aubio=tempo",
        "-vn", "-sn", "-f", "null", "NUL"
    });
    m_process->start();
}

void BeatDetector::runOnsetDetection()
{
    disconnect(m_process, nullptr, this, nullptr);
    connect(m_process, QOverload<int, QProcess::ExitStatus>::of(&QProcess::finished),
            this, &BeatDetector::onOnsetFinished);

    m_process->setProgram(lsc::LscConfig::instance().ffmpegProgram());
    m_process->setArguments({
        "-i", m_audioPath,
        "-af", "aubio=onset=50",
        "-vn", "-sn", "-f", "null", "NUL"
    });
    m_process->start();
}

void BeatDetector::onTempoFinished(int exitCode, QProcess::ExitStatus status)
{
    Q_UNUSED(exitCode); Q_UNUSED(status);
    parseTempoOutput();
    runOnsetDetection();
}

void BeatDetector::onOnsetFinished(int exitCode, QProcess::ExitStatus status)
{
    Q_UNUSED(exitCode); Q_UNUSED(status);
    parseOnsetOutput();
    m_running = false;
    emit finished();
}

void BeatDetector::parseTempoOutput()
{
    QString output = QString::fromUtf8(m_process->readAllStandardError());

    // Extract BPM
    QRegularExpression bpmRe(R"(Tempo:\s*([\d\.]+)\s*BPM)");
    auto bpmMatch = bpmRe.match(output);
    if (bpmMatch.hasMatch()) {
        m_bpm = bpmMatch.captured(1).toDouble();
    }
}

void BeatDetector::parseOnsetOutput()
{
    QString output = QString::fromUtf8(m_process->readAllStandardError());

    // Extract onset timestamps
    QRegularExpression onsetRe(R"(onset\s+([\d\.]+))");
    auto it = onsetRe.globalMatch(output);
    while (it.hasNext()) {
        auto m = it.next();
        BeatInfo beat;
        beat.timestampSec = m.captured(1).toDouble();
        beat.strength = 1.0;
        m_beats.append(beat);
    }
}

void BeatDetector::runRmsFallback()
{
    disconnect(m_process, nullptr, this, nullptr);
    connect(m_process, QOverload<int, QProcess::ExitStatus>::of(&QProcess::finished),
            this, &BeatDetector::onFallbackFinished);

    m_process->setProgram(lsc::LscConfig::instance().ffmpegProgram());
    m_process->setArguments({
        "-i", m_audioPath,
        "-af", "silencedetect=noise=-35dB:d=0.05",
        "-vn", "-sn", "-f", "null", "NUL"
    });
    m_process->start();
}

void BeatDetector::onFallbackFinished(int exitCode, QProcess::ExitStatus status)
{
    Q_UNUSED(exitCode); Q_UNUSED(status);
    parseRmsFallback();
    m_running = false;
    emit finished();
}

void BeatDetector::parseRmsFallback()
{
    QString output = QString::fromUtf8(m_process->readAllStandardError());
    QRegularExpression endRe(R"(silence_end:\s*([\d\.]+))");
    auto endMatches = endRe.globalMatch(output);

    QVector<double> beatTimes;
    while (endMatches.hasNext()) {
        const auto match = endMatches.next();
        beatTimes.append(match.captured(1).toDouble());
    }

    if (beatTimes.isEmpty()) {
        if (!output.contains("silence_start")) {
            beatTimes.append(0.0);
        }
    } else if (!output.contains("silence_start: 0")) {
        beatTimes.prepend(0.0);
    }

    for (double beatTime : std::as_const(beatTimes)) {
        BeatInfo beat;
        beat.timestampSec = beatTime;
        beat.strength = 1.0;
        m_beats.append(beat);
    }

    // Estimate BPM from beat intervals
    if (m_beats.size() > 3) {
        double sumInterval = 0;
        for (int i = 1; i < m_beats.size(); i++) {
            sumInterval += m_beats[i].timestampSec - m_beats[i-1].timestampSec;
        }
        double avgInterval = sumInterval / (m_beats.size() - 1);
        if (avgInterval > 0.1) m_bpm = 60.0 / avgInterval;
    }
}

#undef MODULE_NAME
