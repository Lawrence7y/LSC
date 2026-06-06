#ifndef LSCCONFIG_H
#define LSCCONFIG_H

/**
 * @file LscConfig.h
 * @brief 全局配置中心 — 所有可调参数的单一来源
 *
 * 设计原则：
 * 1. 所有魔法数字必须提取到此处，附带注释说明取值依据
 * 2. 支持通过 LscConfig::instance() 在运行时动态修改
 * 3. 每个参数都有合理的默认值，开箱即用
 */

#include <QCoreApplication>
#include <QDir>
#include <QFileInfo>
#include <QString>
#include <QStringList>

#include <utility>

namespace lsc {

/**
 * @brief 全局配置单例
 *
 * 使用方法：
 *   auto& cfg = LscConfig::instance();
 *   cfg.setSilenceThresholdDb(-45.0);
 */
class LscConfig
{
public:
    static LscConfig& instance()
    {
        static LscConfig s_instance;
        return s_instance;
    }

    QString ffmpegProgram() const
    {
        return resolveExternalTool(QStringLiteral("LSC_FFMPEG"),
                                   QStringLiteral("ffmpeg.exe"),
                                   QStringLiteral("ffmpeg"));
    }

    QString ffprobeProgram() const
    {
        return resolveExternalTool(QStringLiteral("LSC_FFPROBE"),
                                   QStringLiteral("ffprobe.exe"),
                                   QStringLiteral("ffprobe"));
    }

    QString ytDlpProgram() const
    {
        return resolveExternalTool(QStringLiteral("LSC_YTDLP"),
                                   QStringLiteral("yt-dlp.exe"),
                                   QStringLiteral("yt-dlp"));
    }

    // ===== 录制模块配置 =====

    /// 默认输出目录（相对于用户主目录）
    QString defaultOutputSubdir = "Videos/LiveRecordings";

    /// 默认录制格式
    QString defaultFormat = "mp4";

    /// 默认源流画质：best/origin/hd/sd/ld/source
    QString defaultSourceQuality = "best";

    /// 默认分析 profile：generic/dance/valorant/commentary
    QString defaultAnalysisProfile = "generic";

    /// 默认游戏 key
    QString defaultGameKey = "valorant";

    /// 默认使用流拷贝模式（不重新编码，性能最优）
    bool defaultUseStreamCopy = true;

    /// 默认自动重连
    bool defaultAutoReconnect = true;

    /// 录制完成后是否自动分析
    bool defaultAutoAnalyze = true;

    /// 是否默认启用 ASR
    bool defaultEnableASR = false;

    /// 最大重连次数 — 5次足以覆盖大多数临时网络中断
    int defaultReconnectRetries = 5;

    /// 重连基础延迟（毫秒）— 给服务器恢复的时间
    int defaultReconnectDelayMs = 3000;

    /// 最大重连延迟（毫秒）— 指数退避上限
    int maxReconnectDelayMs = 30000;

    /// FFmpeg rw_timeout（微秒）— 网络读写超时，10秒
    /// 说明：直播流通常每几秒就有数据包，10秒无数据说明连接已断
    int ffmpegRwTimeoutUs = 10000000;

    /// stall 检测间隔（毫秒）— 每5秒检查一次文件大小变化
    int stallCheckIntervalMs = 5000;

    /// stall 触发阈值（秒）— 连续N秒文件大小不变视为 stall
    int stallTimeoutSec = 30;

    /// 进度上报间隔（毫秒）
    int progressIntervalMs = 1000;

    /// 录制时长上限（秒）— 0 表示无限制，防止意外无限录制
    /// 默认12小时，覆盖绝大多数直播场景
    int maxRecordingDurationSec = 0;

    /// yt-dlp 超时（毫秒）
    int ytDlpTimeoutMs = 15000;

    // ===== 分析模块配置 =====

    /// 静音检测阈值（dB）— 低于此值视为静音
    /// -50dB 适用于大多数场景；安静环境可调至 -40dB，嘈杂环境可调至 -60dB
    double silenceThresholdDb = -50.0;

    /// 最小静音持续时间（秒）— 短于此值的静音不计入
    double minSilenceDurationSec = 0.5;

