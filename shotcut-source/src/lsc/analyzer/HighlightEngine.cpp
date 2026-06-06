#include "HighlightEngine.h"
#include "ClipExporter.h"
#include "core/LscDatabase.h"
#include "GameStrategy.h"
#include "DanceStrategy.h"
#include "DialogStrategy.h"
#include "GenericStrategy.h"
#include "CompositeHighlightStrategy.h"
#include "CommentaryStrategy.h"
#include "HighlightUtils.h"
#include "LscConfig.h"
#include <QDir>
#include <QDateTime>
#include <QSet>
#include <QJsonDocument>
#include <QJsonArray>
#include <QFile>
#include <QFileInfo>
#include <QUuid>
#include <algorithm>
#include <utility>

HighlightEngine::HighlightEngine(QObject* parent) : QObject(parent) {}

HighlightEngine::~HighlightEngine()
{
    cleanupStrategy();
}

void HighlightEngine::cleanupStrategy()
{
    if (m_strategy) {
        disconnect(m_strategy, nullptr, this, nullptr);
        if (m_ownsStrategy) {
            m_strategy->deleteLater();
        }
        m_strategy = nullptr;
        m_ownsStrategy = false;
    }
}

void HighlightEngine::setStrategy(IHighlightStrategy* strategy, bool takeOwnership)
{
    cleanupStrategy();

    m_strategy = strategy;
    m_ownsStrategy = takeOwnership && strategy;
    m_results.clear();
    m_knownSegments.clear();
    m_pendingSegments.clear();
    m_lastAnalyzedTime = 0.0;
    m_pendingAnalyzedTime = 0.0;

    if (m_strategy) {
        connect(m_strategy, &IHighlightStrategy::finished, this, &HighlightEngine::onStrategyFinished);
        connect(m_strategy, &IHighlightStrategy::errorOccurred, this, &HighlightEngine::onStrategyError);
        connect(m_strategy, &IHighlightStrategy::segmentFound, this, &HighlightEngine::onStrategySegment);
        connect(m_strategy, &IHighlightStrategy::progressChanged, this, &HighlightEngine::progressChanged);
    }
}

void HighlightEngine::setAnalysisProfile(const AnalysisProfile& profile)
{
    m_profile = profile;

    if (profile.id == QStringLiteral("valorant")) {
        auto* composite = new CompositeHighlightStrategy(this);
        composite->setDisplayName(profile.id);

        auto* gameStrategy = createGameStrategy(composite);
        gameStrategy->configure(QJsonObject{
            {QStringLiteral("gameHint"),
             profile.gameKey.isEmpty() ? profile.id : profile.gameKey},
            {QStringLiteral("sensitivity"), 0.7},
        });
        composite->addStrategy(gameStrategy);

        auto* commentaryStrategy = new CommentaryStrategy(composite);
        commentaryStrategy->configure(QJsonObject{
            {QStringLiteral("sensitivity"), 0.5},
        });
        composite->addStrategy(commentaryStrategy);
        setStrategy(composite);
        return;
    }

    if (profile.id == QStringLiteral("dance")) {
        setStrategy(createDanceStrategy(this));
        return;
    }

    if (profile.id == QStringLiteral("commentary")) {
        auto* commentaryStrategy = new CommentaryStrategy(this);
        commentaryStrategy->configure(QJsonObject{
            {QStringLiteral("sensitivity"), 0.5},
        });
        setStrategy(commentaryStrategy);
        return;
    }

    setStrategy(createGenericStrategy(this));
}

void HighlightEngine::setAutoExport(bool enabled, const QString& outputDir)
{
    m_autoExport = enabled;
    if (enabled) {
        if (!m_exporter) {
            m_exporter = new ClipExporter(this);
            connect(m_exporter, &ClipExporter::clipExported, this, &HighlightEngine::onClipExported);
        }
        if (!outputDir.isEmpty()) m_exporter->setOutputDir(outputDir);
    }
}

