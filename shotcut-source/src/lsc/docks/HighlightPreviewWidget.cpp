#include "HighlightPreviewWidget.h"

#include <QAudioOutput>
#include <QHBoxLayout>
#include <QLabel>
#include <QPushButton>
#include <QSlider>
#include <QStyle>
#include <QUrl>
#include <QVBoxLayout>
#include <QFileInfo>

namespace {
QUrl mediaSourceUrl(const QString& source)
{
    const QFileInfo fileInfo(source);
    if (fileInfo.exists()) {
        return QUrl::fromLocalFile(fileInfo.absoluteFilePath());
    }
    return QUrl::fromUserInput(source);
}

bool isUsableMediaSource(const QString& source)
{
    const QUrl url = mediaSourceUrl(source);
    if (url.isLocalFile()) {
        return QFileInfo::exists(url.toLocalFile());
    }
    return url.isValid() && !url.scheme().isEmpty();
}
}

HighlightPreviewWidget::HighlightPreviewWidget(QWidget* parent)
    : QWidget(parent)
{
    setupUi();

    m_player = new QMediaPlayer(this);
    m_audioOutput = new QAudioOutput(this);
    m_player->setAudioOutput(m_audioOutput);
    m_player->setVideoOutput(m_videoWidget);

    connect(m_player, &QMediaPlayer::positionChanged,
            this, &HighlightPreviewWidget::onPositionChanged);
    connect(m_player, &QMediaPlayer::durationChanged,
            this, &HighlightPreviewWidget::onDurationChanged);
    connect(m_player, &QMediaPlayer::playbackStateChanged,
            this, &HighlightPreviewWidget::onPlaybackStateChanged);

    m_playPauseBtn->setEnabled(false);
    m_stopBtn->setEnabled(false);
    m_positionSlider->setEnabled(false);
    m_exportBtn->setEnabled(false);
}

HighlightPreviewWidget::~HighlightPreviewWidget()
{
    m_player->stop();
}

void HighlightPreviewWidget::setupUi()
{
    QVBoxLayout* mainLayout = new QVBoxLayout(this);
    mainLayout->setContentsMargins(0, 0, 0, 0);
    mainLayout->setSpacing(2);

    m_videoWidget = new QVideoWidget(this);
    m_videoWidget->setMinimumSize(160, 90);
    // 使用 AspectRatioMode::KeepAspectRatio 让视频保持原始宽高比，减少黑边
    m_videoWidget->setAspectRatioMode(Qt::KeepAspectRatio);
    m_videoWidget->setSizePolicy(QSizePolicy::Expanding, QSizePolicy::Expanding);
    m_videoWidget->setStyleSheet("background-color: #000000;");
    mainLayout->addWidget(m_videoWidget, 1);

    QHBoxLayout* controlLayout = new QHBoxLayout();
    controlLayout->setContentsMargins(4, 2, 4, 2);
    controlLayout->setSpacing(4);

    m_playPauseBtn = new QPushButton(this);
    m_playPauseBtn->setIcon(style()->standardIcon(QStyle::SP_MediaPlay));
    m_playPauseBtn->setFixedSize(28, 28);
    m_playPauseBtn->setToolTip(QString::fromUtf8("播放/暂停"));
    connect(m_playPauseBtn, &QPushButton::clicked,
            this, &HighlightPreviewWidget::onPlayPauseClicked);
    controlLayout->addWidget(m_playPauseBtn);

    m_stopBtn = new QPushButton(this);
    m_stopBtn->setIcon(style()->standardIcon(QStyle::SP_MediaStop));
    m_stopBtn->setFixedSize(28, 28);
    m_stopBtn->setToolTip(QString::fromUtf8("停止"));
    connect(m_stopBtn, &QPushButton::clicked,
            this, &HighlightPreviewWidget::onStopClicked);
    controlLayout->addWidget(m_stopBtn);

    m_positionSlider = new QSlider(Qt::Horizontal, this);
    m_positionSlider->setRange(0, 0);
    connect(m_positionSlider, &QSlider::sliderPressed,
            this, &HighlightPreviewWidget::onSliderPressed);
    connect(m_positionSlider, &QSlider::sliderReleased,
            this, &HighlightPreviewWidget::onSliderReleased);
    controlLayout->addWidget(m_positionSlider, 1);

    m_timeLabel = new QLabel("00:00 / 00:00", this);
    m_timeLabel->setMinimumWidth(120);
    m_timeLabel->setStyleSheet("font-size: 11px; color: #ccc;");
    controlLayout->addWidget(m_timeLabel);

    m_exportBtn = new QPushButton(QString::fromUtf8("导出片段"), this);
    m_exportBtn->setToolTip(QString::fromUtf8("导出当前预览片段"));
    m_exportBtn->setStyleSheet(
        "QPushButton { background-color: #27ae60; color: white; font-weight: bold; "
        "padding: 2px 8px; border-radius: 3px; font-size: 11px; }"
        "QPushButton:hover { background-color: #2ecc71; }"
        "QPushButton:disabled { background-color: #95a5a6; }");
    connect(m_exportBtn, &QPushButton::clicked, [this]() {
        emit exportRequested(m_startSec, m_endSec);
    });
    controlLayout->addWidget(m_exportBtn);

    mainLayout->addLayout(controlLayout);
}

