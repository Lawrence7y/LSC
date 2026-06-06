#include "SettingsDock.h"
#include "LscConfig.h"

#include <QCheckBox>
#include <QComboBox>
#include <QDir>
#include <QFileDialog>
#include <QFormLayout>
#include <QGroupBox>
#include <QHBoxLayout>
#include <QJsonDocument>
#include <QJsonObject>
#include <QLabel>
#include <QLineEdit>
#include <QMessageBox>
#include <QPushButton>
#include <QScrollArea>
#include <QSlider>
#include <QSpinBox>
#include <QTabWidget>
#include <QVBoxLayout>

namespace lsc {

SettingsDock::SettingsDock(QWidget* parent)
    : QDockWidget(QString::fromUtf8("设置"), parent)
{
    setupUi();
    loadSettings();
}

void SettingsDock::setupUi()
{
    QScrollArea* scrollArea = new QScrollArea(this);
    scrollArea->setWidgetResizable(true);
    scrollArea->setFrameShape(QFrame::NoFrame);
    scrollArea->setHorizontalScrollBarPolicy(Qt::ScrollBarAlwaysOff);

    QWidget* mainWidget = new QWidget(scrollArea);
    QVBoxLayout* mainLayout = new QVBoxLayout(mainWidget);

    QTabWidget* tabWidget = new QTabWidget(mainWidget);

    // ===== 分析模式 Tab =====
    QWidget* analysisTab = new QWidget(tabWidget);
    QFormLayout* analysisLayout = new QFormLayout(analysisTab);

    m_analysisProfileCombo = new QComboBox(analysisTab);
    m_analysisProfileCombo->setObjectName(QStringLiteral("analysisProfileCombo"));
    m_analysisProfileCombo->addItem(QString::fromUtf8("通用直播"), QStringLiteral("generic"));
    m_analysisProfileCombo->addItem(QString::fromUtf8("游戏高光"), QStringLiteral("game"));
    m_analysisProfileCombo->addItem(QString::fromUtf8("舞蹈卡点"), QStringLiteral("dance"));
    m_analysisProfileCombo->addItem(QString::fromUtf8("解说切片"), QStringLiteral("commentary"));
    connect(m_analysisProfileCombo, QOverload<int>::of(&QComboBox::currentIndexChanged),
            this, &SettingsDock::onSettingChanged);
    analysisLayout->addRow(QString::fromUtf8("分析配置:"), m_analysisProfileCombo);

    m_gameTypeCombo = new QComboBox(analysisTab);
    m_gameTypeCombo->setObjectName(QStringLiteral("gameTypeCombo"));
    m_gameTypeCombo->addItem(QString::fromUtf8("无畏契约"), QStringLiteral("valorant"));
    m_gameTypeCombo->addItem(QString::fromUtf8("通用"), QStringLiteral("generic"));
    connect(m_gameTypeCombo, QOverload<int>::of(&QComboBox::currentIndexChanged),
            this, &SettingsDock::onSettingChanged);
    analysisLayout->addRow(QString::fromUtf8("游戏类型:"), m_gameTypeCombo);

    m_sensitivitySlider = new QSlider(Qt::Horizontal, analysisTab);
    m_sensitivitySlider->setObjectName(QStringLiteral("sensitivitySlider"));
    m_sensitivitySlider->setRange(1, 10);
    m_sensitivitySlider->setValue(5);
    connect(m_sensitivitySlider, &QSlider::valueChanged, this, &SettingsDock::onSettingChanged);
    analysisLayout->addRow(QString::fromUtf8("灵敏度:"), m_sensitivitySlider);

    m_minClipLengthSpin = new QSpinBox(analysisTab);
    m_minClipLengthSpin->setObjectName(QStringLiteral("minClipLengthSpin"));
    m_minClipLengthSpin->setRange(1, 120);
    m_minClipLengthSpin->setValue(15);
    m_minClipLengthSpin->setSuffix(QString::fromUtf8(" 秒"));
    connect(m_minClipLengthSpin, QOverload<int>::of(&QSpinBox::valueChanged),
            this, &SettingsDock::onSettingChanged);
    analysisLayout->addRow(QString::fromUtf8("最短片段:"), m_minClipLengthSpin);

    m_maxClipLengthSpin = new QSpinBox(analysisTab);
    m_maxClipLengthSpin->setObjectName(QStringLiteral("maxClipLengthSpin"));
    m_maxClipLengthSpin->setRange(10, 600);
    m_maxClipLengthSpin->setValue(45);
    m_maxClipLengthSpin->setSuffix(QString::fromUtf8(" 秒"));
    connect(m_maxClipLengthSpin, QOverload<int>::of(&QSpinBox::valueChanged),
            this, &SettingsDock::onSettingChanged);
    analysisLayout->addRow(QString::fromUtf8("最长片段:"), m_maxClipLengthSpin);

    tabWidget->addTab(analysisTab, QString::fromUtf8("分析模式"));

    // ===== 录制设置 Tab =====
    QWidget* recordingTab = new QWidget(tabWidget);
    QFormLayout* recordingLayout = new QFormLayout(recordingTab);

    m_defaultQualityCombo = new QComboBox(recordingTab);
    m_defaultQualityCombo->setObjectName(QStringLiteral("defaultQualityCombo"));
    m_defaultQualityCombo->addItem(QString::fromUtf8("自动 / 最佳"), QStringLiteral("best"));
    m_defaultQualityCombo->addItem(QString::fromUtf8("原画"), QStringLiteral("origin"));
    m_defaultQualityCombo->addItem("HD", QStringLiteral("hd"));
    m_defaultQualityCombo->addItem("SD", QStringLiteral("sd"));
    m_defaultQualityCombo->addItem("LD", QStringLiteral("ld"));
    connect(m_defaultQualityCombo, QOverload<int>::of(&QComboBox::currentIndexChanged),
            this, &SettingsDock::onSettingChanged);
    recordingLayout->addRow(QString::fromUtf8("默认质量:"), m_defaultQualityCombo);

    m_defaultFormatCombo = new QComboBox(recordingTab);
    m_defaultFormatCombo->setObjectName(QStringLiteral("defaultFormatCombo"));
    m_defaultFormatCombo->addItem(QStringLiteral("MP4"), QStringLiteral("mp4"));
    m_defaultFormatCombo->addItem(QStringLiteral("MKV"), QStringLiteral("mkv"));
    m_defaultFormatCombo->addItem(QStringLiteral("FLV"), QStringLiteral("flv"));
    m_defaultFormatCombo->addItem(QStringLiteral("TS"), QStringLiteral("ts"));
    connect(m_defaultFormatCombo, QOverload<int>::of(&QComboBox::currentIndexChanged),
            this, &SettingsDock::onSettingChanged);
    recordingLayout->addRow(QString::fromUtf8("默认格式:"), m_defaultFormatCombo);

    m_autoAnalyzeCheck = new QCheckBox(QString::fromUtf8("录制完成后自动分析"), recordingTab);
    m_autoAnalyzeCheck->setObjectName(QStringLiteral("autoAnalyzeCheck"));
    connect(m_autoAnalyzeCheck, &QCheckBox::toggled, this, &SettingsDock::onSettingChanged);
    recordingLayout->addRow(m_autoAnalyzeCheck);

    m_enableASRCheck = new QCheckBox(QString::fromUtf8("启用语音识别 (ASR)"), recordingTab);
    m_enableASRCheck->setObjectName(QStringLiteral("enableASRCheck"));
    connect(m_enableASRCheck, &QCheckBox::toggled, this, &SettingsDock::onSettingChanged);
    recordingLayout->addRow(m_enableASRCheck);

    tabWidget->addTab(recordingTab, QString::fromUtf8("录制设置"));

    // ===== 导出设置 Tab =====
    QWidget* exportTab = new QWidget(tabWidget);
    QFormLayout* exportLayout = new QFormLayout(exportTab);

    QHBoxLayout* outputDirLayout = new QHBoxLayout();
    m_defaultOutputDirEdit = new QLineEdit(exportTab);
    m_defaultOutputDirEdit->setObjectName(QStringLiteral("defaultOutputDirEdit"));
    m_defaultOutputDirEdit->setPlaceholderText(QString::fromUtf8("默认输出目录"));
    outputDirLayout->addWidget(m_defaultOutputDirEdit, 1);
    QPushButton* browseBtn = new QPushButton(QString::fromUtf8("浏览..."), exportTab);
    connect(browseBtn, &QPushButton::clicked, this, [this]() {
        QString dir = QFileDialog::getExistingDirectory(
            this, QString::fromUtf8("选择默认输出目录"),
            m_defaultOutputDirEdit->text());
        if (!dir.isEmpty()) {
            m_defaultOutputDirEdit->setText(dir);
            onSettingChanged();
        }
    });
    outputDirLayout->addWidget(browseBtn);
    exportLayout->addRow(QString::fromUtf8("输出目录:"), outputDirLayout);

    m_filenameTemplateEdit = new QLineEdit(exportTab);
    m_filenameTemplateEdit->setObjectName(QStringLiteral("filenameTemplateEdit"));
    m_filenameTemplateEdit->setPlaceholderText(QString::fromUtf8("{source}_{index}_{time}"));
    connect(m_filenameTemplateEdit, &QLineEdit::textChanged, this, &SettingsDock::onSettingChanged);
    exportLayout->addRow(QString::fromUtf8("文件名模板:"), m_filenameTemplateEdit);

    m_defaultResolutionCombo = new QComboBox(exportTab);
    m_defaultResolutionCombo->setObjectName(QStringLiteral("defaultResolutionCombo"));
    m_defaultResolutionCombo->addItem(QString::fromUtf8("原始分辨率"), QStringLiteral("original"));
    m_defaultResolutionCombo->addItem(QStringLiteral("1080p"), QStringLiteral("1080p"));
    m_defaultResolutionCombo->addItem(QStringLiteral("720p"), QStringLiteral("720p"));
    m_defaultResolutionCombo->addItem(QStringLiteral("480p"), QStringLiteral("480p"));
    connect(m_defaultResolutionCombo, QOverload<int>::of(&QComboBox::currentIndexChanged),
            this, &SettingsDock::onSettingChanged);
    exportLayout->addRow(QString::fromUtf8("默认分辨率:"), m_defaultResolutionCombo);

    tabWidget->addTab(exportTab, QString::fromUtf8("导出设置"));

    // ===== Whisper 设置 Tab =====
    QWidget* whisperTab = new QWidget(tabWidget);
    QFormLayout* whisperLayout = new QFormLayout(whisperTab);

    m_whisperModelCombo = new QComboBox(whisperTab);
    m_whisperModelCombo->setObjectName(QStringLiteral("whisperModelCombo"));
    m_whisperModelCombo->addItem(QString::fromUtf8("Base"), QStringLiteral("models/ggml-base.bin"));
    m_whisperModelCombo->addItem(QString::fromUtf8("Small"), QStringLiteral("models/ggml-small.bin"));
    m_whisperModelCombo->addItem(QString::fromUtf8("Medium"), QStringLiteral("models/ggml-medium.bin"));
    m_whisperModelCombo->addItem(QString::fromUtf8("Large"), QStringLiteral("models/ggml-large.bin"));
    connect(m_whisperModelCombo, QOverload<int>::of(&QComboBox::currentIndexChanged),
            this, &SettingsDock::onSettingChanged);
    whisperLayout->addRow(QString::fromUtf8("模型:"), m_whisperModelCombo);

    m_whisperLanguageCombo = new QComboBox(whisperTab);
    m_whisperLanguageCombo->setObjectName(QStringLiteral("whisperLanguageCombo"));
    m_whisperLanguageCombo->addItem(QString::fromUtf8("中文"), QStringLiteral("zh"));
    m_whisperLanguageCombo->addItem(QString::fromUtf8("英文"), QStringLiteral("en"));
    m_whisperLanguageCombo->addItem(QString::fromUtf8("日文"), QStringLiteral("ja"));
    m_whisperLanguageCombo->addItem(QString::fromUtf8("自动"), QStringLiteral("auto"));
    connect(m_whisperLanguageCombo, QOverload<int>::of(&QComboBox::currentIndexChanged),
            this, &SettingsDock::onSettingChanged);
    whisperLayout->addRow(QString::fromUtf8("语言:"), m_whisperLanguageCombo);

    tabWidget->addTab(whisperTab, QString::fromUtf8("Whisper"));

    mainLayout->addWidget(tabWidget);

    // 操作按钮
    QHBoxLayout* btnLayout = new QHBoxLayout();
    QPushButton* resetBtn = new QPushButton(QString::fromUtf8("恢复默认"), mainWidget);
    connect(resetBtn, &QPushButton::clicked, this, &SettingsDock::onResetDefaults);
    btnLayout->addWidget(resetBtn);

    QPushButton* importBtn = new QPushButton(QString::fromUtf8("导入"), mainWidget);
    connect(importBtn, &QPushButton::clicked, this, &SettingsDock::onImportSettings);
    btnLayout->addWidget(importBtn);

    QPushButton* exportBtn = new QPushButton(QString::fromUtf8("导出"), mainWidget);
    connect(exportBtn, &QPushButton::clicked, this, &SettingsDock::onExportSettings);
    btnLayout->addWidget(exportBtn);

    mainLayout->addLayout(btnLayout);

    scrollArea->setWidget(mainWidget);
    setWidget(scrollArea);
}

void SettingsDock::loadSettings()
{
    auto& cfg = LscConfig::instance();

    // 分析模式
    const int profileIndex = m_analysisProfileCombo->findData(cfg.defaultAnalysisProfile);
    m_analysisProfileCombo->setCurrentIndex(profileIndex >= 0 ? profileIndex : 0);
    const int gameIndex = m_gameTypeCombo->findData(cfg.defaultGameKey);
    m_gameTypeCombo->setCurrentIndex(gameIndex >= 0 ? gameIndex : 0);
    m_sensitivitySlider->setValue(5);
    m_minClipLengthSpin->setValue(static_cast<int>(cfg.shortClipMinSec));
    m_maxClipLengthSpin->setValue(static_cast<int>(cfg.shortClipMaxSec));

    // 录制设置
    const int formatIndex = m_defaultFormatCombo->findData(cfg.defaultFormat);
    if (formatIndex >= 0) {
        m_defaultFormatCombo->setCurrentIndex(formatIndex);
    }
    const int qualityIndex = m_defaultQualityCombo->findData(cfg.defaultSourceQuality);
    m_defaultQualityCombo->setCurrentIndex(qualityIndex >= 0 ? qualityIndex : 0);
    m_autoAnalyzeCheck->setChecked(cfg.defaultAutoAnalyze);
    m_enableASRCheck->setChecked(cfg.defaultEnableASR);

    // 导出设置
    m_defaultOutputDirEdit->setText(
        QDir::home().filePath(cfg.defaultOutputSubdir));
    m_filenameTemplateEdit->setText(cfg.exportFilenameTemplate);
    const int resolutionIndex = m_defaultResolutionCombo->findData(cfg.defaultExportResolution);
    m_defaultResolutionCombo->setCurrentIndex(resolutionIndex >= 0 ? resolutionIndex : 0);

    // Whisper
    const int modelIndex = m_whisperModelCombo->findData(cfg.whisperDefaultModel);
    if (modelIndex >= 0) {
        m_whisperModelCombo->setCurrentIndex(modelIndex);
    }
    const int langIndex = m_whisperLanguageCombo->findData(cfg.whisperDefaultLanguage);
    if (langIndex >= 0) {
        m_whisperLanguageCombo->setCurrentIndex(langIndex);
    }
}

void SettingsDock::saveSettings()
{
    auto& cfg = LscConfig::instance();

    // 分析模式
    cfg.defaultAnalysisProfile = m_analysisProfileCombo->currentData().toString();
    cfg.defaultGameKey = m_gameTypeCombo->currentData().toString();
    cfg.shortClipMinSec = m_minClipLengthSpin->value();
    cfg.shortClipMaxSec = m_maxClipLengthSpin->value();

    // 录制设置
    cfg.defaultSourceQuality = m_defaultQualityCombo->currentData().toString();
    cfg.defaultFormat = m_defaultFormatCombo->currentData().toString();
    cfg.defaultAutoAnalyze = m_autoAnalyzeCheck->isChecked();
    cfg.defaultEnableASR = m_enableASRCheck->isChecked();

    // 导出设置
    cfg.defaultOutputSubdir = m_defaultOutputDirEdit->text();
    cfg.exportFilenameTemplate = m_filenameTemplateEdit->text();
    cfg.defaultExportResolution = m_defaultResolutionCombo->currentData().toString();

    // Whisper
    cfg.whisperDefaultModel = m_whisperModelCombo->currentData().toString();
    cfg.whisperDefaultLanguage = m_whisperLanguageCombo->currentData().toString();
}

void SettingsDock::onSettingChanged()
{
    saveSettings();
    emit settingsChanged();
}

void SettingsDock::onResetDefaults()
{
    auto& cfg = LscConfig::instance();

    cfg.silenceThresholdDb = -50.0;
    cfg.minSilenceDurationSec = 0.5;
    cfg.sceneChangeThreshold = 0.1;
    cfg.motionThreshold = 0.15;
    cfg.highlightThreshold = 0.2;
    cfg.defaultAnalysisProfile = "generic";
    cfg.defaultGameKey = "valorant";
    cfg.defaultSourceQuality = "best";
    cfg.defaultAutoAnalyze = true;
    cfg.defaultEnableASR = false;
    cfg.shortClipMinSec = 15.0;
    cfg.shortClipMaxSec = 45.0;
    cfg.defaultFormat = "mp4";
    cfg.defaultOutputSubdir = "Videos/LiveRecordings";
    cfg.whisperDefaultModel = "models/ggml-base.bin";
    cfg.whisperDefaultLanguage = "zh";
    cfg.exportFilenameTemplate = "{source}_{index}_{time}";
    cfg.defaultExportResolution = "original";

    loadSettings();
    emit settingsChanged();
}

void SettingsDock::onImportSettings()
{
    QString filePath = QFileDialog::getOpenFileName(
        this, QString::fromUtf8("导入设置"),
        QDir::homePath(),
        QString::fromUtf8("JSON 文件 (*.json)"));
    if (filePath.isEmpty()) {
        return;
    }

    QFile file(filePath);
    if (!file.open(QIODevice::ReadOnly)) {
        QMessageBox::warning(this, QString::fromUtf8("导入失败"),
                             QString::fromUtf8("无法打开文件: %1").arg(filePath));
        return;
    }

    const QByteArray data = file.readAll();
    const QJsonDocument doc = QJsonDocument::fromJson(data);
    if (!doc.isObject()) {
        QMessageBox::warning(this, QString::fromUtf8("导入失败"),
                             QString::fromUtf8("文件格式不正确"));
        return;
    }

    auto& cfg = LscConfig::instance();
    const QJsonObject obj = doc.object();

    if (obj.contains("shortClipMinSec"))
        cfg.shortClipMinSec = obj["shortClipMinSec"].toDouble();
    if (obj.contains("shortClipMaxSec"))
        cfg.shortClipMaxSec = obj["shortClipMaxSec"].toDouble();
    if (obj.contains("defaultFormat"))
        cfg.defaultFormat = obj["defaultFormat"].toString();
    if (obj.contains("defaultSourceQuality"))
        cfg.defaultSourceQuality = obj["defaultSourceQuality"].toString();
    if (obj.contains("defaultAnalysisProfile"))
        cfg.defaultAnalysisProfile = obj["defaultAnalysisProfile"].toString();
    if (obj.contains("defaultGameKey"))
        cfg.defaultGameKey = obj["defaultGameKey"].toString();
    if (obj.contains("defaultAutoAnalyze"))
        cfg.defaultAutoAnalyze = obj["defaultAutoAnalyze"].toBool();
    if (obj.contains("defaultEnableASR"))
        cfg.defaultEnableASR = obj["defaultEnableASR"].toBool();
    if (obj.contains("defaultOutputSubdir"))
        cfg.defaultOutputSubdir = obj["defaultOutputSubdir"].toString();
    if (obj.contains("whisperDefaultModel"))
        cfg.whisperDefaultModel = obj["whisperDefaultModel"].toString();
    if (obj.contains("whisperDefaultLanguage"))
        cfg.whisperDefaultLanguage = obj["whisperDefaultLanguage"].toString();
    if (obj.contains("exportFilenameTemplate"))
        cfg.exportFilenameTemplate = obj["exportFilenameTemplate"].toString();
    if (obj.contains("defaultExportResolution"))
        cfg.defaultExportResolution = obj["defaultExportResolution"].toString();

    loadSettings();
    emit settingsChanged();

    QMessageBox::information(this, QString::fromUtf8("导入成功"),
                             QString::fromUtf8("设置已从文件导入"));
}

void SettingsDock::onExportSettings()
{
    QString filePath = QFileDialog::getSaveFileName(
        this, QString::fromUtf8("导出设置"),
        QDir::home().filePath("lsc_settings.json"),
        QString::fromUtf8("JSON 文件 (*.json)"));
    if (filePath.isEmpty()) {
        return;
    }

    auto& cfg = LscConfig::instance();
    QJsonObject obj;
    obj["shortClipMinSec"] = cfg.shortClipMinSec;
    obj["shortClipMaxSec"] = cfg.shortClipMaxSec;
    obj["defaultFormat"] = cfg.defaultFormat;
    obj["defaultSourceQuality"] = cfg.defaultSourceQuality;
    obj["defaultAnalysisProfile"] = cfg.defaultAnalysisProfile;
    obj["defaultGameKey"] = cfg.defaultGameKey;
    obj["defaultAutoAnalyze"] = cfg.defaultAutoAnalyze;
    obj["defaultEnableASR"] = cfg.defaultEnableASR;
    obj["defaultOutputSubdir"] = cfg.defaultOutputSubdir;
    obj["whisperDefaultModel"] = cfg.whisperDefaultModel;
    obj["whisperDefaultLanguage"] = cfg.whisperDefaultLanguage;
    obj["exportFilenameTemplate"] = cfg.exportFilenameTemplate;
    obj["defaultExportResolution"] = cfg.defaultExportResolution;

    QJsonDocument doc(obj);
    QFile file(filePath);
    if (!file.open(QIODevice::WriteOnly)) {
        QMessageBox::warning(this, QString::fromUtf8("导出失败"),
                             QString::fromUtf8("无法写入文件: %1").arg(filePath));
        return;
    }

    file.write(doc.toJson(QJsonDocument::Indented));
    QMessageBox::information(this, QString::fromUtf8("导出成功"),
                             QString::fromUtf8("设置已导出到: %1").arg(filePath));
}

} // namespace lsc
