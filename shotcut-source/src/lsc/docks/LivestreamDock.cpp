#include "LivestreamDock.h"
#include "LivePreviewWidget.h"
#include "analyzer/ClipExporter.h"
#include "analyzer/HighlightEngine.h"
#include "LscConfig.h"
#include "livestream/PreviewController.h"

#include <QDateTime>
#include <QDir>
#include <QFileDialog>
#include <QFileInfo>
#include <QFormLayout>
#include <QFrame>
#include <QGroupBox>
#include <QHBoxLayout>
#include <QMessageBox>
#include <QScrollArea>
#include <QSignalBlocker>
#include <QVBoxLayout>

#define MODULE_NAME "LivestreamDock"

namespace {
QString textOrFallback(const QString& value, const QString& fallback = QStringLiteral("-"))
{
    return value.trimmed().isEmpty() ? fallback : value.trimmed();
}

QString dockScrollBarStyle()
{
    return QStringLiteral(
        "QScrollBar:vertical {"
        " background: #f5f6f8;"
        " width: 12px;"
        " margin: 6px 2px 6px 2px;"
        " border-radius: 6px;"
        "}"
        "QScrollBar::handle:vertical {"
        " background: #c8ccd3;"
        " min-height: 48px;"
        " border-radius: 6px;"
        "}"
        "QScrollBar::handle:vertical:hover {"
        " background: #b5bac3;"
        "}"
        "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {"
        " height: 0px;"
        "}"
        "QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {"
        " background: transparent;"
        "}");
}
}

LivestreamDock::LivestreamDock(QWidget* parent)
    : QDockWidget(QString::fromUtf8("直播录制"), parent)
    , m_session(new RecordingSession(this))
    , m_previewController(new PreviewController(this))
    , m_livePreviewWidget(new LivePreviewWidget(this))
    , m_gameplayDetector(new GameplayDetector(this))
    , m_gameplayExporter(new ClipExporter(this))
{
    setupUi();

    connect(m_previewController, &PreviewController::previewAvailable,
            m_livePreviewWidget, &LivePreviewWidget::startPreview);
    connect(m_previewController, &PreviewController::previewCleared,
            m_livePreviewWidget, &LivePreviewWidget::stopPreview);
    connect(m_session, &RecordingSession::previewSourceChanged,
            m_previewController, &PreviewController::setPreviewSource);
    connect(m_session, &RecordingSession::previewStopped,
            m_previewController, &PreviewController::clearPreviewSource);

    connect(m_gameplayDetector, &GameplayDetector::stateChanged,
            [this](GameState state) {
                const char* stateNames[] = {"未知", "游戏中", "买局阶段", "回合结束", "大厅", "加载中"};
                const int idx = static_cast<int>(state);
                const QString name = (idx >= 0 && idx < 6) ? stateNames[idx] : "未知";
                m_logOutput->append(
                    QString("<span style='color:#3498db'>[%1] 游戏状态: %2</span>")
                        .arg(QDateTime::currentDateTime().toString("hh:mm:ss"))
                        .arg(name));
            });
    connect(m_gameplayDetector, &GameplayDetector::gameplayStarted,
            [this]() {
                m_logOutput->append(
                    QString("<span style='color:#2ecc71'>[%1] 游戏开始 - 继续录制</span>")
                        .arg(QDateTime::currentDateTime().toString("hh:mm:ss")));
            });
    connect(m_gameplayDetector, &GameplayDetector::gameplayEnded,
            [this]() {
                m_logOutput->append(
                    QString("<span style='color:#f39c12'>[%1] 等待阶段 - 可跳过</span>")
                        .arg(QDateTime::currentDateTime().toString("hh:mm:ss")));
            });

    connect(m_session, &RecordingSession::recordingStarted,
            this, &LivestreamDock::onRecordingStarted);
    connect(m_session, &RecordingSession::recordingStopped,
            this, &LivestreamDock::onRecordingStopped);
    connect(m_session, &RecordingSession::errorOccurred,
            this, &LivestreamDock::onError);
    connect(m_session, &RecordingSession::progressUpdated,
            this, &LivestreamDock::onProgress);
    connect(m_session, &RecordingSession::platformParsed,
            this, &LivestreamDock::onPlatformParsed);
    connect(m_session, &RecordingSession::reconnecting,
            this, &LivestreamDock::onReconnecting);
    connect(m_session, &RecordingSession::statusChanged,
            this, &LivestreamDock::onStatusChanged);
    connect(m_session, &RecordingSession::highlightFound,
            this, &LivestreamDock::onHighlightDetected);
    connect(m_session, &RecordingSession::clipExported,
            this, &LivestreamDock::onClipReady);
}

