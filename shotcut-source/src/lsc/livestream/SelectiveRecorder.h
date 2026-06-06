#ifndef SELECTIVERECORDER_H
#define SELECTIVERECORDER_H

#include "GameplayDetector.h"
#include "StreamCapture.h"
#include <QObject>
#include <QVector>

struct RecordedSegment {
    QString filePath;
    double startSec;
    double endSec;
    GameState state;
};

/**
 * @brief 选择性录制器 - 根据游戏状态自动分段录制
 *
 * 核心功能：
 * 1. 持续录制直播流（保证不丢帧）
 * 2. 通过 GameplayDetector 实时检测游戏状态
 * 3. 当游戏状态变化时，自动分割录制文件
 * 4. 只保留 Gameplay 状态的片段，跳过买局/等待时间
 *
 * 工作流程：
 * - 开始录制 → 生成第一个分段文件
 * - 检测到 BuyPhase → 结束当前分段，标记为 Gameplay
 * - BuyPhase 期间 → 不录制（或录制到临时文件）
 * - 检测到 Gameplay → 开始新分段
 * - 停止录制 → 结束最后的分段
 */
class SelectiveRecorder : public QObject
{
    Q_OBJECT
public:
    explicit SelectiveRecorder(QObject* parent = nullptr);

    void startRecording(const QString& streamUrl, const RecordingConfig& config,
                        const QString& gameKey);
    void stopRecording();

    bool isRecording() const;
    GameState currentGameState() const { return m_detector->currentState(); }
    QVector<RecordedSegment> segments() const { return m_segments; }

signals:
    void segmentStarted(const QString& filePath);
    void segmentEnded(const QString& filePath, double durationSec);
    void gameStateChanged(GameState state);
    void errorOccurred(const QString& error);
    void allSegmentsReady(const QVector<RecordedSegment>& segments);

private slots:
    void onGameplayStarted();
    void onGameplayEnded();
    void onCaptureProgress(qint64 durationMs, qint64 fileSizeBytes);
    void onCaptureStatusChanged(RecordingStatus status);
    void onCaptureError(const QString& error);

private:
    void startNewSegment();
    void endCurrentSegment();
    QString generateSegmentPath() const;

    StreamCapture* m_capture;
    GameplayDetector* m_detector;
    RecordingConfig m_baseConfig;
    QString m_streamUrl;
    QString m_baseOutputDir;
    QString m_baseFileName;

    QVector<RecordedSegment> m_segments;
    bool m_currentSegmentActive = false;
    QString m_currentSegmentPath;
    qint64 m_currentSegmentStartMs = 0;
    qint64 m_totalRecordedMs = 0;
    int m_segmentIndex = 0;
};

#endif // SELECTIVERECORDER_H
