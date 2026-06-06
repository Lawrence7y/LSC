#ifndef VALORANTEVALUATION_H
#define VALORANTEVALUATION_H

#include "RankedClip.h"

#include <QString>
#include <QVector>

struct ValorantAnnotation {
    double startSec = 0.0;
    double endSec = 0.0;
    QString label;
};

struct ValorantEvaluationResult {
    int predictionCount = 0;
    int annotationCount = 0;
    int matchedPredictions = 0;
    int matchedAnnotations = 0;
    double topNHitRate = 0.0;
    double recall = 0.0;
    double falsePositiveRate = 0.0;
    double averageBoundaryOffsetSec = 0.0;
    QVector<int> matchedPredictionIndexes;
    QVector<int> matchedAnnotationIndexes;
};

class ValorantEvaluation {
public:
    static QVector<ValorantAnnotation> loadAnnotations(const QString& annotationsPath);
    static QVector<RankedClip> loadPredictions(const QString& analysisPath);
    static ValorantEvaluationResult evaluateFiles(const QString& analysisPath,
                                                  const QString& annotationsPath,
                                                  int topN = 10,
                                                  double overlapThreshold = 0.3);
    static ValorantEvaluationResult evaluate(const QVector<RankedClip>& predictions,
                                             const QVector<ValorantAnnotation>& annotations,
                                             int topN = 10,
                                             double overlapThreshold = 0.3);
};

#endif // VALORANTEVALUATION_H
