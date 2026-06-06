#ifndef COMPOSITEHIGHLIGHTSTRATEGY_H
#define COMPOSITEHIGHLIGHTSTRATEGY_H

#include "IHighlightStrategy.h"
#include <QVector>

/**
 * @brief 组合高光策略 - 同时运行多个策略并合并结果
 *
 * 用于需要多种检测器协同工作的场景，例如 Valorant 游戏直播
 * 需要同时运行游戏高光检测和解说语义检测。
 */
class CompositeHighlightStrategy : public IHighlightStrategy
{
    Q_OBJECT
public:
    explicit CompositeHighlightStrategy(QObject* parent = nullptr);

    void addStrategy(IHighlightStrategy* strategy);
    int strategyCount() const { return m_strategies.size(); }
    void setDisplayName(const QString& displayName) { m_displayName = displayName; }

    QString name() const override;
    QString description() const override;
    void analyze(const QString& videoPath) override;
    void cancel() override;
    bool isRunning() const override;
    HighlightResult result() const override;
    void configure(const QJsonObject& params) override;

private slots:
    void onChildFinished();
    void onChildSegment(const HighlightSegment& segment);
    void onChildError(const QString& message);

private:
    void checkAllFinished();

    QVector<IHighlightStrategy*> m_strategies;
    QVector<HighlightSegment> m_segments;
    int m_finishedCount = 0;
    bool m_running = false;
    QString m_displayName;
};

#endif // COMPOSITEHIGHLIGHTSTRATEGY_H