LivestreamDock::~LivestreamDock()
{
    if (m_session->isRecording()) {
        m_session->stopRecording();
    }
}

void LivestreamDock::setupUi()
{
    const auto& cfg = lsc::LscConfig::instance();

    QScrollArea* scrollArea = new QScrollArea(this);
    scrollArea->setWidgetResizable(true);
    scrollArea->setFrameShape(QFrame::NoFrame);
    scrollArea->setHorizontalScrollBarPolicy(Qt::ScrollBarAlwaysOff);
    scrollArea->setStyleSheet(dockScrollBarStyle());

    QWidget* mainWidget = new QWidget(scrollArea);
    QVBoxLayout* mainLayout = new QVBoxLayout(mainWidget);

    QGroupBox* urlGroup = new QGroupBox(QString::fromUtf8("直播源"), mainWidget);
    QVBoxLayout* urlLayout = new QVBoxLayout(urlGroup);

    QHBoxLayout* urlRow = new QHBoxLayout();
    m_urlInput = new QLineEdit();
    m_urlInput->setPlaceholderText(
        QString::fromUtf8("输入直播链接，例如 https://live.douyin.com/53682367755"));
    m_urlInput->setClearButtonEnabled(true);
    urlRow->addWidget(m_urlInput, 1);

    m_startStopBtn = new QPushButton(QString::fromUtf8("开始录制"));
    m_startStopBtn->setMinimumWidth(100);
    m_startStopBtn->setStyleSheet(
        "QPushButton { background-color: #2ecc71; color: white; font-weight: bold; "
        "padding: 6px 16px; border-radius: 4px; }"
        "QPushButton:hover { background-color: #27ae60; }"
        "QPushButton:disabled { background-color: #95a5a6; }");
    connect(m_startStopBtn, &QPushButton::clicked, this, &LivestreamDock::onStartStopClicked);
    urlRow->addWidget(m_startStopBtn);
    urlLayout->addLayout(urlRow);

    QHBoxLayout* outRow = new QHBoxLayout();
    QLabel* outLabel = new QLabel(QString::fromUtf8("保存到:"));
    m_outputDirEdit = new QLineEdit(QDir::home().filePath(cfg.defaultOutputSubdir));
    m_outputDirEdit->setObjectName(QStringLiteral("outputDirEdit"));
    QPushButton* browseBtn = new QPushButton("...");
    browseBtn->setMaximumWidth(30);
    connect(browseBtn, &QPushButton::clicked, [this]() {
        const QString dir = QFileDialog::getExistingDirectory(
            this, QString::fromUtf8("选择保存目录"), m_outputDirEdit->text());
        if (!dir.isEmpty()) {
            m_outputDirEdit->setText(dir);
        }
    });
    outRow->addWidget(outLabel);
    outRow->addWidget(m_outputDirEdit, 1);
    outRow->addWidget(browseBtn);
    urlLayout->addLayout(outRow);
    mainLayout->addWidget(urlGroup);

    QGroupBox* previewGroup = new QGroupBox(QString::fromUtf8("直播预览"), mainWidget);
    QVBoxLayout* previewLayout = new QVBoxLayout(previewGroup);
    m_livePreviewWidget->setMinimumHeight(200);
    previewLayout->addWidget(m_livePreviewWidget);
    previewGroup->setVisible(false);
    mainLayout->addWidget(previewGroup);

    QGroupBox* statusGroup = new QGroupBox(QString::fromUtf8("状态"), mainWidget);
    QFormLayout* statusLayout = new QFormLayout(statusGroup);

    m_statusLabel = new QLabel(QString::fromUtf8("就绪"));
    m_statusLabel->setStyleSheet("font-weight: bold; color: #27ae60;");
    statusLayout->addRow(QString::fromUtf8("状态:"), m_statusLabel);

    m_platformLabel = new QLabel("-");
    statusLayout->addRow(QString::fromUtf8("平台:"), m_platformLabel);

    m_titleLabel = new QLabel("-");
    m_titleLabel->setWordWrap(true);
    statusLayout->addRow(QString::fromUtf8("标题:"), m_titleLabel);

    m_streamerLabel = new QLabel("-");
    statusLayout->addRow(QString::fromUtf8("主播:"), m_streamerLabel);

    m_durationLabel = new QLabel("00:00:00");
    statusLayout->addRow(QString::fromUtf8("时长:"), m_durationLabel);

    m_fileSizeLabel = new QLabel("0 MB");
    statusLayout->addRow(QString::fromUtf8("大小:"), m_fileSizeLabel);

    m_progressBar = new QProgressBar();
    m_progressBar->setVisible(false);
    statusLayout->addRow(m_progressBar);
    mainLayout->addWidget(statusGroup);

    QGroupBox* logGroup = new QGroupBox(QString::fromUtf8("日志"), mainWidget);
    QVBoxLayout* logLayout = new QVBoxLayout(logGroup);
    m_logOutput = new QTextEdit();
    m_logOutput->setReadOnly(true);
    m_logOutput->setMaximumHeight(120);
    m_logOutput->setPlaceholderText(QString::fromUtf8("操作日志会显示在这里..."));
    logLayout->addWidget(m_logOutput);
    mainLayout->addWidget(logGroup);

    QGroupBox* encGroup =
        new QGroupBox(QString::fromUtf8("编码设置（文件大小优化）"), mainWidget);
    QFormLayout* encLayout = new QFormLayout(encGroup);

    m_sourceQualityCombo = new QComboBox();
    m_sourceQualityCombo->setObjectName(QStringLiteral("sourceQualityCombo"));
    m_sourceQualityCombo->addItem(QString::fromUtf8("自动 / 最佳"), QStringLiteral("best"));
    m_sourceQualityCombo->addItem(QString::fromUtf8("原画"), QStringLiteral("origin"));
    m_sourceQualityCombo->addItem("UHD", QStringLiteral("uhd"));
    m_sourceQualityCombo->addItem("HD", QStringLiteral("hd"));
    m_sourceQualityCombo->addItem("SD", QStringLiteral("sd"));
    m_sourceQualityCombo->addItem("LD", QStringLiteral("ld"));
    m_sourceQualityCombo->addItem(QString::fromUtf8("本地源"), QStringLiteral("source"));
    const int defaultQualityIndex = m_sourceQualityCombo->findData(cfg.defaultSourceQuality);
    if (defaultQualityIndex >= 0) {
        m_sourceQualityCombo->setCurrentIndex(defaultQualityIndex);
    }
    encLayout->addRow(QString::fromUtf8("源流画质:"), m_sourceQualityCombo);

    m_encodeModeCombo = new QComboBox();
    m_encodeModeCombo->addItem(QString::fromUtf8("CRF 恒定质量（推荐）"),
                               static_cast<int>(EncodeMode::CRF));
    m_encodeModeCombo->addItem(QString::fromUtf8("原流复制（不重新编码）"),
                               static_cast<int>(EncodeMode::StreamCopy));
    m_encodeModeCombo->addItem(QString::fromUtf8("固定码率（可预测文件大小）"),
                               static_cast<int>(EncodeMode::TargetBitrate));
    m_encodeModeCombo->addItem(QString::fromUtf8("硬件编码（NVENC/AMF）"),
                               static_cast<int>(EncodeMode::Hardware));
    connect(m_encodeModeCombo, QOverload<int>::of(&QComboBox::currentIndexChanged),
            this, &LivestreamDock::onEncodeModeChanged);
    encLayout->addRow(QString::fromUtf8("编码模式:"), m_encodeModeCombo);

    QWidget* crfFieldWidget = new QWidget(encGroup);
    QVBoxLayout* crfFieldLayout = new QVBoxLayout(crfFieldWidget);
    crfFieldLayout->setContentsMargins(0, 0, 0, 0);
    crfFieldLayout->setSpacing(4);
    QHBoxLayout* crfRow = new QHBoxLayout();
    m_crfSlider = new QSlider(Qt::Horizontal);
    m_crfSlider->setRange(18, 28);
    m_crfSlider->setValue(23);
    m_crfSlider->setTickPosition(QSlider::TicksBelow);
    m_crfSlider->setTickInterval(2);
    connect(m_crfSlider, &QSlider::valueChanged, this, &LivestreamDock::onCrfSliderChanged);
    m_crfLabel = new QLabel();
    m_crfLabel->setMinimumWidth(220);
    crfRow->addWidget(new QLabel(QString::fromUtf8("18（高画质）")));
    crfRow->addWidget(m_crfSlider);
    crfRow->addWidget(new QLabel(QString::fromUtf8("28（小文件）")));
    crfFieldLayout->addLayout(crfRow);
    crfFieldLayout->addWidget(m_crfLabel);
    encLayout->addRow(QString::fromUtf8("CRF:"), crfFieldWidget);

    m_presetCombo = new QComboBox();
    m_presetCombo->addItems({"ultrafast", "veryfast", "fast", "medium", "slow"});
    m_presetCombo->setCurrentText("medium");
    encLayout->addRow(QString::fromUtf8("编码速度:"), m_presetCombo);

    m_bitrateSpin = new QSpinBox();
    m_bitrateSpin->setRange(500, 50000);
    m_bitrateSpin->setValue(4000);
    m_bitrateSpin->setSuffix(" kbps");
    m_bitrateSpin->setSingleStep(500);
    encLayout->addRow(QString::fromUtf8("目标码率:"), m_bitrateSpin);

    QHBoxLayout* resRow = new QHBoxLayout();
    m_maxWidthSpin = new QSpinBox();
    m_maxWidthSpin->setRange(0, 7680);
    m_maxWidthSpin->setValue(0);
    m_maxWidthSpin->setSpecialValueText(QString::fromUtf8("原始"));
    m_maxWidthSpin->setSuffix(" px");
    m_maxHeightSpin = new QSpinBox();
    m_maxHeightSpin->setRange(0, 4320);
    m_maxHeightSpin->setValue(0);
    m_maxHeightSpin->setSpecialValueText(QString::fromUtf8("原始"));
    m_maxHeightSpin->setSuffix(" px");
    resRow->addWidget(m_maxWidthSpin);
    resRow->addWidget(new QLabel(" x "));
    resRow->addWidget(m_maxHeightSpin);
    encLayout->addRow(QString::fromUtf8("最大分辨率:"), resRow);

    mainLayout->addWidget(encGroup);

    QGroupBox* profileGroup = new QGroupBox(QString::fromUtf8("直播类型"), mainWidget);
    QFormLayout* profileLayout = new QFormLayout(profileGroup);
    m_contentProfileCombo = new QComboBox();
    m_contentProfileCombo->setObjectName(QStringLiteral("contentProfileCombo"));
    m_contentProfileCombo->addItem(QString::fromUtf8("通用直播"), QStringLiteral("generic"));
    m_contentProfileCombo->addItem(QString::fromUtf8("舞蹈直播"), QStringLiteral("dance"));
    m_contentProfileCombo->addItem(QString::fromUtf8("无畏契约"), QStringLiteral("valorant"));
    m_contentProfileCombo->addItem(QString::fromUtf8("解说切片"), QStringLiteral("commentary"));
    const int defaultProfileIndex = m_contentProfileCombo->findData(cfg.defaultAnalysisProfile);
    if (defaultProfileIndex >= 0) {
        m_contentProfileCombo->setCurrentIndex(defaultProfileIndex);
    }
    profileLayout->addRow(QString::fromUtf8("内容类型:"), m_contentProfileCombo);
    mainLayout->addWidget(profileGroup);

    QGroupBox* selectiveGroup = new QGroupBox(QString::fromUtf8("选择性录制"), mainWidget);
    QFormLayout* selectiveLayout = new QFormLayout(selectiveGroup);
    m_selectiveRecordingCheck = new QCheckBox(
        QString::fromUtf8("启用游戏状态监测（标记等待/买局阶段）"));
    m_selectiveRecordingCheck->setToolTip(
        QString::fromUtf8("实时检测游戏状态并写入日志；当前版本不会停止写盘或删除等待阶段"));
    selectiveLayout->addRow(m_selectiveRecordingCheck);

    m_gameKeyCombo = new QComboBox();
    m_gameKeyCombo->setObjectName(QStringLiteral("gameKeyCombo"));
    m_gameKeyCombo->addItem(QString::fromUtf8("无畏契约"), QStringLiteral("valorant"));
    m_gameKeyCombo->addItem(QString::fromUtf8("通用 FPS"), QStringLiteral("fps"));
    const int defaultGameIndex = m_gameKeyCombo->findData(cfg.defaultGameKey);
    if (defaultGameIndex >= 0) {
        m_gameKeyCombo->setCurrentIndex(defaultGameIndex);
    }
    m_gameKeyCombo->setEnabled(false);
    selectiveLayout->addRow(QString::fromUtf8("游戏:"), m_gameKeyCombo);

    connect(m_selectiveRecordingCheck, &QCheckBox::toggled,
            m_gameKeyCombo, &QComboBox::setEnabled);
    mainLayout->addWidget(selectiveGroup);

    mainLayout->addStretch();
    scrollArea->setWidget(mainWidget);
    setWidget(scrollArea);

    onCrfSliderChanged(m_crfSlider->value());
    onEncodeModeChanged(m_encodeModeCombo->currentIndex());
}