    /// 场景变化检测阈值 — 0.1 适用于大多数内容
    /// 较低值更敏感（适合谈话类），较高值更严格（适合动作类）
    double sceneChangeThreshold = 0.1;

    /// 运动检测阈值 — 略高于场景变化，过滤噪声
    double motionThreshold = 0.15;

    /// 场景变化分组窗口（秒）— 在此时间内的场景变化归为同一运动段
    double motionGroupWindowSec = 5.0;

    /// 运动强度归一化基准 — scene_score 达到此值视为运动强度 1.0
    double motionNormalizationBase = 3.0;

    // ===== 高光检测配置 =====

    /// 滑动窗口大小（秒）— 每个分析窗口的时长
    double highlightWindowSec = 5.0;

    /// 滑动步长（秒）— 窗口移动的步长，越小越精细但越慢
    double highlightStepSec = 1.0;

    /// 高光阈值 — 综合评分超过此值才标记为高光
    double highlightThreshold = 0.2;

    /// 短视频阈值（秒）— 低于此值使用单窗口模式
    double shortVideoThresholdSec = 6.0;

    /// 短视频高光阈值倍率 — 短视频使用更低的阈值
    double shortVideoThresholdMultiplier = 0.5;

    /// 高光合并间隔（秒）— 间距小于此值的相邻高光合并
    double highlightMergeGapSec = 0.5;

    /// 音频评分权重（通用策略）
    double weightAudio = 0.4;

    /// 视频评分权重（通用策略）
    double weightVideo = 0.3;

    /// 语音评分权重（通用策略）
    double weightSpeech = 0.3;

    /// 音频评分权重（HighlightDetector 旧版）
    double weightAudioDetector = 0.5;

    /// 视频评分权重（HighlightDetector 旧版）
    double weightVideoDetector = 0.3;

    /// 语音评分权重（HighlightDetector 旧版）
    double weightSpeechDetector = 0.2;

    /// 短视频音频权重（短视频音频更重要）
    double weightAudioShort = 0.5;

    /// 短视频视频权重
    double weightVideoShort = 0.3;

    /// 短视频语音权重
    double weightSpeechShort = 0.2;

    /// 高光检测最低分数阈值
    double highlightMinScore = 0.3;

    /// 音频响度映射下限（dB）— 低于此值评分为 0
    double audioScoreFloorDb = -70.0;

    /// 音频响度映射上限（dB）— 高于此值评分为 1
    double audioScoreCeilDb = -25.0;

    /// 关键词匹配基础分 — 每个匹配的关键词贡献此分数
    double keywordMatchBaseScore = 0.3;

    /// 无关键词时的默认语音评分 — 0.0 表示中性（不影响总分）
    double noKeywordSpeechScore = 0.0;

    /// 场景变化时间匹配窗口（秒）
    double sceneChangeMatchWindowSec = 2.0;

    /// 高光片段重叠阈值 — 超过此值认为是重复片段
    double highlightOverlapThreshold = 0.35;

    /// 高光片段相邻合并间隔（秒）
    double highlightAdjacentGapSec = 0.8;

    /// Whisper 默认模型路径
    QString whisperDefaultModel = "models/ggml-base.bin";

    /// Whisper 默认语言
    QString whisperDefaultLanguage = "zh";

    /// 导出文件名模板
    QString exportFilenameTemplate = "{source}_{index}_{time}";

    /// 默认导出分辨率
    QString defaultExportResolution = "original";

    /// Whisper 置信度默认值（SRT 输出不含置信度信息）
    double whisperDefaultConfidence = 0.9;

    /// Whisper 超时（毫秒）
    int whisperTimeoutMs = 300000;

    // ===== UI 配置 =====

    /// URL 输入框占位符文本
    QString urlPlaceholder = "输入直播链接，如 https://live.douyin.com/53682367755";

    /// 默认关键词列表
    QStringList defaultKeywords = {"精彩", "666", "牛逼", "击杀", "获胜"};

    // ===== Valorant 试点配置 =====

    /// 判型信号最低强度 — 低于此值直接判为 uncertain
    double classificationSignalFloor = 0.2;

    /// 判型置信度阈值 — 相对差异低于此值判为 uncertain
    double classificationConfidenceThreshold = 0.2;

