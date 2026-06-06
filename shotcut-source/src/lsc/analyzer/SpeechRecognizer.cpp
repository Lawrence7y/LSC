#include "SpeechRecognizer.h"
#include "LscConfig.h"
#include "LscLog.h"
#include <QDebug>
#include <QFileInfo>
#include <QDir>
#include <QRegularExpression>
#include <QFile>
#include <QTime>

#define MODULE_NAME "SpeechRecognizer"

SpeechRecognizer::SpeechRecognizer(QObject* parent)
    : QObject(parent)
    , m_process(new QProcess(this))
{
    const auto& cfg = lsc::LscConfig::instance();
    m_modelPath = cfg.whisperDefaultModel;
    m_language = cfg.whisperDefaultLanguage;

    connect(m_process, QOverload<int, QProcess::ExitStatus>::of(&QProcess::finished),
            this, &SpeechRecognizer::onProcessFinished);
    connect(m_process, &QProcess::errorOccurred,
            this, &SpeechRecognizer::onProcessError);
}

void SpeechRecognizer::setModelPath(const QString& path)
{
    m_modelPath = path;
}

void SpeechRecognizer::setLanguage(const QString& lang)
{
    m_language = lang;
}

/**
 * @brief 开始语音识别
 *
 * whisper-cli 命令行参数：
 * -m <model>    : 模型文件路径
 * -l <lang>     : 语言代码（zh, en, ja 等）
 * -f <file>     : 输入音频/视频文件
 * -osrt         : 输出 SRT 格式字幕
 * -of <output>  : 输出文件路径（不含扩展名，whisper 会自动添加 .srt）
 */
void SpeechRecognizer::transcribe(const QString& audioPath)
{
    if (m_running) {
        emit errorOccurred(QString::fromUtf8("识别已在进行中"));
        return;
    }

    QFileInfo fi(audioPath);
    if (!fi.exists()) {
        emit errorOccurred(QString::fromUtf8("文件不存在: %1").arg(audioPath));
        return;
    }

    m_subtitles.clear();
    m_running = true;

    // SRT 输出路径：在源文件同目录下生成
    m_srtPath = audioPath + ".srt";

    QStringList args;
    args << "-m" << m_modelPath
         << "-l" << m_language
         << "-f" << audioPath
         << "-osrt"
         << "-of" << audioPath;  // whisper 会自动加 .srt 后缀

    LSC_INFO(MODULE_NAME) << "启动语音识别, 模型:" << m_modelPath
                          << ", 语言:" << m_language;

    m_process->setProgram("whisper-cli");
    m_process->setArguments(args);
    m_process->start();
}

void SpeechRecognizer::cancel()
{
    if (m_process->state() == QProcess::Running) {
        m_process->kill();
        m_process->waitForFinished(3000);
    }
    m_running = false;
}

bool SpeechRecognizer::isRunning() const
{
    return m_running;
}

/**
 * @brief whisper-cli 进程完成回调
 *
 * whisper-cli 的特殊行为：
 * - 信息和进度都输出到 stderr
 * - 即使 exitCode != 0，也可能已经成功生成了 SRT 文件
 * - 需要检查 SRT 文件是否存在来判断是否真正成功
 */
void SpeechRecognizer::onProcessFinished(int exitCode, QProcess::ExitStatus status)
{
    m_running = false;

    if (exitCode != 0 || status != QProcess::NormalExit) {
        QString err = QString::fromUtf8(m_process->readAllStandardError());
        LSC_WARNING(MODULE_NAME) << "whisper-cli 退出码:" << exitCode;

        // whisper 有时返回非零退出码但仍成功生成了 SRT
        QFileInfo srt(m_srtPath);
        if (srt.exists() && srt.size() > 0) {
            LSC_INFO(MODULE_NAME) << "whisper 退出码非零，但 SRT 文件已生成";
            QFile f(srt.absoluteFilePath());
            if (f.open(QIODevice::ReadOnly)) {
                parseSrtOutput(QString::fromUtf8(f.readAll()));
                f.close();
                cleanupSrtFile();
                emit finished();
                return;
            }
        }

        LSC_ERROR(MODULE_NAME) << "Whisper 失败:" << err.left(200);
        cleanupSrtFile();
        emit errorOccurred(QString::fromUtf8("Whisper 失败: %1").arg(err.left(200)));
        return;
    }

    // 正常完成，读取 SRT 文件
    QFile srtFile(m_srtPath);
    if (srtFile.open(QIODevice::ReadOnly)) {
        parseSrtOutput(QString::fromUtf8(srtFile.readAll()));
        srtFile.close();
        LSC_INFO(MODULE_NAME) << "识别完成, 字幕条数:" << m_subtitles.size();
    } else {
        LSC_ERROR(MODULE_NAME) << "无法读取 SRT 文件:" << m_srtPath;
        cleanupSrtFile();
        emit errorOccurred(QString::fromUtf8("无法读取 SRT 文件: %1").arg(m_srtPath));
        return;
    }

    cleanupSrtFile();
    emit finished();
}

void SpeechRecognizer::cleanupSrtFile()
{
    if (!m_srtPath.isEmpty()) {
        QFile::remove(m_srtPath);
        m_srtPath.clear();
    }
}

void SpeechRecognizer::onProcessError(QProcess::ProcessError error)
{
    Q_UNUSED(error)
    m_running = false;
    LSC_ERROR(MODULE_NAME) << "whisper-cli 进程错误:" << m_process->errorString();
    emit errorOccurred(QString::fromUtf8("Whisper 进程错误: %1").arg(m_process->errorString()));
}

/**
 * @brief 解析 SRT 字幕文件
 *
 * SRT 格式：
 * 1
 * 00:00:01,000 --> 00:00:03,500
 * 你好世界
 *
 * 2
 * 00:00:04,000 --> 00:00:06,200
 * 这是一个测试
 *
 * 正则表达式匹配每个字幕块的：序号、开始时间、结束时间、文字内容。
 */
void SpeechRecognizer::parseSrtOutput(const QString& srtContent)
{
    m_subtitles.clear();
    const auto& cfg = lsc::LscConfig::instance();

    QRegularExpression re(
        R"((\d+)\s*\n(\d{2}:\d{2}:\d{2}[,\.]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[,\.]\d{3})\s*\n([\s\S]*?)(?=\n\n|\Z))");

    QRegularExpressionMatchIterator it = re.globalMatch(srtContent);
    while (it.hasNext()) {
        QRegularExpressionMatch match = it.next();

        SubtitleEntry entry;
        // 解析时间戳：将 HH:MM:SS,mmm 转换为秒
        entry.startSec = QTime::fromString(
            match.captured(2).left(12).replace(',', '.'), "hh:mm:ss.zzz")
            .msecsSinceStartOfDay() / 1000.0;
        entry.endSec = QTime::fromString(
            match.captured(3).left(12).replace(',', '.'), "hh:mm:ss.zzz")
            .msecsSinceStartOfDay() / 1000.0;
        entry.text = match.captured(4).trimmed();
        entry.confidence = cfg.whisperDefaultConfidence;

        m_subtitles.append(entry);
    }

    LSC_DEBUG(MODULE_NAME) << "解析 SRT 完成, 条目数:" << m_subtitles.size();
}

#undef MODULE_NAME