void LivestreamDock::onEncodeModeChanged(int index)
{
    const EncodeMode mode =
        static_cast<EncodeMode>(m_encodeModeCombo->itemData(index).toInt());
    const bool isCrf = (mode == EncodeMode::CRF);
    const bool isBitrate = (mode == EncodeMode::TargetBitrate || mode == EncodeMode::Hardware);
    m_crfSlider->setEnabled(isCrf);
    m_crfLabel->setEnabled(isCrf);
    m_presetCombo->setEnabled(mode != EncodeMode::StreamCopy);
    m_bitrateSpin->setEnabled(isBitrate);
}

void LivestreamDock::applyRuntimeConfig()
{
    const auto& cfg = lsc::LscConfig::instance();

    if (m_sourceQualityCombo) {
        const int qualityIndex = m_sourceQualityCombo->findData(cfg.defaultSourceQuality);
        if (qualityIndex >= 0) {
            m_sourceQualityCombo->setCurrentIndex(qualityIndex);
        }
    }

    if (m_contentProfileCombo) {
        const int profileIndex = m_contentProfileCombo->findData(cfg.defaultAnalysisProfile);
        if (profileIndex >= 0) {
            m_contentProfileCombo->setCurrentIndex(profileIndex);
        }
    }

    if (m_gameKeyCombo) {
        const int gameIndex = m_gameKeyCombo->findData(cfg.defaultGameKey);
        if (gameIndex >= 0) {
            m_gameKeyCombo->setCurrentIndex(gameIndex);
        }
    }

    if (m_outputDirEdit && !m_isRecording) {
        m_outputDirEdit->setText(QDir::home().filePath(cfg.defaultOutputSubdir));
    }
}

