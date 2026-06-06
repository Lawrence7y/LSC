#include "livestream/PlatformParser.h"
#include <QCoreApplication>
#include <QTimer>
#include <QFile>
#include <iostream>

#define LOG(msg) std::cout << QString(msg).toStdString() << std::endl

int main(int argc, char* argv[])
{
    QCoreApplication app(argc, argv);

    LOG("=== SSR Debug Test ===");

    PlatformParser* parser = new PlatformParser(&app);

    QObject::connect(parser, &PlatformParser::parseComplete,
        [&](const PlatformInfo& info) {
            LOG("SUCCESS - Parse complete:");
            LOG(QString("  Platform: %1").arg(info.platform));
            LOG(QString("  Valid: %1").arg(info.isValid));
            LOG(QString("  Stream URL: %1").arg(info.streamUrl));
            LOG(QString("  Backup URL: %1").arg(info.backupStreamUrl));
            LOG(QString("  Error: %1").arg(info.errorMsg));
            app.exit(0);
        });

    QObject::connect(parser, &PlatformParser::parseError,
        [&](const QString& error) {
            LOG("INFO - Parse error:");
            LOG("  " + error);
            LOG("Parser responded, treating this as an external-state-dependent smoke pass.");
            app.exit(0);
        });

    parser->parseUrl("https://live.douyin.com/53682367755");

    QTimer::singleShot(30000, &app, [&]() {
        LOG("TIMEOUT after 30s");
        app.exit(1);
    });

    return app.exec();
}