bool HighlightEngine::analyze(const QString& videoPath)
{
    if (!m_strategy) {
        emit errorOccurred("No strategy selected");
        return false;
    }
    m_sourcePath = videoPath;
    m_results.clear();
    m_knownSegments.clear();
    m_pendingSegments.clear();
    m_totalSegments = 0;
    m_lastAnalyzedTime = 0.0;
    m_pendingAnalyzedTime = 0.0;
    m_analyzing = true;
    m_strategy->analyze(videoPath);
    return true;
}

bool HighlightEngine::analyzeIncremental(const QString& videoPath, double currentDurationSec)
{
    if (!m_strategy || m_analyzing) {
        return false;
    }

    // Keep realtime analysis coarse enough to avoid fighting the recorder for
    // the growing file while still surfacing highlights during recording.
    constexpr double kMinInitialDurationSec = 8.0;
    if (m_lastAnalyzedTime <= 0.0 && currentDurationSec < kMinInitialDurationSec) {
        return false;
    }

    // Only analyze if enough new content since last analysis.
    const double minIntervalSec = lsc::LscConfig::instance().highlightStepSec * 4.0;
    if (currentDurationSec - m_lastAnalyzedTime < minIntervalSec) {
        return false;
    }

    m_sourcePath = videoPath;
    m_pendingSegments.clear();
    m_pendingAnalyzedTime = currentDurationSec;
    m_analyzing = true;
    m_strategy->analyze(videoPath);
    return true;
}

void HighlightEngine::cancel()
{
    if (m_strategy) m_strategy->cancel();
    if (m_exporter) m_exporter->cancel();
    m_analyzing = false;
}

bool HighlightEngine::isRunning() const { return m_analyzing || (m_exporter && m_exporter->isRunning()); }

QVector<HighlightResult> HighlightEngine::results() const { return m_results; }

bool HighlightEngine::isNewSegment(const HighlightSegment& seg) const
{
    for (const auto& known : m_knownSegments) {
        if (HighlightUtils::overlapRatio(seg, known) >= 0.8) {
            return false;
        }
    }
    return true;
}

void HighlightEngine::onStrategyFinished()
{
    m_analyzing = false;
    if (m_strategy) {
        HighlightResult r = m_strategy->result();
        QVector<HighlightSegment> normalized = normalizeSegments(r.segments);
        if (normalized.isEmpty() && !m_pendingSegments.isEmpty()) {
            normalized = normalizeSegments(m_pendingSegments);
        }
        r.segments = normalized;
        persistDetectedClips(normalized);
        m_lastAnalyzedTime = qMax(m_pendingAnalyzedTime,
                                  r.metadata.value("analyzedDuration").toDouble(m_lastAnalyzedTime));
        m_pendingAnalyzedTime = 0.0;
        m_results.append(r);

        // Valorant pilot: route through classifier and ranker
        if (m_profile.id == QStringLiteral("valorant") && !normalized.isEmpty()) {
            m_classification = m_classifier.classify(m_materialSignals);

            ValorantProfileConfig profileConfig;
            if (m_classification.materialType == QStringLiteral("uncertain")) {
                // Dual-run: weighted fusion of both profiles.
                const double totalScore = m_classification.streamerScore + m_classification.commentaryScore;
                const double streamerWeight = (totalScore > 0.0)
                    ? m_classification.streamerScore / totalScore
                    : 0.5;
                profileConfig = ValorantProfileConfig::fuse(
                    ValorantProfileConfig::streamer(),
                    ValorantProfileConfig::commentary(),
                    streamerWeight);
            } else if (m_classification.materialType == QStringLiteral("commentary_watchparty")) {
                profileConfig = ValorantProfileConfig::commentary();
            } else {
                profileConfig = ValorantProfileConfig::streamer();
            }

            m_rankedClips = m_ranker.rankCandidates(normalized,
                                                    profileConfig,
                                                    m_classification.materialType);
            // Attach classification metadata to each clip.
            for (RankedClip& clip : m_rankedClips) {
                clip.metadata.insert(QStringLiteral("materialType"), m_classification.materialType);
                clip.metadata.insert(QStringLiteral("classificationConfidence"), m_classification.confidence);
                clip.metadata.insert(QStringLiteral("fallbackActivated"), m_classification.fallbackActivated);
                emit rankedClipFound(clip);
            }

            // Write analysis artifact after ranking.
            if (!m_sourcePath.isEmpty()) {
                writeAnalysisArtifact(m_sourcePath);
            }
        }

        for (const HighlightSegment& segment : std::as_const(normalized)) {
            if (!isNewSegment(segment)) {
                continue;
            }

            m_knownSegments.append(segment);
            ++m_totalSegments;
            QMetaObject::invokeMethod(
                this,
                [this, segment]() { emit segmentFound(segment); },
                Qt::QueuedConnection);

            if (m_autoExport && m_exporter && !m_sourcePath.isEmpty()) {
                exportSegment(segment);
            }
        }
    }
    m_pendingSegments.clear();
    QMetaObject::invokeMethod(this, [this]() { emit finished(); }, Qt::QueuedConnection);
}

