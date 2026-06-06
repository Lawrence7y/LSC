#ifndef PLAYERDOCK_H
#define PLAYERDOCK_H

#include <QDockWidget>

class HighlightPreviewWidget;
class QLabel;
class QPushButton;
class QTimer;

class PlayerDock : public QDockWidget
{
    Q_OBJECT
public:
    explicit PlayerDock(QWidget* parent = nullptr);

    void playLivePreview(const QString& videoPath);
    void playVideo(const QString& videoPath);
    void playSegment(const QString& videoPath, double startSec, double endSec);
    void clearPlayer();

    /** @brief 设置当前活跃的录制文件路径（用于"返回直播"功能） */
    void setRecordingPath(const QString& path);
    /** @brief 清除录制路径（录制停止时调用） */
    void clearRecordingPath();

signals:
    void exportRequested(double startSec, double endSec);
    /** @brief 用户请求返回直播预览 */
    void returnToLiveRequested();

private slots:
    void onReturnToLiveClicked();

private:
    void startPreview(const QString& videoPath, bool liveMode);
    void refreshLivePreview();
    void updateStatus(const QString& text);
    void updateReturnButtonVisibility();

    HighlightPreviewWidget* m_previewWidget = nullptr;
    QLabel* m_statusLabel = nullptr;
    QLabel* m_pathLabel = nullptr;
    QPushButton* m_returnLiveBtn = nullptr;
    QTimer* m_liveRefreshTimer = nullptr;
    QString m_livePreviewPath;
    QString m_recordingPath;  // 当前录制文件路径
    qint64 m_lastObservedSize = -1;
    int m_refreshCount = 0;
};

#endif // PLAYERDOCK_H
