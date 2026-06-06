#ifndef LIVEPREVIEWWIDGET_H
#define LIVEPREVIEWWIDGET_H

#include <QWidget>
#include <QMediaPlayer>
#include <QVideoWidget>
#include <QVBoxLayout>
#include <QLabel>

/**
 * @brief 直播预览控件 - 显示录制中的实时画面
 *
 * 使用 QMediaPlayer 播放正在录制的视频文件，
 * 支持开始预览和停止预览功能。
 */
class LivePreviewWidget : public QWidget
{
    Q_OBJECT
public:
    explicit LivePreviewWidget(QWidget* parent = nullptr);

    void startPreview(const QString& sourcePath);
    void stopPreview();
    bool isPreviewing() const { return m_isPreviewing; }

private slots:
    void onMediaStatusChanged(QMediaPlayer::MediaStatus status);
    void onErrorOccurred(QMediaPlayer::Error error);

private:
    QMediaPlayer* m_player;
    QVideoWidget* m_videoWidget;
    QLabel* m_placeholderLabel;
    QVBoxLayout* m_layout;
    bool m_isPreviewing = false;
};

#endif // LIVEPREVIEWWIDGET_H