void LivestreamDock::onCrfSliderChanged(int value)
{
    QString desc;
    if (value <= 18) {
        desc = QString::fromUtf8("接近无损，文件较大");
    } else if (value <= 20) {
        desc = QString::fromUtf8("高画质");
    } else if (value <= 23) {
        desc = QString::fromUtf8("默认，画质与体积平衡");
    } else if (value <= 26) {
        desc = QString::fromUtf8("压缩率更高，适合长时间录制");
    } else {
        desc = QString::fromUtf8("高压缩，小文件优先");
    }
    m_crfLabel->setText(QString::fromUtf8("CRF %1 - %2").arg(value).arg(desc));
}

RecordingConfig LivestreamDock::buildConfigFromUI()
{
    RecordingConfig config;
    config.outputPath = generateOutputPath();
    config.sourceQuality = m_sourceQualityCombo->currentData().toString();
    config.encodeMode = static_cast<EncodeMode>(m_encodeModeCombo->currentData().toInt());
    config.crf = m_crfSlider->value();
    config.preset = m_presetCombo->currentText();
    config.videoBitrate = m_bitrateSpin->value();
    config.maxWidth = m_maxWidthSpin->value();
    config.maxHeight = m_maxHeightSpin->value();
    // Ensure both dimensions are set if one is set (FFmpeg scale filter requires both)
    if (config.maxWidth > 0 && config.maxHeight == 0) {
        config.maxHeight = config.maxWidth * 9 / 16;  // Assume 16:9
    } else if (config.maxHeight > 0 && config.maxWidth == 0) {
        config.maxWidth = config.maxHeight * 16 / 9;  // Assume 16:9
    }
    const auto& cfg = lsc::LscConfig::instance();
    config.autoReconnect = cfg.defaultAutoReconnect;
    config.reconnectRetries = cfg.defaultReconnectRetries;
    config.reconnectDelayMs = cfg.defaultReconnectDelayMs;
    config.maxReconnectDelayMs = cfg.maxReconnectDelayMs;
    config.stallTimeoutSec = cfg.stallTimeoutSec;
    config.maxDurationSec = cfg.maxRecordingDurationSec;
    return config;
}

