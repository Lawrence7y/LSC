#ifndef HIGHLIGHTUTILS_H
#define HIGHLIGHTUTILS_H

/**
 * @file HighlightUtils.h
 * @brief 公共工具函数 - 高光片段相关的通用算法
 *
 * 提取自多个模块的重复代码，提供统一的实现。
 */

#include "IHighlightStrategy.h"
#include <QStringList>
#include <QtGlobal>

namespace HighlightUtils {

/**
 * @brief 计算两个片段的重叠比率
 * @param a 第一个片段
 * @param b 第二个片段
 * @return 重叠部分占较短片段的比例 (0.0-1.0)
 *
 * 用于判断两个高光片段是否表示同一时刻。
 * 返回值 >= 0.5 通常认为是重复片段。
 */
inline double overlapRatio(const HighlightSegment& a, const HighlightSegment& b)
{
    const double overlapStart = qMax(a.startSec, b.startSec);
    const double overlapEnd = qMin(a.endSec, b.endSec);
    const double overlap = overlapEnd - overlapStart;
    if (overlap <= 0.0) {
        return 0.0;
    }

    const double minLength = qMax(0.1, qMin(a.endSec - a.startSec, b.endSec - b.startSec));
    return overlap / minLength;
}

/**
 * @brief 合并两个关键词列表（去重）
 * @param left 第一个列表
 * @param right 第二个列表
 * @return 合并后的去重列表
 */
inline QStringList mergeKeywords(const QStringList& left, const QStringList& right)
{
    QStringList result = left;
    for (const QString& keyword : right) {
        if (!keyword.isEmpty() && !result.contains(keyword)) {
            result.append(keyword);
        }
    }
    return result;
}

/**
 * @brief 合并两个片段，保留最佳属性
 * @param target 目标片段（会被修改）
 * @param incoming 新片段（只读）
 *
 * 合并规则：
 * - 时间范围取并集
 * - 分数取最大值
 * - 关键词合并去重
 * - 如果新片段分数更高，使用其 reason
 */
inline void mergeSegmentInto(HighlightSegment& target, const HighlightSegment& incoming)
{
    const bool incomingPreferred = incoming.score >= target.score;
    target.startSec = qMin(target.startSec, incoming.startSec);
    target.endSec = qMax(target.endSec, incoming.endSec);
    target.score = qMax(target.score, incoming.score);
    target.audioScore = qMax(target.audioScore, incoming.audioScore);
    target.videoScore = qMax(target.videoScore, incoming.videoScore);
    target.speechScore = qMax(target.speechScore, incoming.speechScore);
    target.keywords = mergeKeywords(target.keywords, incoming.keywords);
    if (incomingPreferred) {
        target.reason = incoming.reason;
    }
}

/**
 * @brief 检查两个片段是否应该合并
 * @param a 第一个片段
 * @param b 第二个片段
 * @param overlapThreshold 重叠阈值（默认 0.35）
 * @param adjacentGapSec 相邻片段最大间隔（默认 0.8 秒）
 * @return true 如果片段应该合并
 *
 * 注意：建议使用 LscConfig::instance() 中的配置值作为参数
 */
inline bool shouldMergeSegments(const HighlightSegment& a, const HighlightSegment& b,
                                double overlapThreshold = 0.35,
                                double adjacentGapSec = 0.8)
{
    const double overlap = overlapRatio(a, b);
    if (overlap >= overlapThreshold) {
        return true;
    }

    const double gap = b.startSec - a.endSec;
    return gap > 0.0 && gap <= adjacentGapSec;
}

/**
 * @brief 对片段列表进行归一化（排序 + 合并重叠）
 * @param segments 输入片段列表
 * @return 归一化后的片段列表
 */
QVector<HighlightSegment> normalizeSegments(const QVector<HighlightSegment>& segments);

/**
 * @brief 从列表中去除重复片段
 * @param segments 输入片段列表
 * @param overlapThreshold 重叠阈值
 * @return 去重后的片段列表
 */
QVector<HighlightSegment> deduplicateSegments(const QVector<HighlightSegment>& segments,
                                              double overlapThreshold = 0.5);

} // namespace HighlightUtils

#endif // HIGHLIGHTUTILS_H
