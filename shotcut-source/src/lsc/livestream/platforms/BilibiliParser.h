#ifndef BILIBILIPARSER_H
#define BILIBILIPARSER_H

#include "livestream/IPlatformParser.h"

#include <QNetworkAccessManager>
#include <QNetworkCookieJar>
#include <QNetworkReply>

namespace lsc {

class BilibiliParser : public IPlatformParser {
    Q_OBJECT

public:
    explicit BilibiliParser(QObject* parent = nullptr);

    QString platformName() const override;
    bool canParse(const QString& url) const override;
    void parse(const QString& url) override;
    void cancel() override;

private slots:
    void onRoomInfoReply(QNetworkReply* reply);
    void onStreamUrlReply(QNetworkReply* reply);

private:
    void parseRoomPage(const QString& roomId);
    void fetchStreamUrl(const QString& roomId, int qn);

    QNetworkAccessManager* m_nam = nullptr;
    QNetworkCookieJar* m_cookieJar = nullptr;
    QNetworkReply* m_activeReply = nullptr;
    QString m_roomId;
    QString m_title;
    QString m_streamerName;
};

} // namespace lsc

#endif // BILIBILIPARSER_H
