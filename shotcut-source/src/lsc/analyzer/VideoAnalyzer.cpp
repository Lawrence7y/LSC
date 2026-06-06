#include "VideoAnalyzer.h"
#include "LscConfig.h"

#include <QFileInfo>
#include <QRegularExpression>

namespace {
double extractTimestamp(const QString& line)
{
    const int idx = line.indexOf("pts_time:");
    if (idx < 0) {
        return -1.0;
    }
    return line.mid(idx + 9).split(QRegularExpression("[\\s,]")).first().toDouble();
}

double extractSignalValue(const QString& line, const QString& key)
{
    const int idx = line.indexOf(key);
    if (idx < 0) {
        return -1.0;
    }
    return line.mid(idx + key.size()).split(QRegularExpression("[\\s,]")).first().toDouble();
}
}

VideoAnalyzer::VideoAnalyzer(QObject* parent)
    : QObject(parent)
    , m_sceneProcess(new QProcess(this))
    , m_motionProcess(new QProcess(this))
{
    connect(m_sceneProcess,
            QOverload<int, QProcess::ExitStatus>::of(&QProcess::finished),
            this,
            [this](int exitCode, QProcess::ExitStatus exitStatus) {
                if (exitStatus == QProcess::NormalExit && exitCode == 0) {
                    QByteArray output = m_sceneProcess->readAllStandardError();
                    output += m_sceneProcess->readAllStandardOutput();
                    parseSceneChanges(output);
                }
                ++m_finishedCount;
                emit progressChanged(50);
                if (m_finishedCount >= m_runningCount) {
                    m_runningCount = 0;
                    emit finished();
                }
            });

    connect(m_motionProcess,
            QOverload<int, QProcess::ExitStatus>::of(&QProcess::finished),
            this,
            [this](int exitCode, QProcess::ExitStatus exitStatus) {
                if (exitStatus == QProcess::NormalExit && exitCode == 0) {
                    QByteArray output = m_motionProcess->readAllStandardError();
                    output += m_motionProcess->readAllStandardOutput();
                    parseMotionData(output);
                }
                ++m_finishedCount;
                emit progressChanged(100);
                if (m_finishedCount >= m_runningCount) {
                    m_runningCount = 0;
                    emit finished();
                }
            });

    auto onProcessError = [this](QProcess::ProcessError) {
        emit errorOccurred("ffmpeg video analysis process failed");
    };
    connect(m_sceneProcess, &QProcess::errorOccurred, this, onProcessError);
    connect(m_motionProcess, &QProcess::errorOccurred, this, onProcessError);
}

void VideoAnalyzer::analyze(const QString& videoPath)
{
    const QFileInfo fi(videoPath);
    if (!fi.exists()) {
        emit errorOccurred(QString("File not found: %1").arg(videoPath));
        return;
    }

    m_sceneChanges.clear();
    m_motionSegments.clear();
    m_averageMotion = 0.0;
    m_runningCount = 2;
    m_finishedCount = 0;

    QStringList sceneArgs;
    const auto& cfg = lsc::LscConfig::instance();
    sceneArgs << "-hide_banner"
              << "-i" << videoPath
              << "-filter:v"
              << QString("select='gt(scene,%1)',metadata=print").arg(cfg.sceneChangeThreshold)
              << "-an"
              << "-sn"
              << "-f"
              << "null"
              << "NUL";
    m_sceneProcess->setProgram(lsc::LscConfig::instance().ffmpegProgram());
    m_sceneProcess->setArguments(sceneArgs);
    m_sceneProcess->start();

    QStringList motionArgs;
    motionArgs << "-hide_banner"
               << "-i" << videoPath
               << "-filter:v"
               << "tblend=all_mode=difference,signalstats,metadata=print"
               << "-an"
               << "-sn"
               << "-f"
               << "null"
               << "NUL";
    m_motionProcess->setProgram(lsc::LscConfig::instance().ffmpegProgram());
    m_motionProcess->setArguments(motionArgs);
    m_motionProcess->start();
}

