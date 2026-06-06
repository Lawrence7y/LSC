#ifndef HIGHLIGHTDETECTOR_H
#define HIGHLIGHTDETECTOR_H

/**
 * @file HighlightDetector.h
 * @brief 高光检测器 — 通过多模态分析自动识别精彩片段
 *
 * 核心算法：
 * 使用滑动窗口遍历视频，对每个窗口计算综合评分：
 *   score = audio * W_audio + video * W_video + speech * W_speech
 *
 * 三个维度的评分：
 * - 音频评分：基于响度，响度越高越可能是精彩时刻
 * - 视频评分：基于场景变化和运动强度
 * - 语音评分：基于关键词匹配（需要 SpeechRecognizer 和关键词列表）
 *
 * 后处理：
 * - 合并间距过近的相邻高光段
 * - 短视频（< 10秒）使用单窗口模式
 *
 * 并行处理：
 * - AudioAnalyzer、VideoAnalyzer、SpeechRecognizer 并行运行
 * - 全部完成后才开始计算高光
 */

#include "IHighlightStrategy.h"
#include "AudioAnalyzer.h"
#include "VideoAnalyzer.h"
#include "SpeechRecognizer.h"
#include <QObject>
#include <QVector>

// HighlightSegment and HighlightResult are now defined in IHighlightStrategy.h

class HighlightDetector : public QObject
{
    Q_OBJECT
public:
    explicit HighlightDetector(QObject* parent = nullptr);

    /**
     * @brief 设置语音识别器（可选）
     *
     * 如果不设置，语音评分维度将被禁用，使用双维度（音频+视频）评分。
     * SpeechRecognizer 的生命周期由调用者管理。
     */
    void setSpeechRecognizer(SpeechRecognizer* sr);

    /** @brief 设置关键词列表，用于语音评分 */
    void setKeywordList(const QStringList& keywords);

    /**
     * @brief 开始高光分析
     *
     * 并行启动三个分析器，全部完成后自动计算高光。
     * 通过 highlightFound 信号逐个报告发现的高光。
     * 通过 finished 信号报告分析完成。
     */
    void analyze(const QString& videoPath);

    /** @brief 取消分析 */
    void cancel();

    const QVector<HighlightSegment>& highlights() const { return m_highlights; }
    bool isRunning() const;

signals:
    void progressChanged(const QString& stage, int percent);
    void highlightFound(const HighlightSegment& segment);
    void finished();
    void errorOccurred(const QString& error);

private slots:
    void onAudioFinished();
    void onVideoFinished();
    void onSpeechFinished();
    void onSpeechError(const QString& error);

private:
    void computeHighlights();
    double computeAudioScore(const AudioSegment& seg);
    double computeVideoScore(double timestamp);
    double computeSpeechScore(double startSec, double endSec);
    void mergeAdjacentHighlights();

    AudioAnalyzer* m_audioAnalyzer;
    VideoAnalyzer* m_videoAnalyzer;
    SpeechRecognizer* m_speechRecognizer;  ///< 外部管理，不拥有
    QStringList m_keywords;

    QVector<HighlightSegment> m_highlights;
    QVector<AudioSegment> m_audioSegments;
    QVector<SceneChange> m_sceneChanges;
    QVector<MotionSegment> m_motionSegments;
    QVector<SubtitleEntry> m_subtitles;

    double m_duration = 0.0;
    int m_completedCount = 0;
    int m_expectedCount = 3;
};

#endif // HIGHLIGHTDETECTOR_H
