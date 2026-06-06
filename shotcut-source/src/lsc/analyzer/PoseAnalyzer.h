#ifndef POSEANALYZER_H
#define POSEANALYZER_H

#include <QObject>
#include <QVector>
#include <QString>

/**
 * @brief 姿态窗口结构体
 *
 * 表示一段时间内的姿态分析结果。
 */
struct PoseWindow {
    double startSec = 0.0;
    double endSec = 0.0;
    double poseConfidence = 0.0;
    double subjectCoverage = 0.0;
    double limbVelocity = 0.0;
};

/**
 * @brief 姿态分析器
 *
 * 分析视频中的人体姿态，提取舞蹈相关的特征。
 * 用于舞蹈直播的高光片段识别。
 *
 * 注意：这是一个基础实现，实际的姿态检测需要
 * 集成 OpenPose 或类似的姿态估计算法。
 */
class PoseAnalyzer : public QObject
{
    Q_OBJECT
public:
    explicit PoseAnalyzer(QObject* parent = nullptr);

    /**
     * @brief 分析视频中的人体姿态
     * @param videoPath 视频文件路径
     */
    void analyze(const QString& videoPath);

    /**
     * @brief 取消分析
     */
    void cancel();

    /**
     * @brief 是否正在分析
     */
    bool isRunning() const { return m_running; }

    /**
     * @brief 获取姿态窗口列表
     */
    const QVector<PoseWindow>& windows() const { return m_windows; }

signals:
    void progressChanged(int percent);
    void finished();
    void errorOccurred(const QString& error);

private:
    /**
     * @brief 基于视频运动分析生成姿态窗口
     *
     * 使用运动强度和场景变化来估算姿态置信度。
     * 这是一个简化的实现，实际应用中应使用
     * 专业的姿态估计算法。
     */
    void generatePoseWindowsFromMotion(const QString& videoPath);

    QVector<PoseWindow> m_windows;
    bool m_running = false;
};

#endif // POSEANALYZER_H
