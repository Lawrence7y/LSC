#ifndef HIGHLIGHTPREVIEWWIDGET_H
#define HIGHLIGHTPREVIEWWIDGET_H

/**
 * @file HighlightPreviewWidget.h
 * @brief 高光片段预览播放器
 *
 * 使用 Qt Multimedia 的 QMediaPlayer + QVideoWidget 实现轻量级片段预览。
 * 支持播放指定时间范围的视频片段，自动循环播放。
 */

#include <QWidget>
#include <QMediaPlayer>
#include <QVideoWidget>

class QSlider;
class QLabel;
class QPushButton;
class QAudioOutput;

class HighlightPreviewWidget : public QWidget
{
    Q_OBJECT
public:
    explicit HighlightPreviewWidget(QWidget* parent = nullptr);
    ~HighlightPreviewWidget();
    void playVideo(const QString& videoPath);

    /**
     * @brief 播放指定片段
     * @param videoPath 视频文件路径
     * @param startSec 起始时间（秒）
     * @param endSec 结束时间（秒）
     */
    void playSegment(const QString& videoPath, double startSec, double endSec);

    /** @brief 停止播放 */
    void stop();

    /** @brief 是否正在播放 */
    bool isPlaying() const;

    /** @brief 是否正在加载/缓冲中 */
    bool isLoading() const;

    /** @brief 设置当前视频路径（用于关联导出操作） */
    void setVideoPath(const QString& path) { m_videoPath = path; }
    QString videoPath() const { return m_videoPath; }

    /** @brief 获取当前片段的时间范围 */
    double currentStartSec() const { return m_startSec; }
    double currentEndSec() const { return m_endSec; }

signals:
    /** @brief 用户点击导出按钮 */
    void exportRequested(double startSec, double endSec);

private slots:
    void onPlayPauseClicked();
    void onStopClicked();
    void onPositionChanged(qint64 position);
    void onDurationChanged(qint64 duration);
    void onPlaybackStateChanged(QMediaPlayer::PlaybackState state);
    void onSliderPressed();
    void onSliderReleased();

private:
    void setupUi();
    QString formatTime(qint64 ms) const;

    QMediaPlayer* m_player;
    QVideoWidget* m_videoWidget;
    QAudioOutput* m_audioOutput;

    QPushButton* m_playPauseBtn;
    QPushButton* m_stopBtn;
    QPushButton* m_exportBtn;
    QSlider* m_positionSlider;
    QLabel* m_timeLabel;

    QString m_videoPath;
    double m_startSec = 0;
    double m_endSec = 0;
    bool m_hasSegmentBounds = false;
    bool m_sliderDragging = false;
    bool m_looping = true;  // 循环播放当前片段
};

#endif // HIGHLIGHTPREVIEWWIDGET_H
