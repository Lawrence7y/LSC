#include "analyzer/ValorantEvaluation.h"

#include <QCoreApplication>
#include <QDir>
#include <QFile>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <iostream>

static int g_pass = 0;
static int g_fail = 0;

void check(const char* name, bool condition)
{
    if (condition) {
        ++g_pass;
        std::cout << "[PASS] " << name << std::endl;
    } else {
        ++g_fail;
        std::cout << "[FAIL] " << name << std::endl;
    }
}

int main(int argc, char* argv[])
{
    QCoreApplication app(argc, argv);

    QVector<ValorantAnnotation> annotations{
        {10.0, 20.0, QStringLiteral("ace")},
        {50.0, 60.0, QStringLiteral("clutch")},
    };

    QVector<RankedClip> predictions;
    RankedClip hitTop1;
    hitTop1.startSec = 11.0;
    hitTop1.endSec = 19.0;
    hitTop1.rankScore = 0.95;
    predictions.append(hitTop1);

    RankedClip miss;
    miss.startSec = 100.0;
    miss.endSec = 110.0;
    miss.rankScore = 0.9;
    predictions.append(miss);

    RankedClip hitTop3;
    hitTop3.startSec = 52.0;
    hitTop3.endSec = 61.0;
    hitTop3.rankScore = 0.8;
    predictions.append(hitTop3);

    const ValorantEvaluationResult result =
        ValorantEvaluation::evaluate(predictions, annotations, 3, 0.3);

    check("top-n hit rate counts matched predictions",
          qAbs(result.topNHitRate - (2.0 / 3.0)) < 0.001);
    check("annotation recall counts matched labels",
          qAbs(result.recall - 1.0) < 0.001);
    check("false positive rate counts misses",
          qAbs(result.falsePositiveRate - (1.0 / 3.0)) < 0.001);
    check("average boundary offset is measured",
          qAbs(result.averageBoundaryOffsetSec - 1.25) < 0.001);
    check("matched prediction indexes are recorded",
          result.matchedPredictionIndexes == QVector<int>({0, 2}));

    const QString annotationsPath = QDir::tempPath() + "/lsc_valorant_annotations.json";
    const QString analysisPath = QDir::tempPath() + "/lsc_valorant_analysis.json";

    QJsonArray annotationItems;
    QJsonObject annotation;
    annotation.insert("startSec", 10.0);
    annotation.insert("endSec", 20.0);
    annotation.insert("label", "ace");
    annotationItems.append(annotation);
    QJsonObject annotationRoot;
    annotationRoot.insert("annotations", annotationItems);
    QFile annotationFile(annotationsPath);
    annotationFile.open(QIODevice::WriteOnly | QIODevice::Truncate);
    annotationFile.write(QJsonDocument(annotationRoot).toJson());
    annotationFile.close();

    QJsonArray clipItems;
    QJsonObject clip;
    clip.insert("startSec", 11.0);
    clip.insert("endSec", 19.0);
    clip.insert("rankScore", 0.95);
    clipItems.append(clip);
    QJsonObject analysisRoot;
    analysisRoot.insert("motherClips", clipItems);
    QFile analysisFile(analysisPath);
    analysisFile.open(QIODevice::WriteOnly | QIODevice::Truncate);
    analysisFile.write(QJsonDocument(analysisRoot).toJson());
    analysisFile.close();

    const auto loadedAnnotations = ValorantEvaluation::loadAnnotations(annotationsPath);
    const auto loadedPredictions = ValorantEvaluation::loadPredictions(analysisPath);
    const auto fileResult = ValorantEvaluation::evaluateFiles(analysisPath, annotationsPath, 10, 0.3);

    check("annotations load from json file", loadedAnnotations.size() == 1);
    check("predictions load from analysis json file", loadedPredictions.size() == 1);
    check("file evaluator reports hit", qAbs(fileResult.topNHitRate - 1.0) < 0.001);

    std::cout << "=== Results: " << g_pass << " passed, " << g_fail << " failed ===" << std::endl;
    return g_fail == 0 ? 0 : 1;
}
