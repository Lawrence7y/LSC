#ifndef ANALYSISDOCK_H
#define ANALYSISDOCK_H

#include <QCheckBox>
#include <QDockWidget>
#include <QImage>
#include <QLineEdit>
#include <QListWidget>
#include <QTreeWidget>

#include "analyzer/HighlightEngine.h"
#include "analyzer/FeedbackStore.h"
#include "analyzer/RankedClip.h"
#include "analyzer/ThumbnailGenerator.h"

class ClipExporter;
class QComboBox;
class QLabel;
class QPushButton;
class QProgressBar;
class QSlider;

struct HighlightCardData {
    double startSec = 0.0;
    double endSec = 0.0;
    double score = 0.0;
    QString reason;
    QStringList keywords;
    QImage thumbnail;
    QString clipExportPath;
    bool selected = false;
    QString sourceTag;
    bool realtime = false;
};

class AnalysisDock : public QDockWidget
{
    Q_OBJECT

public:
    explicit AnalysisDock(QWidget* parent = nullptr);

    void setHighlightEngine(HighlightEngine* engine);
    void setVideoPath(const QString& videoPath);
    void onRecordingComplete(const QString& videoPath);
    void ingestRealtimeSegment(const HighlightSegment& segment, const QString& videoPath);
    void requestPreviewExport(double startSec, double endSec);
    QString videoPath() const { return m_videoPath; }

    // Valorant pilot: ranked clips and annotation
    void setRankedClips(const QVector<RankedClip>& clips);
    QVector<ClipFeedback> annotationFeedback() const { return m_pendingFeedback; }
    void simulateAnnotation(const QString& clipId, const QString& action,
                            int importance, const QString& highlightType);

signals:
    void clipExported(const QString& filePath);
    void highlightSelected(double startSec, double endSec);
    void analysisCompleted();

private slots:
    void onAnalyzeClicked();
    void onSegmentFound(const HighlightSegment& segment);
    void onAnalysisFinished();
    void onProgressChanged(int percent);
    void onError(const QString& error);
    void onThumbnailReady(double timestamp, const QImage& thumbnail);
    void onAllThumbnailsReady();
    void onItemClicked(int index);
    void onItemDoubleClicked(int index);
    void onSelectAllClicked();
    void onInvertSelectionClicked();
    void onBatchExportClicked();
    void onStrategyChanged(int index);
    void onSensitivityChanged(int value);
    void onPreviewExportRequested(double startSec, double endSec);

    // Valorant pilot: annotation slots
    void onAnnotationKeep();
    void onAnnotationDelete();
    void onAnnotationAdjustBoundary();
    void onAnnotationTypeChanged(int index);
    void onAnnotationImportanceChanged(int value);
    void writePendingFeedback();

private:
    void syncStrategyComboToProfile(const AnalysisProfile& profile);
    void setupUi();
    void reconnectEngineSignals();
    void applyStrategySelection();
    void updateAnalyzeButtonState();
    void updateCardWidget(int index);
    void updateSelectionState();
    int upsertCard(const HighlightCardData& card);
    void mergeCardInto(HighlightCardData& target, const HighlightCardData& incoming);
    QVector<int> selectedCardIndices() const;
    void exportSegments(const QVector<int>& indices);
    QString formatSegmentTime(double seconds) const;

    QListWidget* m_listWidget = nullptr;
    QProgressBar* m_progressBar = nullptr;
    QLabel* m_statusLabel = nullptr;
    QLabel* m_summaryLabel = nullptr;
    QComboBox* m_strategyCombo = nullptr;
    QLabel* m_gameModeLabel = nullptr;
    QComboBox* m_gameModeCombo = nullptr;
    QSlider* m_sensitivitySlider = nullptr;
    QLabel* m_sensitivityLabel = nullptr;
    QLineEdit* m_keywordEdit = nullptr;
    QLineEdit* m_videoPathEdit = nullptr;
    QPushButton* m_analyzeBtn = nullptr;
    QPushButton* m_selectAllBtn = nullptr;
    QPushButton* m_invertBtn = nullptr;
    QPushButton* m_batchExportBtn = nullptr;
    QLabel* m_exportStatusLabel = nullptr;
    QCheckBox* m_autoAnalyzeCheck = nullptr;

    HighlightEngine* m_engine = nullptr;
    ThumbnailGenerator* m_thumbnailGen = nullptr;
    ClipExporter* m_clipExporter = nullptr;
    QVector<HighlightCardData> m_cards;
    QString m_videoPath;
    int m_pendingExports = 0;
    bool m_ownsEngine = false;

    // Valorant pilot: ranked clips tree and annotation
    QTreeWidget* m_treeWidget = nullptr;
    FeedbackStore m_feedbackStore;
    QVector<RankedClip> m_rankedClips;
    QVector<ClipFeedback> m_pendingFeedback;
    QString m_feedbackFilePath;

    // Annotation controls
    QComboBox* m_annotationTypeCombo = nullptr;
    QSlider* m_annotationImportanceSlider = nullptr;
    QLabel* m_annotationImportanceLabel = nullptr;
    QPushButton* m_annotationKeepBtn = nullptr;
    QPushButton* m_annotationDeleteBtn = nullptr;
    QPushButton* m_annotationAdjustBtn = nullptr;
    QLabel* m_annotationStatusLabel = nullptr;
};

#endif // ANALYSISDOCK_H
