#include "ValorantEvaluation.h"

#include <QFile>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <algorithm>

namespace {
double overlapRatio(double aStart, double aEnd, double bStart, double bEnd)
{
    const double overlapStart = std::max(aStart, bStart);
    const double overlapEnd = std::min(aEnd, bEnd);
    const double overlap = std::max(0.0, overlapEnd - overlapStart);
    const double minLen = std::max(0.1, std::min(aEnd - aStart, bEnd - bStart));
    return overlap / minLen;
}

double boundaryOffset(const RankedClip& prediction, const ValorantAnnotation& annotation)
{
    return (std::abs(prediction.startSec - annotation.startSec)
            + std::abs(prediction.endSec - annotation.endSec)) / 2.0;
}
}

QVector<ValorantAnnotation> ValorantEvaluation::loadAnnotations(const QString& annotationsPath)
{
    QVector<ValorantAnnotation> annotations;
    QFile file(annotationsPath);
    if (!file.open(QIODevice::ReadOnly)) {
        return annotations;
    }

    const QJsonDocument doc = QJsonDocument::fromJson(file.readAll());
    const QJsonArray items = doc.object().value(QStringLiteral("annotations")).toArray();
    for (const QJsonValue& value : items) {
        const QJsonObject obj = value.toObject();
        ValorantAnnotation annotation;
        annotation.startSec = obj.value(QStringLiteral("startSec")).toDouble();
        annotation.endSec = obj.value(QStringLiteral("endSec")).toDouble();
        annotation.label = obj.value(QStringLiteral("label")).toString();
        if (annotation.endSec > annotation.startSec) {
            annotations.append(annotation);
        }
    }
    return annotations;
}

QVector<RankedClip> ValorantEvaluation::loadPredictions(const QString& analysisPath)
{
    QVector<RankedClip> predictions;
    QFile file(analysisPath);
    if (!file.open(QIODevice::ReadOnly)) {
        return predictions;
    }

    const QJsonDocument doc = QJsonDocument::fromJson(file.readAll());
    const QJsonArray items = doc.object().value(QStringLiteral("motherClips")).toArray();
    for (const QJsonValue& value : items) {
        const QJsonObject obj = value.toObject();
        RankedClip clip;
        clip.startSec = obj.value(QStringLiteral("startSec")).toDouble();
        clip.endSec = obj.value(QStringLiteral("endSec")).toDouble();
        clip.rankScore = obj.value(QStringLiteral("rankScore")).toDouble();
        clip.clipId = obj.value(QStringLiteral("clipId")).toString();
        if (clip.endSec > clip.startSec) {
            predictions.append(clip);
        }
    }

    std::sort(predictions.begin(), predictions.end(), [](const RankedClip& a, const RankedClip& b) {
        return a.rankScore > b.rankScore;
    });
    return predictions;
}

ValorantEvaluationResult ValorantEvaluation::evaluateFiles(const QString& analysisPath,
                                                           const QString& annotationsPath,
                                                           int topN,
                                                           double overlapThreshold)
{
    return evaluate(loadPredictions(analysisPath),
                    loadAnnotations(annotationsPath),
                    topN,
                    overlapThreshold);
}

ValorantEvaluationResult ValorantEvaluation::evaluate(
    const QVector<RankedClip>& predictions,
    const QVector<ValorantAnnotation>& annotations,
    int topN,
    double overlapThreshold)
{
    ValorantEvaluationResult result;
    result.predictionCount = std::min(std::max(topN, 0),
                                      static_cast<int>(predictions.size()));
    result.annotationCount = annotations.size();
    if (result.predictionCount == 0 || annotations.isEmpty()) {
        result.falsePositiveRate = result.predictionCount > 0 ? 1.0 : 0.0;
        return result;
    }

    QVector<bool> annotationMatched(annotations.size(), false);
    double offsetSum = 0.0;

    for (int i = 0; i < result.predictionCount; ++i) {
        const RankedClip& prediction = predictions.at(i);
        int bestAnnotation = -1;
        double bestOverlap = 0.0;
        for (int j = 0; j < annotations.size(); ++j) {
            if (annotationMatched.at(j)) {
                continue;
            }
            const auto& annotation = annotations.at(j);
            const double overlap = overlapRatio(prediction.startSec, prediction.endSec,
                                                annotation.startSec, annotation.endSec);
            if (overlap > bestOverlap) {
                bestOverlap = overlap;
                bestAnnotation = j;
            }
        }

        if (bestAnnotation >= 0 && bestOverlap >= overlapThreshold) {
            annotationMatched[bestAnnotation] = true;
            ++result.matchedPredictions;
            ++result.matchedAnnotations;
            result.matchedPredictionIndexes.append(i);
            result.matchedAnnotationIndexes.append(bestAnnotation);
            offsetSum += boundaryOffset(prediction, annotations.at(bestAnnotation));
        }
    }

    result.topNHitRate = static_cast<double>(result.matchedPredictions) / result.predictionCount;
    result.recall = annotations.isEmpty()
        ? 0.0
        : static_cast<double>(result.matchedAnnotations) / annotations.size();
    result.falsePositiveRate = 1.0 - result.topNHitRate;
    result.averageBoundaryOffsetSec = result.matchedPredictions > 0
        ? offsetSum / result.matchedPredictions
        : 0.0;
    return result;
}
