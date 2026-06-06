# Task 6: 直播源 Dock 面板

## 任务目标

实现 Shotcut 风格的停靠面板（QDockWidget），提供直播URL输入、平台/画质选择、录制控制按钮、录制状态显示等功能。

## 创建文件

- `src/lsc/docks/LivestreamDock.h`
- `src/lsc/docks/LivestreamDock.cpp`

## 前置条件

- Task 5 已完成 (RecordingSession)

## LivestreamDock.h

```cpp
#ifndef LIVESTREAMDOCK_H
#define LIVESTREAMDOCK_H

#include <QDockWidget>
#include <QLineEdit>
#include <QComboBox>
#include <QPushButton>
#include <QLabel>
#include "livestream/RecordingSession.h"

class LivestreamDock : public QDockWidget
{
    Q_OBJECT
public:
    explicit LivestreamDock(QWidget* parent = nullptr);
    ~LivestreamDock();

    RecordingSession* session() const { return m_session; }

signals:
    void recordingStarted(const QString& filePath);
    void recordingStopped(const QString& filePath);

private slots:
    void onStartClicked();
    void onStopClicked();
    void onRecordingStarted(const QString& path);
    void onRecordingStopped(const QString& path, qint64 size);
    void onDurationChanged(qint64 ms);
    void onStatusChanged(RecordingStatus status);

private:
    void setupUI();
    QString formatDuration(qint64 ms) const;

    RecordingSession* m_session;

    QLineEdit* m_urlInput;
    QComboBox* m_platformCombo;
    QComboBox* m_qualityCombo;
    QPushButton* m_startBtn;
    QPushButton* m_stopBtn;
    QPushButton* m_pauseBtn;
    QLabel* m_statusLabel;
    QLabel* m_durationLabel;
    QLabel* m_sizeLabel;
    QLabel* m_platformInfoLabel;
};

#endif
```

## LivestreamDock.cpp

