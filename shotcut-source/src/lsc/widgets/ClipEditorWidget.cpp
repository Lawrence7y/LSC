#include "ClipEditorWidget.h"

#include <QPainter>
#include <QMouseEvent>
#include <QLinearGradient>
#include <algorithm>
#include <cmath>

namespace lsc {

static constexpr int kMarginLeft = 60;
static constexpr int kMarginRight = 20;
static constexpr int kMarginTop = 30;
static constexpr int kDragThreshold = 3;
static constexpr double kMinClipDuration = 0.5;

ClipEditorWidget::ClipEditorWidget(QWidget* parent)
    : QWidget(parent)
{
    setMinimumHeight(m_timelineHeight + kMarginTop + 20);
    setMinimumWidth(300);
    setMouseTracking(true);
    setSizePolicy(QSizePolicy::Expanding, QSizePolicy::Fixed);
}

void ClipEditorWidget::setClips(const QVector<EditableClip>& clips)
{
    m_clips = clips;
    m_dragIndex = -1;
    m_dragMode = DragMode::None;
    update();
}

void ClipEditorWidget::setVideoDuration(double durationSec)
{
    m_durationSec = durationSec;
    update();
}

QVector<EditableClip> ClipEditorWidget::clips() const
{
    return m_clips;
}

int ClipEditorWidget::clipAtPosition(const QPoint& pos) const
{
    for (int i = 0; i < m_clips.size(); ++i) {
        if (clipRect(i).contains(pos)) {
            return i;
        }
    }
    return -1;
}

QRect ClipEditorWidget::clipRect(int index) const
{
    if (index < 0 || index >= m_clips.size() || m_durationSec <= 0) {
        return {};
    }

    const auto& clip = m_clips[index];
    const int x1 = timeToPos(clip.startSec);
    const int x2 = timeToPos(clip.endSec);
    const int y = kMarginTop;
    return QRect(x1, y, x2 - x1, m_clipHeight);
}

double ClipEditorWidget::posToTime(int x) const
{
    const int trackWidth = width() - kMarginLeft - kMarginRight;
    if (trackWidth <= 0 || m_durationSec <= 0) {
        return 0;
    }
    const double ratio = static_cast<double>(x - kMarginLeft) / trackWidth;
    return std::clamp(ratio * m_durationSec, 0.0, m_durationSec);
}

int ClipEditorWidget::timeToPos(double sec) const
{
    const int trackWidth = width() - kMarginLeft - kMarginRight;
    if (trackWidth <= 0 || m_durationSec <= 0) {
        return kMarginLeft;
    }
    return kMarginLeft + static_cast<int>((sec / m_durationSec) * trackWidth);
}

void ClipEditorWidget::paintEvent(QPaintEvent* /*event*/)
{
    QPainter p(this);
    p.setRenderHint(QPainter::Antialiasing);

    const int w = width();
    const int h = height();
    const int trackWidth = w - kMarginLeft - kMarginRight;

    // Background
    p.fillRect(rect(), QColor(30, 30, 30));

    // Timeline track background
    p.setPen(Qt::NoPen);
    p.setBrush(QColor(50, 50, 50));
    p.drawRoundedRect(kMarginLeft, kMarginTop, trackWidth, m_timelineHeight, 4, 4);

    // Tick marks
    if (m_durationSec > 0) {
        p.setPen(QColor(100, 100, 100));
        const int tickCount = std::min(20, static_cast<int>(m_durationSec));
        for (int i = 0; i <= tickCount; ++i) {
            const double sec = (tickCount > 0)
                ? m_durationSec * i / tickCount
                : 0;
            const int x = timeToPos(sec);
            p.drawLine(x, kMarginTop, x, kMarginTop + 6);

            const int totalSecs = static_cast<int>(std::round(sec));
            const int mm = totalSecs / 60;
            const int ss = totalSecs % 60;
            const QString label = QString("%1:%2")
                                      .arg(mm, 2, 10, QChar('0'))
                                      .arg(ss, 2, 10, QChar('0'));
            p.setPen(QColor(160, 160, 160));
            p.setFont(QFont("Segoe UI", 8));
            p.drawText(QRect(x - 30, kMarginTop - 20, 60, 16),
                       Qt::AlignCenter, label);
            p.setPen(QColor(100, 100, 100));
        }
    }

    // Clips
    for (int i = 0; i < m_clips.size(); ++i) {
        const auto& clip = m_clips[i];
        const QRect r = clipRect(i);
        if (r.width() < 1) {
            continue;
        }

        // Clip body
        QColor fillColor;
        if (clip.modified) {
            fillColor = QColor(230, 126, 34); // orange for modified
        } else if (clip.selected) {
            fillColor = QColor(41, 128, 185); // blue for selected
        } else {
            fillColor = QColor(100, 100, 100); // grey for deselected
        }

        QLinearGradient grad(r.topLeft(), r.bottomLeft());
        grad.setColorAt(0, fillColor.lighter(120));
        grad.setColorAt(1, fillColor);

        p.setPen(clip.selected ? QColor(255, 255, 255, 80) : QColor(80, 80, 80));
        p.setBrush(grad);
        p.drawRoundedRect(r, 3, 3);

        // Clip label (title + score)
        p.setPen(QColor(255, 255, 255));
        p.setFont(QFont("Segoe UI", 8));
        const QString text = clip.title.isEmpty()
            ? QString::number(clip.score, 'f', 1)
            : clip.title;
        p.drawText(r.adjusted(4, 2, -4, -2),
                   Qt::AlignLeft | Qt::AlignTop, text);

        // Score bar at bottom
        if (clip.score > 0) {
            const int barWidth = static_cast<int>(r.width() * std::clamp(clip.score, 0.0, 1.0));
            const QRect scoreBar(r.left(), r.bottom() - 4, barWidth, 3);
            p.setPen(Qt::NoPen);
            p.setBrush(QColor(46, 204, 113));
            p.drawRoundedRect(scoreBar, 1, 1);
        }

        // Drag handles (small rectangles at edges)
        if (clip.selected) {
            p.setPen(Qt::NoPen);
            p.setBrush(QColor(255, 255, 255, 160));
            const int handleW = 4;
            p.drawRoundedRect(r.left(), r.top(), handleW, r.height(), 1, 1);
            p.drawRoundedRect(r.right() - handleW, r.top(), handleW, r.height(), 1, 1);
        }
    }

    // Current drag indicator line
    if (m_dragMode == DragMode::MoveStart || m_dragMode == DragMode::MoveEnd) {
        p.setPen(QPen(QColor(231, 76, 60), 2, Qt::DashLine));
        const int x = m_dragMode == DragMode::MoveStart
            ? timeToPos(m_clips[m_dragIndex].startSec)
            : timeToPos(m_clips[m_dragIndex].endSec);
        p.drawLine(x, kMarginTop - 5, x, kMarginTop + m_timelineHeight + 5);
    }
}

void ClipEditorWidget::mousePressEvent(QMouseEvent* event)
{
    if (event->button() != Qt::LeftButton) {
        return;
    }

    const int index = clipAtPosition(event->pos());
    if (index < 0) {
        return;
    }

    const QRect r = clipRect(index);
    const int localX = event->pos().x() - r.left();
    const int edgeZone = std::min(10, r.width() / 4);

    m_dragIndex = index;
    m_dragStartX = event->pos().x();
    m_dragOriginalStart = m_clips[index].startSec;
    m_dragOriginalEnd = m_clips[index].endSec;

    if (localX <= edgeZone) {
        m_dragMode = DragMode::MoveStart;
    } else if (localX >= r.width() - edgeZone) {
        m_dragMode = DragMode::MoveEnd;
    } else {
        m_dragMode = DragMode::MoveClip;
    }
}

void ClipEditorWidget::mouseMoveEvent(QMouseEvent* event)
{
    if (m_dragMode == DragMode::None || m_dragIndex < 0) {
        return;
    }

    const int dx = event->pos().x() - m_dragStartX;
    const double dt = posToTime(m_dragStartX + dx) - posToTime(m_dragStartX);

    auto& clip = m_clips[m_dragIndex];

    switch (m_dragMode) {
    case DragMode::MoveStart: {
        double newStart = std::clamp(m_dragOriginalStart + dt,
                                     0.0, clip.endSec - kMinClipDuration);
        if (std::abs(newStart - clip.startSec) > 0.01) {
            clip.startSec = newStart;
            clip.modified = true;
            update();
        }
        break;
    }
    case DragMode::MoveEnd: {
        double newEnd = std::clamp(m_dragOriginalEnd + dt,
                                   clip.startSec + kMinClipDuration, m_durationSec);
        if (std::abs(newEnd - clip.endSec) > 0.01) {
            clip.endSec = newEnd;
            clip.modified = true;
            update();
        }
        break;
    }
    case DragMode::MoveClip: {
        const double duration = m_dragOriginalEnd - m_dragOriginalStart;
        double newStart = m_dragOriginalStart + dt;
        double newEnd = m_dragOriginalEnd + dt;

        // Clamp to bounds
        if (newStart < 0) {
            newStart = 0;
            newEnd = duration;
        } else if (newEnd > m_durationSec) {
            newEnd = m_durationSec;
            newStart = m_durationSec - duration;
        }

        if (std::abs(newStart - clip.startSec) > 0.01) {
            clip.startSec = newStart;
            clip.endSec = newEnd;
            clip.modified = true;
            update();
        }
        break;
    }
    default:
        break;
    }
}

void ClipEditorWidget::mouseReleaseEvent(QMouseEvent* event)
{
    if (event->button() != Qt::LeftButton) {
        return;
    }

    if (m_dragMode != DragMode::None && m_dragIndex >= 0) {
        const auto& clip = m_clips[m_dragIndex];
        if (clip.modified) {
            emit clipBoundaryChanged(clip.id, clip.startSec, clip.endSec);
        }
    } else if (m_dragMode == DragMode::None && m_dragIndex >= 0) {
        // Click without drag → toggle selection
        auto& clip = m_clips[m_dragIndex];
        clip.selected = !clip.selected;
        emit clipSelected(clip.id);
        emit selectionChanged();
    }

    m_dragMode = DragMode::None;
    m_dragIndex = -1;
    update();
}

} // namespace lsc
