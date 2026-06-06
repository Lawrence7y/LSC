#include "WheelEventBlocker.h"

#include <QAbstractSpinBox>
#include <QComboBox>
#include <QEvent>
#include <QSlider>

namespace lsc {

WheelEventBlocker::WheelEventBlocker(QObject* parent)
    : QObject(parent)
{
}

bool WheelEventBlocker::eventFilter(QObject* watched, QEvent* event)
{
    if (event->type() == QEvent::Wheel) {
        if (qobject_cast<QComboBox*>(watched)
            || qobject_cast<QAbstractSpinBox*>(watched)
            || qobject_cast<QSlider*>(watched)) {
            event->ignore();
            return true;
        }
    }

    return QObject::eventFilter(watched, event);
}

} // namespace lsc