AnalysisProfile LivestreamDock::buildAnalysisProfileFromUi() const
{
    const QString profileId = m_contentProfileCombo->currentData().toString();
    if (profileId == "valorant") {
        return AnalysisProfile::valorant();
    }
    if (profileId == "dance") {
        return AnalysisProfile::dance();
    }
    if (profileId == "commentary") {
        return AnalysisProfile::commentary();
    }
    return AnalysisProfile::generic();
}

void LivestreamDock::onStartStopClicked()
{
    if (m_isRecording) {
        m_session->stopRecording();
        return;
    }

    const QString url = m_urlInput->text().trimmed();
    if (url.isEmpty()) {
        QMessageBox::warning(this, QString::fromUtf8("错误"),
                             QString::fromUtf8("请输入直播链接。"));
        return;
    }

    const RecordingConfig config = buildConfigFromUI();
    const AnalysisProfile profile = buildAnalysisProfileFromUi();
    m_session->setAnalysisProfile(profile);
    if (HighlightEngine* engine = m_session->highlightEngine()) {
        engine->setAutoExport(true, ClipExporter::defaultHighlightDirForSource(config.outputPath));
    }

    m_logOutput->clear();
    m_logOutput->append(QString::fromUtf8("[%1] 开始解析: %2")
                            .arg(QDateTime::currentDateTime().toString("hh:mm:ss"))
                            .arg(url));
    m_logOutput->append(QString::fromUtf8("[%1] 直播类型: %2")
                            .arg(QDateTime::currentDateTime().toString("hh:mm:ss"))
                            .arg(profile.displayName));

    if (m_selectiveRecordingCheck->isChecked()) {
        const QString gameKey = m_gameKeyCombo->currentData().toString();
        m_gameplayDetector->setGameKey(gameKey);
        m_logOutput->append(QString::fromUtf8("[%1] 游戏状态监测: 已启用 (游戏: %2)")
                                .arg(QDateTime::currentDateTime().toString("hh:mm:ss"))
                                .arg(m_gameKeyCombo->currentText()));
    }

    m_progressBar->setVisible(true);
    m_progressBar->setRange(0, 0);

    m_session->startRecording(url, config);
}

