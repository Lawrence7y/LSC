"""Valorant 官方赛事/Observer HUD 顶部比分状态追踪器。

官方赛事转播（VCT 等）使用 Observer 定制观战客户端，其最显著且唯一恒定的
强物理特征是：顶部战队大比分栏的局分递增（如 Team A [7] - [5] Team B）。
当小局结束时，总比分必然单调 +1。

本模块提供单调比分状态机与跳变检测，为赛事流切片提供 100% 确凿的回合结束时间基准，
彻底摆脱对个人客户端全屏结算大横幅（VICTORY/DEFEAT）的脆弱依赖。
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

import numpy as np

_log = logging.getLogger(__name__)

# 比分范围合理性约束（常规比赛至多 13 分获胜，加时赛极罕见超过 25 分）
MIN_SCORE = 0
MAX_SCORE = 30

# 防抖确认帧数：比分跳变后需维持至少连续帧数才确认，防止导播比分板闪烁或特效误判
SCORE_CONFIRM_STREAK_FRAMES = 2


@dataclass(frozen=True, slots=True)
class ScoreSnapshot:
    """单帧比分观测快照。"""

    ts: float
    left_score: int
    right_score: int
    confidence: float = 1.0


@dataclass(frozen=True, slots=True)
class ScoreTransition:
    """确认的比分递增跳变事件（标志上一回合结束）。"""

    ts: float
    previous_score: tuple[int, int]
    new_score: tuple[int, int]
    winning_side: str  # "left" | "right" | "unknown"
    reason: str = "score_increment"


class ObserverScoreboardTracker:
    """单调比分跟踪与跳变检测状态机。"""

    def __init__(self) -> None:
        self.current_left: int | None = None
        self.current_right: int | None = None
        self._candidate_left: int | None = None
        self._candidate_right: int | None = None
        self._candidate_streak: int = 0
        self._candidate_first_ts: float | None = None
        self.transitions: list[ScoreTransition] = []
        self._history: list[ScoreSnapshot] = []

    def reset(self) -> None:
        """重置状态机（如检测到换图或大比分重置）。"""
        self.current_left = None
        self.current_right = None
        self._candidate_left = None
        self._candidate_right = None
        self._candidate_streak = 0
        self._candidate_first_ts = None
        self.transitions.clear()
        self._history.clear()

    def update(
        self,
        ts: float,
        left_score: int | None,
        right_score: int | None,
        *,
        confidence: float = 1.0,
    ) -> ScoreTransition | None:
        """喂入单帧比分读数，若检测到稳固的比分递增跳变则返回事件。"""
        if left_score is None or right_score is None:
            return None
        if not (MIN_SCORE <= left_score <= MAX_SCORE and MIN_SCORE <= right_score <= MAX_SCORE):
            return None

        ts_val = round(float(ts), 3)
        self._history.append(ScoreSnapshot(ts_val, left_score, right_score, confidence))

        # 初始比分锁定
        if self.current_left is None or self.current_right is None:
            self.current_left = left_score
            self.current_right = right_score
            return None

        # 读数完全未变，重置候选跳变状态
        if left_score == self.current_left and right_score == self.current_right:
            self._candidate_left = None
            self._candidate_right = None
            self._candidate_streak = 0
            self._candidate_first_ts = None
            return None

        # 比分断崖下跌：识别为换图/新对局开始，重新对齐基线
        if left_score < self.current_left or right_score < self.current_right:
            if left_score + right_score <= 2 and (self.current_left + self.current_right) >= 12:
                _log.info(
                    "检测到大比分重置，重置状态机: (%d-%d) -> (%d-%d) at %.1fs",
                    self.current_left, self.current_right, left_score, right_score, ts_val,
                )
                self.reset()
                self.current_left = left_score
                self.current_right = right_score
            return None

        # 核心判定：单调递增 +1
        delta_left = left_score - self.current_left
        delta_right = right_score - self.current_right
        total_delta = delta_left + delta_right

        # 只认可严格 +1 的小局胜负（平局加时也是各小局交替 +1）
        if total_delta != 1:
            return None

        # 防抖确认机制：比分变化必须维持 SCORE_CONFIRM_STREAK_FRAMES
        if left_score == self._candidate_left and right_score == self._candidate_right:
            self._candidate_streak += 1
        else:
            self._candidate_left = left_score
            self._candidate_right = right_score
            self._candidate_streak = 1
            self._candidate_first_ts = ts_val

        if self._candidate_streak >= SCORE_CONFIRM_STREAK_FRAMES:
            prev = (self.current_left, self.current_right)
            new_score = (left_score, right_score)
            winning_side = "left" if delta_left == 1 else "right"
            event_ts = self._candidate_first_ts if self._candidate_first_ts is not None else ts_val
            transition = ScoreTransition(
                ts=event_ts,
                previous_score=prev,
                new_score=new_score,
                winning_side=winning_side,
            )
            self.transitions.append(transition)
            _log.info(
                "确认官方赛事比分跳变: (%d-%d) -> (%d-%d), 胜方=%s, 切换时间点=%.2fs",
                prev[0], prev[1], new_score[0], new_score[1], winning_side, event_ts,
            )
            self.current_left = left_score
            self.current_right = right_score
            self._candidate_left = None
            self._candidate_right = None
            self._candidate_streak = 0
            self._candidate_first_ts = None
            return transition

        return None


def extract_observer_scoreboard_scores(
    frame_bgr: np.ndarray,
    ocr_callable: Any = None,
) -> tuple[int | None, int | None]:
    """从 Observer HUD 顶部大比分区域提取 (left_score, right_score)。

    裁剪区域针对 16:9 标准转播画面：
    顶部 0%~12% 高度，中央 30%~70% 宽度。
    """
    if frame_bgr is None or frame_bgr.size == 0:
        return None, None

    h, w = frame_bgr.shape[:2]
    # 聚焦 Observer 顶部比分栏
    crop_h = max(1, int(h * 0.12))
    crop_x1 = max(0, int(w * 0.30))
    crop_x2 = min(w, int(w * 0.70))
    roi = frame_bgr[:crop_h, crop_x1:crop_x2]

    if ocr_callable is None:
        try:
            from lsc.analyzer.ocr_detector import _get_ocr
            ocr_callable = _get_ocr()
        except Exception as exc:  # noqa: BLE001
            _log.debug("获取 OCR 失败: %s", exc)
            return None, None

    try:
        lines, _ = ocr_callable(roi)
    except Exception as exc:  # noqa: BLE001
        _log.debug("Observer 比分栏 OCR 失败: %s", exc)
        return None, None

    if not lines:
        return None, None

    # 解析行中的纯数字
    digit_pattern = re.compile(r"^\D*(\d{1,2})\D*$")
    candidates: list[tuple[float, int]] = []
    for line in lines:
        if not isinstance(line, (list, tuple)) or len(line) < 2:
            continue
        text_conf = line[1]
        if not isinstance(text_conf, (list, tuple)) or not text_conf:
            continue
        text = str(text_conf[0]).strip()
        m = digit_pattern.match(text)
        if m:
            val = int(m.group(1))
            if MIN_SCORE <= val <= MAX_SCORE:
                try:
                    pts = line[0]
                    x_center = sum(float(p[0]) for p in pts) / len(pts)
                except Exception:
                    x_center = float(len(candidates))
                candidates.append((x_center, val))

    if len(candidates) < 2:
        return None, None

    # 按水平坐标排序，最左为左队比分，最右为右队比分
    candidates.sort(key=lambda item: item[0])
    return candidates[0][1], candidates[-1][1]