    /// Ranker 去重重叠阈值 — 超过此值认为是重复片段
    double rankerMergeOverlapThreshold = 0.35;

    /// CompositeHighlightStrategy 在 Ranker 存在时的去重阈值（仅合并几乎完全重叠者）
    double compositeMergeOverlapThresholdWhenRankerEnabled = 0.7;

    /// Valorant 母片最小长度（秒）
    double motherClipMinSecValorant = 45.0;

    /// Valorant 母片最大长度（秒）
    double motherClipMaxSecValorant = 120.0;

    /// 短高光最小长度（秒）
    double shortClipMinSec = 15.0;

    /// 短高光最大长度（秒）
    double shortClipMaxSec = 45.0;

    /// 短高光滑动窗口步长（秒）
    double shortClipStepSec = 0.5;

    /// 短高光前后缓冲（秒）
    double shortClipPaddingSec = 2.0;

    /// 无畏契约热词表
    QStringList valorantHotwords = {
        "ace", "1v1", "1v2", "1v3", "翻盘", "赛点", "残局", "爆能器",
        "炼狱", "霓虹", "捷风", "幻棱", "狂徒", "冥驹", "准星"
    };

private:
    QString resolveExternalTool(const QString& envVar,
                                const QString& windowsExeName,
                                const QString& fallbackCommand) const
    {
        const QString fromEnv = qEnvironmentVariable(envVar.toUtf8().constData()).trimmed();
        if (!fromEnv.isEmpty() && QFileInfo::exists(fromEnv)) {
            return QDir::toNativeSeparators(fromEnv);
        }

        const QString appDir = QCoreApplication::applicationDirPath();
        QStringList candidates{
            QDir(appDir).filePath(windowsExeName),
            QDir(appDir).filePath(QStringLiteral("bin/%1").arg(windowsExeName)),
            QDir(appDir).filePath(QStringLiteral("tools/%1").arg(windowsExeName)),
            QDir(appDir).filePath(QStringLiteral("../bin/%1").arg(windowsExeName)),
            QDir(appDir).filePath(QStringLiteral("../tools/%1").arg(windowsExeName)),
            QDir(appDir).filePath(QStringLiteral("../deps/ffmpeg/ffmpeg-master-latest-win64-gpl-shared/bin/%1")
                                      .arg(windowsExeName)),
            QDir(appDir).filePath(QStringLiteral("../../deps/ffmpeg/ffmpeg-master-latest-win64-gpl-shared/bin/%1")
                                      .arg(windowsExeName)),
            QDir(appDir).filePath(QStringLiteral("../../../deps/ffmpeg/ffmpeg-master-latest-win64-gpl-shared/bin/%1")
                                      .arg(windowsExeName)),
            // Also check deps/ relative to workspace root
            QDir(appDir).filePath(QStringLiteral("../../deps/bin/%1").arg(windowsExeName)),
            QDir(appDir).filePath(QStringLiteral("../../../deps/bin/%1").arg(windowsExeName)),
        };

        for (const QString& candidate : std::as_const(candidates)) {
            if (QFileInfo::exists(candidate)) {
                return QDir::toNativeSeparators(QFileInfo(candidate).absoluteFilePath());
            }
        }

        // Check if the tool is available in PATH
        const QString pathEnv = qEnvironmentVariable("PATH");
        const QStringList pathDirs = pathEnv.split(';', Qt::SkipEmptyParts);
        for (const QString& dir : pathDirs) {
            const QString fullPath = QDir(dir).filePath(windowsExeName);
            if (QFileInfo::exists(fullPath)) {
                return QDir::toNativeSeparators(fullPath);
            }
            // Also check without .exe extension for non-Windows
            const QString fullPathNoExt = QDir(dir).filePath(windowsExeName.section('.', 0, 0));
            if (QFileInfo::exists(fullPathNoExt)) {
                return QDir::toNativeSeparators(fullPathNoExt);
            }
        }

        return fallbackCommand;
    }

    LscConfig() = default;
    ~LscConfig() = default;
    LscConfig(const LscConfig&) = delete;
    LscConfig& operator=(const LscConfig&) = delete;
};

} // namespace lsc

#endif // LSCCONFIG_H
