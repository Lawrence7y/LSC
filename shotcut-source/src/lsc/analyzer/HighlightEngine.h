#ifndef HIGHLIGHTENGINE_H
#define HIGHLIGHTENGINE_H

#include "IHighlightStrategy.h"
#include "AnalysisProfile.h"
#include "HighlightRanker.h"
#include "MaterialClassifier.h"
#include "RankedClip.h"

#include <QObject>
#include <QPointer>
#include <QVector>
#include <QElapsedTimer>

class ClipExporter;

class HighlightEngine : public QObject
{
    Q_OBJECT
public:
    explicit HighlightEngine(QObject* parent = nullptr);
    ~HighlightEngine();

    void setStrategy(IHighlightStrategy* strategy, bool takeOwnership = true);
    IHighlightStrategy* currentStrategy() const { return m_strategy; }

    void setAnalysisProfile(const AnalysisProfile& profile);
    AnalysisProfile analysisProfile() const { return m_profile; }

    bool analyze(const QString& videoPath);
    bool analyzeIncremental(const QString& videoPath, double currentDurationSec);
    void cancel();
    bool isRunning() const;
    QVector<HighlightResult> results() const;
    int totalSegmentsFound() const { return m_totalSegments; }

    // Ranked output and classification
    QVector<RankedClip> rankedClips() const { return m_rankedClips; }
    MaterialClassification classification() const { return m_classification; }

    // Receive accumulated MaterialSignals from RecordingSession
    void setMaterialSignals(const MaterialSignals& inputSignals) { m_materialSignals = inputSignals; }

    // Persist analysis artifact
    void writeAnalysisArtifact(const QString& videoPath) const;

    void setAutoExport(bool enabled, const QString& outputDir = QString());
    bool autoExport() const { return m_autoExport; }
    void onClipExportedForPersistence(const QString& path, const QString& title);

    static IHighlightStrategy* createGameStrategy(QObject* parent);
    static IHighlightStrategy* createDanceStrategy(QObject* parent);
    static IHighlightStrategy* createDialogStrategy(QObject* parent);
    static IHighlightStrategy* createGenericStrategy(QObject* parent);

signals:
    void progressChanged(int percent);
    void segmentFound(const HighlightSegment& segment);
    void rankedClipFound(const RankedClip& clip);
    void clipExported(const QString& filePath, const QString& title);
    void finished();
    void errorOccurred(const QString& message);

private slots:
    void onStrategyFinished();
    void onStrategyError(const QString& msg);
    void onStrategySegment(const HighlightSegment& seg);
    void onClipExported(const QString& path, const QString& title);

private:
    void cleanupStrategy();
    void exportSegment(const HighlightSegment& seg);
    void persistDetectedClips(const QVector<HighlightSegment>& segments);
    void persistExportedClip(const QString& path, const QString& title);
    QString resolveCurrentProjectId() const;
    bool isNewSegment(const HighlightSegment& seg) const;
    static QVector<HighlightSegment> normalizeSegments(const QVector<HighlightSegment>& segments);
    static void mergeSegmentInto(HighlightSegment& target, const HighlightSegment& incoming);

    IHighlightStrategy* m_strategy = nullptr;
    bool m_ownsStrategy = false;
    QVector<HighlightResult> m_results;
    QVector<HighlightSegment> m_knownSegments;
    QVector<HighlightSegment> m_pendingSegments;
    int m_totalSegments = 0;
    QString m_sourcePath;
    QString m_currentProjectId;
    bool m_autoExport = false;
    ClipExporter* m_exporter = nullptr;
    double m_lastAnalyzedTime = 0.0;
    double m_pendingAnalyzedTime = 0.0;
    bool m_analyzing = false;
    AnalysisProfile m_profile{AnalysisProfile::generic()};

    // Pilot members
    MaterialClassifier m_classifier;
    HighlightRanker m_ranker;
    MaterialSignals m_materialSignals;
    QVector<RankedClip> m_rankedClips;
    MaterialClassification m_classification;
};

#endif
