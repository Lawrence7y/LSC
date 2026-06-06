#ifndef PLATFORMPARSER_H
#define PLATFORMPARSER_H

#include <QNetworkAccessManager>
#include <QNetworkCookieJar>
#include <QNetworkReply>
#include <QObject>
#include <QProcess>
#include <QMap>
#include <QString>
#include <QStringList>
#include <QUrl>

namespace lsc {
class IPlatformParser;
}

struct PlatformInfo {
    QString platform;
    QString streamUrl;
    QString backupStreamUrl;
    QString roomId;
    QString title;
    QString streamerName;
    QString preferredQuality;
    QStringList availableQualities;
    QMap<QString, QString> availableStreams;
    bool isValid = false;
    QString errorMsg;
};

class PlatformParser : public QObject
{
    Q_OBJECT
public:
    explicit PlatformParser(QObject* parent = nullptr);

    void parseUrl(const QString& url);
    static QString detectPlatform(const QUrl& url);
    static QString normalizeError(const QString& rawError, const QString& platform);

    QNetworkCookieJar* cookieJar() const { return m_cookieJar; }
    void setCookieJar(QNetworkCookieJar* jar);

signals:
    void parseComplete(const PlatformInfo& info);
    void parseError(const QString& error);

private slots:
    void onPageFetched(QNetworkReply* reply);
    void onYtDlpFinished(int exitCode, QProcess::ExitStatus status);

private:
    void parseDouyinLive(const QUrl& url);
    bool parseWithNativeParser(const QString& url, const QString& platform);
    void parseWithYtDlp(const QString& url, const QString& platform);
    PlatformInfo extractDouyinFromHtml(const QByteArray& html, const QString& url);
    PlatformInfo extractDouyinSsrData(const QByteArray& html);
    static bool isValidStreamUrl(const QString& url);

    QNetworkAccessManager* m_nam;
    QNetworkCookieJar* m_cookieJar;
    QProcess* m_ytdlpProcess;
    lsc::IPlatformParser* m_nativeParser = nullptr;
    QString m_ytdlpPlatform;
    QString m_ytdlpUrl;
    bool m_ytdlpFallbackPhase = false;
};

#endif // PLATFORMPARSER_H