void HighlightEngine::onStrategyError(const QString& msg) { m_analyzing = false; emit errorOccurred(msg); }

void HighlightEngine::onStrategySegment(const HighlightSegment& seg)
{
    // Deduplicate — only process segments we haven't seen before
    m_pendingSegments.append(seg);
}

void HighlightEngine::exportSegment(const HighlightSegment& seg)
{
    QString dir = m_exporter->outputDir();
    QString ts = QDateTime::currentDateTime().toString("yyyyMMdd_HHmmss");
    QString filename = QString("highlight_%1_%2s-%3s.mp4")
        .arg(ts).arg(static_cast<int>(seg.startSec)).arg(static_cast<int>(seg.endSec));

    ClipJob job;
    job.sourcePath = m_sourcePath;
    job.startSec = seg.startSec;
    job.endSec = seg.endSec;
    job.outputPath = dir + "/" + filename;
    job.title = seg.reason;
    job.useCopy = true;

    m_exporter->exportClip(job);
}

void HighlightEngine::onClipExported(const QString& path, const QString& title)
{
    persistExportedClip(path, title);
    emit clipExported(path, title);
}

void HighlightEngine::onClipExportedForPersistence(const QString& path, const QString& title)
{
    persistExportedClip(path, title);
}

QString HighlightEngine::resolveCurrentProjectId() const
{
    auto& db = lsc::LscDatabase::instance();
    if (!db.isOpen() && !db.initialize()) {
        return {};
    }

    const QString sourceCanonical = QFileInfo(m_sourcePath).absoluteFilePath();
    const auto projects = db.allProjects();
    for (const auto& project : projects) {
        if (QFileInfo(project.videoPath).absoluteFilePath() == sourceCanonical) {
            return project.id;
        }
    }
    return {};
}

void HighlightEngine::persistDetectedClips(const QVector<HighlightSegment>& segments)
{
    if (segments.isEmpty() || m_sourcePath.isEmpty()) {
        return;
    }

    auto& db = lsc::LscDatabase::instance();
    if (!db.isOpen() && !db.initialize()) {
        return;
    }

    m_currentProjectId = resolveCurrentProjectId();
    if (m_currentProjectId.isEmpty()) {
        return;
    }

    for (const HighlightSegment& segment : segments) {
        lsc::ClipRecord clip;
        clip.id = QUuid::createUuid().toString(QUuid::WithoutBraces);
        clip.projectId = m_currentProjectId;
        clip.startSec = segment.startSec;
        clip.endSec = segment.endSec;
        clip.score = segment.score;
        clip.reason = segment.reason;
        clip.keywords = segment.keywords.join(QStringLiteral(","));
        clip.title = segment.reason.isEmpty()
            ? QStringLiteral("Highlight %1-%2")
                  .arg(segment.startSec, 0, 'f', 1)
                  .arg(segment.endSec, 0, 'f', 1)
            : segment.reason;
        clip.status = QStringLiteral("detected");
        clip.createdAt = QDateTime::currentDateTime();
        db.insertClip(clip);
    }

    lsc::ProjectRecord project = db.project(m_currentProjectId);
    if (!project.id.isEmpty()) {
        project.status = QStringLiteral("analyzed");
        db.updateProject(project);
    }
}

