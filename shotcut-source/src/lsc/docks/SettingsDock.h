#ifndef SETTINGSDOCK_H
#define SETTINGSDOCK_H

#include <QDockWidget>

class QTabWidget;
class QComboBox;
class QSlider;
class QSpinBox;
class QCheckBox;
class QLineEdit;

namespace lsc {

class SettingsDock : public QDockWidget {
    Q_OBJECT

public:
    explicit SettingsDock(QWidget* parent = nullptr);

signals:
    void settingsChanged();

private slots:
    void onSettingChanged();
    void onResetDefaults();
    void onImportSettings();
    void onExportSettings();

private:
    void setupUi();
    void loadSettings();
    void saveSettings();

    // 分析模式
    QComboBox* m_analysisProfileCombo;
    QComboBox* m_gameTypeCombo;
    QSlider* m_sensitivitySlider;
    QSpinBox* m_minClipLengthSpin;
    QSpinBox* m_maxClipLengthSpin;

    // 录制设置
    QComboBox* m_defaultQualityCombo;
    QComboBox* m_defaultFormatCombo;
    QCheckBox* m_autoAnalyzeCheck;
    QCheckBox* m_enableASRCheck;

    // 导出设置
    QLineEdit* m_defaultOutputDirEdit;
    QLineEdit* m_filenameTemplateEdit;
    QComboBox* m_defaultResolutionCombo;

    // Whisper 设置
    QComboBox* m_whisperModelCombo;
    QComboBox* m_whisperLanguageCombo;
};

} // namespace lsc

#endif // SETTINGSDOCK_H
