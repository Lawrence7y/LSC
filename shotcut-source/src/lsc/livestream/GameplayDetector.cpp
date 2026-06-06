#include "GameplayDetector.h"
#include "LscConfig.h"
#include "LscLog.h"

#include <QFileInfo>
#include <QDir>
#include <QTemporaryFile>
#include <QColor>
#include <QtMath>

#define MODULE_NAME "GameplayDetector"

GameplayDetector::GameplayDetector(QObject* parent)
    : QObject(parent)
    , m_sampleTimer(new QTimer(this))
    , m_frameExtractor(new QProcess(this))
{
    m_sampleTimer->setInterval(m_sampleIntervalMs);
    connect(m_sampleTimer, &QTimer::timeout, this, &GameplayDetector::onSampleTimer);

    connect(m_frameExtractor,
            QOverload<int, QProcess::ExitStatus>::of(&QProcess::finished),
            this, [this](int exitCode, QProcess::ExitStatus status) {
                if (exitCode != 0 || status != QProcess::NormalExit) {
                    return;
                }
                const QByteArray pngData = m_frameExtractor->readAllStandardOutput();
                if (pngData.isEmpty()) {
                    return;
                }
                QImage frame;
                if (!frame.loadFromData(pngData, "PNG")) {
                    return;
                }
                const GameState newState = analyzeFrame(frame);
                if (newState != m_currentState) {
                    m_currentState = newState;
                    m_timeline.recordState(newState, m_elapsed.elapsed());
                    emit stateChanged(newState);
                    if (newState == GameState::Gameplay) {
                        emit gameplayStarted();
                    } else if (m_lastEmittedState == GameState::Gameplay) {
                        emit gameplayEnded();
                    }
                    m_lastEmittedState = newState;
                }
            });
}

void GameplayDetector::startMonitoring(const QString& videoPath)
{
    if (m_sampleTimer->isActive()) {
        stopMonitoring();
    }
    m_videoPath = videoPath;
    m_currentState = GameState::Unknown;
    m_lastEmittedState = GameState::Unknown;
    m_consecutiveBuyPhase = 0;
    m_consecutiveGameplay = 0;
    m_lastSampleTimeMs = 0;
    m_elapsed.restart();
    m_timeline.start(0);
    m_sampleTimer->setInterval(m_sampleIntervalMs);
    m_sampleTimer->start();
    LSC_INFO(MODULE_NAME) << "开始监控游戏状态: " << videoPath;
}

void GameplayDetector::stopMonitoring()
{
    m_sampleTimer->stop();
    if (m_frameExtractor->state() == QProcess::Running) {
        m_frameExtractor->kill();
        m_frameExtractor->waitForFinished(2000);
    }
    if (m_elapsed.isValid()) {
        m_timeline.finish(m_elapsed.elapsed());
    }
    m_currentState = GameState::Unknown;
    m_lastEmittedState = GameState::Unknown;
    LSC_INFO(MODULE_NAME) << "停止监控游戏状态";
}

void GameplayDetector::onSampleTimer()
{
    if (m_videoPath.isEmpty() || !QFileInfo::exists(m_videoPath)) {
        return;
    }
    if (m_frameExtractor->state() == QProcess::Running) {
        return; // Previous extraction still running, skip this cycle
    }

    // Extract a single frame from the end of the current recording
    // Using -sseof -1 to get the last frame (most recent)
    const auto& cfg = lsc::LscConfig::instance();
    m_frameExtractor->setProgram(cfg.ffmpegProgram());
    m_frameExtractor->setArguments({
        "-hide_banner", "-loglevel", "error",
        "-sseof", "-1",
        "-i", m_videoPath,
        "-frames:v", "1",
        "-f", "image2pipe",
        "-c:v", "png",
        "pipe:1"
    });
    m_frameExtractor->start();
}

GameState GameplayDetector::analyzeFrame(const QImage& frame)
{
    if (frame.isNull()) {
        return m_currentState;
    }

    if (m_gameKey.contains("valorant", Qt::CaseInsensitive)) {
        return analyzeValorantFrame(frame);
    }

    // Generic game state detection based on overall brightness and motion
    const double brightness = analyzeOverallBrightness(frame);
    if (brightness < 0.15) {
        return GameState::Loading;
    }
    return GameState::Gameplay;
}

