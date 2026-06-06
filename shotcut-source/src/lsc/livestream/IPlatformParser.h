#ifndef IPLATFORMPARSER_H
#define IPLATFORMPARSER_H

#include <QObject>
#include <QString>
#include <QStringList>
#include <QVariantMap>

namespace lsc {

struct StreamInfo {
    QString platform;
    QString streamUrl;
    QString backupStreamUrl;
    QString roomId;
    QString title;
    QString streamerName;
    QStringList availableQualities;
    QString selectedQuality;
    QVariantMap cookies;
    QVariantMap headers;
    bool isLive = false;
};

class IPlatformParser : public QObject {
    Q_OBJECT

public:
    explicit IPlatformParser(QObject* parent = nullptr)
        : QObject(parent)
    {
    }
    virtual ~IPlatformParser() = default;

    virtual QString platformName() const = 0;
    virtual bool canParse(const QString& url) const = 0;
    virtual void parse(const QString& url) = 0;
    virtual void cancel() = 0;

signals:
    void parseComplete(const StreamInfo& info);
    void parseError(const QString& error);
};

} // namespace lsc

Q_DECLARE_METATYPE(lsc::StreamInfo)

#endif // IPLATFORMPARSER_H
