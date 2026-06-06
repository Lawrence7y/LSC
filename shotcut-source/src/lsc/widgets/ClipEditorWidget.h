#ifndef CLIPEDITORWIDGET_H
#define CLIPEDITORWIDGET_H

#include <QWidget>
#include <QVector>

namespace lsc {

struct EditableClip {
    QString id;
    double startSec;
    double endSec;
    double score;
    QString title;
    bool selected = true;
    bool modified = false;
};

class ClipEditorWidget : public QWidget {
    Q_OBJECT

public:
    explicit ClipEditorWidget(QWidget* parent = nullptr);

    void setClips(const QVector<EditableClip>& clips);
    void setVideoDuration(double durationSec);
    QVector<EditableClip> clips() const;

signals:
    void clipBoundaryChanged(const QString& clipId,
                             double newStart, double newEnd);
    void clipsMerged(const QString& clipId1, const QString& clipId2);
    void clipDeleted(const QString& clipId);
    void clipSelected(const QString& clipId);
    void selectionChanged();

protected:
    void paintEvent(QPaintEvent* event) override;
    void mousePressEvent(QMouseEvent* event) override;
    void mouseMoveEvent(QMouseEvent* event) override;
    void mouseReleaseEvent(QMouseEvent* event) override;

private:
    enum class DragMode {
        None,
        MoveStart,
        MoveEnd,
        MoveClip
    };

    int clipAtPosition(const QPoint& pos) const;
    QRect clipRect(int index) const;
    double posToTime(int x) const;
    int timeToPos(double sec) const;

    QVector<EditableClip> m_clips;
    double m_durationSec = 0;
    int m_dragIndex = -1;
    DragMode m_dragMode = DragMode::None;
    int m_dragStartX = 0;
    double m_dragOriginalStart = 0;
    double m_dragOriginalEnd = 0;
    int m_timelineHeight = 60;
    int m_clipHeight = 40;
};

} // namespace lsc

#endif // CLIPEDITORWIDGET_H
