#include <QCoreApplication>
#include <QDir>
#include <QFileInfo>

#include <iostream>

static int g_pass = 0;
static int g_fail = 0;

#define LOG(msg) std::cout << QString(msg).toStdString() << std::endl

void check(const QString& name, bool cond)
{
    if (cond) {
        ++g_pass;
        LOG(QString("[PASS] %1").arg(name));
    } else {
        ++g_fail;
        LOG(QString("[FAIL] %1").arg(name));
    }
}

int main(int argc, char* argv[])
{
    QCoreApplication app(argc, argv);

    const QString runtimeDir = QCoreApplication::applicationDirPath();
    const QDir dir(runtimeDir);

    check("lsc_app.exe is present", QFileInfo::exists(dir.filePath("lsc_app.exe")));
    check("Qt6Core.dll is deployed next to app", QFileInfo::exists(dir.filePath("Qt6Core.dll")));
    check("Qt6Gui.dll is deployed next to app", QFileInfo::exists(dir.filePath("Qt6Gui.dll")));
    check("Qt6Widgets.dll is deployed next to app", QFileInfo::exists(dir.filePath("Qt6Widgets.dll")));
    check("platform plugin is deployed", QFileInfo::exists(dir.filePath("platforms/qwindows.dll")));
    check("multimedia plugin directory exists",
          QFileInfo::exists(dir.filePath("multimedia")) || QFileInfo::exists(dir.filePath("plugins/multimedia")));

    LOG(QString("=== Results: %1 passed, %2 failed ===").arg(g_pass).arg(g_fail));
    return g_fail > 0 ? 1 : 0;
}
