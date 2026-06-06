#include "LscConfig.h"
#include "docks/LivestreamDock.h"
#include "docks/SettingsDock.h"

#include <QApplication>
#include <QCheckBox>
#include <QComboBox>
#include <QLineEdit>
#include <QSpinBox>
#include <iostream>

static int g_testCount = 0;
static int g_passCount = 0;
static int g_failCount = 0;

#define LOG(msg) std::cout << QString(msg).toStdString() << std::endl

void runTest(const QString& name, bool condition)
{
    ++g_testCount;
    if (condition) {
        ++g_passCount;
        LOG(QString("[PASS] %1").arg(name));
    } else {
        ++g_failCount;
        LOG(QString("[FAIL] %1").arg(name));
    }
}

template <typename T>
T* child(lsc::SettingsDock& dock, const char* objectName)
{
    return dock.findChild<T*>(QString::fromLatin1(objectName));
}

int main(int argc, char* argv[])
{
    QApplication app(argc, argv);

    LOG("=== SettingsDock Tests ===");
    LOG("");

    auto& cfg = lsc::LscConfig::instance();
    cfg.defaultAnalysisProfile = "generic";
    cfg.defaultGameKey = "valorant";
    cfg.defaultSourceQuality = "best";
    cfg.defaultFormat = "mp4";
    cfg.defaultAutoAnalyze = true;
    cfg.defaultEnableASR = false;
    cfg.defaultOutputSubdir = "Videos/LiveRecordings";
    cfg.exportFilenameTemplate = "{source}_{index}_{time}";
    cfg.defaultExportResolution = "original";

    lsc::SettingsDock dock;

    auto* profile = child<QComboBox>(dock, "analysisProfileCombo");
    auto* game = child<QComboBox>(dock, "gameTypeCombo");
    auto* quality = child<QComboBox>(dock, "defaultQualityCombo");
    auto* format = child<QComboBox>(dock, "defaultFormatCombo");
    auto* autoAnalyze = child<QCheckBox>(dock, "autoAnalyzeCheck");
    auto* enableAsr = child<QCheckBox>(dock, "enableASRCheck");
    auto* outputDir = child<QLineEdit>(dock, "defaultOutputDirEdit");
    auto* filenameTemplate = child<QLineEdit>(dock, "filenameTemplateEdit");
    auto* resolution = child<QComboBox>(dock, "defaultResolutionCombo");
    auto* minClip = child<QSpinBox>(dock, "minClipLengthSpin");
    auto* maxClip = child<QSpinBox>(dock, "maxClipLengthSpin");

    runTest("settings controls are discoverable",
            profile && game && quality && format && autoAnalyze && enableAsr
                && outputDir && filenameTemplate && resolution && minClip && maxClip);

    if (g_failCount == 0) {
        profile->setCurrentIndex(profile->findData("commentary"));
        game->setCurrentIndex(game->findData("generic"));
        quality->setCurrentIndex(quality->findData("hd"));
        format->setCurrentIndex(format->findData("mkv"));
        autoAnalyze->setChecked(false);
        enableAsr->setChecked(true);
        outputDir->setText("D:/LSC/Recordings");
        filenameTemplate->setText("{streamer}_{time}");
        resolution->setCurrentIndex(resolution->findData("720p"));
        minClip->setValue(12);
        maxClip->setValue(60);

        runTest("settings dock writes analysis profile", cfg.defaultAnalysisProfile == "commentary");
        runTest("settings dock writes game key", cfg.defaultGameKey == "generic");
        runTest("settings dock writes source quality", cfg.defaultSourceQuality == "hd");
        runTest("settings dock writes format", cfg.defaultFormat == "mkv");
        runTest("settings dock writes auto analyze", !cfg.defaultAutoAnalyze);
        runTest("settings dock writes ASR flag", cfg.defaultEnableASR);
        runTest("settings dock writes output dir", cfg.defaultOutputSubdir == "D:/LSC/Recordings");
        runTest("settings dock writes filename template", cfg.exportFilenameTemplate == "{streamer}_{time}");
        runTest("settings dock writes resolution", cfg.defaultExportResolution == "720p");
        runTest("settings dock writes clip lengths", cfg.shortClipMinSec == 12 && cfg.shortClipMaxSec == 60);
    }

    LivestreamDock livestreamDock;
    cfg.defaultSourceQuality = "sd";
    cfg.defaultAnalysisProfile = "valorant";
    cfg.defaultGameKey = "valorant";
    cfg.defaultOutputSubdir = "D:/LSC/LiveRuntime";
    livestreamDock.applyRuntimeConfig();

    auto* liveQuality = livestreamDock.findChild<QComboBox*>("sourceQualityCombo");
    auto* liveProfile = livestreamDock.findChild<QComboBox*>("contentProfileCombo");
    auto* liveOutputDir = livestreamDock.findChild<QLineEdit*>("outputDirEdit");
    runTest("livestream controls are discoverable",
            liveQuality && liveProfile && liveOutputDir);
    if (liveQuality && liveProfile && liveOutputDir) {
        runTest("livestream dock applies runtime quality setting",
                liveQuality->currentData().toString() == "sd");
        runTest("livestream dock applies runtime profile setting",
                liveProfile->currentData().toString() == "valorant");
        runTest("livestream dock applies runtime output directory",
                liveOutputDir->text().endsWith("D:/LSC/LiveRuntime")
                    || liveOutputDir->text() == "D:/LSC/LiveRuntime");
    }

    LOG("");
    LOG(QString("=== Results: %1/%2 passed, %3 failed ===")
        .arg(g_passCount).arg(g_testCount).arg(g_failCount));
    return g_failCount > 0 ? 1 : 0;
}
