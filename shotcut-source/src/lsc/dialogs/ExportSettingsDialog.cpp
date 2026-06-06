#include "ExportSettingsDialog.h"

#include <QVBoxLayout>
#include <QHBoxLayout>
#include <QFormLayout>
#include <QGroupBox>
#include <QLineEdit>
#include <QComboBox>
#include <QSpinBox>
#include <QCheckBox>
#include <QDoubleSpinBox>
#include <QPushButton>
#include <QLabel>
#include <QDialogButtonBox>
#include <QFileDialog>
#include <QDir>

namespace lsc {

ExportSettingsDialog::ExportSettingsDialog(QWidget* parent)
    : QDialog(parent)
{
    setWindowTitle(tr("导出设置"));
    setMinimumWidth(480);
    setupUi();
}

void ExportSettingsDialog::setupUi()
{
    auto* mainLayout = new QVBoxLayout(this);

    // --- 输出设置 ---
    auto* outputGroup = new QGroupBox(tr("输出设置"));
    auto* outputLayout = new QFormLayout(outputGroup);

    auto* dirLayout = new QHBoxLayout();
    m_outputDirEdit = new QLineEdit(QDir::homePath() + "/Videos/LiveClips");
    auto* browseBtn = new QPushButton(tr("浏览..."));
    connect(browseBtn, &QPushButton::clicked, this, &ExportSettingsDialog::onBrowseOutputDir);
    dirLayout->addWidget(m_outputDirEdit);
    dirLayout->addWidget(browseBtn);
    outputLayout->addRow(tr("输出目录:"), dirLayout);

    m_filenameTemplateEdit = new QLineEdit(QStringLiteral("{streamer}_{date}_{index}"));
    m_filenameTemplateEdit->setToolTip(tr("可用变量: {streamer}, {date}, {index}, {title}"));
    outputLayout->addRow(tr("文件名模板:"), m_filenameTemplateEdit);

    m_formatCombo = new QComboBox();
    m_formatCombo->addItems({QStringLiteral("mp4"), QStringLiteral("mkv"), QStringLiteral("webm")});
    outputLayout->addRow(tr("格式:"), m_formatCombo);

    mainLayout->addWidget(outputGroup);

    // --- 视频设置 ---
    auto* videoGroup = new QGroupBox(tr("视频设置"));
    auto* videoLayout = new QFormLayout(videoGroup);

    auto* resLayout = new QHBoxLayout();
    m_widthSpin = new QSpinBox();
    m_widthSpin->setRange(0, 7680);
    m_widthSpin->setValue(0);
    m_widthSpin->setSpecialValueText(tr("原始"));
    m_heightSpin = new QSpinBox();
    m_heightSpin->setRange(0, 4320);
    m_heightSpin->setValue(0);
    m_heightSpin->setSpecialValueText(tr("原始"));
    resLayout->addWidget(m_widthSpin);
    resLayout->addWidget(new QLabel(QStringLiteral("x")));
    resLayout->addWidget(m_heightSpin);
    videoLayout->addRow(tr("分辨率:"), resLayout);

    m_codecCombo = new QComboBox();
    m_codecCombo->addItems({QStringLiteral("copy"), QStringLiteral("h264"), QStringLiteral("h265")});
    connect(m_codecCombo, QOverload<int>::of(&QComboBox::currentIndexChanged),
            this, &ExportSettingsDialog::onCodecChanged);
    videoLayout->addRow(tr("编码:"), m_codecCombo);

    m_crfSpin = new QSpinBox();
    m_crfSpin->setRange(0, 51);
    m_crfSpin->setValue(23);
    m_crfSpin->setEnabled(false);
    videoLayout->addRow(tr("CRF:"), m_crfSpin);

    m_bitrateSpin = new QSpinBox();
    m_bitrateSpin->setRange(0, 100000);
    m_bitrateSpin->setValue(0);
    m_bitrateSpin->setSuffix(QStringLiteral(" kbps"));
    m_bitrateSpin->setSpecialValueText(tr("自动"));
    m_bitrateSpin->setEnabled(false);
    videoLayout->addRow(tr("码率:"), m_bitrateSpin);

    mainLayout->addWidget(videoGroup);

    // --- 竖屏裁切 ---
    auto* cropGroup = new QGroupBox(tr("竖屏裁切"));
    auto* cropLayout = new QFormLayout(cropGroup);

    m_verticalCropCheck = new QCheckBox(tr("启用竖屏裁切"));
    connect(m_verticalCropCheck, &QCheckBox::toggled, this, &ExportSettingsDialog::onVerticalCropToggled);
    cropLayout->addRow(m_verticalCropCheck);

    m_cropXSpin = new QDoubleSpinBox();
    m_cropXSpin->setRange(0.0, 1.0);
    m_cropXSpin->setSingleStep(0.05);
    m_cropXSpin->setValue(0.1);
    m_cropXSpin->setEnabled(false);
    cropLayout->addRow(tr("X 偏移:"), m_cropXSpin);

    m_cropYSpin = new QDoubleSpinBox();
    m_cropYSpin->setRange(0.0, 1.0);
    m_cropYSpin->setSingleStep(0.05);
    m_cropYSpin->setValue(0.0);
    m_cropYSpin->setEnabled(false);
    cropLayout->addRow(tr("Y 偏移:"), m_cropYSpin);

    m_cropWidthSpin = new QDoubleSpinBox();
    m_cropWidthSpin->setRange(0.1, 1.0);
    m_cropWidthSpin->setSingleStep(0.05);
    m_cropWidthSpin->setValue(0.8);
    m_cropWidthSpin->setEnabled(false);
    cropLayout->addRow(tr("宽度比例:"), m_cropWidthSpin);

    m_cropHeightSpin = new QDoubleSpinBox();
    m_cropHeightSpin->setRange(0.1, 1.0);
    m_cropHeightSpin->setSingleStep(0.05);
    m_cropHeightSpin->setValue(1.0);
    m_cropHeightSpin->setEnabled(false);
    cropLayout->addRow(tr("高度比例:"), m_cropHeightSpin);

    mainLayout->addWidget(cropGroup);

    // --- 字幕 ---
    auto* subGroup = new QGroupBox(tr("字幕"));
    auto* subLayout = new QFormLayout(subGroup);

    m_burnSubtitlesCheck = new QCheckBox(tr("烧录字幕"));
    connect(m_burnSubtitlesCheck, &QCheckBox::toggled, this, &ExportSettingsDialog::onBurnSubtitlesToggled);
    subLayout->addRow(m_burnSubtitlesCheck);

    m_subtitlePathEdit = new QLineEdit();
    m_subtitlePathEdit->setEnabled(false);
    m_subtitlePathEdit->setPlaceholderText(tr("SRT/ASS 字幕文件路径"));
    subLayout->addRow(tr("字幕文件:"), m_subtitlePathEdit);

    m_subtitleStyleEdit = new QLineEdit();
    m_subtitleStyleEdit->setEnabled(false);
    m_subtitleStyleEdit->setPlaceholderText(tr("可选，ASS 样式覆盖"));
    subLayout->addRow(tr("样式:"), m_subtitleStyleEdit);

    mainLayout->addWidget(subGroup);

    // --- 封面 ---
    auto* thumbGroup = new QGroupBox(tr("封面"));
    auto* thumbLayout = new QFormLayout(thumbGroup);

    m_generateThumbnailCheck = new QCheckBox(tr("生成封面缩略图"));
    m_generateThumbnailCheck->setChecked(true);
    thumbLayout->addRow(m_generateThumbnailCheck);

    m_thumbnailTimeSpin = new QSpinBox();
    m_thumbnailTimeSpin->setRange(0, 86400);
    m_thumbnailTimeSpin->setValue(0);
    m_thumbnailTimeSpin->setSpecialValueText(tr("视频中间"));
    m_thumbnailTimeSpin->setSuffix(tr(" 秒"));
    thumbLayout->addRow(tr("截图时间:"), m_thumbnailTimeSpin);

    auto* thumbResLayout = new QHBoxLayout();
    m_thumbnailWidthSpin = new QSpinBox();
    m_thumbnailWidthSpin->setRange(64, 3840);
    m_thumbnailWidthSpin->setValue(1280);
    m_thumbnailHeightSpin = new QSpinBox();
    m_thumbnailHeightSpin->setRange(64, 2160);
    m_thumbnailHeightSpin->setValue(720);
    thumbResLayout->addWidget(m_thumbnailWidthSpin);
    thumbResLayout->addWidget(new QLabel(QStringLiteral("x")));
    thumbResLayout->addWidget(m_thumbnailHeightSpin);
    thumbLayout->addRow(tr("封面分辨率:"), thumbResLayout);

    mainLayout->addWidget(thumbGroup);

    // --- 元数据 ---
    auto* metaGroup = new QGroupBox(tr("元数据"));
    auto* metaLayout = new QFormLayout(metaGroup);

    m_titleEdit = new QLineEdit();
    m_titleEdit->setPlaceholderText(tr("视频标题"));
    metaLayout->addRow(tr("标题:"), m_titleEdit);

    m_descriptionEdit = new QLineEdit();
    m_descriptionEdit->setPlaceholderText(tr("视频描述"));
    metaLayout->addRow(tr("描述:"), m_descriptionEdit);

    m_tagsEdit = new QLineEdit();
    m_tagsEdit->setPlaceholderText(tr("逗号分隔的标签"));
    metaLayout->addRow(tr("标签:"), m_tagsEdit);

    mainLayout->addWidget(metaGroup);

    // --- 对话框按钮 ---
    auto* buttonBox = new QDialogButtonBox(QDialogButtonBox::Ok | QDialogButtonBox::Cancel);
    connect(buttonBox, &QDialogButtonBox::accepted, this, &QDialog::accept);
    connect(buttonBox, &QDialogButtonBox::rejected, this, &QDialog::reject);
    mainLayout->addWidget(buttonBox);
}

ExportConfig ExportSettingsDialog::config() const
{
    ExportConfig cfg;
    cfg.outputDir = m_outputDirEdit->text();
    cfg.filenameTemplate = m_filenameTemplateEdit->text();
    cfg.format = m_formatCombo->currentText();

    cfg.width = m_widthSpin->value();
    cfg.height = m_heightSpin->value();
    cfg.bitrate = m_bitrateSpin->value();
    cfg.codec = m_codecCombo->currentText();
    cfg.crf = m_crfSpin->value();

    cfg.verticalCrop = m_verticalCropCheck->isChecked();
    cfg.cropX = m_cropXSpin->value();
    cfg.cropY = m_cropYSpin->value();
    cfg.cropWidth = m_cropWidthSpin->value();
    cfg.cropHeight = m_cropHeightSpin->value();

    cfg.burnSubtitles = m_burnSubtitlesCheck->isChecked();
    cfg.subtitlePath = m_subtitlePathEdit->text();
    cfg.subtitleStyle = m_subtitleStyleEdit->text();

    cfg.generateThumbnail = m_generateThumbnailCheck->isChecked();
    cfg.thumbnailTimeSec = m_thumbnailTimeSpin->value();
    cfg.thumbnailWidth = m_thumbnailWidthSpin->value();
    cfg.thumbnailHeight = m_thumbnailHeightSpin->value();

    cfg.title = m_titleEdit->text();
    cfg.description = m_descriptionEdit->text();
    const QString tagsStr = m_tagsEdit->text();
    if (!tagsStr.isEmpty()) {
        cfg.tags = tagsStr.split(QStringLiteral(","), Qt::SkipEmptyParts);
        for (QString& tag : cfg.tags) tag = tag.trimmed();
    }

    return cfg;
}

void ExportSettingsDialog::setConfig(const ExportConfig& cfg)
{
    m_outputDirEdit->setText(cfg.outputDir);
    m_filenameTemplateEdit->setText(cfg.filenameTemplate);
    m_formatCombo->setCurrentText(cfg.format);

    m_widthSpin->setValue(cfg.width);
    m_heightSpin->setValue(cfg.height);
    m_bitrateSpin->setValue(cfg.bitrate);
    m_codecCombo->setCurrentText(cfg.codec);
    m_crfSpin->setValue(cfg.crf);

    m_verticalCropCheck->setChecked(cfg.verticalCrop);
    m_cropXSpin->setValue(cfg.cropX);
    m_cropYSpin->setValue(cfg.cropY);
    m_cropWidthSpin->setValue(cfg.cropWidth);
    m_cropHeightSpin->setValue(cfg.cropHeight);

    m_burnSubtitlesCheck->setChecked(cfg.burnSubtitles);
    m_subtitlePathEdit->setText(cfg.subtitlePath);
    m_subtitleStyleEdit->setText(cfg.subtitleStyle);

    m_generateThumbnailCheck->setChecked(cfg.generateThumbnail);
    m_thumbnailTimeSpin->setValue(cfg.thumbnailTimeSec);
    m_thumbnailWidthSpin->setValue(cfg.thumbnailWidth);
    m_thumbnailHeightSpin->setValue(cfg.thumbnailHeight);

    m_titleEdit->setText(cfg.title);
    m_descriptionEdit->setText(cfg.description);
    m_tagsEdit->setText(cfg.tags.join(QStringLiteral(",")));
}

void ExportSettingsDialog::onBrowseOutputDir()
{
    const QString dir = QFileDialog::getExistingDirectory(this, tr("选择输出目录"),
                                                          m_outputDirEdit->text());
    if (!dir.isEmpty()) {
        m_outputDirEdit->setText(dir);
    }
}

void ExportSettingsDialog::onVerticalCropToggled(bool checked)
{
    m_cropXSpin->setEnabled(checked);
    m_cropYSpin->setEnabled(checked);
    m_cropWidthSpin->setEnabled(checked);
    m_cropHeightSpin->setEnabled(checked);
}

void ExportSettingsDialog::onBurnSubtitlesToggled(bool checked)
{
    m_subtitlePathEdit->setEnabled(checked);
    m_subtitleStyleEdit->setEnabled(checked);
}

void ExportSettingsDialog::onCodecChanged(int index)
{
    const bool isCopy = (index == 0);
    m_crfSpin->setEnabled(!isCopy);
    m_bitrateSpin->setEnabled(!isCopy);
}

} // namespace lsc