GameState GameplayDetector::analyzeValorantFrame(const QImage& frame)
{
    // In Valorant:
    // - During gameplay: top center has a white/light timer countdown
    //   Timer area is roughly top 8%, center 30% of the screen
    // - During buy phase: top center shows "BUY PHASE" or round info
    //   The area is visually different (often shows round number)
    // - During round end: scoreboard overlay appears

    const int w = frame.width();
    const int h = frame.height();

    // Timer region: top 5-10%, center 25-35% of screen
    const int timerX = w * 35 / 100;
    const int timerY = h * 1 / 100;
    const int timerW = w * 30 / 100;
    const int timerH = h * 8 / 100;

    const double timerBrightness = analyzeRegionBrightness(frame, timerX, timerY, timerW, timerH);
    const double timerContrast = analyzeRegionContrast(frame, timerX, timerY, timerW, timerH);
    const double overallBrightness = analyzeOverallBrightness(frame);

    // Buy phase detection:
    // - Timer area has lower contrast (no sharp digits)
    // - Overall screen is often darker
    // - Less visual activity in the timer region

    // Gameplay detection:
    // - Timer area has high contrast (white digits on dark background)
    // - Timer brightness is moderate to high
    // - There's a clear countdown pattern

    const bool hasTimer = timerContrast > 0.3 && timerBrightness > 0.25;
    const bool isDarkScreen = overallBrightness < 0.2;

    if (hasTimer) {
        m_consecutiveGameplay++;
        m_consecutiveBuyPhase = 0;
        if (m_consecutiveGameplay >= 2) {
            return GameState::Gameplay;
        }
        return m_currentState; // Wait for confirmation
    }

    if (isDarkScreen && timerContrast < 0.2) {
        m_consecutiveBuyPhase++;
        m_consecutiveGameplay = 0;
        if (m_consecutiveBuyPhase >= 2) {
            return GameState::BuyPhase;
        }
        return m_currentState; // Wait for confirmation
    }

    // If overall brightness is very low, might be loading/transition
    if (overallBrightness < 0.1) {
        return GameState::Loading;
    }

    return m_currentState;
}

double GameplayDetector::analyzeRegionBrightness(const QImage& frame, int x, int y, int w, int h)
{
    const int x2 = qMin(x + w, frame.width());
    const int y2 = qMin(y + h, frame.height());
    if (x >= x2 || y >= y2) return 0.0;

    double totalBrightness = 0.0;
    int pixelCount = 0;

    // Sample every 4th pixel for performance
    for (int py = y; py < y2; py += 4) {
        for (int px = x; px < x2; px += 4) {
            const QColor color = frame.pixelColor(px, py);
            totalBrightness += (color.redF() + color.greenF() + color.blueF()) / 3.0;
            ++pixelCount;
        }
    }

    return pixelCount > 0 ? totalBrightness / pixelCount : 0.0;
}

double GameplayDetector::analyzeRegionContrast(const QImage& frame, int x, int y, int w, int h)
{
    const int x2 = qMin(x + w, frame.width());
    const int y2 = qMin(y + h, frame.height());
    if (x >= x2 || y >= y2) return 0.0;

    double sum = 0.0;
    double sumSq = 0.0;
    int count = 0;

    for (int py = y; py < y2; py += 4) {
        for (int px = x; px < x2; px += 4) {
            const QColor color = frame.pixelColor(px, py);
            const double brightness = (color.redF() + color.greenF() + color.blueF()) / 3.0;
            sum += brightness;
            sumSq += brightness * brightness;
            ++count;
        }
    }

    if (count < 2) return 0.0;
    const double mean = sum / count;
    const double variance = sumSq / count - mean * mean;
    return qSqrt(qMax(0.0, variance));
}

double GameplayDetector::analyzeOverallBrightness(const QImage& frame)
{
    // Sample a grid of points across the entire frame
    const int w = frame.width();
    const int h = frame.height();
    double total = 0.0;
    int count = 0;

    for (int y = 0; y < h; y += 16) {
        for (int x = 0; x < w; x += 16) {
            const QColor color = frame.pixelColor(x, y);
            total += (color.redF() + color.greenF() + color.blueF()) / 3.0;
            ++count;
        }
    }

    return count > 0 ? total / count : 0.0;
}

#undef MODULE_NAME
