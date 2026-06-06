#ifndef EXPORTSETTINGSDIALOG_H
#define EXPORTSETTINGSDIALOG_H

#include <QDialog>
#include "analyzer/ClipExporter.h"

class QLineEdit;
class QComboBox;
class QSpinBox;
class QCheckBox;
class QDoubleSpinBox;

namespace lsc {

class ExportSettingsDialog : public QDialog {
    Q_OBJECT

public:
    explicit ExportSettingsDialog(QWidget* parent = nullptr);

    ExportConfig config() const;
    void setConfig(const ExportConfig& config);

private slots:
    void onBrowseOutputDir();
    void onVerticalCropToggled(bool checked);
    void onBurnSubtitlesToggled(bool checked);
    void onCodecChanged(int index);

private:
    void setupUi();

    // 输出设置
    QLineEdit* m_outputDirEdit;
    QLineEdit* m_filenameTemplateEdit;
    QComboBox* m_formatCombo;

    // 视频设置
    QSpinBox* m_widthSpin;
    QSpinBox* m_heightSpin;
    QSpinBox* m_bitrateSpin;
    QComboBox* m_codecCombo;
    QSpinBox* m_crfSpin;

    // 竖屏裁切
    QCheckBox* m_verticalCropCheck;
    QDoubleSpinBox* m_cropXSpin;
    QDoubleSpinBox* m_cropYSpin;
    QDoubleSpinBox* m_cropWidthSpin;
    QDoubleSpinBox* m_cropHeightSpin;

    // 字幕
    QCheckBox* m_burnSubtitlesCheck;
    QLineEdit* m_subtitlePathEdit;
    QLineEdit* m_subtitleStyleEdit;

    // 封面
    QCheckBox* m_generateThumbnailCheck;
    QSpinBox* m_thumbnailTimeSpin;
    QSpinBox* m_thumbnailWidthSpin;
    QSpinBox* m_thumbnailHeightSpin;

    // 元数据
    QLineEdit* m_titleEdit;
    QLineEdit* m_descriptionEdit;
    QLineEdit* m_tagsEdit;
};

} // namespace lsc

#endif // EXPORTSETTINGSDIALOG_H
