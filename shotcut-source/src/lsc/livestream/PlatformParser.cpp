#include "PlatformParser.h"
#include "LscConfig.h"
#include "LscLog.h"
#include "livestream/IPlatformParser.h"
#include "livestream/platforms/BilibiliParser.h"
#include "livestream/platforms/DouyinParser.h"

#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QNetworkReply>
#include <QNetworkRequest>
#include <QProcess>

#define MODULE_NAME "PlatformParser"

namespace {
QString pickFirstString(const QJsonObject& root, const QStringList& paths)
{
    for (const QString& path : paths) {
        QJsonValue current(root);
        const QStringList parts = path.split('.');
        bool valid = true;
        for (const QString& part : parts) {
            if (!current.isObject()) {
                valid = false;
                break;
            }
            current = current.toObject().value(part);
        }
        if (!valid) {
            continue;
        }

        const QString value = current.toString().trimmed();
        if (!value.isEmpty()) {
            return value;
        }
    }

    return {};
}

void appendQuality(PlatformInfo& info, const QString& quality, const QString& url)
{
    const QString key = quality.trimmed().toLower();
    if (key.isEmpty() || url.isEmpty() || info.availableStreams.contains(key)) {
        return;
    }

    info.availableStreams.insert(key, url);
    info.availableQualities.append(key);
    if (info.preferredQuality.isEmpty()) {
        info.preferredQuality = key;
    }
}

bool isLikelyStreamUrl(const QString& url)
{
    return url.startsWith("http://") || url.startsWith("https://");
}

PlatformInfo toPlatformInfo(const lsc::StreamInfo& streamInfo)
{
    PlatformInfo info;
    info.platform = streamInfo.platform;
    info.streamUrl = streamInfo.streamUrl;
    info.backupStreamUrl = streamInfo.backupStreamUrl.isEmpty()
        ? streamInfo.streamUrl
        : streamInfo.backupStreamUrl;
    info.roomId = streamInfo.roomId;
    info.title = streamInfo.title;
    info.streamerName = streamInfo.streamerName;
    info.preferredQuality = streamInfo.selectedQuality;
    info.availableQualities = streamInfo.availableQualities;
    info.isValid = streamInfo.isLive && isLikelyStreamUrl(streamInfo.streamUrl);

    if (info.preferredQuality.isEmpty() && !info.availableQualities.isEmpty()) {
        info.preferredQuality = info.availableQualities.first();
    }
    if (info.availableQualities.isEmpty() && !info.preferredQuality.isEmpty()) {
        info.availableQualities.append(info.preferredQuality);
    }
    if (info.availableQualities.isEmpty() && !info.streamUrl.isEmpty()) {
        info.preferredQuality = "best";
        info.availableQualities.append(info.preferredQuality);
    }
    for (const QString& quality : std::as_const(info.availableQualities)) {
        appendQuality(info, quality, info.streamUrl);
    }
    if (!info.preferredQuality.isEmpty() && !info.availableStreams.contains(info.preferredQuality)) {
        appendQuality(info, info.preferredQuality, info.streamUrl);
    }
    return info;
}
}

PlatformParser::PlatformParser(QObject* parent)
    : QObject(parent)
    , m_nam(new QNetworkAccessManager(this))
    , m_cookieJar(new QNetworkCookieJar(this))
    , m_ytdlpProcess(new QProcess(this))
{
    m_nam->setCookieJar(m_cookieJar);
    m_nam->setRedirectPolicy(QNetworkRequest::NoLessSafeRedirectPolicy);

    connect(m_nam, &QNetworkAccessManager::finished, this, &PlatformParser::onPageFetched);
    connect(m_ytdlpProcess,
            QOverload<int, QProcess::ExitStatus>::of(&QProcess::finished),
            this,
            &PlatformParser::onYtDlpFinished);
}

void PlatformParser::setCookieJar(QNetworkCookieJar* jar)
{
    if (!jar) {
        return;
    }

    m_cookieJar = jar;
    m_nam->setCookieJar(m_cookieJar);
}

QString PlatformParser::detectPlatform(const QUrl& url)
{
    if (url.isLocalFile()) {
        return "direct";
    }

    const QString urlString = url.toString().toLower();
    if (urlString.endsWith(".m3u8")
        || urlString.endsWith(".flv")
        || urlString.endsWith(".mp4")
        || urlString.endsWith(".ts")) {
        return "direct";
    }

    const QString host = url.host().toLower();
    if (host.contains("douyin.com") || host.contains("tiktok.com")) {
        return "douyin";
    }
    if (host.contains("kuaishou.com")) {
        return "kuaishou";
    }
    if (host.contains("bilibili.com") || host.contains("b23.tv")) {
        return "bilibili";
    }
    if (host.contains("youtube.com") || host.contains("youtu.be")) {
        return "youtube";
    }
    if (host.contains("twitch.tv")) {
        return "twitch";
    }

    return QString();
}

