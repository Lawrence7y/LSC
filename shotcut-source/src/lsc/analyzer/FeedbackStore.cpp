// shotcut-source/src/lsc/analyzer/FeedbackStore.cpp
#include "FeedbackStore.h"

#include <QDir>
#include <QFile>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QTextStream>

FeedbackStore::FeedbackStore(QObject* parent)
    : QObject(parent)
{
}

bool FeedbackStore::save(const QString& filePath, const QVector<ClipFeedback>& feedback) const
{
    QJsonArray arr;
    for (const ClipFeedback& f : feedback) {
        QJsonObject obj;
        obj["clipId"] = f.clipId;
        obj["action"] = f.action;
        obj["importance"] = f.importance;
        if (!f.highlightType.isEmpty()) obj["highlightType"] = f.highlightType;
        if (f.adjustedStartSec >= 0.0) obj["adjustedStartSec"] = f.adjustedStartSec;
        if (f.adjustedEndSec >= 0.0) obj["adjustedEndSec"] = f.adjustedEndSec;
        arr.append(obj);
    }
    QJsonObject root;
    root["feedback"] = arr;
    QFile file(filePath);
    if (!file.open(QIODevice::WriteOnly | QIODevice::Truncate)) return false;
    file.write(QJsonDocument(root).toJson(QJsonDocument::Indented));
    return true;
}

QVector<ClipFeedback> FeedbackStore::load(const QString& filePath) const
{
    QVector<ClipFeedback> result;
    QFile file(filePath);
    if (!file.open(QIODevice::ReadOnly)) return result;
    const QJsonDocument doc = QJsonDocument::fromJson(file.readAll());
    const QJsonArray arr = doc.object().value("feedback").toArray();
    for (const QJsonValue& val : arr) {
        const QJsonObject obj = val.toObject();
        ClipFeedback f;
        f.clipId = obj.value("clipId").toString();
        f.action = obj.value("action").toString();
        f.importance = obj.value("importance").toInt();
        f.highlightType = obj.value("highlightType").toString();
        f.adjustedStartSec = obj.value("adjustedStartSec").toDouble(-1.0);
        f.adjustedEndSec = obj.value("adjustedEndSec").toDouble(-1.0);
        result.append(f);
    }
    return result;
}

FeedbackStats FeedbackStore::computeStats(const QVector<ClipFeedback>& feedback)
{
    FeedbackStats stats;
    stats.totalClips = feedback.size();

    double totalAdjustment = 0;
    int adjustmentCount = 0;
    double totalRating = 0;
    int ratingCount = 0;

    for (const ClipFeedback& f : feedback) {
        if (f.action == "keep") stats.keptClips++;
        else if (f.action == "delete") stats.deletedClips++;
        else if (f.action == "export") stats.exportedClips++;

        stats.actionCounts[f.action]++;

        if (!f.highlightType.isEmpty())
            stats.highlightTypeCounts[f.highlightType]++;

        if (f.adjustedStartSec >= 0.0 && f.adjustedEndSec >= 0.0) {
            totalAdjustment += (f.adjustedEndSec - f.adjustedStartSec);
            adjustmentCount++;
        }

        if (f.importance > 0) {
            totalRating += f.importance;
            ratingCount++;
        }
    }

    stats.avgBoundaryAdjustment = adjustmentCount > 0
        ? totalAdjustment / adjustmentCount : 0.0;
    stats.avgUserRating = ratingCount > 0
        ? totalRating / ratingCount : 0.0;

    return stats;
}

FeedbackStats FeedbackStore::statsForProject(const QString& videoPath) const
{
    const QString feedbackPath = videoPath + ".feedback.json";
    return computeStats(load(feedbackPath));
}

FeedbackStats FeedbackStore::globalStats() const
{
    QVector<ClipFeedback> allFeedback;
    QDir dir = QDir::home();
    const QString basePath = dir.filePath("Videos/LiveClips");

    if (dir.cd(basePath)) {
        const QStringList files = dir.entryList(QStringList("*.feedback.json"), QDir::Files);
        for (const QString& file : files)
            allFeedback.append(load(dir.filePath(file)));
    }

    return computeStats(allFeedback);
}

void FeedbackStore::exportStatsReport(const QString& outputPath) const
{
    const FeedbackStats stats = globalStats();

    QFile file(outputPath);
    if (!file.open(QIODevice::WriteOnly | QIODevice::Truncate))
        return;

    QTextStream out(&file);
    out.setEncoding(QStringConverter::Utf8);

    out << "=== 反馈闭环统计报告 ===\n\n";
    out << QString("总切片数: %1\n").arg(stats.totalClips);
    out << QString("保留切片: %1\n").arg(stats.keptClips);
    out << QString("删除切片: %1\n").arg(stats.deletedClips);
    out << QString("导出切片: %1\n").arg(stats.exportedClips);
    out << QString("平均边界调整时长: %1 秒\n").arg(stats.avgBoundaryAdjustment, 0, 'f', 2);
    out << QString("平均用户评分: %1 / 5\n\n").arg(stats.avgUserRating, 0, 'f', 2);

    out << "--- 高光类型分布 ---\n";
    for (auto it = stats.highlightTypeCounts.constBegin(); it != stats.highlightTypeCounts.constEnd(); ++it)
        out << QString("  %1: %2\n").arg(it.key()).arg(it.value());

    out << "\n--- 操作类型分布 ---\n";
    for (auto it = stats.actionCounts.constBegin(); it != stats.actionCounts.constEnd(); ++it)
        out << QString("  %1: %2\n").arg(it.key()).arg(it.value());

    out.flush();
}
