#include "app/MainWindow.h"
#include "core/LscDatabase.h"
#include "core/TaskCenter.h"
#include "widgets/WheelEventBlocker.h"
#include <QApplication>
#include <QDebug>

int main(int argc, char* argv[])
{
    QApplication app(argc, argv);
    app.setApplicationName("LSC");
    app.setApplicationVersion("1.0.0");
    app.setOrganizationName("LSC");

    QCoreApplication::setAttribute(Qt::AA_EnableHighDpiScaling);

    if (!lsc::LscDatabase::instance().initialize()) {
        qWarning() << "LSC database initialization failed; history and diagnostics may be limited.";
    }
    lsc::TaskCenter::instance().recoverInterruptedTasks("Application restarted");

    lsc::WheelEventBlocker wheelEventBlocker(&app);
    app.installEventFilter(&wheelEventBlocker);

    MainWindow window;
    window.show();

    return app.exec();
}
