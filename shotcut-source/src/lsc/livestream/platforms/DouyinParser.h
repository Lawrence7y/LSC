#ifndef DOUYINPARSER_H
#define DOUYINPARSER_H

#include "livestream/IPlatformParser.h"

#include <QNetworkAccessManager>
#include <QNetworkCookieJar>
#include <QNetworkReply>

namespace lsc {

class DouyinParser : public IPlatformParser {
    Q_OBJECT

public:
    explicit DouyinParser(QObject* parent = nullptr);

    QString platformName() const override;
    bool canParse(const QString& url) const override;
    void parse(const QString& url) override;
    void cancel() override;

private slots:
    void onPageFetched(QNetworkReply* reply);

private:
    StreamInfo extractFromHtml(const QByteArray& html, const QString& url);
    StreamInfo extractSsrData(const QByteArray& html);
    static bool isValidStreamUrl(const QString& url);

    QNetworkAccessManager* m_nam = nullptr;
    QNetworkCookieJar* m_cookieJar = nullptr;
    QNetworkReply* m_activeReply = nullptr;
};

} // namespace lsc

#endif // DOUYINPARSER_H
