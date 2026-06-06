#ifndef DANCESEGMENTSCORER_H
#define DANCESEGMENTSCORER_H

/**
 * @brief 舞蹈特征结构体
 *
 * 包含用于评估舞蹈片段质量的各个维度特征。
 */
struct DanceFeatures {
    double beatAlignment = 0.0;     // 节拍对齐度
    double motionStrength = 0.0;    // 动作强度
    double poseConfidence = 0.0;    // 姿态置信度
    double subjectCoverage = 0.0;   // 主体覆盖度
};

/**
 * @brief 舞蹈片段评分器
 *
 * 根据多维度特征计算舞蹈片段的综合得分。
 * 用于舞蹈直播的高光片段识别。
 */
class DanceSegmentScorer
{
public:
    /**
     * @brief 计算舞蹈片段得分
     * @param features 舞蹈特征
     * @return 综合得分 (0.0-1.0)
     */
    double score(const DanceFeatures& features) const;

    /**
     * @brief 设置各维度权重
     */
    void setWeights(double beat, double motion, double pose, double coverage);

private:
    double m_beatWeight = 0.35;
    double m_motionWeight = 0.25;
    double m_poseWeight = 0.20;
    double m_coverageWeight = 0.20;
};

#endif // DANCESEGMENTSCORER_H