void LivestreamDock::onRecordingStarted(const QString& path)
{
    m_isRecording = true;
    m_startStopBtn->setText(QString::fromUtf8("停止录制"));
    m_startStopBtn->setStyleSheet(
        "QPushButton { background-color: #e74c3c; color: white; font-weight: bold; "
        "padding: 6px 16px; border-radius: 4px; }"
        "QPushButton:hover { background-color: #c0392b; }");
    m_urlInput->setEnabled(false);
    m_outputDirEdit->setEnabled(false);

    m_logOutput->append(QString::fromUtf8("[%1] 录制已开始: %2")
                            .arg(QDateTime::currentDateTime().toString("hh:mm:ss"))
                            .arg(path));

    // Start gameplay detection for selective recording
    if (m_selectiveRecordingCheck->isChecked()) {
        m_gameplayDetector->startMonitoring(path);
    }
}

void LivestreamDock::onRecordingStopped(const QString& path, qint64 size)
{
    const auto gameplaySegments = m_gameplayDetector->gameplaySegments();
    m_gameplayDetector->stopMonitoring();
    if (m_selectiveRecordingCheck->isChecked() && !gameplaySegments.isEmpty()) {
        QVector<ClipJob> jobs;
        const QFileInfo sourceInfo(path);
        const QString outputDir = sourceInfo.dir().filePath(QStringLiteral("gameplay_segments"));
        int index = 1;
        for (const GameplayTimeSegment& segment : gameplaySegments) {
            ClipJob job;
            job.sourcePath = path;
            job.startSec = segment.startSec;
            job.endSec = segment.endSec;
            job.title = QStringLiteral("gameplay_%1").arg(index, 3, 10, QLatin1Char('0'));
            job.outputPath = QDir(outputDir).filePath(job.title + QStringLiteral(".mp4"));
            job.useCopy = true;
            jobs.append(job);
            ++index;
        }

        ExportConfig exportConfig;
        exportConfig.outputDir = outputDir;
        exportConfig.codec = QStringLiteral("copy");
        m_gameplayExporter->exportBatch(jobs, exportConfig);
        m_logOutput->append(QString::fromUtf8("[%1] 已提交 Gameplay 后处理导出: %2 段")
                                .arg(QDateTime::currentDateTime().toString("hh:mm:ss"))
                                .arg(jobs.size()));
    }
    m_isRecording = false;
    m_startStopBtn->setText(QString::fromUtf8("开始录制"));
    m_startStopBtn->setStyleSheet(
        "QPushButton { background-color: #2ecc71; color: white; font-weight: bold; "
        "padding: 6px 16px; border-radius: 4px; }"
        "QPushButton:hover { background-color: #27ae60; }");
    m_urlInput->setEnabled(true);
    m_outputDirEdit->setEnabled(true);
    m_progressBar->setVisible(false);

    const double sizeMB = size / (1024.0 * 1024.0);
    m_logOutput->append(QString::fromUtf8("[%1] 录制完成: %2 (%3 MB)")
                            .arg(QDateTime::currentDateTime().toString("hh:mm:ss"))
                            .arg(path)
                            .arg(sizeMB, 0, 'f', 1));

    emit recordingFinished(path);
}

