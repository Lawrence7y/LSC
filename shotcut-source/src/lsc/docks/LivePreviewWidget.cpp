#include "LivePreviewWidget.h"

#include <QFileInfo>

LivePreviewWidget::LivePreviewWidget(QWidget* parent)
    : QWidget(parent)
    , m_player(new QMediaPlayer(this))
    , m_videoWidget(new QVideoWidget(this))
    , m_placeholderLabel(new QLabel(QString::fromUtf8("录制开始后预览画面将显示在这里"), this))
    , m_layout(new QVBoxLayout(this))
{
    m_layout->setContentsMargins(0, 0, 0, 0);

    m_placeholderLabel->setAlignment(Qt::AlignCenter);
    m_placeholderLabel->setStyleSheet("color: #95a5a6; font-size: 14px;");
    m_layout->addWidget(m_placeholderLabel);

    m_videoWidget->setVisible(false);
    m_layout->addWidget(m_videoWidget);

    m_player->setVideoOutput(m_videoWidget);

    connect(m_player, &QMediaPlayer::mediaStatusChanged,
            this, &LivePreviewWidget::onMediaStatusChanged);
    connect(m_player, &QMediaPlayer::errorOccurred,
            this, &LivePreviewWidget::onErrorOccurred);
}

void LivePreviewWidget::startPreview(const QString& sourcePath)
{
    if (m_isPreviewing) {
        stopPreview();
    }

    if (sourcePath.isEmpty() || !QFileInfo::exists(sourcePath)) {
        return;
    }

    m_placeholderLabel->setVisible(false);
    m_videoWidget->setVisible(true);

    m_player->setSource(QUrl::fromLocalFile(sourcePath));
    m_player->play();
    m_isPreviewing = true;
}

void LivePreviewWidget::stopPreview()
{
    if (!m_isPreviewing) {
        return;
    }

    m_player->stop();
    m_player->setSource(QUrl());

    m_videoWidget->setVisible(false);
    m_placeholderLabel->setVisible(true);

    m_isPreviewing = false;
}

void LivePreviewWidget::onMediaStatusChanged(QMediaPlayer::MediaStatus status)
{
    if (status == QMediaPlayer::EndOfMedia) {
        // Loop playback for continuous preview
        // Guard against corrupted files that would cause infinite error loops
        if (m_player->duration() > 0) {
            m_player->setPosition(0);
            m_player->play();
        } else {
            stopPreview();
        }
    }
}

void LivePreviewWidget::onErrorOccurred(QMediaPlayer::Error error)
{
    Q_UNUSED(error)
    // Don't fully stop — just reset the flag so the refresh timer can retry.
    // The timer will call playVideo() again when the file has more data.
    m_isPreviewing = false;
    m_player->stop();
}
