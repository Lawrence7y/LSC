#ifndef COMMENTARYSEGMENTER_H
#define COMMENTARYSEGMENTER_H

#include "IHighlightStrategy.h"
#include "SpeechRecognizer.h"

#include <QVector>
#include <QString>
#include <QStringList>

/**
 * @brief 解说片段结构体
 *
 * 表示一段连续的解说内容。
 */
struct CommentarySegment {
    double startSec = 0.0;
    double endSec = 0.0;
    QStringList texts;
    QStringList keywords;
    double score = 0.0;
};

/**
 * @brief 解说内容分段器
 *
 * 根据字幕文本和关键词将连续的解说内容分割成有意义的片段。
 * 支持基于停顿和关键词的分段策略。
 */
class CommentarySegmenter
{
public:
    /**
     * @brief 根据字幕构建解说片段
     * @param subtitles 字幕列表
     * @param keywords 关键词列表
     * @return 解说片段列表
     */
    QVector<CommentarySegment> buildSegments(
        const QVector<SubtitleEntry>& subtitles,
        const QStringList& keywords) const;

    /**
     * @brief 设置停顿阈值（秒）
     */
    void setPauseThreshold(double threshold) { m_pauseThreshold = threshold; }

    /**
     * @brief 设置最小片段时长（秒）
     */
    void setMinSegmentDuration(double duration) { m_minSegmentDuration = duration; }

private:
    bool containsKeyword(const QString& text, const QStringList& keywords) const;
    double scoreSegment(const CommentarySegment& segment, const QStringList& keywords) const;

    double m_pauseThreshold = 2.0;
    double m_minSegmentDuration = 5.0;
};

#endif // COMMENTARYSEGMENTER_H
