#include "PreviewController.h"

PreviewController::PreviewController(QObject* parent)
    : QObject(parent)
{
}

void PreviewController::setPreviewSource(const QString& sourcePath)
{
    if (m_sourcePath == sourcePath) {
        return;
    }

    m_sourcePath = sourcePath;
    if (!m_sourcePath.isEmpty()) {
        emit previewAvailable(m_sourcePath);
    }
}

void PreviewController::clearPreviewSource()
{
    if (m_sourcePath.isEmpty()) {
        return;
    }

    m_sourcePath.clear();
    emit previewCleared();
}
