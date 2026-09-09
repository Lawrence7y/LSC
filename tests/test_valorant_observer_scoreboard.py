"""Unit tests for ObserverScoreboardTracker."""
from __future__ import annotations

import numpy as np
import pytest

from lsc.analyzer.valorant_observer_scoreboard import (
    ObserverScoreboardTracker,
    ScoreTransition,
    extract_observer_scoreboard_scores,
)


def test_tracker_initialization() -> None:
    tracker = ObserverScoreboardTracker()
    assert tracker.current_left is None
    assert tracker.current_right is None

    # 第一帧锁定基线
    res = tracker.update(10.0, 7, 5)
    assert res is None
    assert tracker.current_left == 7
    assert tracker.current_right == 5


def test_tracker_monotonic_increment_with_debounce() -> None:
    tracker = ObserverScoreboardTracker()
    tracker.update(10.0, 7, 5)
    tracker.update(11.0, 7, 5)

    # 第 1 帧检测到 8-5（候选，尚未满 2 帧防抖）
    res1 = tracker.update(12.0, 8, 5)
    assert res1 is None

    # 第 2 帧继续维持 8-5（触发防抖确认）
    res2 = tracker.update(13.0, 8, 5)
    assert isinstance(res2, ScoreTransition)
    assert res2.previous_score == (7, 5)
    assert res2.new_score == (8, 5)
    assert res2.winning_side == "left"
    # 事件时间戳应追溯到首现时刻 12.0s
    assert res2.ts == 12.0
    assert tracker.current_left == 8
    assert tracker.current_right == 5


def test_tracker_right_side_increment() -> None:
    tracker = ObserverScoreboardTracker()
    tracker.update(10.0, 7, 5)
    tracker.update(12.0, 7, 6)
    res = tracker.update(13.0, 7, 6)
    assert isinstance(res, ScoreTransition)
    assert res.winning_side == "right"
    assert res.new_score == (7, 6)


def test_tracker_ignores_flicker_noise() -> None:
    tracker = ObserverScoreboardTracker()
    tracker.update(10.0, 7, 5)

    # 瞬间闪烁 8-5 仅维持 1 帧后跌回 7-5
    res1 = tracker.update(11.0, 8, 5)
    assert res1 is None
    res2 = tracker.update(12.0, 7, 5)
    assert res2 is None
    # 状态仍保持 7-5，未误触发
    assert tracker.current_left == 7
    assert tracker.current_right == 5
    assert len(tracker.transitions) == 0


def test_tracker_ignores_illegal_jump() -> None:
    tracker = ObserverScoreboardTracker()
    tracker.update(10.0, 7, 5)

    # 异常非单调大跳变（+3 分）
    res1 = tracker.update(11.0, 10, 5)
    res2 = tracker.update(12.0, 10, 5)
    assert res1 is None
    assert res2 is None
    assert tracker.current_left == 7


def test_tracker_handles_map_reset() -> None:
    tracker = ObserverScoreboardTracker()
    tracker.update(10.0, 13, 11)

    # 下一张地图从 0-0 开始
    tracker.update(100.0, 0, 0)
    assert tracker.current_left == 0
    assert tracker.current_right == 0

    # 新地图正常计分
    tracker.update(110.0, 1, 0)
    res = tracker.update(111.0, 1, 0)
    assert isinstance(res, ScoreTransition)
    assert res.new_score == (1, 0)


def test_extract_observer_scoreboard_scores_mock() -> None:
    fake_frame = np.zeros((1080, 1920, 3), dtype=np.uint8)

    def fake_ocr(img):
        # 模拟返回两条包含数字的行
        return [
            ([[100, 20], [140, 20], [140, 60], [100, 60]], ["8", 0.95]),
            ([[500, 20], [540, 20], [540, 60], [500, 60]], ["5", 0.92]),
        ], None

    left, right = extract_observer_scoreboard_scores(fake_frame, ocr_callable=fake_ocr)
    assert left == 8
    assert right == 5