```cpp
#include "LivestreamDock.h"
#include <QVBoxLayout>
#include <QHBoxLayout>
#include <QFormLayout>
#include <QGroupBox>
#include <QDateTime>

LivestreamDock::LivestreamDock(QWidget* parent)
    : QDockWidget("直播源", parent)
    , m_session(new RecordingSession(this))
{
    setupUI();

    connect(m_session, &RecordingSession::recordingStarted,
            this, &LivestreamDock::onRecordingStarted);
    connect(m_session, &RecordingSession::recordingStopped,
            this, &LivestreamDock::onRecordingStopped);
    connect(m_session, &RecordingSession::durationChanged,
            this, &LivestreamDock::onDurationChanged);
    connect(m_session, &RecordingSession::statusChanged,
            this, &LivestreamDock::onStatusChanged);
}

LivestreamDock::~LivestreamDock() {}

void LivestreamDock::setupUI()
{
    QWidget* container = new QWidget(this);
    QVBoxLayout* layout = new QVBoxLayout(container);

    // 源配置
    QGroupBox* sourceGroup = new QGroupBox("直播源配置");
    QFormLayout* form = new QFormLayout(sourceGroup);
    m_urlInput = new QLineEdit();
    m_urlInput->setPlaceholderText("输入直播URL或粘贴分享链接...");
    form->addRow("直播地址:", m_urlInput);

    m_platformCombo = new QComboBox();
    m_platformCombo->addItems(
        {"自动检测", "抖音", "快手", "B站", "YouTube", "Twitch"});
    form->addRow("平台:", m_platformCombo);

    m_qualityCombo = new QComboBox();
    m_qualityCombo->addItems({"自动", "蓝光", "超清", "高清", "标清"});
    form->addRow("画质:", m_qualityCombo);
    layout->addWidget(sourceGroup);

    // 按钮
    QHBoxLayout* btnLayout = new QHBoxLayout();
    m_startBtn = new QPushButton("开始录制");
    m_startBtn->setStyleSheet(
        "QPushButton { background: #e74c3c; color: white; "
        "padding: 8px; border-radius: 4px; }");
    m_stopBtn = new QPushButton("停止");
    m_stopBtn->setEnabled(false);
    m_pauseBtn = new QPushButton("暂停");
    m_pauseBtn->setEnabled(false);
    btnLayout->addWidget(m_startBtn);
    btnLayout->addWidget(m_pauseBtn);
    btnLayout->addWidget(m_stopBtn);
    layout->addLayout(btnLayout);

    connect(m_startBtn, &QPushButton::clicked,
            this, &LivestreamDock::onStartClicked);
    connect(m_stopBtn, &QPushButton::clicked,
            this, &LivestreamDock::onStopClicked);

    // 状态
    QGroupBox* statusGroup = new QGroupBox("录制状态");
    QFormLayout* statusLayout = new QFormLayout(statusGroup);
    m_statusLabel = new QLabel("就绪");
    m_statusLabel->setStyleSheet("color: #a6e3a1;");
    statusLayout->addRow("状态:", m_statusLabel);
    m_durationLabel = new QLabel("00:00:00");
    statusLayout->addRow("时长:", m_durationLabel);
    m_sizeLabel = new QLabel("0 MB");
    statusLayout->addRow("大小:", m_sizeLabel);
    m_platformInfoLabel = new QLabel("—");
    statusLayout->addRow("平台:", m_platformInfoLabel);
    layout->addWidget(statusGroup);
    layout->addStretch();

    setWidget(container);
    setMinimumWidth(280);
}

void LivestreamDock::onStartClicked()
{
    QString url = m_urlInput->text().trimmed();
    if (url.isEmpty()) return;

    RecordingConfig cfg;
    cfg.outputPath = QString("recordings/livestream_%1.mp4")
        .arg(QDateTime::currentDateTime().toString("yyyyMMdd_hhmmss"));

    m_session->startRecording(url, cfg);
}

void LivestreamDock::onStopClicked() { m_session->stopRecording(); }

void LivestreamDock::onRecordingStarted(const QString& path)
{
    m_startBtn->setEnabled(false);
    m_stopBtn->setEnabled(true);
    m_pauseBtn->setEnabled(true);
    emit recordingStarted(path);
}

void LivestreamDock::onRecordingStopped(const QString& path, qint64 size)
{
    m_startBtn->setEnabled(true);
    m_stopBtn->setEnabled(false);
    m_pauseBtn->setEnabled(false);
    emit recordingStopped(path);
}

void LivestreamDock::onDurationChanged(qint64 ms)
{
    m_durationLabel->setText(formatDuration(ms));
}

void LivestreamDock::onStatusChanged(RecordingStatus status)
{
    switch (status) {
    case RecordingStatus::Stopped:
        m_statusLabel->setText("已停止");
        m_statusLabel->setStyleSheet("color: #888;"); break;
    case RecordingStatus::Recording:
        m_statusLabel->setText("录制中");
        m_statusLabel->setStyleSheet("color: #e74c3c; font-weight: bold;"); break;
    case RecordingStatus::Reconnecting:
        m_statusLabel->setText("重连中...");
        m_statusLabel->setStyleSheet("color: #f9e2af;"); break;
    case RecordingStatus::Error:
        m_statusLabel->setText("错误");
        m_statusLabel->setStyleSheet("color: #e74c3c;"); break;
    default:
        m_statusLabel->setText("就绪");
        m_statusLabel->setStyleSheet("color: #a6e3a1;");
    }
}

QString LivestreamDock::formatDuration(qint64 ms) const
{
    int s = ms / 1000;
    return QString("%1:%2:%3")
        .arg(s / 3600, 2, 10, QChar('0'))
        .arg((s % 3600) / 60, 2, 10, QChar('0'))
        .arg(s % 60, 2, 10, QChar('0'));
}
```

## 验证

```bash
cd shotcut-source/build
cmake --build . --config Release
```

预期：编译通过。