void LivestreamDock::onError(const QString& error)
{
    m_logOutput->append(QString::fromUtf8("<span style='color:red'>[%1] 错误: %2</span>")
                            .arg(QDateTime::currentDateTime().toString("hh:mm:ss"))
                            .arg(error));

    m_statusLabel->setText(QString::fromUtf8("错误"));
    m_statusLabel->setStyleSheet("font-weight: bold; color: #e74c3c;");

    m_isRecording = false;
    m_startStopBtn->setText(QString::fromUtf8("开始录制"));
    m_urlInput->setEnabled(true);
    m_outputDirEdit->setEnabled(true);
    m_progressBar->setVisible(false);
}

void LivestreamDock::onProgress(qint64 durationMs, qint64 fileSizeBytes)
{
    const int secs = static_cast<int>(durationMs / 1000);
    const int h = secs / 3600;
    const int m = (secs % 3600) / 60;
    const int s = secs % 60;
    m_durationLabel->setText(QString("%1:%2:%3")
                                 .arg(h, 2, 10, QChar('0'))
                                 .arg(m, 2, 10, QChar('0'))
                                 .arg(s, 2, 10, QChar('0')));

    const double mb = fileSizeBytes / (1024.0 * 1024.0);
    m_fileSizeLabel->setText(QString("%1 MB").arg(mb, 0, 'f', 1));
}

void LivestreamDock::onPlatformParsed(const PlatformInfo& info)
{
    populateSourceQualityOptions(info);

    QString platformText = info.platform;
    if (!info.preferredQuality.isEmpty()) {
        platformText += QString(" (%1)").arg(info.preferredQuality);
    }
    m_platformLabel->setText(platformText);
    m_titleLabel->setText(textOrFallback(info.title));
    m_streamerLabel->setText(textOrFallback(info.streamerName));

    m_logOutput->append(QString::fromUtf8("[%1] 平台识别: %2，已获取可录制流")
                            .arg(QDateTime::currentDateTime().toString("hh:mm:ss"))
                            .arg(info.platform));
    if (!info.title.isEmpty() || !info.streamerName.isEmpty()) {
        m_logOutput->append(QString::fromUtf8("[%1] 标题: %2 | 主播: %3")
                                .arg(QDateTime::currentDateTime().toString("hh:mm:ss"))
                                .arg(textOrFallback(info.title))
                                .arg(textOrFallback(info.streamerName)));
    }
    if (!info.roomId.isEmpty()) {
        m_logOutput->append(QString::fromUtf8("[%1] 房间 ID: %2")
                                .arg(QDateTime::currentDateTime().toString("hh:mm:ss"))
                                .arg(info.roomId));
    }
}

void LivestreamDock::onReconnecting(int attempt, int maxAttempts)
{
    m_statusLabel->setText(QString::fromUtf8("重连中（%1/%2）").arg(attempt).arg(maxAttempts));
    m_statusLabel->setStyleSheet("font-weight: bold; color: #f39c12;");
    m_logOutput->append(QString::fromUtf8("[%1] 正在重连...（%2/%3）")
                            .arg(QDateTime::currentDateTime().toString("hh:mm:ss"))
                            .arg(attempt)
                            .arg(maxAttempts));
}

