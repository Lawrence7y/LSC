#include "PoseAnalyzer.h"
#include "LscConfig.h"

#include <QFileInfo>
#include <QProcess>
#include <QJsonDocument>
#include <QJsonObject>
#include <QRegularExpression>

PoseAnalyzer::PoseAnalyzer(QObject* parent)
    : QObject(parent)
{
}

void PoseAnalyzer::analyze(const QString& videoPath)
{
    if (m_running) {
        return;
    }

    if (!QFileInfo::exists(videoPath)) {
        emit errorOccurred("File not found: " + videoPath);
        return;
    }

    m_windows.clear();
    m_running = true;

    generatePoseWindowsFromMotion(videoPath);
}

void PoseAnalyzer::cancel()
{
    m_running = false;
}

void PoseAnalyzer::generatePoseWindowsFromMotion(const QString& videoPath)
{
    // Use FFmpeg's scene change detection and motion estimation to approximate
    // pose confidence: high motion + scene stability = likely active dance pose.
    // This is a heuristic-based approach since real OpenPose integration is too
    // heavy for real-time analysis.

    QFileInfo fi(videoPath);
    if (!fi.exists()) {
        m_running = false;
        emit errorOccurred("Video file not found");
        return;
    }

    // Step 1: Get video duration
    QProcess probe;
    probe.start(lsc::LscConfig::instance().ffprobeProgram(), {
        "-v", "quiet", "-print_format", "json", "-show_format", videoPath
    });
    if (!probe.waitForFinished(30000)) {
        m_running = false;
        emit errorOccurred("Failed to probe video duration");
        return;
    }

    const QJsonDocument doc = QJsonDocument::fromJson(probe.readAllStandardOutput());
    const double duration = doc.object().value("format").toObject()
                                .value("duration").toString().toDouble();
    if (duration <= 0) {
        m_running = false;
        emit errorOccurred("Invalid video duration");
        return;
    }

    // Step 2: Extract motion intensity per second using tblend+signalstats
    QProcess motionProbe;
    motionProbe.start(lsc::LscConfig::instance().ffmpegProgram(), {
        "-hide_banner", "-i", videoPath,
        "-vf", "tblend=all_mode=difference,signalstats",
        "-an", "-sn", "-f", "null", "NUL"
    });
    if (!motionProbe.waitForFinished(120000)) {
        m_running = false;
        emit errorOccurred("Motion analysis timed out");
        return;
    }

    const QString output = QString::fromUtf8(motionProbe.readAllStandardError());
    const QRegularExpression tsRegex(R"(pts_time:([\d.]+))");
    const QRegularExpression yavgRegex(R"(lavfi\.signalstats\.YAVG=([\d.]+))");

    // Collect per-frame motion values
    struct FrameMotion {
        double timeSec;
        double yavg;
    };
    QVector<FrameMotion> frameMotions;

    const QStringList lines = output.split('\n', Qt::SkipEmptyParts);
    double currentTime = -1.0;
    for (const QString& line : lines) {
        const auto tsMatch = tsRegex.match(line);
        if (tsMatch.hasMatch()) {
            currentTime = tsMatch.captured(1).toDouble();
            continue;
        }
        const auto yavgMatch = yavgRegex.match(line);
        if (yavgMatch.hasMatch() && currentTime >= 0.0) {
            frameMotions.append({currentTime, yavgMatch.captured(1).toDouble()});
        }
    }

    if (frameMotions.isEmpty()) {
        // Fallback: generate uniform windows if no motion data
        const double windowSize = 2.0;
        for (double t = 0; t < duration - windowSize; t += 1.0) {
            m_windows.append({t, t + windowSize, 0.5, 0.5, 0.3});
        }
        m_running = false;
        emit progressChanged(100);
        emit finished();
        return;
    }

    // Step 3: Aggregate into 2-second windows with 1-second overlap
    const double windowSize = 2.0;
    const double step = 1.0;
    constexpr double kMaxMotion = 15.0;  // YAVG normalization baseline

    for (double t = 0; t < duration - windowSize; t += step) {
        const double winEnd = t + windowSize;

        double motionSum = 0.0;
        int motionCount = 0;
        double maxMotion = 0.0;

        for (const FrameMotion& fm : frameMotions) {
            if (fm.timeSec >= t && fm.timeSec < winEnd) {
                motionSum += fm.yavg;
                maxMotion = qMax(maxMotion, fm.yavg);
                ++motionCount;
            }
        }

        if (motionCount == 0) {
            m_windows.append({t, winEnd, 0.4, 0.4, 0.2});
            continue;
        }

        const double avgMotion = motionSum / motionCount;
        const double normalizedMotion = qBound(0.0, avgMotion / kMaxMotion, 1.0);
        const double normalizedMax = qBound(0.0, maxMotion / kMaxMotion, 1.0);

        // Pose confidence: higher when motion is consistent (not spiky)
        // Consistent motion suggests controlled dance movements
        const double consistency = (maxMotion > 0.1)
            ? qBound(0.0, 1.0 - (normalizedMax - normalizedMotion) / normalizedMax, 1.0)
            : 0.3;
        const double poseConfidence = qBound(0.2, 0.4 + consistency * 0.5, 0.95);

        // Subject coverage: higher when there's active motion (person is visible)
        const double subjectCoverage = qBound(0.2, 0.3 + normalizedMotion * 0.6, 0.95);

        // Limb velocity: directly from motion intensity
        const double limbVelocity = qBound(0.0, normalizedMotion, 1.0);

        PoseWindow window;
        window.startSec = t;
        window.endSec = winEnd;
        window.poseConfidence = poseConfidence;
        window.subjectCoverage = subjectCoverage;
        window.limbVelocity = limbVelocity;
        m_windows.append(window);

        if (m_windows.size() % 10 == 0) {
            emit progressChanged(qMin(90, static_cast<int>(t / duration * 100)));
        }
    }

    m_running = false;
    emit progressChanged(100);
    emit finished();
}