QString PlatformParser::normalizeError(const QString& rawError, const QString& platform)
{
    const QString lower = rawError.toLower();
    const QString prefix = platform.isEmpty()
        ? QStringLiteral("平台解析失败")
        : QStringLiteral("%1 平台解析失败").arg(platform);

    if (lower.contains("not live") || lower.contains("offline")
        || lower.contains("未开播") || lower.contains("下播")) {
        return QStringLiteral("[not_live] %1：直播间当前未开播或已下播，请换一个正在直播的房间。原始错误：%2")
            .arg(prefix, rawError);
    }
    if (lower.contains("login") || lower.contains("cookie")
        || lower.contains("permission") || lower.contains("403")
        || lower.contains("风控")) {
        return QStringLiteral("[login_required] %1：平台可能要求登录 Cookie 或触发风控，请在浏览器确认房间可播放后重试。原始错误：%2")
            .arg(prefix, rawError);
    }
    if (lower.contains("timeout") || lower.contains("timed out")
        || lower.contains("network") || lower.contains("connection")
        || lower.contains("host")) {
        return QStringLiteral("[network_error] %1：网络连接失败或超时，请检查网络/代理后重试。原始错误：%2")
            .arg(prefix, rawError);
    }
    if (lower.contains("room id") || lower.contains("short url")
        || lower.contains("redirect")) {
        return QStringLiteral("[url_unresolved] %1：无法解析房间号或短链跳转，请使用直播间完整链接重试。原始错误：%2")
            .arg(prefix, rawError);
    }

    return QStringLiteral("[parse_failed] %1：未能获取可录制直播流。原始错误：%2")
        .arg(prefix, rawError);
}

void PlatformParser::parseUrl(const QString& url)
{
    QUrl qurl = QUrl::fromUserInput(url);
    if (!qurl.isValid()) {
        emit parseError(QString::fromUtf8("无效的 URL: %1").arg(url));
        return;
    }

    const QString platform = detectPlatform(qurl);
    if (platform.isEmpty()) {
        emit parseError(QString::fromUtf8("无法识别平台: %1").arg(url));
        return;
    }

    if (platform == "direct") {
        PlatformInfo info;
        info.platform = platform;
        info.streamUrl = qurl.isLocalFile() ? qurl.toLocalFile() : qurl.toString();
        info.backupStreamUrl = info.streamUrl;
        info.title = qurl.fileName();
        info.roomId = qurl.fileName();
        info.streamerName = "direct";
        appendQuality(info, "source", info.streamUrl);
        info.preferredQuality = "source";
        info.isValid = !info.streamUrl.isEmpty();
        if (!info.isValid) {
            info.errorMsg = QString::fromUtf8("无法解析直链流地址");
            emit parseError(info.errorMsg);
            return;
        }
        emit parseComplete(info);
        return;
    }

    if (platform == "douyin") {
        if (!parseWithNativeParser(url, platform)) {
            parseDouyinLive(qurl);
        }
        return;
    }

    if (platform == "bilibili") {
        if (!parseWithNativeParser(url, platform)) {
            parseWithYtDlp(url, platform);
        }
        return;
    }

    parseWithYtDlp(url, platform);
}

bool PlatformParser::parseWithNativeParser(const QString& url, const QString& platform)
{
    if (m_nativeParser) {
        m_nativeParser->cancel();
        m_nativeParser->deleteLater();
        m_nativeParser = nullptr;
    }

    if (platform == "douyin") {
        m_nativeParser = new lsc::DouyinParser(this);
    } else if (platform == "bilibili") {
        m_nativeParser = new lsc::BilibiliParser(this);
    }

    if (!m_nativeParser || !m_nativeParser->canParse(url)) {
        return false;
    }

    connect(m_nativeParser, &lsc::IPlatformParser::parseComplete, this,
            [this](const lsc::StreamInfo& streamInfo) {
                PlatformInfo info = toPlatformInfo(streamInfo);
                if (info.isValid) {
                    emit parseComplete(info);
                } else {
                    emit parseError(QString::fromUtf8("平台解析未返回有效直播流"));
                }
            });
    connect(m_nativeParser, &lsc::IPlatformParser::parseError,
            this, [this, platform](const QString& error) {
                emit parseError(normalizeError(error, platform));
            });

    m_nativeParser->parse(url);
    return true;
}

