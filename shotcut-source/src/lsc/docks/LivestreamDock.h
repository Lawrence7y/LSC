#ifndef LIVESTREAMDOCK_H
#define LIVESTREAMDOCK_H

#include <QCheckBox>
#include <QComboBox>
#include <QDockWidget>
#include <QLabel>
#include <QLineEdit>
#include <QProgressBar>
#include <QPushButton>
#include <QSlider>
#include <QSpinBox>
#include <QTextEdit>

#include "analyzer/IHighlightStrategy.h"
#include "analyzer/AnalysisProfile.h"
#include "livestream/RecordingSession.h"
#include "livestream/GameplayDetector.h"

class PreviewController;
class LivePreviewWidget;
class ClipExporter;

class LivestreamDock : public QDockWidget
{
    Q_OBJECT
public:
    explicit LivestreamDock(QWidget* parent = nullptr);
    ~LivestreamDock();

    RecordingSession* session() const { return m_session; }
    void applyRuntimeConfig();

signals:
    void recordingFinished(const QString& filePath);
    void highlightFound(const HighlightSegment& segment);
    void clipExported(const QString& filePath, const QString& title);

private slots:
    void onStartStopClicked();
    void onRecordingStarted(const QString& path);
    void onRecordingStopped(const QString& path, qint64 size);
    void onError(const QString& error);
    void onProgress(qint64 durationMs, qint64 fileSizeBytes);
    void onPlatformParsed(const PlatformInfo& info);
    void onReconnecting(int attempt, int maxAttempts);
    void onStatusChanged(RecordingStatus status);
    void onEncodeModeChanged(int index);
    void onCrfSliderChanged(int value);
    void onHighlightDetected(const HighlightSegment& segment);
    void onClipReady(const QString& filePath, const QString& title);

private:
    void setupUi();
    RecordingConfig buildConfigFromUI();
    AnalysisProfile buildAnalysisProfileFromUi() const;
    QString generateOutputPath();
    void populateSourceQualityOptions(const PlatformInfo& info);
    static QString qualityLabel(const QString& qualityKey);

    RecordingSession* m_session;
    QLineEdit* m_urlInput;
    QPushButton* m_startStopBtn;
    QLabel* m_statusLabel;
    QLabel* m_durationLabel;
    QLabel* m_fileSizeLabel;
    QLabel* m_platformLabel;
    QLabel* m_titleLabel;
    QLabel* m_streamerLabel;
    QProgressBar* m_progressBar;
    QTextEdit* m_logOutput;
    QComboBox* m_sourceQualityCombo;
    QComboBox* m_encodeModeCombo;
    QLineEdit* m_outputDirEdit;
    QSlider* m_crfSlider;
    QLabel* m_crfLabel;
    QComboBox* m_presetCombo;
    QSpinBox* m_bitrateSpin;
    QSpinBox* m_maxWidthSpin;
    QSpinBox* m_maxHeightSpin;
    QComboBox* m_contentProfileCombo;
    QCheckBox* m_selectiveRecordingCheck;
    QComboBox* m_gameKeyCombo;

    bool m_isRecording = false;
    PreviewController* m_previewController;
    LivePreviewWidget* m_livePreviewWidget;
    GameplayDetector* m_gameplayDetector;
    ClipExporter* m_gameplayExporter;
};

#endif // LIVESTREAMDOCK_H
