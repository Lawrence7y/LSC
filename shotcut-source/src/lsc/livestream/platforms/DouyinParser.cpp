#include "DouyinParser.h"

#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QNetworkRequest>

namespace lsc {

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

} // anonymous namespace

DouyinParser::DouyinParser(QObject* parent)
    : IPlatformParser(parent)
    , m_nam(new QNetworkAccessManager(this))
    , m_cookieJar(new QNetworkCookieJar(this))
{
    m_nam->setCookieJar(m_cookieJar);
}

QString DouyinParser::platformName() const
{
    return QStringLiteral("douyin");
}

bool DouyinParser::canParse(const QString& url) const
{
    const QString lower = url.toLower();
    return lower.contains("douyin.com") || lower.contains("tiktok.com");
}

void DouyinParser::parse(const QString& url)
{
    cancel();

    QUrl qurl = QUrl::fromUserInput(url);
    if (!qurl.isValid()) {
        emit parseError(QStringLiteral("Invalid URL"));
        return;
    }

    QNetworkRequest request(qurl);
    request.setRawHeader("User-Agent",
                         "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                         "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36");
    request.setRawHeader("Accept",
                         "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8");
    request.setRawHeader("Accept-Language", "zh-CN,zh;q=0.9,en;q=0.8");
    request.setAttribute(QNetworkRequest::RedirectPolicyAttribute,
                         QNetworkRequest::NoLessSafeRedirectPolicy);

    m_activeReply = m_nam->get(request);
    connect(m_activeReply, &QNetworkReply::finished, this, [this]() {
        auto* reply = qobject_cast<QNetworkReply*>(sender());
        if (!reply) return;
        onPageFetched(reply);
    });
}

void DouyinParser::cancel()
{
    if (m_activeReply) {
        m_activeReply->abort();
        m_activeReply->deleteLater();
        m_activeReply = nullptr;
    }
}

void DouyinParser::onPageFetched(QNetworkReply* reply)
{
    reply->deleteLater();
    m_activeReply = nullptr;

    if (reply->error() != QNetworkReply::NoError) {
        emit parseError(QStringLiteral("Page request failed: %1").arg(reply->errorString()));
        return;
    }

    const QByteArray html = reply->readAll();
    const QString reqUrl = reply->request().url().toString();

    const StreamInfo info = extractFromHtml(html, reqUrl);
    if (info.isLive) {
        emit parseComplete(info);
    } else {
        emit parseError(info.title.isEmpty()
                            ? QStringLiteral("Cannot extract stream URL from page")
                            : info.title);
    }
}

StreamInfo DouyinParser::extractFromHtml(const QByteArray& html, const QString& url)
{
    StreamInfo info = extractSsrData(html);
    if (info.isLive) {
        info.platform = platformName();
        if (info.roomId.isEmpty()) {
            info.roomId = QUrl(url).path().section('/', -1);
        }
        return info;
    }

    info.platform = platformName();
    info.isLive = false;
    info.title = QStringLiteral("SSR data parse failed, stream may be offline or require login.");
    return info;
}

StreamInfo DouyinParser::extractSsrData(const QByteArray& html)
{
    StreamInfo info;
    info.platform = platformName();

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

            if (!info.availableQualities.contains(quality)) {
                info.availableQualities.append(quality);
            }
            if (info.streamUrl.isEmpty()) {
                info.streamUrl = preferredUrl;
                info.backupStreamUrl = isValidStreamUrl(hlsUrl) ? hlsUrl : preferredUrl;
                info.selectedQuality = quality;
                info.isLive = true;
            }
        }

        if (info.isLive) {
            return info;
        }

        searchPos = endIdx + 3;
    }

    return info;
}

bool DouyinParser::isValidStreamUrl(const QString& url)
{
    return url.startsWith("http://") || url.startsWith("https://");
}

} // namespace lsc
