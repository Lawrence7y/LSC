# Task 8: 高能时刻检测器 + 音频分析器

## 任务目标

实现高能时刻检测：通过音频能量分析识别直播中的精彩片段（主播欢呼、音效等），支持多种分析维度。

## 创建文件

- `src/lsc/analyzer/HighlightDetector.h`
- `src/lsc/analyzer/HighlightDetector.cpp`
- `src/lsc/analyzer/AudioAnalyzer.h`
- `src/lsc/analyzer/AudioAnalyzer.cpp`

## 前置条件

- Task 2 已完成（模块目录结构）

## HighlightDetector.h

```cpp
#ifndef HIGHLIGHTDETECTOR_H
#define HIGHLIGHTDETECTOR_H

#include <QObject>
#include <QList>
#include <QVariantMap>
#include <QFuture>

struct Highlight {
    qint64 startMs;
    qint64 endMs;
    float confidence;       // 0.0 - 1.0
    QString type;           // "audio_peak", "visual_change", "chat_spike"
    QString description;
    QVariantMap metadata;
};

class HighlightDetector : public QObject
{
    Q_OBJECT
public:
    explicit HighlightDetector(QObject* parent = nullptr);

    void analyze(const QString& videoPath,
                 bool enableAudioAnalysis = true,
                 bool enableVisualAnalysis = true);
    void cancel();
    QList<Highlight> results() const { return m_results; }

signals:
    void progressChanged(int percent, const QString& status);
    void highlightFound(const Highlight& highlight);
    void analysisCompleted(const QList<Highlight>& results);
    void errorOccurred(const QString& error);

private:
    QList<Highlight> mergeAndScore(
        const QList<Highlight>& audio,
        const QList<Highlight>& visual);

    QList<Highlight> m_results;
    bool m_cancelRequested = false;
};

#endif
```

## HighlightDetector.cpp

```cpp
#include "HighlightDetector.h"
#include "AudioAnalyzer.h"
#include <QDebug>
#include <algorithm>

HighlightDetector::HighlightDetector(QObject* parent)
    : QObject(parent) {}

void HighlightDetector::analyze(const QString& videoPath,
                                 bool enableAudio, bool enableVisual)
{
    m_results.clear();
    m_cancelRequested = false;

    QList<Highlight> audioHighlights;
    QList<Highlight> visualHighlights;

    int step = 0;
    int total = (enableAudio ? 1 : 0) + (enableVisual ? 1 : 0);

    if (enableAudio) {
        emit progressChanged(step * 100 / total, "分析音频...");
        AudioAnalyzer aa;
        audioHighlights = aa.detectHighlights(videoPath);
        step++;
        if (m_cancelRequested) return;
    }

    if (enableVisual) {
        emit progressChanged(step * 100 / total, "分析画面...");
        // OpenCV 场景检测（后续实现）
        step++;
        if (m_cancelRequested) return;
    }

    emit progressChanged(80, "合并评分...");
    m_results = mergeAndScore(audioHighlights, visualHighlights);

    for (const auto& h : m_results)
        emit highlightFound(h);

    emit progressChanged(100, "分析完成");
    emit analysisCompleted(m_results);
}

void HighlightDetector::cancel() { m_cancelRequested = true; }

QList<Highlight> HighlightDetector::mergeAndScore(
    const QList<Highlight>& audio,
    const QList<Highlight>& visual)
{
    QList<Highlight> merged;
    for (auto& h : audio) merged.append(h);
    for (auto& h : visual) merged.append(h);

    // 按置信度降序
    std::sort(merged.begin(), merged.end(),
              [](const Highlight& a, const Highlight& b) {
                  return a.confidence > b.confidence;
              });
    return merged;
}
```

## AudioAnalyzer.h

```cpp
#ifndef AUDIOANALYZER_H
#define AUDIOANALYZER_H

#include "HighlightDetector.h"
#include <QVector>

class AudioAnalyzer {
public:
    /**
     * 分析音频，检测高能时刻
     * 方法：计算短时能量(RMS)，标记超过阈值2倍标准差的区域
     */
    QList<Highlight> detectHighlights(const QString& videoPath);

private:
    QList<float> calculateEnergy(
        const QVector<float>& samples, int windowSize = 1024);
    QList<float> extractAudioSamples(const QString& videoPath);
};

#endif
```

## AudioAnalyzer.cpp

```cpp
#include "AudioAnalyzer.h"
#include <QDebug>
#include <QtMath>
#include <cmath>

QList<Highlight> AudioAnalyzer::detectHighlights(const QString& videoPath)
{
    QList<Highlight> highlights;

    // 从视频中提取音频采样
    QVector<float> samples = extractAudioSamples(videoPath);
    if (samples.isEmpty()) return highlights;

    // 计算短时能量
    QList<float> energy = calculateEnergy(samples);

    // 计算统计特征
    float sum = 0, sumSq = 0;
    for (float e : energy) { sum += e; sumSq += e * e; }
    int n = energy.size();
    float mean = sum / n;
    float std = std::sqrt(sumSq / n - mean * mean);
    float threshold = mean + 2.0f * std;  // 2σ阈值

    // 检测超过阈值的区域
    bool inHighlight = false;
    int highlightStart = 0;
    const int sampleRate = 16000;  // 假设16kHz
    const int hopMs = 32;          // 窗口步长

    for (int i = 0; i < energy.size(); i++) {
        if (energy[i] > threshold && !inHighlight) {
            inHighlight = true;
            highlightStart = i;
        } else if (energy[i] <= threshold && inHighlight) {
            inHighlight = false;
            Highlight h;
            h.startMs = highlightStart * hopMs;
            h.endMs = i * hopMs;
            h.confidence = qMin(0.99f, (energy[i] - mean) / (3 * std));
            h.type = "audio_peak";
            h.description = "音频高能时刻";
            highlights.append(h);
        }
    }

    return highlights;
}

QVector<float> AudioAnalyzer::extractAudioSamples(const QString& videoPath)
{
    // 使用FFmpeg提取音频为PCM采样
    // 伪代码 - 实际需要调用FFmpeg API:
    //
    // 1. avformat_open_input → context
    // 2. avformat_find_stream_info
    // 3. 查找音频流
    // 4. 用swresample转为16kHz mono float
    // 5. 读取packet并解码为frame
    // 6. 收集samples到QVector<float>

    QVector<float> samples;
    // 生成测试数据保证编译后不崩溃
    samples.resize(16000 * 10);  // 10秒
    return samples;
}

QList<float> AudioAnalyzer::calculateEnergy(
    const QVector<float>& samples, int windowSize)
{
    QList<float> energy;
    int hop = windowSize / 2;  // 50%重叠
    for (int i = 0; i < samples.size() - windowSize; i += hop) {
        float sum = 0;
        for (int j = 0; j < windowSize; j++)
            sum += samples[i + j] * samples[i + j];
        energy.append(sum / windowSize);
    }
    return energy;
}
```

## 验证

```bash
cd shotcut-source/build
cmake --build . --config Release
```

预期：编译通过。
