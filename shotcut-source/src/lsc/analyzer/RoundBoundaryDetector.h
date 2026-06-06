#ifndef ROUNDBOUNDARYDETECTOR_H
#define ROUNDBOUNDARYDETECTOR_H

#include <QVector>
#include <QString>

/**
 * @brief HUD 事件结构体
 *
 * 表示游戏 HUD 中检测到的事件，如买局阶段、回合结束等。
 */
struct HudEvent {
    double timestampSec = 0.0;
    QString type;
    QString text;
    double confidence = 0.0;
};

/**
 * @brief 回合片段结构体
 *
 * 表示一个完整的游戏回合，包含开始时间、结束时间和标题。
 */
struct RoundSegment {
    double startSec = 0.0;
    double endSec = 0.0;
    QString title;
};

/**
 * @brief 回合边界检测器
 *
 * 根据 HUD 事件检测游戏回合的边界。
 * 支持 Valorant 等游戏的回合分段。
 */
class RoundBoundaryDetector
{
public:
    /**
     * @brief 根据 HUD 事件构建回合列表
     * @param events HUD 事件列表
     * @return 回合片段列表
     */
    QVector<RoundSegment> buildRounds(const QVector<HudEvent>& events) const;

    /**
     * @brief 设置买局阶段事件类型
     */
    void setBuyPhaseType(const QString& type) { m_buyPhaseType = type; }

    /**
     * @brief 设置回合结束事件类型
     */
    void setRoundEndType(const QString& type) { m_roundEndType = type; }

private:
    QString m_buyPhaseType = "buy_phase";
    QString m_roundEndType = "round_end";
};

#endif // ROUNDBOUNDARYDETECTOR_H
