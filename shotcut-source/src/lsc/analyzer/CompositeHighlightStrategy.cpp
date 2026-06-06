#include "CompositeHighlightStrategy.h"
#include "HighlightUtils.h"
#include "../LscConfig.h"

#include <QJsonObject>
#include <algorithm>

CompositeHighlightStrategy::CompositeHighlightStrategy(QObject* parent)
    : IHighlightStrategy(parent)
{
}

void CompositeHighlightStrategy::addStrategy(IHighlightStrategy* strategy)
{
    if (!strategy) {
        return;
    }

    m_strategies.append(strategy);

    connect(strategy, &IHighlightStrategy::segmentFound,
            this, &CompositeHighlightStrategy::onChildSegment);
    connect(strategy, &IHighlightStrategy::finished,
            this, &CompositeHighlightStrategy::onChildFinished);
    connect(strategy, &IHighlightStrategy::errorOccurred,
            this, &CompositeHighlightStrategy::onChildError);
}

QString CompositeHighlightStrategy::name() const
{
    if (!m_displayName.isEmpty()) {
        return m_displayName;
    }

    if (m_strategies.isEmpty()) {
        return QStringLiteral("composite");
    }

    QStringList names;
    for (const auto* strategy : m_strategies) {
        names.append(strategy->name());
    }
    return QStringLiteral("composite(%1)").arg(names.join('+'));
}

QString CompositeHighlightStrategy::description() const
{
    const QString prefix = m_displayName.isEmpty() ? QStringLiteral("Composite strategy")
                                                   : m_displayName;
    if (m_strategies.isEmpty()) {
        return prefix;
    }

    QStringList descriptions;
    for (const auto* strategy : m_strategies) {
        descriptions.append(strategy->description());
    }
    return QStringLiteral("%1: %2").arg(prefix, descriptions.join(" + "));
}

void CompositeHighlightStrategy::analyze(const QString& videoPath)
{
    if (m_running) {
        return;
    }

    m_segments.clear();
    m_finishedCount = 0;
    m_running = true;

    if (m_strategies.isEmpty()) {
        m_running = false;
        emit finished();
        return;
    }

    for (auto* strategy : m_strategies) {
        strategy->analyze(videoPath);
    }
}

void CompositeHighlightStrategy::cancel()
{
    for (auto* strategy : m_strategies) {
        strategy->cancel();
    }
    m_running = false;
}

bool CompositeHighlightStrategy::isRunning() const
{
    return m_running;
}

HighlightResult CompositeHighlightStrategy::result() const
{
    HighlightResult result;
    result.segments = m_segments;
    result.strategyName = name();

    QJsonObject metadata;
    metadata["strategyCount"] = m_strategies.size();
    metadata["totalSegments"] = m_segments.size();
    result.metadata = metadata;

    return result;
}

void CompositeHighlightStrategy::configure(const QJsonObject& params)
{
    // Do NOT blindly forward all params to all sub-strategies.
    // Different strategies need different params (e.g., gameHint is only for GameStrategy).
    // Sub-strategies should be configured individually before adding to the composite.
    Q_UNUSED(params)
}

void CompositeHighlightStrategy::onChildFinished()
{
    ++m_finishedCount;
    checkAllFinished();
}

void CompositeHighlightStrategy::onChildSegment(const HighlightSegment& segment)
{
    m_segments.append(segment);
    emit segmentFound(segment);
}

void CompositeHighlightStrategy::onChildError(const QString& message)
{
    emit errorOccurred(message);
}

void CompositeHighlightStrategy::checkAllFinished()
{
    if (m_finishedCount < m_strategies.size()) {
        return;
    }

    m_running = false;

    std::sort(m_segments.begin(), m_segments.end(),
              [](const HighlightSegment& a, const HighlightSegment& b) {
                  return a.startSec < b.startSec;
              });

    // When a downstream HighlightRanker will do the heavy dedup,
    // only merge near-identical segments (0.7 threshold) here.
    m_segments = HighlightUtils::deduplicateSegments(
        m_segments,
        lsc::LscConfig::instance().compositeMergeOverlapThresholdWhenRankerEnabled);
    emit finished();
}
