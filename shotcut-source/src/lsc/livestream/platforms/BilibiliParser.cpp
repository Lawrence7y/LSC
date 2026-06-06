#include "BilibiliParser.h"

#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QNetworkRequest>
#include <QRegularExpression>
#include <QUrlQuery>

namespace lsc {

BilibiliParser::BilibiliParser(QObject* parent)
    : IPlatformParser(parent)
    , m_nam(new QNetworkAccessManager(this))
    , m_cookieJar(new QNetworkCookieJar(this))
{
    m_nam->setCookieJar(m_cookieJar);
}

QString BilibiliParser::platformName() const
{
    return QStringLiteral("bilibili");
}

bool BilibiliParser::canParse(const QString& url) const
{
    const QString lower = url.toLower();
    return lower.contains("bilibili.com") || lower.contains("b23.tv");
}

void BilibiliParser::parse(const QString& url)
{
    cancel();

    QUrl qurl = QUrl::fromUserInput(url);
    if (!qurl.isValid()) {
        emit parseError(QStringLiteral("Invalid URL"));
        return;
    }

    const QString path = qurl.path();
    QRegularExpression re("/(\\d+)");
    QRegularExpressionMatch match = re.match(path);
    if (!match.hasMatch()) {
        emit parseError(QStringLiteral("Cannot extract room ID from URL"));
        return;
    }

    m_roomId = match.captured(1);
    parseRoomPage(m_roomId);
}

void BilibiliParser::cancel()
{
    if (m_activeReply) {
        m_activeReply->abort();
        m_activeReply->deleteLater();
        m_activeReply = nullptr;
    }
}

void BilibiliParser::parseRoomPage(const QString& roomId)
{
    const QString apiUrl =
        QStringLiteral("https://api.live.bilibili.com/room/v1/Room/get_info?room_id=%1").arg(roomId);

    QNetworkRequest request{QUrl(apiUrl)};
    request.setRawHeader("User-Agent",
                         "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                         "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36");
    request.setRawHeader("Referer", "https://live.bilibili.com/");

    m_activeReply = m_nam->get(request);
    connect(m_activeReply, &QNetworkReply::finished, this, [this]() {
        auto* reply = qobject_cast<QNetworkReply*>(sender());
        if (!reply) return;
        onRoomInfoReply(reply);
    });
}

void BilibiliParser::onRoomInfoReply(QNetworkReply* reply)
{
    reply->deleteLater();
    m_activeReply = nullptr;

    if (reply->error() != QNetworkReply::NoError) {
        emit parseError(QStringLiteral("Room info request failed: %1").arg(reply->errorString()));
        return;
    }

    const QJsonDocument doc = QJsonDocument::fromJson(reply->readAll());
    if (!doc.isObject()) {
        emit parseError(QStringLiteral("Invalid room info response"));
        return;
    }

    const QJsonObject root = doc.object();
    const int code = root.value("code").toInt(-1);
    if (code != 0) {
        emit parseError(QStringLiteral("Bilibili API error: %1").arg(root.value("message").toString()));
        return;
    }

    const QJsonObject data = root.value("data").toObject();
    m_title = data.value("title").toString();
    const int liveStatus = data.value("live_status").toInt();
    const bool isLive = (liveStatus == 1);

    if (!isLive) {
        emit parseError(QStringLiteral("Room is not live"));
        return;
    }

    fetchStreamUrl(m_roomId, 0);
}

void BilibiliParser::fetchStreamUrl(const QString& roomId, int qn)
{
    const QString apiUrl = QStringLiteral(
                               "https://api.live.bilibili.com/room/v1/Room/playUrl?room_id=%1&quality=%2&platform=web")
                               .arg(roomId)
                               .arg(qn);

    QNetworkRequest request{QUrl(apiUrl)};
    request.setRawHeader("User-Agent",
                         "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                         "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36");
    request.setRawHeader("Referer", "https://live.bilibili.com/");

    m_activeReply = m_nam->get(request);
    connect(m_activeReply, &QNetworkReply::finished, this, [this]() {
        auto* reply = qobject_cast<QNetworkReply*>(sender());
        if (!reply) return;
        onStreamUrlReply(reply);
    });
}

void BilibiliParser::onStreamUrlReply(QNetworkReply* reply)
{
    reply->deleteLater();
    m_activeReply = nullptr;

    if (reply->error() != QNetworkReply::NoError) {
        emit parseError(QStringLiteral("Stream URL request failed: %1").arg(reply->errorString()));
        return;
    }

    const QJsonDocument doc = QJsonDocument::fromJson(reply->readAll());
    if (!doc.isObject()) {
        emit parseError(QStringLiteral("Invalid stream URL response"));
        return;
    }

    const QJsonObject root = doc.object();
    const QJsonObject data = root.value("data").toObject();
    const QJsonArray durls = data.value("durl").toArray();

    if (durls.isEmpty()) {
        emit parseError(QStringLiteral("No stream URLs available"));
        return;
    }

    StreamInfo info;
    info.platform = platformName();
    info.roomId = m_roomId;
    info.title = m_title;
    info.isLive = true;

    for (const QJsonValue& val : durls) {
        const QJsonObject durl = val.toObject();
        const QString streamUrl = durl.value("url").toString();
        if (!streamUrl.isEmpty()) {
            if (info.streamUrl.isEmpty()) {
                info.streamUrl = streamUrl;
            } else if (info.backupStreamUrl.isEmpty()) {
                info.backupStreamUrl = streamUrl;
            }
            const int order = durl.value("order").toInt();
            info.availableQualities.append(QStringLiteral("quality_%1").arg(order));
        }
    }

    info.selectedQuality = info.availableQualities.isEmpty() ? QString() : info.availableQualities.first();

    if (info.streamUrl.isEmpty()) {
        emit parseError(QStringLiteral("No valid stream URL found"));
        return;
    }

    emit parseComplete(info);
}

} // namespace lsc
