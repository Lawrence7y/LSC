#ifndef PREVIEWCONTROLLER_H
#define PREVIEWCONTROLLER_H

#include <QObject>
#include <QString>

/**
 * @brief 预览控制器 - 管理直播预览的生命周期
 *
 * 负责接收录制会话的预览信号，控制预览控件的启停。
 */
class PreviewController : public QObject
{
    Q_OBJECT
public:
    explicit PreviewController(QObject* parent = nullptr);

    void setPreviewSource(const QString& sourcePath);
    void clearPreviewSource();

    QString currentSource() const { return m_sourcePath; }
    bool isActive() const { return !m_sourcePath.isEmpty(); }

signals:
    void previewAvailable(const QString& sourcePath);
    void previewCleared();

private:
    QString m_sourcePath;
};

#endif // PREVIEWCONTROLLER_H