void LivestreamDock::onStatusChanged(RecordingStatus status)
{
    switch (status) {
    case RecordingStatus::Recording:
        m_statusLabel->setText(QString::fromUtf8("录制中"));
        m_statusLabel->setStyleSheet("font-weight: bold; color: #e74c3c;");
        break;
    case RecordingStatus::Reconnecting:
        m_statusLabel->setText(QString::fromUtf8("重连中"));
        m_statusLabel->setStyleSheet("font-weight: bold; color: #f39c12;");
        break;
    case RecordingStatus::Stopped:
        m_statusLabel->setText(QString::fromUtf8("已停止"));
        m_statusLabel->setStyleSheet("font-weight: bold; color: #95a5a6;");
        break;
    case RecordingStatus::Error:
        m_statusLabel->setText(QString::fromUtf8("错误"));
        m_statusLabel->setStyleSheet("font-weight: bold; color: #e74c3c;");
        break;
    default:
        break;
    }
}

QString LivestreamDock::generateOutputPath()
{
    QString dir = m_outputDirEdit->text().trimmed();
    if (dir.isEmpty()) {
        dir = QDir::homePath() + "/Videos/LiveRecordings";
    }
    QDir().mkpath(dir);
    const QString timestamp = QDateTime::currentDateTime().toString("yyyyMMdd_HHmmss");
    return dir + "/live_" + timestamp + ".mp4";
}

void LivestreamDock::onHighlightDetected(const HighlightSegment& segment)
{
    m_logOutput->append(
        QString("<span style='color:#f39c12'>[%1] 检测到高光: %2s-%3s 评分:%4%</span>")
            .arg(QDateTime::currentDateTime().toString("hh:mm:ss"))
            .arg(static_cast<int>(segment.startSec))
            .arg(static_cast<int>(segment.endSec))
            .arg(static_cast<int>(segment.score * 100)));

    emit highlightFound(segment);
}

void LivestreamDock::onClipReady(const QString& filePath, const QString& title)
{
    m_logOutput->append(
        QString("<span style='color:#2ecc71'>[%1] 片段已导出: %2</span>")
            .arg(QDateTime::currentDateTime().toString("hh:mm:ss"))
            .arg(QFileInfo(filePath).fileName()));

    emit clipExported(filePath, title);
}

void LivestreamDock::populateSourceQualityOptions(const PlatformInfo& info)
{
    if (!m_sourceQualityCombo) {
        return;
    }

    const QString currentKey = m_sourceQualityCombo->currentData().toString();
    const QSignalBlocker blocker(m_sourceQualityCombo);

    m_sourceQualityCombo->clear();
    m_sourceQualityCombo->addItem(QString::fromUtf8("自动 / 最佳"), QStringLiteral("best"));

    if (info.availableQualities.isEmpty()) {
        m_sourceQualityCombo->addItem(QString::fromUtf8("原画"), QStringLiteral("origin"));
        m_sourceQualityCombo->addItem("UHD", QStringLiteral("uhd"));
        m_sourceQualityCombo->addItem("HD", QStringLiteral("hd"));
        m_sourceQualityCombo->addItem("SD", QStringLiteral("sd"));
        m_sourceQualityCombo->addItem("LD", QStringLiteral("ld"));
        m_sourceQualityCombo->addItem(QString::fromUtf8("本地源"), QStringLiteral("source"));
    } else {
        for (const QString& quality : info.availableQualities) {
            m_sourceQualityCombo->addItem(qualityLabel(quality), quality);
        }
    }

    QString preferredKey = currentKey;
    if (!info.preferredQuality.isEmpty()) {
        preferredKey = info.preferredQuality;
    }
    int index = m_sourceQualityCombo->findData(preferredKey);
    if (index < 0) {
        index = 0;
    }
    m_sourceQualityCombo->setCurrentIndex(index);
}

QString LivestreamDock::qualityLabel(const QString& qualityKey)
{
    const QString key = qualityKey.trimmed().toLower();
    if (key == QStringLiteral("best")) {
        return QString::fromUtf8("自动 / 最佳");
    }
    if (key == QStringLiteral("origin")) {
        return QString::fromUtf8("原画");
    }
    if (key == QStringLiteral("source")) {
        return QString::fromUtf8("本地源");
    }
    if (key == QStringLiteral("uhd")) {
        return QStringLiteral("UHD");
    }
    if (key == QStringLiteral("hd")) {
        return QStringLiteral("HD");
    }
    if (key == QStringLiteral("sd")) {
        return QStringLiteral("SD");
    }
    if (key == QStringLiteral("ld")) {
        return QStringLiteral("LD");
    }
    return qualityKey;
}

#undef MODULE_NAME
