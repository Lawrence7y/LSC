#ifndef GAMEPLAYDETECTOR_H
#define GAMEPLAYDETECTOR_H

#include <QObject>
#include <QProcess>
#include <QTimer>
#include <QImage>
#include <QElapsedTimer>

#include "GameplayState.h"
#include "GameplayTimeline.h"

/**
 * @brief 游戏状态检测器 - 实时检测直播中的游戏状态
 *
 * 通过定期从 FFmpeg 录制文件中采样帧，分析画面顶部中央区域
 * 来检测无畏契约等游戏的当前状态（游戏中/买局/回合结束等）。
 *
 * 检测原理：
 * - 游戏中：顶部中央有回合计时器（白色数字倒计时）
 * - 买局阶段：顶部中央显示 "BUY PHASE" 或类似文字，画面较暗
 * - 回合结束：画面有明显的记分板/回合结算动画
 */
class GameplayDetector : public QObject
{
    Q_OBJECT
public:
    explicit GameplayDetector(QObject* parent = nullptr);

    void startMonitoring(const QString& videoPath);
    void stopMonitoring();

    GameState currentState() const { return m_currentState; }
    bool isGameplay() const { return m_currentState == GameState::Gameplay; }
    QVector<GameplayTimeSegment> gameplaySegments() const { return m_timeline.gameplaySegments(); }
    QVector<GameplayTimeSegment> stateSegments() const { return m_timeline.segments(); }

    void setSampleIntervalMs(int ms) { m_sampleIntervalMs = ms; }
    void setGameKey(const QString& key) { m_gameKey = key; }

signals:
    void stateChanged(GameState newState);
    void gameplayStarted();
    void gameplayEnded();

private slots:
    void onSampleTimer();

private:
    GameState analyzeFrame(const QImage& frame);
    GameState analyzeValorantFrame(const QImage& frame);
    double analyzeTopCenterBrightness(const QImage& frame);
    double analyzeOverallBrightness(const QImage& frame);
    double analyzeRegionBrightness(const QImage& frame, int x, int y, int w, int h);
    double analyzeRegionContrast(const QImage& frame, int x, int y, int w, int h);

    QTimer* m_sampleTimer;
    QProcess* m_frameExtractor;
    QString m_videoPath;
    QString m_gameKey;
    GameState m_currentState = GameState::Unknown;
    GameState m_lastEmittedState = GameState::Unknown;
    int m_sampleIntervalMs = 2000;
    int m_consecutiveBuyPhase = 0;
    int m_consecutiveGameplay = 0;
    qint64 m_lastSampleTimeMs = 0;
    QElapsedTimer m_elapsed;
    GameplayTimeline m_timeline;
};

#endif // GAMEPLAYDETECTOR_H
