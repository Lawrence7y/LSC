#ifndef GAMEPLAYTIMELINE_H
#define GAMEPLAYTIMELINE_H

#include "GameplayState.h"

#include <algorithm>
#include <QVector>

struct GameplayTimeSegment {
    double startSec = 0.0;
    double endSec = 0.0;
    GameState state = GameState::Unknown;
};

class GameplayTimeline {
public:
    void start(qint64 startMs = 0)
    {
        m_segments.clear();
        m_currentStartMs = startMs;
        m_currentState = GameState::Unknown;
        m_started = true;
    }

    void recordState(GameState state, qint64 timestampMs)
    {
        if (!m_started) {
            start(timestampMs);
        }
        timestampMs = std::max(timestampMs, m_currentStartMs);
        if (state == m_currentState) {
            return;
        }

        if (m_currentState != GameState::Unknown && timestampMs > m_currentStartMs) {
            GameplayTimeSegment segment;
            segment.startSec = m_currentStartMs / 1000.0;
            segment.endSec = timestampMs / 1000.0;
            segment.state = m_currentState;
            m_segments.append(segment);
        }

        m_currentState = state;
        m_currentStartMs = timestampMs;
    }

    void finish(qint64 endMs)
    {
        if (!m_started) {
            return;
        }
        endMs = std::max(endMs, m_currentStartMs);
        if (m_currentState != GameState::Unknown && endMs > m_currentStartMs) {
            GameplayTimeSegment segment;
            segment.startSec = m_currentStartMs / 1000.0;
            segment.endSec = endMs / 1000.0;
            segment.state = m_currentState;
            m_segments.append(segment);
        }
        m_started = false;
    }

    QVector<GameplayTimeSegment> segments() const { return m_segments; }
    QVector<GameplayTimeSegment> gameplaySegments(double minDurationSec = 1.0) const
    {
        QVector<GameplayTimeSegment> result;
        for (const GameplayTimeSegment& segment : m_segments) {
            if (segment.state == GameState::Gameplay
                && segment.endSec - segment.startSec >= minDurationSec) {
                result.append(segment);
            }
        }
        return result;
    }
    GameState currentState() const { return m_currentState; }

private:
    qint64 m_currentStartMs = 0;
    GameState m_currentState = GameState::Unknown;
    bool m_started = false;
    QVector<GameplayTimeSegment> m_segments;
};

#endif // GAMEPLAYTIMELINE_H