void HighlightEngine::persistExportedClip(const QString& path, const QString& title)
{
    auto& db = lsc::LscDatabase::instance();
    if (!db.isOpen() && !db.initialize()) {
        return;
    }

    QString projectId = m_currentProjectId;
    if (projectId.isEmpty()) {
        projectId = resolveCurrentProjectId();
    }
    if (projectId.isEmpty()) {
        return;
    }

    auto clips = db.clipsByProject(projectId);
    for (lsc::ClipRecord& clip : clips) {
        if (!clip.exportPath.isEmpty() && clip.exportPath != path) {
            continue;
        }
        if (!title.isEmpty() && clip.title != title && clip.reason != title) {
            continue;
        }
        clip.exportPath = path;
        clip.status = QStringLiteral("exported");
        db.updateClip(clip);

        lsc::ProjectRecord project = db.project(projectId);
        if (!project.id.isEmpty()) {
            project.status = QStringLiteral("exported");
            db.updateProject(project);
        }
        return;
    }
}

QVector<HighlightSegment> HighlightEngine::normalizeSegments(const QVector<HighlightSegment>& segments)
{
    return HighlightUtils::normalizeSegments(segments);
}

void HighlightEngine::mergeSegmentInto(HighlightSegment& target, const HighlightSegment& incoming)
{
    HighlightUtils::mergeSegmentInto(target, incoming);
}

static QJsonObject rankedClipToJson(const RankedClip& clip)
{
    QJsonObject json;
    json["clipId"] = clip.clipId;
    json["startSec"] = clip.startSec;
    json["endSec"] = clip.endSec;
    json["rankScore"] = clip.rankScore;
    json["roundImportance"] = clip.roundImportance;
    json["combatIntensity"] = clip.combatIntensity;
    json["reactionIntensity"] = clip.reactionIntensity;
    json["semanticExcitement"] = clip.semanticExcitement;
    json["novelty"] = clip.novelty;
    json["clipCompleteness"] = clip.clipCompleteness;
    json["explanation"] = clip.explanation;
    json["sourceType"] = clip.sourceType;
    json["roundIndex"] = clip.roundIndex;
    json["roundPhase"] = clip.roundPhase;
    json["parentClipId"] = clip.parentClipId;
    json["isPrimary"] = clip.isPrimary;

    QJsonArray altIds;
    for (const QString& id : clip.alternateIds) {
        altIds.append(id);
    }
    json["alternateIds"] = altIds;

    QJsonArray sigs;
    for (const QString& s : clip.signalNames) {
        sigs.append(s);
    }
    json["signalNames"] = sigs;
    return json;
}

void HighlightEngine::writeAnalysisArtifact(const QString& videoPath) const
{
    QJsonObject root;
    root["materialType"] = m_classification.materialType;
    root["classificationConfidence"] = m_classification.confidence;
    root["fallbackActivated"] = m_classification.fallbackActivated;
    root["profileUsed"] = m_classification.materialType == QStringLiteral("uncertain")
        ? QStringLiteral("dual-run")
        : m_classification.materialType;

    QJsonArray clips;
    for (const RankedClip& clip : m_rankedClips) {
        clips.append(rankedClipToJson(clip));
    }
    root["motherClips"] = clips;
    root["totalDurationSec"] = m_lastAnalyzedTime;

    QFile file(videoPath + QStringLiteral(".analysis.json"));
    if (file.open(QIODevice::WriteOnly | QIODevice::Truncate)) {
        file.write(QJsonDocument(root).toJson(QJsonDocument::Indented));
    }
}

// Factory methods
IHighlightStrategy* HighlightEngine::createGameStrategy(QObject* parent) { return new GameStrategy(parent); }
IHighlightStrategy* HighlightEngine::createDanceStrategy(QObject* parent) { return new DanceStrategy(parent); }
IHighlightStrategy* HighlightEngine::createDialogStrategy(QObject* parent) { return new DialogStrategy(parent); }
IHighlightStrategy* HighlightEngine::createGenericStrategy(QObject* parent) { return new GenericStrategy(parent); }
