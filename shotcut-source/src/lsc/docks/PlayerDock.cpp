#include "PlayerDock.h"

#include "HighlightPreviewWidget.h"
#include "LscConfig.h"

#include <QFileInfo>
#include <QLabel>
#include <QPushButton>
#include <QTimer>
#include <QUrl>
#include <QVBoxLayout>
#include <QWidget>

namespace {
bool isLocalPreviewFile(const QString& source)
{
    return QFileInfo::exists(source);
}

QString displayPath(const QString& path)
{
    const QFileInfo fi(path);
    if (fi.exists()) {
        return fi.fileName();
    }
    if (path.length() > 60) {
        return path.left(30) + "..." + path.right(25);
    }
    return path;
}
}

PlayerDock::PlayerDock(QWidget* parent)
    : QDockWidget(QString::fromUtf8("播放器模块"), parent)
{
    QWidget* container = new QWidget(this);
    QVBoxLayout* layout = new QVBoxLayout(container);
    layout->setContentsMargins(4, 4, 4, 4);
    layout->setSpacing(2);

    // 顶部状态栏 + 返回按钮
    QHBoxLayout* topLayout = new QHBoxLayout();
    topLayout->setContentsMargins(0, 0, 0, 0);
    topLayout->setSpacing(4);

    m_statusLabel = new QLabel(QString::fromUtf8("等待直播预览或视频片段"), container);
    m_statusLabel->setWordWrap(true);
    m_statusLabel->setStyleSheet("font-size: 11px; padding: 2px;");
    topLayout->addWidget(m_statusLabel, 1);

    m_returnLiveBtn = new QPushButton(QString::fromUtf8("返回直播"), container);
    m_returnLiveBtn->setToolTip(QString::fromUtf8("返回直播实时预览"));
    m_returnLiveBtn->setStyleSheet(
        "QPushButton { background-color: #3498db; color: white; font-weight: bold; "
        "padding: 4px 12px; border-radius: 3px; font-size: 11px; }"
        "QPushButton:hover { background-color: #2980b9; }"
        "QPushButton:disabled { background-color: #95a5a6; }");
    m_returnLiveBtn->setVisible(false);
    connect(m_returnLiveBtn, &QPushButton::clicked,
            this, &PlayerDock::onReturnToLiveClicked);
    topLayout->addWidget(m_returnLiveBtn);

    layout->addLayout(topLayout);

    m_pathLabel = new QLabel(QString::fromUtf8("未加载"), container);
    m_pathLabel->setWordWrap(true);
    m_pathLabel->setStyleSheet("color: #6b7280; font-size: 10px; padding: 2px;");
    layout->addWidget(m_pathLabel);

    m_previewWidget = new HighlightPreviewWidget(container);
    m_previewWidget->setMinimumHeight(200);
    layout->addWidget(m_previewWidget, 1);

    setWidget(container);

    m_liveRefreshTimer = new QTimer(this);
    m_liveRefreshTimer->setInterval(lsc::LscConfig::instance().progressIntervalMs * 2);
    connect(m_liveRefreshTimer, &QTimer::timeout, this, &PlayerDock::refreshLivePreview);

    connect(m_previewWidget, &HighlightPreviewWidget::exportRequested,
            this, &PlayerDock::exportRequested);
}

void PlayerDock::playLivePreview(const QString& videoPath)
{
    m_livePreviewPath = videoPath;
    m_lastObservedSize = -1;
    m_refreshCount = 0;
    startPreview(videoPath, true);
}

void PlayerDock::playVideo(const QString& videoPath)
{
    m_livePreviewPath.clear();
    m_liveRefreshTimer->stop();
    startPreview(videoPath, false);
}

void PlayerDock::playSegment(const QString& videoPath, double startSec, double endSec)
{
    if (videoPath.isEmpty()) {
        clearPlayer();
        return;
    }

    m_livePreviewPath.clear();
    m_liveRefreshTimer->stop();
    m_pathLabel->setText(displayPath(videoPath));
    updateStatus(QString::fromUtf8("片段预览: %1 [%2s - %3s]")
                     .arg(QFileInfo(videoPath).fileName())
                     .arg(startSec, 0, 'f', 1)
                     .arg(endSec, 0, 'f', 1));
    m_previewWidget->playSegment(videoPath, startSec, endSec);
    updateReturnButtonVisibility();
}

