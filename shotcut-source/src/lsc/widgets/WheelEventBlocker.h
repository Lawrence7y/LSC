#ifndef WHEELEVENTBLOCKER_H
#define WHEELEVENTBLOCKER_H

#include <QObject>

namespace lsc {

class WheelEventBlocker : public QObject
{
public:
    explicit WheelEventBlocker(QObject* parent = nullptr);

protected:
    bool eventFilter(QObject* watched, QEvent* event) override;
};

} // namespace lsc

#endif // WHEELEVENTBLOCKER_H
