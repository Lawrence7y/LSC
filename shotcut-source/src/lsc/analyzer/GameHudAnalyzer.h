#ifndef GAMEHUDANALYZER_H
#define GAMEHUDANALYZER_H

#include "RoundBoundaryDetector.h"

#include <QObject>
#include <QVector>
#include <QString>

/**
 * @brief 游戏 HUD 分析器
 *
 * 分析游戏直播视频中的 HUD 元素，识别游戏事件。
 * 支持 Valorant 等游戏的回合检测。
 *
 * 注意：这是一个基础实现，实际的 HUD 检测需要
 * 集成计算机视觉算法来识别游戏界面元素。
 */
class GameHudAnalyzer : public QObject
{
    Q_OBJECT
public:
    explicit GameHudAnalyzer(QObject* parent = nullptr);

    /**
     * @brief 分析视频中的游戏 HUD
     * @param videoPath 视频文件路径
     * @param gameKey 游戏标识（如 "valorant"）
     */
    void analyze(const QString& videoPath, const QString& gameKey = "valorant");

    /**
     * @brief 取消分析
     */
    void cancel();

    /**
     * @brief 是否正在分析
     */
    bool isRunning() const { return m_running; }

    /**
     * @brief 获取检测到的 HUD 事件
     */
    const QVector<HudEvent>& events() const { return m_events; }

    /**
     * @brief 获取游戏标识
     */
    const QString& gameKey() const { return m_gameKey; }

signals:
    void progressChanged(int percent);
    void finished();
    void errorOccurred(const QString& error);

private:
    /**
     * @brief 基于视频分析生成 HUD 事件
     *
     * 使用场景变化和音频爆发来估算游戏事件。
     * 这是一个简化的实现，实际应用中应使用
     * 专业的游戏 HUD 检测算法。
     */
    void generateEventsFromVideoAnalysis(const QString& videoPath);

    QVector<HudEvent> m_events;
    QString m_gameKey;
    bool m_running = false;
};

#endif // GAMEHUDANALYZER_H