void VideoAnalyzer::cancel()
{
    for (QProcess* process : {m_sceneProcess, m_motionProcess}) {
        if (process->state() == QProcess::Running) {
            process->kill();
            process->waitForFinished(3000);
        }
    }
    m_runningCount = 0;
}

bool VideoAnalyzer::isRunning() const
{
    return m_runningCount > 0;
}

void VideoAnalyzer::parseSceneChanges(const QByteArray& output)
{
    m_sceneChanges.clear();
    const QString text = QString::fromUtf8(output);
    const QStringList lines = text.split('\n', Qt::SkipEmptyParts);

    SceneChange current{};
    bool hasTimestamp = false;

    for (const QString& line : lines) {
        if (line.contains("pts_time:")) {
            current.timestampSec = extractTimestamp(line);
            hasTimestamp = current.timestampSec >= 0.0;
        }
        if (line.contains("lavfi.scene_score=")) {
            const int idx = line.indexOf("lavfi.scene_score=");
            current.score = line.mid(idx + 18).split(QRegularExpression("[\\s,]")).first().toDouble();
        }
        if (hasTimestamp && current.score > 0.0) {
            m_sceneChanges.append(current);
            current = SceneChange{};
            hasTimestamp = false;
        }
    }
}

void VideoAnalyzer::parseMotionData(const QByteArray& output)
{
    m_motionSegments.clear();
    m_averageMotion = 0.0;

    const QString text = QString::fromUtf8(output);
    const QStringList lines = text.split('\n', Qt::SkipEmptyParts);

    struct MotionFrame {
        double timestampSec = -1.0;
        double rawMotion = 0.0;
    };

    QVector<MotionFrame> frames;
    frames.reserve(lines.size() / 4);

    MotionFrame currentFrame;
    for (const QString& line : lines) {
        if (line.contains("pts_time:")) {
            currentFrame.timestampSec = extractTimestamp(line);
            continue;
        }

        if (line.contains("lavfi.signalstats.YAVG=")) {
            currentFrame.rawMotion = extractSignalValue(line, "lavfi.signalstats.YAVG=");
            if (currentFrame.timestampSec >= 0.0) {
                frames.append(currentFrame);
            }
            currentFrame = MotionFrame{};
        }
    }

    if (frames.isEmpty()) {
        return;
    }

    constexpr double kMotionThreshold = 2.0;
    constexpr double kMaxMotionForNormalization = 12.0;
    constexpr double kFrameGapToleranceSec = 0.12;

    MotionSegment current{};
    double motionSum = 0.0;
    int activeFrames = 0;

    auto flushCurrent = [&]() {
        if (activeFrames == 0) {
            return;
        }
        current.motionLevel =
            qBound(0.0, (motionSum / activeFrames) / kMaxMotionForNormalization, 1.0);
        m_motionSegments.append(current);
        current = MotionSegment{};
        motionSum = 0.0;
        activeFrames = 0;
    };

    for (const MotionFrame& frame : std::as_const(frames)) {
        if (frame.rawMotion < kMotionThreshold) {
            flushCurrent();
            continue;
        }

        if (activeFrames == 0) {
            current.startSec = frame.timestampSec;
            current.endSec = frame.timestampSec;
            motionSum = frame.rawMotion;
            activeFrames = 1;
            continue;
        }

        if (frame.timestampSec - current.endSec > kFrameGapToleranceSec) {
            flushCurrent();
            current.startSec = frame.timestampSec;
            current.endSec = frame.timestampSec;
            motionSum = frame.rawMotion;
            activeFrames = 1;
            continue;
        }

        current.endSec = frame.timestampSec;
        motionSum += frame.rawMotion;
        ++activeFrames;
    }

    flushCurrent();

    if (m_motionSegments.isEmpty()) {
        return;
    }

    double total = 0.0;
    for (const MotionSegment& segment : m_motionSegments) {
        total += segment.motionLevel;
    }
    m_averageMotion = total / m_motionSegments.size();
}