void PlayerDock::clearPlayer()
{
    m_livePreviewPath.clear();
    m_liveRefreshTimer->stop();
    m_lastObservedSize = -1;
    m_refreshCount = 0;
    updateStatus(QString::fromUtf8("等待直播预览或视频片段"));
    if (m_pathLabel) {
        m_pathLabel->setText(QString::fromUtf8("未加载"));
    }
    m_previewWidget->stop();
    updateReturnButtonVisibility();
}

void PlayerDock::startPreview(const QString& videoPath, bool liveMode)
{
    if (videoPath.isEmpty()) {
        clearPlayer();
        return;
    }

    const QFileInfo fileInfo(videoPath);
    if (m_pathLabel) {
        m_pathLabel->setText(displayPath(videoPath));
    }

    if (liveMode) {
        updateStatus(QString::fromUtf8("直播预览准备中: %1").arg(fileInfo.fileName()));
        if (isLocalPreviewFile(videoPath)) {
            m_liveRefreshTimer->start();
        } else {
            m_liveRefreshTimer->stop();
            m_lastObservedSize = -1;
            m_previewWidget->playVideo(videoPath);
            updateStatus(QString::fromUtf8("直播预览中: %1").arg(displayPath(videoPath)));
            return;
        }
    } else {
        updateStatus(QString::fromUtf8("正在播放: %1").arg(fileInfo.fileName()));
    }

    const qint64 minSize = liveMode ? 16384 : 0;
    if (!fileInfo.exists() || fileInfo.size() < minSize) {
        return;
    }

    m_lastObservedSize = fileInfo.size();
    m_previewWidget->playVideo(videoPath);
    if (liveMode) {
        updateStatus(QString::fromUtf8("直播预览中: %1").arg(displayPath(videoPath)));
    }
}

void PlayerDock::refreshLivePreview()
{
    if (m_livePreviewPath.isEmpty()) {
        m_liveRefreshTimer->stop();
        return;
    }

    const QFileInfo fileInfo(m_livePreviewPath);
    if (!fileInfo.exists() || fileInfo.size() <= 0) {
        updateStatus(QString::fromUtf8("等待录制文件写入预览画面"));
        return;
    }

    const qint64 currentSize = fileInfo.size();
    m_lastObservedSize = currentSize;

    const bool playerStopped = !m_previewWidget->isPlaying()
        && !m_previewWidget->isLoading();
    const bool shouldReload = playerStopped && currentSize >= 16384;

    if (shouldReload) {
        ++m_refreshCount;
        m_previewWidget->playVideo(m_livePreviewPath);
        updateStatus(QString::fromUtf8("直播预览中: %1").arg(displayPath(m_livePreviewPath)));
    }
}

void PlayerDock::updateStatus(const QString& text)
{
    if (m_statusLabel) {
        m_statusLabel->setText(text);
    }
}

void PlayerDock::setRecordingPath(const QString& path)
{
    m_recordingPath = path;
    updateReturnButtonVisibility();
}

void PlayerDock::clearRecordingPath()
{
    m_recordingPath.clear();
    updateReturnButtonVisibility();
}

void PlayerDock::onReturnToLiveClicked()
{
    if (m_recordingPath.isEmpty()) {
        return;
    }

    // 恢复直播预览
    playLivePreview(m_recordingPath);
    emit returnToLiveRequested();
}

void PlayerDock::updateReturnButtonVisibility()
{
    // 只有在有录制路径且当前不在直播预览模式时才显示"返回直播"按钮
    const bool hasRecording = !m_recordingPath.isEmpty();
    const bool isLivePreview = !m_livePreviewPath.isEmpty();
    m_returnLiveBtn->setVisible(hasRecording && !isLivePreview);
}