void HighlightPreviewWidget::playSegment(const QString& videoPath, double startSec, double endSec)
{
    if (videoPath.isEmpty() || !QFileInfo::exists(videoPath)) {
        return;
    }

    m_videoPath = videoPath;
    m_startSec = startSec;
    m_endSec = endSec;
    m_hasSegmentBounds = true;
    m_looping = true;

    m_player->setSource(QUrl::fromLocalFile(QFileInfo(videoPath).absoluteFilePath()));

    const qint64 startMs = static_cast<qint64>(startSec * 1000);
    m_player->setPosition(startMs);
    m_player->play();

    m_playPauseBtn->setEnabled(true);
    m_stopBtn->setEnabled(true);
    m_positionSlider->setEnabled(true);
    m_exportBtn->setEnabled(true);
    m_playPauseBtn->setIcon(style()->standardIcon(QStyle::SP_MediaPause));
}

void HighlightPreviewWidget::playVideo(const QString& videoPath)
{
    if (videoPath.isEmpty() || !isUsableMediaSource(videoPath)) {
        return;
    }

    m_videoPath = videoPath;
    m_startSec = 0.0;
    m_endSec = 0.0;
    m_hasSegmentBounds = false;
    m_looping = false;

    // If player is in error state, clear source first to reset it
    if (m_player->mediaStatus() == QMediaPlayer::InvalidMedia
        || m_player->mediaStatus() == QMediaPlayer::NoMedia) {
        m_player->setSource(QUrl());
    }

    m_player->setSource(mediaSourceUrl(videoPath));
    m_player->play();

    m_playPauseBtn->setEnabled(true);
    m_stopBtn->setEnabled(true);
    m_positionSlider->setEnabled(true);
    m_exportBtn->setEnabled(false);
    m_playPauseBtn->setIcon(style()->standardIcon(QStyle::SP_MediaPause));
}

void HighlightPreviewWidget::stop()
{
    m_player->stop();
    m_player->setSource(QUrl());
    m_playPauseBtn->setIcon(style()->standardIcon(QStyle::SP_MediaPlay));
    m_positionSlider->setValue(0);
    m_timeLabel->setText("00:00 / 00:00");
    m_hasSegmentBounds = false;
}

bool HighlightPreviewWidget::isPlaying() const
{
    return m_player->playbackState() == QMediaPlayer::PlayingState;
}

bool HighlightPreviewWidget::isLoading() const
{
    const auto status = m_player->mediaStatus();
    return status == QMediaPlayer::LoadingMedia
        || status == QMediaPlayer::BufferingMedia
        || status == QMediaPlayer::BufferedMedia;
}

void HighlightPreviewWidget::onPlayPauseClicked()
{
    if (m_player->playbackState() == QMediaPlayer::PlayingState) {
        m_player->pause();
    } else {
        m_player->play();
    }
}

void HighlightPreviewWidget::onStopClicked()
{
    stop();
}

void HighlightPreviewWidget::onPositionChanged(qint64 position)
{
    if (m_sliderDragging) {
        return;
    }

    if (!m_hasSegmentBounds) {
        m_positionSlider->setValue(static_cast<int>(position));
        m_timeLabel->setText(QString("%1 / %2")
                                 .arg(formatTime(position))
                                 .arg(formatTime(m_player->duration())));
        return;
    }

    const qint64 segmentStartMs = static_cast<qint64>(m_startSec * 1000);
    const qint64 segmentEndMs = static_cast<qint64>(m_endSec * 1000);
    const qint64 segmentPositionMs = qMax<qint64>(0, position - segmentStartMs);
    const qint64 segmentDurationMs = qMax<qint64>(0, segmentEndMs - segmentStartMs);

    m_positionSlider->setValue(static_cast<int>(segmentPositionMs));
    m_timeLabel->setText(QString("%1 / %2")
                             .arg(formatTime(segmentPositionMs))
                             .arg(formatTime(segmentDurationMs)));

    if (m_looping && position >= segmentEndMs) {
        m_player->setPosition(segmentStartMs);
    }
}

void HighlightPreviewWidget::onDurationChanged(qint64 duration)
{
    if (!m_hasSegmentBounds) {
        m_positionSlider->setRange(0, static_cast<int>(qMax<qint64>(0, duration)));
        return;
    }

    const qint64 segmentDurationMs = static_cast<qint64>((m_endSec - m_startSec) * 1000);
    m_positionSlider->setRange(0, static_cast<int>(qMax<qint64>(0, segmentDurationMs)));
}

void HighlightPreviewWidget::onPlaybackStateChanged(QMediaPlayer::PlaybackState state)
{
    if (state == QMediaPlayer::PlayingState) {
        m_playPauseBtn->setIcon(style()->standardIcon(QStyle::SP_MediaPause));
    } else {
        m_playPauseBtn->setIcon(style()->standardIcon(QStyle::SP_MediaPlay));
    }
}

void HighlightPreviewWidget::onSliderPressed()
{
    m_sliderDragging = true;
}

void HighlightPreviewWidget::onSliderReleased()
{
    m_sliderDragging = false;
    qint64 targetMs = m_positionSlider->value();
    if (m_hasSegmentBounds) {
        const qint64 segmentStartMs = static_cast<qint64>(m_startSec * 1000);
        const qint64 segmentEndMs = static_cast<qint64>(m_endSec * 1000);
        targetMs += segmentStartMs;
        // Clamp to segment bounds
        targetMs = qBound(segmentStartMs, targetMs, segmentEndMs - 100);
    }
    m_player->setPosition(qMax<qint64>(0, targetMs));
}

QString HighlightPreviewWidget::formatTime(qint64 ms) const
{
    if (ms < 0) {
        ms = 0;
    }

    const int secs = static_cast<int>(ms / 1000);
    const int minutes = secs / 60;
    const int seconds = secs % 60;
    return QString("%1:%2").arg(minutes, 2, 10, QChar('0')).arg(seconds, 2, 10, QChar('0'));
}
