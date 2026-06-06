#include "ErrorManager.h"

namespace lsc {

ErrorManager::ErrorManager()
{
    initErrorDefinitions();
}

ErrorManager& ErrorManager::instance()
{
    static ErrorManager mgr;
    return mgr;
}

void ErrorManager::reportError(const QString& code, const QString& message,
                                ErrorSeverity severity,
                                RecoveryAction recovery,
                                const QString& technicalDetail)
{
    ErrorInfo info;
    info.code = code;
    info.severity = severity;
    info.message = message;
    info.technicalDetail = technicalDetail;
    info.recovery = recovery;
    info.recoveryHint = m_recoveryHints.value(code);
    info.timestamp = QDateTime::currentDateTime();

    while (m_errors.size() >= m_maxErrors)
        m_errors.dequeue();

    m_errors.enqueue(info);

    emit errorReported(info);
    emit errorCountChanged(m_errors.size());
}

QVector<ErrorInfo> ErrorManager::recentErrors(int count) const
{
    const int n = qMin(count, m_errors.size());
    QVector<ErrorInfo> result;
    result.reserve(n);
    for (int i = m_errors.size() - n; i < m_errors.size(); ++i)
        result.append(m_errors.at(i));
    return result;
}

ErrorInfo ErrorManager::lastError() const
{
    if (m_errors.isEmpty())
        return {};
    return m_errors.last();
}

bool ErrorManager::hasErrors() const
{
    return !m_errors.isEmpty();
}

void ErrorManager::clearErrors()
{
    const int oldCount = m_errors.size();
    m_errors.clear();
    if (oldCount > 0)
        emit errorCountChanged(0);
}

QString ErrorManager::userMessage(const QString& code) const
{
    return m_userMessages.value(code, code);
}

QString ErrorManager::recoveryHint(const QString& code) const
{
    return m_recoveryHints.value(code);
}

void ErrorManager::initErrorDefinitions()
{
    m_userMessages[QStringLiteral("STREAM_EXPIRED")]       = QStringLiteral("直播流已过期");
    m_recoveryHints[QStringLiteral("STREAM_EXPIRED")]      = QStringLiteral("请刷新页面获取新的直播链接");

    m_userMessages[QStringLiteral("STREAM_DISCONNECTED")]  = QStringLiteral("直播连接断开");
    m_recoveryHints[QStringLiteral("STREAM_DISCONNECTED")] = QStringLiteral("正在自动重连...");

    m_userMessages[QStringLiteral("STREAM_403")]           = QStringLiteral("访问被拒绝");
    m_recoveryHints[QStringLiteral("STREAM_403")]          = QStringLiteral("可能需要登录或使用其他清晰度");

    m_userMessages[QStringLiteral("STREAM_OFFLINE")]       = QStringLiteral("主播已下播");
    m_recoveryHints[QStringLiteral("STREAM_OFFLINE")]      = QStringLiteral("录制已自动停止");

    m_userMessages[QStringLiteral("FFMPEG_ERROR")]         = QStringLiteral("视频处理失败");
    m_recoveryHints[QStringLiteral("FFMPEG_ERROR")]        = QStringLiteral("请检查文件格式或尝试重新处理");

    m_userMessages[QStringLiteral("FFMPEG_DISK_FULL")]     = QStringLiteral("磁盘空间不足");
    m_recoveryHints[QStringLiteral("FFMPEG_DISK_FULL")]    = QStringLiteral("请清理磁盘空间后重试");

    m_userMessages[QStringLiteral("ASR_TIMEOUT")]          = QStringLiteral("语音识别超时");
    m_recoveryHints[QStringLiteral("ASR_TIMEOUT")]         = QStringLiteral("可以尝试使用更小的模型或跳过语音识别");

    m_userMessages[QStringLiteral("ASR_MODEL_NOT_FOUND")]  = QStringLiteral("语音识别模型未找到");
    m_recoveryHints[QStringLiteral("ASR_MODEL_NOT_FOUND")] = QStringLiteral("请下载 Whisper 模型文件");

    m_userMessages[QStringLiteral("FILE_CORRUPTED")]       = QStringLiteral("文件损坏");
    m_recoveryHints[QStringLiteral("FILE_CORRUPTED")]      = QStringLiteral("请尝试重新录制");

    m_userMessages[QStringLiteral("FILE_NOT_FOUND")]       = QStringLiteral("文件未找到");
    m_recoveryHints[QStringLiteral("FILE_NOT_FOUND")]      = QStringLiteral("文件可能已被移动或删除");
}

} // namespace lsc
