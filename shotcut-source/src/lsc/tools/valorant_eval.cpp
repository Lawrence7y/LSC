#include "analyzer/ValorantEvaluation.h"

#include <QCoreApplication>
#include <QJsonDocument>
#include <QJsonObject>
#include <QTextStream>

int main(int argc, char* argv[])
{
    QCoreApplication app(argc, argv);
    QTextStream out(stdout);
    QTextStream err(stderr);

    const QStringList args = app.arguments();
    if (args.size() < 3) {
        err << "Usage: valorant_eval <analysis.json> <annotations.json> [topN] [overlapThreshold]\n";
        return 2;
    }

    const QString analysisPath = args.at(1);
    const QString annotationsPath = args.at(2);
    const int topN = args.size() >= 4 ? args.at(3).toInt() : 10;
    const double overlapThreshold = args.size() >= 5 ? args.at(4).toDouble() : 0.3;

    const ValorantEvaluationResult result =
        ValorantEvaluation::evaluateFiles(analysisPath, annotationsPath, topN, overlapThreshold);

    QJsonObject json;
    json.insert(QStringLiteral("predictionCount"), result.predictionCount);
    json.insert(QStringLiteral("annotationCount"), result.annotationCount);
    json.insert(QStringLiteral("matchedPredictions"), result.matchedPredictions);
    json.insert(QStringLiteral("matchedAnnotations"), result.matchedAnnotations);
    json.insert(QStringLiteral("topNHitRate"), result.topNHitRate);
    json.insert(QStringLiteral("recall"), result.recall);
    json.insert(QStringLiteral("falsePositiveRate"), result.falsePositiveRate);
    json.insert(QStringLiteral("averageBoundaryOffsetSec"), result.averageBoundaryOffsetSec);

    out << QJsonDocument(json).toJson(QJsonDocument::Indented);
    return 0;
}
