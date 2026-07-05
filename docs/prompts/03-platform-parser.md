# Task 3: 直播平台 URL 解析器

## 任务目标

实现平台 URL 解析器，支持识别抖音、快手、B站、YouTube、Twitch 等平台的直播链接，并获取直播流地址。

## 创建文件

- `src/lsc/livestream/PlatformParser.h`
- `src/lsc/livestream/PlatformParser.cpp`

## 前置条件

- Task 2 已完成（模块目录结构）

## PlatformParser.h

```cpp
#ifndef PLATFORMPARSER_H
#define PLATFORMPARSER_H

#include <QObject>
#include <QString>
#include <QUrl>

struct PlatformInfo {
    QString platform;       // "douyin", "kuaishou", "bilibili", "youtube", "twitch"
    QString streamUrl;      // 解析后的直接流地址
    QString roomId;
    QString title;
    QString streamerName;
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

signals:
    void parseComplete(const PlatformInfo& info);
    void parseError(const QString& error);
};

#endif
```

## PlatformParser.cpp

```cpp
#include "PlatformParser.h"
#include <QRegularExpression>
#include <QProcess>
#include <QJsonDocument>
#include <QJsonObject>

PlatformParser::PlatformParser(QObject* parent)
    : QObject(parent) {}

void PlatformParser::parseUrl(const QString& url)
{
    QUrl qurl(url);
    QString platform = detectPlatform(qurl);

    if (platform.isEmpty()) {
        emit parseError(QString("无法识别平台: %1").arg(url));
        return;
    }

    PlatformInfo info;
    info.platform = platform;
    info.streamUrl = url;
    info.isValid = true;

    // 尝试用 yt-dlp 获取直接流地址
    QProcess process;
    process.setProgram("yt-dlp");
    process.setArguments({"-g", url});
    process.start();
    process.waitForFinished(10000);

    if (process.exitCode() == 0) {
        info.streamUrl = QString::fromUtf8(
            process.readAllStandardOutput()).trimmed();
    }

    emit parseComplete(info);
}

QString PlatformParser::detectPlatform(const QUrl& url)
{
    QString host = url.host().toLower();

    if (host.contains("douyin.com") || host.contains("tiktok.com"))
        return "douyin";
    if (host.contains("kuaishou.com"))
        return "kuaishou";
    if (host.contains("bilibili.com") || host.contains("live.bilibili.com"))
        return "bilibili";
    if (host.contains("youtube.com") || host.contains("youtu.be"))
        return "youtube";
    if (host.contains("twitch.tv"))
        return "twitch";

    return QString();
}
```

## 验证

编译项目：
```bash
cd shotcut-source/build
cmake --build . --config Release
```

预期：编译通过，无错误。

## 说明

此版本使用 `yt-dlp` 作为外部工具获取流地址（需要在系统PATH中安装 `yt-dlp`）。后续可扩展为平台特定 API 调用。