void PlatformParser::parseDouyinLive(const QUrl& url)
{
    QNetworkRequest request(url);
    request.setRawHeader(
        "User-Agent",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36");
    request.setRawHeader(
        "Accept",
        "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8");
    request.setRawHeader("Accept-Language", "zh-CN,zh;q=0.9,en;q=0.8");
    request.setAttribute(QNetworkRequest::RedirectPolicyAttribute,
                         QNetworkRequest::NoLessSafeRedirectPolicy);
    request.setTransferTimeout(lsc::LscConfig::instance().ffmpegRwTimeoutUs / 1000);

    m_nam->get(request);
}

void PlatformParser::onPageFetched(QNetworkReply* reply)
{
    reply->deleteLater();

    if (reply->error() != QNetworkReply::NoError) {
        emit parseError(QString::fromUtf8("页面请求失败: %1").arg(reply->errorString()));
        return;
    }

    const QByteArray html = reply->readAll();
    const QString reqUrl = reply->request().url().toString();
    const QString platform = detectPlatform(QUrl(reqUrl));

    if (platform != "douyin") {
        emit parseError(QString::fromUtf8("暂不支持该页面解析平台: %1").arg(platform));
        return;
    }

    const PlatformInfo info = extractDouyinFromHtml(html, reqUrl);
    if (info.isValid) {
        emit parseComplete(info);
    } else {
        emit parseError(info.errorMsg.isEmpty()
                            ? QString::fromUtf8("无法从页面提取直播流地址")
                            : info.errorMsg);
    }
}

PlatformInfo PlatformParser::extractDouyinFromHtml(const QByteArray& html, const QString& url)
{
    PlatformInfo info = extractDouyinSsrData(html);
    if (info.isValid) {
        info.platform = "douyin";
        if (info.roomId.isEmpty()) {
            info.roomId = QUrl(url).path().section('/', -1);
        }
        return info;
    }

    info.platform = "douyin";
    info.isValid = false;
    info.errorMsg = QString::fromUtf8("SSR 数据解析失败，直播可能未开播或需要登录。");
    return info;
}

PlatformInfo PlatformParser::extractDouyinSsrData(const QByteArray& html)
{
    PlatformInfo info;
    info.platform = "douyin";

    const QString htmlStr = QString::fromUtf8(html);
    const QString prefix = "self.__pace_f.push([1,\"";

    int searchPos = 0;
    while (searchPos < htmlStr.length()) {
        int startIdx = htmlStr.indexOf(prefix, searchPos);
        if (startIdx < 0) {
            break;
        }

        startIdx += prefix.length();
        int endIdx = htmlStr.indexOf("\"])", startIdx);
        if (endIdx < 0) {
            endIdx = htmlStr.indexOf("\"])</script>", startIdx);
        }
        if (endIdx < 0) {
            searchPos = startIdx;
            continue;
        }

        QString jsonStr = htmlStr.mid(startIdx, endIdx - startIdx);
        if (!jsonStr.contains("stream_name")) {
            searchPos = endIdx + 3;
            continue;
        }

        jsonStr.replace("\\\"", "\"");
        jsonStr.replace("\\\\", "\\");
        jsonStr.replace("\\/", "/");

        QJsonParseError parseError;
        const QJsonDocument doc = QJsonDocument::fromJson(jsonStr.toUtf8(), &parseError);
        if (parseError.error != QJsonParseError::NoError || !doc.isObject()) {
            searchPos = endIdx + 3;
            continue;
        }

        const QJsonObject data = doc.object().value("data").toObject();
        info.title = pickFirstString(data, {"title", "room.title", "seo_title"});
        info.streamerName = pickFirstString(data, {"owner.nickname", "anchor.nickname", "nickname"});
        info.roomId = pickFirstString(data, {"room_id", "roomId", "room.id", "web_rid"});
        const QStringList qualityKeys = {"origin", "uhd", "hd", "sd", "ld", "ao"};
        for (const QString& quality : qualityKeys) {
            const QJsonObject main = data.value(quality).toObject().value("main").toObject();
            QString flvUrl = main.value("flv").toString();
            QString hlsUrl = main.value("hls").toString();
            flvUrl.replace("\\u0026", "&");
            hlsUrl.replace("\\u0026", "&");

            const QString preferredUrl = isValidStreamUrl(flvUrl) ? flvUrl : hlsUrl;
            if (!isValidStreamUrl(preferredUrl)) {
                continue;
            }

            appendQuality(info, quality, preferredUrl);
            if (info.streamUrl.isEmpty()) {
                info.streamUrl = preferredUrl;
                info.backupStreamUrl = isValidStreamUrl(hlsUrl) ? hlsUrl : preferredUrl;
                info.isValid = true;
            }
        }

        if (info.isValid) {
            return info;
        }

        searchPos = endIdx + 3;
    }

    return info;
}

bool PlatformParser::isValidStreamUrl(const QString& url)
{
    return url.startsWith("http://") || url.startsWith("https://");
}

void PlatformParser::parseWithYtDlp(const QString& url, const QString& platform)
{
    const auto& cfg = lsc::LscConfig::instance();
    m_ytdlpPlatform = platform;
    m_ytdlpUrl = url;
    m_ytdlpFallbackPhase = false;

    m_ytdlpProcess->setProgram(cfg.ytDlpProgram());
    m_ytdlpProcess->setArguments({"--dump-single-json", "--no-playlist", "--skip-download", url});
    m_ytdlpProcess->start();
}

void PlatformParser::onYtDlpFinished(int exitCode, QProcess::ExitStatus status)
{
    const auto& cfg = lsc::LscConfig::instance();

    if (m_ytdlpFallbackPhase) {
        // Fallback phase: -g output
        if (exitCode != 0 || status != QProcess::NormalExit) {
            const QString err = QString::fromUtf8(m_ytdlpProcess->readAllStandardError()).trimmed();
            emit parseError(
                QString::fromUtf8("yt-dlp 解析失败（平台: %1）: %2").arg(m_ytdlpPlatform, err.left(200)));
            return;
        }

        PlatformInfo info;
        info.platform = m_ytdlpPlatform;

        const QStringList lines =
            QString::fromUtf8(m_ytdlpProcess->readAllStandardOutput()).split('\n', Qt::SkipEmptyParts);
        for (const QString& line : lines) {
            const QString resolvedUrl = line.trimmed();
            if (!isValidStreamUrl(resolvedUrl)) {
                continue;
            }
            appendQuality(info, "best", resolvedUrl);
            break;
        }

        if (info.availableStreams.isEmpty()) {
            emit parseError(QString::fromUtf8("yt-dlp 未返回有效的流地址（平台: %1）").arg(m_ytdlpPlatform));
            return;
        }

        info.streamUrl = info.availableStreams.value(info.availableStreams.firstKey());
        info.backupStreamUrl = info.streamUrl;
        info.isValid = true;
        emit parseComplete(info);
        return;
    }

    // Primary phase: --dump-single-json output
    PlatformInfo info;
    info.platform = m_ytdlpPlatform;

    if (exitCode == 0 && status == QProcess::NormalExit) {
        const QJsonDocument doc = QJsonDocument::fromJson(m_ytdlpProcess->readAllStandardOutput());
        if (doc.isObject()) {
            const QJsonObject root = doc.object();
            info.title = root.value("title").toString();
            info.streamerName = root.value("uploader").toString();
            if (info.streamerName.isEmpty()) {
                info.streamerName = root.value("channel").toString();
            }
            info.roomId = root.value("id").toString();

            const QString directUrl = root.value("url").toString();
            if (isValidStreamUrl(directUrl)) {
                appendQuality(info, "best", directUrl);
            }

            const QJsonArray formats = root.value("formats").toArray();
            for (const QJsonValue& value : formats) {
                const QJsonObject format = value.toObject();
                const QString formatUrl = format.value("url").toString();
                if (!isValidStreamUrl(formatUrl)) {
                    continue;
                }

                QString quality = format.value("format_note").toString().trimmed().toLower();
                if (quality.isEmpty()) {
                    const int height = format.value("height").toInt();
                    if (height > 0) {
                        quality = QString("%1p").arg(height);
                    }
                }
                if (quality.isEmpty()) {
                    quality = format.value("format_id").toString().trimmed().toLower();
                }
                appendQuality(info, quality, formatUrl);
            }
        }
    }

    if (info.availableStreams.isEmpty()) {
        // Fallback: try -g to get direct URL
        m_ytdlpFallbackPhase = true;
        m_ytdlpProcess->setArguments({"-g", m_ytdlpUrl});
        m_ytdlpProcess->start();
        return;
    }

    if (info.preferredQuality.isEmpty()) {
        info.preferredQuality = info.availableStreams.firstKey();
    }
    info.streamUrl = info.availableStreams.value(info.preferredQuality);
    info.backupStreamUrl = info.streamUrl;
    info.isValid = isValidStreamUrl(info.streamUrl);
    emit parseComplete(info);
}

#undef MODULE_NAME
